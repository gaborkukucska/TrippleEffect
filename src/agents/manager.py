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
        self._initialize_bootstrap_agents()
        logger.info(f"AgentManager initialized. Bootstrap agents: {self.bootstrap_agents}")
        logger.info(f"Initial dynamic state: {len(self.agents)} total agents, {len(self.teams)} teams.")
        if not self.agents:
             logger.warning("AgentManager initialized with zero bootstrap agents. Admin AI might be missing!")

        # Project/Session State
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        # Ensure projects base directory exists (moved from settings for clarity)
        self._ensure_projects_dir()


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
        """
        logger.info("Initializing bootstrap agents from configuration...")
        # Assuming settings.AGENT_CONFIGURATIONS is loaded correctly
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list:
            logger.warning("No bootstrap agent configurations found in settings. Cannot initialize Admin AI.")
            return

        # Ensure the main 'sandboxes' directory exists
        main_sandbox_dir = BASE_DIR / "sandboxes"
        try:
            main_sandbox_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
            logger.error(f"Error creating main sandbox directory: {e}")

        # --- Iterate and initialize configured bootstrap agents ---
        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id:
                logger.warning("Skipping bootstrap agent configuration due to missing 'agent_id'.")
                continue

            logger.info(f"--- Initializing bootstrap agent '{agent_id}' ---")
            try:
                # --- Call internal creation logic ---
                # This reuses the dynamic creation logic but flags it as bootstrap
                success, message, created_agent_id = self._create_agent_internal(
                    agent_id_requested=agent_id, # Use the ID from config
                    agent_config_data=agent_conf_entry.get("config", {}),
                    is_bootstrap=True # Flag this as a bootstrap agent
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


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Routes incoming user messages exclusively to the Admin AI agent (BOOTSTRAP_AGENT_ID).
        """
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
            logger.info(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') is busy (Status: {admin_agent.status}). User message not processed immediately.")
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({
                "type": "error",
                "agent_id": "manager",
                "content": f"Admin AI is currently busy (Status: {admin_agent.status}). Please wait."
            })


    async def _handle_agent_generator(self, agent: Agent):
        """
        Handles the async generator interaction for a single agent's processing cycle.
        Listens for events, executes tools, handles 'send_message' routing,
        and processes signals from 'ManageTeamTool'.
        """
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None

        try:
            agent_generator = agent.process_message() # Message already in history

            while True:
                try:
                    event = await agent_generator.asend(None)
                except StopAsyncIteration:
                    logger.info(f"Agent '{agent_id}' generator finished normally.")
                    break
                except Exception as gen_err:
                     logger.error(f"Error interacting with agent '{agent_id}' generator: {gen_err}", exc_info=True)
                     agent.set_status(AGENT_STATUS_ERROR)
                     await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Agent generator failed: {gen_err}]"})
                     break

                event_type = event.get("type")

                # --- Pass through simple events ---
                if event_type in ["response_chunk", "status", "error", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "error":
                        logger.error(f"Agent '{agent_id}' reported an error, stopping handling.")
                        break
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content})
                            logger.debug(f"Manager: Appended final assistant response for '{agent_id}'.")

                # --- Handle tool requests ---
                elif event_type == "tool_requests":
                    tool_calls_requested = event.get("calls", [])
                    agent_last_response = event.get("raw_assistant_response")

                    # Append assistant response (with XML) to history
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response})
                         logger.debug(f"Manager: Appended assistant response (with tools) for '{agent_id}'.")

                    tool_tasks = []
                    calls_to_process_post_exec: List[Dict] = [] # Store calls needing post-processing (ManageTeam, SendMessage)

                    for call in tool_calls_requested:
                        call_id = call.get("id")
                        tool_name = call.get("name")
                        tool_args = call.get("arguments", {})
                        if call_id and tool_name and isinstance(tool_args, dict):
                            # Store call details for post-processing if it's ManageTeam or SendMessage
                            if tool_name == ManageTeamTool.name or tool_name == "send_message":
                                calls_to_process_post_exec.append(call)
                            # Create execution task for ALL tools
                            task = asyncio.create_task( self._execute_single_tool(agent, call_id, tool_name, tool_args) )
                            tool_tasks.append(task)
                        else:
                            logger.warning(f"Manager: Skipping invalid tool request format from '{agent_id}': {call}")
                            tool_tasks.append(asyncio.create_task(self._failed_tool_result(call_id, tool_name)))

                    if tool_tasks:
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(tool_tasks)} tool(s)..."})
                        tool_results_raw = await asyncio.gather(*tool_tasks)
                        executed_tool_results = [res for res in tool_results_raw if res is not None]
                    else:
                        executed_tool_results = []

                    # Append ALL tool results to sender's history
                    if executed_tool_results:
                        append_count = 0
                        for result in executed_tool_results:
                            # ManageTeamTool returns dict, others return str. We need content for history.
                            result_content = result.get("message") if isinstance(result, dict) else result
                            tool_message: MessageDict = { "role": "tool", "tool_call_id": result["call_id"], "content": str(result_content) } # Ensure content is string
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != result["call_id"]:
                                 agent.message_history.append(tool_message)
                                 append_count += 1
                        logger.debug(f"Manager: Appended {append_count} tool result(s) to history for '{agent_id}'.")

                    # --- Post-Execution Processing (ManageTeam, SendMessage) ---
                    activation_tasks = []
                    for call in calls_to_process_post_exec:
                        call_id = call["id"]
                        tool_name = call["name"]
                        tool_args = call["arguments"]
                        exec_result = next((r for r in executed_tool_results if r["call_id"] == call_id), None)

                        if not exec_result:
                            logger.error(f"Could not find execution result for processed call {call_id} ({tool_name}). Skipping post-processing.")
                            continue

                        # Handle ManageTeamTool signals
                        if tool_name == ManageTeamTool.name and isinstance(exec_result, dict) and exec_result.get("status") == "success":
                            action = exec_result.get("action")
                            params = exec_result.get("params", {})
                            logger.info(f"Manager processing ManageTeamTool action '{action}' from '{agent_id}'.")
                            await self._handle_manage_team_action(action, params)
                            # ManageTeamTool results are confirmations, no activation needed from them directly.

                        # Handle SendMessageTool routing
                        elif tool_name == "send_message" and isinstance(exec_result, str) and not exec_result.startswith("Error:"):
                             target_id = tool_args.get("target_agent_id")
                             msg_content = tool_args.get("message_content")
                             if target_id and msg_content is not None:
                                 activation_task = await self._route_and_activate_agent_message(
                                      sender_id=agent_id, target_id=target_id, message_content=msg_content
                                 )
                                 if activation_task:
                                      activation_tasks.append(activation_task)
                             else: # Should have been caught by tool validation, but check anyway
                                  logger.error(f"SendMessageTool args missing target/content even after successful execution result for call {call_id}. Args: {tool_args}")

                        elif exec_result and ( (isinstance(exec_result, dict) and exec_result.get("status") == "error") or (isinstance(exec_result, str) and exec_result.startswith("Error:")) ):
                             # Log if the tool execution itself failed
                             logger.warning(f"Tool call {call_id} ({tool_name}) executed with error, skipping post-processing. Result: {exec_result}")

                    if activation_tasks:
                         logger.info(f"Manager: Triggered activation for {len(activation_tasks)} agents based on send_message calls from '{agent_id}'.")

                else:
                    logger.warning(f"Manager: Received unknown event type '{event_type}' from agent '{agent_id}'.")

        except Exception as e:
            error_msg = f"Error during manager generator handling for agent {agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            agent.set_status(AGENT_STATUS_ERROR)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {error_msg}]"})
        finally:
             # Cleanup logic (generator closing, status update) remains the same
             if agent_generator:
                 try: await agent_generator.aclose(); logger.debug(f"Closed generator for '{agent_id}'.")
                 except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)
             await self.push_agent_status_update(agent_id)
             logger.info(f"Manager finished handling generator cycle for Agent '{agent_id}'. Final status: {agent.status}")

    # --- Dynamic Agent/Team Management Methods ---

    async def _handle_manage_team_action(self, action: Optional[str], params: Dict[str, Any]):
        """Dispatches ManageTeamTool actions to the corresponding manager methods."""
        if not action: return

        success = False
        message = "Unknown action or error."
        result_data = None # For actions returning data like list_agents

        try:
            if action == "create_agent":
                 success, message, created_agent_id = await self.create_agent_instance(
                     agent_id_requested=params.get("agent_id"), # Optional
                     provider=params.get("provider"),
                     model=params.get("model"),
                     system_prompt=params.get("system_prompt"),
                     persona=params.get("persona"),
                     team_id=params.get("team_id"), # Optional
                     temperature=params.get("temperature") # Optional
                     # Pass other potential config kwargs if added later
                 )
            elif action == "delete_agent":
                success, message = await self.delete_agent_instance(params.get("agent_id"))
            elif action == "create_team":
                 success, message = await self.create_new_team(params.get("team_id"))
            elif action == "delete_team":
                 success, message = await self.delete_existing_team(params.get("team_id"))
            elif action == "add_agent_to_team":
                 success, message = await self.add_agent_to_team(params.get("agent_id"), params.get("team_id"))
            elif action == "remove_agent_from_team":
                 success, message = await self.remove_agent_from_team(params.get("agent_id"), params.get("team_id"))
            elif action == "list_agents":
                 success = True
                 result_data = await self.get_agent_info_list()
                 message = f"Found {len(result_data)} agents."
            elif action == "list_teams":
                 success = True
                 result_data = await self.get_team_info_dict()
                 message = f"Found {len(result_data)} teams."
            else:
                 message = f"Unrecognized ManageTeamTool action: {action}"
                 logger.warning(message)

            logger.info(f"ManageTeamTool action '{action}' result: Success={success}, Message='{message}'")
            # TODO: How to get this feedback (message, result_data) back to the Admin AI?
            # The Admin AI currently only gets the confirmation from the tool's execute().
            # Potential Solution: Modify _handle_agent_generator to append another 'tool' message
            # with the outcome of the manager method *after* it's awaited. Requires careful history management.
            # For now, we just log it.

        except Exception as e:
             message = f"Error processing ManageTeamTool action '{action}': {e}"
             logger.error(message, exc_info=True)
             # Log error, but Admin AI won't see this specific error message directly yet.


    def _generate_unique_agent_id(self, prefix="agent") -> str:
        """Generates a unique agent ID."""
        while True:
            # Generate a short UUID and prefix it
            new_id = f"{prefix}_{uuid.uuid4().hex[:6]}"
            if new_id not in self.agents:
                return new_id

    async def _create_agent_internal(
        self,
        agent_id_requested: Optional[str],
        agent_config_data: Dict[str, Any],
        is_bootstrap: bool = False,
        team_id: Optional[str] = None
        ) -> Tuple[bool, str, Optional[str]]:
        """Internal logic to instantiate an agent and provider."""

        # 1. Determine Agent ID
        if agent_id_requested and agent_id_requested in self.agents:
            return False, f"Agent ID '{agent_id_requested}' already exists.", None
        agent_id = agent_id_requested or self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to generate or provide a valid Agent ID.", None # Should not happen

        # 2. Extract Config
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        system_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        # Collect remaining kwargs for provider
        provider_specific_kwargs = { k: v for k, v in agent_config_data.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url'] }
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]

        # 3. Instantiate Provider
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass: return False, f"Unknown provider '{provider_name}' specified.", None
        base_provider_config = settings.get_provider_config(provider_name)
        # Dynamic agents CANNOT override keys/base_url from config - MUST use env vars/defaults
        final_provider_args = { **base_provider_config, **provider_specific_kwargs }
        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        try:
             # TODO: Implement provider reuse later based on final_provider_args?
             llm_provider_instance = ProviderClass(**final_provider_args)
             logger.info(f"  Instantiated provider {ProviderClass.__name__} for agent '{agent_id}'.")
        except Exception as e:
             logger.error(f"  Failed to instantiate provider {ProviderClass.__name__} for '{agent_id}': {e}", exc_info=True)
             return False, f"Failed to instantiate provider: {e}", None

        # 4. Instantiate Agent
        # Reconstruct the agent entry structure expected by Agent class
        agent_entry = {
            "agent_id": agent_id,
            "config": { # Pass the explicit config details used
                 "provider": provider_name,
                 "model": model,
                 "system_prompt": system_prompt,
                 "persona": persona,
                 "temperature": temperature,
                 **provider_specific_kwargs # Include extra args passed to provider
            }
        }
        try:
            agent = Agent(
                agent_config=agent_entry,
                llm_provider=llm_provider_instance,
                manager=self,
                tool_descriptions_xml=self.tool_descriptions_xml
            )
        except Exception as e:
             logger.error(f"  Failed to instantiate Agent class for '{agent_id}': {e}", exc_info=True)
             # Clean up provider instance? Only if not reused. Complex. Defer for now.
             return False, f"Failed to instantiate agent: {e}", None

        # 5. Ensure Sandbox
        if not agent.ensure_sandbox_exists():
             logger.warning(f"  Failed to ensure sandbox for agent '{agent_id}'. File operations might fail.")
             # Proceed anyway, but log warning

        # 6. Add to Manager State (AFTER successful instantiation)
        self.agents[agent_id] = agent

        # 7. Add to Team if specified
        if team_id:
             team_add_success, team_add_msg = await self.add_agent_to_team(agent_id, team_id)
             if not team_add_success:
                 # Agent created, but failed to add to team. Log warning.
                 logger.warning(f"Agent '{agent_id}' created, but failed to add to team '{team_id}': {team_add_msg}")
                 # Proceed with agent creation, but team association failed.

        return True, f"Agent '{agent_id}' created successfully.", agent_id


    async def create_agent_instance(self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs) -> Tuple[bool, str, Optional[str]]:
        """Public method called by Admin AI (via tool) to create a dynamic agent."""
        if not provider or not model or not system_prompt or not persona:
            return False, "Missing required parameters (provider, model, system_prompt, persona) for create_agent.", None

        # Construct config dict from params
        agent_config_data = {
            "provider": provider, "model": model, "system_prompt": system_prompt,
            "persona": persona, **kwargs # Include any other potential kwargs from tool
        }
        if temperature is not None: agent_config_data["temperature"] = temperature

        success, message, created_agent_id = await self._create_agent_internal(
            agent_id_requested=agent_id_requested,
            agent_config_data=agent_config_data,
            is_bootstrap=False, # Dynamic agent
            team_id=team_id # Pass team_id for association
        )

        if success and created_agent_id:
            await self.send_to_ui({
                "type": "agent_added",
                "agent_id": created_agent_id,
                "config": agent_config_data, # Send config used
                "team": self.agent_to_team.get(created_agent_id)
            })
        return success, message, created_agent_id


    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        """Public method called by Admin AI to delete a dynamic agent."""
        if not agent_id or agent_id not in self.agents:
            return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents:
            return False, f"Cannot delete bootstrap agent '{agent_id}'."

        agent_instance = self.agents.pop(agent_id) # Remove from active agents
        team_id = self.agent_to_team.pop(agent_id, None) # Remove from team map

        if team_id and team_id in self.teams and agent_id in self.teams[team_id]:
             self.teams[team_id].remove(agent_id)
             logger.info(f"Removed agent '{agent_id}' from team '{team_id}'.")

        # Cleanup provider session (important!)
        provider = agent_instance.llm_provider
        if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
            try:
                logger.info(f"Closing provider session for deleted agent '{agent_id}'...")
                await provider.close_session()
            except Exception as e:
                logger.error(f"Error closing provider session for deleted agent '{agent_id}': {e}", exc_info=True)

        # TODO: Consider cleaning up sandbox directory? Or leave it?

        message = f"Agent '{agent_id}' deleted successfully."
        logger.info(message)
        await self.send_to_ui({"type": "agent_deleted", "agent_id": agent_id})
        return True, message

    async def create_new_team(self, team_id: str) -> Tuple[bool, str]:
        """Creates a new, empty team."""
        if not team_id: return False, "Team ID cannot be empty."
        if team_id in self.teams: return False, f"Team '{team_id}' already exists."

        self.teams[team_id] = []
        message = f"Team '{team_id}' created successfully."
        logger.info(message)
        await self.send_to_ui({"type": "team_created", "team_id": team_id})
        return True, message

    async def delete_existing_team(self, team_id: str) -> Tuple[bool, str]:
        """Deletes a team. Fails if team is not empty."""
        if not team_id: return False, "Team ID cannot be empty."
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        if self.teams[team_id]: return False, f"Cannot delete team '{team_id}' because it is not empty. Remove agents first."

        del self.teams[team_id]
        message = f"Team '{team_id}' deleted successfully."
        logger.info(message)
        await self.send_to_ui({"type": "team_deleted", "team_id": team_id})
        return True, message

    async def add_agent_to_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        """Adds an existing agent to a team."""
        if not agent_id or not team_id: return False, "Agent ID and Team ID cannot be empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        if agent_id in self.agent_to_team and self.agent_to_team[agent_id] == team_id:
             return True, f"Agent '{agent_id}' is already in team '{team_id}'." # Not an error
        if agent_id in self.agent_to_team: # Agent is in another team
             old_team = self.agent_to_team[agent_id]
             if old_team in self.teams and agent_id in self.teams[old_team]:
                 self.teams[old_team].remove(agent_id)
             logger.info(f"Removed agent '{agent_id}' from previous team '{old_team}'.")

        if agent_id not in self.teams[team_id]:
            self.teams[team_id].append(agent_id)
        self.agent_to_team[agent_id] = team_id

        message = f"Agent '{agent_id}' added to team '{team_id}'."
        logger.info(message)
        await self.send_to_ui({
            "type": "agent_moved_team",
            "agent_id": agent_id,
            "new_team_id": team_id,
            "old_team_id": old_team if 'old_team' in locals() else None
        })
        # Update agent's individual status too
        await self.push_agent_status_update(agent_id)
        return True, message

    async def remove_agent_from_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        """Removes an agent from a specific team."""
        if not agent_id or not team_id: return False, "Agent ID and Team ID cannot be empty."
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        if agent_id not in self.agent_to_team or self.agent_to_team[agent_id] != team_id:
             return False, f"Agent '{agent_id}' is not currently in team '{team_id}'."

        if agent_id in self.teams[team_id]:
             self.teams[team_id].remove(agent_id)
        del self.agent_to_team[agent_id]

        message = f"Agent '{agent_id}' removed from team '{team_id}'."
        logger.info(message)
        await self.send_to_ui({"type": "agent_moved_team", "agent_id": agent_id, "new_team_id": None, "old_team_id": team_id})
        await self.push_agent_status_update(agent_id) # Update status to show no team
        return True, message

    async def get_agent_info_list(self) -> List[Dict[str, Any]]:
        """Returns basic info for all currently active agents."""
        info_list = []
        for agent_id, agent in self.agents.items():
             state = agent.get_state()
             info = {
                 "agent_id": agent_id,
                 "persona": state.get("persona"),
                 "provider": state.get("provider"),
                 "model": state.get("model"),
                 "status": state.get("status"),
                 "team": self.agent_to_team.get(agent_id)
             }
             info_list.append(info)
        return info_list

    async def get_team_info_dict(self) -> Dict[str, List[str]]:
        """Returns a copy of the current team structure."""
        return self.teams.copy()


    # --- Tool Execution and Routing (Minor change for ManageTeamTool result) ---

    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict | str]:
        """ Executes a tool. Returns dict for ManageTeamTool, str otherwise. """
        if not self.tool_executor:
             logger.error(f"ToolExecutor not available for agent '{agent.agent_id}'.")
             return {"call_id": call_id, "content": "[Tool Execution Error: ToolExecutor unavailable]"} # Return structured error

        tool_info = {"name": tool_name, "call_id": call_id}
        agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)

        result_data: Optional[Dict | str] = None # Store result (can be dict or str)
        try:
            logger.debug(f"Manager: Executing tool '{tool_name}' (Call ID: {call_id}) for agent '{agent.agent_id}'")
            # ToolExecutor now returns dict for ManageTeamTool, str otherwise
            result_data = await self.tool_executor.execute_tool(
                agent_id=agent.agent_id,
                agent_sandbox_path=agent.sandbox_path,
                tool_name=tool_name,
                tool_args=tool_args
            )
            logger.debug(f"Tool '{tool_name}' (Call ID: {call_id}) execution completed for agent '{agent.agent_id}'.")

            # Package result appropriately for history (ToolResultDict expects 'content')
            if isinstance(result_data, dict): # ManageTeamTool success/error dict
                 # History gets the 'message' part, post-processing uses the full dict
                 return {"call_id": call_id, "content": result_data.get("message", "ManageTeamTool action processed."), "_raw_result": result_data}
            elif isinstance(result_data, str): # Other tools or tool not found string
                 return {"call_id": call_id, "content": result_data}
            else:
                 logger.error(f"Unexpected result type from ToolExecutor for tool '{tool_name}': {type(result_data)}")
                 return {"call_id": call_id, "content": "[Tool Execution Error: Unexpected result type]"}

        except Exception as e:
            error_msg = f"Manager error during _execute_single_tool '{tool_name}' (Call ID: {call_id}) for agent '{agent.agent_id}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            # Return error content for history
            return {"call_id": call_id, "content": f"[Tool Execution Error: {error_msg}]"}
        finally:
            if agent.status == AGENT_STATUS_EXECUTING_TOOL:
                 agent.set_status(AGENT_STATUS_PROCESSING)


    # --- Other existing methods (_failed_tool_result, push_agent_status_update, send_to_ui, get_agent_status) remain largely the same ---
    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        error_content = f"[Tool Execution Error: Failed to dispatch tool '{tool_name or 'unknown'}'. Invalid format.]"
        final_call_id = call_id or f"invalid_xml_call_{int(time.time())}_{os.urandom(2).hex()}"
        return {"call_id": final_call_id, "content": error_content}

    async def push_agent_status_update(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if agent:
            status_data = agent.get_state(); status_data["team"] = self.agent_to_team.get(agent_id, None); # Team can be None
            await self.send_to_ui({ "type": "agent_status_update", "agent_id": agent_id, "status": status_data })
        else: logger.warning(f"Manager: Cannot push status for unknown agent_id: {agent_id}")

    async def send_to_ui(self, message_data: Dict[str, Any]):
        if not self.send_to_ui_func: logger.warning("UI broadcast func not configured."); return
        try:
            if 'agent_id' not in message_data and message_data.get('type') == 'error': message_data['agent_id'] = 'manager'
            await self.send_to_ui_func(json.dumps(message_data))
        except TypeError as e: logger.error(f"JSON serialization error: {e}", exc_info=True); logger.debug(f"Data: {message_data}") # Log data
        except Exception as e: logger.error(f"Error sending to UI: {e}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        statuses = {};
        for agent_id, agent in self.agents.items(): state = agent.get_state(); state["team"] = self.agent_to_team.get(agent_id, None); statuses[agent_id] = state
        return statuses


    # --- Session Persistence Methods (Adapted for Dynamic Configs) ---

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the current state including dynamic agent configs and histories."""
        if not project_name: return False, "Project name cannot be empty."
        if not session_name: session_name = f"session_{int(time.time())}"
        histories_file = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json" # Renamed file
        logger.info(f"Attempting to save session to: {histories_file}")

        session_data = {
            "project": project_name, "session": session_name, "timestamp": time.time(),
            "teams": self.teams, # Save current team structure
            "agent_to_team": self.agent_to_team, # Save agent mappings
            "dynamic_agents_config": {}, # Configs for dynamically created agents
            "agent_histories": {} # Histories for ALL agents (bootstrap + dynamic)
        }

        for agent_id, agent in self.agents.items():
            # Save history for all agents
            try:
                json.dumps(agent.message_history) # Check serializability
                session_data["agent_histories"][agent_id] = agent.message_history
            except TypeError as e:
                 logger.error(f"History for agent '{agent_id}' not JSON serializable: {e}. Saving error placeholder.")
                 session_data["agent_histories"][agent_id] = [{"role": "system", "content": f"[History serialization error: {e}]"}]

            # Save config ONLY for dynamic agents
            if agent_id not in self.bootstrap_agents:
                 # Extract config from agent instance (needs agent to store it or provide getter)
                 # Assuming agent stores its initial config dict: agent.agent_config["config"]
                 try:
                      # We need the config used to create the agent, which _create_agent_internal reconstructs
                      # Let's try to access it via the agent object directly for now
                      config_to_save = agent.agent_config.get("config") # Get the 'config' sub-dict
                      if config_to_save:
                           session_data["dynamic_agents_config"][agent_id] = config_to_save
                      else:
                           logger.warning(f"Could not retrieve config sub-dictionary for dynamic agent '{agent_id}'. Config not saved.")
                 except AttributeError:
                       logger.warning(f"Agent object for '{agent_id}' missing 'agent_config' attribute. Config not saved.")
        try:
            def save_sync():
                histories_file.parent.mkdir(parents=True, exist_ok=True)
                with open(histories_file, 'w', encoding='utf-8') as f:
                    json.dump(session_data, f, indent=2)
            await asyncio.to_thread(save_sync)
            logger.info(f"Session saved successfully to {histories_file}")
            self.current_project = project_name; self.current_session = session_name
            return True, f"Session '{session_name}' saved in project '{project_name}'."
        except Exception as e: logger.error(f"Error saving session to {histories_file}: {e}", exc_info=True); return False, f"Error saving session: {e}"


    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Loads dynamic agents, teams, and histories from a saved session file."""
        histories_file = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Attempting to load session from: {histories_file}")
        if not histories_file.is_file(): return False, f"Session file '{session_name}' not found in project '{project_name}'."

        try:
            def load_sync():
                 with open(histories_file, 'r', encoding='utf-8') as f: return json.load(f)
            session_data = await asyncio.to_thread(load_sync)

            # --- Clear existing dynamic state ---
            dynamic_agents_to_delete = [aid for aid in self.agents if aid not in self.bootstrap_agents]
            logger.info(f"Clearing existing dynamic agents: {dynamic_agents_to_delete}")
            delete_tasks = [self.delete_agent_instance(aid) for aid in dynamic_agents_to_delete]
            await asyncio.gather(*delete_tasks) # Wait for deletions
            self.teams = {} # Clear teams map
            self.agent_to_team = {} # Clear reverse map
            logger.info("Cleared existing dynamic agent state.")

            # --- Load and Rebuild Dynamic State ---
            self.teams = session_data.get("teams", {})
            self.agent_to_team = session_data.get("agent_to_team", {})
            dynamic_agents_config = session_data.get("dynamic_agents_config", {})
            agent_histories = session_data.get("agent_histories", {})

            logger.info(f"Loading {len(dynamic_agents_config)} dynamic agents...")
            creation_tasks = []
            created_agent_ids = set()
            for agent_id, config_data in dynamic_agents_config.items():
                # Recreate agent using internal method (team assignment handled by loaded maps)
                creation_tasks.append(self._create_agent_internal(
                    agent_id_requested=agent_id,
                    agent_config_data=config_data,
                    is_bootstrap=False,
                    team_id=self.agent_to_team.get(agent_id) # Ensure team association if loaded
                ))

            results = await asyncio.gather(*creation_tasks)
            successful_creations = 0
            for success, msg, created_id in results:
                 if success and created_id:
                      successful_creations += 1
                      created_agent_ids.add(created_id)
                 else:
                      logger.error(f"Failed to recreate agent from session data: {msg}")

            logger.info(f"Recreated {successful_creations}/{len(dynamic_agents_config)} dynamic agents.")

            # --- Load Histories ---
            loaded_history_count = 0
            for agent_id, history in agent_histories.items():
                agent = self.agents.get(agent_id) # Check if agent exists (bootstrap or recreated dynamic)
                if agent and isinstance(history, list):
                     # Simple validation
                     if all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in history):
                          agent.message_history = history
                          agent.set_status(AGENT_STATUS_IDLE) # Reset status
                          loaded_history_count += 1
                     else: logger.warning(f"Invalid history format for agent '{agent_id}'. Skipping.")
                elif agent:
                     logger.warning(f"History data for agent '{agent_id}' is not a list. Skipping.")
                # else: logger.warning(f"Agent '{agent_id}' from history not found after load. Skipping history.")

            logger.info(f"Loaded histories for {loaded_history_count} agents.")

            self.current_project = project_name
            self.current_session = session_name
            await asyncio.gather(*(self.push_agent_status_update(aid) for aid in self.agents.keys())) # Update UI for all
            return True, f"Session '{session_name}' loaded. {successful_creations} dynamic agents recreated."

        except json.JSONDecodeError as e: logger.error(f"Error decoding JSON: {e}"); return False, f"Invalid session file format."
        except Exception as e: logger.error(f"Error loading session: {e}", exc_info=True); return False, f"Error loading session: {e}"

    # --- End Session Persistence ---

    async def cleanup_providers(self):
        """ Calls cleanup methods on providers. """
        logger.info("Cleaning up LLM providers...")
        # Create list of providers currently in use
        active_providers = {agent.llm_provider for agent in self.agents.values()}
        logger.info(f"Found {len(active_providers)} unique provider instances to clean up.")
        for provider in active_providers:
             if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
                 try: logger.info(f"Closing session for provider instance: {provider!r}"); await provider.close_session()
                 except Exception as e: logger.error(f"Error closing provider session {provider!r}: {e}", exc_info=True)
        logger.info("LLM Provider cleanup finished.")
