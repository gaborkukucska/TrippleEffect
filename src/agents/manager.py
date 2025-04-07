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
from src.agents.core import (
    Agent, AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE
)
from src.llm_providers.base import BaseLLMProvider, ToolResultDict, MessageDict

# Import settings instance, BASE_DIR, and default values
from src.config.settings import settings, BASE_DIR # Import settings

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
# Define the retry delays and calculate max retries
STREAM_RETRY_DELAYS = [5.0, 10.0, 10.0, 65.0] # New retry delays
MAX_STREAM_RETRIES = len(STREAM_RETRY_DELAYS) # Max retries based on delay list length

# Standard framework instructions
STANDARD_FRAMEWORK_INSTRUCTIONS = """

--- Standard Tool & Communication Protocol ---
Your Agent ID: `{agent_id}`
Your Assigned Team ID: `{team_id}`

You have access to the following tools. Use the specified XML format precisely when you need to use a tool. Only use one tool call per response message, placed at the very end.

{tool_descriptions_xml}

**Communication:**
- Use the `<send_message>` tool to communicate with other agents *within your team* or the Admin AI (`admin_ai`). Specify the `target_agent_id` and `message_content`.
- Respond to messages directed to you ([From @...]).
- **Report results:** When you complete a task assigned by the Admin AI or another agent, use the `<send_message>` tool to send your results (e.g., generated code, analysis summary, file content, or path to created file in your sandbox) back to the requesting agent (usually `admin_ai`).

**File System:**
- Use the `<file_system>` tool to read/write/list files *only within your own sandbox*. All paths are relative to your sandbox root.
--- End Standard Protocol ---
"""


class AgentManager:
    """
    Main coordinator for agents. Handles task distribution, agent lifecycle (creation/deletion),
    tool execution routing, and orchestrates state/session management via dedicated managers.
    Includes provider configuration checks and stream error retries with user override.
    Injects standard framework instructions into dynamic agents.
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
        try:
             settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)


    # --- ASYNCHRONOUS Bootstrap Initialization ---
    async def initialize_bootstrap_agents(self):
        """
        ASYNCHRONOUSLY loads bootstrap agents from settings.AGENT_CONFIGURATIONS.
        Injects allowed model list and standard instructions into Admin AI's prompt.
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
            # Ensure provider is configured before trying to create bootstrap agent
            provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
            if not settings.is_provider_configured(provider_name):
                logger.error(f"--- Cannot initialize bootstrap agent '{agent_id}': Provider '{provider_name}' is not configured in .env. Skipping. ---")
                continue # Skip this agent if its provider isn't set up

            # Modify Admin AI prompt injection
            if agent_id == BOOTSTRAP_AGENT_ID:
                original_prompt = agent_config_data.get("system_prompt", "")
                agent_config_data = agent_config_data.copy() # Avoid modifying original settings dict
                # Format standard instructions for Admin AI (no team initially)
                standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(
                    agent_id=BOOTSTRAP_AGENT_ID,
                    team_id="N/A", # Admin AI doesn't belong to a dynamic team
                    tool_descriptions_xml=self.tool_descriptions_xml
                )
                # Combine: Original Prompt + Allowed Models + Standard Instructions
                agent_config_data["system_prompt"] = original_prompt + "\n\n" + formatted_allowed_models + "\n\n" + standard_info
                logger.info(f"Injected allowed models list AND standard instructions into '{BOOTSTRAP_AGENT_ID}' system prompt.")

            # Append task to create agent (will handle prompt injection inside)
            tasks.append(self._create_agent_internal( agent_id_requested=agent_id, agent_config_data=agent_config_data, is_bootstrap=True ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_ids = []
        for i, result in enumerate(results):
            # Find corresponding agent_id based on valid tasks submitted
            task_index = -1
            processed_count = 0
            for idx, conf in enumerate(agent_configs_list):
                 p_name = conf.get("config", {}).get("provider", settings.DEFAULT_AGENT_PROVIDER)
                 if settings.is_provider_configured(p_name):
                     if processed_count == i: task_index = idx; break
                     processed_count += 1
            if task_index == -1: agent_id = f"unknown_{i}"; logger.error(f"Could not map result index {i} back to agent config.")
            else: agent_id = agent_configs_list[task_index].get("agent_id", f"unknown_{i}")

            if isinstance(result, Exception): logger.error(f"--- Failed bootstrap init '{agent_id}': {result} ---", exc_info=result)
            elif isinstance(result, tuple) and len(result) == 3:
                 success, message, created_agent_id = result
                 if success and created_agent_id: self.bootstrap_agents.append(created_agent_id); successful_ids.append(created_agent_id); logger.info(f"--- Bootstrap agent '{created_agent_id}' initialized. ---")
                 else: logger.error(f"--- Failed bootstrap init '{agent_id}': {message} ---")
            else: logger.error(f"--- Unexpected result type during bootstrap init for '{agent_id}': {result} ---")

        logger.info(f"Finished async bootstrap agent initialization. Active bootstrap agents: {successful_ids}")
        if BOOTSTRAP_AGENT_ID not in self.agents: logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize!")


    # --- Agent Creation Logic (Injects Standard Prompt) ---
    async def _create_agent_internal(
        self, agent_id_requested: Optional[str], agent_config_data: Dict[str, Any], is_bootstrap: bool = False, team_id: Optional[str] = None, loading_from_session: bool = False
        ) -> Tuple[bool, str, Optional[str]]:
        # (Remains unchanged from previous step)
        # ... (logic for validation, prompt injection, provider/agent instantiation, sandbox, team assignment) ...
        # 1. Determine Agent ID
        if agent_id_requested and agent_id_requested in self.agents: return False, f"Agent ID '{agent_id_requested}' already exists.", None
        agent_id = agent_id_requested or self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to generate Agent ID.", None
        logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")

        # 2. Extract Config & Validate Provider Configuration
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)

        if not settings.is_provider_configured(provider_name):
            msg = f"Validation Error: Provider '{provider_name}' is not configured."; logger.error(msg); return False, msg, None

        # 3. Validate Model against allowed list (only for dynamic agents)
        if not is_bootstrap and not loading_from_session:
            allowed_models = settings.ALLOWED_SUB_AGENT_MODELS.get(provider_name)
            if allowed_models is None: msg = f"Validation Error: Provider '{provider_name}' not in allowed_sub_agent_models."; logger.error(msg); return False, msg, None
            valid_allowed_models = [m for m in allowed_models if m and m.strip()]
            if not valid_allowed_models or model not in valid_allowed_models:
                allowed_list_str = ', '.join(valid_allowed_models) if valid_allowed_models else 'None'
                msg = f"Validation Error: Model '{model}' not allowed for '{provider_name}'. Allowed: [{allowed_list_str}]"; logger.error(msg); return False, msg, None
            logger.info(f"Dynamic agent creation validated: Provider '{provider_name}', Model '{model}' is allowed.")

        # 4. Extract other config
        role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        allowed_provider_keys = ['api_key', 'base_url', 'referer']; provider_specific_kwargs = { k: v for k, v in agent_config_data.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona'] + allowed_provider_keys}
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]

        # 5. Construct Final Prompt (Injecting Standard Instructions)
        final_system_prompt = role_specific_prompt # Start with the role-specific part

        # Inject standard instructions for dynamic agents *unless* loading from session
        # The bootstrap Admin AI gets its standard instructions during bootstrap init
        if not loading_from_session and not is_bootstrap:
             standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(
                 agent_id=agent_id,
                 team_id=team_id or "N/A",
                 tool_descriptions_xml=self.tool_descriptions_xml
             )
             # Prepend standard info to the role-specific prompt
             final_system_prompt = standard_info + "\n\n--- Your Specific Role & Task ---\n" + role_specific_prompt
             logger.debug(f"Injected standard framework instructions for dynamic agent '{agent_id}'.")
        elif loading_from_session:
             logger.debug(f"Skipping standard instruction injection for agent '{agent_id}' (loading from session).")
        elif is_bootstrap and agent_id == BOOTSTRAP_AGENT_ID:
             # This case is handled during bootstrap init, use the prompt from there
             final_system_prompt = agent_config_data.get("system_prompt", final_system_prompt)
             logger.debug(f"Using pre-injected prompt for bootstrap Admin AI.")
        else: # Other bootstrap agents (if any) don't get injection by default
             logger.debug(f"Using provided prompt directly for bootstrap agent '{agent_id}'.")


        # 6. Store the final combined config entry
        final_agent_config_entry = {
            "agent_id": agent_id,
            "config": {
                "provider": provider_name,
                "model": model,
                "system_prompt": final_system_prompt, # Use the final combined prompt
                "persona": persona,
                "temperature": temperature,
                **provider_specific_kwargs
            }
        }

        # 7. Instantiate Provider
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass: return False, f"Unknown provider '{provider_name}'.", None
        base_provider_config = settings.get_provider_config(provider_name); provider_config_overrides = {k: agent_config_data[k] for k in allowed_provider_keys if k in agent_config_data}
        final_provider_args = { **base_provider_config, **provider_specific_kwargs, **provider_config_overrides}; final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        try: llm_provider_instance = ProviderClass(**final_provider_args); logger.info(f"  Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")
        except Exception as e: logger.error(f"  Provider instantiation failed for '{agent_id}': {e}", exc_info=True); return False, f"Provider instantiation failed: {e}", None

        # 8. Instantiate Agent
        try:
            # Agent constructor no longer takes tool_descriptions_xml
            agent = Agent(
                agent_config=final_agent_config_entry,
                llm_provider=llm_provider_instance,
                manager=self
            )
            logger.info(f"  Instantiated Agent object for '{agent_id}'.")
        except Exception as e:
            logger.error(f"  Agent instantiation failed for '{agent_id}': {e}", exc_info=True)
            await self._close_provider_safe(llm_provider_instance)
            return False, f"Agent instantiation failed: {e}", None

        # 9. Ensure Sandbox
        try:
            sandbox_ok = await asyncio.to_thread(agent.ensure_sandbox_exists)
            if not sandbox_ok:
                 logger.warning(f"  Failed to ensure sandbox for '{agent_id}'.")
        except Exception as e:
            logger.error(f"Sandbox error for '{agent_id}': {e}", exc_info=True)
            logger.warning(f"Proceeding without guaranteed sandbox for '{agent_id}'.")

        # 10. Add agent instance to registry
        self.agents[agent_id] = agent; logger.debug(f"Agent '{agent_id}' added to self.agents dictionary.")

        # 11. Assign to Team State via StateManager
        team_add_msg_suffix = ""
        if team_id:
            # No need to update prompt here, already done in step 5
            # Delegate state update
            team_add_success, team_add_msg = await self.state_manager.add_agent_to_team(agent_id, team_id)
            if not team_add_success:
                team_add_msg_suffix = f" (Warning adding to team state: {team_add_msg})"
            else:
                logger.info(f"Agent '{agent_id}' state added to team '{team_id}' via StateManager.")

        message = f"Agent '{agent_id}' created successfully." + team_add_msg_suffix
        return True, message, agent_id


    async def create_agent_instance( # Public method unchanged
        self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs
        ) -> Tuple[bool, str, Optional[str]]:
        if not provider or not model or not system_prompt or not persona: return False, "Missing required params.", None
        agent_config_data = { "provider": provider, "model": model, "system_prompt": system_prompt, "persona": persona }
        if temperature is not None: agent_config_data["temperature"] = temperature
        known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
        extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args}; agent_config_data.update(extra_kwargs)
        success, message, created_agent_id = await self._create_agent_internal( agent_id_requested=agent_id_requested, agent_config_data=agent_config_data, is_bootstrap=False, team_id=team_id, loading_from_session=False )
        if success and created_agent_id:
            created_agent = self.agents.get(created_agent_id)
            config_sent_to_ui = created_agent.agent_config.get("config", {}) if created_agent else {}
            await self.send_to_ui({ "type": "agent_added", "agent_id": created_agent_id, "config": config_sent_to_ui, "team": self.state_manager.get_agent_team(created_agent_id) })
        return success, message, created_agent_id

    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]: # Unchanged
        if not agent_id: return False, "Agent ID cannot be empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."
        agent_instance = self.agents.pop(agent_id); self.state_manager.remove_agent_from_all_teams_state(agent_id); await self._close_provider_safe(agent_instance.llm_provider)
        message = f"Agent '{agent_id}' deleted successfully."; logger.info(message); await self.send_to_ui({"type": "agent_deleted", "agent_id": agent_id}); return True, message

    def _generate_unique_agent_id(self, prefix="agent") -> str: # Unchanged
        timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]
        while True:
            new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_");
            if new_id not in self.agents: return new_id
            time.sleep(0.001); timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]


    # --- Async Message/Task Handling ---
    async def handle_user_message(self, message: str, client_id: Optional[str] = None): # Unchanged
        logger.info(f"AgentManager received user message for Admin AI: '{message[:100]}...'")
        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent: logger.error(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') not found."); await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."}); return
        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Delegating user message to '{BOOTSTRAP_AGENT_ID}'.")
            admin_agent.message_history.append({"role": "user", "content": message})
            asyncio.create_task(self._handle_agent_generator(admin_agent))
        elif admin_agent.status == AGENT_STATUS_AWAITING_USER_OVERRIDE:
             logger.warning(f"Admin AI ({admin_agent.status}) awaiting user override. New message ignored.")
             await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI is waiting for user input to resolve previous error. Please respond to the prompt." })
        else:
            logger.info(f"Admin AI busy ({admin_agent.status}). User message queued implicitly in history.")
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Your message will be processed when idle." })


    # --- *** UPDATED _handle_agent_generator with history check *** ---
    async def _handle_agent_generator(self, agent: Agent, retry_count: int = 0):
        """Handles agent processing cycle, including specific retry delays and user override signal."""
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}' (Retry Attempt: {retry_count})...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback = [] # Feedback from ManageTeamTool actions
        reactivate_agent_after_feedback = False # Flag to re-run generator after adding feedback
        current_cycle_error = False # Track if an error occurred in this cycle
        is_stream_related_error = False # Flag specific error type
        last_error_content = "" # Store last error for override message

        # --- Track history length BEFORE processing ---
        history_len_before_processing = len(agent.message_history)
        logger.debug(f"Agent '{agent_id}' history length before cycle: {history_len_before_processing}")

        try:
            agent_generator = agent.process_message() # Get the generator

            while True:
                try: event = await agent_generator.asend(None) # Use asend(None)
                except StopAsyncIteration: logger.info(f"Agent '{agent_id}' generator finished normally."); break
                except Exception as gen_err: logger.error(f"Generator error for '{agent_id}': {gen_err}", exc_info=True); agent.set_status(AGENT_STATUS_ERROR); await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Generator crashed - {gen_err}]"}); current_cycle_error = True; break

                event_type = event.get("type")

                # Handle Standard Events
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content}); logger.debug(f"Appended final response for '{agent_id}'.")

                # Handle errors yielded by agent/provider
                elif event_type == "error":
                    last_error_content = event.get("content", "[Unknown Agent Error]")
                    logger.error(f"Agent '{agent_id}' reported error: {last_error_content}")
                    is_stream_error = any(indicator in last_error_content for indicator in ["Error processing stream chunk", "APIError during stream", "Failed to decode stream chunk", "Stream connection error", "Provider returned error"])
                    is_stream_related_error = is_stream_error
                    if is_stream_error:
                        logger.warning(f"Detected potentially temporary stream error for agent '{agent_id}'.")
                    else: # Permanent error
                        if "agent_id" not in event: event["agent_id"] = agent_id
                        await self.send_to_ui(event)
                        agent.set_status(AGENT_STATUS_ERROR)
                    current_cycle_error = True
                    break # Stop generator loop on any error

                # Handle Tool Requests (List)
                elif event_type == "tool_requests":
                    all_tool_calls: List[Dict] = event.get("calls", [])
                    if not all_tool_calls: continue

                    logger.info(f"Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response}); logger.debug(f"Appended assistant response (with tools) for '{agent_id}'.")

                    management_calls = []; other_calls = []; executed_results_map = {}; invalid_call_results = []
                    for call in all_tool_calls:
                         call_id, tool_name, tool_args = call.get("id"), call.get("name"), call.get("arguments", {})
                         if call_id and tool_name and isinstance(tool_args, dict):
                            if tool_name == ManageTeamTool.name: management_calls.append(call)
                            else: other_calls.append(call)
                         else:
                            logger.warning(f"Skipping invalid tool request format from '{agent_id}': {call}")
                            fail_result = await self._failed_tool_result(call_id, tool_name)
                            if fail_result: invalid_call_results.append(fail_result)
                    if invalid_call_results:
                        for fail_res in invalid_call_results: agent.message_history.append({"role": "tool", "tool_call_id": fail_res['call_id'], "content": str(fail_res['content']) })

                    manager_action_feedback = []
                    activation_tasks = []
                    calls_to_execute = management_calls + other_calls

                    if calls_to_execute:
                        logger.info(f"Executing {len(calls_to_execute)} tool call(s) sequentially for agent '{agent_id}'. Mgmt: {len(management_calls)}, Other: {len(other_calls)}")
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})

                        for call in calls_to_execute:
                            call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']
                            result = await self._execute_single_tool(agent, call_id, tool_name, tool_args)
                            if result:
                                executed_results_map[call_id] = result
                                raw_content_for_hist = result.get("content", "[Tool Error: No content]")
                                tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_for_hist) }
                                if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id:
                                     agent.message_history.append(tool_msg); logger.debug(f"Appended raw tool result for {call_id}.")

                                raw_tool_output = result.get("_raw_result")
                                if tool_name == ManageTeamTool.name:
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
                                    else:
                                         logger.warning(f"ManageTeamTool call {call_id} had unexpected structure: {result}")
                                         manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result structure."})
                                elif tool_name == SendMessageTool.name:
                                    target_id = call['arguments'].get("target_agent_id"); msg_content = call['arguments'].get("message_content")
                                    activation_task = None
                                    if target_id and msg_content is not None:
                                        if target_id in self.agents:
                                            activation_task = await self._route_and_activate_agent_message(agent_id, target_id, msg_content)
                                            if activation_task: activation_tasks.append(activation_task)
                                        else:
                                            logger.error(f"SendMessage failed: Target agent '{target_id}' not found.")
                                            manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": f"Failed to send: Target agent '{target_id}' not found."})
                                    else:
                                        logger.error(f"SendMessage args incomplete for call {call_id}. Args: {call['arguments']}")
                                        manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": "Missing target_agent_id or message_content."})
                            else:
                                 logger.error(f"Tool execution failed for call_id {call_id}, no result returned.")
                                 manager_action_feedback.append({"call_id": call_id, "action": tool_name, "success": False, "message": "Tool execution failed unexpectedly."})

                        logger.info(f"Finished executing {len(calls_to_execute)} tool calls for agent '{agent_id}'.")
                        if activation_tasks:
                            logger.info(f"Waiting for {len(activation_tasks)} activation tasks triggered by '{agent_id}'.")
                            await asyncio.gather(*activation_tasks)
                            logger.info(f"Completed activation tasks triggered by '{agent_id}'.")
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
            current_cycle_error = True
            last_error_content = f"[Manager Error: Unexpected error in generator handler - {e}]"
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
        finally:
            if agent_generator:
                try: await agent_generator.aclose(); logger.debug(f"Closed generator for '{agent_id}'.")
                except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            # Check for retry *first*
            if current_cycle_error and is_stream_related_error and retry_count < MAX_STREAM_RETRIES:
                retry_delay = STREAM_RETRY_DELAYS[retry_count]
                logger.warning(f"Stream error for '{agent_id}'. Retrying in {retry_delay:.1f}s (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES})...")
                await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Experiencing provider issues... Retrying automatically (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES}, delay {retry_delay}s)..."})
                await asyncio.sleep(retry_delay)
                agent.set_status(AGENT_STATUS_IDLE)
                asyncio.create_task(self._handle_agent_generator(agent, retry_count + 1))
                logger.info(f"Retry task scheduled for agent '{agent_id}'. This cycle ending.")
                return

            # Check if all retries failed
            elif current_cycle_error and is_stream_related_error and retry_count >= MAX_STREAM_RETRIES:
                logger.error(f"Agent '{agent_id}' failed after {MAX_STREAM_RETRIES} retries. Requesting user override.")
                agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
                await self.send_to_ui({
                    "type": "request_user_override",
                    "agent_id": agent_id,
                    "persona": agent.persona,
                    "current_provider": agent.provider_name,
                    "current_model": agent.model,
                    "last_error": last_error_content,
                    "message": f"Agent '{agent.persona}' ({agent_id}) failed after multiple retries. Please provide an alternative Provider/Model or try again later."
                })
                logger.info(f"User override requested for agent '{agent_id}'. Cycle ending, awaiting user input.")
                return

            # Check for reactivation due to feedback from *this* cycle
            elif reactivate_agent_after_feedback and not current_cycle_error:
                logger.info(f"Reactivating agent '{agent_id}' to process manager feedback from this cycle.")
                agent.set_status(AGENT_STATUS_IDLE)
                asyncio.create_task(self._handle_agent_generator(agent, 0))
                logger.info(f"Reactivation task scheduled for agent '{agent_id}'. This cycle ending.")
                return

            # --- *** NEW CHECK: Reactivate if new message arrived during processing *** ---
            elif not current_cycle_error and agent.status == AGENT_STATUS_PROCESSING:
                 # Check if history grew AND the last message is a 'user' message (from send_message)
                 history_len_after_processing = len(agent.message_history)
                 if history_len_after_processing > history_len_before_processing and agent.message_history[-1].get("role") == "user":
                      logger.info(f"Agent '{agent_id}' has new message(s) queued in history (length {history_len_before_processing} -> {history_len_after_processing}). Reactivating immediately.")
                      agent.set_status(AGENT_STATUS_IDLE) # Set idle before reactivating
                      asyncio.create_task(self._handle_agent_generator(agent, 0)) # Reset retry count
                      logger.info(f"Reactivation task scheduled for agent '{agent_id}' due to queued message. This cycle ending.")
                      return # Prevent fall-through to final status setting
                 else:
                     # If history didn't grow or last message isn't 'user', proceed to normal idle
                     logger.debug(f"Agent '{agent_id}' processing cycle finished, no new incoming messages detected.")
                     agent.set_status(AGENT_STATUS_IDLE)

            # Else finalize status if not retrying, awaiting override, or reactivating
            else:
                final_status = agent.status
                if final_status not in [AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE]:
                     logger.warning(f"Agent '{agent_id}' ended generator handling in unexpected non-terminal state '{final_status}'. Setting to IDLE.")
                     agent.set_status(AGENT_STATUS_IDLE)
            # --- *** END NEW CHECK *** ---

            # Final status update push (only if not returned above)
            await self.push_agent_status_update(agent_id)
            log_level = logging.ERROR if agent.status in [AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE] else logging.INFO
            logger.log(log_level, f"Manager finished handling generator cycle for Agent '{agent_id}'. Final status: {agent.status}")


    # --- Tool Execution & Team Management Delegation (Remains the Same) ---
    async def _handle_manage_team_action(self, action: Optional[str], params: Dict[str, Any]) -> Tuple[bool, str, Optional[Any]]:
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
                if success and created_agent_id:
                    result_data = { "created_agent_id": created_agent_id, "persona": persona, "provider": provider, "model": model, "team_id": team_id }
                    message = f"Agent '{persona}' created successfully with ID '{created_agent_id}'."
            elif action == "delete_agent": success, message = await self.delete_agent_instance(agent_id)
            elif action == "create_team":
                success, message = await self.state_manager.create_new_team(team_id)
                if success: result_data = {"created_team_id": team_id}
            elif action == "delete_team": success, message = await self.state_manager.delete_existing_team(team_id)
            elif action == "add_agent_to_team":
                success, message = await self.state_manager.add_agent_to_team(agent_id, team_id)
                if success: await self._update_agent_prompt_team_id(agent_id, team_id) # This updates internal state, not the prompt file
            elif action == "remove_agent_from_team":
                success, message = await self.state_manager.remove_agent_from_team(agent_id, team_id)
                if success: await self._update_agent_prompt_team_id(agent_id, None) # This updates internal state
            elif action == "list_agents":
                 filter_team_id = params.get("team_id");
                 result_data = self.get_agent_info_list_sync(filter_team_id=filter_team_id)
                 success = True; count = len(result_data)
                 message = f"Found {count} agent(s) in team '{filter_team_id}'." if filter_team_id else f"Found {count} agent(s) in total."
            elif action == "list_teams":
                 result_data = self.state_manager.get_team_info_dict(); success = True; message = f"Found {len(result_data)} team(s)."
            else: message = f"Unrecognized action: {action}"; logger.warning(message)

            logger.info(f"ManageTeamTool action '{action}' result: Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e: message = f"Error processing '{action}': {e}"; logger.error(message, exc_info=True); return False, message, None

    async def _update_agent_prompt_team_id(self, agent_id: str, new_team_id: Optional[str]):
        agent = self.agents.get(agent_id)
        if agent and not (agent_id in self.bootstrap_agents): # Only update for dynamic agents
            try:
                # Use regex to replace the team ID line safely
                team_line_regex = r"Your Assigned Team ID:.*"
                new_team_line = f"Your Assigned Team ID: {new_team_id or 'N/A'}"

                # Update the agent's live system prompt
                agent.final_system_prompt = re.sub(team_line_regex, new_team_line, agent.final_system_prompt)

                # Update the prompt within the stored agent_config dictionary
                if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config:
                     agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt

                # Update the prompt in the agent's active message history
                if agent.message_history and agent.message_history[0]["role"] == "system":
                    agent.message_history[0]["content"] = agent.final_system_prompt

                logger.info(f"Updated team ID ({new_team_id}) in live prompt state for dynamic agent '{agent_id}'.")
            except Exception as e:
                 logger.error(f"Error updating system prompt state for agent '{agent_id}' after team change: {e}")


    # --- Corrected Message Routing ---
    async def _route_and_activate_agent_message(self, sender_id: str, target_id: str, message_content: str) -> Optional[asyncio.Task]:
        """Routes a message between agents, allowing messages TO Admin AI."""
        sender_agent = self.agents.get(sender_id); target_agent = self.agents.get(target_id)
        if not sender_agent: logger.error(f"SendMsg route error: Sender '{sender_id}' not found."); return None
        if not target_agent: logger.error(f"SendMsg route error: Target '{target_id}' not found in self.agents dictionary."); return None

        sender_team = self.state_manager.get_agent_team(sender_id)
        target_team = self.state_manager.get_agent_team(target_id)

        # --- Communication Rules ---
        allowed = False
        # Case 1: Sender is Admin AI
        if sender_id == BOOTSTRAP_AGENT_ID:
            allowed = True
            logger.info(f"Admin AI sending message from '{sender_id}' to '{target_id}'.")
        # Case 2: Target is Admin AI
        elif target_id == BOOTSTRAP_AGENT_ID:
             allowed = True
             logger.info(f"Agent '{sender_id}' sending message to Admin AI ('{target_id}').")
        # Case 3: Sender and Target are in the same team (and neither is Admin AI implicitly)
        elif sender_team and sender_team == target_team:
            allowed = True
            logger.info(f"Routing message from '{sender_id}' to '{target_id}' in team '{target_team}'.")
        # --- End Communication Rules ---

        if not allowed:
            logger.warning(f"SendMessage blocked: Sender '{sender_id}' (Team: {sender_team}) cannot send to Target '{target_id}' (Team: {target_team}).")
            # TODO: Consider sending feedback to the sender agent?
            return None # Indicate routing failed

        # Proceed with message routing and activation
        formatted_message: MessageDict = { "role": "user", "content": f"[From @{sender_id}]: {message_content}" }
        target_agent.message_history.append(formatted_message); logger.debug(f"Appended message from '{sender_id}' to history of '{target_id}'.")

        if target_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Target '{target_id}' is IDLE. Activating...");
            return asyncio.create_task(self._handle_agent_generator(target_agent))
        # Handle case where target is awaiting override - queue message but don't activate
        elif target_agent.status == AGENT_STATUS_AWAITING_USER_OVERRIDE:
             logger.info(f"Target '{target_id}' is {AGENT_STATUS_AWAITING_USER_OVERRIDE}. Message queued, not activating.")
             await self.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued (awaiting user override)." })
             return None
        else: # Target is busy (processing, executing tool, etc.)
            logger.info(f"Target '{target_id}' not IDLE (Status: {target_agent.status}). Message queued in history.")
            await self.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued." })
            return None


    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict]:
        # (Keep the existing logic)
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
        # (Keep the existing logic)
        error_content = f"[ToolExec Error: Failed dispatch for '{tool_name or 'unknown'}'. Invalid format or arguments.]"
        final_call_id = call_id or f"invalid_call_{int(time.time())}"
        return {"call_id": final_call_id, "content": error_content, "_raw_result": {"status": "error", "message": error_content}}


    # --- Status and UI Update Methods (Remains the Same) ---
    async def push_agent_status_update(self, agent_id: str):
        # (Keep the existing logic)
        agent = self.agents.get(agent_id)
        if agent:
            state = agent.get_state()
            state["team"] = self.state_manager.get_agent_team(agent_id) # Use StateManager
            await self.send_to_ui({ "type": "agent_status_update", "agent_id": agent_id, "status": state })
        else: logger.warning(f"Cannot push status update for unknown agent: {agent_id}")

    async def send_to_ui(self, message_data: Dict[str, Any]):
        # (Keep the existing logic)
        if not self.send_to_ui_func: logger.warning("UI broadcast function not configured."); return
        try: await self.send_to_ui_func(json.dumps(message_data))
        except TypeError as e: logger.error(f"JSON serialization error sending to UI: {e}. Data: {message_data}", exc_info=True)
        except Exception as e: logger.error(f"Error sending message to UI: {e}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        # (Keep the existing logic)
        statuses = {}
        for agent_id, agent in self.agents.items():
             state = agent.get_state()
             state["team"] = self.state_manager.get_agent_team(agent_id) # Use StateManager
             statuses[agent_id] = state
        return statuses


    # --- Session Persistence (Delegated to SessionManager - Remains the Same) ---
    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        # (Keep the existing logic)
        logger.info(f"Delegating save_session call for project '{project_name}'...")
        return await self.session_manager.save_session(project_name, session_name)

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        # (Keep the existing logic)
        logger.info(f"Delegating load_session call for project '{project_name}', session '{session_name}'...")
        return await self.session_manager.load_session(project_name, session_name)


    # --- Cleanup (Remains the Same) ---
    async def cleanup_providers(self):
        # (Keep the existing logic)
        logger.info("Cleaning up LLM providers..."); active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}; logger.info(f"Found {len(active_providers)} unique provider instances to clean up.")
        tasks = [ asyncio.create_task(self._close_provider_safe(provider)) for provider in active_providers if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session) ]
        if tasks: await asyncio.gather(*tasks); logger.info("LLM Provider cleanup tasks completed.")
        else: logger.info("No provider cleanup tasks were necessary.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        # (Keep the existing logic)
        try: logger.info(f"Attempting to close session for provider: {provider!r}"); await provider.close_session(); logger.info(f"Successfully closed session for provider: {provider!r}")
        except Exception as e: logger.error(f"Error closing session for provider {provider!r}: {e}", exc_info=True)

    # --- get_agent_info_list_sync (Remains the Same) ----
    def get_agent_info_list_sync(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        # (Keep the existing logic)
        info_list = []
        for agent_id, agent in self.agents.items():
             current_team = self.state_manager.get_agent_team(agent_id)
             if filter_team_id is not None and current_team != filter_team_id: continue
             state = agent.get_state()
             info = { "agent_id": agent_id, "persona": state.get("persona"), "provider": state.get("provider"), "model": state.get("model"), "status": state.get("status"), "team": current_team }
             info_list.append(info)
        return info_list

    # --- Handle User Override Method (Remains the Same) ---
    async def handle_user_override(self, override_data: Dict[str, Any]):
        """Handles configuration override provided by the user for a stuck agent."""
        agent_id = override_data.get("agent_id")
        new_provider_name = override_data.get("new_provider")
        new_model = override_data.get("new_model")

        if not all([agent_id, new_provider_name, new_model]):
            logger.error(f"Received invalid user override data: {override_data}")
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": "Invalid override data received from UI."})
            return

        agent = self.agents.get(agent_id)
        if not agent:
            logger.error(f"Cannot apply user override: Agent '{agent_id}' not found.")
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"Cannot apply override: Agent {agent_id} not found."})
            return

        if agent.status != AGENT_STATUS_AWAITING_USER_OVERRIDE:
            logger.warning(f"Received override for agent '{agent_id}' but its status is '{agent.status}', not '{AGENT_STATUS_AWAITING_USER_OVERRIDE}'. Ignoring.")
            return

        logger.info(f"Applying user override for agent '{agent_id}'. New provider: '{new_provider_name}', New model: '{new_model}'")

        # --- Validate Provider/Model (Basic Checks) ---
        ProviderClass = PROVIDER_CLASS_MAP.get(new_provider_name)
        if not ProviderClass:
            logger.error(f"Override failed: Unknown provider '{new_provider_name}'.")
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"Override failed: Unknown provider '{new_provider_name}'."})
            agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE) # Keep awaiting
            return

        if not settings.is_provider_configured(new_provider_name):
            logger.error(f"Override failed: Provider '{new_provider_name}' is not configured in settings.")
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"Override failed: Provider '{new_provider_name}' is not configured in settings."})
            agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE) # Keep awaiting
            return

        # --- Update Agent Configuration and Re-instantiate Provider ---
        old_provider_instance = agent.llm_provider
        old_provider_name = agent.provider_name
        old_model = agent.model

        try:
            # Update agent attributes
            agent.provider_name = new_provider_name
            agent.model = new_model

            # Update stored config dictionary (important for saving session later)
            if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict):
                if "config" not in agent.agent_config: agent.agent_config["config"] = {}
                agent.agent_config["config"]["provider"] = new_provider_name
                agent.agent_config["config"]["model"] = new_model
            else:
                logger.warning(f"Agent '{agent_id}' missing agent_config attribute. Config persistence might be affected.")

            # Prepare args for new provider instance (use existing kwargs if possible)
            base_provider_config = settings.get_provider_config(new_provider_name)
            provider_kwargs = agent.agent_config.get("config", {}).copy()
            for key in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']:
                provider_kwargs.pop(key, None)
            final_provider_args = { **base_provider_config, **provider_kwargs }
            final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

            # Instantiate new provider
            new_provider_instance = ProviderClass(**final_provider_args)
            agent.llm_provider = new_provider_instance
            logger.info(f"Instantiated new provider {ProviderClass.__name__} for agent '{agent_id}' after override.")

            # Close the old provider session safely
            await self._close_provider_safe(old_provider_instance)

            # Reset status and restart the generator cycle
            logger.info(f"User override applied successfully for agent '{agent_id}'. Restarting processing cycle.")
            agent.set_status(AGENT_STATUS_IDLE)
            await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Configuration updated by user. Retrying with Provider: {new_provider_name}, Model: {new_model}."})
            # Restart generator with retry_count = 0 using the new config
            asyncio.create_task(self._handle_agent_generator(agent, 0))

        except Exception as e:
            logger.error(f"Error applying user override for agent '{agent_id}': {e}", exc_info=True)
            # Attempt to revert changes
            agent.provider_name = old_provider_name
            agent.model = old_model
            agent.llm_provider = old_provider_instance
            if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config:
                 agent.agent_config["config"]["provider"] = old_provider_name
                 agent.agent_config["config"]["model"] = old_model
            # Set status back to awaiting override
            agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"Failed to apply override: {e}. Please try again."})
