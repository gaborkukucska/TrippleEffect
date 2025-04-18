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

# Import PROVIDER_CLASS_MAP and BOOTSTRAP_AGENT_ID from the refactored manager
# Avoid circular import using TYPE_CHECKING
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

# --- Import Specific Provider Classes ---
from src.llm_providers.openai_provider import OpenAIProvider
# *** Import the NEW OllamaProvider that uses the 'openai' library ***
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider
# --- End Provider Imports ---

logger = logging.getLogger(__name__)

# --- Define PROVIDER_CLASS_MAP using imported classes ---
PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider, # <<< Ensure this points to the new OllamaProvider
    "openrouter": OpenRouterProvider,
    # TODO: Add LiteLLMProvider when implemented
    # "litellm": LiteLLMProvider,
}
# --- End PROVIDER_CLASS_MAP Definition ---

# Define BOOTSTRAP_AGENT_ID here or import if moved elsewhere
BOOTSTRAP_AGENT_ID = "admin_ai"
# Define PREFERRED_ADMIN_MODELS here or import if moved elsewhere
PREFERRED_ADMIN_MODELS = [
    "ollama/llama3*", "litellm/llama3*", # Local prioritized
    "anthropic/claude-3.5-sonnet", # Tier 1 Remote (Opus removed as default for cost)
    "openai/gpt-4o",
    "google/gemini-1.5-pro", # Use 1.5 Pro over 2.5 experimental for stability?
    "google/gemini-1.5-flash", # Add Flash as a good free/cheap option
    "mistralai/mistral-large-latest",
    "anthropic/claude-3-haiku",
    "meta-llama/llama-3.1-70b-instruct",
    "meta-llama/llama-3.1-8b-instruct",
    "google/gemma-2-9b-it:free", # Explicitly free tier from OpenRouter
    "mistralai/mistral-7b-instruct:free",
    "*", # Fallback to any available model
]

# --- ADDED: KNOWN_OLLAMA_OPTIONS ---
# Known valid Ollama options (from ollama_provider.py, needed for filtering kwargs)
KNOWN_OLLAMA_OPTIONS = {
    "mirostat", "mirostat_eta", "mirostat_tau", "num_ctx", "num_gpu", "num_thread",
    "num_keep", "seed", "num_predict", "repeat_last_n", "repeat_penalty",
    "temperature", "tfs_z", "top_k", "top_p", "min_p", "use_mmap", "use_mlock",
    "numa", "num_batch", "main_gpu", "low_vram", "f16_kv", "logits_all",
    "vocab_only", "stop", "presence_penalty", "frequency_penalty", "penalize_newline",
    "typical_p"
}
# --- END ADD ---


async def initialize_bootstrap_agents(manager: 'AgentManager'):
    """ Initializes bootstrap agents defined in settings. """
    logger.info("Lifecycle: Initializing bootstrap agents...")
    agent_configs_list = settings.AGENT_CONFIGURATIONS
    if not agent_configs_list:
        logger.warning("Lifecycle: No bootstrap agent configurations found.")
        return

    main_sandbox_dir = BASE_DIR / "sandboxes"
    try:
        await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Lifecycle: Failed to create main sandboxes directory {main_sandbox_dir}: {e}")

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
                    # --- ADDED: Provider/Model mismatch check for config.yaml ---
                    if config_provider in ["ollama", "litellm"] and not config_model.startswith(f"{config_provider}/"):
                         logger.warning(f"Lifecycle: Admin AI model '{config_model}' in config.yaml does not match local provider '{config_provider}'. Ignoring configured values.")
                    elif config_provider not in ["ollama", "litellm"] and '/' in config_model:
                         logger.warning(f"Lifecycle: Admin AI model '{config_model}' in config.yaml looks like a local model, but provider is '{config_provider}'. Ignoring configured values.")
                    else:
                         logger.info(f"Lifecycle: Using Admin AI provider/model specified in config.yaml: {config_provider}/{config_model}")
                         use_config_value = True
                    # --- END ADD ---

            if not use_config_value:
                # --- This block only runs if config.yaml is invalid or not set ---
                logger.info("Lifecycle: Admin AI provider/model not fully specified or invalid in config.yaml. Attempting automatic selection...")
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
                            # Construct the ID to match against the pattern
                            match_candidate = f"{provider_guess}/{model_id_full_or_suffix}" if provider_guess in ["ollama", "litellm"] else model_id_full_or_suffix
                            model_id_to_store = model_id_full_or_suffix # The ID to use if matched

                            # Check if provider is configured and has non-quarantined keys (for remote)
                            if not settings.is_provider_configured(provider_guess): continue
                            if provider_guess not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(provider_guess): continue

                            # Attempt pattern matching
                            if fnmatch.fnmatch(match_candidate, pattern):
                                 selected_admin_provider = provider_guess
                                 selected_admin_model = model_id_to_store
                                 logger.info(f"Lifecycle: Auto-selected Admin AI model based on pattern '{pattern}': {selected_admin_provider}/{selected_admin_model}"); found_match = True; break
                            # Also try matching suffix if pattern doesn't contain '/' but candidate does
                            elif '/' not in pattern and '/' in match_candidate:
                                 _, model_suffix = match_candidate.split('/', 1)
                                 if fnmatch.fnmatch(model_suffix, pattern):
                                      selected_admin_provider = provider_guess
                                      selected_admin_model = model_id_to_store
                                      logger.info(f"Lifecycle: Auto-selected Admin AI model based on pattern '{pattern}' (suffix match): {selected_admin_provider}/{selected_admin_model}"); found_match = True; break
                    if found_match: break

                if not selected_admin_model:
                    logger.error("Lifecycle: Could not automatically select any available/configured/non-depleted model for Admin AI! Check .env configurations and model discovery logs.")
                    continue # Skip creating Admin AI if no suitable model found
                # Update the config dict with the selected provider/model
                final_agent_config_data["provider"] = selected_admin_provider
                final_agent_config_data["model"] = selected_admin_model
        # --- End Admin AI Auto-Selection Logic ---

        # --- Final Provider/Model Checks (These are now critical) ---
        final_provider = final_agent_config_data.get("provider")
        final_model = final_agent_config_data.get("model")
        if not final_provider or not final_model:
            logger.error(f"Lifecycle: Cannot initialize '{agent_id}': Final provider or model is missing after selection. Skipping.")
            continue
        if not settings.is_provider_configured(final_provider):
            logger.error(f"Lifecycle: Cannot initialize '{agent_id}': Final provider '{final_provider}' is not configured in .env. Skipping.")
            continue
        if final_provider not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(final_provider):
            logger.error(f"Lifecycle: Cannot initialize '{agent_id}': All keys for selected provider '{final_provider}' are quarantined. Skipping.")
            continue
        # --- End Final Provider Checks ---

        # Inject Admin AI operational instructions or standard ones
        if agent_id == BOOTSTRAP_AGENT_ID:
            user_defined_prompt = agent_config_data.get("system_prompt", "")
            admin_ops_template = settings.PROMPTS.get("admin_ai_operational_instructions", "--- Admin Ops Instructions Missing ---")
            # Always use XML
            tool_desc = manager.tool_descriptions_xml
            operational_instructions = admin_ops_template.replace("{tool_descriptions_xml}", tool_desc)
            operational_instructions = operational_instructions.replace("{tool_descriptions_json}", "") # Remove other placeholder

            final_agent_config_data["system_prompt"] = (
                f"--- Primary Goal/Persona ---\n{user_defined_prompt}\n\n"
                f"{operational_instructions}\n\n"
                f"---\n{formatted_available_models}\n---" # Include model list
            )
            logger.info(f"Lifecycle: Assembled final prompt for '{BOOTSTRAP_AGENT_ID}' (using {selection_method} selection: {final_provider}/{final_model})")
        else:
             logger.info(f"Lifecycle: Using system prompt from config for bootstrap agent '{agent_id}'.")
             if "system_prompt" not in final_agent_config_data:
                  final_agent_config_data["system_prompt"] = ""


        # Schedule agent creation task using the internal function
        tasks.append(_create_agent_internal(
            manager,
            agent_id_requested=agent_id,
            agent_config_data=final_agent_config_data,
            is_bootstrap=True
            ))

    # Gather results (same logic as before)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successful_ids = []
    num_expected_tasks = len([cfg for cfg in agent_configs_list if cfg.get("agent_id")])

    if len(results) != num_expected_tasks:
        logger.error(f"Lifecycle: Mismatch between expected bootstrap tasks ({num_expected_tasks}) and results received ({len(results)}). Some initializations might have failed early.")
    else:
        processed_configs = [cfg for cfg in agent_configs_list if cfg.get("agent_id")]
        for i, result in enumerate(results):
             original_agent_id_attempted = processed_configs[i].get("agent_id", f"unknown_index_{i}")
             try:
                 if isinstance(result, tuple) and result[0]:
                     created_agent_id = result[2]
                     if created_agent_id:
                         if created_agent_id not in manager.bootstrap_agents:
                             manager.bootstrap_agents.append(created_agent_id)
                             successful_ids.append(created_agent_id)
                             logger.info(f"--- Lifecycle: Bootstrap agent '{created_agent_id}' initialized. ---")
                         else:
                             logger.warning(f"Lifecycle: Bootstrap agent '{created_agent_id}' appears to be already initialized. Skipping duplicate add.")
                     else:
                         logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result[1]} (Success reported but no ID?) ---")
                 elif isinstance(result, Exception):
                     logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result} ---", exc_info=result)
                 else: # Assume tuple (False, message, None)
                     error_msg = result[1] if isinstance(result, tuple) else str(result)
                     logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {error_msg} ---")
             except Exception as gather_err:
                 logger.error(f"Lifecycle: Unexpected error processing bootstrap result for '{original_agent_id_attempted}': {gather_err}", exc_info=True)


    logger.info(f"Lifecycle: Finished bootstrap initialization. Active: {successful_ids}")
    if BOOTSTRAP_AGENT_ID not in manager.agents:
        logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize! Check previous errors.")


async def _create_agent_internal(
    manager: 'AgentManager',
    agent_id_requested: Optional[str],
    agent_config_data: Dict[str, Any],
    is_bootstrap: bool = False,
    team_id: Optional[str] = None,
    loading_from_session: bool = False
    ) -> Tuple[bool, str, Optional[str]]:
    """ Internal method for creating agent instances using ProviderKeyManager. """
    agent_id: Optional[str] = None;
    if agent_id_requested and agent_id_requested in manager.agents:
        msg = f"Lifecycle: Agent ID '{agent_id_requested}' already exists."
        logger.error(msg); return False, msg, None
    elif agent_id_requested: agent_id = agent_id_requested
    else: agent_id = _generate_unique_agent_id(manager) # Use local helper

    if not agent_id: return False, "Lifecycle: Failed to determine Agent ID.", None

    logger.debug(f"Lifecycle: Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")

    provider_name = agent_config_data.get("provider") # Required, checked by ManageTeamTool
    model = agent_config_data.get("model")         # Required, checked by ManageTeamTool
    persona = agent_config_data.get("persona")       # Required, checked by ManageTeamTool

    # --- Provider/Model Validation Block ---
    if not provider_name or not model or not persona:
         msg = f"Lifecycle Error: Missing provider, model, or persona for agent '{agent_id}'. This should have been caught by ManageTeamTool."
         logger.error(msg); return False, msg, None

    if not settings.is_provider_configured(provider_name):
        msg = f"Lifecycle: Provider '{provider_name}' not configured in .env settings."
        logger.error(msg); return False, msg, None

    if provider_name not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(provider_name):
        msg = f"Lifecycle: Cannot create agent '{agent_id}': All keys for '{provider_name}' are quarantined."
        logger.error(msg); return False, msg, None

    # --- ADDED: Provider/Model mismatch check ---
    is_local_provider = provider_name in ["ollama", "litellm"]
    model_has_prefix = '/' in model # Simple check if model has a prefix
    correct_prefix = f"{provider_name}/"

    if is_local_provider and not model.startswith(correct_prefix):
        msg = f"Lifecycle Error: Model '{model}' must start with provider prefix '{correct_prefix}' when provider is '{provider_name}'."
        logger.error(msg); return False, msg, None
    elif not is_local_provider and model_has_prefix:
        msg = f"Lifecycle Error: Model '{model}' looks like a local model (contains '/'), but provider is '{provider_name}'. Use only the model name suffix for non-local providers."
        logger.error(msg); return False, msg, None
    # --- END: Provider/Model mismatch check ---

    # --- Adjusted Model ID for local providers ---
    # Store the model ID *without* the prefix if it's a local provider,
    # as the provider instance itself doesn't need the prefix.
    model_to_use_in_provider = model
    if is_local_provider and model.startswith(correct_prefix):
        model_to_use_in_provider = model[len(correct_prefix):]
    # --- END ADJUSTMENT ---

    # Validate against available models AFTER adjusting local model ID
    if not model_registry.is_model_available(provider_name, model_to_use_in_provider):
        full_model_id_check = model # Use original model ID for error message
        available_list_str = ", ".join(model_registry.get_available_models_list(provider=provider_name)) or "(None available)"
        msg = f"Lifecycle: Model '{full_model_id_check}' not available for provider '{provider_name}'. Available: [{available_list_str}]"
        logger.error(msg); return False, msg, None
    else: logger.info(f"Lifecycle: Dynamic agent model validated: Provider='{provider_name}', Model='{model_to_use_in_provider}'.")
    # --- End Validation Block ---


    # Assemble System Prompt (Unchanged from previous step)
    role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
    final_system_prompt = role_specific_prompt # Assume prompt is complete unless dynamic/non-bootstrap
    if not loading_from_session and not is_bootstrap:
        logger.debug(f"Lifecycle: Constructing final prompt for dynamic agent '{agent_id}'...")
        standard_info_template = settings.PROMPTS.get("standard_framework_instructions", "--- Standard Instructions Missing ---")
        tool_desc = manager.tool_descriptions_xml # Always use XML now
        standard_info = standard_info_template.replace("{tool_descriptions_xml}", tool_desc)
        standard_info = standard_info.replace("{tool_descriptions_json}", "")
        try: standard_info = standard_info.format(agent_id=agent_id, team_id=team_id or "N/A")
        except KeyError as fmt_err: logger.error(f"Failed to format agent_id/team_id into standard instructions: {fmt_err}")
        final_system_prompt = standard_info + "\n\n--- Your Specific Role & Task ---\n" + role_specific_prompt
        logger.info(f"Lifecycle: Injected standard framework instructions (XML format) for dynamic agent '{agent_id}'.")
    elif loading_from_session:
         final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt)
         logger.debug(f"Lifecycle: Using stored prompt for loaded agent '{agent_id}'.")
    elif is_bootstrap:
         final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt)
         logger.debug(f"Lifecycle: Using pre-assembled prompt for bootstrap agent '{agent_id}'.")

    # Prepare Provider Arguments using ProviderKeyManager
    final_provider_args: Optional[Dict[str, Any]] = None
    api_key_used = None # Track the specific key used
    if provider_name in ["ollama", "litellm"]:
        final_provider_args = settings.get_provider_config(provider_name)
        if provider_name == 'ollama' and PROVIDER_CLASS_MAP.get(provider_name) == OllamaProvider:
             final_provider_args['api_key'] = 'ollama' # Dummy key for OpenAI lib interface
    else: # Remote providers
        final_provider_args = await manager.key_manager.get_active_key_config(provider_name)
        if final_provider_args is None:
            msg = f"Lifecycle: Failed to get active API key for provider '{provider_name}'."; logger.error(msg); return False, msg, None
        api_key_used = final_provider_args.get('api_key')

    # Add agent-specific kwargs
    temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
    allowed_provider_keys = ['api_key', 'base_url', 'referer']
    agent_config_keys_to_exclude = ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'project_name', 'session_name'] + allowed_provider_keys
    client_init_kwargs = {k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude and k not in KNOWN_OLLAMA_OPTIONS}
    api_call_options = {k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude and k in KNOWN_OLLAMA_OPTIONS}
    final_provider_args = {**final_provider_args, **client_init_kwargs}
    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

    # Create final agent config entry for storage/state
    # Use the *original* model ID (e.g., "ollama/llama3") for config storage
    final_agent_config_entry = {
        "agent_id": agent_id,
        "config": { "provider": provider_name, "model": model, "system_prompt": final_system_prompt, "persona": persona, "temperature": temperature, **api_call_options, **client_init_kwargs }
    }


    # Instantiate Provider
    ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
    if not ProviderClass:
        msg = f"Lifecycle: Unknown provider type '{provider_name}'."; logger.error(msg); return False, msg, None
    try:
        llm_provider_instance = ProviderClass(**final_provider_args)
    except Exception as e:
        msg = f"Lifecycle: Provider init failed for {provider_name}: {e}"; logger.error(msg, exc_info=True); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")

    # Instantiate Agent
    try:
        # Pass the config entry with the *original* model ID to the Agent constructor
        agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=manager)
        # Set the model attribute within the agent instance to the one the *provider* needs
        agent.model = model_to_use_in_provider
        if api_key_used: agent._last_api_key_used = api_key_used
    except Exception as e:
        msg = f"Lifecycle: Agent instantiation failed: {e}"; logger.error(msg, exc_info=True)
        await manager._close_provider_safe(llm_provider_instance); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated Agent object for '{agent_id}'.")

    # Final steps (Sandbox, Add to Manager, Add to Team State)
    try: await asyncio.to_thread(agent.ensure_sandbox_exists)
    except Exception as e: logger.error(f"  Lifecycle: Error ensuring sandbox for '{agent_id}': {e}", exc_info=True)

    manager.agents[agent_id] = agent
    logger.debug(f"Lifecycle: Agent '{agent_id}' added to manager.agents dict.")

    team_add_msg_suffix = ""
    if team_id:
        team_add_success, team_add_msg = await manager.state_manager.add_agent_to_team(agent_id, team_id)
        if not team_add_success: team_add_msg_suffix = f" (Warning adding to team: {team_add_msg})"
        logger.info(f"Lifecycle: Agent '{agent_id}' state added to team '{team_id}'.{team_add_msg_suffix}")

    message = f"Agent '{agent_id}' ({persona}) created successfully." + team_add_msg_suffix
    return True, message, agent_id


async def create_agent_instance(
    manager: 'AgentManager',
    agent_id_requested: Optional[str],
    provider: str, model: str, system_prompt: str, persona: str,
    team_id: Optional[str] = None, temperature: Optional[float] = None,
    **kwargs
    ) -> Tuple[bool, str, Optional[str]]:
    """ Creates a dynamic agent instance by calling the internal function. """
    # Add the validation check for required fields *before* calling internal
    if not all([provider, model, system_prompt, persona]):
        msg = "Lifecycle Error: Missing required arguments (provider, model, system_prompt, persona) for creating dynamic agent."
        logger.error(msg)
        return False, msg, None

    agent_config_data = { "provider": provider, "model": model, "system_prompt": system_prompt, "persona": persona }
    if temperature is not None: agent_config_data["temperature"] = temperature

    known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature', 'project_name', 'session_name']
    extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args}
    agent_config_data.update(extra_kwargs)

    success, message, created_agent_id = await _create_agent_internal(
        manager, agent_id_requested=agent_id_requested,
        agent_config_data=agent_config_data, is_bootstrap=False,
        team_id=team_id, loading_from_session=False
    )

    if success and created_agent_id:
        agent = manager.agents.get(created_agent_id)
        team = manager.state_manager.get_agent_team(created_agent_id)
        config_ui = agent.agent_config.get("config", {}) if agent else {}
        await manager.send_to_ui({ "type": "agent_added", "agent_id": created_agent_id, "config": config_ui, "team": team })
        await manager.push_agent_status_update(created_agent_id)

    return success, message, created_agent_id


async def delete_agent_instance(manager: 'AgentManager', agent_id: str) -> Tuple[bool, str]:
    """ Deletes a dynamic agent instance. """
    if not agent_id: return False, "Lifecycle Error: Agent ID empty."
    if agent_id not in manager.agents: return False, f"Lifecycle Error: Agent '{agent_id}' not found."
    if agent_id in manager.bootstrap_agents: return False, f"Lifecycle Error: Cannot delete bootstrap agent '{agent_id}'."

    agent_instance = manager.agents.pop(agent_id, None)
    manager.state_manager.remove_agent_from_all_teams_state(agent_id)
    if agent_instance and agent_instance.llm_provider:
        await manager._close_provider_safe(agent_instance.llm_provider)

    message = f"Agent '{agent_id}' deleted."
    logger.info(f"Lifecycle: {message}")
    await manager.send_to_ui({"type": "agent_deleted", "agent_id": agent_id})
    return True, message


def _generate_unique_agent_id(manager: 'AgentManager', prefix="agent") -> str:
    """ Generates a unique agent ID. """
    timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]
    while True:
        new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_")
        if new_id not in manager.agents: return new_id
        time.sleep(0.001); timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]
