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

# --- Helper function to determine base provider type for class lookup ---
def _get_base_provider_type_for_class_lookup(specific_provider_name: str) -> str:
    """
    Determines the base provider type from a specific provider name.
    This is used for looking up the provider class in PROVIDER_CLASS_MAP.
    Handles "ollama-local", "ollama-local-IP", "ollama-proxy" -> "ollama"
    Handles "litellm-local", "litellm-local-IP", "litellm-proxy" -> "litellm"
    Defaults to returning the original name for direct mappings like "openai".
    """
    if specific_provider_name.startswith("ollama-local") or specific_provider_name == "ollama-proxy":
        return "ollama"
    if specific_provider_name.startswith("litellm-local") or specific_provider_name == "litellm-proxy":
        return "litellm"
    # Add other known base types if they have dynamic naming conventions, e.g.
    # if specific_provider_name.startswith("someother-local"):
    #     return "someother"
    return specific_provider_name # Default for direct names like "openai", "openrouter"
# --- END Helper function ---

# --- Automatic Model Selection Logic ---
async def _select_best_available_model( # Added current_rr_indices_override to signature
    manager: 'AgentManager',
    current_rr_indices_override: Optional[Dict[str, int]] = None
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
    """
    Selects the best available and usable model.
    Prioritizes an "API-first Round-Robin" strategy for local providers.
    If that fails, falls back to a comprehensive sort of all available models.
    Respects MODEL_TIER and provider key availability.

    Args:
        manager: The AgentManager instance.
        current_rr_indices_override: Optional dictionary to override starting RR indices
                                     for specific base types during a sequence of selections (e.g., bootstrap).

    Returns:
        Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
          (specific_provider_name, model_id_suffix, base_provider_type_if_rr_local, index_used_in_list_if_rr_local)
          The last two elements are None if selection was not API-first RR for a local provider.
    """
    logger.info("Attempting model selection: API-first Round-Robin strategy...")
    local_provider_type_preference = ["ollama", "litellm"]

    for base_provider_type in local_provider_type_preference:
        logger.debug(f"API-first RR: Accessing manager.available_local_providers_list. Current content: {manager.available_local_providers_list}")
        specific_instances_list = manager.available_local_providers_list.get(base_provider_type)
        logger.debug(f"API-first RR: For base_provider_type '{base_provider_type}', retrieved specific_instances_list: {specific_instances_list}")
        if not specific_instances_list:
            logger.debug(f"API-first RR: No specific instances found for base type '{base_provider_type}'. Skipping.")
            continue

        rr_index_to_use: int
        if current_rr_indices_override and base_provider_type in current_rr_indices_override:
            rr_index_to_use = current_rr_indices_override[base_provider_type]
            logger.debug(f"API-first RR: Using overridden start index {rr_index_to_use} for base type '{base_provider_type}'.")
        else:
            rr_index_to_use = manager.local_api_usage_round_robin_index.get(base_provider_type, 0)
            logger.debug(f"API-first RR: Using global start index {rr_index_to_use} for base type '{base_provider_type}'.")

        logger.debug(f"API-first RR: Trying base type '{base_provider_type}' with {len(specific_instances_list)} instance(s), starting attempt loop with index {rr_index_to_use}.")

        for i in range(len(specific_instances_list)):
            current_instance_list_idx = (rr_index_to_use + i) % len(specific_instances_list)
            chosen_specific_instance = specific_instances_list[current_instance_list_idx]

            logger.debug(f"API-first RR: Attempting instance '{chosen_specific_instance}' (list index {current_instance_list_idx}) for base '{base_provider_type}'.")

            current_model_tier = settings.MODEL_TIER
            is_chosen_instance_local = chosen_specific_instance.startswith(base_provider_type + "-local") or chosen_specific_instance.endswith("-proxy")
            if current_model_tier == "LOCAL" and not is_chosen_instance_local:
                logger.debug(f"API-first RR: Instance '{chosen_specific_instance}' skipped. Tier is LOCAL, but instance doesn't appear local.")
                continue

            if base_provider_type in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[base_provider_type]:
                if await manager.key_manager.is_provider_depleted(base_provider_type):
                    logger.debug(f"API-first RR: Keys for base provider '{base_provider_type}' (for instance '{chosen_specific_instance}') depleted. Skipping this instance.")
                    continue

            models_on_this_instance_infos = manager.model_registry.available_models.get(chosen_specific_instance)
            if not models_on_this_instance_infos:
                logger.debug(f"API-first RR: No models listed for instance '{chosen_specific_instance}'. Skipping.")
                continue

            def get_sort_key(model_dict):
                num_params = model_dict.get("num_parameters_sortable", model_dict.get("num_parameters", 0))
                if not isinstance(num_params, (int, float)):
                    num_params = 0
                return (-num_params, model_dict.get("id", ""))

            sorted_models_on_instance = sorted(models_on_this_instance_infos, key=get_sort_key)

            if not sorted_models_on_instance:
                logger.debug(f"API-first RR: No models left after sorting for instance '{chosen_specific_instance}'. Skipping.")
                continue

            selected_model_info = sorted_models_on_instance[0]
            selected_model_id_suffix = selected_model_info.get("id")

            if selected_model_id_suffix:
                if current_model_tier == "FREE":
                    # is_free_model = ":free" in selected_model_id_suffix.lower()
                    # Local models are assumed fine for FREE tier in API-first.
                    pass

                # DO NOT update global manager.local_api_usage_round_robin_index here.
                # Return the base_provider_type and current_instance_list_idx instead.
                logger.info(f"Automatic selection (API-first RR): Tentatively selected {chosen_specific_instance}/{selected_model_id_suffix} "
                            f"(Base: {base_provider_type}, List Index Used: {current_instance_list_idx})")
                return chosen_specific_instance, selected_model_id_suffix, base_provider_type, current_instance_list_idx
            else:
                logger.warning(f"API-first RR: Top sorted model on '{chosen_specific_instance}' missing 'id'. Data: {selected_model_info}")

        logger.debug(f"API-first RR: No suitable model found on any instance of base type '{base_provider_type}' after checking all {len(specific_instances_list)} instances.")

    logger.info("API-first Round-Robin strategy did not yield a model. Falling back to Comprehensive Selection...")

    all_models_from_registry: Dict[str, List[Dict[str, Any]]] = manager.model_registry.get_available_models_dict()
    if not all_models_from_registry:
        logger.warning("Comprehensive Fallback: No models available in the registry.")
        return None, None, None, None

    flattened_model_infos: List[Dict[str, Any]] = []
    for specific_provider_name_comp, models_list_comp in all_models_from_registry.items():
        for model_data_comp in models_list_comp:
            model_info_copy_comp = model_data_comp.copy()
            model_info_copy_comp["provider"] = specific_provider_name_comp
            flattened_model_infos.append(model_info_copy_comp)
            
    if not flattened_model_infos:
        logger.warning("Comprehensive Fallback: No models found after flattening registry data.")
        return None, None, None, None

    all_perf_metrics_raw = manager.performance_tracker.get_metrics()
    metrics_for_sorter = {}
    for prov, model_list in all_models_from_registry.items():
        metrics_for_sorter[prov] = {}
        base_prov = prov.split("-local-")[0].split("-proxy")[0]
        for m_info in model_list:
            m_id = m_info['id']
            model_perf = all_perf_metrics_raw.get(base_prov, {}).get(m_id)
            if model_perf:
                metrics_for_sorter[prov][m_id] = model_perf
            else:
                metrics_for_sorter[prov][m_id] = {"score": 0.0, "latency": float('inf'), "calls": 0, "success_count": 0, "failure_count": 0, "total_duration_ms": 0.0}

    comprehensively_sorted_models = sort_models_by_size_performance_id(
        flattened_model_infos,
        performance_metrics=metrics_for_sorter
    )
    logger.debug(f"Comprehensive Fallback: Total models after sorting: {len(comprehensively_sorted_models)}")

    current_model_tier = settings.MODEL_TIER

    for model_info in comprehensively_sorted_models:
        specific_provider_name = model_info["provider"]
        model_id_suffix = model_info["id"]
        num_params = model_info.get("num_parameters_sortable", 0)
        perf_score = model_info.get("performance_score", 0.0)

        base_provider_type_comp = specific_provider_name.split("-local-")[0].split("-proxy")[0]
        is_provider_local_comp = base_provider_type_comp in ["ollama", "litellm"]

        if current_model_tier == "LOCAL" and not is_provider_local_comp:
            logger.debug(f"Comprehensive Fallback: Skipping '{specific_provider_name}/{model_id_suffix}': Tier is LOCAL, provider is remote.")
            continue
        
        if current_model_tier == "FREE":
            is_free_model = ":free" in model_id_suffix.lower()
            if not is_provider_local_comp and not is_free_model:
                logger.debug(f"Comprehensive Fallback: Skipping '{specific_provider_name}/{model_id_suffix}': Tier is FREE, provider is remote and model not free.")
                continue

        if not is_provider_local_comp:
            if not settings.is_provider_configured(base_provider_type_comp):
                logger.debug(f"Comprehensive Fallback: Skipping '{specific_provider_name}/{model_id_suffix}': Remote provider '{base_provider_type_comp}' not configured.")
                continue
            if await manager.key_manager.is_provider_depleted(base_provider_type_comp):
                logger.debug(f"Comprehensive Fallback: Skipping '{specific_provider_name}/{model_id_suffix}': Keys for remote provider '{base_provider_type_comp}' depleted.")
                continue
        else:
             if base_provider_type_comp in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[base_provider_type_comp]:
                 if await manager.key_manager.is_provider_depleted(base_provider_type_comp):
                    logger.debug(f"Comprehensive Fallback: Skipping local '{specific_provider_name}/{model_id_suffix}': Keys for base provider '{base_provider_type_comp}' depleted.")
                    continue
        
        if not manager.model_registry.is_model_available(specific_provider_name, model_id_suffix):
            logger.debug(f"Comprehensive Fallback: Model '{model_id_suffix}' not available on specific provider '{specific_provider_name}'. Skipping.")
            continue

        logger.info(f"Automatic selection (Comprehensive Fallback): Selected {specific_provider_name}/{model_id_suffix} "
                    f"(Size: {num_params}, Score: {perf_score:.2f}, Tier: {current_model_tier})")
        return specific_provider_name, model_id_suffix, None, None # Not an API-first RR local selection

    logger.error("Automatic model selection failed: No available models found after API-first RR and Comprehensive Fallback.")
    return None, None, None, None
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

    current_bootstrap_rr_indices: Dict[str, int] = {} # Initialize local RR index tracker

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

        if config_provider and config_model:
            logger.info(f"Lifecycle: Agent '{agent_id}' defined in config.yaml: Provider='{config_provider}', Model='{config_model}'")

            # --- New Logic for Generic Local Provider Round-Robin ---
            if config_provider in ["ollama", "litellm"]:
                logger.info(f"Lifecycle: Config provider '{config_provider}' is a generic local type. Attempting round-robin mapping.")
                specific_local_provider_list = manager.available_local_providers_list.get(config_provider)

                if specific_local_provider_list and len(specific_local_provider_list) > 0:
                    model_suffix_from_config = None
                    if config_model:
                        if config_model.startswith(f"{config_provider}/"):
                            model_suffix_from_config = config_model[len(config_provider)+1:]
                        else:
                            # If no prefix, assume the whole string is the suffix.
                            # This might be risky if users put "ollama/model" as provider and "model" as model.
                            # The recommended way is "ollama" as provider and "ollama/model" or "model" as model.
                            model_suffix_from_config = config_model
                            logger.warning(f"Lifecycle: Model '{config_model}' for local provider '{config_provider}' does not have expected prefix '{config_provider}/'. Using full string as suffix for matching: '{model_suffix_from_config}'.")


                    if model_suffix_from_config:
                        # Use and update local RR index for bootstrap sequence
                        if config_provider not in current_bootstrap_rr_indices:
                            current_bootstrap_rr_indices[config_provider] = manager.local_api_usage_round_robin_index.get(config_provider, 0)
                        round_robin_start_index = current_bootstrap_rr_indices[config_provider]

                        for i in range(len(specific_local_provider_list)):
                            current_attempt_index = (round_robin_start_index + i) % len(specific_local_provider_list)
                            candidate_specific_provider = specific_local_provider_list[current_attempt_index]

                            if manager.model_registry.is_model_available(candidate_specific_provider, model_suffix_from_config):
                                final_provider_for_creation = candidate_specific_provider
                                # Ensure final_model_canonical has the prefix
                                if not config_model.startswith(f"{config_provider}/"):
                                    final_model_canonical = f"{config_provider}/{model_suffix_from_config}"
                                else:
                                    final_model_canonical = config_model

                                # Update local tracker immediately for next agent in this bootstrap sequence
                                current_bootstrap_rr_indices[config_provider] = (current_attempt_index + 1) % len(specific_local_provider_list)
                                use_config_value = True
                                selection_method = f"config.yaml (round-robin to {candidate_specific_provider})"
                                logger.info(f"Lifecycle: Mapped generic '{config_provider}/{model_suffix_from_config}' to specific '{final_provider_for_creation}/{model_suffix_from_config}' via round-robin for agent '{agent_id}'. Next RR index for '{config_provider}' in this bootstrap: {current_bootstrap_rr_indices[config_provider]}")
                                break # Found a suitable specific provider
                        if not use_config_value:
                            logger.warning(f"Lifecycle: Model '{model_suffix_from_config}' for generic provider '{config_provider}' not found on any specific instances: {specific_local_provider_list}. Agent '{agent_id}' will fallback to auto-selection if enabled.")
                    else:
                        logger.warning(f"Lifecycle: Could not determine model suffix from '{config_model}' for generic provider '{config_provider}'. Agent '{agent_id}' will fallback to auto-selection.")
                else:
                    logger.warning(f"Lifecycle: Generic local provider '{config_provider}' specified for agent '{agent_id}', but no specific instances found in `available_local_providers_list`. Fallback to auto-selection.")
            # --- End New Logic for Generic Local Provider Round-Robin ---

            # --- Existing Logic for Remote Providers or Specific Local (if new logic didn't set use_config_value) ---
            if not use_config_value and config_provider not in ["ollama", "litellm"]: # Only run this for remote or if local RR failed
                provider_configured = settings.is_provider_configured(config_provider)
                if not provider_configured:
                    logger.warning(f"Lifecycle: Remote provider '{config_provider}' for agent '{agent_id}' not configured in .env. Ignoring config.")
                    # use_config_value remains False
                else:
                    # Validate format and get model suffix (this part is mostly for remote)
                    model_id_suffix_remote = None
                    if config_model.startswith("ollama/") or config_model.startswith("litellm/"):
                        logger.warning(f"Lifecycle: Agent '{agent_id}' model '{config_model}' in config.yaml starts with local prefix, but provider is remote '{config_provider}'. Ignoring config.")
                        # use_config_value remains False
                    else: # Remote provider
                        model_id_suffix_remote = config_model # Use full name for check
                        if not model_registry.is_model_available(config_provider, model_id_suffix_remote):
                            logger.warning(f"Lifecycle: Model '{model_id_suffix_remote}' (from '{config_model}') specified for agent '{agent_id}' in config is not available via registry for (remote) provider '{config_provider}'. Ignoring config.")
                            # use_config_value remains False
                        else:
                            logger.info(f"Lifecycle: Using agent '{agent_id}' (remote) provider/model specified in config.yaml: {config_provider}/{config_model}")
                            final_provider_for_creation = config_provider
                            final_model_canonical = config_model
                            use_config_value = True
            elif not use_config_value and config_provider in ["ollama", "litellm"]:
                 logger.info(f"Lifecycle: Round-robin for generic local provider '{config_provider}' did not yield a usable model for agent '{agent_id}'. Will attempt auto-selection.")

        # --- Fallback to automatic selection if config wasn't valid, specified, or available ---
        if not use_config_value: # This flag is True if either generic local RR succeeded or remote direct config succeeded
            logger.info(f"Lifecycle: Agent '{agent_id}' provider/model not specified or invalid/unavailable in config.yaml. Attempting automatic selection...")
            # Pass current_bootstrap_rr_indices to _select_best_available_model
            selected_provider, selected_model_suffix, rr_base_type, rr_idx_chosen = await _select_best_available_model(
                manager,
                current_rr_indices_override=current_bootstrap_rr_indices
            )
            selection_method = "automatic"

            if rr_base_type and rr_idx_chosen is not None: # API-first RR was successful for a local provider
                specific_local_provider_list_for_update = manager.available_local_providers_list.get(rr_base_type)
                if specific_local_provider_list_for_update and len(specific_local_provider_list_for_update) > 0:
                    # Ensure the index for this base type is initialized in the local bootstrap tracker
                    if rr_base_type not in current_bootstrap_rr_indices:
                         current_bootstrap_rr_indices[rr_base_type] = manager.local_api_usage_round_robin_index.get(rr_base_type, 0) # Initialize if needed

                    # Update local bootstrap index based on what _select_best_available_model returned and used
                    current_bootstrap_rr_indices[rr_base_type] = (rr_idx_chosen + 1) % len(specific_local_provider_list_for_update)
                    logger.info(f"Lifecycle: Auto-selection for agent '{agent_id}' used API-first RR. Updated local bootstrap index for '{rr_base_type}' to {current_bootstrap_rr_indices[rr_base_type]} based on returned index {rr_idx_chosen}.")
                else:
                    logger.warning(f"Lifecycle: Auto-selection for agent '{agent_id}' returned RR info for '{rr_base_type}', but no specific provider list found to update local bootstrap index.")

            if not selected_model_suffix or not selected_provider:
                logger.error(f"Lifecycle: Could not automatically select any available/configured/non-depleted model for agent '{agent_id}'! Check .env configurations and model discovery logs.")
                continue # Skip creating this agent

            final_provider_for_creation = selected_provider
            # Determine canonical model ID for storage/logging
            base_provider_type_fallback = selected_provider.split('-local-')[0].split('-proxy')[0]
            if base_provider_type_fallback in ["ollama", "litellm"]:
                final_model_canonical = f"{base_provider_type_fallback}/{selected_model_suffix}"
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
        base_provider_name_check = final_provider_for_creation.split('-local-')[0].split('-proxy')[0]
        if base_provider_name_check not in ["ollama", "litellm"]:
            if await manager.key_manager.is_provider_depleted(base_provider_name_check):
                logger.error(f"Lifecycle: Cannot initialize '{agent_id}': All keys for selected provider '{base_provider_name_check}' (base for '{final_provider_for_creation}', method: {selection_method}) are quarantined. Skipping.")
                continue
        else:
            logger.debug(f"Skipping key depletion check for local provider '{base_provider_name_check}'")
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
                    final_agent_config_data["max_tokens"] = settings.ADMIN_AI_LOCAL_MAX_TOKENS # This was specific to Admin AI, but now applies to any local bootstrap
                    logger.info(f"Lifecycle: Injecting default max_tokens ({settings.ADMIN_AI_LOCAL_MAX_TOKENS}) for local bootstrap agent '{agent_id}'.")
                else:
                    logger.debug(f"Lifecycle: max_tokens/num_predict already set for local bootstrap agent '{agent_id}', skipping injection.")

        # Corrected Logging for initial prompt:
        # This now correctly reflects the agent_id being processed and the nature of its initial prompt.
        log_prompt_info = final_agent_config_data.get('system_prompt', '')
        if not log_prompt_info:
            logger.info(f"Lifecycle: Initial system prompt for bootstrap agent '{agent_id}' is empty. WorkflowManager will set the state-specific prompt.")
        else:
            logger.info(f"Lifecycle: Initial system prompt for bootstrap agent '{agent_id}' from config is being passed. WorkflowManager will set the state-specific prompt.")
        # --- End Prompt Assembly / Corrected Logging ---

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
                 if isinstance(result, tuple) and result[0]: # Agent creation reported success
                     created_agent_id = result[2]
                     if created_agent_id: # Agent ID was returned
                         if created_agent_id not in manager.bootstrap_agents:
                             manager.bootstrap_agents.append(created_agent_id)
                             successful_ids.append(created_agent_id)
                             logger.info(f"--- Lifecycle: Bootstrap agent '{created_agent_id}' initialized. ---")

                             # Send agent_added message to UI
                             agent = manager.agents.get(created_agent_id)
                             if agent:
                                 config_ui = agent.agent_config.get("config", {})
                                 team = manager.state_manager.get_agent_team(created_agent_id)
                                 await manager.send_to_ui({
                                     "type": "agent_added",
                                     "agent_id": created_agent_id,
                                     "config": config_ui,
                                     "team": team,
                                     "status": agent.get_state()
                                 })
                                 await manager.push_agent_status_update(created_agent_id)
                                 logger.info(f"Lifecycle: Sent agent_added and pushed status_update for bootstrap agent '{created_agent_id}'.")
                             else:
                                 logger.warning(f"Lifecycle: Could not retrieve agent '{created_agent_id}' after creation to send agent_added UI message.")
                         else: # This else pairs with "if created_agent_id not in manager.bootstrap_agents"
                             logger.warning(f"Lifecycle: Bootstrap agent '{created_agent_id}' appears to be already initialized. Skipping duplicate add.")
                     else: # This else pairs with "if created_agent_id:" (i.e., creation succeeded but no agent_id was in result[2])
                         logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result[1]} (Success reported but no agent ID in result tuple index 2?) ---")
                 elif isinstance(result, Exception): # Agent creation task raised an exception
                     logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {result} ---", exc_info=result)
                 else: # Agent creation task returned a failure tuple (e.g., (False, "some error", None))
                     error_msg = result[1] if isinstance(result, tuple) and len(result) > 1 else str(result)
                     logger.error(f"--- Lifecycle: Failed bootstrap init '{original_agent_id_attempted}': {error_msg} ---")
             except Exception as gather_err: # Catch-all for unexpected issues processing the result itself
                 logger.error(f"Lifecycle: Unexpected error processing bootstrap result for '{original_agent_id_attempted}': {gather_err}", exc_info=True)

    # Update global round-robin indices from the local tracker after all bootstrap agents are processed
    for base_type, final_index_value in current_bootstrap_rr_indices.items():
        # Check if the base_type was actually used and index changed, or if it's a new base_type for global
        # This avoids overwriting global if no agent of this base_type was round-robined during this bootstrap
        if base_type in manager.local_api_usage_round_robin_index or final_index_value != manager.local_api_usage_round_robin_index.get(base_type, 0):
             manager.local_api_usage_round_robin_index[base_type] = final_index_value
             logger.info(f"Lifecycle: Updated global round-robin index for '{base_type}' to {final_index_value} after bootstrap sequence.")

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

    # --- START: Generic Local Provider Mapping for Dynamic Agents ---
    specific_provider_name_resolved = provider_name # Will be updated if generic is resolved
    model_suffix_for_check = model_id_canonical # Will be updated if prefix is part of canonical

    if provider_name in ["ollama", "litellm"] and model_id_canonical and not is_bootstrap: # Generic local provider specified in config for a dynamic agent
        logger.info(f"Lifecycle: Generic local provider '{provider_name}' with model '{model_id_canonical}' specified for dynamic agent '{agent_id}'. Attempting to map to specific instance.")

        # Determine potential model suffix
        if model_id_canonical.startswith(f"{provider_name}/"):
            model_suffix_for_check = model_id_canonical[len(provider_name)+1:]
        else:
            model_suffix_for_check = model_id_canonical # Assume it's already a suffix

        specific_instances = manager.available_local_providers_list.get(provider_name, [])
        found_specific_instance = False
        if specific_instances:
            # Prefer round-robin for fairness if multiple instances have the model
            rr_key = f"{provider_name}_dynamic_pm_assignment" # Unique key for this round robin
            start_index = manager.local_api_usage_round_robin_index.get(rr_key, 0)

            for i in range(len(specific_instances)):
                current_instance_idx = (start_index + i) % len(specific_instances)
                candidate_specific_provider = specific_instances[current_instance_idx]

                # Check if model_suffix_for_check is available on this candidate_specific_provider
                if model_registry.is_model_available(candidate_specific_provider, model_suffix_for_check):
                    specific_provider_name_resolved = candidate_specific_provider
                    # Update global round-robin index for this specific use case
                    manager.local_api_usage_round_robin_index[rr_key] = (current_instance_idx + 1) % len(specific_instances)
                    logger.info(f"Lifecycle: Mapped generic '{provider_name}/{model_suffix_for_check}' to specific '{specific_provider_name_resolved}/{model_suffix_for_check}' for dynamic agent '{agent_id}'.")
                    found_specific_instance = True
                    break
            if not found_specific_instance:
                logger.warning(f"Lifecycle: Model '{model_suffix_for_check}' not found on any specific instances of '{provider_name}': {specific_instances}. Will proceed with original generic provider name which might lead to auto-selection or failure.")
                # Let it fall through to auto-selection or fail later if generic 'ollama' can't be directly used
        else:
            logger.warning(f"Lifecycle: Generic local provider '{provider_name}' specified, but no specific instances discovered. Will proceed with original generic provider name.")

        # Update provider_name and model_id_canonical if successfully resolved for direct use
        if found_specific_instance:
            provider_name = specific_provider_name_resolved # Now it's specific, e.g., "ollama-local-..."
            # model_id_canonical should already be correct or its suffix model_suffix_for_check is what we need
            # For is_model_available, we need the suffix. For the Agent object, the canonical form.
            # Ensure agent_config_data reflects this resolved specific provider
            agent_config_data["provider"] = provider_name
            # model_id_canonical in agent_config_data should already be the canonical one.
            # The model_id_for_provider (suffix) will be derived again later based on the now specific provider_name.
    # --- END: Generic Local Provider Mapping ---

    # Auto-selection only for non-bootstrap agents if needed
    if not provider_name or not model_id_canonical:
        if is_bootstrap:
             # This case should ideally not be reached due to checks in initialize_bootstrap_agents
             msg = f"Lifecycle Error: Bootstrap agent '{agent_id}' reached _create_agent_internal without provider/model."
             logger.critical(msg); return False, msg, None
        logger.info(f"Lifecycle: Provider or model not specified for dynamic agent '{agent_id}'. Attempting automatic selection...")
        # Correctly unpack all four values returned by _select_best_available_model
        # Note: current_rr_indices_override is NOT passed here for dynamic agents, so _select_best_available_model
        # will use the global manager.local_api_usage_round_robin_index.
        selected_provider, selected_model_suffix, rr_base_type, rr_idx_chosen = await _select_best_available_model(manager)
        selection_source = "automatic"

        logger.debug(f"Dynamic agent auto-selection in _create_agent_internal: _select_best_available_model returned provider='{selected_provider}', model_suffix='{selected_model_suffix}', rr_base_type='{rr_base_type}', rr_idx_chosen='{rr_idx_chosen}'")

        # If API-first RR was used by _select_best_available_model for a dynamic agent, it would have updated the global index itself.
        # No special handling of rr_base_type or rr_idx_chosen is needed here in _create_agent_internal for dynamic agents.

        if not selected_provider or not selected_model_suffix:
            msg = f"Lifecycle Error: Automatic model selection failed for agent '{agent_id}'. No suitable model found."
            logger.error(msg); return False, msg, None
        provider_name = selected_provider # This is the specific instance name (e.g., ollama-local-...)
        # Construct canonical ID carefully, avoid double prefix
        base_provider_type_sel = provider_name.split('-local-')[0].split('-proxy')[0]
        if base_provider_type_sel in ["ollama", "litellm"]:
             if selected_model_suffix.startswith(f"{base_provider_type_sel}/"):
                  model_id_canonical = selected_model_suffix
                  logger.warning(f"Auto-selected model suffix '{selected_model_suffix}' unexpectedly contained prefix for provider '{provider_name}'. Using suffix directly.")
             else:
                  model_id_canonical = f"{base_provider_type_sel}/{selected_model_suffix}" # Add prefix
        else: # Remote provider
             model_id_canonical = selected_model_suffix # Use suffix directly
        # Update config data passed to this function
        agent_config_data["provider"] = provider_name
        agent_config_data["model"] = model_id_canonical
        logger.info(f"Lifecycle: Automatically selected {model_id_canonical} (Provider: {provider_name}) for agent '{agent_id}'.")
    # --- End Model/Provider Handling ---


    # --- Provider/Model Validation ---
    # Define error_prefix HERE, after provider_name, model_id_canonical, and selection_source are set
    error_prefix = f"Lifecycle Error ({selection_source} model '{model_id_canonical}' for provider '{provider_name}'):"

    if not provider_name or not model_id_canonical: # This check might be redundant if auto-selection above guarantees these.
         msg = f"{error_prefix} Missing final provider or model after all selection attempts.";
         logger.error(msg); return False, msg, None

    base_provider_name_val = _get_base_provider_type_for_class_lookup(provider_name)


    if base_provider_name_val in ["ollama", "litellm"]: # This refers to generic base types or specific instances mapped to them
            # For specific local instances (e.g. ollama-local-ip), provider_name is specific.
            # For generic "ollama" that might not have resolved, this check is still relevant.
            if not model_registry.is_provider_discovered(provider_name): # Check specific instance if provider_name is specific, or generic if not
                msg = f"{error_prefix} Local provider type/instance '{provider_name}' not discovered/verified by ModelRegistry.";
                logger.error(msg); return False, msg, None
    else: # Non-dynamic specific name (e.g. "openai", "openrouter")
            if not settings.is_provider_configured(base_provider_name_val): # Check base config like "openai"
                msg = f"{error_prefix} Provider '{base_provider_name_val}' not configured in .env settings.";
                logger.error(msg); return False, msg, None

            if base_provider_name_val not in ["ollama", "litellm"] and await manager.key_manager.is_provider_depleted(base_provider_name_val):
                msg = f"{error_prefix} All keys for '{base_provider_name_val}' are quarantined.";
                logger.error(msg); return False, msg, None

    model_id_for_provider = None
    validation_passed = True
    error_msg_val = None

    # provider_name here is now specific_provider_name_resolved if mapping occurred
    is_definitely_local_type_after_resolution = provider_name.startswith("ollama-local") or \
                                              provider_name == "ollama-proxy" or \
                                              provider_name.startswith("litellm-local") or \
                                              provider_name == "litellm-proxy"

    if is_definitely_local_type_after_resolution:
        base_type = _get_base_provider_type_for_class_lookup(provider_name) # e.g., "ollama"
        # model_id_canonical is from config, e.g., "gemma3:4b-it-q4_K_M" or "ollama/gemma3:4b-it-q4_K_M"
        if model_id_canonical.startswith(f"{base_type}/"):
            model_id_for_provider = model_id_canonical[len(base_type)+1:]
        else:
            model_id_for_provider = model_id_canonical # Assume it's already a suffix
    else: # Remote or unmapped generic (though unmapped generic should ideally not reach here if validation is strict)
        if model_id_canonical.startswith("ollama/") or model_id_canonical.startswith("litellm/"):
             error_msg_val = f"{error_prefix} Remote model ID '{model_id_canonical}' (for provider '{provider_name}') should not start with a local provider prefix."
             logger.error(error_msg_val)
             validation_passed = False
             model_id_for_provider = None
        else:
             model_id_for_provider = model_id_canonical

    if not validation_passed: # This check should use the error_msg_val set above
        final_error_message = error_msg_val if error_msg_val else f"{error_prefix} Model ID validation failed for an unspecified reason."
        return False, final_error_message, None

    if not model_id_for_provider: # If model_id_for_provider is None after logic above (e.g. due to error)
        if not error_msg_val: # Ensure there's an error message if not already set
             error_msg_val = f"{error_prefix} Could not determine model suffix for provider '{provider_name}' from canonical ID '{model_id_canonical}'."
        logger.error(error_msg_val)
        return False, error_msg_val, None

    if not model_registry.is_model_available(provider_name, model_id_for_provider): # Uses specific provider_name
        available_list_str = ", ".join(model_registry.get_available_models_list(provider=provider_name)) or "(None available)"
        msg = f"{error_prefix} Model suffix '{model_id_for_provider}' not available for specific provider '{provider_name}'. Available: [{available_list_str}]"
        logger.error(msg); return False, msg, None
    # --- End Provider/Model Validation ---

    logger.info(f"Lifecycle: Final model validated: Provider='{provider_name}', Model='{model_id_for_provider}'. Canonical stored: '{model_id_canonical}'.")

    # Assemble System Prompt
    role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
    if role_specific_prompt is None:
        role_specific_prompt = ""

    final_system_prompt = role_specific_prompt
    if not loading_from_session and not is_bootstrap:
        logger.debug(f"Lifecycle: Constructing final prompt for dynamic agent '{agent_id}'...")
        from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER
        determined_agent_type = AGENT_TYPE_WORKER
        if agent_id == BOOTSTRAP_AGENT_ID: determined_agent_type = AGENT_TYPE_ADMIN
        elif agent_id.startswith("pm_"): determined_agent_type = AGENT_TYPE_PM
        
        standard_instr_key = manager.workflow_manager._standard_instructions_map.get(determined_agent_type, "standard_framework_instructions")
        standard_info_template = settings.PROMPTS.get(standard_instr_key, "--- Standard Instructions Missing ---")
        
        final_system_prompt = role_specific_prompt
        logger.info(f"Lifecycle: Using role-specific prompt for dynamic agent '{agent_id}'. WorkflowManager will finalize.")
    elif loading_from_session:
        final_system_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        logger.debug(f"Lifecycle: Using stored prompt for loaded agent '{agent_id}'.")
    elif is_bootstrap:
        final_system_prompt = agent_config_data.get("system_prompt", "")
        logger.debug(f"Lifecycle: Using pre-assembled prompt for bootstrap agent '{agent_id}'.")

    # Prepare Provider Arguments
    final_provider_args: Dict[str, Any] = {}; api_key_used = None
    # Use final_base_provider_name for some arg lookups, but provider_name for direct URL if specific.
    final_base_provider_name_for_args = _get_base_provider_type_for_class_lookup(provider_name)


    if is_definitely_local_type_after_resolution: # Use the more accurate local check
        base_url = model_registry.get_reachable_provider_url(provider_name) # provider_name is specific here
        if base_url: final_provider_args['base_url'] = base_url

        if final_base_provider_name_for_args in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[final_base_provider_name_for_args]:
            key_config = await manager.key_manager.get_active_key_config(final_base_provider_name_for_args)
            if key_config is None:
                msg = f"{error_prefix} Failed to get active API key for local provider '{provider_name}' (base type '{final_base_provider_name_for_args}') - keys might be configured but all quarantined."
                logger.error(msg); return False, msg, None
            final_provider_args.update(key_config); api_key_used = final_provider_args.get('api_key')
            logger.info(f"Using configured API key ending '...{api_key_used[-4:] if api_key_used else 'N/A'}' for local provider '{provider_name}'.")
        else:
            ProviderClassCheck = PROVIDER_CLASS_MAP.get(final_base_provider_name_for_args)
            if final_base_provider_name_for_args == 'ollama' and ProviderClassCheck == OllamaProvider: final_provider_args['api_key'] = 'ollama'
    else: # Remote provider
        final_provider_args = settings.get_provider_config(final_base_provider_name_for_args) # Use base name like "openai"
        key_config = await manager.key_manager.get_active_key_config(final_base_provider_name_for_args)
        if key_config is None:
            msg = f"{error_prefix} Failed to get active API key for remote provider '{final_base_provider_name_for_args}'.";
            logger.error(msg); return False, msg, None
        final_provider_args.update(key_config); api_key_used = final_provider_args.get('api_key')

    temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
    
    OPENAI_CLIENT_VALID_KWARGS = {"timeout", "http_client", "organization", "project"} 
    allowed_provider_keys = ['api_key', 'base_url', 'referer']
    framework_agent_config_keys = {'provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'team_id', 'plan_description', '_selection_method', 'project_name_context', 'initial_plan_description'}

    client_init_kwargs = {}; api_call_options = {} 

    for k, v in agent_config_data.items():
        if k in framework_agent_config_keys or k in allowed_provider_keys: continue 
        if final_base_provider_name_for_args == "ollama" and k in KNOWN_OLLAMA_OPTIONS: api_call_options[k] = v
        elif final_base_provider_name_for_args in ["openai", "openrouter"] and k in OPENAI_CLIENT_VALID_KWARGS: client_init_kwargs[k] = v
        else:
            if not (final_base_provider_name_for_args == "ollama" and k in KNOWN_OLLAMA_OPTIONS) and \
               not (final_base_provider_name_for_args in ["openai", "openrouter"] and k in OPENAI_CLIENT_VALID_KWARGS):
                logger.debug(f"Lifecycle: Kwarg '{k}' from agent_config_data not explicitly handled for client init for provider '{final_base_provider_name_for_args}'. Will be passed as api_call_option.")
            api_call_options[k] = v

    final_provider_args = {**final_provider_args, **client_init_kwargs}
    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

    final_config_for_agent_object = {
        "provider": provider_name,
        "model": model_id_canonical,
        "system_prompt": final_system_prompt,
        "persona": persona,
        "temperature": temperature,
        **api_call_options,
    }
    from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER
    agent_type = AGENT_TYPE_WORKER
    if agent_id == BOOTSTRAP_AGENT_ID: agent_type = AGENT_TYPE_ADMIN
    elif agent_id.startswith("pm_"): agent_type = AGENT_TYPE_PM
    final_config_for_agent_object["agent_type"] = agent_type

    if 'initial_plan_description' in agent_config_data:
        final_config_for_agent_object["initial_plan_description"] = agent_config_data['initial_plan_description']
    if 'project_name_context' in agent_config_data:
        final_config_for_agent_object["project_name_context"] = agent_config_data['project_name_context']
    if '_selection_method' in agent_config_data:
        final_config_for_agent_object["_selection_method"] = agent_config_data['_selection_method']


    final_agent_config_entry = {
        "agent_id": agent_id,
        "config": final_config_for_agent_object
    }

    base_name_for_class_lookup = _get_base_provider_type_for_class_lookup(provider_name)
    ProviderClass = PROVIDER_CLASS_MAP.get(base_name_for_class_lookup)

    if not ProviderClass:
        msg = f"Lifecycle: Unknown provider base type '{base_name_for_class_lookup}' (derived from specific provider name '{provider_name}') for class lookup in PROVIDER_CLASS_MAP."
        logger.error(msg)
        return False, msg, None

    logger.info(f"  Lifecycle: Determined ProviderClass '{ProviderClass.__name__}' for specific provider '{provider_name}' using base type '{base_name_for_class_lookup}'.")

    try:
        llm_provider_instance = ProviderClass(**final_provider_args)
    except Exception as e:
        msg = f"Lifecycle: Provider init failed for {base_name_for_class_lookup} (specific: {provider_name}): {e}"; logger.error(msg, exc_info=True); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")

    try:
        agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=manager)
        agent.model = model_id_for_provider
        if api_key_used: agent._last_api_key_used = api_key_used
    except Exception as e:
        msg = f"Lifecycle: Agent instantiation failed: {e}"; logger.error(msg, exc_info=True);
        await manager._close_provider_safe(llm_provider_instance); return False, msg, None
    logger.info(f"  Lifecycle: Instantiated Agent object for '{agent_id}'.")
    
    from src.agents.constants import ADMIN_STATE_STARTUP, PM_STATE_STARTUP, WORKER_STATE_STARTUP
    initial_state_to_set = None
    if agent.agent_type == AGENT_TYPE_ADMIN: initial_state_to_set = ADMIN_STATE_STARTUP
    elif agent.agent_type == AGENT_TYPE_PM: initial_state_to_set = PM_STATE_STARTUP
    elif agent.agent_type == AGENT_TYPE_WORKER: initial_state_to_set = WORKER_STATE_STARTUP

    if initial_state_to_set:
        if hasattr(manager, 'workflow_manager'):
            manager.workflow_manager.change_state(agent, initial_state_to_set)
            logger.info(f"Lifecycle: Set initial state for {agent.agent_type} agent '{agent_id}' to '{initial_state_to_set}' via WorkflowManager.")
        else:
            logger.error("WorkflowManager not available on manager. Cannot set initial agent state.")
            agent.set_state(initial_state_to_set)

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

    message = f"Agent '{agent_id}' ({persona}) created successfully using {model_id_canonical} (Provider: {provider_name}, Source: {selection_source})." + team_add_msg_suffix
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
# END OF FILE src/agents/agent_lifecycle.py