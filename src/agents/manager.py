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
# settings needs to be fully initialized BEFORE AgentManager is instantiated
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

# Standard framework instructions to inject into dynamic agent prompts
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
    Manages the lifecycle and task distribution for dynamically created agents within teams.
    The Admin AI (bootstrap agent) directs agent/team creation via ManageTeamTool.
    User messages are routed exclusively to the Admin AI.
    Handles session persistence for dynamic configurations and histories.
    Injects standard framework context into agent prompts.
    Validates dynamic agent creation requests against allowed models.
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
        Injects allowed model list into Admin AI's prompt.
        Should be called during application startup (e.g., lifespan event).
        """
        logger.info("Initializing bootstrap agents asynchronously...")
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list:
            logger.warning("No bootstrap agent configurations found in settings.")
            return

        main_sandbox_dir = BASE_DIR / "sandboxes"
        try:
            # Use asyncio.to_thread for sync mkdir
            await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
            logger.info(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
            logger.error(f"Error creating main sandbox directory: {e}")

        tasks = []
        # Get formatted allowed models list once
        formatted_allowed_models = settings.get_formatted_allowed_models()

        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id:
                logger.warning("Skipping bootstrap agent configuration due to missing 'agent_id'.")
                continue

            agent_config_data = agent_conf_entry.get("config", {})

            # --- Inject allowed models list into Admin AI's prompt ---
            if agent_id == BOOTSTRAP_AGENT_ID:
                original_prompt = agent_config_data.get("system_prompt", "")
                # Make a copy to modify
                agent_config_data = agent_config_data.copy()
                agent_config_data["system_prompt"] = original_prompt + "\n\n" + formatted_allowed_models
                logger.info(f"Injected allowed models list into '{BOOTSTRAP_AGENT_ID}' system prompt.")
            # --- End Injection ---

            tasks.append(self._create_agent_internal(
                 agent_id_requested=agent_id,
                 agent_config_data=agent_config_data, # Use potentially modified config
                 is_bootstrap=True
                 # No team_id needed for bootstrap here unless defined in config (unlikely now)
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_ids = []
        for i, result in enumerate(results):
            agent_id = agent_configs_list[i].get("agent_id", f"unknown_{i}")
            if isinstance(result, Exception): logger.error(f"--- Failed bootstrap init '{agent_id}': {result} ---", exc_info=result)
            elif isinstance(result, tuple) and len(result) == 3:
                 success, message, created_agent_id = result
                 if success and created_agent_id:
                      self.bootstrap_agents.append(created_agent_id)
                      successful_ids.append(created_agent_id)
                      logger.info(f"--- Bootstrap agent '{created_agent_id}' initialized. ---")
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
        team_id: Optional[str] = None,
        # Add param for loading from session to bypass some checks/prompt modifications
        loading_from_session: bool = False
        ) -> Tuple[bool, str, Optional[str]]:
        """
        ASYNC internal logic to instantiate agent, provider, sandbox, and handle team.
        Validates provider/model for dynamic agents.
        Injects standard instructions into dynamic agent prompts.
        """
        # 1. Determine Agent ID
        if agent_id_requested and agent_id_requested in self.agents: return False, f"Agent ID '{agent_id_requested}' already exists.", None
        agent_id = agent_id_requested or self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to generate Agent ID.", None
        logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session})")

        # 2. Extract Config & Validate Provider/Model for Dynamic Agents
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        allowed_provider_keys = ['api_key', 'base_url', 'referer']
        provider_specific_kwargs = { k: v for k, v in agent_config_data.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona'] + allowed_provider_keys}
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]

        # --- Provider/Model Validation (only for dynamic agents not loaded from session) ---
        if not is_bootstrap and not loading_from_session:
            allowed_models_for_provider = settings.ALLOWED_SUB_AGENT_MODELS.get(provider_name)
            if allowed_models_for_provider is None:
                # Provider itself is not listed in the allowed_sub_agent_models structure
                msg = f"Validation Error: Provider '{provider_name}' is not configured for dynamic agent creation in allowed_sub_agent_models."
                logger.error(msg)
                return False, msg, None
            if model not in allowed_models_for_provider:
                # Provider is listed, but the specific model is not allowed
                allowed_list_str = ', '.join(allowed_models_for_provider) if allowed_models_for_provider else 'None'
                msg = f"Validation Error: Model '{model}' is not allowed for provider '{provider_name}'. Allowed models: [{allowed_list_str}]"
                logger.error(msg)
                return False, msg, None
            logger.info(f"Dynamic agent creation validated: Provider '{provider_name}', Model '{model}' is allowed.")
        # --- End Validation ---

        # 3. Construct Final System Prompt
        # If loading from session, role_specific_prompt already IS the final combined prompt.
        # For bootstrap agents, use the prompt as-is (Admin AI already got injection).
        # For new dynamic agents, combine role prompt + standard instructions.
        final_system_prompt = role_specific_prompt
        if not is_bootstrap and not loading_from_session:
            standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(
                agent_id=agent_id,
                team_id=team_id or "N/A"
            )
            final_system_prompt = role_specific_prompt + "\n" + standard_info
            logger.debug(f"Constructed final prompt for dynamic agent '{agent_id}'.")

        # --- Store the final prompt back into a structure for Agent instantiation ---
        # Create the final agent config entry that will be used by the Agent class
        # and potentially saved during session persistence.
        final_agent_config_entry = {
            "agent_id": agent_id,
            "config": {
                "provider": provider_name,
                "model": model,
                "system_prompt": final_system_prompt, # Use the final combined prompt
                "persona": persona,
                "temperature": temperature,
                **provider_specific_kwargs # Include any other args
            }
        }

        # 4. Instantiate Provider
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass: return False, f"Unknown provider '{provider_name}'.", None
        base_provider_config = settings.get_provider_config(provider_name)
        # Allow potential overrides from the agent config itself (e.g., specific base_url)
        provider_config_overrides = {k: agent_config_data[k] for k in allowed_provider_keys if k in agent_config_data}
        final_provider_args = { **base_provider_config, **provider_specific_kwargs, **provider_config_overrides}
        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None} # Clean None values
        try:
             llm_provider_instance = ProviderClass(**final_provider_args)
             logger.info(f"  Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")
        except Exception as e: logger.error(f"  Provider instantiation failed for '{agent_id}': {e}", exc_info=True); return False, f"Provider instantiation failed: {e}", None

        # 5. Instantiate Agent using the FINAL config entry
        try:
            agent = Agent(
                agent_config=final_agent_config_entry, # Pass the structure with the final prompt
                llm_provider=llm_provider_instance,
                manager=self,
                tool_descriptions_xml=self.tool_descriptions_xml
                )
            # Store the final config ON the agent object for easy access (e.g., saving state)
            agent.agent_config = final_agent_config_entry # Ensures combined prompt is stored
            logger.info(f"  Instantiated Agent object for '{agent_id}'.")
        except Exception as e: logger.error(f"  Agent instantiation failed for '{agent_id}': {e}", exc_info=True); return False, f"Agent instantiation failed: {e}", None

        # 6. Ensure Sandbox
        try:
            sandbox_ok = await asyncio.to_thread(agent.ensure_sandbox_exists)
            if not sandbox_ok: logger.warning(f"  Failed to ensure sandbox for '{agent_id}'.")
        except Exception as e: logger.error(f"Sandbox error for '{agent_id}': {e}", exc_info=True); logger.warning(f"Proceeding without guaranteed sandbox for '{agent_id}'.")

        # 7. Add to Manager State
        self.agents[agent_id] = agent
        logger.debug(f"Agent '{agent_id}' added to self.agents.")

        # 8. Add to Team if specified
        team_add_msg_suffix = ""
        if team_id:
             # Update team_id in the agent's final prompt *if* we just added them dynamically
             # Avoid doing this if loading from session or for bootstrap agents
             if not loading_from_session and not is_bootstrap:
                 try:
                     # Replace the placeholder team ID set during initial prompt construction
                     new_team_str = f"Your Team ID: {team_id}"
                     # Use regex for safer replacement (case-insensitive matching might be useful too)
                     agent.final_system_prompt = re.sub(r"Your Team ID:.*", new_team_str, agent.final_system_prompt)
                     agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt # Update stored config too
                     # Update message history too (index 0 is system prompt)
                     if agent.message_history and agent.message_history[0]["role"] == "system":
                         agent.message_history[0]["content"] = agent.final_system_prompt
                 except Exception as e:
                     logger.error(f"Error updating team ID in dynamic agent prompt for {agent_id}: {e}")

             team_add_success, team_add_msg = await self.add_agent_to_team(agent_id, team_id) # add_agent_to_team handles state update
             if not team_add_success: team_add_msg_suffix = f" (Warning: {team_add_msg})"
             else: logger.info(f"Agent '{agent_id}' added to team '{team_id}'.")
        message = f"Agent '{agent_id}' created successfully." + team_add_msg_suffix
        return True, message, agent_id


    # --- ASYNCHRONOUS Public Method for Dynamic Creation ---
    async def create_agent_instance(
        self,
        agent_id_requested: Optional[str],
        provider: str,
        model: str,
        system_prompt: str, # This is the ROLE-SPECIFIC prompt from Admin AI
        persona: str,
        team_id: Optional[str] = None,
        temperature: Optional[float] = None,
        **kwargs
        ) -> Tuple[bool, str, Optional[str]]:
        """Public ASYNC method called by Admin AI (via tool) to create a dynamic agent."""
        if not provider or not model or not system_prompt or not persona:
            return False, "Missing required params (provider, model, system_prompt, persona).", None

        # Prepare config data, AgentManager._create_agent_internal will combine prompts
        agent_config_data = {
            "provider": provider,
            "model": model,
            "system_prompt": system_prompt, # Pass role-specific prompt
            "persona": persona
        }
        if temperature is not None: agent_config_data["temperature"] = temperature
        # Filter known args before passing extras
        known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
        extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args}
        agent_config_data.update(extra_kwargs)

        # Call internal creation logic (which handles validation and prompt injection)
        success, message, created_agent_id = await self._create_agent_internal(
            agent_id_requested=agent_id_requested,
            agent_config_data=agent_config_data,
            is_bootstrap=False, # Definitely dynamic
            team_id=team_id,
            loading_from_session=False # Not loading from session
            )

        # If creation succeeded, notify UI
        if success and created_agent_id:
            created_agent = self.agents.get(created_agent_id)
            # Send the config used for *creation* (which includes final combined prompt)
            config_sent_to_ui = {}
            if created_agent and hasattr(created_agent, 'agent_config'):
                 config_sent_to_ui = created_agent.agent_config.get("config", {})

            await self.send_to_ui({
                "type": "agent_added",
                "agent_id": created_agent_id,
                "config": config_sent_to_ui, # Send final config used
                "team": self.agent_to_team.get(created_agent_id)
                })
        # If success is False, the message already contains the reason (e.g., validation error)
        return success, message, created_agent_id


    # --- Async Handler Methods ---
    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """Routes incoming user messages exclusively to the Admin AI agent."""
        logger.info(f"AgentManager received user message for Admin AI: '{message[:100]}...'")
        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent:
            logger.error(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') not found.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."})
            return

        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Delegating user message to '{BOOTSTRAP_AGENT_ID}'.")
            # Append user message to Admin AI's history
            admin_agent.message_history.append({"role": "user", "content": message})
            asyncio.create_task(self._handle_agent_generator(admin_agent))
        else:
            logger.info(f"Admin AI busy ({admin_agent.status}). User message queued implicitly in history.")
            # Send status update to UI, maybe indicate message is implicitly queued
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({
                "type": "status", # Use status type
                "agent_id": admin_agent.agent_id, # Associate with Admin AI
                "content": f"Admin AI busy ({admin_agent.status}). Your message will be processed when idle."
            })


    async def _handle_agent_generator(self, agent: Agent):
        """Handles the async generator interaction for a single agent's processing cycle."""
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback = [] # Feedback from ManageTeamTool actions
        reactivate_agent_after_feedback = False # Flag to re-run generator after adding feedback

        try:
            agent_generator = agent.process_message() # Get the generator

            while True:
                try:
                    event = await agent_generator.asend(None) # Use asend(None)
                except StopAsyncIteration:
                    logger.info(f"Agent '{agent_id}' generator finished normally.")
                    break # Normal finish
                except Exception as gen_err:
                    logger.error(f"Generator error for '{agent_id}': {gen_err}", exc_info=True); agent.set_status(AGENT_STATUS_ERROR); await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Generator crashed - {gen_err}]"}); break

                event_type = event.get("type")

                # --- Handle Standard Events ---
                if event_type in ["response_chunk", "status", "error", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content}); logger.debug(f"Appended final response for '{agent_id}'.")
                    if event_type == "error": logger.error(f"Agent '{agent_id}' reported error. Stopping generator handling."); break

                # --- Handle Tool Requests (with sequential processing) ---
                elif event_type == "tool_requests":
                    all_tool_calls = event.get("calls", [])
                    agent_last_response = event.get("raw_assistant_response")

                    # Append assistant's response containing tool call(s) to history
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response})
                         logger.debug(f"Appended assistant response (with tools) for '{agent_id}'.")

                    # --- Sequential Tool Execution ---
                    management_calls = []
                    other_calls = []
                    executed_results_map = {} # Map call_id -> result_dict
                    invalid_call_results = []

                    # 1. Validate and Categorize Calls
                    for call in all_tool_calls:
                         call_id, tool_name, tool_args = call.get("id"), call.get("name"), call.get("arguments", {})
                         if call_id and tool_name and isinstance(tool_args, dict):
                            if tool_name == ManageTeamTool.name:
                                management_calls.append(call)
                            else:
                                other_calls.append(call)
                         else:
                            logger.warning(f"Skipping invalid tool request from '{agent_id}': {call}")
                            fail_result = await self._failed_tool_result(call_id, tool_name)
                            if fail_result: invalid_call_results.append(fail_result)

                    # Append failure results for invalid calls immediately
                    if invalid_call_results:
                        for fail_res in invalid_call_results:
                             tool_msg: MessageDict = {"role": "tool", "tool_call_id": fail_res['call_id'], "content": str(fail_res['content']) }
                             agent.message_history.append(tool_msg)

                    # 2. Execute Management Tool Calls First (Sequentially)
                    if management_calls:
                        logger.info(f"Executing {len(management_calls)} management tool call(s) sequentially for agent '{agent_id}'.")
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(management_calls)} management tool(s)..."})
                        for call in management_calls: # Execute one by one
                            result = await self._execute_single_tool(agent, call['id'], call['name'], call['arguments'])
                            if result: executed_results_map[call['id']] = result
                        logger.info(f"Finished executing management tool calls for agent '{agent_id}'.")

                    # 3. Process Management Results & Generate Feedback
                    manager_action_feedback = [] # Reset feedback
                    activation_tasks = [] # Reset activation tasks for this turn
                    if management_calls:
                        for call in management_calls:
                            call_id = call['id']
                            result = executed_results_map.get(call_id)
                            if not result: continue # Skip if execution failed somehow

                            # Append raw result to history
                            raw_content_for_hist = result.get("content", "[Tool Error: No content]")
                            tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_for_hist) }
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id:
                                agent.message_history.append(tool_msg); logger.debug(f"Appended raw mgmt tool result for {call_id}.")

                            # Generate manager feedback specifically for ManageTeamTool
                            raw_tool_output = result.get("_raw_result")
                            if call['name'] == ManageTeamTool.name:
                                if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                    action = raw_tool_output.get("action")
                                    params = raw_tool_output.get("params", {})
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

                    # 4. Execute Other Tool Calls (Sequentially)
                    if other_calls:
                        logger.info(f"Executing {len(other_calls)} other tool call(s) sequentially for agent '{agent_id}'.")
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(other_calls)} other tool(s)..."})
                        for call in other_calls: # Execute one by one
                             result = await self._execute_single_tool(agent, call['id'], call['name'], call['arguments'])
                             if result: executed_results_map[call['id']] = result
                        logger.info(f"Finished executing other tool calls for agent '{agent_id}'.")

                        # Append raw results & process SendMessage
                        for call in other_calls:
                            result = executed_results_map.get(call['id'])
                            if not result: continue
                            raw_content_for_hist = result.get("content", "[Tool Error: No content]")
                            tool_msg: MessageDict = {"role": "tool", "tool_call_id": call['id'], "content": str(raw_content_for_hist) }
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call['id']:
                                agent.message_history.append(tool_msg); logger.debug(f"Appended raw other tool result for {call['id']}.")

                            # Process SendMessage specifically
                            if call['name'] == SendMessageTool.name:
                                target_id = call['arguments'].get("target_agent_id")
                                msg_content = call['arguments'].get("message_content")
                                if target_id and msg_content is not None:
                                    # Check target existence *after* potential mgmt actions
                                    if target_id in self.agents:
                                         activation_task = await self._route_and_activate_agent_message(agent_id, target_id, msg_content)
                                         if activation_task: activation_tasks.append(activation_task)
                                    else:
                                         logger.error(f"SendMessage failed: Target agent '{target_id}' not found after management actions.")
                                         manager_action_feedback.append({"call_id": call['id'], "action": "send_message", "success": False, "message": f"Failed to send: Target agent '{target_id}' not found."})
                                else:
                                     logger.error(f"SendMessage args incomplete for call {call['id']}. Args: {call['arguments']}")
                                     manager_action_feedback.append({"call_id": call['id'], "action": "send_message", "success": False, "message": "Missing target_agent_id or message_content."})

                    # 5. Wait for any agent activations triggered by SendMessage
                    if activation_tasks:
                         await asyncio.gather(*activation_tasks)
                         logger.info(f"Completed activation tasks triggered by '{agent_id}'.")

                    # 6. Append All Manager Feedback to Caller's History
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
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Unexpected error in generator handler - {e}]"})
        finally:
            if agent_generator:
                try: await agent_generator.aclose(); logger.debug(f"Closed generator for '{agent_id}'.")
                except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)
            if reactivate_agent_after_feedback and agent.status != AGENT_STATUS_ERROR:
                logger.info(f"Reactivating agent '{agent_id}' to process manager feedback."); agent.set_status(AGENT_STATUS_IDLE); asyncio.create_task(self._handle_agent_generator(agent))
            else: await self.push_agent_status_update(agent_id); logger.info(f"Manager finished handling generator cycle for Agent '{agent_id}'. Final status: {agent.status}")


    async def _handle_manage_team_action(self, action: Optional[str], params: Dict[str, Any]) -> Tuple[bool, str, Optional[Any]]:
        """Dispatches ManageTeamTool actions, including filtering for list_agents."""
        if not action: return False, "No action specified.", None
        success, message, result_data = False, "Unknown action or error.", None
        try:
            logger.debug(f"Handling ManageTeam action '{action}' with params: {params}")
            agent_id = params.get("agent_id"); team_id = params.get("team_id") # team_id used by multiple actions
            provider = params.get("provider"); model = params.get("model")
            system_prompt = params.get("system_prompt"); persona = params.get("persona")
            temperature = params.get("temperature")
            known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
            extra_kwargs = {k: v for k, v in params.items() if k not in known_args}

            if action == "create_agent":
                success, message, created_agent_id = await self.create_agent_instance( agent_id, provider, model, system_prompt, persona, team_id, temperature, **extra_kwargs );
                if success and created_agent_id:
                     # Adjust message slightly for clarity
                     message = f"Agent '{persona}' (ID: {created_agent_id}) creation request processed. Status: {message}"
                     result_data = {"created_agent_id": created_agent_id}
                # Failure message already contains validation details
            elif action == "delete_agent": success, message = await self.delete_agent_instance(agent_id)
            elif action == "create_team": success, message = await self.create_new_team(team_id)
            elif action == "delete_team": success, message = await self.delete_existing_team(team_id)
            elif action == "add_agent_to_team": success, message = await self.add_agent_to_team(agent_id, team_id)
            elif action == "remove_agent_from_team": success, message = await self.remove_agent_from_team(agent_id, team_id)
            elif action == "list_agents":
                 # --- Handle optional team_id filter ---
                 filter_team_id = params.get("team_id") # Get team_id from params if provided
                 result_data = await self.get_agent_info_list(filter_team_id=filter_team_id)
                 success = True
                 count = len(result_data)
                 if filter_team_id:
                     message = f"Found {count} agent(s) in team '{filter_team_id}'."
                 else:
                     message = f"Found {count} agent(s) in total."
                 # --- End filter handling ---
            elif action == "list_teams":
                 result_data = await self.get_team_info_dict()
                 success = True
                 message = f"Found {len(result_data)} team(s)."
            else: message = f"Unrecognized action: {action}"; logger.warning(message)

            logger.info(f"ManageTeamTool action '{action}' result: Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e: message = f"Error processing '{action}': {e}"; logger.error(message, exc_info=True); return False, message, None


    def _generate_unique_agent_id(self, prefix="agent") -> str:
        """Generates a unique agent ID using timestamp and random hex."""
        timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]
        while True:
            new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_");
            if new_id not in self.agents: return new_id
            time.sleep(0.001); timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]


    # --- Dynamic Team/Agent Async Methods ---
    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        """Deletes a dynamic agent instance."""
        if not agent_id: return False, "Agent ID cannot be empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."

        agent_instance = self.agents.pop(agent_id)
        team_id = self.agent_to_team.pop(agent_id, None)
        if team_id and team_id in self.teams and agent_id in self.teams[team_id]:
            self.teams[team_id].remove(agent_id)
            logger.info(f"Removed '{agent_id}' from team '{team_id}'.")

        await self._close_provider_safe(agent_instance.llm_provider)
        # Optional: Sandbox cleanup
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
        await self.send_to_ui({"type": "team_created", "team_id": team_id, "members": []})
        return True, message

    async def delete_existing_team(self, team_id: str) -> Tuple[bool, str]:
        """Deletes an existing empty team."""
        if not team_id: return False, "Team ID cannot be empty."
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        agents_in_team_map = [aid for aid, tid in self.agent_to_team.items() if tid == team_id]
        if agents_in_team_map or self.teams.get(team_id):
             member_list = agents_in_team_map or self.teams.get(team_id, [])
             logger.warning(f"Delete team '{team_id}' failed. Team still contains agents: {member_list}.")
             return False, f"Team '{team_id}' is not empty. Remove agents first. Members: {member_list}"
        del self.teams[team_id]
        message = f"Team '{team_id}' deleted successfully."
        logger.info(message)
        await self.send_to_ui({"type": "team_deleted", "team_id": team_id})
        return True, message

    async def add_agent_to_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        """Adds an agent to a team, updating state and agent prompt."""
        logger.debug(f"Attempting to add agent '{agent_id}' to team '{team_id}'.") # Entry log
        if not agent_id or not team_id:
             logger.error("add_agent_to_team failed: Agent ID or Team ID empty.")
             return False, "Agent ID and Team ID cannot be empty."
        if agent_id not in self.agents:
             logger.error(f"add_agent_to_team failed: Agent '{agent_id}' not found in self.agents.")
             return False, f"Agent '{agent_id}' not found."

        # Create team if it doesn't exist
        if team_id not in self.teams:
             logger.info(f"Team '{team_id}' does not exist. Attempting to auto-create.")
             success, msg = await self.create_new_team(team_id)
             if not success:
                 logger.error(f"add_agent_to_team failed: Could not auto-create team '{team_id}': {msg}")
                 return False, f"Failed to auto-create team '{team_id}': {msg}"

        old_team = self.agent_to_team.get(agent_id)
        if old_team == team_id:
             logger.info(f"Agent '{agent_id}' is already in team '{team_id}'. No action needed.")
             return True, f"Agent '{agent_id}' is already in team '{team_id}'."

        # --- State Update Logging ---
        logger.debug(f"Before update: agent_to_team map contains '{agent_id}': {agent_id in self.agent_to_team}")
        logger.debug(f"Before update: team '{team_id}' exists: {team_id in self.teams}, members: {self.teams.get(team_id)}")
        if old_team:
            logger.debug(f"Before update: old team '{old_team}' exists: {old_team in self.teams}, members: {self.teams.get(old_team)}")

        # Remove from old team list if necessary
        if old_team and old_team in self.teams and agent_id in self.teams[old_team]:
            try:
                self.teams[old_team].remove(agent_id)
                logger.info(f"Removed '{agent_id}' from old team list '{old_team}'.")
            except Exception as e:
                 logger.error(f"Error removing '{agent_id}' from old team '{old_team}': {e}")
                 # Continue, but log the error

        # Add to new team list
        try:
            if agent_id not in self.teams[team_id]:
                self.teams[team_id].append(agent_id)
                logger.info(f"Appended '{agent_id}' to new team list '{team_id}'.")
        except Exception as e:
             logger.error(f"Error appending '{agent_id}' to new team list '{team_id}': {e}")
             return False, f"Failed to add agent to team list: {e}" # Fail if append fails

        # Update agent_to_team mapping
        self.agent_to_team[agent_id] = team_id
        logger.info(f"Updated agent_to_team map: '{agent_id}' -> '{team_id}'.")

        # --- Post State Update Logging ---
        logger.debug(f"After update: agent_to_team map value for '{agent_id}': {self.agent_to_team.get(agent_id)}")
        logger.debug(f"After update: team '{team_id}' members: {self.teams.get(team_id)}")
        if old_team:
            logger.debug(f"After update: old team '{old_team}' members: {self.teams.get(old_team)}")
        # --- End State Update Logging ---


        # Update Agent's Prompt/State (if agent exists and is dynamic)
        agent = self.agents.get(agent_id)
        if agent and not (agent_id in self.bootstrap_agents):
            try:
                 new_team_str = f"Your Team ID: {team_id}"
                 agent.final_system_prompt = re.sub(r"Your Team ID:.*", new_team_str, agent.final_system_prompt)
                 agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt
                 if agent.message_history and agent.message_history[0]["role"] == "system":
                     agent.message_history[0]["content"] = agent.final_system_prompt
                 logger.info(f"Updated team ID in system prompt for agent '{agent_id}'.")
            except Exception as e:
                 logger.error(f"Error updating system prompt for agent '{agent_id}' after team change: {e}")

        message = f"Agent '{agent_id}' added to team '{team_id}'."
        # Don't log info here, wait for return
        await self.send_to_ui({ "type": "agent_moved_team", "agent_id": agent_id, "new_team_id": team_id, "old_team_id": old_team })
        await self.push_agent_status_update(agent_id)
        logger.info(f"add_agent_to_team completed successfully for '{agent_id}' -> '{team_id}'.") # Final success log
        return True, message

    async def remove_agent_from_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        """Removes an agent from a team, updating state and agent prompt."""
        if not agent_id or not team_id: return False, "Agent ID and Team ID cannot be empty."
        if team_id not in self.teams: return False, f"Team '{team_id}' not found."
        if self.agent_to_team.get(agent_id) != team_id:
             return False, f"Agent '{agent_id}' is not recorded as being in team '{team_id}'."

        # Remove from team list
        if agent_id in self.teams[team_id]:
            self.teams[team_id].remove(agent_id)

        # Remove from agent_to_team mapping
        old_team_id = self.agent_to_team.pop(agent_id, None) # Use pop with default

        # Update Agent's Prompt/State (if agent exists and is dynamic)
        agent = self.agents.get(agent_id)
        if agent and not (agent_id in self.bootstrap_agents):
            try:
                 new_team_str = f"Your Team ID: N/A"
                 agent.final_system_prompt = re.sub(r"Your Team ID:.*", new_team_str, agent.final_system_prompt)
                 agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt
                 if agent.message_history and agent.message_history[0]["role"] == "system":
                     agent.message_history[0]["content"] = agent.final_system_prompt
                 logger.info(f"Updated team ID to N/A in system prompt for agent '{agent_id}'.")
            except Exception as e:
                 logger.error(f"Error updating system prompt for agent '{agent_id}' after team removal: {e}")

        message = f"Agent '{agent_id}' removed from team '{team_id}'."
        logger.info(message)
        await self.send_to_ui({ "type": "agent_moved_team", "agent_id": agent_id, "new_team_id": None, "old_team_id": old_team_id }) # Use popped value
        await self.push_agent_status_update(agent_id)
        return True, message

    async def get_agent_info_list(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns list of dicts with info about each agent, optionally filtered by team."""
        info_list = []
        for agent_id, agent in self.agents.items():
             current_team = self.agent_to_team.get(agent_id)
             # Apply filter if provided
             if filter_team_id is not None and current_team != filter_team_id:
                 continue # Skip agent if not in the filtered team

             state = agent.get_state()
             info = {
                 "agent_id": agent_id,
                 "persona": state.get("persona"),
                 "provider": state.get("provider"),
                 "model": state.get("model"),
                 "status": state.get("status"),
                 "team": current_team # Use the fetched current team
             }
             info_list.append(info)
        return info_list

    async def get_team_info_dict(self) -> Dict[str, List[str]]:
        """Returns a copy of the current team structure."""
        # Could enhance to ensure consistency with agent_to_team map
        return self.teams.copy()


    # --- Tool Execution and Routing ---
    async def _route_and_activate_agent_message(self, sender_id: str, target_id: str, message_content: str) -> Optional[asyncio.Task]:
        """Routes a message between agents, appends to history, activates target if idle."""
        sender_agent = self.agents.get(sender_id)
        target_agent = self.agents.get(target_id) # Attempt to get target agent

        if not sender_agent: logger.error(f"SendMsg route error: Sender '{sender_id}' not found."); return None
        # --- *** ADDED CRITICAL CHECK *** ---
        if not target_agent:
            logger.error(f"SendMsg route error: Target '{target_id}' not found in self.agents dictionary.")
            # Consider sending feedback to sender? (Maybe via manager_action_feedback in calling function)
            return None
        # --- End Critical Check ---

        sender_team = self.agent_to_team.get(sender_id)
        target_team = self.agent_to_team.get(target_id)

        # Allow Admin AI to send to anyone, otherwise enforce same team
        if sender_id != BOOTSTRAP_AGENT_ID and (not sender_team or sender_team != target_team):
            logger.warning(f"SendMessage blocked: Sender '{sender_id}' (Team: {sender_team}) and Target '{target_id}' (Team: {target_team}) are not in the same team.")
            return None
        elif sender_id == BOOTSTRAP_AGENT_ID:
            logger.info(f"Admin AI sending message from '{sender_id}' to '{target_id}'.")
        else:
            logger.info(f"Routing message from '{sender_id}' to '{target_id}' in team '{target_team}'.")

        # Format the message with sender info
        formatted_message: MessageDict = {
            "role": "user", # Treat inter-agent messages as 'user' role for the recipient
            "content": f"[From @{sender_id}]: {message_content}"
        }

        # Append to target's history
        target_agent.message_history.append(formatted_message)
        logger.debug(f"Appended message from '{sender_id}' to history of '{target_id}'.")

        # Activate target agent if idle
        if target_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Target '{target_id}' is IDLE. Activating...")
            return asyncio.create_task(self._handle_agent_generator(target_agent))
        else:
            logger.info(f"Target '{target_id}' not IDLE (Status: {target_agent.status}). Message queued in history.")
            await self.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued." })
            return None


    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict]:
        """ Executes a tool via ToolExecutor. Returns a dictionary for manager processing. """
        if not self.tool_executor:
            logger.error("ToolExecutor unavailable.")
            return {"call_id": call_id, "content": "[ToolExec Error: ToolExecutor unavailable]", "_raw_result": None}

        tool_info = {"name": tool_name, "call_id": call_id}
        agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)
        raw_result: Optional[Any] = None
        result_content: str = "[Tool Execution Error: Unknown]" # Default content

        try:
            logger.debug(f"Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}'")
            # Execute via ToolExecutor, which handles validation and returns raw result/string
            raw_result = await self.tool_executor.execute_tool(
                agent.agent_id,
                agent.sandbox_path,
                tool_name,
                tool_args
            )
            logger.debug(f"Tool '{tool_name}' completed execution.")

            # Determine the 'content' for history based on result type
            if isinstance(raw_result, dict):
                 # For dict results (like ManageTeamTool), use its message or stringify
                 result_content = raw_result.get("message", str(raw_result))
            elif isinstance(raw_result, str):
                 result_content = raw_result
            else:
                 # Fallback for unexpected types
                 result_content = str(raw_result)

        except Exception as e:
            error_msg = f"Manager error during _execute_single_tool '{tool_name}': {e}"
            logger.error(error_msg, exc_info=True)
            result_content = f"[ToolExec Error: {error_msg}]"
            raw_result = None # Ensure raw_result is None on error
        finally:
            # Set status back to processing (or idle/error if needed)
            # Should ideally be PROCESSING, as the agent loop continues
            if agent.status == AGENT_STATUS_EXECUTING_TOOL:
                agent.set_status(AGENT_STATUS_PROCESSING)

        # Return dictionary including raw result for manager post-processing
        return {"call_id": call_id, "content": result_content, "_raw_result": raw_result}


    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        """Returns a formatted error result dictionary for failed tool dispatch."""
        error_content = f"[ToolExec Error: Failed dispatch for '{tool_name or 'unknown'}'. Invalid format or arguments.]"
        final_call_id = call_id or f"invalid_call_{int(time.time())}"
        # Ensure _raw_result indicates failure
        return {"call_id": final_call_id, "content": error_content, "_raw_result": {"status": "error", "message": error_content}}

    async def push_agent_status_update(self, agent_id: str):
        """Retrieves full agent state and sends to UI."""
        agent = self.agents.get(agent_id)
        if agent:
            state = agent.get_state()
            state["team"] = self.agent_to_team.get(agent_id) # Add team info
            await self.send_to_ui({
                "type": "agent_status_update",
                "agent_id": agent_id,
                "status": state # Send the whole state dictionary
                })
        else:
            logger.warning(f"Cannot push status update for unknown agent: {agent_id}")

    async def send_to_ui(self, message_data: Dict[str, Any]):
        """Sends JSON-serialized data to all UI clients via broadcast."""
        if not self.send_to_ui_func:
            logger.warning("UI broadcast function not configured. Cannot send message.")
            return
        try:
            # Ensure message_data is serializable
            json_message = json.dumps(message_data)
            await self.send_to_ui_func(json_message)
        except TypeError as e:
            logger.error(f"JSON serialization error sending to UI: {e}. Data: {message_data}", exc_info=True)
        except Exception as e:
            logger.error(f"Error sending message to UI: {e}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Synchronously gets status snapshot of all agents."""
        statuses = {}
        for agent_id, agent in self.agents.items():
             state = agent.get_state()
             state["team"] = self.agent_to_team.get(agent_id) # Add team info
             statuses[agent_id] = state
        return statuses


    # --- Session Persistence Methods ---
    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the current state including dynamic agent configs (with final prompts) and histories."""
        if not project_name: return False, "Project name cannot be empty."
        if not session_name: session_name = f"session_{int(time.time())}"
        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Saving session state to: {session_file_path}")

        session_data = {
            "project": project_name,
            "session": session_name,
            "timestamp": time.time(),
            "teams": self.teams, # Current team structure
            "agent_to_team": self.agent_to_team, # Current agent mappings
            "dynamic_agents_config": {}, # Store config for DYNAMIC agents only
            "agent_histories": {} # Store histories for ALL agents
        }

        # Iterate through current agents
        for agent_id, agent in self.agents.items():
            # Save history for all agents
            try:
                json.dumps(agent.message_history) # Quick check for basic serializability
                session_data["agent_histories"][agent_id] = agent.message_history
            except TypeError as e:
                logger.error(f"History for agent '{agent_id}' is not JSON serializable: {e}. Saving placeholder.")
                session_data["agent_histories"][agent_id] = [{"role": "system", "content": f"[History Serialization Error: {e}]"}]

            # Save config ONLY for dynamic agents
            if agent_id not in self.bootstrap_agents:
                 try:
                      config_to_save = agent.agent_config.get("config") if hasattr(agent, 'agent_config') else None
                      if config_to_save:
                          session_data["dynamic_agents_config"][agent_id] = config_to_save
                          logger.debug(f"Saved final config for dynamic agent '{agent_id}'.")
                      else:
                          logger.warning(f"Could not find agent_config attribute on dynamic agent '{agent_id}'. Config not saved.")
                 except Exception as e_cfg:
                      logger.warning(f"Error accessing config for dynamic agent '{agent_id}': {e_cfg}. Config not saved.", exc_info=True)

        # Save to file asynchronously
        try:
            def save_sync():
                session_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(session_file_path, 'w', encoding='utf-8') as f:
                     json.dump(session_data, f, indent=2)

            await asyncio.to_thread(save_sync)
            logger.info(f"Session saved successfully: {session_file_path}")
            self.current_project, self.current_session = project_name, session_name
            await self.send_to_ui({"type": "system_event", "event": "session_saved", "project": project_name, "session": session_name})
            return True, f"Session '{session_name}' saved successfully in project '{project_name}'."
        except Exception as e:
            logger.error(f"Error saving session file to {session_file_path}: {e}", exc_info=True)
            return False, f"Error saving session file: {e}"

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Loads dynamic agents, teams, and histories from a saved session file."""
        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Attempting to load session from: {session_file_path}")
        if not session_file_path.is_file():
            return False, f"Session file '{session_name}' not found in project '{project_name}'."

        try:
            # Load session data from file
            def load_sync():
                 with open(session_file_path, 'r', encoding='utf-8') as f:
                     return json.load(f)
            session_data = await asyncio.to_thread(load_sync)

            # --- Clear current dynamic state before loading ---
            dynamic_agents_to_delete = [aid for aid in self.agents if aid not in self.bootstrap_agents]
            logger.info(f"Clearing current dynamic state. Agents to delete: {dynamic_agents_to_delete}")
            delete_results = await asyncio.gather( *(self.delete_agent_instance(aid) for aid in dynamic_agents_to_delete), return_exceptions=True )
            for i, res in enumerate(delete_results):
                 if isinstance(res, Exception): logger.error(f"Error deleting agent {dynamic_agents_to_delete[i]} during load: {res}")
            self.teams, self.agent_to_team = {}, {} # Reset team structures
            for boot_id in self.bootstrap_agents:
                if boot_id in self.agents: self.agents[boot_id].clear_history()
            logger.info("Cleared current dynamic agents and teams.")
            # --- End Clearing ---

            # Load teams and mappings
            self.teams = session_data.get("teams", {})
            self.agent_to_team = session_data.get("agent_to_team", {})
            dynamic_configs = session_data.get("dynamic_agents_config", {})
            histories = session_data.get("agent_histories", {})
            logger.info(f"Loaded {len(self.teams)} teams and {len(dynamic_configs)} dynamic agent configs from session file.")

            # Recreate dynamic agents
            creation_tasks = []
            for agent_id, agent_cfg in dynamic_configs.items():
                team_id = self.agent_to_team.get(agent_id)
                creation_tasks.append(self._create_agent_internal( agent_id_requested=agent_id, agent_config_data=agent_cfg, is_bootstrap=False, team_id=team_id, loading_from_session=True ))

            creation_results = await asyncio.gather(*creation_tasks, return_exceptions=True)
            successful_creations = 0; failed_creations = []
            for i, result in enumerate(creation_results):
                 agent_id_attempted = list(dynamic_configs.keys())[i]
                 if isinstance(result, Exception): logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {result}", exc_info=result); failed_creations.append(f"{agent_id_attempted} (Error: {result})")
                 elif isinstance(result, tuple) and result[0]: successful_creations += 1
                 else: error_msg = result[1] if isinstance(result, tuple) else 'Unknown creation error'; logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {error_msg}"); failed_creations.append(f"{agent_id_attempted} (Failed: {error_msg})")
            logger.info(f"Successfully recreated {successful_creations}/{len(dynamic_configs)} dynamic agents.")
            if failed_creations: logger.warning(f"Failed to recreate the following agents: {', '.join(failed_creations)}")

            # Restore histories
            loaded_history_count = 0
            for agent_id, history in histories.items():
                agent = self.agents.get(agent_id)
                if agent:
                     if isinstance(history, list) and all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in history):
                         agent.message_history = history; agent.set_status(AGENT_STATUS_IDLE); loaded_history_count += 1
                     else: logger.warning(f"Invalid or missing history format for agent '{agent_id}' in session file. History not loaded.")
            logger.info(f"Loaded histories for {loaded_history_count} agents.")
            self.current_project, self.current_session = project_name, session_name

            # Send full state update to UI
            await asyncio.gather(*(self.push_agent_status_update(aid) for aid in self.agents.keys()))

            load_message = f"Session '{session_name}' loaded successfully. {successful_creations} dynamic agents recreated."
            if failed_creations: load_message += f" Failed to recreate {len(failed_creations)} agents."
            return True, load_message

        except json.JSONDecodeError as e: logger.error(f"JSON decode error loading session file {session_file_path}: {e}", exc_info=True); return False, "Invalid session file format (JSON decode error)."
        except Exception as e: logger.error(f"Unexpected error loading session from {session_file_path}: {e}", exc_info=True); return False, f"Unexpected error loading session: {e}"


    # --- Cleanup ---
    async def cleanup_providers(self):
        """ Calls cleanup methods (close_session) on all active LLM providers. """
        logger.info("Cleaning up LLM providers...")
        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        logger.info(f"Found {len(active_providers)} unique provider instances to clean up.")
        tasks = [ asyncio.create_task(self._close_provider_safe(provider)) for provider in active_providers if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session) ]
        if tasks: await asyncio.gather(*tasks); logger.info("LLM Provider cleanup tasks completed.")
        else: logger.info("No provider cleanup tasks were necessary.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        """Safely attempts to call the asynchronous close_session method on a provider."""
        try: logger.info(f"Attempting to close session for provider: {provider!r}"); await provider.close_session(); logger.info(f"Successfully closed session for provider: {provider!r}")
        except Exception as e: logger.error(f"Error closing session for provider {provider!r}: {e}", exc_info=True)
