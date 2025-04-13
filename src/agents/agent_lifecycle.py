# START OF FILE src/agents/agent_lifecycle.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple
import logging
import uuid
import time
import fnmatch

# Import necessary components from other modules
from src.agents.core import Agent
from src.llm_providers.base import BaseLLMProvider
# Import settings and model_registry, BASE_DIR
from src.config.settings import settings, model_registry, BASE_DIR

# --- REMOVED Prompt Imports ---
# Prompts are now accessed via settings.PROMPTS
# from src.agents.prompt_utils import (
#     STANDARD_FRAMEWORK_INSTRUCTIONS,
#     ADMIN_AI_OPERATIONAL_INSTRUCTIONS
# )
# --- END REMOVAL ---

# Import PROVIDER_CLASS_MAP and BOOTSTRAP_AGENT_ID from the refactored manager
# Avoid circular import using TYPE_CHECKING
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

# Import provider classes directly if needed (less ideal but works)
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

logger = logging.getLogger(__name__)

# Re-define PROVIDER_CLASS_MAP here or import from manager if structure allows
PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    # TODO: Add LiteLLMProvider when implemented
}
# Define BOOTSTRAP_AGENT_ID here or import
BOOTSTRAP_AGENT_ID = "admin_ai"
# Define PREFERRED_ADMIN_MODELS here or import
PREFERRED_ADMIN_MODELS = [
    "ollama/llama3*", "litellm/llama3*",
    "anthropic/claude-3-opus*", "openai/gpt-4o*", "google/gemini-2.5-pro*",
    "llama3:70b*", "command-r-plus*", "qwen/qwen2-72b-instruct*",
    "anthropic/claude-3-sonnet*", "google/gemini-pro*", "llama3*",
    "mistralai/mixtral-8x7b*", "mistralai/mistral-large*", "*wizardlm2*",
    "*deepseek-coder*", "google/gemini-flash*", "*"
]


async def initialize_bootstrap_agents(manager: 'AgentManager'):
    """ Initializes bootstrap agents defined in settings. """
    logger.info("Lifecycle: Initializing bootstrap agents...")
    agent_configs_list = settings.AGENT_CONFIGURATIONS
    if not agent_configs_list:
        logger.warning("Lifecycle: No bootstrap agent configurations found.")
        return

    # Ensure main sandbox directory exists
    main_sandbox_dir = BASE_DIR / "sandboxes"
    try:
        await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Lifecycle: Failed to create main sandboxes directory {main_sandbox_dir}: {e}")
        # Decide if this is fatal or if agents can run without sandboxes initially
        # For now, log the error and continue

    tasks = []
    formatted_available_models = model_registry.get_formatted_available_models()
    logger.debug("Lifecycle: Retrieved formatted available models for Admin AI prompt.")
    all_available_models_flat: List[str] = model_registry.get_available_models_list()

    for agent_conf_entry in agent_configs_list:
        agent_id = agent_conf_entry.get("agent_id")
        if not agent_id:
            logger.warning("Lifecycle: Skipping bootstrap agent due to missing 'agent_id'.")
            continue

        agent_config_data = agent_conf_entry.get("config", {})
        final_agent_config_data = agent_config_data.copy()
        selected_admin_provider = final_agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        selected_admin_model = final_agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        selection_method = "config.yaml"

        # --- Admin AI Auto-Selection Logic ---
        if agent_id == BOOTSTRAP_AGENT_ID:
            logger.info(f"Lifecycle: Processing Admin AI ({BOOTSTRAP_AGENT_ID}) configuration...")
            config_provider = final_agent_config_data.get("provider")
            config_model = final_agent_config_data.get("model")
            use_config_value = False

            if config_provider and config_model:
                logger.info(f"Lifecycle: Admin AI defined in config.yaml: {config_provider}/{config_model}")
                if not settings.is_provider_configured(config_provider):
                    logger.warning(f"Lifecycle: Provider '{config_provider}' specified for Admin AI in config is not configured in .env. Ignoring.")
                elif not model_registry.is_model_available(config_provider, config_model):
                    full_model_id_check = f"{config_provider}/{config_model}" if config_provider in ["ollama", "litellm"] else config_model
                    logger.warning(f"Lifecycle: Model '{full_model_id_check}' specified for Admin AI in config is not available via registry. Ignoring.")
                else:
                    logger.info(f"Lifecycle: Using Admin AI provider/model specified in config.yaml: {config_provider}/{config_model}")
                    use_config_value = True
            else:
                logger.info("Lifecycle: Admin AI provider/model not fully specified in config.yaml. Attempting automatic selection...")

            if not use_config_value:
                selection_method = "automatic"
                selected_admin_provider = None
                selected_admin_model = None
                logger.info(f"Lifecycle: Attempting automatic Admin AI model selection. Preferred patterns: {PREFERRED_ADMIN_MODELS}")
                logger.debug(f"Lifecycle: Full available models list (prioritized): {all_available_models_flat}")

                for pattern in PREFERRED_ADMIN_MODELS:
                    found_match = False
                    for model_id_full_or_suffix in all_available_models_flat:
                        provider_guess = model_registry.find_provider_for_model(model_id_full_or_suffix)
                        if provider_guess:
                            match_candidate = f"{provider_guess}/{model_id_full_or_suffix}" if provider_guess in ["ollama", "litellm"] else model_id_full_or_suffix
                            model_id_to_store = model_id_full_or_suffix

                            if not settings.is_provider_configured(provider_guess): continue
                            if provider_guess not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(provider_guess): continue

                            if fnmatch.fnmatch(match_candidate, pattern):
                                 selected_admin_provider = provider_guess; selected_admin_model = model_id_to_store
                                 logger.info(f"Lifecycle: Auto-selected Admin AI model based on pattern '{pattern}': {selected_admin_provider}/{selected_admin_model}"); found_match = True; break
                            elif '/' not in pattern and '/' in match_candidate:
                                 _, model_suffix = match_candidate.split('/', 1)
                                 if fnmatch.fnmatch(model_suffix, pattern):
                                      selected_admin_provider = provider_guess; selected_admin_model = model_id_to_store
                                      logger.info(f"Lifecycle: Auto-selected Admin AI model based on pattern '{pattern}' (suffix match): {selected_admin_provider}/{selected_admin_model}"); found_match = True; break
                    if found_match: break

                if not selected_admin_model:
                    logger.error("Lifecycle: Could not automatically select any available/configured/non-depleted model for Admin AI! Check .env configurations and model discovery logs.")
                    continue
                final_agent_config_data["provider"] = selected_admin_provider
                final_agent_config_data["model"] = selected_admin_model
        # --- End Admin AI Auto-Selection Logic ---

        # --- Final Provider Checks ---
        final_provider = final_agent_config_data.get("provider")
        if not final_provider or not settings.is_provider_configured(final_provider):
            logger.error(f"Lifecycle: Cannot initialize '{agent_id}': Final provider '{final_provider}' is not configured in .env. Skipping.")
            continue
        if final_provider not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(final_provider):
            logger.error(f"Lifecycle: Cannot initialize '{agent_id}': All keys for selected provider '{final_provider}' are quarantined. Skipping.")
            continue
        # --- End Final Provider Checks ---

        # Inject Admin AI operational instructions
        if agent_id == BOOTSTRAP_AGENT_ID:
            # Get the base persona/goal from the config.yaml
            user_defined_prompt = agent_config_data.get("system_prompt", "")
            # --- Retrieve operational instructions from settings ---
            admin_ops_template = settings.PROMPTS.get("admin_ai_operational_instructions", "--- Admin Ops Instructions Missing ---")
            operational_instructions = admin_ops_template.format(tool_descriptions_xml=manager.tool_descriptions_xml)
            # --- End Retrieval ---
            final_agent_config_data["system_prompt"] = (
                f"--- Primary Goal/Persona ---\n{user_defined_prompt}\n\n"
                f"{operational_instructions}\n\n"
                f"---\n{formatted_available_models}\n---"
            )
            logger.info(f"Lifecycle: Assembled final prompt for '{BOOTSTRAP_AGENT_ID}' (using {selection_method} selection: {selected_admin_provider}/{selected_admin_model}) including available model list.")
        else:
            # For other bootstrap agents, just use the prompt as defined in config.yaml
            logger.info(f"Lifecycle: Using system prompt from config for bootstrap agent '{agent_id}'.")

        # Schedule agent creation task
        tasks.append(_create_agent_internal(manager, agent_id_requested=agent_id, agent_config_data=final_agent_config_data, is_bootstrap=True))

    # Gather results
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successful_ids = []
    for i, result in enumerate(results):
        try:
             original_agent_id_attempted = agent_configs_list[i].get("agent_id", f"unknown_index_{i}") if i < len(agent_configs_list) and isinstance(agent_configs_list[i], dict) else f"unknown_index_{i}"
             if isinstance(result, tuple) and result[0]:
                 created_agent_id = result[2]
                 if created_agent_id:
                     manager.bootstrap_agents.append(created_agent_id)
                     successful_ids.append(created_agent_id)
                     logger.info(f"--- Lifecycle: Bootstrap agent '{created_agent_id}' initialized. ---")
                 else:
                     logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result[1]} (Success reported but no ID?) ---")
             elif isinstance(result, Exception):
                 logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result} ---", exc_info=result)
             else:
                 error_msg = result[1] if isinstance(result, tuple) else str(result)
                 logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {error_msg} ---")
        except IndexError:
             logger.error(f"Lifecycle: Error matching result to original agent config at index {i}.")
        except Exception as gather_err:
             logger.error(f"Lifecycle: Unexpected error processing bootstrap results: {gather_err}", exc_info=True)

    logger.info(f"Lifecycle: Finished bootstrap initialization. Active: {successful_ids}")
    if BOOTSTRAP_AGENT_ID not in manager.agents:
        logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize! Check previous errors.")


async def _create_agent_internal( manager: 'AgentManager', agent_id_requested: Optional[str], agent_config_data: Dict[str, Any], is_bootstrap: bool = False, team_id: Optional[str] = None, loading_from_session: bool = False ) -> Tuple[bool, str, Optional[str]]:
    """ Internal method for creating agent instances, now uses ProviderKeyManager for remote providers. """
    agent_id: Optional[str] = None;
    if agent_id_requested and agent_id_requested in manager.agents:
        msg = f"Lifecycle: Agent ID '{agent_id_requested}' already exists."
        logger.error(msg)
        return False, msg, None
    elif agent_id_requested:
        agent_id = agent_id_requested
    else:
        agent_id = _generate_unique_agent_id(manager) # Pass manager

    if not agent_id:
        return False, "Lifecycle: Failed to determine Agent ID.", None

    logger.debug(f"Lifecycle: Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")

    provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
    model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
    persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)

    # Validate provider configuration and model availability
    if not settings.is_provider_configured(provider_name):
        msg = f"Lifecycle: Provider '{provider_name}' not configured in .env settings."
        logger.error(msg)
        return False, msg, None
    if provider_name not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(provider_name):
        msg = f"Lifecycle: Cannot create agent with provider '{provider_name}': All API keys are currently quarantined."
        logger.error(msg)
        return False, msg, None
    if not is_bootstrap and not loading_from_session:
        if not model_registry.is_model_available(provider_name, model):
            full_model_id_check = f"{provider_name}/{model}" if provider_name in ["ollama", "litellm"] else model
            available_list_str = ", ".join(model_registry.get_available_models_list(provider=provider_name))
            available_list_str = available_list_str or "(None discovered/available)"
            msg = f"Lifecycle: Model '{full_model_id_check}' is not available for provider '{provider_name}'. Available: [{available_list_str}]"
            logger.error(msg)
            return False, msg, None
        else:
            logger.info(f"Lifecycle: Dynamic agent model validated via ModelRegistry: '{provider_name}/{model}'.")

    # Assemble System Prompt
    role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
    final_system_prompt = role_specific_prompt
    if not loading_from_session and not is_bootstrap:
        logger.debug(f"Lifecycle: Constructing final prompt for dynamic agent '{agent_id}'...")
        # --- Retrieve standard instructions from settings ---
        standard_info_template = settings.PROMPTS.get("standard_framework_instructions", "--- Standard Instructions Missing ---")
        standard_info = standard_info_template.format(
            agent_id=agent_id,
            team_id=team_id or "N/A",
            tool_descriptions_xml=manager.tool_descriptions_xml
        )
        # --- End Retrieval ---
        final_system_prompt = standard_info + "\n\n--- Your Specific Role & Task ---\n" + role_specific_prompt
        logger.info(f"Lifecycle: Injected standard framework instructions for dynamic agent '{agent_id}'.")
    elif loading_from_session or is_bootstrap:
        # Use the prompt directly as loaded/provided for bootstrap or session load
        # It already includes operational instructions for Admin AI, or was loaded with context for dynamic agents
        final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt)
        logger.debug(f"Lifecycle: Using provided system prompt for {'loaded' if loading_from_session else 'bootstrap'} agent '{agent_id}'.")

    # Prepare Provider Arguments using ProviderKeyManager
    final_provider_args: Optional[Dict[str, Any]] = None
    if provider_name in ["ollama", "litellm"]: # Local providers
        final_provider_args = settings.get_provider_config(provider_name)
    else: # Remote providers - get config with active key
        final_provider_args = await manager.key_manager.get_active_key_config(provider_name)
        if final_provider_args is None:
            msg = f"Lifecycle: Failed to get active API key configuration for provider '{provider_name}'. All keys might be quarantined."
            logger.error(msg)
            return False, msg, None

    # Add agent-specific kwargs
    temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
    allowed_provider_keys = ['api_key', 'base_url', 'referer']
    agent_config_keys_to_exclude = ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'project_name', 'session_name'] + allowed_provider_keys
    provider_specific_kwargs = {k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude}
    final_provider_args = {**final_provider_args, **provider_specific_kwargs}
    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

    # Create final agent config entry for storage/state
    # CRITICAL: Ensure final_system_prompt is stored here
    final_agent_config_entry = {
        "agent_id": agent_id,
        "config": {
            "provider": provider_name,
            "model": model,
            "system_prompt": final_system_prompt, # Store the assembled prompt
            "persona": persona,
            "temperature": temperature,
            **provider_specific_kwargs
        }
    }

    # Instantiate Provider
    ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
    if not ProviderClass:
        if provider_name == "litellm": msg = f"Lifecycle: LiteLLM provider support not yet fully implemented."; logger.error(msg); return False, msg, None
        msg = f"Lifecycle: Unknown provider type '{provider_name}' specified."; logger.error(msg); return False, msg, None
    try:
        llm_provider_instance = ProviderClass(**final_provider_args)
    except Exception as e:
        msg = f"Lifecycle: Provider instantiation failed for {provider_name} with args {final_provider_args}: {e}"
        logger.error(msg, exc_info=True)
        return False, msg, None
    logger.info(f"  Lifecycle: Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")

    # Instantiate Agent
    try:
        # Agent.__init__ uses the 'system_prompt' from the config passed
        agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=manager)
    except Exception as e:
        msg = f"Lifecycle: Agent instantiation failed: {e}"
        logger.error(msg, exc_info=True)
        await manager._close_provider_safe(llm_provider_instance)
        return False, msg, None
    logger.info(f"  Lifecycle: Instantiated Agent object for '{agent_id}'.")

    # Final steps
    try:
        await asyncio.to_thread(agent.ensure_sandbox_exists)
    except Exception as e:
        logger.error(f"  Lifecycle: Error ensuring sandbox for '{agent_id}': {e}", exc_info=True)
    manager.agents[agent_id] = agent # Add to manager's agent dict
    logger.debug(f"Lifecycle: Agent '{agent_id}' added to manager.agents dictionary.")

    team_add_msg_suffix = ""
    if team_id:
        team_add_success, team_add_msg = await manager.state_manager.add_agent_to_team(agent_id, team_id)
        if team_add_success:
            logger.info(f"Lifecycle: Agent '{agent_id}' state added to team '{team_id}'.")
        else:
            team_add_msg_suffix = f" (Warning adding to team state: {team_add_msg})"
            logger.warning(f"Lifecycle: Agent '{agent_id}': {team_add_msg_suffix}")

    message = f"Agent '{agent_id}' ({persona}) created successfully." + team_add_msg_suffix
    return True, message, agent_id


async def create_agent_instance( manager: 'AgentManager', agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs ) -> Tuple[bool, str, Optional[str]]:
    """ Creates a dynamic agent instance. """
    if not all([provider, model, system_prompt, persona]):
        return False, "Lifecycle Error: Missing required args for create_agent_instance.", None

    agent_config_data = {
        "provider": provider, "model": model, "system_prompt": system_prompt, "persona": persona
    }
    if temperature is not None: agent_config_data["temperature"] = temperature

    known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature', 'project_name', 'session_name']
    extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args}
    agent_config_data.update(extra_kwargs)

    success, message, created_agent_id = await _create_agent_internal(
        manager,
        agent_id_requested=agent_id_requested,
        agent_config_data=agent_config_data,
        is_bootstrap=False,
        team_id=team_id,
        loading_from_session=False
    )

    if success and created_agent_id:
        agent = manager.agents.get(created_agent_id)
        team = manager.state_manager.get_agent_team(created_agent_id)
        config_ui = agent.agent_config.get("config", {}) if agent else {}
        await manager.send_to_ui({
            "type": "agent_added", "agent_id": created_agent_id, "config": config_ui, "team": team
        })
        await manager.push_agent_status_update(created_agent_id)

    return success, message, created_agent_id


async def delete_agent_instance(manager: 'AgentManager', agent_id: str) -> Tuple[bool, str]:
    """ Deletes a dynamic agent instance. """
    if not agent_id:
        return False, "Lifecycle Error: Agent ID empty."
    if agent_id not in manager.agents:
        return False, f"Lifecycle Error: Agent '{agent_id}' not found."
    if agent_id in manager.bootstrap_agents:
        return False, f"Lifecycle Error: Cannot delete bootstrap agent '{agent_id}'."

    agent_instance = manager.agents.pop(agent_id)
    manager.state_manager.remove_agent_from_all_teams_state(agent_id)
    await manager._close_provider_safe(agent_instance.llm_provider)
    message = f"Agent '{agent_id}' deleted."
    logger.info(f"Lifecycle: {message}")
    await manager.send_to_ui({"type": "agent_deleted", "agent_id": agent_id})
    return True, message


def _generate_unique_agent_id(manager: 'AgentManager', prefix="agent") -> str:
    """ Generates a unique agent ID. """
    timestamp = int(time.time() * 1000)
    short_uuid = uuid.uuid4().hex[:4]
    while True:
        new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_")
        if new_id not in manager.agents:
            return new_id
        time.sleep(0.001)
        timestamp = int(time.time() * 1000)
        short_uuid = uuid.uuid4().hex[:4]
