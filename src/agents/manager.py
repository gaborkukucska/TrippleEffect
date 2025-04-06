# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple
import json
import os
import traceback
import time
import logging
import uuid # For generating agent IDs
import re # For replacing team ID in prompts

# Import Agent class, Status constants, and BaseLLMProvider types
from src.agents.core import Agent, AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_ERROR
from src.llm_providers.base import BaseLLMProvider, ToolResultDict, MessageDict

# Import settings instance, BASE_DIR, and default values
from src.config.settings import settings, BASE_DIR

# Import WebSocket broadcast function
from src.api.websocket_manager import broadcast

# Import ToolExecutor and Tool base class/types
from src.tools.executor import ToolExecutor
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool # Need this for tool name check
from src.tools.file_system import FileSystemTool # Need this for tool name check

# Import Provider classes
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

# --- Import the new Managers ---
from src.agents.state_manager import AgentStateManager
from src.agents.session_manager import SessionManager

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

# Standard framework instructions (remains the same)
STANDARD_FRAMEWORK_INSTRUCTIONS = """

--- Standard Tool & Communication Protocol ---
You have access to the following tools (use the XML format described in the main prompt):
- `file_system`: Read, write, list files in your sandbox. Paths are relative.
- `send_message`: Communicate with other agents in your team. Specify `target_agent_id` and `message_content`.

Your Agent ID: `{agent_id}`
Your Team ID: `{team_id}` (if assigned)

Respond to messages directed to you. Use tools appropriately to fulfill your role.
--- End Standard Protocol ---
"""


class AgentManager:
    """
    Main coordinator for agents. Handles task distribution, agent lifecycle (creation/deletion),
    tool execution routing, and orchestrates state/session management via dedicated managers.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        """
        Initializes the AgentManager, ToolExecutor, StateManager, and SessionManager.
        """
        self.bootstrap_agents: List[str] = [] # List of bootstrap agent IDs
        self.agents: Dict[str, Agent] = {} # Holds ALL active Agent instances

        self.send_to_ui_func = broadcast # Function to send updates to UI

        logger.info("Instantiating ToolExecutor...")
        self.tool_executor = ToolExecutor()
        logger.info("ToolExecutor instantiated.")

        # Get formatted XML tool descriptions once
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        logger.info("Generated XML tool descriptions for prompts.")

        # --- Instantiate State and Session Managers ---
        logger.info("Instantiating AgentStateManager...")
        self.state_manager = AgentStateManager(self) # Pass self reference
        logger.info("AgentStateManager instantiated.")

        logger.info("Instantiating SessionManager...")
        self.session_manager = SessionManager(self, self.state_manager) # Pass refs
        logger.info("SessionManager instantiated.")
        # --- End Instantiation ---

        # Project/Session Tracking (remains here for now)
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        self._ensure_projects_dir() # Synchronous check

        logger.info("AgentManager initialized synchronously. Bootstrap agents will be loaded asynchronously.")


    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data."""
        # This can remain here or be moved fully to SessionManager init if preferred
        try:
             settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)


    # --- ASYNCHRONOUS Bootstrap Initialization (remains largely the same) ---
    async def initialize_bootstrap_agents(self):
        """
        ASYNCHRONOUSLY loads bootstrap agents from settings.AGENT_CONFIGURATIONS.
        Injects allowed model list into Admin AI's prompt.
        """
        logger.info("Initializing bootstrap agents asynchronously...")
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list: logger.warning("No bootstrap agent configurations found in settings."); return

        main_sandbox_dir = BASE_DIR / "sandboxes"
        try: await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True); logger.info(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e: logger.error(f"Error creating main sandbox directory: {e}")

        tasks = []; formatted_allowed_models = settings.get_formatted_allowed_models()

        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id: logger.warning("Skipping bootstrap agent configuration due to missing 'agent_id'."); continue
            agent_config_data = agent_conf_entry.get("config", {})
            if agent_id == BOOTSTRAP_AGENT_ID:
                original_prompt = agent_config_data.get("system_prompt", ""); agent_config_data = agent_config_data.copy()
                agent_config_data["system_prompt"] = original_prompt + "\n\n" + formatted_allowed_models
                logger.info(f"Injected allowed models list into '{BOOTSTRAP_AGENT_ID}' system prompt.")
            tasks.append(self._create_agent_internal( agent_id_requested=agent_id, agent_config_data=agent_config_data, is_bootstrap=True ))

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


    # --- *** CORRECTED _create_agent_internal with SyntaxError Fix *** ---
    async def _create_agent_internal(
        self, agent_id_requested: Optional[str], agent_config_data: Dict[str, Any], is_bootstrap: bool = False, team_id: Optional[str] = None, loading_from_session: bool = False
        ) -> Tuple[bool, str, Optional[str]]:
        """Internal logic to instantiate agent, provider, sandbox. Delegates team assignment state."""
        # 1. Determine Agent ID
        if agent_id_requested and agent_id_requested in self.agents: return False, f"Agent ID '{agent_id_requested}' already exists.", None
        agent_id = agent_id_requested or self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to generate Agent ID.", None
        logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session})")

        # 2. Extract Config & Validate Provider/Model
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        allowed_provider_keys = ['api_key', 'base_url', 'referer']
        provider_specific_kwargs = { k: v for k, v in agent_config_data.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona'] + allowed_provider_keys}
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]

        if not is_bootstrap and not loading_from_session: # Validation
            allowed_models = settings.ALLOWED_SUB_AGENT_MODELS.get(provider_name)
            if allowed_models is None: msg = f"Validation Error: Provider '{provider_name}' not configured."; logger.error(msg); return False, msg, None
            if model not in allowed_models: msg = f"Validation Error: Model '{model}' not allowed for '{provider_name}'. Allowed: [{', '.join(allowed_models)}]"; logger.error(msg); return False, msg, None
            logger.info(f"Dynamic agent creation validated: Provider '{provider_name}', Model '{model}' is allowed.")

        # 3. Construct Final Prompt
        final_system_prompt = role_specific_prompt
        if not is_bootstrap and not loading_from_session:
            standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(agent_id=agent_id, team_id=team_id or "N/A")
            final_system_prompt = role_specific_prompt + "\n" + standard_info
            logger.debug(f"Constructed final prompt for dynamic agent '{agent_id}'.")

        final_agent_config_entry = { "agent_id": agent_id, "config": { "provider": provider_name, "model": model, "system_prompt": final_system_prompt, "persona": persona, "temperature": temperature, **provider_specific_kwargs } }

        # 4. Instantiate Provider
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass: return False, f"Unknown provider '{provider_name}'.", None
        base_provider_config = settings.get_provider_config(provider_name); provider_config_overrides = {k: agent_config_data[k] for k in allowed_provider_keys if k in agent_config_data}
        final_provider_args = { **base_provider_config, **provider_specific_kwargs, **provider_config_overrides}; final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        try: llm_provider_instance = ProviderClass(**final_provider_args); logger.info(f"  Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")
        except Exception as e: logger.error(f"  Provider instantiation failed for '{agent_id}': {e}", exc_info=True); return False, f"Provider instantiation failed: {e}", None

        # 5. Instantiate Agent
        try:
            agent = Agent( agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=self, tool_descriptions_xml=self.tool_descriptions_xml )
            agent.agent_config = final_agent_config_entry # Store final config on agent
            logger.info(f"  Instantiated Agent object for '{agent_id}'.")
        except Exception as e: logger.error(f"  Agent instantiation failed for '{agent_id}': {e}", exc_info=True); return False, f"Agent instantiation failed: {e}", None

        # 6. Ensure Sandbox
        try:
            sandbox_ok = await asyncio.to_thread(agent.ensure_sandbox_exists)
            if not sandbox_ok:
                 logger.warning(f"  Failed to ensure sandbox for '{agent_id}'.")
        except Exception as e:
            logger.error(f"Sandbox error for '{agent_id}': {e}", exc_info=True)
            logger.warning(f"Proceeding without guaranteed sandbox for '{agent_id}'.")

        # --- 7. Add agent instance to registry BEFORE assigning team state ---
        self.agents[agent_id] = agent
        logger.debug(f"Agent '{agent_id}' added to self.agents dictionary.")

        # --- 8. Assign to Team State via StateManager ---
        team_add_msg_suffix = ""
        if team_id:
            # Update agent's internal prompt if dynamically created now
            if not loading_from_session and not is_bootstrap:
                 # --- **** CORRECTED TRY/EXCEPT FOR PROMPT UPDATE **** ---
                 try:
                     new_team_str = f"Your Team ID: {team_id}"
                     agent.final_system_prompt = re.sub(r"Your Team ID:.*", new_team_str, agent.final_system_prompt)
                     agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt
                     # Check history exists and is not empty before accessing index 0
                     if agent.message_history and agent.message_history[0]["role"] == "system":
                          agent.message_history[0]["content"] = agent.final_system_prompt
                 except Exception as e:
                      logger.error(f"Error updating team ID in dynamic agent prompt for {agent_id}: {e}")
                 # --- **** END CORRECTION **** ---

            # Delegate actual state update
            team_add_success, team_add_msg = await self.state_manager.add_agent_to_team(agent_id, team_id)
            if not team_add_success:
                team_add_msg_suffix = f" (Warning adding to team state: {team_add_msg})"
            else:
                logger.info(f"Agent '{agent_id}' state added to team '{team_id}' via StateManager.")

        message = f"Agent '{agent_id}' created successfully." + team_add_msg_suffix
        return True, message, agent_id
    # --- *** END CORRECTED _create_agent_internal *** ---


    async def create_agent_instance( # Public method remains the same
        self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs
        ) -> Tuple[bool, str, Optional[str]]:
        """Public method to create dynamic agents. Calls internal logic."""
        if not provider or not model or not system_prompt or not persona: return False, "Missing required params (provider, model, system_prompt, persona).", None
        agent_config_data = { "provider": provider, "model": model, "system_prompt": system_prompt, "persona": persona }
        if temperature is not None: agent_config_data["temperature"] = temperature
        known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
        extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args}; agent_config_data.update(extra_kwargs)
        success, message, created_agent_id = await self._create_agent_internal( agent_id_requested=agent_id_requested, agent_config_data=agent_config_data, is_bootstrap=False, team_id=team_id, loading_from_session=False )
        if success and created_agent_id: # Notify UI
            created_agent = self.agents.get(created_agent_id); config_sent_to_ui = created_agent.agent_config.get("config", {}) if created_agent and hasattr(created_agent, 'agent_config') else {}
            await self.send_to_ui({ "type": "agent_added", "agent_id": created_agent_id, "config": config_sent_to_ui, "team": self.state_manager.get_agent_team(created_agent_id) }) # Use state_manager getter
        return success, message, created_agent_id

    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        """Deletes a dynamic agent instance and cleans up state."""
        if not agent_id: return False, "Agent ID cannot be empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."

        agent_instance = self.agents.pop(agent_id) # Remove from active agents
        # Delegate team state cleanup
        self.state_manager.remove_agent_from_all_teams_state(agent_id)
        # Close provider session
        await self._close_provider_safe(agent_instance.llm_provider)
        # Optional: Sandbox cleanup?

        message = f"Agent '{agent_id}' deleted successfully."
        logger.info(message)
        await self.send_to_ui({"type": "agent_deleted", "agent_id": agent_id}) # Notify UI
        return True, message

    def _generate_unique_agent_id(self, prefix="agent") -> str:
        """Generates unique agent ID."""
        timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]
        while True:
            new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_");
            if new_id not in self.agents: return new_id
            time.sleep(0.001); timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]


    # --- Async Message/Task Handling (Remains in AgentManager) ---
    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """Routes user messages to Admin AI."""
        logger.info(f"AgentManager received user message for Admin AI: '{message[:100]}...'")
        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent: logger.error(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') not found."); await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."}); return
        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Delegating user message to '{BOOTSTRAP_AGENT_ID}'.")
            admin_agent.message_history.append({"role": "user", "content": message})
            asyncio.create_task(self._handle_agent_generator(admin_agent))
        else:
            logger.info(f"Admin AI busy ({admin_agent.status}). User message queued implicitly in history.")
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Your message will be processed when idle." })


    # --- **** CORRECTED _handle_agent_generator for Reactivation Timing **** ---
    async def _handle_agent_generator(self, agent: Agent):
        """Handles the async generator interaction for a single agent's processing cycle."""
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback = [] # Feedback from ManageTeamTool actions
        reactivate_agent_after_feedback = False # Flag to re-run generator after adding feedback
        current_cycle_error = False # Track if an error occurred in this cycle

        try:
            agent_generator = agent.process_message() # Get the generator

            while True:
                try:
                    event = await agent_generator.asend(None) # Use asend(None)
                except StopAsyncIteration:
                    logger.info(f"Agent '{agent_id}' generator finished normally.")
                    break # Normal finish
                except Exception as gen_err:
                    logger.error(f"Generator error for '{agent_id}': {gen_err}", exc_info=True); agent.set_status(AGENT_STATUS_ERROR); await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Generator crashed - {gen_err}]"});
                    current_cycle_error = True # Mark error occurred
                    break # Error finish

                event_type = event.get("type")

                # --- Handle Standard Events ---
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content}); logger.debug(f"Appended final response for '{agent_id}'.")

                elif event_type == "error": # Handle errors yielded by agent/provider
                    error_content = event.get("content", "[Unknown Agent Error]")
                    logger.error(f"Agent '{agent_id}' reported error: {error_content}")
                    is_stream_error = any(indicator in error_content for indicator in ["Error processing stream chunk", "APIError during stream", "Failed to decode stream chunk", "Stream connection error"])
                    if is_stream_error:
                        logger.warning(f"Detected temporary stream error for agent '{agent_id}'. Resetting to idle.")
                        await self.send_to_ui({ "type": "error", "agent_id": agent_id, "content": f"[Manager Note]: Agent '{agent.persona}' experienced a temporary provider issue. Resetting to idle. Retry needed. (Details: {error_content})" })
                        agent.set_status(AGENT_STATUS_IDLE) # Reset status
                        current_cycle_error = True # Mark as error for finally block logic
                    else: # Permanent error
                        if "agent_id" not in event: event["agent_id"] = agent_id
                        await self.send_to_ui(event)
                        agent.set_status(AGENT_STATUS_ERROR)
                        current_cycle_error = True # Mark as error
                    break # Stop generator loop on any error

                elif event_type == "tool_requests": # Handle Sequential Tool Execution
                    all_tool_calls = event.get("calls", [])
                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response}); logger.debug(f"Appended assistant response (with tools) for '{agent_id}'.")

                    management_calls = []; other_calls = []; executed_results_map = {}; invalid_call_results = []
                    # 1. Validate and Categorize Calls
                    for call in all_tool_calls:
                         call_id, tool_name, tool_args = call.get("id"), call.get("name"), call.get("arguments", {})
                         if call_id and tool_name and isinstance(tool_args, dict):
                            if tool_name == ManageTeamTool.name: management_calls.append(call)
                            else: other_calls.append(call)
                         else:
                            logger.warning(f"Skipping invalid tool request from '{agent_id}': {call}")
                            fail_result = await self._failed_tool_result(call_id, tool_name)
                            if fail_result: invalid_call_results.append(fail_result)
                    if invalid_call_results: # Append failures
                        for fail_res in invalid_call_results: agent.message_history.append({"role": "tool", "tool_call_id": fail_res['call_id'], "content": str(fail_res['content']) })

                    manager_action_feedback = [] # Reset feedback for this batch
                    activation_tasks = [] # Reset activation tasks

                    # 2. Execute Management Calls Sequentially
                    if management_calls:
                        logger.info(f"Executing {len(management_calls)} management tool call(s) sequentially for agent '{agent_id}'.")
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(management_calls)} management tool(s)..."})
                        for call in management_calls:
                            result = await self._execute_single_tool(agent, call['id'], call['name'], call['arguments'])
                            if result: executed_results_map[call['id']] = result
                        logger.info(f"Finished executing management tool calls for agent '{agent_id}'.")

                        # 3. Process Management Results & Generate Feedback
                        for call in management_calls:
                            call_id = call['id']; result = executed_results_map.get(call_id)
                            if not result: continue
                            raw_content_for_hist = result.get("content", "[Tool Error: No content]")
                            tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_for_hist) }
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id:
                                agent.message_history.append(tool_msg); logger.debug(f"Appended raw mgmt tool result for {call_id}.")
                            raw_tool_output = result.get("_raw_result")
                            if call['name'] == ManageTeamTool.name: # Only ManageTeamTool requires manager action
                                if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                    action = raw_tool_output.get("action"); params = raw_tool_output.get("params", {})
                                    logger.info(f"Processing successful ManageTeamTool execution: Action='{action}' by '{agent_id}'.")
                                    action_success, action_message, action_data = await self._handle_manage_team_action(action, params)
                                    feedback = {"call_id": call_id, "action": action, "success": action_success, "message": action_message}
                                    if action_data: feedback["data"] = action_data
                                    manager_action_feedback.append(feedback)
                                elif isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "error":
                                     logger.warning(f"ManageTeamTool call {call_id} failed validation/execution. Raw Result: {raw_tool_output}")
                                     manager_action_feedback.append({"call_id": call_id, "action": raw_tool_output.get("action"), "success": False, "message": raw_tool_output.get("message", "Tool execution failed.")})
                                else: logger.warning(f"ManageTeamTool call {call_id} had unexpected structure: {result}"); manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result structure."})

                    # 4. Execute Other Tool Calls Sequentially
                    if other_calls:
                        logger.info(f"Executing {len(other_calls)} other tool call(s) sequentially for agent '{agent_id}'.")
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(other_calls)} other tool(s)..."})
                        for call in other_calls:
                             result = await self._execute_single_tool(agent, call['id'], call['name'], call['arguments'])
                             if result: executed_results_map[call['id']] = result
                        logger.info(f"Finished executing other tool calls for agent '{agent_id}'.")
                        for call in other_calls: # Process results
                            result = executed_results_map.get(call['id'])
                            if not result: continue
                            raw_content_for_hist = result.get("content", "[Tool Error: No content]")
                            tool_msg: MessageDict = {"role": "tool", "tool_call_id": call['id'], "content": str(raw_content_for_hist) }
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call['id']:
                                agent.message_history.append(tool_msg); logger.debug(f"Appended raw other tool result for {call['id']}.")
                            if call['name'] == SendMessageTool.name: # Process SendMessage specifically
                                target_id = call['arguments'].get("target_agent_id"); msg_content = call['arguments'].get("message_content")
                                if target_id and msg_content is not None:
                                    if target_id in self.agents: activation_task = await self._route_and_activate_agent_message(agent_id, target_id, msg_content);
                                    if activation_task: activation_tasks.append(activation_task)
                                    else: logger.error(f"SendMessage failed: Target agent '{target_id}' not found after management actions."); manager_action_feedback.append({"call_id": call['id'], "action": "send_message", "success": False, "message": f"Failed to send: Target agent '{target_id}' not found."})
                                else: logger.error(f"SendMessage args incomplete for call {call['id']}. Args: {call['arguments']}"); manager_action_feedback.append({"call_id": call['id'], "action": "send_message", "success": False, "message": "Missing target_agent_id or message_content."})

                    # 5. Wait for any agent activations
                    if activation_tasks: await asyncio.gather(*activation_tasks); logger.info(f"Completed activation tasks triggered by '{agent_id}'.")

                    # 6. Append All Manager Feedback
                    if manager_action_feedback:
                        feedback_appended = False
                        for feedback in manager_action_feedback:
                            feedback_content = f"[Manager Result for {feedback.get('action', 'N/A')} (Call ID: {feedback['call_id']})]: Success={feedback['success']}. Message: {feedback['message']}"
                            if feedback.get("data"):
                                 try: data_str = json.dumps(feedback['data'], indent=2); feedback_content += f"\nData:\n{data_str[:1500]}{'... (truncated)' if len(data_str) > 1500 else ''}"
                                 except TypeError: feedback_content += f"\nData: [Unserializable Data]"
                            feedback_message: MessageDict = { "role": "tool", "tool_call_id": feedback['call_id'], "content": feedback_content }
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != feedback_content:
                                 agent.message_history.append(feedback_message); logger.debug(f"Appended manager feedback for call {feedback['call_id']} to '{agent_id}' history."); feedback_appended = True
                        if feedback_appended: reactivate_agent_after_feedback = True

                else:
                    logger.warning(f"Unknown event type '{event_type}' received from agent '{agent_id}'.")

        except Exception as e:
            logger.error(f"Error occurred while handling generator for agent '{agent_id}': {e}", exc_info=True)
            agent.set_status(AGENT_STATUS_ERROR)
            current_cycle_error = True # Mark error occurred
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Unexpected error in generator handler - {e}]"})
        finally:
            if agent_generator:
                try: await agent_generator.aclose(); logger.debug(f"Closed generator for '{agent_id}'.")
                except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            # --- *** CORRECTED FINAL BLOCK LOGIC *** ---
            # Check for reactivation *first*
            if reactivate_agent_after_feedback and not current_cycle_error: # Only reactivate if no error occurred
                logger.info(f"Reactivating agent '{agent_id}' to process manager feedback.")
                agent.set_status(AGENT_STATUS_IDLE) # Set idle before restarting
                asyncio.create_task(self._handle_agent_generator(agent))
                # If reactivating, DO NOT proceed to the final status push/log below for this cycle
            else:
                # If not reactivating (or if an error occurred), push the final status
                final_status = agent.status
                # Ensure agent is left in a terminal state (idle or error) if not reactivating
                if final_status not in [AGENT_STATUS_IDLE, AGENT_STATUS_ERROR]:
                     logger.warning(f"Agent '{agent_id}' ended generator handling in non-terminal state '{final_status}'. Setting to IDLE (due to no reactivation or prior error).")
                     agent.set_status(AGENT_STATUS_IDLE) # Default to IDLE if stuck

                await self.push_agent_status_update(agent_id) # Push the determined final state
                logger.info(f"Manager finished handling generator cycle for Agent '{agent_id}'. Final status: {agent.status}")
            # --- *** END CORRECTION *** ---


    # --- Tool Execution & Team Management Delegation ---
    async def _handle_manage_team_action(self, action: Optional[str], params: Dict[str, Any]) -> Tuple[bool, str, Optional[Any]]:
        """Validates and delegates ManageTeamTool actions to appropriate methods."""
        if not action: return False, "No action specified.", None
        success, message, result_data = False, "Unknown action or error.", None
        try:
            logger.debug(f"Manager: Delegating ManageTeam action '{action}' with params: {params}")
            agent_id = params.get("agent_id"); team_id = params.get("team_id")
            provider = params.get("provider"); model = params.get("model")
            system_prompt = params.get("system_prompt"); persona = params.get("persona")
            temperature = params.get("temperature")
            known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
            extra_kwargs = {k: v for k, v in params.items() if k not in known_args}

            if action == "create_agent":
                success, message, created_agent_id = await self.create_agent_instance( agent_id, provider, model, system_prompt, persona, team_id, temperature, **extra_kwargs );
                if success and created_agent_id: message = f"Agent '{persona}' (ID: {created_agent_id}) creation request processed."; result_data = {"created_agent_id": created_agent_id}
            elif action == "delete_agent": success, message = await self.delete_agent_instance(agent_id) # Still handled by manager
            # --- Delegate team actions to StateManager ---
            elif action == "create_team": success, message = await self.state_manager.create_new_team(team_id)
            elif action == "delete_team": success, message = await self.state_manager.delete_existing_team(team_id)
            elif action == "add_agent_to_team":
                success, message = await self.state_manager.add_agent_to_team(agent_id, team_id)
                # Update agent's internal prompt after team state change
                if success: await self._update_agent_prompt_team_id(agent_id, team_id)
            elif action == "remove_agent_from_team":
                success, message = await self.state_manager.remove_agent_from_team(agent_id, team_id)
                 # Update agent's internal prompt after team state change
                if success: await self._update_agent_prompt_team_id(agent_id, None) # Pass None for team_id
            # --- List actions use StateManager getters ---
            elif action == "list_agents":
                 filter_team_id = params.get("team_id"); # Use state_manager method directly now
                 result_data = self.state_manager.get_agent_info_list(filter_team_id=filter_team_id) # Corrected: Call state_manager method
                 success = True; count = len(result_data)
                 message = f"Found {count} agent(s) in team '{filter_team_id}'." if filter_team_id else f"Found {count} agent(s) in total."
            elif action == "list_teams":
                 result_data = self.state_manager.get_team_info_dict(); success = True; message = f"Found {len(result_data)} team(s)."
            else: message = f"Unrecognized action: {action}"; logger.warning(message)

            logger.info(f"ManageTeamTool action '{action}' result: Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e: message = f"Error processing '{action}': {e}"; logger.error(message, exc_info=True); return False, message, None

    async def _update_agent_prompt_team_id(self, agent_id: str, new_team_id: Optional[str]):
        """Internal helper to update agent's prompt state after team change."""
        agent = self.agents.get(agent_id)
        if agent and not (agent_id in self.bootstrap_agents):
            try:
                new_team_str = f"Your Team ID: {new_team_id or 'N/A'}"
                agent.final_system_prompt = re.sub(r"Your Team ID:.*", new_team_str, agent.final_system_prompt)
                agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt
                if agent.message_history and agent.message_history[0]["role"] == "system":
                    agent.message_history[0]["content"] = agent.final_system_prompt
                logger.info(f"Updated team ID ({new_team_id}) in system prompt state for agent '{agent_id}'.")
            except Exception as e:
                 logger.error(f"Error updating system prompt state for agent '{agent_id}' after team change: {e}")


    async def _route_and_activate_agent_message(self, sender_id: str, target_id: str, message_content: str) -> Optional[asyncio.Task]:
        """Routes a message between agents, checking team state via StateManager."""
        sender_agent = self.agents.get(sender_id); target_agent = self.agents.get(target_id)
        if not sender_agent: logger.error(f"SendMsg route error: Sender '{sender_id}' not found."); return None
        if not target_agent: logger.error(f"SendMsg route error: Target '{target_id}' not found in self.agents dictionary."); return None

        # Use StateManager to check team membership
        sender_team = self.state_manager.get_agent_team(sender_id)
        target_team = self.state_manager.get_agent_team(target_id)

        if sender_id != BOOTSTRAP_AGENT_ID and (not sender_team or sender_team != target_team):
            logger.warning(f"SendMessage blocked: Sender '{sender_id}' (Team: {sender_team}) and Target '{target_id}' (Team: {target_team}) are not in the same team according to StateManager."); return None
        elif sender_id == BOOTSTRAP_AGENT_ID: logger.info(f"Admin AI sending message from '{sender_id}' to '{target_id}'.")
        else: logger.info(f"Routing message from '{sender_id}' to '{target_id}' in team '{target_team}'.")

        formatted_message: MessageDict = { "role": "user", "content": f"[From @{sender_id}]: {message_content}" }
        target_agent.message_history.append(formatted_message); logger.debug(f"Appended message from '{sender_id}' to history of '{target_id}'.")
        if target_agent.status == AGENT_STATUS_IDLE: logger.info(f"Target '{target_id}' is IDLE. Activating..."); return asyncio.create_task(self._handle_agent_generator(target_agent))
        else: logger.info(f"Target '{target_id}' not IDLE (Status: {target_agent.status}). Message queued in history."); await self.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued." }); return None


    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict]:
        """ Executes a tool via ToolExecutor. """
        if not self.tool_executor: logger.error("ToolExecutor unavailable."); return {"call_id": call_id, "content": "[ToolExec Error: ToolExecutor unavailable]", "_raw_result": None}
        tool_info = {"name": tool_name, "call_id": call_id}; agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)
        raw_result: Optional[Any] = None; result_content: str = "[Tool Execution Error: Unknown]"
        try:
            logger.debug(f"Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}'")
            raw_result = await self.tool_executor.execute_tool( agent.agent_id, agent.sandbox_path, tool_name, tool_args )
            logger.debug(f"Tool '{tool_name}' completed execution.")
            if isinstance(raw_result, dict): result_content = raw_result.get("message", str(raw_result))
            elif isinstance(raw_result, str): result_content = raw_result
            else: result_content = str(raw_result)
        except Exception as e: error_msg = f"Manager error during _execute_single_tool '{tool_name}': {e}"; logger.error(error_msg, exc_info=True); result_content = f"[ToolExec Error: {error_msg}]"; raw_result = None
        finally:
            if agent.status == AGENT_STATUS_EXECUTING_TOOL: agent.set_status(AGENT_STATUS_PROCESSING)
        return {"call_id": call_id, "content": result_content, "_raw_result": raw_result}


    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        """Returns a formatted error result dictionary for failed tool dispatch."""
        error_content = f"[ToolExec Error: Failed dispatch for '{tool_name or 'unknown'}'. Invalid format or arguments.]"
        final_call_id = call_id or f"invalid_call_{int(time.time())}"
        return {"call_id": final_call_id, "content": error_content, "_raw_result": {"status": "error", "message": error_content}}


    # --- Status and UI Update Methods ---
    async def push_agent_status_update(self, agent_id: str):
        """Retrieves full agent state (including team from StateManager) and sends to UI."""
        agent = self.agents.get(agent_id)
        if agent:
            state = agent.get_state()
            state["team"] = self.state_manager.get_agent_team(agent_id) # Use StateManager
            await self.send_to_ui({ "type": "agent_status_update", "agent_id": agent_id, "status": state })
        else: logger.warning(f"Cannot push status update for unknown agent: {agent_id}")

    async def send_to_ui(self, message_data: Dict[str, Any]):
        """Sends JSON-serialized data to all UI clients via broadcast."""
        if not self.send_to_ui_func: logger.warning("UI broadcast function not configured."); return
        try: await self.send_to_ui_func(json.dumps(message_data))
        except TypeError as e: logger.error(f"JSON serialization error sending to UI: {e}. Data: {message_data}", exc_info=True)
        except Exception as e: logger.error(f"Error sending message to UI: {e}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Synchronously gets status snapshot of all agents (including team from StateManager)."""
        statuses = {}
        for agent_id, agent in self.agents.items():
             state = agent.get_state()
             state["team"] = self.state_manager.get_agent_team(agent_id) # Use StateManager
             statuses[agent_id] = state
        return statuses


    # --- Session Persistence (Delegated to SessionManager) ---
    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Delegates saving the current session state to the SessionManager."""
        logger.info(f"Delegating save_session call for project '{project_name}'...")
        return await self.session_manager.save_session(project_name, session_name)

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Delegates loading a session state to the SessionManager."""
        logger.info(f"Delegating load_session call for project '{project_name}', session '{session_name}'...")
        return await self.session_manager.load_session(project_name, session_name)


    # --- Cleanup ---
    async def cleanup_providers(self):
        """ Calls cleanup methods (close_session) on all active LLM providers. """
        logger.info("Cleaning up LLM providers..."); active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}; logger.info(f"Found {len(active_providers)} unique provider instances to clean up.")
        tasks = [ asyncio.create_task(self._close_provider_safe(provider)) for provider in active_providers if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session) ]
        if tasks: await asyncio.gather(*tasks); logger.info("LLM Provider cleanup tasks completed.")
        else: logger.info("No provider cleanup tasks were necessary.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        """Safely attempts to call the asynchronous close_session method on a provider."""
        try: logger.info(f"Attempting to close session for provider: {provider!r}"); await provider.close_session(); logger.info(f"Successfully closed session for provider: {provider!r}")
        except Exception as e: logger.error(f"Error closing session for provider {provider!r}: {e}", exc_info=True)
