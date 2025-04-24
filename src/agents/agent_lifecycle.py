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
    "ollama": OllamaProvider,
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

# --- KNOWN_OLLAMA_OPTIONS ---
# Known valid Ollama options (from ollama_provider.py, needed for filtering kwargs)
KNOWN_OLLAMA_OPTIONS = {
    "mirostat", "mirostat_eta", "mirostat_tau", "num_ctx", "num_gpu", "num_thread",
    "num_keep", "seed", "num_predict", "repeat_last_n", "repeat_penalty",
    "temperature", "tfs_z", "top_k", "top_p", "min_p", "use_mmap", "use_mlock",
    "numa", "num_batch", "main_gpu", "low_vram", "f16_kv", "logits_all",
    "vocab_only", "stop", "presence_penalty", "frequency_penalty", "penalize_newline",
    "typical_p"
}
# --- END KNOWN_OLLAMA_OPTIONS ---

# --- Automatic Model Selection Logic ---
async def _select_best_available_model(manager: 'AgentManager') -> Tuple[Optional[str], Optional[str]]:
    """
    Selects the best available and usable model based on performance ranking or registry order.

    Args:
        manager: The AgentManager instance.

    Returns:
        Tuple[Optional[str], Optional[str]]: (specific_provider_name, model_suffix) or (None, None)
        Returns the specific provider instance name (e.g., ollama-local-...) and the model suffix.
    """
    logger.info("Attempting automatic model selection...")
    ranked_models = manager.performance_tracker.get_ranked_models(min_calls=0)
    available_dict = model_registry.get_available_models_dict()

    # Try ranked models first
    if ranked_models:
        logger.debug(f"Ranking based selection: {len(ranked_models)} models ranked. Checking availability and keys...")
        for provider_base, model_suffix, score, _ in ranked_models: # provider_base is 'ollama', 'openrouter' etc.
            # Find a specific reachable instance for this base provider type
            specific_provider_instance = None
            # Look for dynamic local, proxy, or the base name itself if remote/configured directly
            matching_providers = [p for p in model_registry._reachable_providers if p.startswith(f"{provider_base}-local-") or p == f"{provider_base}-proxy" or p == provider_base]
            if not matching_providers:
                 logger.debug(f"Auto-select skipping {provider_base}/{model_suffix} (score {score}): No reachable instance found for base provider.")
                 continue
            specific_provider_instance = matching_providers[0] # Take the first reachable one

            # Check if the model suffix is available on that specific instance
            if not model_registry.is_model_available(specific_provider_instance, model_suffix):
                logger.debug(f"Auto-select skipping {provider_base}/{model_suffix} (score {score}): Model not available on reachable instance '{specific_provider_instance}'.")
                continue

            # Check key depletion for remote providers (using base name)
            if provider_base not in ["ollama", "litellm"]:
                 if await manager.key_manager.is_provider_depleted(provider_base):
                     logger.debug(f"Auto-select skipping {provider_base}/{model_suffix} (score {score}): Provider keys depleted.")
                     continue

            logger.info(f"Automatic selection successful (Ranked): Selected {specific_provider_instance}/{model_suffix} (Score: {score})")
            return specific_provider_instance, model_suffix
        logger.warning("Automatic selection: No ranked models passed availability/key checks. Falling back to registry order.")

    # Fallback to registry order if ranking fails or no ranked models
    provider_order = ["ollama", "litellm", "openrouter", "openai"] # Prioritize local base types
    for provider_base in provider_order:
        # Find reachable instances of this base type
        matching_providers = sorted([p for p in available_dict if p.startswith(f"{provider_base}-local-") or p == f"{provider_base}-proxy" or p == provider_base])
        if not matching_providers: continue # Skip if no instance of this type is reachable

        # Check key depletion for remote base type
        if provider_base not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(provider_base):
            logger.debug(f"Fallback selection skipping base provider {provider_base}: depleted keys.")
            continue

        # Try models from the first reachable instance of this type
        first_reachable_instance = matching_providers[0]
        models = available_dict.get(first_reachable_instance, [])
        if models:
            model_id_suffix = models[0].get("id") # Get first model's suffix
            if model_id_suffix:
                logger.info(f"Automatic selection (Fallback): Selected first available '{first_reachable_instance}/{model_id_suffix}'")
                return first_reachable_instance, model_id_suffix # Return specific instance and suffix

    logger.error("Automatic selection failed: No available models found in registry fallback.")
    return None, None
# --- END Automatic Model Selection Logic ---


# --- initialize_bootstrap_agents (Ensured full code included) ---
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

    for agent_conf_entry in agent_configs_list:
        agent_id = agent_conf_entry.get("agent_id")
        if not agent_id:
            logger.warning("Lifecycle: Skipping bootstrap agent due to missing 'agent_id'.")
            continue

        agent_config_data = agent_conf_entry.get("config", {})
        final_agent_config_data = agent_config_data.copy()
        selection_method = "config.yaml" # Assume config first
        final_provider_for_creation = None # Store the specific provider instance name

        # --- Admin AI Model/Provider Determination ---
        if agent_id == BOOTSTRAP_AGENT_ID:
            logger.info(f"Lifecycle: Processing Admin AI ({BOOTSTRAP_AGENT_ID}) configuration...")
            config_provider = final_agent_config_data.get("provider") # Base name like 'ollama'
            config_model = final_agent_config_data.get("model") # Canonical name like 'ollama/model...'
            use_config_value = False

            if config_provider and config_model:
                logger.info(f"Lifecycle: Admin AI defined in config.yaml: Provider='{config_provider}', Model='{config_model}'")
                # Check if base provider type is configured in .env
                provider_configured = settings.is_provider_configured(config_provider)
                if not provider_configured:
                    logger.warning(f"Lifecycle: Base provider '{config_provider}' specified for Admin AI is not configured in .env. Ignoring config.")
                else:
                    # Validate format and get model suffix
                    is_local_provider_cfg = config_provider in ["ollama", "litellm"]
                    model_id_suffix = None
                    format_valid = True

                    if is_local_provider_cfg:
                        if config_model.startswith(f"{config_provider}/"):
                            model_id_suffix = config_model[len(config_provider)+1:]
                        else:
                            logger.warning(f"Lifecycle: Admin AI model '{config_model}' in config.yaml must start with '{config_provider}/'. Ignoring config.")
                            format_valid = False
                    elif config_model.startswith("ollama/") or config_model.startswith("litellm/"):
                         logger.warning(f"Lifecycle: Admin AI model '{config_model}' in config.yaml starts with local prefix, but provider is '{config_provider}'. Ignoring config.")
                         format_valid = False
                    else: # Remote provider
                         model_id_suffix = config_model # Use full name for check

                    # Check availability if format was valid
                    if format_valid and model_id_suffix is not None:
                        found_on_specific_provider = None
                        if is_local_provider_cfg:
                            # Search discovered local providers for this model suffix
                            matching_providers = [p for p in model_registry.get_available_models_dict() if p.startswith(f"{config_provider}-local-") or p == f"{config_provider}-proxy"]
                            for specific_provider_name in matching_providers:
                                if model_registry.is_model_available(specific_provider_name, model_id_suffix):
                                    found_on_specific_provider = specific_provider_name
                                    break # Found it
                            if not found_on_specific_provider:
                                logger.warning(f"Lifecycle: Model suffix '{model_id_suffix}' (from '{config_model}') not found under any discovered '{config_provider}-local-*' or '{config_provider}-proxy' providers. Ignoring config.")
                            else:
                                logger.info(f"Lifecycle: Using Admin AI model '{model_id_suffix}' from config on discovered provider '{found_on_specific_provider}'.")
                                final_provider_for_creation = found_on_specific_provider
                                use_config_value = True
                        else: # Remote provider check
                            if not model_registry.is_model_available(config_provider, model_id_suffix):
                                 logger.warning(f"Lifecycle: Model '{model_id_suffix}' (from '{config_model}') specified for Admin AI in config is not available via registry for provider '{config_provider}'. Ignoring config.")
                            else:
                                 logger.info(f"Lifecycle: Using Admin AI provider/model specified in config.yaml: {config_provider}/{config_model}")
                                 final_provider_for_creation = config_provider
                                 use_config_value = True

            # Fallback to automatic selection if config wasn't valid, specified, or available
            if not use_config_value:
                logger.info("Lifecycle: Admin AI provider/model not specified or invalid/unavailable in config.yaml. Attempting automatic selection...")
                selected_provider, selected_model_suffix = await _select_best_available_model(manager) # Returns specific instance name for local
                selection_method = "automatic"

                if not selected_model_suffix or not selected_provider:
                    logger.error("Lifecycle: Could not automatically select any available/configured/non-depleted model for Admin AI! Check .env configurations and model discovery logs.")
                    continue # Skip creating Admin AI

                final_provider_for_creation = selected_provider
                # Determine canonical model ID for storage/logging
                base_provider_type = selected_provider.split('-local-')[0].split('-proxy')[0]
                if base_provider_type in ["ollama", "litellm"]:
                    final_agent_config_data["model"] = f"{base_provider_type}/{selected_model_suffix}"
                else:
                    final_agent_config_data["model"] = selected_model_suffix
                final_agent_config_data["provider"] = final_provider_for_creation # Store the specific provider name used
            else:
                 # If using config value, ensure final_provider_for_creation is set
                 if not final_provider_for_creation: final_provider_for_creation = config_provider
                 # Ensure the final config reflects the provider being used
                 final_agent_config_data["provider"] = final_provider_for_creation
                 final_agent_config_data["model"] = config_model # Keep original canonical name

        # --- Final Provider/Model Checks ---
        final_provider = final_provider_for_creation # Use the determined specific provider name
        final_model = final_agent_config_data.get("model") # Canonical name like 'ollama/...' or 'openai/...'
        if not final_provider or not final_model:
            logger.error(f"Lifecycle: Cannot initialize '{agent_id}': Final provider or model is missing after selection/validation. Skipping.")
            continue

        # Check if the selected provider is actually reachable
        if final_provider not in model_registry._reachable_providers:
             logger.error(f"Lifecycle: Cannot initialize '{agent_id}': Final provider '{final_provider}' is not in the list of reachable providers found during discovery. Skipping.")
             continue

        # Check key depletion only for remote providers, using the base name
        base_provider_name = final_provider.split('-local-')[0].split('-proxy')[0]
        if base_provider_name not in ["ollama", "litellm"]:
             if await manager.key_manager.is_provider_depleted(base_provider_name):
                  logger.error(f"Lifecycle: Cannot initialize '{agent_id}': All keys for selected provider '{base_provider_name}' (base for '{final_provider}') are quarantined. Skipping.")
                  continue
        # --- End Final Provider Checks ---

        # Inject Admin AI operational instructions or standard ones
        if agent_id == BOOTSTRAP_AGENT_ID:
            user_defined_prompt = agent_config_data.get("system_prompt", "")
            tool_desc = manager.tool_descriptions_xml

            # --- Select prompt based on provider type ---
            is_local_provider_selected = final_provider.startswith("ollama-local-") or \
                                         final_provider.startswith("litellm-local-") or \
                                         final_provider.endswith("-proxy") # Consider proxy local too

            if is_local_provider_selected:
                prompt_key = "admin_ai_operational_instructions_local"
                logger.info(f"Lifecycle: Using LOCAL prompt template '{prompt_key}' for Admin AI on provider '{final_provider}'.")
            else:
                prompt_key = "admin_ai_operational_instructions"
                logger.info(f"Lifecycle: Using STANDARD prompt template '{prompt_key}' for Admin AI on provider '{final_provider}'.")

            admin_ops_template = settings.PROMPTS.get(prompt_key, f"--- {prompt_key} Instructions Missing ---")
            # --- End prompt selection ---

            # Use the template directly, assuming placeholders are removed from prompts.json
            operational_instructions = admin_ops_template

            final_agent_config_data["system_prompt"] = (
                f"--- Primary Goal/Persona ---\n{user_defined_prompt}\n\n"
                f"{operational_instructions}" # Now uses the template without static tool descriptions or model list
            )
            logger.info(f"Lifecycle: Assembled final prompt for '{BOOTSTRAP_AGENT_ID}' (using {selection_method} selection: {final_provider}/{final_model}) - Model list excluded.")
        else:
             logger.info(f"Lifecycle: Using system prompt from config for bootstrap agent '{agent_id}'.")
             if "system_prompt" not in final_agent_config_data:
                  final_agent_config_data["system_prompt"] = ""

        # Schedule agent creation task using the internal function
        tasks.append(_create_agent_internal(
            manager,
            agent_id_requested=agent_id,
            agent_config_data=final_agent_config_data, # Pass potentially modified config
            is_bootstrap=True
            ))

    # Gather results
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
                     created_agent_id = result[2];
                     if created_agent_id:
                         if created_agent_id not in manager.bootstrap_agents: manager.bootstrap_agents.append(created_agent_id); successful_ids.append(created_agent_id); logger.info(f"--- Lifecycle: Bootstrap agent '{created_agent_id}' initialized. ---")
                         else: logger.warning(f"Lifecycle: Bootstrap agent '{created_agent_id}' appears to be already initialized. Skipping duplicate add.")
                     else: logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result[1]} (Success reported but no ID?) ---")
                 elif isinstance(result, Exception): logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result} ---", exc_info=result)
                 else: error_msg = result[1] if isinstance(result, tuple) else str(result); logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {error_msg} ---")
             except Exception as gather_err: logger.error(f"Lifecycle: Unexpected error processing bootstrap result for '{original_agent_id_attempted}': {gather_err}", exc_info=True)
    logger.info(f"Lifecycle: Finished bootstrap initialization. Active: {successful_ids}")
    if BOOTSTRAP_AGENT_ID not in manager.agents: logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize! Check previous errors.")
# --- END initialize_bootstrap_agents ---


# --- _create_agent_internal (with corrected validation) ---
async def _create_agent_internal(
    manager: 'AgentManager',
    agent_id_requested: Optional[str],
    agent_config_data: Dict[str, Any],
    is_bootstrap: bool = False,
    team_id: Optional[str] = None,
    loading_from_session: bool = False
    ) -> Tuple[bool, str, Optional[str]]:
    """ Internal method for creating agent instances. Now supports automatic model selection. """
    agent_id: Optional[str] = None;
    if agent_id_requested and agent_id_requested in manager.agents:
        msg = f"Lifecycle: Agent ID '{agent_id_requested}' already exists."
        logger.error(msg); return False, msg, None
    elif agent_id_requested: agent_id = agent_id_requested
    else: agent_id = _generate_unique_agent_id(manager)

    if not agent_id: return False, "Lifecycle: Failed to determine Agent ID.", None

    logger.debug(f"Lifecycle: Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")

    # --- Model/Provider Handling ---
    provider_name = agent_config_data.get("provider") # This might be specific like 'ollama-local-...' if set by bootstrap init
    model_id_canonical = agent_config_data.get("model") # This should be canonical like 'ollama/...'
    persona = agent_config_data.get("persona")
    selection_source = "specified" if not is_bootstrap else agent_config_data.get("_selection_method", "specified") # Track source if bootstrap

    if not persona:
         msg = f"Lifecycle Error: Missing persona for agent '{agent_id}'."
         logger.error(msg); return False, msg, None

    # Auto-selection only for non-bootstrap agents if needed
    if not provider_name or not model_id_canonical:
        if is_bootstrap:
             # This case should ideally not be reached due to checks in initialize_bootstrap_agents
             msg = f"Lifecycle Error: Bootstrap agent '{agent_id}' reached _create_agent_internal without provider/model."
             logger.critical(msg); return False, msg, None
        logger.info(f"Lifecycle: Provider or model not specified for dynamic agent '{agent_id}'. Attempting automatic selection...")
        selected_provider, selected_model_suffix = await _select_best_available_model(manager); selection_source = "automatic"
        if not selected_provider or not selected_model_suffix:
            msg = f"Lifecycle Error: Automatic model selection failed for agent '{agent_id}'. No suitable model found."
            logger.error(msg); return False, msg, None
        provider_name = selected_provider # This is the specific instance name (e.g., ollama-local-...)
        # Construct canonical ID carefully, avoid double prefix
        base_provider_type = provider_name.split('-local-')[0].split('-proxy')[0]
        if base_provider_type in ["ollama", "litellm"]:
             if selected_model_suffix.startswith(f"{base_provider_type}/"):
                  model_id_canonical = selected_model_suffix
                  logger.warning(f"Auto-selected model suffix '{selected_model_suffix}' unexpectedly contained prefix for provider '{provider_name}'. Using suffix directly.")
             else:
                  model_id_canonical = f"{base_provider_type}/{selected_model_suffix}" # Add prefix
        else: # Remote provider
             model_id_canonical = selected_model_suffix # Use suffix directly
        # Update config data passed to this function
        agent_config_data["provider"] = provider_name
        agent_config_data["model"] = model_id_canonical
        logger.info(f"Lifecycle: Automatically selected {model_id_canonical} (Provider: {provider_name}) for agent '{agent_id}'.")
    # --- End Model/Provider Handling ---


    # --- Provider/Model Validation ---
    if not provider_name or not model_id_canonical:
         msg = f"Lifecycle Error: Missing final provider or model for agent '{agent_id}'."; logger.error(msg); return False, msg, None

    # Check configuration/depletion using the *base* provider name if it's dynamic local
    is_dynamic_local = "-local-" in provider_name or "-proxy" in provider_name
    check_provider_name = provider_name.split('-local-')[0].split('-proxy')[0] if is_dynamic_local else provider_name

    # Skip .env config check for dynamic local providers, as their config comes from discovery
    if not is_dynamic_local and not settings.is_provider_configured(check_provider_name):
        msg = f"Lifecycle: Provider '{check_provider_name}' (base for '{provider_name}', source: {selection_source}) not configured in .env settings."; logger.error(msg); return False, msg, None

    # Key depletion check still applies to the base provider name for remote providers
    if check_provider_name not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(check_provider_name):
        msg = f"Lifecycle: Cannot create agent '{agent_id}': All keys for '{check_provider_name}' (base for '{provider_name}', source: {selection_source}) are quarantined."; logger.error(msg); return False, msg, None

    # --- Corrected Validation Logic ---
    # Determine if the final provider is local based on its name
    is_local_provider = "-local-" in provider_name or "-proxy" in provider_name
    model_id_for_provider = None # ID to pass to the provider class (without prefix)
    validation_passed = True
    error_msg_val = None
    error_prefix = f"Lifecycle Error ({selection_source} model '{model_id_canonical}' for provider '{provider_name}'):"

    if is_local_provider:
        # Expect canonical ID like "ollama/model_name" or "litellm/model_name"
        # Provider name might be dynamic (e.g., ollama-local-127-0-0-1)
        base_provider_type = provider_name.split('-local-')[0].split('-proxy')[0] # Get 'ollama' or 'litellm'
        if model_id_canonical.startswith(f"{base_provider_type}/"):
            model_id_for_provider = model_id_canonical[len(base_provider_type)+1:] # Strip prefix
        else:
            error_msg_val = f"{error_prefix} Local model ID must start with prefix '{base_provider_type}/'."
            validation_passed = False
    else: # Remote provider
        # Expect canonical ID like "google/gemma..." - it should NOT start with a local prefix
        if model_id_canonical.startswith("ollama/") or model_id_canonical.startswith("litellm/"):
             error_msg_val = f"{error_prefix} Remote model ID should not start with 'ollama/' or 'litellm/'."
             validation_passed = False
        else:
             # Remote IDs (like OpenRouter's) can contain slashes, use the canonical ID directly
             model_id_for_provider = model_id_canonical

    if not validation_passed:
        logger.error(error_msg_val); return False, error_msg_val, None

    # Final availability check using the model ID format the provider expects
    # Use the potentially dynamic provider_name for the check
    if not model_registry.is_model_available(provider_name, model_id_for_provider):
        available_list_str = ", ".join(model_registry.get_available_models_list(provider=provider_name)) or "(None available)"
        msg = f"Lifecycle: Model '{model_id_for_provider}' (derived from '{model_id_canonical}', source: {selection_source}) not available for provider '{provider_name}'. Available: [{available_list_str}]"
        logger.error(msg); return False, msg, None
    # --- End Corrected Validation ---

    logger.info(f"Lifecycle: Final model validated: Provider='{provider_name}', Model='{model_id_for_provider}'. Canonical stored: '{model_id_canonical}'.")

    # Assemble System Prompt
    role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
    final_system_prompt = role_specific_prompt
    if not loading_from_session and not is_bootstrap:
        logger.debug(f"Lifecycle: Constructing final prompt for dynamic agent '{agent_id}'...")
        standard_info_template = settings.PROMPTS.get("standard_framework_instructions", "--- Standard Instructions Missing ---")
        tool_desc = manager.tool_descriptions_xml
        standard_info = standard_info_template.replace("{tool_descriptions_xml}", tool_desc); standard_info = standard_info.replace("{tool_descriptions_json}", "")
        try: standard_info = standard_info.format(agent_id=agent_id, team_id=team_id or "N/A")
        except KeyError as fmt_err: logger.error(f"Failed to format agent_id/team_id into standard instructions: {fmt_err}")
        final_system_prompt = standard_info + "\n\n--- Your Specific Role & Task ---\n" + role_specific_prompt
        logger.info(f"Lifecycle: Injected standard framework instructions (XML format) for dynamic agent '{agent_id}'.")
    elif loading_from_session: final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt); logger.debug(f"Lifecycle: Using stored prompt for loaded agent '{agent_id}'.")
    elif is_bootstrap: final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt); logger.debug(f"Lifecycle: Using pre-assembled prompt for bootstrap agent '{agent_id}'.")

    # Prepare Provider Arguments
    final_provider_args: Dict[str, Any] = {}; api_key_used = None
    is_final_provider_local = "-local-" in provider_name or "-proxy" in provider_name

    if is_final_provider_local:
        # Get base URL from registry
        base_url = model_registry.get_reachable_provider_url(provider_name)
        if base_url:
             final_provider_args['base_url'] = base_url

        # Check if keys are configured for the base type (e.g., 'ollama')
        base_provider_type = provider_name.split('-local-')[0].split('-proxy')[0]
        if base_provider_type in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[base_provider_type]:
            logger.debug(f"API keys found for local provider base type '{base_provider_type}'. Attempting to get active key.")
            key_config = await manager.key_manager.get_active_key_config(base_provider_type)
            if key_config is None:
                msg = f"Lifecycle: Failed to get active API key for local provider '{provider_name}' (base type '{base_provider_type}') - keys might be configured but all quarantined."
                logger.error(msg); return False, msg, None
            final_provider_args.update(key_config) # Merge key config
            api_key_used = final_provider_args.get('api_key')
            logger.info(f"Using configured API key ending '...{api_key_used[-4:]}' for local provider '{provider_name}'.")
        else:
            # No keys configured for base type, proceed without key (except special ollama case)
            logger.debug(f"No API keys configured for local provider base type '{base_provider_type}'. Proceeding without key for '{provider_name}'.")
            ProviderClassCheck = PROVIDER_CLASS_MAP.get(base_provider_type) # Check base type class
            if base_provider_type == 'ollama' and ProviderClassCheck == OllamaProvider:
                 final_provider_args['api_key'] = 'ollama' # Add special key if needed by class

    else: # Remote provider
        # Get base config (e.g., referer) from settings
        final_provider_args = settings.get_provider_config(provider_name)
        # Get an active API key config
        key_config = await manager.key_manager.get_active_key_config(provider_name)
        if key_config is None:
            msg = f"Lifecycle: Failed to get active API key for remote provider '{provider_name}'."; logger.error(msg); return False, msg, None
        final_provider_args.update(key_config) # Merge key config
        api_key_used = final_provider_args.get('api_key')

    temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
    allowed_provider_keys = ['api_key', 'base_url', 'referer']; agent_config_keys_to_exclude = ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'project_name', 'session_name'] + allowed_provider_keys
    client_init_kwargs = {k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude and k not in KNOWN_OLLAMA_OPTIONS}
    api_call_options = {k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude and k in KNOWN_OLLAMA_OPTIONS}
    final_provider_args = {**final_provider_args, **client_init_kwargs}; final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

    # Create final agent config entry for storage/state - Use canonical ID
    final_agent_config_entry = { "agent_id": agent_id, "config": { "provider": provider_name, "model": model_id_canonical, "system_prompt": final_system_prompt, "persona": persona, "temperature": temperature, **api_call_options, **client_init_kwargs } }

    # Instantiate Provider
    ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
    if not ProviderClass: # Handle dynamic local provider names
        base_provider_type = provider_name.split('-local-')[0].split('-proxy')[0]
        ProviderClass = PROVIDER_CLASS_MAP.get(base_provider_type)

    if not ProviderClass: msg = f"Lifecycle: Unknown provider type '{provider_name}'."; logger.error(msg); return False, msg, None
    try: llm_provider_instance = ProviderClass(**final_provider_args)
    except Exception as e: msg = f"Lifecycle: Provider init failed for {provider_name}: {e}"; logger.error(msg, exc_info=True); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")

    # Instantiate Agent
    try:
        agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=manager)
        agent.model = model_id_for_provider # Use adjusted ID for provider calls
        if api_key_used: agent._last_api_key_used = api_key_used
    except Exception as e: msg = f"Lifecycle: Agent instantiation failed: {e}"; logger.error(msg, exc_info=True); await manager._close_provider_safe(llm_provider_instance); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated Agent object for '{agent_id}'.")

    # Final steps
    try: await asyncio.to_thread(agent.ensure_sandbox_exists)
    except Exception as e: logger.error(f"  Lifecycle: Error ensuring sandbox for '{agent_id}': {e}", exc_info=True)
    manager.agents[agent_id] = agent; logger.debug(f"Lifecycle: Agent '{agent_id}' added to manager.agents dict.")
    team_add_msg_suffix = ""
    if team_id:
        team_add_success, team_add_msg = await manager.state_manager.add_agent_to_team(agent_id, team_id)
        if not team_add_success: team_add_msg_suffix = f" (Warning adding to team: {team_add_msg})"
        logger.info(f"Lifecycle: Agent '{agent_id}' state added to team '{team_id}'.{team_add_msg_suffix}")
    message = f"Agent '{agent_id}' ({persona}) created successfully using {model_id_canonical} ({selection_source})." + team_add_msg_suffix
    return True, message, agent_id
# --- END _create_agent_internal ---


# --- create_agent_instance (Ensured full code included) ---
async def create_agent_instance(
    manager: 'AgentManager',
    agent_id_requested: Optional[str],
    provider: Optional[str], # Optional
    model: Optional[str],    # Optional
    system_prompt: str, persona: str, # Required
    team_id: Optional[str] = None, temperature: Optional[float] = None,
    **kwargs
    ) -> Tuple[bool, str, Optional[str]]:
    """ Creates a dynamic agent instance, allowing provider/model to be omitted for auto-selection. """
    if not all([system_prompt, persona]):
        msg = "Lifecycle Error: Missing required arguments (system_prompt, persona) for creating dynamic agent."
        logger.error(msg); return False, msg, None
    agent_config_data = { "system_prompt": system_prompt, "persona": persona }
    if provider: agent_config_data["provider"] = provider
    if model: agent_config_data["model"] = model
    if temperature is not None: agent_config_data["temperature"] = temperature
    known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature', 'project_name', 'session_name']
    extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args}
    agent_config_data.update(extra_kwargs)
    success, message, created_agent_id = await _create_agent_internal( manager, agent_id_requested=agent_id_requested, agent_config_data=agent_config_data, is_bootstrap=False, team_id=team_id, loading_from_session=False )
    if success and created_agent_id:
        agent = manager.agents.get(created_agent_id); team = manager.state_manager.get_agent_team(created_agent_id)
        config_ui = agent.agent_config.get("config", {}) if agent else {}
        await manager.send_to_ui({ "type": "agent_added", "agent_id": created_agent_id, "config": config_ui, "team": team })
        await manager.push_agent_status_update(created_agent_id)
    return success, message, created_agent_id
# --- END create_agent_instance ---


# --- delete_agent_instance (Ensured full code included) ---
async def delete_agent_instance(manager: 'AgentManager', agent_id: str) -> Tuple[bool, str]:
    """ Deletes a dynamic agent instance. """
    if not agent_id: return False, "Lifecycle Error: Agent ID empty."
    if agent_id not in manager.agents: return False, f"Lifecycle Error: Agent '{agent_id}' not found."
    if agent_id in manager.bootstrap_agents: return False, f"Lifecycle Error: Cannot delete bootstrap agent '{agent_id}'."
    agent_instance = manager.agents.pop(agent_id, None); manager.state_manager.remove_agent_from_all_teams_state(agent_id)
    if agent_instance and agent_instance.llm_provider: await manager._close_provider_safe(agent_instance.llm_provider)
    message = f"Agent '{agent_id}' deleted."; logger.info(f"Lifecycle: {message}")
    await manager.send_to_ui({"type": "agent_deleted", "agent_id": agent_id}); return True, message
# --- END delete_agent_instance ---


# --- _generate_unique_agent_id (Ensured full code included) ---
def _generate_unique_agent_id(manager: 'AgentManager', prefix="agent") -> str:
    """ Generates a unique agent ID. """
    timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]
    while True:
        new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_");
        if new_id not in manager.agents: return new_id
        time.sleep(0.001); timestamp = int(time.time() * 1000); short_uuid = uuid.uuid4().hex[:4]
