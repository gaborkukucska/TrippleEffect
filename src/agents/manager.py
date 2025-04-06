# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple
import json
import os
import traceback
import time
import logging
import uuid # For generating agent IDs

# Import Agent class, Status constants, and BaseLLMProvider types
from src.agents.core import Agent, AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_ERROR
from src.llm_providers.base import BaseLLMProvider, ToolResultDict, MessageDict

# Import settings instance, BASE_DIR, and default values
from src.config.settings import settings, BASE_DIR

# Import WebSocket broadcast function
from src.api.websocket_manager import broadcast

# Import ToolExecutor and Tool base class/types
from src.tools.executor import ToolExecutor
from src.tools.manage_team import ManageTeamTool # Import the new tool class name

# Import Provider classes
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

from pathlib import Path

logger = logging.getLogger(__name__)

# Mapping from provider name string to provider class
PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
}

# --- Constants ---
BOOTSTRAP_AGENT_ID = "admin_ai" # Define the primary bootstrap agent ID


class AgentManager:
    """
    Manages the lifecycle and task distribution for dynamically created agents within teams.
    The Admin AI (bootstrap agent) directs agent/team creation via ManageTeamTool.
    User messages are routed exclusively to the Admin AI.
    Handles session persistence for dynamic configurations and histories.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        """
        Initializes the AgentManager. Instantiates ToolExecutor and bootstrap agent(s).
        """
        self.bootstrap_agents: List[str] = [] # List of agent IDs loaded from config
        self.agents: Dict[str, Agent] = {} # All agents (bootstrap + dynamic)
        self.teams: Dict[str, List[str]] = {} # Dynamic team structure: team_id -> [agent_id]
        self.agent_to_team: Dict[str, str] = {} # Reverse mapping: agent_id -> team_id

        self.send_to_ui_func = broadcast

        logger.info("Instantiating ToolExecutor...")
        self.tool_executor = ToolExecutor()
        logger.info("ToolExecutor instantiated.")

        # Get formatted XML tool descriptions once
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        logger.info("Generated XML tool descriptions for prompts.")

        # Initialize ONLY bootstrap agents from config.yaml
        self._initialize_bootstrap_agents() # This is SYNCHRONOUS
        logger.info(f"AgentManager initialized. Bootstrap agents: {self.bootstrap_agents}")
        logger.info(f"Initial dynamic state: {len(self.agents)} total agents, {len(self.teams)} teams.")
        if not self.agents.get(BOOTSTRAP_AGENT_ID): # Check specifically for admin_ai
             logger.warning(f"AgentManager initialized but bootstrap agent '{BOOTSTRAP_AGENT_ID}' failed to load!")

        # Project/Session State
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        self._ensure_projects_dir() # Synchronous call is fine here


    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data."""
        try:
             settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)

    def _initialize_bootstrap_agents(self):
        """
        Loads ONLY the bootstrap agents (e.g., admin_ai) from settings.AGENT_CONFIGURATIONS.
        This method runs SYNCHRONOUSLY during startup.
        """
        logger.info("Initializing bootstrap agents from configuration...")
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list:
            logger.warning("No bootstrap agent configurations found in settings. Cannot initialize Admin AI.")
            return

        main_sandbox_dir = BASE_DIR / "sandboxes"
        try:
            main_sandbox_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
            logger.error(f"Error creating main sandbox directory: {e}")

        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id:
                logger.warning("Skipping bootstrap agent configuration due to missing 'agent_id'.")
                continue

            logger.info(f"--- Initializing bootstrap agent '{agent_id}' ---")
            try:
                # --- Call SYNCHRONOUS internal creation logic ---
                # Do NOT pass team_id here, bootstrap agents aren't assigned teams dynamically at init
                success, message, created_agent_id = self._create_agent_internal_sync(
                    agent_id_requested=agent_id,
                    agent_config_data=agent_conf_entry.get("config", {}),
                    is_bootstrap=True
                )
                if success and created_agent_id:
                    self.bootstrap_agents.append(created_agent_id)
                    logger.info(f"--- Bootstrap agent '{created_agent_id}' successfully initialized. ---")
                else:
                    logger.error(f"--- Failed to initialize bootstrap agent '{agent_id}': {message} ---")

            except Exception as e:
                 logger.error(f"  Unexpected Error creating bootstrap agent '{agent_id}': {e}", exc_info=True)
                 logger.info(f"--- Bootstrap agent '{agent_id}' initialization failed. ---")

        logger.info(f"Finished bootstrap agent initialization. Active bootstrap agents: {self.bootstrap_agents}")


    # --- SYNCHRONOUS Internal Agent Creation Logic (for bootstrap) ---
    def _create_agent_internal_sync(
        self,
        agent_id_requested: Optional[str],
        agent_config_data: Dict[str, Any],
        is_bootstrap: bool = False
        ) -> Tuple[bool, str, Optional[str]]:
        """
        SYNCHRONOUS internal logic to instantiate an agent, provider, and sandbox.
        Does NOT handle team assignment (must be done asynchronously later if needed).
        """
        # 1. Determine Agent ID
        if agent_id_requested and agent_id_requested in self.agents:
            return False, f"Agent ID '{agent_id_requested}' already exists.", None
        agent_id = agent_id_requested or self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to generate or provide a valid Agent ID.", None

        # 2. Extract Config (same as before)
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        system_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        provider_specific_kwargs = { k: v for k, v in agent_config_data.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url'] }
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]

        # 3. Instantiate Provider (Synchronously)
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass: return False, f"Unknown provider '{provider_name}' specified.", None
        base_provider_config = settings.get_provider_config(provider_name)
        final_provider_args = { **base_provider_config, **provider_specific_kwargs }
        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        try:
             # TODO: Implement provider reuse based on final_provider_args? Needs careful state mgmt.
             llm_provider_instance = ProviderClass(**final_provider_args)
             logger.info(f"  Instantiated provider {ProviderClass.__name__} for agent '{agent_id}'.")
        except Exception as e:
             logger.error(f"  Failed to instantiate provider {ProviderClass.__name__} for '{agent_id}': {e}", exc_info=True)
             return False, f"Failed to instantiate provider: {e}", None

        # 4. Instantiate Agent (Synchronously)
        agent_entry = { "agent_id": agent_id, "config": { "provider": provider_name, "model": model, "system_prompt": system_prompt, "persona": persona, "temperature": temperature, **provider_specific_kwargs } }
        try:
            agent = Agent(
                agent_config=agent_entry,
                llm_provider=llm_provider_instance,
                manager=self,
                tool_descriptions_xml=self.tool_descriptions_xml
            )
        except Exception as e:
             logger.error(f"  Failed to instantiate Agent class for '{agent_id}': {e}", exc_info=True)
             # Clean up provider instance? Only if not reused. Defer.
             return False, f"Failed to instantiate agent: {e}", None

        # 5. Ensure Sandbox (Synchronously)
        try:
            agent.sandbox_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Error creating sandbox directory for Agent {agent_id} at {agent.sandbox_path}: {e}")
            # Fail agent creation if sandbox cannot be created? Or just warn? Let's warn for now.
            logger.warning(f"  Failed to create sandbox for agent '{agent_id}'. File operations will fail.")
        except Exception as e:
            logger.error(f"Unexpected error ensuring sandbox for Agent {agent_id}: {e}", exc_info=True)
            logger.warning(f"  Failed to ensure sandbox for agent '{agent_id}'.")


        # 6. Add to Manager State (Synchronously)
        self.agents[agent_id] = agent
        # Note: Team assignment is NOT done here.

        # Store the config used to create the agent ON the agent object for persistence
        agent.agent_config = agent_entry # Store the reconstructed config

        return True, f"Agent '{agent_id}' instance created synchronously.", agent_id


    # --- ASYNCHRONOUS Public Method for Dynamic Creation ---
    async def create_agent_instance(self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs) -> Tuple[bool, str, Optional[str]]:
        """Public ASYNC method called by Admin AI (via tool) to create a dynamic agent."""
        if not provider or not model or not system_prompt or not persona:
            return False, "Missing required parameters (provider, model, system_prompt, persona) for create_agent.", None

        # Construct config dict from params
        agent_config_data = {
            "provider": provider, "model": model, "system_prompt": system_prompt,
            "persona": persona, **kwargs # Include any other potential kwargs from tool
        }
        if temperature is not None: agent_config_data["temperature"] = temperature

        # --- Call Synchronous internal creation ---
        # This is okay to call from async because it doesn't block the loop long
        # (provider/agent init is fast, mkdir is acceptable for now)
        success, message, created_agent_id = self._create_agent_internal_sync(
            agent_id_requested=agent_id_requested,
            agent_config_data=agent_config_data,
            is_bootstrap=False # Dynamic agent
        )

        if not success or not created_agent_id:
            return success, message, created_agent_id # Return failure

        # --- Handle ASYNC operations AFTER sync creation ---
        team_add_success = True
        team_add_msg = ""
        if team_id:
            team_add_success, team_add_msg = await self.add_agent_to_team(created_agent_id, team_id)
            if not team_add_success:
                 logger.warning(f"Agent '{created_agent_id}' created, but failed to add to team '{team_id}': {team_add_msg}")
                 message += f" (Warning: {team_add_msg})" # Append warning to success message

        # Send UI update
        await self.send_to_ui({
            "type": "agent_added",
            "agent_id": created_agent_id,
            "config": agent_config_data, # Send config used
            "team": self.agent_to_team.get(created_agent_id)
        })

        return success, message, created_agent_id

    # --- Other methods remain largely the same, ensure they are async ---

    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        # (Logic remains the same: route to admin_ai)
        logger.info(f"AgentManager received user message for Admin AI: '{message[:100]}...'")
        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent:
            logger.error(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') not found or not initialized. Cannot process user message.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI is not available."})
            return
        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Adding user message to history for '{BOOTSTRAP_AGENT_ID}' and starting processing task.")
            admin_agent.message_history.append({"role": "user", "content": message})
            asyncio.create_task(self._handle_agent_generator(admin_agent))
            logger.info(f"Delegated user message to '{BOOTSTRAP_AGENT_ID}'.")
        else:
            logger.info(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') is busy (Status: {admin_agent.status}). User message not processed.")
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({ "type": "error", "agent_id": "manager", "content": f"Admin AI is currently busy (Status: {admin_agent.status}). Please wait." })


    async def _handle_agent_generator(self, agent: Agent):
        # (Logic remains the same: includes handling ManageTeamTool results)
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        try:
            agent_generator = agent.process_message()
            while True:
                try: event = await agent_generator.asend(None)
                except StopAsyncIteration: logger.info(f"Agent '{agent_id}' generator finished normally."); break
                except Exception as gen_err: logger.error(f"Error interacting with agent '{agent_id}' generator: {gen_err}", exc_info=True); agent.set_status(AGENT_STATUS_ERROR); await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {gen_err}]"}); break
                event_type = event.get("type")
                if event_type in ["response_chunk", "status", "error", "final_response"]: # Simple events
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "error": logger.error(f"Agent '{agent_id}' reported error."); break
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content}); logger.debug(f"Appended final response for '{agent_id}'.")
                elif event_type == "tool_requests": # Tool requests
                    tool_calls = event.get("calls", []); agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response}); logger.debug(f"Appended assistant response (tools) for '{agent_id}'.")
                    tool_tasks = []; calls_post_exec = []
                    for call in tool_calls:
                        call_id, tool_name, tool_args = call.get("id"), call.get("name"), call.get("arguments", {})
                        if call_id and tool_name and isinstance(tool_args, dict):
                            if tool_name == ManageTeamTool.name or tool_name == "send_message": calls_post_exec.append(call)
                            tool_tasks.append(asyncio.create_task(self._execute_single_tool(agent, call_id, tool_name, tool_args)))
                        else: logger.warning(f"Skipping invalid tool req from '{agent_id}': {call}"); tool_tasks.append(asyncio.create_task(self._failed_tool_result(call_id, tool_name)))
                    executed_results = []
                    if tool_tasks: await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(tool_tasks)} tool(s)..."}); tool_results_raw = await asyncio.gather(*tool_tasks); executed_results = [res for res in tool_results_raw if res is not None]
                    if executed_results: # Append results to history
                        append_count = 0
                        for result in executed_results:
                            result_content = result.get("content", "[No content found in tool result]") if isinstance(result, dict) else result # Handle dict/str result
                            tool_msg: MessageDict = {"role": "tool", "tool_call_id": result["call_id"], "content": str(result_content) }
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != result["call_id"]: agent.message_history.append(tool_msg); append_count += 1
                        logger.debug(f"Appended {append_count} tool result(s) for '{agent_id}'.")
                    # Post-execution processing
                    activation_tasks = []
                    for call in calls_post_exec:
                        call_id, tool_name, tool_args = call["id"], call["name"], call["arguments"]
                        exec_result = next((r for r in executed_results if r["call_id"] == call_id), None)
                        if not exec_result: logger.error(f"No exec result for call {call_id} ({tool_name}). Skipping."); continue
                        if tool_name == ManageTeamTool.name and isinstance(exec_result, dict) and exec_result.get("status") == "success":
                             action = exec_result.get("action"); params = exec_result.get("params", {})
                             logger.info(f"Processing ManageTeamTool action '{action}' from '{agent_id}'.")
                             await self._handle_manage_team_action(action, params) # Await manager action
                        elif tool_name == "send_message" and isinstance(exec_result, dict) and not exec_result["content"].startswith("Error:"): # Check result content string within dict
                             target_id, msg_content = tool_args.get("target_agent_id"), tool_args.get("message_content")
                             if target_id and msg_content is not None:
                                 activation_task = await self._route_and_activate_agent_message(agent_id, target_id, msg_content)
                                 if activation_task: activation_tasks.append(activation_task)
                             else: logger.error(f"SendMessage args incomplete for {call_id}. Args: {tool_args}")
                        elif (isinstance(exec_result, dict) and exec_result.get("status") == "error") or (isinstance(exec_result, str) and exec_result.startswith("Error:")):
                             logger.warning(f"Tool call {call_id} ({tool_name}) failed exec. Skipping post-proc. Result: {exec_result.get('content', exec_result)}")
                    if activation_tasks: logger.info(f"Triggered activation for {len(activation_tasks)} agents from '{agent_id}'.")
                else: logger.warning(f"Unknown event type '{event_type}' from '{agent_id}'.")
        except Exception as e: logger.error(f"Error handling generator for {agent_id}: {e}", exc_info=True); agent.set_status(AGENT_STATUS_ERROR); await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {e}]"})
        finally:
             if agent_generator:
                 try: await agent_generator.aclose(); logger.debug(f"Closed generator for '{agent_id}'.")
                 except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)
             await self.push_agent_status_update(agent_id)
             logger.info(f"Manager finished handling generator cycle for Agent '{agent_id}'. Final status: {agent.status}")


    async def _handle_manage_team_action(self, action: Optional[str], params: Dict[str, Any]):
        # (Logic remains the same: dispatch to manager methods)
        if not action: return; success, message, result_data = False, "Unknown action or error.", None
        try:
            if action == "create_agent": success, message, _ = await self.create_agent_instance( params.get("agent_id"), params.get("provider"), params.get("model"), params.get("system_prompt"), params.get("persona"), params.get("team_id"), params.get("temperature") )
            elif action == "delete_agent": success, message = await self.delete_agent_instance(params.get("agent_id"))
            elif action == "create_team": success, message = await self.create_new_team(params.get("team_id"))
            elif action == "delete_team": success, message = await self.delete_existing_team(params.get("team_id"))
            elif action == "add_agent_to_team": success, message = await self.add_agent_to_team(params.get("agent_id"), params.get("team_id"))
            elif action == "remove_agent_from_team": success, message = await self.remove_agent_from_team(params.get("agent_id"), params.get("team_id"))
            elif action == "list_agents": success, result_data, message = True, await self.get_agent_info_list(), f"Found {len(result_data)} agents."
            elif action == "list_teams": success, result_data, message = True, await self.get_team_info_dict(), f"Found {len(result_data)} teams."
            else: message = f"Unrecognized ManageTeamTool action: {action}"; logger.warning(message)
            logger.info(f"ManageTeamTool action '{action}' result: Success={success}, Message='{message}'")
            # TODO: Feedback mechanism to Admin AI needed here.
        except Exception as e: message = f"Error processing ManageTeamTool action '{action}': {e}"; logger.error(message, exc_info=True)


    def _generate_unique_agent_id(self, prefix="agent") -> str:
        # (Logic remains the same)
        while True: new_id = f"{prefix}_{uuid.uuid4().hex[:6]}";
        if new_id not in self.agents: return new_id

    # --- Dynamic Team/Agent Async Methods ---
    # (delete_agent_instance, create_new_team, delete_existing_team, add_agent_to_team, remove_agent_from_team, get_agent_info_list, get_team_info_dict remain async and mostly unchanged)
    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        if not agent_id or agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."
        agent_instance = self.agents.pop(agent_id); team_id = self.agent_to_team.pop(agent_id, None)
        if team_id and team_id in self.teams and agent_id in self.teams[team_id]: self.teams[team_id].remove(agent_id); logger.info(f"Removed '{agent_id}' from team '{team_id}'.")
        provider = agent_instance.llm_provider # Cleanup provider
        if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
            try: logger.info(f"Closing provider session for deleted agent '{agent_id}'..."); await provider.close_session()
            except Exception as e: logger.error(f"Error closing provider session for '{agent_id}': {e}", exc_info=True)
        message = f"Agent '{agent_id}' deleted successfully."; logger.info(message)
        await self.send_to_ui({"type": "agent_deleted", "agent_id": agent_id}); return True, message

    async def create_new_team(self, team_id: str) -> Tuple[bool, str]:
        if not team_id: return False, "Team ID empty.";
        if team_id in self.teams: return False, f"Team '{team_id}' exists."
        self.teams[team_id] = []; message = f"Team '{team_id}' created."; logger.info(message)
        await self.send_to_ui({"type": "team_created", "team_id": team_id}); return True, message

    async def delete_existing_team(self, team_id: str) -> Tuple[bool, str]:
        if not team_id: return False, "Team ID empty.";
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        if self.teams[team_id]: return False, f"Team '{team_id}' not empty."
        del self.teams[team_id]; message = f"Team '{team_id}' deleted."; logger.info(message)
        await self.send_to_ui({"type": "team_deleted", "team_id": team_id}); return True, message

    async def add_agent_to_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        if not agent_id or not team_id: return False, "Agent/Team ID empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        old_team = None
        if agent_id in self.agent_to_team:
            if self.agent_to_team[agent_id] == team_id: return True, f"Agent '{agent_id}' already in '{team_id}'."
            old_team = self.agent_to_team[agent_id]
            if old_team in self.teams and agent_id in self.teams[old_team]: self.teams[old_team].remove(agent_id)
            logger.info(f"Removed '{agent_id}' from old team '{old_team}'.")
        if agent_id not in self.teams[team_id]: self.teams[team_id].append(agent_id)
        self.agent_to_team[agent_id] = team_id
        message = f"Agent '{agent_id}' added to team '{team_id}'."; logger.info(message)
        await self.send_to_ui({"type": "agent_moved_team", "agent_id": agent_id, "new_team_id": team_id, "old_team_id": old_team})
        await self.push_agent_status_update(agent_id); return True, message

    async def remove_agent_from_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        if not agent_id or not team_id: return False, "Agent/Team ID empty."
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        if agent_id not in self.agent_to_team or self.agent_to_team[agent_id] != team_id: return False, f"Agent '{agent_id}' not in '{team_id}'."
        if agent_id in self.teams[team_id]: self.teams[team_id].remove(agent_id)
        del self.agent_to_team[agent_id]
        message = f"Agent '{agent_id}' removed from team '{team_id}'."; logger.info(message)
        await self.send_to_ui({"type": "agent_moved_team", "agent_id": agent_id, "new_team_id": None, "old_team_id": team_id})
        await self.push_agent_status_update(agent_id); return True, message

    async def get_agent_info_list(self) -> List[Dict[str, Any]]:
        info_list = []
        for agent_id, agent in self.agents.items(): state = agent.get_state(); info = { "agent_id": agent_id, "persona": state.get("persona"), "provider": state.get("provider"), "model": state.get("model"), "status": state.get("status"), "team": self.agent_to_team.get(agent_id) }; info_list.append(info)
        return info_list

    async def get_team_info_dict(self) -> Dict[str, List[str]]:
        return self.teams.copy()


    # --- Tool Execution and Routing ---
    async def _route_and_activate_agent_message(self, sender_id: str, target_id: str, message_content: str) -> Optional[asyncio.Task]:
        # (Logic remains the same)
        sender_agent, target_agent = self.agents.get(sender_id), self.agents.get(target_id)
        if not sender_agent or not target_agent: logger.error(f"SendMsg route err: Sender/Target not found."); return None
        sender_team, target_team = self.agent_to_team.get(sender_id), self.agent_to_team.get(target_id)
        if not sender_team or not target_team or sender_team != target_team: logger.warning(f"SendMsg blocked: Agents not in same team."); return None
        logger.info(f"Routing message from '{sender_id}' to '{target_id}' in team '{target_team}'.")
        formatted_message: MessageDict = { "role": "user", "content": f"[From @{sender_id}]: {message_content}" }
        target_agent.message_history.append(formatted_message); logger.debug(f"Appended msg to history of '{target_id}'.")
        if target_agent.status == AGENT_STATUS_IDLE:
             logger.info(f"Target '{target_id}' is IDLE. Activating..."); return asyncio.create_task(self._handle_agent_generator(target_agent))
        else: logger.info(f"Target '{target_id}' not IDLE ({target_agent.status}). Not activating."); return None

    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict | str]:
        # (Logic remains the same: expects dict result from ManageTeamTool)
        if not self.tool_executor: logger.error(f"ToolExecutor unavailable."); return {"call_id": call_id, "content": "[ToolExec Error: ToolExecutor unavailable]"}
        tool_info = {"name": tool_name, "call_id": call_id}; agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)
        result_data: Optional[Dict | str] = None
        try:
            logger.debug(f"Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}'"); result_data = await self.tool_executor.execute_tool(agent.agent_id, agent.sandbox_path, tool_name, tool_args); logger.debug(f"Tool '{tool_name}' completed.")
            if isinstance(result_data, dict): return {"call_id": call_id, "content": result_data.get("message", "Action processed."), "_raw_result": result_data}
            elif isinstance(result_data, str): return {"call_id": call_id, "content": result_data}
            else: logger.error(f"Unexpected result type {type(result_data)} from tool '{tool_name}'"); return {"call_id": call_id, "content": "[ToolExec Error: Unexpected result type]"}
        except Exception as e: error_msg = f"Manager error during _execute_single_tool '{tool_name}': {e}"; logger.error(error_msg, exc_info=True); return {"call_id": call_id, "content": f"[ToolExec Error: {error_msg}]"}
        finally:
            if agent.status == AGENT_STATUS_EXECUTING_TOOL: agent.set_status(AGENT_STATUS_PROCESSING)

    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        # (Logic remains the same)
        error_content = f"[ToolExec Error: Failed dispatch '{tool_name or 'unknown'}'. Invalid format.]"; final_call_id = call_id or f"invalid_call_{int(time.time())}"; return {"call_id": final_call_id, "content": error_content}

    async def push_agent_status_update(self, agent_id: str):
        # (Logic remains the same)
        agent = self.agents.get(agent_id);
        if agent: state = agent.get_state(); state["team"] = self.agent_to_team.get(agent_id); await self.send_to_ui({ "type": "agent_status_update", "agent_id": agent_id, "status": state })
        else: logger.warning(f"Cannot push status for unknown agent: {agent_id}")

    async def send_to_ui(self, message_data: Dict[str, Any]):
        # (Logic remains the same)
        if not self.send_to_ui_func: logger.warning("UI broadcast func not configured."); return
        try: await self.send_to_ui_func(json.dumps(message_data))
        except TypeError as e: logger.error(f"JSON serialization error: {e}", exc_info=True); logger.debug(f"Data: {message_data}")
        except Exception as e: logger.error(f"Error sending to UI: {e}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        # (Logic remains the same)
        statuses = {};
        for agent_id, agent in self.agents.items(): state = agent.get_state(); state["team"] = self.agent_to_team.get(agent_id); statuses[agent_id] = state
        return statuses


    # --- Session Persistence Methods (Adapted for Dynamic Configs) ---
    # (Save/Load logic remains mostly the same, relies on agent.agent_config)
    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the current state including dynamic agent configs and histories."""
        if not project_name: return False, "Project name empty."
        if not session_name: session_name = f"session_{int(time.time())}"
        session_file = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Saving session to: {session_file}")
        session_data = { "project": project_name, "session": session_name, "timestamp": time.time(), "teams": self.teams, "agent_to_team": self.agent_to_team, "dynamic_agents_config": {}, "agent_histories": {} }
        for agent_id, agent in self.agents.items():
            try: json.dumps(agent.message_history); session_data["agent_histories"][agent_id] = agent.message_history
            except TypeError as e: logger.error(f"History for '{agent_id}' not JSON serializable: {e}."); session_data["agent_histories"][agent_id] = [{"role": "system", "content": f"[History serialization error: {e}]"}]
            if agent_id not in self.bootstrap_agents:
                 try: config_to_save = agent.agent_config.get("config");
                 if config_to_save: session_data["dynamic_agents_config"][agent_id] = config_to_save
                 else: logger.warning(f"No config sub-dict for dynamic agent '{agent_id}'. Not saved.")
                 except AttributeError: logger.warning(f"Agent '{agent_id}' missing 'agent_config'. Not saved.")
        try:
            def save_sync(): session_file.parent.mkdir(parents=True, exist_ok=True); f.write(json.dumps(session_data, indent=2))
            with open(session_file, 'w', encoding='utf-8') as f: await asyncio.to_thread(save_sync)
            logger.info(f"Session saved: {session_file}"); self.current_project, self.current_session = project_name, session_name
            return True, f"Session '{session_name}' saved in '{project_name}'."
        except Exception as e: logger.error(f"Error saving session: {e}", exc_info=True); return False, f"Error saving session: {e}"

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Loads dynamic agents, teams, and histories from a saved session file."""
        session_file = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Loading session from: {session_file}")
        if not session_file.is_file(): return False, f"Session file '{session_name}' not found in '{project_name}'."
        try:
            def load_sync():
                 with open(session_file, 'r', encoding='utf-8') as f: return json.load(f)
            session_data = await asyncio.to_thread(load_sync)
            dynamic_agents_to_delete = [aid for aid in self.agents if aid not in self.bootstrap_agents]; logger.info(f"Clearing dynamic agents: {dynamic_agents_to_delete}")
            await asyncio.gather(*(self.delete_agent_instance(aid) for aid in dynamic_agents_to_delete))
            self.teams, self.agent_to_team = {}, {}; logger.info("Cleared dynamic state.")
            self.teams = session_data.get("teams", {}); self.agent_to_team = session_data.get("agent_to_team", {}); dynamic_configs = session_data.get("dynamic_agents_config", {}); histories = session_data.get("agent_histories", {})
            logger.info(f"Loading {len(dynamic_configs)} dynamic agents..."); creation_results = await asyncio.gather(*(self._create_agent_internal(aid, cfg, False, self.agent_to_team.get(aid)) for aid, cfg in dynamic_configs.items())) # Recreate using internal async method
            successful_creations = sum(1 for success, _, _ in creation_results if success); logger.info(f"Recreated {successful_creations}/{len(dynamic_configs)} dynamic agents.")
            loaded_history_count = 0
            for agent_id, history in histories.items():
                agent = self.agents.get(agent_id)
                if agent and isinstance(history, list) and all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in history):
                     agent.message_history = history; agent.set_status(AGENT_STATUS_IDLE); loaded_history_count += 1
                elif agent: logger.warning(f"Invalid/missing history for '{agent_id}'.")
            logger.info(f"Loaded histories for {loaded_history_count} agents.")
            self.current_project, self.current_session = project_name, session_name
            await asyncio.gather(*(self.push_agent_status_update(aid) for aid in self.agents.keys())); return True, f"Session '{session_name}' loaded. {successful_creations} dynamic agents recreated."
        except json.JSONDecodeError as e: logger.error(f"JSON decode error: {e}"); return False, f"Invalid session file format."
        except Exception as e: logger.error(f"Error loading session: {e}", exc_info=True); return False, f"Error loading session: {e}"


    async def cleanup_providers(self):
        # (Logic remains the same)
        logger.info("Cleaning up LLM providers...")
        active_providers = {agent.llm_provider for agent in self.agents.values()}; logger.info(f"Found {len(active_providers)} unique provider instances.")
        for provider in active_providers:
             if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
                 try: logger.info(f"Closing session: {provider!r}"); await provider.close_session()
                 except Exception as e: logger.error(f"Error closing {provider!r}: {e}", exc_info=True)
        logger.info("LLM Provider cleanup finished.")
