# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple
import json
import os
import traceback
import time # Needed for default session name timestamp
import logging
import uuid

# Import Agent class, Status constants, and BaseLLMProvider types
from src.agents.core import (
    Agent, AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE
)
from src.llm_providers.base import BaseLLMProvider, ToolResultDict, MessageDict

# Import settings instance, BASE_DIR, and default values
from src.config.settings import settings, BASE_DIR

# Import WebSocket broadcast function
from src.api.websocket_manager import broadcast

# Import ToolExecutor and specific Tool classes for checks if needed
from src.tools.executor import ToolExecutor
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool

# Import Provider classes
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

# --- Import the component managers and utils ---
from src.agents.state_manager import AgentStateManager
from src.agents.session_manager import SessionManager
from src.agents.interaction_handler import AgentInteractionHandler # <-- New Handler
from src.agents.prompt_utils import ( # <-- New Prompt Utils
    STANDARD_FRAMEWORK_INSTRUCTIONS,
    ADMIN_AI_OPERATIONAL_INSTRUCTIONS,
    update_agent_prompt_team_id
)

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
STREAM_RETRY_DELAYS = [5.0, 10.0, 10.0, 65.0] # Retry delays
MAX_STREAM_RETRIES = len(STREAM_RETRY_DELAYS) # Max retries
DEFAULT_PROJECT_NAME = "DefaultProject" # Name for auto-created project

class AgentManager:
    """
    Main coordinator for agents. Manages agent lifecycle, orchestrates task execution
    via interaction handler, delegates state/session management, handles errors/retries.
    Injects standard framework instructions into dynamic agents and Admin AI.
    Automatically creates a default project/session context on first user message if none exists.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        # (Initialization code remains the same...)
        self.bootstrap_agents: List[str] = []
        self.agents: Dict[str, Agent] = {}
        self.send_to_ui_func = broadcast
        logger.info("Instantiating ToolExecutor...")
        self.tool_executor = ToolExecutor()
        logger.info("ToolExecutor instantiated.")
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        logger.info("Generated XML tool descriptions for prompts.")
        logger.info("Instantiating AgentStateManager...")
        self.state_manager = AgentStateManager(self)
        logger.info("AgentStateManager instantiated.")
        logger.info("Instantiating SessionManager...")
        self.session_manager = SessionManager(self, self.state_manager)
        logger.info("SessionManager instantiated.")
        logger.info("Instantiating AgentInteractionHandler...")
        self.interaction_handler = AgentInteractionHandler(self)
        logger.info("AgentInteractionHandler instantiated.")
        self.reactivate_agent_flags: Dict[str, bool] = {}
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        self._ensure_projects_dir()
        logger.info("AgentManager initialized synchronously. Bootstrap agents will be loaded asynchronously.")


    def _ensure_projects_dir(self):
        # (Code remains the same...)
        try:
             settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)


    async def initialize_bootstrap_agents(self):
        # (Code remains the same...)
        logger.info("Initializing bootstrap agents asynchronously...")
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list: logger.warning("No bootstrap agent configurations found."); return

        main_sandbox_dir = BASE_DIR / "sandboxes"
        try: await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
        except Exception as e: logger.error(f"Error creating main sandbox directory: {e}")

        tasks = []
        formatted_allowed_models = settings.get_formatted_allowed_models()

        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id: logger.warning("Skipping bootstrap agent due to missing 'agent_id'."); continue

            agent_config_data = agent_conf_entry.get("config", {})
            provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
            if not settings.is_provider_configured(provider_name):
                logger.error(f"Cannot initialize '{agent_id}': Provider '{provider_name}' not configured. Skipping."); continue

            final_agent_config_data = agent_config_data.copy()

            if agent_id == BOOTSTRAP_AGENT_ID:
                user_defined_prompt = final_agent_config_data.get("system_prompt", "")
                operational_instructions = ADMIN_AI_OPERATIONAL_INSTRUCTIONS.format(
                    tool_descriptions_xml=self.tool_descriptions_xml
                )
                final_agent_config_data["system_prompt"] = (
                    f"--- Primary Goal/Persona ---\n{user_defined_prompt}\n\n"
                    f"{operational_instructions}\n\n"
                    f"---\n{formatted_allowed_models}\n---"
                )
                logger.info(f"Assembled final prompt for '{BOOTSTRAP_AGENT_ID}' using updated prompt_utils.")
            else:
                logger.info(f"Using system prompt from config for bootstrap agent '{agent_id}'.")

            tasks.append(self._create_agent_internal(
                agent_id_requested=agent_id,
                agent_config_data=final_agent_config_data,
                is_bootstrap=True
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_ids = []
        for i, result in enumerate(results):
             agent_id_log = f"unknown_{i}";
             if isinstance(result, tuple) and result[0]:
                 created_agent_id = result[2]
                 if created_agent_id:
                     agent_id_log = created_agent_id
                     self.bootstrap_agents.append(created_agent_id)
                     successful_ids.append(created_agent_id)
                     logger.info(f"--- Bootstrap agent '{created_agent_id}' initialized. ---")
                 else: logger.error(f"--- Failed bootstrap init '{agent_id_log}': {result[1]} ---")
             else: logger.error(f"--- Failed bootstrap init '{agent_id_log}': {result} ---", exc_info=isinstance(result, Exception))

        logger.info(f"Finished bootstrap initialization. Active: {successful_ids}")
        if BOOTSTRAP_AGENT_ID not in self.agents:
             logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize!")


    async def _create_agent_internal(
        self,
        agent_id_requested: Optional[str],
        agent_config_data: Dict[str, Any],
        is_bootstrap: bool = False,
        team_id: Optional[str] = None,
        loading_from_session: bool = False
        ) -> Tuple[bool, str, Optional[str]]:
        # (Code remains the same...)
        agent_id: Optional[str] = None
        if agent_id_requested and agent_id_requested in self.agents: msg = f"Agent ID '{agent_id_requested}' already exists."; logger.error(msg); return False, msg, None
        elif agent_id_requested: agent_id = agent_id_requested
        else: agent_id = self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to determine Agent ID.", None
        logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")

        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)
        if not settings.is_provider_configured(provider_name): msg = f"Provider '{provider_name}' not configured."; logger.error(msg); return False, msg, None
        if not is_bootstrap and not loading_from_session:
            allowed_models = settings.ALLOWED_SUB_AGENT_MODELS.get(provider_name)
            valid_allowed = [m for m in (allowed_models or []) if m and m.strip()]
            if not valid_allowed or model not in valid_allowed:
                 allowed_str = ', '.join(valid_allowed) if valid_allowed else 'None'
                 msg = f"Model '{model}' not allowed for '{provider_name}'. Allowed: [{allowed_str}]"; logger.error(msg); return False, msg, None
            logger.info(f"Dynamic agent model validated: '{provider_name}/{model}'.")

        role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        allowed_provider_keys = ['api_key', 'base_url', 'referer']
        agent_config_keys_to_exclude = ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'project_name', 'session_name'] + allowed_provider_keys
        provider_specific_kwargs = {k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude}
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]

        final_system_prompt = role_specific_prompt
        if not loading_from_session and not is_bootstrap:
             logger.debug(f"Constructing final prompt for dynamic agent '{agent_id}' using prompt_utils...")
             standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(
                 agent_id=agent_id, team_id=team_id or "N/A", tool_descriptions_xml=self.tool_descriptions_xml
             )
             final_system_prompt = standard_info + "\n\n--- Your Specific Role & Task ---\n" + role_specific_prompt
             logger.info(f"Injected standard framework instructions for dynamic agent '{agent_id}'.")
        elif loading_from_session: final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt)
        elif is_bootstrap: final_system_prompt = agent_config_data.get("system_prompt", final_system_prompt)

        final_agent_config_entry = { "agent_id": agent_id, "config": { "provider": provider_name, "model": model, "system_prompt": final_system_prompt, "persona": persona, "temperature": temperature, **provider_specific_kwargs } }

        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass: msg = f"Unknown provider '{provider_name}'"; logger.error(msg); return False, msg, None
        base_provider_config = settings.get_provider_config(provider_name)
        provider_config_overrides = {k: agent_config_data[k] for k in allowed_provider_keys if k in agent_config_data}
        final_provider_args = {**base_provider_config, **provider_specific_kwargs, **provider_config_overrides}
        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        try: llm_provider_instance = ProviderClass(**final_provider_args)
        except Exception as e: msg = f"Provider instantiation failed: {e}"; logger.error(msg, exc_info=True); return False, msg, None
        logger.info(f"  Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")

        try: agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=self)
        except Exception as e: msg = f"Agent instantiation failed: {e}"; logger.error(msg, exc_info=True); await self._close_provider_safe(llm_provider_instance); return False, msg, None
        logger.info(f"  Instantiated Agent object for '{agent_id}'.")

        try: await asyncio.to_thread(agent.ensure_sandbox_exists)
        except Exception as e: logger.error(f"  Error ensuring sandbox for '{agent_id}': {e}", exc_info=True)

        self.agents[agent_id] = agent
        logger.debug(f"Agent '{agent_id}' added to self.agents dictionary.")

        team_add_msg_suffix = ""
        if team_id:
            team_add_success, team_add_msg = await self.state_manager.add_agent_to_team(agent_id, team_id)
            if team_add_success: logger.info(f"Agent '{agent_id}' state added to team '{team_id}'.")
            else: team_add_msg_suffix = f" (Warning adding to team state: {team_add_msg})"

        message = f"Agent '{agent_id}' created successfully." + team_add_msg_suffix
        return True, message, agent_id


    async def create_agent_instance(
        self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str,
        team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs
        ) -> Tuple[bool, str, Optional[str]]:
        """Public method for dynamic agents. Calls internal logic & notifies UI."""
        # (Code remains the same)
        if not all([provider, model, system_prompt, persona]): return False, "Missing required args.", None
        agent_config_data = {"provider": provider, "model": model, "system_prompt": system_prompt, "persona": persona}
        if temperature is not None: agent_config_data["temperature"] = temperature
        known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
        extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args and k not in ['project_name', 'session_name']}
        agent_config_data.update(extra_kwargs)

        success, message, created_agent_id = await self._create_agent_internal(
            agent_id_requested=agent_id_requested, agent_config_data=agent_config_data, is_bootstrap=False, team_id=team_id, loading_from_session=False
        )
        if success and created_agent_id:
            agent = self.agents.get(created_agent_id); team = self.state_manager.get_agent_team(created_agent_id)
            config_ui = agent.agent_config.get("config", {}) if agent else {}
            await self.send_to_ui({"type": "agent_added", "agent_id": created_agent_id, "config": config_ui, "team": team})
            await self.push_agent_status_update(created_agent_id)
        return success, message, created_agent_id


    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        """Removes dynamic agent, cleans up, updates state."""
        # (Code remains the same)
        if not agent_id: return False, "Agent ID empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."

        agent_instance = self.agents.pop(agent_id)
        self.state_manager.remove_agent_from_all_teams_state(agent_id)
        await self._close_provider_safe(agent_instance.llm_provider)
        message = f"Agent '{agent_id}' deleted."
        logger.info(message)
        await self.send_to_ui({"type": "agent_deleted", "agent_id": agent_id})
        return True, message


    def _generate_unique_agent_id(self, prefix="agent") -> str:
        """Generates unique agent ID."""
        # (Code remains the same)
        timestamp = int(time.time() * 1000)
        short_uuid = uuid.uuid4().hex[:4]
        while True:
            new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_")
            if new_id not in self.agents: return new_id
            time.sleep(0.001)
            timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Routes user message to Admin AI.
        Ensures a default project/session context exists before proceeding.
        """
        # (Code remains the same)
        logger.info(f"Received user message for Admin AI: '{message[:100]}...'")
        if self.current_project is None:
            logger.info("No active project/session context found. Creating default context...")
            default_project = DEFAULT_PROJECT_NAME
            default_session = time.strftime("%Y%m%d_%H%M%S")
            try:
                success, save_msg = await self.save_session(default_project, default_session)
                if success:
                    logger.info(f"Auto-created and saved default session: '{default_project}/{default_session}'")
                    await self.send_to_ui({"type": "status", "agent_id": "manager", "content": f"Created default session: {default_project}/{default_session}"})
                else:
                    logger.error(f"Failed to auto-save default session '{default_project}/{default_session}': {save_msg}")
                    await self.send_to_ui({"type": "error", "agent_id": "manager", "content": f"Failed to create default session: {save_msg}"})
            except Exception as e:
                logger.error(f"Unexpected error during default session auto-save: {e}", exc_info=True)
                await self.send_to_ui({"type": "error", "agent_id": "manager", "content": f"Error creating default session: {e}"})

        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent:
            logger.error(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') not found. Cannot process message.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."})
            return

        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Delegating message to '{BOOTSTRAP_AGENT_ID}'.")
            admin_agent.message_history.append({"role": "user", "content": message})
            asyncio.create_task(self._handle_agent_generator(admin_agent))
        elif admin_agent.status == AGENT_STATUS_AWAITING_USER_OVERRIDE:
             logger.warning(f"Admin AI ({admin_agent.status}) awaiting override. Message ignored."); await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": "Admin AI waiting..." })
        else:
            logger.info(f"Admin AI busy ({admin_agent.status}). Message queued."); admin_agent.message_history.append({"role": "user", "content": message})
            await self.push_agent_status_update(admin_agent.agent_id); await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Queued." })


    async def handle_user_override(self, override_data: Dict[str, Any]):
        """Handles user override for a stuck agent."""
        # (Code remains the same)
        agent_id = override_data.get("agent_id"); new_provider_name = override_data.get("new_provider"); new_model = override_data.get("new_model")
        if not all([agent_id, new_provider_name, new_model]): logger.error(f"Invalid override data: {override_data}"); return
        agent = self.agents.get(agent_id)
        if not agent: logger.error(f"Override error: Agent '{agent_id}' not found."); return
        if agent.status != AGENT_STATUS_AWAITING_USER_OVERRIDE: logger.warning(f"Override for '{agent_id}' ignored (Status: {agent.status})."); return

        logger.info(f"Applying user override for '{agent_id}'. New: {new_provider_name}/{new_model}")
        ProviderClass = PROVIDER_CLASS_MAP.get(new_provider_name)
        if not ProviderClass or not settings.is_provider_configured(new_provider_name):
            error_msg = f"Override failed: Provider '{new_provider_name}' unknown/unconfigured."; logger.error(error_msg)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": error_msg}); return

        old_provider_instance = agent.llm_provider; old_provider_name = agent.provider_name; old_model = agent.model
        try:
            agent.provider_name = new_provider_name; agent.model = new_model
            if hasattr(agent, 'agent_config') and "config" in agent.agent_config: agent.agent_config["config"].update({"provider": new_provider_name, "model": new_model})
            base_provider_config = settings.get_provider_config(new_provider_name)
            provider_kwargs = {k: v for k, v in agent.agent_config.get("config", {}).items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
            final_provider_args = {**base_provider_config, **provider_kwargs}; final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
            new_provider_instance = ProviderClass(**final_provider_args)
            agent.llm_provider = new_provider_instance
            await self._close_provider_safe(old_provider_instance)
            logger.info(f"Override applied for '{agent_id}'. Restarting cycle.")
            agent.set_status(AGENT_STATUS_IDLE)
            await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Override applied. Retrying with {new_provider_name}/{new_model}."})
            asyncio.create_task(self._handle_agent_generator(agent, 0))
        except Exception as e:
            logger.error(f"Error applying override for '{agent_id}': {e}", exc_info=True)
            agent.provider_name = old_provider_name; agent.model = old_model; agent.llm_provider = old_provider_instance
            if hasattr(agent, 'agent_config') and "config" in agent.agent_config: agent.agent_config["config"].update({"provider": old_provider_name, "model": old_model})
            agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"Failed to apply override: {e}. Try again."})

    # --- *** MODIFIED _handle_agent_generator *** ---
    async def _handle_agent_generator(self, agent: Agent, retry_count: int = 0):
        """
        Manages agent's process_message generator loop. Handles events, errors, retries,
        delegates tool execution/handling, passes context, and handles reactivation.
        """
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}' (Retry: {retry_count}).")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        current_cycle_error = False; is_stream_related_error = False; last_error_content = ""
        history_len_before = len(agent.message_history)
        executed_tool_successfully_this_cycle = False # Track if any tool ran

        try:
            agent_generator = agent.process_message()
            while True:
                try: event = await agent_generator.asend(None)
                except StopAsyncIteration: logger.info(f"Agent '{agent_id}' generator finished."); break
                except Exception as gen_err: logger.error(f"Generator error for '{agent_id}': {gen_err}", exc_info=True); current_cycle_error = True; break

                event_type = event.get("type")
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                             agent.message_history.append({"role": "assistant", "content": final_content})
                elif event_type == "error":
                    last_error_content = event.get("content", "[Agent Error]")
                    logger.error(f"Agent '{agent_id}' reported error: {last_error_content}")
                    is_stream_related_error = any(ind in last_error_content for ind in ["Error processing stream", "APIError during stream", "decode stream chunk", "Stream connection", "Provider returned error", "connection/timeout", "Status 429", "RateLimitError", "Status 500", "Status 503"])
                    if not is_stream_related_error:
                        if "agent_id" not in event: event["agent_id"] = agent_id
                        await self.send_to_ui(event); agent.set_status(AGENT_STATUS_ERROR)
                    current_cycle_error = True; break
                elif event_type == "tool_requests":
                    all_tool_calls: List[Dict] = event.get("calls", [])
                    if not all_tool_calls: continue
                    logger.info(f"Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response})

                    mgmt_calls = []; other_calls = []; invalid_call_results = []
                    for call in all_tool_calls:
                         cid, tname, targs = call.get("id"), call.get("name"), call.get("arguments", {})
                         if cid and tname and isinstance(targs, dict):
                             if tname == ManageTeamTool.name: mgmt_calls.append(call)
                             else: other_calls.append(call)
                         else:
                             fail_res = await self.interaction_handler.failed_tool_result(cid, tname);
                             if fail_res: invalid_call_results.append(fail_res)
                    if invalid_call_results:
                        for res in invalid_call_results: agent.message_history.append({"role": "tool", **res})

                    calls_to_execute = mgmt_calls + other_calls
                    activation_tasks = []
                    manager_action_feedback = []
                    # Reset flag for this batch of tool calls
                    executed_tool_successfully_this_cycle = False
                    if calls_to_execute:
                        logger.info(f"Executing {len(calls_to_execute)} tool(s) sequentially for '{agent_id}'.")
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})

                        for call in calls_to_execute:
                            call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']
                            result = await self.interaction_handler.execute_single_tool(
                                agent, call_id, tool_name, tool_args,
                                project_name=self.current_project, session_name=self.current_session
                            )
                            if result:
                                raw_content_hist = result.get("content", "[Tool Error: No content]")
                                tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_hist)}
                                if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id:
                                     agent.message_history.append(tool_msg)

                                # Check if the tool execution itself was successful (not just returned something)
                                # A simple check: assume success if content doesn't start with common error indicators
                                tool_exec_success = not str(raw_content_hist).strip().startswith(("Error:", "[ToolExec Error:", "[Manager Error:"))
                                if tool_exec_success:
                                    executed_tool_successfully_this_cycle = True # Mark success

                                raw_tool_output = result.get("_raw_result")
                                if tool_name == ManageTeamTool.name:
                                    if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                        action = raw_tool_output.get("action"); params = raw_tool_output.get("params", {})
                                        act_success, act_msg, act_data = await self.interaction_handler.handle_manage_team_action(action, params, agent_id)
                                        feedback = {"call_id": call_id, "action": action, "success": act_success, "message": act_msg}
                                        if act_data: feedback["data"] = act_data
                                        manager_action_feedback.append(feedback)
                                    elif isinstance(raw_tool_output, dict):
                                         manager_action_feedback.append({"call_id": call_id, "action": raw_tool_output.get("action"), "success": False, "message": raw_tool_output.get("message", "Tool execution failed.")})
                                    else:
                                         manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result structure."})
                                elif tool_name == SendMessageTool.name:
                                    target_id = call['arguments'].get("target_agent_id"); msg_content = call['arguments'].get("message_content")
                                    if target_id and msg_content is not None:
                                        activation_task = await self.interaction_handler.route_and_activate_agent_message(agent_id, target_id, msg_content)
                                        if activation_task: activation_tasks.append(activation_task)
                                    else:
                                        manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": "Validation Error: Missing target_id or message_content."})
                            else:
                                 manager_action_feedback.append({"call_id": call_id, "action": tool_name, "success": False, "message": "Tool execution failed unexpectedly (no result)."})

                        if activation_tasks: await asyncio.gather(*activation_tasks); logger.info(f"Completed activation tasks for '{agent_id}'.")
                        if manager_action_feedback:
                             feedback_appended = False
                             for fb in manager_action_feedback:
                                 fb_content = f"[Manager Result for {fb.get('action', 'N/A')} (Call ID: {fb['call_id']})]: Success={fb['success']}. Message: {fb['message']}"
                                 if fb.get("data"):
                                     try: data_str = json.dumps(fb['data'], indent=2); fb_content += f"\nData:\n{data_str[:1500]}{'... (truncated)' if len(data_str) > 1500 else ''}"
                                     except TypeError: fb_content += "\nData: [Unserializable Data]"
                                 fb_msg: MessageDict = {"role": "tool", "tool_call_id": fb['call_id'], "content": fb_content}
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != fb_content:
                                      agent.message_history.append(fb_msg); feedback_appended = True
                             if feedback_appended: self.reactivate_agent_flags[agent_id] = True # Signal reactivation

                        # --- ** NEW: Reactivate after successful standard tool calls ** ---
                        if executed_tool_successfully_this_cycle and not manager_action_feedback:
                             logger.debug(f"Successful standard tool execution detected for '{agent_id}'. Setting reactivation flag.")
                             self.reactivate_agent_flags[agent_id] = True
                        # --- ** END NEW ** ---

                else: logger.warning(f"Unknown event type '{event_type}' from '{agent_id}'.")

        except Exception as e:
             logger.error(f"Error handling generator for '{agent_id}': {e}", exc_info=True); current_cycle_error = True
             last_error_content = f"[Manager Error: Unexpected error in generator handler - {e}]"
             await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
        finally:
            if agent_generator: try: await agent_generator.aclose() except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            # --- Retry / Override / Reactivation Logic (Remains the same) ---
            if current_cycle_error and is_stream_related_error and retry_count < MAX_STREAM_RETRIES:
                retry_delay = STREAM_RETRY_DELAYS[retry_count]; logger.warning(f"Stream error for '{agent_id}'. Retrying in {retry_delay:.1f}s ({retry_count + 1}/{MAX_STREAM_RETRIES})...")
                await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES}, delay {retry_delay}s)..."})
                await asyncio.sleep(retry_delay); agent.set_status(AGENT_STATUS_IDLE)
                asyncio.create_task(self._handle_agent_generator(agent, retry_count + 1))
            elif current_cycle_error and is_stream_related_error: # Max retries reached
                logger.error(f"Agent '{agent_id}' failed after {MAX_STREAM_RETRIES} retries. Requesting user override.")
                agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
                await self.send_to_ui({"type": "request_user_override", "agent_id": agent_id, "persona": agent.persona, "current_provider": agent.provider_name, "current_model": agent.model, "last_error": last_error_content, "message": f"Agent '{agent.persona}' failed after retries."})
            elif self.reactivate_agent_flags.pop(agent_id, False) and not current_cycle_error: # Check and clear flag
                logger.info(f"Reactivating agent '{agent_id}' due to manager feedback/interaction signal."); agent.set_status(AGENT_STATUS_IDLE)
                asyncio.create_task(self._handle_agent_generator(agent, 0))
            elif not current_cycle_error: # Check for new messages if cycle finished cleanly
                 history_len_after = len(agent.message_history)
                 if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                      logger.info(f"Agent '{agent_id}' has new user message(s). Reactivating."); agent.set_status(AGENT_STATUS_IDLE)
                      asyncio.create_task(self._handle_agent_generator(agent, 0))
                 else: logger.debug(f"Agent '{agent_id}' finished cleanly, no reactivation needed.")

            if agent_id not in self.reactivate_agent_flags:
                 final_status = agent.status
                 if final_status not in [AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE, AGENT_STATUS_AWAITING_TOOL]:
                      logger.warning(f"Agent '{agent_id}' ended in unexpected state '{final_status}'. Setting IDLE.")
                      agent.set_status(AGENT_STATUS_IDLE)
                 await self.push_agent_status_update(agent_id)

            log_level = logging.ERROR if agent.status in [AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE] else logging.INFO
            logger.log(log_level, f"Manager finished cycle for Agent '{agent_id}'. Final status: {agent.status}")

    # --- Other methods remain the same ---

    async def push_agent_status_update(self, agent_id: str):
        # (Code remains the same)
        agent = self.agents.get(agent_id)
        if agent: state = agent.get_state(); state["team"] = self.state_manager.get_agent_team(agent_id)
        else: state = {"status": "deleted", "team": None}; logger.warning(f"Cannot push status update for unknown/deleted agent: {agent_id}")
        await self.send_to_ui({"type": "agent_status_update", "agent_id": agent_id, "status": state})

    async def send_to_ui(self, message_data: Dict[str, Any]):
        # (Code remains the same)
        if not self.send_to_ui_func: logger.warning("UI broadcast func not set."); return
        try: await self.send_to_ui_func(json.dumps(message_data))
        except Exception as e: logger.error(f"Error sending to UI: {e}. Data: {message_data}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        # (Code remains the same)
        return {aid: (ag.get_state() | {"team": self.state_manager.get_agent_team(aid)}) for aid, ag in self.agents.items()}

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        # (Code remains the same)
        logger.info(f"Delegating save_session for '{project_name}'...")
        return await self.session_manager.save_session(project_name, session_name)

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        # (Code remains the same)
        logger.info(f"Delegating load_session for '{project_name}/{session_name}'...")
        return await self.session_manager.load_session(project_name, session_name)

    async def cleanup_providers(self):
        # (Code remains the same)
        logger.info("Cleaning up LLM providers...");
        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        tasks = [asyncio.create_task(self._close_provider_safe(p)) for p in active_providers if hasattr(p, 'close_session')]
        if tasks: await asyncio.gather(*tasks); logger.info("Provider cleanup complete.")
        else: logger.info("No provider cleanup needed.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        # (Code remains the same)
        try: await provider.close_session(); logger.info(f"Closed session for {provider!r}")
        except Exception as e: logger.error(f"Error closing session for {provider!r}: {e}", exc_info=True)

    def get_agent_info_list_sync(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        # (Code remains the same)
        info_list = []
        for agent_id, agent in self.agents.items():
             current_team = self.state_manager.get_agent_team(agent_id)
             if filter_team_id is not None and current_team != filter_team_id: continue
             state = agent.get_state()
             info = {"agent_id": agent_id, "persona": state.get("persona"), "provider": state.get("provider"), "model": state.get("model"), "status": state.get("status"), "team": current_team}
             info_list.append(info)
        return info_list
