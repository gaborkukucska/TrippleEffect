# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple, Set # Added Set
import json
import os
import traceback
import time
import logging
import uuid
import fnmatch
import copy

# Import Agent class, Status constants, and BaseLLMProvider types
from src.agents.core import (
    Agent, AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_ERROR # Removed AWAITING_USER_OVERRIDE
)
from src.llm_providers.base import BaseLLMProvider, ToolResultDict, MessageDict

# Import settings, model_registry, AND BASE_DIR
from src.config.settings import settings, model_registry, BASE_DIR

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
# TODO: Add LiteLLMProvider when implemented

# Import the component managers and utils
from src.agents.state_manager import AgentStateManager
from src.agents.session_manager import SessionManager
from src.agents.interaction_handler import AgentInteractionHandler
# --- Import AgentCycleHandler constants ---
from src.agents.cycle_handler import (
    AgentCycleHandler, MAX_STREAM_RETRIES, STREAM_RETRY_DELAYS, MAX_FAILOVER_ATTEMPTS
)
# --- End Import ---
from src.agents.prompt_utils import (
    STANDARD_FRAMEWORK_INSTRUCTIONS,
    ADMIN_AI_OPERATIONAL_INSTRUCTIONS,
    update_agent_prompt_team_id
)
from src.agents.performance_tracker import ModelPerformanceTracker

from pathlib import Path

logger = logging.getLogger(__name__)

# Mapping from provider name string to provider class
PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    # TODO: Add LiteLLMProvider when implemented
}

# Constants and Preferred Admin Models
BOOTSTRAP_AGENT_ID = "admin_ai"
# Retry/Failover constants now imported from cycle_handler
DEFAULT_PROJECT_NAME = "DefaultProject"
PREFERRED_ADMIN_MODELS = [ # For initial Admin AI selection only
    "anthropic/claude-3-opus*", "openai/gpt-4o*", "google/gemini-2.5-pro*",
    "llama3:70b*", "command-r-plus*", "qwen/qwen2-72b-instruct*",
    "anthropic/claude-3-sonnet*", "google/gemini-pro*", "llama3*",
    "mistralai/mixtral-8x7b*", "mistralai/mistral-large*", "*wizardlm2*",
    "*deepseek-coder*", "google/gemini-flash*", "*"
]
# MAX_FAILOVER_ATTEMPTS is now imported from cycle_handler


class AgentManager:
    """
    Main coordinator for agents. Includes automatic failover logic
    for persistent provider/model errors during agent cycles.
    User override mechanism removed. Handles retries via AgentCycleHandler.
    Prioritizes local providers during failover.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        self.bootstrap_agents: List[str] = []
        self.agents: Dict[str, Agent] = {}
        self.send_to_ui_func = broadcast
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        logger.info("Instantiating ToolExecutor..."); self.tool_executor = ToolExecutor(); logger.info("ToolExecutor instantiated.")
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml(); logger.info("Generated XML tool descriptions for prompts.")
        logger.info("Instantiating AgentStateManager..."); self.state_manager = AgentStateManager(self); logger.info("AgentStateManager instantiated.")
        logger.info("Instantiating SessionManager..."); self.session_manager = SessionManager(self, self.state_manager); logger.info("SessionManager instantiated.")
        logger.info("Instantiating AgentInteractionHandler..."); self.interaction_handler = AgentInteractionHandler(self); logger.info("AgentInteractionHandler instantiated.")
        # Pass self (AgentManager) to CycleHandler constructor
        logger.info("Instantiating AgentCycleHandler..."); self.cycle_handler = AgentCycleHandler(self, self.interaction_handler); logger.info("AgentCycleHandler instantiated.")
        logger.info("Instantiating ModelPerformanceTracker..."); self.performance_tracker = ModelPerformanceTracker(); logger.info("ModelPerformanceTracker instantiated and metrics loaded.")
        self._ensure_projects_dir()
        logger.info("AgentManager initialized synchronously. Bootstrap agents and model discovery run asynchronously.")

    def _ensure_projects_dir(self):
        try: settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True); logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e: logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)

    async def initialize_bootstrap_agents(self):
        # (No changes needed in this method for current step)
        logger.info("Initializing bootstrap agents asynchronously...")
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list: logger.warning("No bootstrap agent configurations found."); return
        main_sandbox_dir = BASE_DIR / "sandboxes"; await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
        tasks = []
        formatted_available_models = model_registry.get_formatted_available_models(); logger.debug("Retrieved formatted available models for Admin AI prompt.")
        all_available_models_flat: List[str] = model_registry.get_available_models_list() # Gets prioritized list
        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id");
            if not agent_id: logger.warning("Skipping bootstrap agent due to missing 'agent_id'."); continue
            agent_config_data = agent_conf_entry.get("config", {}); final_agent_config_data = agent_config_data.copy()
            selected_admin_provider = final_agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER); selected_admin_model = final_agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL); selection_method = "config.yaml"
            if agent_id == BOOTSTRAP_AGENT_ID:
                logger.info(f"Processing Admin AI ({BOOTSTRAP_AGENT_ID}) configuration..."); config_provider = final_agent_config_data.get("provider"); config_model = final_agent_config_data.get("model"); use_config_value = False
                if config_provider and config_model:
                    logger.info(f"Admin AI defined in config.yaml: {config_provider}/{config_model}")
                    if not settings.is_provider_configured(config_provider): logger.warning(f"Provider '{config_provider}' specified for Admin AI in config is not configured in .env. Ignoring.")
                    # Check using full model ID format for local providers
                    full_model_id_check = f"{config_provider}/{config_model}" if config_provider in ["ollama", "litellm"] else config_model
                    if not model_registry.is_model_available(config_provider, config_model): logger.warning(f"Model '{full_model_id_check}' specified for Admin AI in config is not available via registry. Ignoring.")
                    else: logger.info(f"Using Admin AI provider/model specified in config.yaml: {config_provider}/{config_model}"); use_config_value = True
                else: logger.info("Admin AI provider/model not fully specified in config.yaml. Attempting automatic selection...")
                if not use_config_value:
                    selection_method = "automatic"; selected_admin_provider = None; selected_admin_model = None
                    logger.info(f"Attempting automatic Admin AI model selection. Preferred patterns: {PREFERRED_ADMIN_MODELS}"); logger.debug(f"Full available models list (prioritized): {all_available_models_flat}")
                    # --- TODO: Integrate performance ranking into selection ---
                    for pattern in PREFERRED_ADMIN_MODELS:
                        found_match = False
                        # Iterate through the prioritized flat list
                        for model_id_full_or_suffix in all_available_models_flat:
                            provider_guess = model_registry.find_provider_for_model(model_id_full_or_suffix)
                            if provider_guess:
                                # Use the full ID (provider/model) for local, suffix for remote for matching
                                match_candidate = f"{provider_guess}/{model_id_full_or_suffix}" if provider_guess in ["ollama", "litellm"] else model_id_full_or_suffix
                                # Use the simple model ID (suffix) for storage
                                model_id_to_store = model_id_full_or_suffix

                                if fnmatch.fnmatch(match_candidate, pattern):
                                     if settings.is_provider_configured(provider_guess):
                                         selected_admin_provider = provider_guess; selected_admin_model = model_id_to_store
                                         logger.info(f"Auto-selected Admin AI model based on pattern '{pattern}': {selected_admin_provider}/{selected_admin_model}"); found_match = True; break
                                else:
                                     # Fallback check if pattern doesn't contain '/' (e.g., "llama3*")
                                     # and model ID is local (contains '/')
                                     if '/' not in pattern and '/' in match_candidate:
                                          _, model_suffix = match_candidate.split('/', 1)
                                          if fnmatch.fnmatch(model_suffix, pattern):
                                               if settings.is_provider_configured(provider_guess):
                                                    selected_admin_provider = provider_guess; selected_admin_model = model_id_to_store
                                                    logger.info(f"Auto-selected Admin AI model based on pattern '{pattern}' (suffix match): {selected_admin_provider}/{selected_admin_model}"); found_match = True; break


                        if found_match: break
                    if not selected_admin_model: logger.error("Could not automatically select any available/configured model for Admin AI! Check .env configurations and model discovery logs."); continue
                    final_agent_config_data["provider"] = selected_admin_provider; final_agent_config_data["model"] = selected_admin_model
            final_provider = final_agent_config_data.get("provider")
            if not settings.is_provider_configured(final_provider): logger.error(f"Cannot initialize '{agent_id}': Selected provider '{final_provider}' is not configured in .env. Skipping."); continue
            if agent_id == BOOTSTRAP_AGENT_ID:
                user_defined_prompt = agent_config_data.get("system_prompt", ""); operational_instructions = ADMIN_AI_OPERATIONAL_INSTRUCTIONS.format(tool_descriptions_xml=self.tool_descriptions_xml)
                final_agent_config_data["system_prompt"] = (f"--- Primary Goal/Persona ---\n{user_defined_prompt}\n\n{operational_instructions}\n\n---\n{formatted_available_models}\n---")
                logger.info(f"Assembled final prompt for '{BOOTSTRAP_AGENT_ID}' (using {selection_method} selection: {selected_admin_provider}/{selected_admin_model}) including available model list.")
            else: logger.info(f"Using system prompt from config for bootstrap agent '{agent_id}'.")
            tasks.append(self._create_agent_internal(agent_id_requested=agent_id, agent_config_data=final_agent_config_data, is_bootstrap=True))
        results = await asyncio.gather(*tasks, return_exceptions=True); successful_ids = []
        for i, result in enumerate(results):
             original_agent_id_attempted = agent_configs_list[i].get("agent_id", f"unknown_index_{i}") if i < len(agent_configs_list) and isinstance(agent_configs_list[i], dict) else f"unknown_index_{i}"
             if isinstance(result, tuple) and result[0]:
                 created_agent_id = result[2];
                 if created_agent_id: agent_id_log = created_agent_id; self.bootstrap_agents.append(created_agent_id); successful_ids.append(created_agent_id); logger.info(f"--- Bootstrap agent '{created_agent_id}' initialized. ---")
                 else: logger.error(f"--- Failed bootstrap init '{original_agent_id_attempted}': {result[1]} (Success reported but no ID?) ---")
             elif isinstance(result, Exception): logger.error(f"--- Failed bootstrap init '{original_agent_id_attempted}': {result} ---", exc_info=result)
             else: error_msg = result[1] if isinstance(result, tuple) else str(result); logger.error(f"--- Failed bootstrap init '{original_agent_id_attempted}': {error_msg} ---")
        logger.info(f"Finished bootstrap initialization. Active: {successful_ids}")
        if BOOTSTRAP_AGENT_ID not in self.agents: logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize! Check previous errors.")


    async def _create_agent_internal( self, agent_id_requested: Optional[str], agent_config_data: Dict[str, Any], is_bootstrap: bool = False, team_id: Optional[str] = None, loading_from_session: bool = False ) -> Tuple[bool, str, Optional[str]]:
        # (No changes needed in this method for current step)
        agent_id: Optional[str] = None;
        if agent_id_requested and agent_id_requested in self.agents: msg = f"Agent ID '{agent_id_requested}' already exists."; logger.error(msg); return False, msg, None
        elif agent_id_requested: agent_id = agent_id_requested
        else: agent_id = self._generate_unique_agent_id()
        if not agent_id: return False, "Failed to determine Agent ID.", None
        logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER); model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL); persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)
        if not settings.is_provider_configured(provider_name): msg = f"Provider '{provider_name}' not configured in .env settings."; logger.error(msg); return False, msg, None
        if not is_bootstrap and not loading_from_session:
            # Check availability using the provider and simple model ID
            if not model_registry.is_model_available(provider_name, model):
                 # Format the full ID for logging/error messages
                 full_model_id_check = f"{provider_name}/{model}" if provider_name in ["ollama", "litellm"] else model
                 available_list_str = ", ".join(model_registry.get_available_models_list(provider=provider_name)); available_list_str = available_list_str or "(None discovered/available)"
                 msg = f"Model '{full_model_id_check}' is not available for provider '{provider_name}' based on discovery and tier settings. Available for '{provider_name}': [{available_list_str}]"; logger.error(msg); return False, msg, None
            else: logger.info(f"Dynamic agent model validated via ModelRegistry: '{provider_name}/{model}'.")
        role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT); final_system_prompt = role_specific_prompt
        if not loading_from_session and not is_bootstrap:
             logger.debug(f"Constructing final prompt for dynamic agent '{agent_id}' using prompt_utils...")
             standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(agent_id=agent_id, team_id=team_id or "N/A", tool_descriptions_xml=self.tool_descriptions_xml)
             final_system_prompt = standard_info + "\n\n--- Your Specific Role & Task ---\n" + role_specific_prompt; logger.info(f"Injected standard framework instructions for dynamic agent '{agent_id}'.")
        elif loading_from_session or is_bootstrap: final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt); logger.debug(f"Using provided system prompt for {'loaded' if loading_from_session else 'bootstrap'} agent '{agent_id}'.")
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE); allowed_provider_keys = ['api_key', 'base_url', 'referer']; agent_config_keys_to_exclude = ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'project_name', 'session_name'] + allowed_provider_keys
        provider_specific_kwargs = {k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude}; base_provider_config = settings.get_provider_config(provider_name)
        final_provider_args = {**base_provider_config, **provider_specific_kwargs}; final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        final_agent_config_entry = {"agent_id": agent_id, "config": {"provider": provider_name, "model": model, "system_prompt": final_system_prompt, "persona": persona, "temperature": temperature, **provider_specific_kwargs}}
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass:
            if provider_name == "litellm": msg = f"LiteLLM provider support is not yet fully implemented."; logger.error(msg); return False, msg, None
            msg = f"Unknown provider type '{provider_name}' specified in config or PROVIDER_CLASS_MAP."; logger.error(msg); return False, msg, None
        try: llm_provider_instance = ProviderClass(**final_provider_args)
        except Exception as e: msg = f"Provider instantiation failed for {provider_name}: {e}"; logger.error(msg, exc_info=True); return False, msg, None
        logger.info(f"  Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")
        try: agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=self)
        except Exception as e: msg = f"Agent instantiation failed: {e}"; logger.error(msg, exc_info=True); await self._close_provider_safe(llm_provider_instance); return False, msg, None
        logger.info(f"  Instantiated Agent object for '{agent_id}'.")
        try: await asyncio.to_thread(agent.ensure_sandbox_exists)
        except Exception as e: logger.error(f"  Error ensuring sandbox for '{agent_id}': {e}", exc_info=True)
        self.agents[agent_id] = agent; logger.debug(f"Agent '{agent_id}' added to self.agents dictionary.")
        team_add_msg_suffix = ""
        if team_id:
            team_add_success, team_add_msg = await self.state_manager.add_agent_to_team(agent_id, team_id)
            if team_add_success: logger.info(f"Agent '{agent_id}' state added to team '{team_id}'.")
            else: team_add_msg_suffix = f" (Warning adding to team state: {team_add_msg})"; logger.warning(f"Agent '{agent_id}': {team_add_msg_suffix}")
        message = f"Agent '{agent_id}' ({persona}) created successfully." + team_add_msg_suffix
        return True, message, agent_id


    async def create_agent_instance( self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs ) -> Tuple[bool, str, Optional[str]]:
        # (No changes needed in this method for current step)
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
        # (No changes needed in this method for current step)
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
        # (No changes needed in this method for current step)
        timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4];
        while True:
            new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_");
            if new_id not in self.agents:
                return new_id
            time.sleep(0.001); timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]


    async def schedule_cycle(self, agent: Agent, retry_count: int = 0):
        """Schedules the agent's execution cycle via the AgentCycleHandler."""
        if not agent: logger.error("Schedule cycle called with invalid Agent object."); return
        logger.debug(f"Manager: Scheduling cycle for agent '{agent.agent_id}' (Retry: {retry_count}).")
        # Pass the retry count to the cycle handler
        asyncio.create_task(self.cycle_handler.run_cycle(agent, retry_count))


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        # (No changes needed in this method for current step)
        logger.info(f"Manager: Received user message for Admin AI: '{message[:100]}...'");
        if self.current_project is None:
            logger.info("Manager: No active project/session context found. Creating default context...")
            default_project = DEFAULT_PROJECT_NAME; default_session = time.strftime("%Y%m%d_%H%M%S")
            success = False; save_msg = "Initialization error"
            try:
                success, save_msg = await self.save_session(default_project, default_session)
                if success: logger.info(f"Manager: Auto-created session: '{default_project}/{default_session}'")
                else: logger.error(f"Manager: Failed to auto-save default session: {save_msg}")
            except Exception as e: logger.error(f"Manager: Error during default session auto-save: {e}", exc_info=True); save_msg = f"Error during auto-save: {e}"
            await self.send_to_ui({"type": "system_event", "event": "session_saved", "project": default_project, "session": default_session, "message": f"Context set to default: {default_project}/{default_session}" if success else f"Failed to create default context: {save_msg}"})

        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID);
        if not admin_agent: logger.error(f"Manager: Admin AI ('{BOOTSTRAP_AGENT_ID}') not found. Cannot process message."); await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."}); return;

        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Manager: Delegating message to '{BOOTSTRAP_AGENT_ID}' and scheduling cycle.")
            admin_agent.message_history.append({"role": "user", "content": message}); await self.schedule_cycle(admin_agent, 0); # Start with retry_count 0
        else: # Busy or Error
            logger.info(f"Manager: Admin AI busy ({admin_agent.status}). Message queued."); admin_agent.message_history.append({"role": "user", "content": message}); await self.push_agent_status_update(admin_agent.agent_id); await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Queued." })


    async def handle_agent_model_failover(self, agent_id: str, last_error: str):
        """
        Attempts failover after retries have failed or a fatal error occurred.
        Selects the next best model, prioritizing local providers.
        Sets agent to ERROR if no alternatives work or failover limit reached.
        """
        agent = self.agents.get(agent_id)
        if not agent: logger.error(f"Failover Error: Agent '{agent_id}' not found."); return

        logger.warning(f"Agent '{agent_id}' failover process initiated due to error: {last_error}")
        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Attempting automatic failover due to error..."})

        # Retrieve the set of models already failed in this sequence
        failed_models_this_cycle = getattr(agent, '_failed_models_this_cycle', set())
        original_provider = agent.provider_name; original_model = agent.model
        original_model_key = f"{original_provider}/{original_model}" # Used for logging

        # Check if failover limit reached for this sequence
        if len(failed_models_this_cycle) >= MAX_FAILOVER_ATTEMPTS:
            fail_reason = f"[Failover Limit Reached after {len(failed_models_this_cycle)} models tried] Last error on {original_model_key}: {last_error}"
            logger.error(f"Agent '{agent_id}': Max failover attempts ({MAX_FAILOVER_ATTEMPTS}) reached for this task sequence. Setting to ERROR.")
            agent.set_status(AGENT_STATUS_ERROR)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
            # Clear the set here, as this task sequence is definitively over for this agent
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
            return

        # Select the next model, prioritizing local, excluding already failed
        next_provider, next_model = self._select_next_failover_model(agent, failed_models_this_cycle)

        if next_provider and next_model:
            next_model_key = f"{next_provider}/{next_model}"
            logger.info(f"Agent '{agent_id}': Failing over from '{original_model_key}' to model: {next_model_key}")
            await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Switching to {next_provider}/{next_model}"})

            old_provider_instance = agent.llm_provider
            try:
                # --- Update Agent State ---
                agent.provider_name = next_provider
                agent.model = next_model
                # Update the config stored on the agent instance if possible
                if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config:
                    if isinstance(agent.agent_config["config"], dict):
                        agent.agent_config["config"].update({"provider": next_provider, "model": next_model})
                    else:
                         logger.warning(f"Agent '{agent_id}': Cannot update agent_config, 'config' key is not a dictionary.")
                # --- End Update Agent State ---

                # --- Re-instantiate Provider ---
                # Get base config (potentially including API key/URL from settings)
                # TODO: Integrate ProviderKeyManager here in Phase 2 to get the active key config
                base_provider_config = settings.get_provider_config(next_provider)
                # Get any extra kwargs from the original agent config (excluding standard ones)
                provider_kwargs = {k: v for k, v in agent.agent_config.get("config", {}).items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
                # Merge, prioritizing kwargs from agent config over base settings (except essentials like api_key/base_url)
                final_provider_args = {**base_provider_config, **provider_kwargs}
                final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None} # Clean None values

                NewProviderClass = PROVIDER_CLASS_MAP.get(next_provider)
                if not NewProviderClass: raise ValueError(f"Provider class not found for {next_provider}")

                new_provider_instance = NewProviderClass(**final_provider_args)
                agent.llm_provider = new_provider_instance
                await self._close_provider_safe(old_provider_instance)
                # --- End Re-instantiate Provider ---

                # Set agent back to idle and schedule with the new model (reset retry count)
                agent.set_status(AGENT_STATUS_IDLE)
                await self.schedule_cycle(agent, 0) # Start with retry_count 0 for the new model
                logger.info(f"Agent '{agent_id}' failover successful to {next_model_key}. Rescheduled cycle.")

            except Exception as failover_err:
                # If switching the provider/model itself fails
                fail_reason = f"[Failover attempt failed during switch to {next_provider}/{next_model}: {failover_err}] Last operational error: {last_error}"
                logger.error(f"Agent '{agent_id}': Error during failover switch to {next_model_key}: {failover_err}", exc_info=True)
                # Add the model we *tried* to switch to, to the failed set as well
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.add(next_model_key)
                # Set to ERROR state as the failover mechanism itself failed
                logger.error(f"Agent '{agent_id}': Failover switch failed. Setting agent to permanent ERROR state for this task sequence.")
                agent.set_status(AGENT_STATUS_ERROR)
                await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
                # Clear the failover set as this sequence is over
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
        else:
            # No alternative model found by the selection logic
            fail_reason = f"[No alternative models found after {len(failed_models_this_cycle)} failover attempts] Last error on {original_model_key}: {last_error}"
            logger.error(f"Agent '{agent_id}': No alternative models available to failover to after trying {len(failed_models_this_cycle)} model(s). Setting agent to permanent ERROR state.")
            agent.set_status(AGENT_STATUS_ERROR)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
            # Clear the failover set as this sequence is over
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()

    # --- Renamed and updated selection logic ---
    def _select_next_failover_model(self, agent: Agent, already_failed: Set[str]) -> Tuple[Optional[str], Optional[str]]:
        """
        Selects the next available model for failover, prioritizing local providers.
        Skips models already failed in this sequence and the current model.

        Args:
            agent (Agent): The agent needing failover.
            already_failed (Set[str]): Set of 'provider/model' keys already failed in this sequence.

        Returns:
            Tuple[Optional[str], Optional[str]]: (provider_name, model_id) or (None, None)
        """
        logger.debug(f"Selecting next failover model for agent '{agent.agent_id}'. Current: {agent.provider_name}/{agent.model}. Already failed this sequence: {already_failed}")

        available_models_dict = model_registry.get_available_models_dict()
        current_model_tier = settings.MODEL_TIER # e.g., "ALL", "FREE"

        # --- 1. Try Local Providers First ---
        local_providers = ["ollama", "litellm"]
        logger.debug(f"Checking local providers first: {local_providers}")
        for provider in local_providers:
            if provider in model_registry._reachable_providers and provider in available_models_dict:
                models_list = available_models_dict.get(provider, [])
                sorted_model_ids = sorted([m.get('id') for m in models_list if m.get('id')])
                for model_id in sorted_model_ids:
                    failover_key = f"{provider}/{model_id}"
                    # Check if this model has already failed in this sequence
                    if failover_key not in already_failed:
                        logger.info(f"Next failover model selected (Local): {provider}/{model_id}")
                        return provider, model_id
                    # else: logger.debug(f"Skipping already failed local model: {failover_key}")
            # else: logger.debug(f"Local provider '{provider}' not reachable or has no available models.")

        # --- 2. Try External Providers (Respecting Tier) ---
        external_providers = ["openrouter", "openai"] # Add others if needed
        # Separate into Free/Paid tiers based on ID for OpenRouter, assume OpenAI is Paid
        free_models: List[Tuple[str, str]] = []
        paid_models: List[Tuple[str, str]] = []

        for provider in external_providers:
             if provider in model_registry._reachable_providers and provider in available_models_dict:
                 models_list = available_models_dict.get(provider, [])
                 for model_info in models_list:
                     model_id = model_info.get("id")
                     if not model_id: continue
                     failover_key = f"{provider}/{model_id}" # Use provider/model for external key too for consistency
                     # Check if failed already
                     if failover_key in already_failed:
                          # logger.debug(f"Skipping already failed external model: {failover_key}")
                          continue
                     # Tier check
                     is_free = ":free" in model_id.lower() if provider == "openrouter" else False
                     if is_free:
                          free_models.append((provider, model_id))
                     else:
                          paid_models.append((provider, model_id))

        # Sort models alphabetically within tiers for deterministic selection
        free_models.sort(key=lambda x: x[1])
        paid_models.sort(key=lambda x: x[1])

        logger.debug(f"Checking external providers. Free models found: {len(free_models)}. Paid models found: {len(paid_models)}. Tier setting: {current_model_tier}")

        # Try Free models first if tier allows
        if current_model_tier != "PAID_ONLY": # Assuming no such tier yet, but for future
            logger.debug("Checking available Free external models...")
            for provider, model_id in free_models:
                 logger.info(f"Next failover model selected (External Free): {provider}/{model_id}")
                 return provider, model_id

        # Try Paid models if tier allows
        if current_model_tier != "FREE":
            logger.debug("Checking available Paid external models...")
            for provider, model_id in paid_models:
                logger.info(f"Next failover model selected (External Paid): {provider}/{model_id}")
                return provider, model_id

        # If no model found in any allowed tier
        logger.warning(f"Could not find any suitable alternative model (Local or External) for failover for agent '{agent.agent_id}' that hasn't already failed ({already_failed}).")
        return None, None

    # --- Helper Methods (Remain the same) ---
    async def push_agent_status_update(self, agent_id: str):
        agent = self.agents.get(agent_id);
        if agent: state = agent.get_state(); state["team"] = self.state_manager.get_agent_team(agent_id);
        else: state = {"status": "deleted", "team": None}; logger.warning(f"Cannot push status update for unknown/deleted agent: {agent_id}");
        await self.send_to_ui({"type": "agent_status_update", "agent_id": agent_id, "status": state})

    async def send_to_ui(self, message_data: Dict[str, Any]):
        if not self.send_to_ui_func: logger.warning("UI broadcast func not set."); return;
        try: await self.send_to_ui_func(json.dumps(message_data));
        except Exception as e: logger.error(f"Error sending to UI: {e}. Data: {message_data}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        return {aid: (ag.get_state() | {"team": self.state_manager.get_agent_team(aid)}) for aid, ag in self.agents.items()}

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        logger.info(f"Manager: Delegating save_session for '{project_name}'...")
        return await self.session_manager.save_session(project_name, session_name)

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        logger.info(f"Manager: Delegating load_session for '{project_name}/{session_name}'...")
        return await self.session_manager.load_session(project_name, session_name)

    async def cleanup_providers(self):
        logger.info("Manager: Cleaning up LLM providers and saving metrics...");
        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        provider_tasks = [asyncio.create_task(self._close_provider_safe(p)) for p in active_providers if hasattr(p, 'close_session')]
        metrics_save_task = asyncio.create_task(self.performance_tracker.save_metrics())
        all_cleanup_tasks = provider_tasks + [metrics_save_task]
        if all_cleanup_tasks: await asyncio.gather(*all_cleanup_tasks); logger.info("Manager: Provider cleanup and metrics saving complete.")
        else: logger.info("Manager: No provider cleanup or metrics saving needed.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        try:
             if hasattr(provider, 'close_session') and callable(provider.close_session): await provider.close_session(); logger.info(f"Manager: Closed session for {provider!r}")
             else: logger.debug(f"Manager: Provider {provider!r} does not have a close_session method.")
        except Exception as e: logger.error(f"Manager: Error closing session for {provider!r}: {e}", exc_info=True)

    def get_agent_info_list_sync(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        info_list = [];
        for agent_id, agent in self.agents.items():
             current_team = self.state_manager.get_agent_team(agent_id);
             if filter_team_id is not None and current_team != filter_team_id: continue;
             state = agent.get_state(); info = {"agent_id": agent_id, "persona": state.get("persona"), "provider": state.get("provider"), "model": state.get("model"), "status": state.get("status"), "team": current_team}; info_list.append(info);
        return info_list
