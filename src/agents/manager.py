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
# NOTE: settings needs to be fully initialized BEFORE AgentManager is instantiated if manager uses settings in __init__
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
        SYNCHRONOUSLY initializes the AgentManager.
        Sets up core attributes, ToolExecutor.
        Bootstrap agent initialization is deferred to an async method called during application lifespan startup.
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

        # Project/Session State
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        # Ensure projects dir synchronously here, as it doesn't require await
        self._ensure_projects_dir()

        logger.info("AgentManager initialized synchronously. Bootstrap agents will be loaded asynchronously.")


    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data."""
        try:
             settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)


    # --- ASYNCHRONOUS Bootstrap Initialization ---
    async def initialize_bootstrap_agents(self):
        """
        ASYNCHRONOUSLY loads bootstrap agents from settings.AGENT_CONFIGURATIONS.
        Should be called during application startup (e.g., lifespan event).
        """
        logger.info("Initializing bootstrap agents asynchronously...")
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list:
            logger.warning("No bootstrap agent configurations found in settings.")
            return

        main_sandbox_dir = BASE_DIR / "sandboxes"
        try:
            await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
            logger.info(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
            logger.error(f"Error creating main sandbox directory: {e}")

        tasks = []
        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id:
                logger.warning("Skipping bootstrap agent configuration due to missing 'agent_id'.")
                continue
            tasks.append(self._create_agent_internal(
                 agent_id_requested=agent_id,
                 agent_config_data=agent_conf_entry.get("config", {}),
                 is_bootstrap=True
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_ids = []
        for i, result in enumerate(results):
            agent_id = agent_configs_list[i].get("agent_id", f"unknown_{i}")
            if isinstance(result, Exception): logger.error(f"--- Failed bootstrap init '{agent_id}': {result} ---", exc_info=result)
            elif isinstance(result, tuple) and len(result) == 3:
                 success, message, created_agent_id = result
                 if success and created_agent_id: self.bootstrap_agents.append(created_agent_id); successful_ids.append(created_agent_id); logger.info(f"--- Bootstrap agent '{created_agent_id}' initialized. ---")
                 else: logger.error(f"--- Failed bootstrap init '{agent_id}': {message} ---")
            else: logger.error(f"--- Unexpected result type during bootstrap init for '{agent_id}': {result} ---")

        logger.info(f"Finished async bootstrap agent initialization. Active bootstrap agents: {successful_ids}")
        if BOOTSTRAP_AGENT_ID not in self.agents: logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize!")


    # --- ASYNCHRONOUS Internal Agent Creation Logic ---
    async def _create_agent_internal(
        self,
        agent_id_requested: Optional[str],
        agent_config_data: Dict[str, Any],
        is_bootstrap: bool = False,
        team_id: Optional[str] = None
        ) -> Tuple[bool, str, Optional[str]]:
        """ASYNC internal logic to instantiate agent, provider, sandbox, and handle team."""
        # 1. Determine Agent ID
        if agent_id_requested and agent_id_requested in self.agents: return False, f"Agent ID '{agent_id_requested}' exists.", None
        agent_id = agent_id_requested or self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to generate Agent ID.", None
        logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap})")
        # 2. Extract Config
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        system_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        allowed_provider_keys = ['api_key', 'base_url', 'referer']
        provider_specific_kwargs = { k: v for k, v in agent_config_data.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona'] + allowed_provider_keys}
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]
        # 3. Instantiate Provider
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass: return False, f"Unknown provider '{provider_name}'.", None
        base_provider_config = settings.get_provider_config(provider_name)
        final_provider_args = { **base_provider_config, **provider_specific_kwargs }
        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        try:
             llm_provider_instance = ProviderClass(**final_provider_args)
             logger.info(f"  Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")
        except Exception as e: logger.error(f"  Provider instantiation failed for '{agent_id}': {e}", exc_info=True); return False, f"Provider instantiation failed: {e}", None
        # 4. Instantiate Agent
        agent_entry = { "agent_id": agent_id, "config": { "provider": provider_name, "model": model, "system_prompt": system_prompt, "persona": persona, "temperature": temperature, **provider_specific_kwargs } }
        try:
            agent = Agent( agent_config=agent_entry, llm_provider=llm_provider_instance, manager=self, tool_descriptions_xml=self.tool_descriptions_xml )
            agent.agent_config = agent_entry # Store config
            logger.info(f"  Instantiated Agent object for '{agent_id}'.")
        except Exception as e: logger.error(f"  Agent instantiation failed for '{agent_id}': {e}", exc_info=True); return False, f"Agent instantiation failed: {e}", None
        # 5. Ensure Sandbox
        try:
            sandbox_ok = await asyncio.to_thread(agent.ensure_sandbox_exists)
            if not sandbox_ok: logger.warning(f"  Failed to ensure sandbox for '{agent_id}'.")
        except Exception as e: logger.error(f"Sandbox error for '{agent_id}': {e}", exc_info=True); logger.warning(f"Proceeding without guaranteed sandbox for '{agent_id}'.")
        # 6. Add to Manager State
        self.agents[agent_id] = agent
        logger.debug(f"Agent '{agent_id}' added to self.agents.")
        # 7. Add to Team if specified
        team_add_msg_suffix = ""
        if team_id:
             team_add_success, team_add_msg = await self.add_agent_to_team(agent_id, team_id)
             if not team_add_success: team_add_msg_suffix = f" (Warning: {team_add_msg})"
             else: logger.info(f"Agent '{agent_id}' added to team '{team_id}'.")
        message = f"Agent '{agent_id}' created successfully." + team_add_msg_suffix
        return True, message, agent_id


    # --- ASYNCHRONOUS Public Method for Dynamic Creation ---
    async def create_agent_instance(self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs) -> Tuple[bool, str, Optional[str]]:
        """Public ASYNC method called by Admin AI (via tool) to create a dynamic agent."""
        if not provider or not model or not system_prompt or not persona:
            return False, "Missing required params (provider, model, system_prompt, persona).", None
        agent_config_data = { "provider": provider, "model": model, "system_prompt": system_prompt, "persona": persona }
        if temperature is not None: agent_config_data["temperature"] = temperature
        extra_kwargs = {k: v for k, v in kwargs.items() if k not in ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']}
        agent_config_data.update(extra_kwargs)
        success, message, created_agent_id = await self._create_agent_internal( agent_id_requested=agent_id_requested, agent_config_data=agent_config_data, is_bootstrap=False, team_id=team_id )
        if success and created_agent_id:
            created_agent = self.agents.get(created_agent_id)
            config_sent_to_ui = created_agent.agent_config.get("config", {}) if created_agent and hasattr(created_agent, 'agent_config') else {}
            await self.send_to_ui({ "type": "agent_added", "agent_id": created_agent_id, "config": config_sent_to_ui, "team": self.agent_to_team.get(created_agent_id) })
        return success, message, created_agent_id


    # --- Async Handler Methods ---
    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """Routes incoming user messages exclusively to the Admin AI agent."""
        logger.info(f"AgentManager received user message for Admin AI: '{message[:100]}...'")
        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent: logger.error(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') not found."); await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."}); return
        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Delegating user message to '{BOOTSTRAP_AGENT_ID}'.")
            admin_agent.message_history.append({"role": "user", "content": message})
            asyncio.create_task(self._handle_agent_generator(admin_agent))
        else: logger.info(f"Admin AI busy ({admin_agent.status})."); await self.push_agent_status_update(admin_agent.agent_id); await self.send_to_ui({ "type": "error", "agent_id": "manager", "content": f"Admin AI busy ({admin_agent.status}). Please wait." })


    async def _handle_agent_generator(self, agent: Agent):
        """Handles the async generator interaction for a single agent's processing cycle."""
        agent_id = agent.agent_id; logger.info(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback = []
        reactivate_agent_after_feedback = False # Flag to check if agent needs re-activation

        try:
            agent_generator = agent.process_message()
            while True:
                try: event = await agent_generator.asend(None)
                except StopAsyncIteration: logger.info(f"Agent '{agent_id}' generator finished normally."); break # Normal finish
                except Exception as gen_err: logger.error(f"Generator error for '{agent_id}': {gen_err}", exc_info=True); agent.set_status(AGENT_STATUS_ERROR); await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {gen_err}]"}); break # Error finish

                event_type = event.get("type")
                if event_type in ["response_chunk", "status", "error", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "error": logger.error(f"Agent '{agent_id}' reported error."); break # Break loop on agent error
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content}); logger.debug(f"Appended final response for '{agent_id}'.")

                elif event_type == "tool_requests":
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
                    if executed_results:
                        append_count = 0
                        for result in executed_results:
                            call_id_from_res = result.get("call_id"); result_content = result.get("content", "[No content]");
                            if not call_id_from_res: continue
                            tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id_from_res, "content": str(result_content) }
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id_from_res: agent.message_history.append(tool_msg); append_count += 1
                        logger.debug(f"Appended {append_count} tool result(s) for '{agent_id}'.")

                    # --- Post-execution processing ---
                    activation_tasks = []; manager_action_feedback = [] # Reset feedback list
                    for call in calls_post_exec:
                        call_id, tool_name, tool_args = call["id"], call["name"], call["arguments"]
                        exec_result = next((r for r in executed_results if isinstance(r, dict) and r.get("call_id") == call_id), None)
                        if not exec_result: logger.error(f"No dict result for {call_id} ({tool_name}). Skipping."); continue
                        raw_tool_output = exec_result.get("_raw_result")
                        if tool_name == ManageTeamTool.name:
                             if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                  action = raw_tool_output.get("action"); params = raw_tool_output.get("params", {})
                                  logger.info(f"Processing ManageTeamTool action '{action}' from '{agent_id}'.")
                                  action_success, action_message, action_data = await self._handle_manage_team_action(action, params)
                                  feedback = {"call_id": call_id, "action": action, "success": action_success, "message": action_message}
                                  if action_data: feedback["data"] = action_data
                                  manager_action_feedback.append(feedback) # Store feedback
                             elif raw_tool_output: logger.warning(f"ManageTeamTool call {call_id} unsuccessful. Raw Result: {raw_tool_output}")
                             else: logger.warning(f"ManageTeamTool call {call_id} had unexpected structure: {exec_result}")
                        elif tool_name == "send_message":
                              result_content_str = exec_result.get("content", "")
                              if not result_content_str.startswith("Error:"):
                                  target_id, msg_content = tool_args.get("target_agent_id"), tool_args.get("message_content")
                                  if target_id and msg_content is not None:
                                       activation_task = await self._route_and_activate_agent_message(agent_id, target_id, msg_content)
                                       if activation_task: activation_tasks.append(activation_task)
                                  else: logger.error(f"SendMessage args incomplete for {call_id}. Args: {tool_args}")
                              else: logger.warning(f"SendMessageTool call {call_id} failed exec. Result: {result_content_str}")
                    if activation_tasks: logger.info(f"Triggered activation for {len(activation_tasks)} agents from '{agent_id}'.")

                    # --- Append Manager Action Feedback ---
                    if manager_action_feedback:
                         feedback_appended = False
                         for feedback in manager_action_feedback:
                             feedback_content = f"[Manager Result for {feedback['action']} (Call ID: {feedback['call_id']})]: Success={feedback['success']}. Message: {feedback['message']}"
                             if feedback.get("data"):
                                  try: feedback_content += f"\nData: {json.dumps(feedback['data'], indent=2)}"
                                  except TypeError: feedback_content += f"\nData: [Unserializable Data]"
                             feedback_message: MessageDict = { "role": "tool", "tool_call_id": feedback['call_id'], "content": feedback_content }
                             agent.message_history.append(feedback_message); logger.debug(f"Appended manager feedback for call {feedback['call_id']} to '{agent_id}' history.")
                             feedback_appended = True
                         # --- Set flag to reactivate this agent ---
                         if feedback_appended:
                             reactivate_agent_after_feedback = True

                else: logger.warning(f"Unknown event type '{event_type}' from '{agent_id}'.")

        except Exception as e: logger.error(f"Error handling generator for {agent_id}: {e}", exc_info=True); agent.set_status(AGENT_STATUS_ERROR); await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {e}]"})
        finally:
             if agent_generator:
                 try: await agent_generator.aclose(); logger.debug(f"Closed generator for '{agent_id}'.")
                 except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)

             # --- Reactivate agent if feedback was added ---
             if reactivate_agent_after_feedback and agent.status != AGENT_STATUS_ERROR:
                 logger.info(f"Reactivating agent '{agent_id}' to process manager feedback.")
                 agent.set_status(AGENT_STATUS_IDLE) # Ensure it's idle before restarting
                 asyncio.create_task(self._handle_agent_generator(agent))
                 # Don't update status to IDLE here, let the new task handle it
             else:
                 # Update final status only if not reactivating
                 await self.push_agent_status_update(agent_id)
                 logger.info(f"Manager finished handling generator cycle for Agent '{agent_id}'. Final status: {agent.status}")


    async def _handle_manage_team_action(self, action: Optional[str], params: Dict[str, Any]) -> Tuple[bool, str, Optional[Any]]:
        """Dispatches ManageTeamTool actions. Returns (success, message, optional_data)."""
        if not action: return False, "No action specified.", None
        success, message, result_data = False, "Unknown action or error.", None
        created_agent_id = None
        try:
            logger.debug(f"Handling ManageTeam action '{action}' with params: {params}")
            agent_id = params.get("agent_id"); team_id = params.get("team_id")
            provider = params.get("provider"); model = params.get("model")
            system_prompt = params.get("system_prompt"); persona = params.get("persona")
            temperature = params.get("temperature")
            extra_kwargs = {k: v for k, v in params.items() if k not in ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']}

            if action == "create_agent":
                 success, message, created_agent_id = await self.create_agent_instance( agent_id, provider, model, system_prompt, persona, team_id, temperature, **extra_kwargs );
                 if success and created_agent_id: message = f"Agent '{persona}' created with ID '{created_agent_id}'. Status: {message}"; result_data = {"created_agent_id": created_agent_id}
            elif action == "delete_agent": success, message = await self.delete_agent_instance(agent_id)
            elif action == "create_team": success, message = await self.create_new_team(team_id)
            elif action == "delete_team": success, message = await self.delete_existing_team(team_id)
            elif action == "add_agent_to_team": success, message = await self.add_agent_to_team(agent_id, team_id)
            elif action == "remove_agent_from_team": success, message = await self.remove_agent_from_team(agent_id, team_id)
            elif action == "list_agents": result_data = await self.get_agent_info_list(); success = True; message = f"Found {len(result_data)} agents."
            elif action == "list_teams": result_data = await self.get_team_info_dict(); success = True; message = f"Found {len(result_data)} teams."
            else: message = f"Unrecognized action: {action}"; logger.warning(message)
            logger.info(f"ManageTeamTool action '{action}' result: Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e: message = f"Error processing '{action}': {e}"; logger.error(message, exc_info=True); return False, message, None


    def _generate_unique_agent_id(self, prefix="agent") -> str:
        """Generates a unique agent ID."""
        timestamp = int(time.time() * 1000)
        short_uuid = uuid.uuid4().hex[:3]
        while True:
            new_id = f"{prefix}_{timestamp}_{short_uuid}";
            if new_id not in self.agents: return new_id
            timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:3]


    # --- Dynamic Team/Agent Async Methods ---
    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        if not agent_id or agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."
        agent_instance = self.agents.pop(agent_id); team_id = self.agent_to_team.pop(agent_id, None)
        if team_id and team_id in self.teams and agent_id in self.teams[team_id]: self.teams[team_id].remove(agent_id); logger.info(f"Removed '{agent_id}' from team '{team_id}'.")
        await self._close_provider_safe(agent_instance.llm_provider)
        message = f"Agent '{agent_id}' deleted."; logger.info(message)
        await self.send_to_ui({"type": "agent_deleted", "agent_id": agent_id}); return True, message

    async def create_new_team(self, team_id: str) -> Tuple[bool, str]:
        if not team_id: return False, "Team ID empty.";
        if team_id in self.teams: return False, f"Team '{team_id}' exists."
        self.teams[team_id] = []; message = f"Team '{team_id}' created."; logger.info(message)
        await self.send_to_ui({"type": "team_created", "team_id": team_id}); return True, message

    async def delete_existing_team(self, team_id: str) -> Tuple[bool, str]:
        if not team_id: return False, "Team ID empty.";
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        agents_in_team = [aid for aid, tid in self.agent_to_team.items() if tid == team_id]
        if agents_in_team or (team_id in self.teams and self.teams[team_id]): logger.warning(f"Delete team '{team_id}' failed. Agents: {agents_in_team or self.teams[team_id]}."); return False, f"Team '{team_id}' not empty."
        del self.teams[team_id]; message = f"Team '{team_id}' deleted."; logger.info(message)
        await self.send_to_ui({"type": "team_deleted", "team_id": team_id}); return True, message

    async def add_agent_to_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        if not agent_id or not team_id: return False, "Agent/Team ID empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if team_id not in self.teams: await self.create_new_team(team_id)
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
        sender_agent, target_agent = self.agents.get(sender_id), self.agents.get(target_id)
        if not sender_agent or not target_agent: logger.error(f"SendMsg route err: Sender '{sender_id}' or Target '{target_id}' not found."); return None
        sender_team, target_team = self.agent_to_team.get(sender_id), self.agent_to_team.get(target_id)
        if sender_id != BOOTSTRAP_AGENT_ID and (not sender_team or not target_team or sender_team != target_team): logger.warning(f"SendMsg blocked: Agents not in same team."); return None
        elif sender_id == BOOTSTRAP_AGENT_ID: logger.info(f"AdminAI sending message to '{target_id}'.")
        else: logger.info(f"Routing message from '{sender_id}' to '{target_id}' in team '{target_team}'.")
        formatted_message: MessageDict = { "role": "user", "content": f"[From @{sender_id}]: {message_content}" }
        target_agent.message_history.append(formatted_message); logger.debug(f"Appended msg to history of '{target_id}'.")
        if target_agent.status == AGENT_STATUS_IDLE: logger.info(f"Target '{target_id}' is IDLE. Activating..."); return asyncio.create_task(self._handle_agent_generator(target_agent))
        else: logger.info(f"Target '{target_id}' not IDLE ({target_agent.status}). Not activating."); return None

    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict]:
        """ Executes a tool. Returns a dictionary containing call_id, content, and optionally _raw_result. """
        if not self.tool_executor: logger.error(f"ToolExecutor unavailable."); return {"call_id": call_id, "content": "[ToolExec Error: ToolExecutor unavailable]"}
        tool_info = {"name": tool_name, "call_id": call_id}; agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)
        result_data: Optional[Any] = None
        response_dict = {"call_id": call_id, "content": "[Tool Execution Error: Unknown]"}
        try:
            logger.debug(f"Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}'"); result_data = await self.tool_executor.execute_tool(agent.agent_id, agent.sandbox_path, tool_name, tool_args); logger.debug(f"Tool '{tool_name}' completed.")
            if isinstance(result_data, dict) and tool_name == ManageTeamTool.name: response_dict["content"] = result_data.get("message", "Action processed."); response_dict["_raw_result"] = result_data
            elif isinstance(result_data, str): response_dict["content"] = result_data
            elif isinstance(result_data, dict): response_dict["content"] = result_data.get("message", "[Tool Error Content Missing]"); response_dict["_raw_result"] = result_data
            else: logger.error(f"Unexpected result type {type(result_data)} from tool '{tool_name}'"); response_dict["content"] = "[ToolExec Error: Unexpected result type]"
        except Exception as e: error_msg = f"Manager error during _execute_single_tool '{tool_name}': {e}"; logger.error(error_msg, exc_info=True); response_dict["content"] = f"[ToolExec Error: {error_msg}]"
        finally:
            if agent.status == AGENT_STATUS_EXECUTING_TOOL: agent.set_status(AGENT_STATUS_PROCESSING)
        return response_dict


    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        error_content = f"[ToolExec Error: Failed dispatch '{tool_name or 'unknown'}'. Invalid format.]"; final_call_id = call_id or f"invalid_call_{int(time.time())}"; return {"call_id": final_call_id, "content": error_content}

    async def push_agent_status_update(self, agent_id: str):
        agent = self.agents.get(agent_id);
        if agent: state = agent.get_state(); state["team"] = self.agent_to_team.get(agent_id); await self.send_to_ui({ "type": "agent_status_update", "agent_id": agent_id, "status": state })
        else: logger.warning(f"Cannot push status for unknown agent: {agent_id}")

    async def send_to_ui(self, message_data: Dict[str, Any]):
        if not self.send_to_ui_func: logger.warning("UI broadcast func not configured."); return
        try: await self.send_to_ui_func(json.dumps(message_data))
        except TypeError as e: logger.error(f"JSON serialization error: {e}", exc_info=True); logger.debug(f"Data: {message_data}")
        except Exception as e: logger.error(f"Error sending to UI: {e}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        statuses = {};
        for agent_id, agent in self.agents.items(): state = agent.get_state(); state["team"] = self.agent_to_team.get(agent_id); statuses[agent_id] = state
        return statuses


    # --- Session Persistence Methods ---
    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the current state including dynamic agent configs and histories."""
        if not project_name: return False, "Project name empty."
        if not session_name: session_name = f"session_{int(time.time())}"
        session_file = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Saving session to: {session_file}")
        session_data = { "project": project_name, "session": session_name, "timestamp": time.time(), "teams": self.teams, "agent_to_team": self.agent_to_team, "dynamic_agents_config": {}, "agent_histories": {} }
        for agent_id, agent in self.agents.items():
            try: json.dumps(agent.message_history); session_data["agent_histories"][agent_id] = agent.message_history
            except TypeError as e: logger.error(f"History for '{agent_id}' not JSON serializable: {e}."); session_data["agent_histories"][agent_id] = [{"role": "system", "content": f"[History err: {e}]"}]
            if agent_id not in self.bootstrap_agents:
                 try:
                      config_to_save = agent.agent_config.get("config") if hasattr(agent, 'agent_config') else None
                      if config_to_save: session_data["dynamic_agents_config"][agent_id] = config_to_save
                      else: logger.warning(f"No config found for dynamic agent '{agent_id}'. Not saved.")
                 except Exception as e_cfg:
                      logger.warning(f"Error accessing config for dynamic agent '{agent_id}': {e_cfg}. Not saved.")

        try:
            def save_sync():
                session_file.parent.mkdir(parents=True, exist_ok=True)
                with open(session_file, 'w', encoding='utf-8') as f: json.dump(session_data, f, indent=2)
            await asyncio.to_thread(save_sync)
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
            await asyncio.gather(*(self.delete_agent_instance(aid) for aid in dynamic_agents_to_delete), return_exceptions=True)
            self.teams, self.agent_to_team = {}, {}; logger.info("Cleared dynamic state.")
            self.teams = session_data.get("teams", {}); self.agent_to_team = session_data.get("agent_to_team", {}); dynamic_configs = session_data.get("dynamic_agents_config", {}); histories = session_data.get("agent_histories", {})
            logger.info(f"Loading {len(dynamic_configs)} dynamic agents...");
            creation_tasks = [self._create_agent_internal(aid, cfg, False, self.agent_to_team.get(aid)) for aid, cfg in dynamic_configs.items()]
            creation_results = await asyncio.gather(*creation_tasks, return_exceptions=True)
            successful_creations = 0
            for i, result in enumerate(creation_results):
                 agent_id_attempted = list(dynamic_configs.keys())[i]
                 if isinstance(result, Exception): logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {result}", exc_info=result)
                 elif isinstance(result, tuple) and result[0]: successful_creations += 1
                 else: logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {result[1] if isinstance(result, tuple) else 'Unknown error'}")
            logger.info(f"Recreated {successful_creations}/{len(dynamic_configs)} dynamic agents.")
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
        """ Calls cleanup methods on providers. """
        logger.info("Cleaning up LLM providers...")
        active_providers = {agent.llm_provider for agent in self.agents.values()}; logger.info(f"Found {len(active_providers)} unique provider instances.")
        tasks = []
        for provider in active_providers:
             if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
                 tasks.append(asyncio.create_task(self._close_provider_safe(provider)))
        if tasks: await asyncio.gather(*tasks)
        logger.info("LLM Provider cleanup finished.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        """Safely attempts to close a provider session."""
        try: logger.info(f"Closing session: {provider!r}"); await provider.close_session()
        except Exception as e: logger.error(f"Error closing {provider!r}: {e}", exc_info=True)
