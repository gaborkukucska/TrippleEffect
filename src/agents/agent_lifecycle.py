# START OF FILE src/agents/agent_lifecycle.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple
import logging
import uuid
import time
import fnmatch

import re # Import re for pattern matching if needed later
# Import necessary components from other modules
from src.agents.core import Agent
from src.llm_providers.base import BaseLLMProvider
# Import settings and model_registry, BASE_DIR
from src.config.settings import settings, model_registry, BASE_DIR
# --- Import centralized constants ---
from src.agents.constants import BOOTSTRAP_AGENT_ID, KNOWN_OLLAMA_OPTIONS
# --- End Import ---

# Import PROVIDER_CLASS_MAP and BOOTSTRAP_AGENT_ID from the refactored manager
# Avoid circular import using TYPE_CHECKING
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

# --- Import Specific Provider Classes ---
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider
# --- End Provider Imports ---

# --- Import new sorting utility ---
from src.agents.agent_utils import sort_models_by_size_performance_id
# --- End Import ---

# --- ***MOVED UP: Define PROVIDER_CLASS_MAP using imported classes ***---
PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    # TODO: Add LiteLLMProvider when implemented
    # "litellm": LiteLLMProvider,
}
# --- *** END MOVED UP *** ---

logger = logging.getLogger(__name__)

# PREFERRED_ADMIN_MODELS - No change needed to this list itself for now

# --- Automatic Model Selection Logic ---
async def _select_best_available_model(manager: 'AgentManager') -> Tuple[Optional[str], Optional[str]]:
    """
    Selects the best available and usable model based on a comprehensive ranking:
    1. Number of parameters (descending).
    2. Performance score (descending).
    3. Model ID (alphabetical ascending).
    Respects MODEL_TIER and provider key availability.

    Args:
        manager: The AgentManager instance.

    Returns:
        Tuple[Optional[str], Optional[str]]: (specific_provider_name, model_id_suffix) or (None, None)
    """
    logger.info("Attempting automatic model selection with comprehensive ranking...")
    
    all_models_from_registry: Dict[str, List[Dict[str, Any]]] = manager.model_registry.get_available_models_dict()
    if not all_models_from_registry:
        logger.warning("Automatic selection: No models available in the registry.")
        return None, None

    # Flatten all models from all providers, adding 'provider' key to each model_info
    flattened_model_infos: List[Dict[str, Any]] = []
    for specific_provider_name, models_list in all_models_from_registry.items():
        for model_data in models_list:
            # model_data from registry should already have 'id' and 'num_parameters' (Optional)
            # We add the specific_provider_name to use for performance lookup and final selection
            model_info_copy = model_data.copy()
            model_info_copy["provider"] = specific_provider_name # Store the specific provider name
            flattened_model_infos.append(model_info_copy)
            
    if not flattened_model_infos:
        logger.warning("Automatic selection: No models found after flattening registry data.")
        return None, None

    # Get performance metrics - this expects provider base names
    # The sort_models_by_size_performance_id helper needs to handle looking up metrics
    # using the base provider name derived from the specific_provider_name.
    # For now, we'll pass all metrics. The helper can be adapted or we can pre-process.
    # Let's assume sort_models_by_size_performance_id can handle specific provider names
    # if performance_metrics keys are specific provider names.
    # If performance_metrics keys are base names (e.g. "ollama"), the helper needs to adapt.
    # For simplicity, let's assume performance_tracker.get_all_metrics() returns metrics keyed by specific provider names.
    # If not, we'd need to map them or adjust the helper.
    # The current `get_all_metrics` in `PerformanceTracker` returns `self._metrics` which is keyed by `provider_base/model_id`.
    # The `sort_models_by_size_performance_id` expects `provider -> model_id -> metrics`.
    # We need to adapt. For now, let's assume performance_tracker.get_all_metrics()
    # returns a dict like: { "specific_provider_name/model_id": {"score": float, ...} }
    # Or, better, let's make the sorter robust or adapt the input here.

    # Adapting performance metrics for the sorter:
    # Sorter expects: {provider_name: {model_id: {"score": ...}}}
    # PerformanceTracker stores: { "provider_base/model_id": {"score": ...} }
    # We need to make sure the sorter can look up correctly.
    # The sorter currently takes `model_info["provider"]` and `model_info["id"]` to lookup.
    # So, performance_metrics should be keyed by the specific provider name.
    # Let's assume PerformanceTracker.get_all_metrics() returns data that can be transformed or used.
    
    # Simplified: performance_tracker.get_all_metrics() returns { "provider_base/model_id": {"score": ...} }
    # We can transform this or make the sorter smarter.
    # For now, let's pass it as is and assume the sorter might need adjustment if this doesn't work.
    # OR, let the sorter take the tracker instance directly. (Future refactor)
    
    # Let's assume `get_all_metrics` in `PerformanceTracker` returns a dict keyed by provider (base or specific)
    # and then by model_id. The `sort_models_by_size_performance_id` will use `model_info['provider']`.
    
    # The `get_ranked_models` in PerformanceTracker returns a list of (provider_base, model_id, score, calls).
    # We need a way to get scores for *all* models, not just those meeting min_calls.
    # Let's assume `manager.performance_tracker.get_metrics_for_model(provider, model_id)` exists or adapt.
    # For now, we'll pass None for performance_metrics to the sorter, relying on size and ID first.
    # This is a simplification for now; a proper implementation would fetch all scores.
    
    # Correct approach: Fetch all model metrics from performance tracker
    # PerformanceTracker._metrics is provider_base/model_id -> {score, latency, ...}
    # We need to transform it for sort_models_by_size_performance_id,
    # which expects {provider_name: {model_id: {score: ...}}}
    
    all_perf_metrics_raw = manager.performance_tracker.get_all_metrics() # provider_base/model_id -> data
    # Transform all_perf_metrics_raw for the sorter:
    # The sorter uses model_info["provider"] (specific name) and model_info["id"] (suffix)
    # So, the metrics dict should be keyed by specific_provider_name.
    
    # For now, let's make the sorter handle the combined key if possible, or assume it gets individual scores.
    # The current sorter expects metrics keyed by specific provider, then model_id.
    # We will pass None for performance_metrics for now, meaning sorting will be by size then ID.
    # TODO: Properly integrate full performance data into this selection.
    # For now, the subtask is about integrating size primarily.

    # Create a dictionary for performance metrics structured as:
    # { specific_provider_name: { model_id_suffix: {"score": float, ...} } }
    # This requires iterating through all_models_from_registry and fetching scores.
    # PerformanceTracker has `get_metrics(self, provider_base: str, model_id: str)`
    
    metrics_for_sorter = {}
    for prov, model_list in all_models_from_registry.items():
        metrics_for_sorter[prov] = {}
        base_prov = prov.split("-local-")[0].split("-proxy")[0]
        for m_info in model_list:
            m_id = m_info['id']
            # Assuming performance_tracker has a method to get metrics for a specific model
            # For now, let's simulate this or assume default scores if not found
            model_perf = manager.performance_tracker.get_metrics(base_prov, m_id)
            if model_perf:
                metrics_for_sorter[prov][m_id] = model_perf
            else: # Default if no metrics
                 metrics_for_sorter[prov][m_id] = {"score": 0.0, "latency": float('inf'), "calls": 0}


    logger.debug(f"Total flattened models before sorting: {len(flattened_model_infos)}")
    # Sort all models using the new comprehensive sorter
    comprehensively_sorted_models = sort_models_by_size_performance_id(
        flattened_model_infos,
        performance_metrics=metrics_for_sorter
    )
    logger.debug(f"Total models after comprehensive sorting: {len(comprehensively_sorted_models)}")

    current_model_tier = settings.MODEL_TIER

    for model_info in comprehensively_sorted_models:
        specific_provider_name = model_info["provider"] # This is the specific name like "ollama-local-..."
        model_id_suffix = model_info["id"] # This is the suffix like "llama3"
        num_params = model_info.get("num_parameters_sortable", 0)
        perf_score = model_info.get("performance_score", 0.0)

        # Determine base provider type (e.g., "ollama", "openrouter")
        base_provider_type = specific_provider_name.split("-local-")[0].split("-proxy")[0]
        
        is_local_provider = base_provider_type in ["ollama", "litellm"]

        # Tier Check
        if current_model_tier == "LOCAL" and not is_local_provider:
            logger.debug(f"Skipping '{specific_provider_name}/{model_id_suffix}': Tier is LOCAL, model is remote.")
            continue
        
        if current_model_tier == "FREE":
            is_free_model = ":free" in model_id_suffix.lower()
            if not is_local_provider and not is_free_model:
                logger.debug(f"Skipping '{specific_provider_name}/{model_id_suffix}': Tier is FREE, model is remote and not free.")
                continue
        
        # Key/Configuration Check for remote providers
        if not is_local_provider:
            if not manager.settings.is_provider_configured(base_provider_type):
                logger.debug(f"Skipping '{specific_provider_name}/{model_id_suffix}': Remote provider '{base_provider_type}' not configured.")
                continue
            if await manager.key_manager.is_provider_depleted(base_provider_type):
                logger.debug(f"Skipping '{specific_provider_name}/{model_id_suffix}': Keys for remote provider '{base_provider_type}' depleted.")
                continue
        
        # If all checks pass, this is the best model
        logger.info(f"Automatic selection (Comprehensive Sort): Selected {specific_provider_name}/{model_id_suffix} "
                    f"(Size: {num_params}, Score: {perf_score:.2f}, Tier: {current_model_tier})")
        return specific_provider_name, model_id_suffix

    logger.error("Automatic model selection failed: No available models found after comprehensive sorting and filtering.")
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
        final_provider_for_creation = None # Store the specific provider instance name (e.g., ollama-local-...)
        final_model_canonical = None # Store the canonical model ID (e.g., ollama/...)

        logger.info(f"Lifecycle: Processing bootstrap agent '{agent_id}' configuration...")
        config_provider = final_agent_config_data.get("provider") # Base name like 'ollama'
        config_model = final_agent_config_data.get("model") # Canonical name like 'ollama/model...'
        use_config_value = False

        # --- Step 1: Attempt to use config.yaml values ---
        if config_provider and config_model:
            logger.info(f"Lifecycle: Agent '{agent_id}' defined in config.yaml: Provider='{config_provider}', Model='{config_model}'")
        # Check if base provider type is configured/discovered
        if config_provider:
            if config_provider in ["ollama", "litellm"]:
                # For local providers, check if they are discovered by ModelRegistry
                discovered_providers = list(model_registry.available_models.keys())
                matching_local_providers = [p for p in discovered_providers if p.startswith(f"{config_provider}-local-")]
                if not matching_local_providers:
                    logger.warning(f"Lifecycle: Local provider '{config_provider}' specified for agent '{agent_id}' is not discovered. Ignoring config.")
                    use_config_value = False
                else:
                    use_config_value = True
            else:
                # For remote providers, check if they are configured in .env
                provider_configured = settings.is_provider_configured(config_provider)
                if not provider_configured:
                    use_config_value = False
                else:
                # Validate format and get model suffix
                    is_local_provider_cfg = config_provider in ["ollama", "litellm"]
                    model_id_suffix = None
                    format_valid = True

                if is_local_provider_cfg:
                    if config_model.startswith(f"{config_provider}/"):
                        model_id_suffix = config_model[len(config_provider)+1:]
                    else:
                        logger.warning(f"Lifecycle: Agent '{agent_id}' model '{config_model}' in config.yaml must start with '{config_provider}/'. Ignoring config.")
                        format_valid = False
                elif config_model.startswith("ollama/") or config_model.startswith("litellm/"):
                     logger.warning(f"Lifecycle: Agent '{agent_id}' model '{config_model}' in config.yaml starts with local prefix, but provider is '{config_provider}'. Ignoring config.")
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
                            logger.warning(f"Lifecycle: Model suffix '{model_id_suffix}' (from '{config_model}') for agent '{agent_id}' not found under any discovered '{config_provider}-local-*' or '{config_provider}-proxy' providers. Ignoring config.")
                        else:
                            logger.info(f"Lifecycle: Using agent '{agent_id}' model '{model_id_suffix}' from config on discovered provider '{found_on_specific_provider}'.")
                            final_provider_for_creation = found_on_specific_provider
                            final_model_canonical = config_model # Keep original canonical name
                            use_config_value = True
                    else: # Remote provider check
                        if not model_registry.is_model_available(config_provider, model_id_suffix):
                             logger.warning(f"Lifecycle: Model '{model_id_suffix}' (from '{config_model}') specified for agent '{agent_id}' in config is not available via registry for provider '{config_provider}'. Ignoring config.")
                        else:
                             logger.info(f"Lifecycle: Using agent '{agent_id}' provider/model specified in config.yaml: {config_provider}/{config_model}")
                             final_provider_for_creation = config_provider
                             final_model_canonical = config_model
                             use_config_value = True

        # --- Step 2: Fallback to automatic selection if config wasn't valid, specified, or available ---
        if not use_config_value:
            logger.info(f"Lifecycle: Agent '{agent_id}' provider/model not specified or invalid/unavailable in config.yaml. Attempting automatic selection...")
            selected_provider, selected_model_suffix = await _select_best_available_model(manager) # Returns specific instance name for local
            selection_method = "automatic"

            if not selected_model_suffix or not selected_provider:
                logger.error(f"Lifecycle: Could not automatically select any available/configured/non-depleted model for agent '{agent_id}'! Check .env configurations and model discovery logs.")
                continue # Skip creating this agent

            final_provider_for_creation = selected_provider
            # Determine canonical model ID for storage/logging
            base_provider_type = selected_provider.split('-local-')[0].split('-proxy')[0]
            if base_provider_type in ["ollama", "litellm"]:
                final_model_canonical = f"{base_provider_type}/{selected_model_suffix}"
            else:
                final_model_canonical = selected_model_suffix
            # Update the config data that will be passed to _create_agent_internal
            final_agent_config_data["provider"] = final_provider_for_creation # Store the specific provider name used
            final_agent_config_data["model"] = final_model_canonical
            logger.info(f"Lifecycle: Automatically selected {final_model_canonical} (Provider: {final_provider_for_creation}) for agent '{agent_id}'.")
        else:
             # If using config value, ensure final_provider_for_creation and final_model_canonical are set
             if not final_provider_for_creation: final_provider_for_creation = config_provider
             if not final_model_canonical: final_model_canonical = config_model
             # Ensure the final config reflects the provider being used
             final_agent_config_data["provider"] = final_provider_for_creation
             final_agent_config_data["model"] = final_model_canonical

        # --- Step 3: Final Provider/Model Checks (Common for both paths) ---
        if not final_provider_for_creation or not final_model_canonical:
            logger.error(f"Lifecycle: Cannot initialize '{agent_id}': Final provider or model is missing after selection/validation ({selection_method}). Skipping.")
            continue

        # Check if the selected provider is actually reachable
        if final_provider_for_creation not in model_registry._reachable_providers:
             logger.error(f"Lifecycle: Cannot initialize '{agent_id}': Final provider '{final_provider_for_creation}' ({selection_method}) is not in the list of reachable providers found during discovery. Skipping.")
             continue

        # Check key depletion only for remote providers, using the base name
        base_provider_name = final_provider_for_creation.split('-local-')[0].split('-proxy')[0]
        if base_provider_name not in ["ollama", "litellm"]:
            if await manager.key_manager.is_provider_depleted(base_provider_name):
                logger.error(f"Lifecycle: Cannot initialize '{agent_id}': All keys for selected provider '{base_provider_name}' (base for '{final_provider_for_creation}', method: {selection_method}) are quarantined. Skipping.")
                continue
        else:
            logger.debug(f"Skipping key depletion check for local provider '{base_provider_name}'")
        # --- End Final Provider Checks ---

        # --- Step 4: Assemble Prompt (REMOVED Admin AI specific logic here) ---
        # Prompt assembly, including personality injection, is now handled by AgentWorkflowManager.
        # We just ensure the system_prompt field exists in the config passed to _create_agent_internal,
        # even if it's empty for Admin AI at this stage.
        if "system_prompt" not in final_agent_config_data:
             final_agent_config_data["system_prompt"] = "" # Ensure key exists
        logger.info(f"Lifecycle: Passing system prompt from config (or empty) for bootstrap agent '{agent_id}' to _create_agent_internal.")
        # --- End Prompt Assembly ---

        # --- Add logging before scheduling task ---
        logger.debug(f"Lifecycle: Preparing to schedule creation task for bootstrap agent '{agent_id}' with final config: { {k: v for k, v in final_agent_config_data.items()} }")
        # --- End added logging ---

        # Schedule agent creation task using the internal function
            # tool_desc = manager.tool_descriptions_xml # No longer needed here

            # --- Load the INITIAL Conversation Prompt for Admin AI ---
            # The CycleHandler will load the state-appropriate prompt later.
        prompt_key = "admin_ai_conversation_prompt"
        initial_conversation_prompt = settings.PROMPTS.get(prompt_key, f"--- {prompt_key} Instructions Missing ---")                
        logger.info(f"Lifecycle: Set INITIAL system prompt for '{BOOTSTRAP_AGENT_ID}' to '{prompt_key}'. State-specific prompts will be loaded by CycleHandler.")
            # --- End Initial Prompt Loading ---

            # Inject max_tokens if needed (logic remains the same, based on final provider)
        is_local_provider_selected = final_provider_for_creation.startswith("ollama-local-") or \
                                         final_provider_for_creation.startswith("litellm-local-") or \
                                         final_provider_for_creation.endswith("-proxy")
        if is_local_provider_selected:
                if "max_tokens" not in final_agent_config_data and "num_predict" not in final_agent_config_data:
                    final_agent_config_data["max_tokens"] = settings.ADMIN_AI_LOCAL_MAX_TOKENS
                    logger.info(f"Lifecycle: Injecting default max_tokens ({settings.ADMIN_AI_LOCAL_MAX_TOKENS}) for local Admin AI.")
                else:
                    logger.debug(f"Lifecycle: max_tokens/num_predict already set for local Admin AI, skipping injection.")
        else:
             # For other bootstrap agents, ensure system_prompt exists but don't modify it here
             logger.info(f"Lifecycle: Using system prompt from config for bootstrap agent '{agent_id}'.")
             if "system_prompt" not in final_agent_config_data:
                  final_agent_config_data["system_prompt"] = ""
        # --- End Prompt Assembly ---

        tasks.append(_create_agent_internal(
            manager,
            agent_id_requested=agent_id,
            agent_config_data=final_agent_config_data, # Pass potentially modified config
            is_bootstrap=True
            ))

    # Gather results
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # --- Add logging for gathered results ---
    logger.debug(f"Lifecycle: Gathered bootstrap agent creation results (Count: {len(results)}): {results}")
    # --- End added logging ---
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
    base_provider_name = provider_name.split('-local-')[0].split('-proxy')[0] if is_dynamic_local else provider_name

    if is_dynamic_local:
            # For dynamic local providers, check if they are discovered by ModelRegistry
            if not model_registry.is_provider_discovered(provider_name):
                msg = f"Lifecycle: Local provider '{provider_name}' (base for '{base_provider_name}', source: {selection_source}) not discovered by ModelRegistry."; logger.error(msg); return False, msg, None
    else:
            # For remote providers, check if they are configured in .env
            if not settings.is_provider_configured(base_provider_name):
                msg = f"Lifecycle: Provider '{base_provider_name}' (base for '{provider_name}', source: {selection_source}) not configured in .env settings."; logger.error(msg); return False, msg, None

            # Key depletion check still applies to the base provider name for remote providers
            if base_provider_name not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(base_provider_name):
                msg = f"Lifecycle: Cannot create agent '{agent_id}': All keys for '{base_provider_name}' (base for '{provider_name}', source: {selection_source}) are quarantined."; logger.error(msg); return False, msg, None

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
    # Allow empty string for system_prompt if AgentWorkflowManager will set it
    if role_specific_prompt is None: # Ensure it's at least an empty string if missing from config
        role_specific_prompt = ""

    final_system_prompt = role_specific_prompt
    if not loading_from_session and not is_bootstrap:
        logger.debug(f"Lifecycle: Constructing final prompt for dynamic agent '{agent_id}'...")
        # Use the agent_type determined earlier to get standard instructions
        from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER # Local import
        determined_agent_type = AGENT_TYPE_WORKER # Default for dynamic agents
        if agent_id == BOOTSTRAP_AGENT_ID: determined_agent_type = AGENT_TYPE_ADMIN
        elif agent_id.startswith("pm_"): determined_agent_type = AGENT_TYPE_PM
        
        standard_instr_key = manager.workflow_manager._standard_instructions_map.get(determined_agent_type, "standard_framework_instructions") # Fallback
        standard_info_template = settings.PROMPTS.get(standard_instr_key, "--- Standard Instructions Missing ---")
        
        # Address book and other context will be formatted by WorkflowManager when it sets the final prompt.
        # Here, we just ensure the `system_prompt` field (role_specific_prompt) is present.
        final_system_prompt = role_specific_prompt # For dynamic agents, this is often minimal initially.
        logger.info(f"Lifecycle: Using role-specific prompt for dynamic agent '{agent_id}'. WorkflowManager will finalize.")
    elif loading_from_session:
        final_system_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        logger.debug(f"Lifecycle: Using stored prompt for loaded agent '{agent_id}'.")
    elif is_bootstrap:
        final_system_prompt = agent_config_data.get("system_prompt", "") # Use the one from config for bootstrap (might be empty)
        logger.debug(f"Lifecycle: Using pre-assembled prompt for bootstrap agent '{agent_id}'.")

    # Prepare Provider Arguments
    final_provider_args: Dict[str, Any] = {}; api_key_used = None
    is_final_provider_local = "-local-" in provider_name or "-proxy" in provider_name

    if is_final_provider_local:
        base_url = model_registry.get_reachable_provider_url(provider_name)
        if base_url: final_provider_args['base_url'] = base_url
        base_provider_type = provider_name.split('-local-')[0].split('-proxy')[0]
        if base_provider_type in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[base_provider_type]:
            key_config = await manager.key_manager.get_active_key_config(base_provider_type)
            if key_config is None:
                msg = f"Lifecycle: Failed to get active API key for local provider '{provider_name}' (base type '{base_provider_type}') - keys might be configured but all quarantined."
                logger.error(msg); return False, msg, None
            final_provider_args.update(key_config); api_key_used = final_provider_args.get('api_key')
            logger.info(f"Using configured API key ending '...{api_key_used[-4:] if api_key_used else 'N/A'}' for local provider '{provider_name}'.")
        else:
            ProviderClassCheck = PROVIDER_CLASS_MAP.get(base_provider_type)
            if base_provider_type == 'ollama' and ProviderClassCheck == OllamaProvider: final_provider_args['api_key'] = 'ollama'
    else: # Remote provider
        final_provider_args = settings.get_provider_config(provider_name)
        key_config = await manager.key_manager.get_active_key_config(provider_name)
        if key_config is None:
            msg = f"Lifecycle: Failed to get active API key for remote provider '{provider_name}'."; logger.error(msg); return False, msg, None
        final_provider_args.update(key_config); api_key_used = final_provider_args.get('api_key')

    temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
    
    OPENAI_CLIENT_VALID_KWARGS = {"timeout", "http_client", "organization", "project"} 
    allowed_provider_keys = ['api_key', 'base_url', 'referer']
    framework_agent_config_keys = {'provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'team_id', 'plan_description', '_selection_method', 'project_name_context', 'initial_plan_description'}
    client_init_kwargs = {}; api_call_options = {} 

    for k, v in agent_config_data.items():
        if k in framework_agent_config_keys or k in allowed_provider_keys: continue 
        if base_provider_name == "ollama" and k in KNOWN_OLLAMA_OPTIONS: api_call_options[k] = v 
        elif base_provider_name in ["openai", "openrouter"] and k in OPENAI_CLIENT_VALID_KWARGS: client_init_kwargs[k] = v 
        else:
            if not (base_provider_name == "ollama" and k in KNOWN_OLLAMA_OPTIONS) and \
               not (base_provider_name in ["openai", "openrouter"] and k in OPENAI_CLIENT_VALID_KWARGS):
                logger.debug(f"Lifecycle: Kwarg '{k}' from agent_config_data not explicitly handled for client init for provider '{base_provider_name}'. Will be passed as api_call_option.")
            api_call_options[k] = v
    final_provider_args = {**final_provider_args, **client_init_kwargs}
    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

    final_agent_config_entry = {
        "agent_id": agent_id,
        "config": {
            "provider": provider_name, "model": model_id_canonical,
            "system_prompt": final_system_prompt, "persona": persona,
            "temperature": temperature, **api_call_options, **client_init_kwargs
        }
    }
    ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
    if not ProviderClass:
        base_provider_type_for_class = provider_name.split('-local')[0].split('-proxy')[0]
        ProviderClass = PROVIDER_CLASS_MAP.get(base_provider_type_for_class)
    if not ProviderClass: msg = f"Lifecycle: Unknown provider type '{provider_name}'."; logger.error(msg); return False, msg, None
    try: llm_provider_instance = ProviderClass(**final_provider_args)
    except Exception as e: msg = f"Lifecycle: Provider init failed for {provider_name}: {e}"; logger.error(msg, exc_info=True); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")

    from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER
    agent_type = AGENT_TYPE_WORKER
    if agent_id == BOOTSTRAP_AGENT_ID: agent_type = AGENT_TYPE_ADMIN
    elif agent_id.startswith("pm_"): agent_type = AGENT_TYPE_PM
    logger.debug(f"Determined agent_type for '{agent_id}' as '{agent_type}'.")
    final_agent_config_entry["config"]["agent_type"] = agent_type
    
    # Pass initial_plan_description from agent_config_data to final_agent_config_entry
    if 'initial_plan_description' in agent_config_data:
        final_agent_config_entry["config"]["initial_plan_description"] = agent_config_data['initial_plan_description']
    if 'project_name_context' in agent_config_data:
        final_agent_config_entry["config"]["project_name_context"] = agent_config_data['project_name_context']


    try:
        agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=manager)
        agent.model = model_id_for_provider
        if api_key_used: agent._last_api_key_used = api_key_used
    except Exception as e: msg = f"Lifecycle: Agent instantiation failed: {e}"; logger.error(msg, exc_info=True); await manager._close_provider_safe(llm_provider_instance); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated Agent object for '{agent_id}'.")
    
    from src.agents.constants import ADMIN_STATE_STARTUP, PM_STATE_STARTUP, WORKER_STATE_STARTUP
    initial_state_to_set = None
    if agent_type == AGENT_TYPE_ADMIN: initial_state_to_set = ADMIN_STATE_STARTUP
    elif agent_type == AGENT_TYPE_PM: initial_state_to_set = PM_STATE_STARTUP
    elif agent_type == AGENT_TYPE_WORKER: initial_state_to_set = WORKER_STATE_STARTUP
    if initial_state_to_set:
        if hasattr(manager, 'workflow_manager'):
            manager.workflow_manager.change_state(agent, initial_state_to_set)
            logger.info(f"Lifecycle: Set initial state for {agent_type} agent '{agent_id}' to '{initial_state_to_set}' via WorkflowManager.")
        else:
            logger.error("WorkflowManager not available on manager. Cannot set initial agent state.")
            agent.set_state(initial_state_to_set) # Fallback to direct set_state if WM fails

    try: await asyncio.to_thread(agent.ensure_sandbox_exists)
    except Exception as e: logger.error(f"  Lifecycle: Error ensuring sandbox for '{agent_id}': {e}", exc_info=True)
    logger.debug(f"Lifecycle: Attempting to add agent '{agent_id}' to manager.agents dictionary...")
    manager.agents[agent_id] = agent;
    logger.info(f"  Lifecycle: Successfully added agent '{agent_id}' to manager.agents dict. Current keys: {list(manager.agents.keys())}")
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
    system_prompt: str, # Modified: This can be an empty string if WM sets it later
    persona: str, # Required
    team_id: Optional[str] = None, temperature: Optional[float] = None,
    **kwargs # Accept arbitrary kwargs
    ) -> Tuple[bool, str, Optional[str]]:
    """ Creates a dynamic agent instance, allowing provider/model to be omitted for auto-selection. """
    # --- MODIFIED: Allow empty system_prompt, but persona is required ---
    if not persona: # system_prompt can be empty if WorkflowManager sets it later
        msg = "Lifecycle Error: Missing required argument 'persona' for creating dynamic agent."
        logger.error(msg); return False, msg, None
    # --- END MODIFICATION ---
    
    # Start with essential args
    agent_config_data = { "system_prompt": system_prompt, "persona": persona }
    
    # Add optional args if provided
    if provider: agent_config_data["provider"] = provider
    if model: agent_config_data["model"] = model
    if temperature is not None: agent_config_data["temperature"] = temperature
    
    # Merge any other kwargs passed (e.g., plan_description)
    agent_config_data.update(kwargs)
    
    # Call the internal creation logic
    success, message, created_agent_id = await _create_agent_internal(
        manager,
        agent_id_requested=agent_id_requested,
        agent_config_data=agent_config_data, # Pass the combined config
        is_bootstrap=False,
        team_id=team_id,
        loading_from_session=False
    )
    
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