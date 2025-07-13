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
    logger.debug("Attempting model selection using API-first Round-Robin strategy.")
    local_provider_type_preference = ["ollama", "litellm"]

    for base_provider_type in local_provider_type_preference:
        specific_instances_list = manager.available_local_providers_list.get(base_provider_type)
        if not specific_instances_list:
            continue

        rr_index_to_use: int
        if current_rr_indices_override and base_provider_type in current_rr_indices_override:
            rr_index_to_use = current_rr_indices_override[base_provider_type]
        else:
            rr_index_to_use = manager.local_api_usage_round_robin_index.get(base_provider_type, 0)

        logger.debug(f"Attempting to select model from {len(specific_instances_list)} instances of base type '{base_provider_type}'.")

        for i in range(len(specific_instances_list)):
            current_instance_list_idx = (rr_index_to_use + i) % len(specific_instances_list)
            chosen_specific_instance = specific_instances_list[current_instance_list_idx]

            current_model_tier = settings.MODEL_TIER
            is_chosen_instance_local = chosen_specific_instance.startswith(base_provider_type + "-local") or chosen_specific_instance.endswith("-proxy")
            if current_model_tier == "LOCAL" and not is_chosen_instance_local:
                continue

            if base_provider_type in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[base_provider_type]:
                if await manager.key_manager.is_provider_depleted(base_provider_type):
                    continue

            models_on_this_instance_infos = manager.model_registry.available_models.get(chosen_specific_instance)
            if not models_on_this_instance_infos:
                continue

            def get_sort_key(model_dict):
                num_params = model_dict.get("num_parameters_sortable", model_dict.get("num_parameters", 0))
                if not isinstance(num_params, (int, float)):
                    num_params = 0
                return (-num_params, model_dict.get("id", ""))

            sorted_models_on_instance = sorted(models_on_this_instance_infos, key=get_sort_key)

            if not sorted_models_on_instance:
                continue

            selected_model_info = sorted_models_on_instance[0]
            selected_model_id_suffix = selected_model_info.get("id")

            if selected_model_id_suffix:
                logger.info(f"Auto-selected model via API-first RR: {chosen_specific_instance}/{selected_model_id_suffix}")
                return chosen_specific_instance, selected_model_id_suffix, base_provider_type, current_instance_list_idx

    logger.info("API-first Round-Robin failed. Falling back to Comprehensive Selection.")

    all_models_from_registry: Dict[str, List[Dict[str, Any]]] = manager.model_registry.get_available_models_dict()
    if not all_models_from_registry:
        logger.warning("No models available in the registry for comprehensive selection.")
        return None, None, None, None

    flattened_model_infos: List[Dict[str, Any]] = []
    for specific_provider_name_comp, models_list_comp in all_models_from_registry.items():
        for model_data_comp in models_list_comp:
            model_info_copy_comp = model_data_comp.copy()
            model_info_copy_comp["provider"] = specific_provider_name_comp
            flattened_model_infos.append(model_info_copy_comp)
            
    if not flattened_model_infos:
        logger.warning("No models found after flattening registry data for comprehensive selection.")
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
    logger.debug(f"Total models after comprehensive sorting: {len(comprehensively_sorted_models)}")

    current_model_tier = settings.MODEL_TIER

    for model_info in comprehensively_sorted_models:
        specific_provider_name = model_info["provider"]
        model_id_suffix = model_info["id"]
        num_params = model_info.get("num_parameters_sortable", 0)
        perf_score = model_info.get("performance_score", 0.0)

        base_provider_type_comp = specific_provider_name.split("-local-")[0].split("-proxy")[0]
        is_provider_local_comp = base_provider_type_comp in ["ollama", "litellm"]

        if current_model_tier == "LOCAL" and not is_provider_local_comp:
            continue
        
        if current_model_tier == "FREE":
            is_free_model = ":free" in model_id_suffix.lower()
            if not is_provider_local_comp and not is_free_model:
                continue

        if not is_provider_local_comp:
            if not settings.is_provider_configured(base_provider_type_comp):
                continue
            if await manager.key_manager.is_provider_depleted(base_provider_type_comp):
                continue
        else:
             if base_provider_type_comp in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[base_provider_type_comp]:
                 if await manager.key_manager.is_provider_depleted(base_provider_type_comp):
                    continue
        
        if not manager.model_registry.is_model_available(specific_provider_name, model_id_suffix):
            continue

        logger.info(f"Auto-selected model via Comprehensive Fallback: {specific_provider_name}/{model_id_suffix} (Size: {num_params}, Score: {perf_score:.2f}, Tier: {current_model_tier})")
        return specific_provider_name, model_id_suffix, None, None

    logger.error("Automatic model selection failed completely.")
    return None, None, None, None
# --- END Automatic Model Selection Logic ---


# --- initialize_bootstrap_agents (Ensured full code included) ---
async def initialize_bootstrap_agents(manager: 'AgentManager'):
    """ Initializes bootstrap agents defined in settings. """
    logger.info("Initializing bootstrap agents...")
    agent_configs_list = settings.AGENT_CONFIGURATIONS
    if not agent_configs_list:
        logger.warning("No bootstrap agent configurations found.")
        return

    main_sandbox_dir = BASE_DIR / "sandboxes"
    try:
        await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create main sandboxes directory {main_sandbox_dir}: {e}")

    tasks = []
    formatted_available_models = model_registry.get_formatted_available_models()
    logger.debug(f"Available models for Admin AI prompt: {formatted_available_models}")

    current_bootstrap_rr_indices: Dict[str, int] = {}

    for agent_conf_entry in agent_configs_list:
        agent_id = agent_conf_entry.get("agent_id")
        if not agent_id:
            logger.warning("Skipping bootstrap agent due to missing 'agent_id'.")
            continue

        agent_config_data = agent_conf_entry.get("config", {})
        final_agent_config_data = agent_config_data.copy()
        selection_method = "config.yaml"
        final_provider_for_creation = None
        final_model_canonical = None

        logger.info(f"Processing bootstrap agent '{agent_id}'...")
        config_provider = final_agent_config_data.get("provider")
        config_model = final_agent_config_data.get("model")
        use_config_value = False

        if config_provider and config_model:
            logger.info(f"Agent '{agent_id}' configured with Provider='{config_provider}', Model='{config_model}'")

            if config_provider in ["ollama", "litellm"]:
                specific_local_provider_list = manager.available_local_providers_list.get(config_provider)
                if specific_local_provider_list and len(specific_local_provider_list) > 0:
                    model_suffix_from_config = config_model.split('/')[-1]
                    if config_provider not in current_bootstrap_rr_indices:
                        current_bootstrap_rr_indices[config_provider] = manager.local_api_usage_round_robin_index.get(config_provider, 0)
                    round_robin_start_index = current_bootstrap_rr_indices[config_provider]

                    for i in range(len(specific_local_provider_list)):
                        current_attempt_index = (round_robin_start_index + i) % len(specific_local_provider_list)
                        candidate_specific_provider = specific_local_provider_list[current_attempt_index]
                        if manager.model_registry.is_model_available(candidate_specific_provider, model_suffix_from_config):
                            final_provider_for_creation = candidate_specific_provider
                            final_model_canonical = f"{config_provider}/{model_suffix_from_config}"
                            current_bootstrap_rr_indices[config_provider] = (current_attempt_index + 1) % len(specific_local_provider_list)
                            use_config_value = True
                            selection_method = f"config.yaml (round-robin to {candidate_specific_provider})"
                            logger.info(f"Mapped generic '{config_provider}/{model_suffix_from_config}' to specific '{final_provider_for_creation}/{model_suffix_from_config}' for agent '{agent_id}'.")
                            break
                    if not use_config_value:
                        logger.warning(f"Model '{model_suffix_from_config}' not found on any specific instances of '{config_provider}'. Agent '{agent_id}' will fallback to auto-selection.")
                else:
                    logger.warning(f"Generic local provider '{config_provider}' specified for agent '{agent_id}', but no specific instances found. Fallback to auto-selection.")

            elif not use_config_value:
                provider_configured = settings.is_provider_configured(config_provider)
                if not provider_configured:
                    logger.warning(f"Remote provider '{config_provider}' for agent '{agent_id}' not configured. Ignoring config.")
                else:
                    model_id_suffix_remote = config_model
                    if not model_registry.is_model_available(config_provider, model_id_suffix_remote):
                        logger.warning(f"Model '{model_id_suffix_remote}' for agent '{agent_id}' not available for provider '{config_provider}'. Ignoring config.")
                    else:
                        logger.info(f"Using remote provider/model for agent '{agent_id}': {config_provider}/{config_model}")
                        final_provider_for_creation = config_provider
                        final_model_canonical = config_model
                        use_config_value = True

        if not use_config_value:
            logger.info(f"Auto-selecting model for agent '{agent_id}'...")
            selected_provider, selected_model_suffix, rr_base_type, rr_idx_chosen = await _select_best_available_model(manager, current_bootstrap_rr_indices)
            selection_method = "automatic"

            if rr_base_type and rr_idx_chosen is not None:
                specific_local_provider_list_for_update = manager.available_local_providers_list.get(rr_base_type)
                if specific_local_provider_list_for_update and len(specific_local_provider_list_for_update) > 0:
                    if rr_base_type not in current_bootstrap_rr_indices:
                         current_bootstrap_rr_indices[rr_base_type] = manager.local_api_usage_round_robin_index.get(rr_base_type, 0)
                    current_bootstrap_rr_indices[rr_base_type] = (rr_idx_chosen + 1) % len(specific_local_provider_list_for_update)
                    logger.info(f"Updated local bootstrap index for '{rr_base_type}' to {current_bootstrap_rr_indices[rr_base_type]}.")

            if not selected_model_suffix or not selected_provider:
                logger.error(f"Could not auto-select model for agent '{agent_id}'. Skipping.")
                continue

            final_provider_for_creation = selected_provider
            base_provider_type_fallback = selected_provider.split('-local-')[0].split('-proxy')[0]
            if base_provider_type_fallback in ["ollama", "litellm"]:
                final_model_canonical = f"{base_provider_type_fallback}/{selected_model_suffix}"
            else:
                final_model_canonical = selected_model_suffix
            final_agent_config_data["provider"] = final_provider_for_creation
            final_agent_config_data["model"] = final_model_canonical
            logger.info(f"Auto-selected {final_model_canonical} (Provider: {final_provider_for_creation}) for agent '{agent_id}'.")
        else:
             if not final_provider_for_creation: final_provider_for_creation = config_provider
             if not final_model_canonical: final_model_canonical = config_model
             final_agent_config_data["provider"] = final_provider_for_creation
             final_agent_config_data["model"] = final_model_canonical

        if not final_provider_for_creation or not final_model_canonical:
            logger.error(f"Cannot initialize '{agent_id}': Final provider or model is missing. Skipping.")
            continue

        if final_provider_for_creation not in model_registry._reachable_providers:
             logger.error(f"Cannot initialize '{agent_id}': Final provider '{final_provider_for_creation}' is not reachable. Skipping.")
             continue

        base_provider_name_check = final_provider_for_creation.split('-local-')[0].split('-proxy')[0]
        if base_provider_name_check not in ["ollama", "litellm"]:
            if await manager.key_manager.is_provider_depleted(base_provider_name_check):
                logger.error(f"Cannot initialize '{agent_id}': Keys for provider '{base_provider_name_check}' are quarantined. Skipping.")
                continue

        if "system_prompt" not in final_agent_config_data:
             final_agent_config_data["system_prompt"] = ""
        logger.info(f"Passing system prompt for bootstrap agent '{agent_id}' to internal creation.")

        tasks.append(_create_agent_internal(
            manager,
            agent_id_requested=agent_id,
            agent_config_data=final_agent_config_data,
            is_bootstrap=True
            ))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    successful_ids = []
    num_expected_tasks = len([cfg for cfg in agent_configs_list if cfg.get("agent_id")])
    if len(results) != num_expected_tasks:
        logger.error(f"Mismatch in expected bootstrap tasks. Expected: {num_expected_tasks}, Got: {len(results)}.")
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
                             logger.info(f"Bootstrap agent '{created_agent_id}' initialized.")

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
                                 logger.info(f"UI updated for bootstrap agent '{created_agent_id}'.")
                             else:
                                 logger.warning(f"Could not retrieve agent '{created_agent_id}' after creation for UI update.")
                         else:
                             logger.warning(f"Bootstrap agent '{created_agent_id}' already initialized. Skipping duplicate.")
                     else:
                         logger.error(f"Failed bootstrap init for '{original_agent_id_attempted}': {result[1]} (No agent ID returned).")
                 elif isinstance(result, Exception):
                     logger.error(f"Failed bootstrap init for '{original_agent_id_attempted}': {result}", exc_info=result)
                 else:
                     error_msg = result[1] if isinstance(result, tuple) and len(result) > 1 else str(result)
                     logger.error(f"Failed bootstrap init for '{original_agent_id_attempted}': {error_msg}")
             except Exception as gather_err:
                 logger.error(f"Error processing bootstrap result for '{original_agent_id_attempted}': {gather_err}", exc_info=True)

    for base_type, final_index_value in current_bootstrap_rr_indices.items():
        if base_type in manager.local_api_usage_round_robin_index or final_index_value != manager.local_api_usage_round_robin_index.get(base_type, 0):
             manager.local_api_usage_round_robin_index[base_type] = final_index_value
             logger.info(f"Updated global round-robin index for '{base_type}' to {final_index_value}.")

    logger.info(f"Finished bootstrap initialization. Active agents: {successful_ids}")
    if BOOTSTRAP_AGENT_ID not in manager.agents: logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize!")
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
        msg = f"Agent ID '{agent_id_requested}' already exists."
        logger.error(msg); return False, msg, None
    elif agent_id_requested: agent_id = agent_id_requested
    else: agent_id = _generate_unique_agent_id(manager)

    if not agent_id: return False, "Failed to determine Agent ID.", None

    logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")

    provider_name = agent_config_data.get("provider")
    model_id_canonical = agent_config_data.get("model")
    persona = agent_config_data.get("persona")
    selection_source = "specified" if not is_bootstrap else agent_config_data.get("_selection_method", "specified")

    if not persona:
         msg = f"Missing persona for agent '{agent_id}'."
         logger.error(msg); return False, msg, None

    specific_provider_name_resolved = provider_name
    model_suffix_for_check = model_id_canonical

    if provider_name in ["ollama", "litellm"] and model_id_canonical and not is_bootstrap:
        logger.info(f"Mapping generic local provider '{provider_name}' for dynamic agent '{agent_id}'.")
        model_suffix_for_check = model_id_canonical.split('/')[-1]
        specific_instances = manager.available_local_providers_list.get(provider_name, [])
        found_specific_instance = False
        if specific_instances:
            rr_key = f"{provider_name}_dynamic_pm_assignment"
            start_index = manager.local_api_usage_round_robin_index.get(rr_key, 0)
            for i in range(len(specific_instances)):
                current_instance_idx = (start_index + i) % len(specific_instances)
                candidate_specific_provider = specific_instances[current_instance_idx]
                if model_registry.is_model_available(candidate_specific_provider, model_suffix_for_check):
                    specific_provider_name_resolved = candidate_specific_provider
                    manager.local_api_usage_round_robin_index[rr_key] = (current_instance_idx + 1) % len(specific_instances)
                    logger.info(f"Mapped generic '{provider_name}/{model_suffix_for_check}' to specific '{specific_provider_name_resolved}/{model_suffix_for_check}' for agent '{agent_id}'.")
                    found_specific_instance = True
                    break
            if not found_specific_instance:
                logger.warning(f"Model '{model_suffix_for_check}' not found on any specific instances of '{provider_name}'.")
        else:
            logger.warning(f"Generic local provider '{provider_name}' specified, but no specific instances discovered.")
        if found_specific_instance:
            provider_name = specific_provider_name_resolved
            agent_config_data["provider"] = provider_name

    if not provider_name or not model_id_canonical:
        if is_bootstrap:
             msg = f"Bootstrap agent '{agent_id}' missing provider/model."
             logger.critical(msg); return False, msg, None
        logger.info(f"Auto-selecting model for dynamic agent '{agent_id}'.")
        selected_provider, selected_model_suffix, rr_base_type, rr_idx_chosen = await _select_best_available_model(manager)
        selection_source = "automatic"
        if not selected_provider or not selected_model_suffix:
            msg = f"Auto-selection failed for agent '{agent_id}'."
            logger.error(msg); return False, msg, None
        provider_name = selected_provider
        base_provider_type_sel = provider_name.split('-local-')[0].split('-proxy')[0]
        if base_provider_type_sel in ["ollama", "litellm"]:
             model_id_canonical = f"{base_provider_type_sel}/{selected_model_suffix}"
        else:
             model_id_canonical = selected_model_suffix
        agent_config_data["provider"] = provider_name
        agent_config_data["model"] = model_id_canonical
        logger.info(f"Auto-selected {model_id_canonical} (Provider: {provider_name}) for agent '{agent_id}'.")

    error_prefix = f"Lifecycle Error ({selection_source} model '{model_id_canonical}' for provider '{provider_name}'):"
    if not provider_name or not model_id_canonical:
         msg = f"{error_prefix} Missing final provider or model.";
         logger.error(msg); return False, msg, None

    base_provider_name_val = _get_base_provider_type_for_class_lookup(provider_name)
    if base_provider_name_val in ["ollama", "litellm"]:
        if not model_registry.is_provider_discovered(provider_name):
            msg = f"{error_prefix} Local provider '{provider_name}' not discovered.";
            logger.error(msg); return False, msg, None
    else:
        if not settings.is_provider_configured(base_provider_name_val):
            msg = f"{error_prefix} Provider '{base_provider_name_val}' not configured.";
            logger.error(msg); return False, msg, None
        if await manager.key_manager.is_provider_depleted(base_provider_name_val):
            msg = f"{error_prefix} Keys for '{base_provider_name_val}' are quarantined.";
            logger.error(msg); return False, msg, None

    model_id_for_provider = model_id_canonical.split('/')[-1]
    if not model_registry.is_model_available(provider_name, model_id_for_provider):
        available_list_str = ", ".join(model_registry.get_available_models_list(provider=provider_name)) or "(None)"
        msg = f"{error_prefix} Model '{model_id_for_provider}' not available for provider '{provider_name}'. Available: [{available_list_str}]"
        logger.error(msg); return False, msg, None

    logger.info(f"Final model validated: Provider='{provider_name}', Model='{model_id_for_provider}'.")

    role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
    if not role_specific_prompt: role_specific_prompt = ""

    final_system_prompt = manager.workflow_manager.get_system_prompt(
        Agent(agent_config={"config": {"agent_type": agent_id.split('_')[0]}}, llm_provider=None, manager=manager), manager
    ) if not loading_from_session and not is_bootstrap else role_specific_prompt

    final_provider_args: Dict[str, Any] = {}; api_key_used = None
    final_base_provider_name_for_args = _get_base_provider_type_for_class_lookup(provider_name)

    if provider_name.startswith(("ollama-local", "litellm-local")) or provider_name.endswith("-proxy"):
        base_url = model_registry.get_reachable_provider_url(provider_name)
        if base_url: final_provider_args['base_url'] = base_url
        if final_base_provider_name_for_args in settings.PROVIDER_API_KEYS and settings.PROVIDER_API_KEYS[final_base_provider_name_for_args]:
            key_config = await manager.key_manager.get_active_key_config(final_base_provider_name_for_args)
            if key_config:
                final_provider_args.update(key_config)
                api_key_used = key_config.get('api_key')
                logger.info(f"Using API key for local provider '{provider_name}'.")
    else:
        final_provider_args = settings.get_provider_config(final_base_provider_name_for_args)
        key_config = await manager.key_manager.get_active_key_config(final_base_provider_name_for_args)
        if key_config:
            final_provider_args.update(key_config)
            api_key_used = key_config.get('api_key')

    temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
    
    allowed_provider_keys = ['api_key', 'base_url', 'referer']
    framework_agent_config_keys = {'provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'team_id', 'plan_description', '_selection_method', 'project_name_context', 'initial_plan_description'}
    client_init_kwargs = {}; api_call_options = {}
    for k, v in agent_config_data.items():
        if k in framework_agent_config_keys or k in allowed_provider_keys: continue
        if final_base_provider_name_for_args == "ollama" and k in KNOWN_OLLAMA_OPTIONS: api_call_options[k] = v
        elif final_base_provider_name_for_args in ["openai", "openrouter"] and k in {"timeout", "http_client", "organization", "project"}: client_init_kwargs[k] = v
        else: api_call_options[k] = v

    final_provider_args.update(client_init_kwargs)
    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

    final_config_for_agent_object = {
        "provider": provider_name, "model": model_id_canonical, "system_prompt": final_system_prompt,
        "persona": persona, "temperature": temperature, **api_call_options
    }
    agent_type = "worker"
    if agent_id == BOOTSTRAP_AGENT_ID: agent_type = "admin"
    elif agent_id.startswith("pm_"): agent_type = "pm"
    final_config_for_agent_object["agent_type"] = agent_type

    if 'initial_plan_description' in agent_config_data:
        final_config_for_agent_object["initial_plan_description"] = agent_config_data['initial_plan_description']
    if 'project_name_context' in agent_config_data:
        final_config_for_agent_object["project_name_context"] = agent_config_data['project_name_context']
    if '_selection_method' in agent_config_data:
        final_config_for_agent_object["_selection_method"] = agent_config_data['_selection_method']

    final_agent_config_entry = {"agent_id": agent_id, "config": final_config_for_agent_object}

    base_name_for_class_lookup = _get_base_provider_type_for_class_lookup(provider_name)
    ProviderClass = PROVIDER_CLASS_MAP.get(base_name_for_class_lookup)
    if not ProviderClass:
        msg = f"Unknown provider base type '{base_name_for_class_lookup}'."
        logger.error(msg)
        return False, msg, None

    logger.info(f"Instantiating provider {ProviderClass.__name__} for '{agent_id}'.")
    try:
        llm_provider_instance = ProviderClass(**final_provider_args)
    except Exception as e:
        msg = f"Provider init failed for {base_name_for_class_lookup}: {e}"; logger.error(msg, exc_info=True); return False, msg, None

    try:
        agent = Agent(agent_config=final_agent_config_entry, llm_provider=llm_provider_instance, manager=manager)
        agent.model = model_id_for_provider
        if api_key_used: agent._last_api_key_used = api_key_used
    except Exception as e:
        msg = f"Agent instantiation failed: {e}"; logger.error(msg, exc_info=True);
        await manager._close_provider_safe(llm_provider_instance); return False, msg, None
    logger.info(f"Instantiated Agent object for '{agent_id}'.")
    
    initial_state_to_set = {"admin": "startup", "pm": "startup", "worker": "startup"}.get(agent.agent_type)
    if initial_state_to_set:
        manager.workflow_manager.change_state(agent, initial_state_to_set)
        logger.info(f"Set initial state for agent '{agent_id}' to '{initial_state_to_set}'.")

    try: await asyncio.to_thread(agent.ensure_sandbox_exists)
    except Exception as e: logger.error(f"Error ensuring sandbox for '{agent_id}': {e}", exc_info=True)

    manager.agents[agent_id] = agent
    logger.info(f"Agent '{agent_id}' added to manager.agents.")

    if team_id:
        await manager.state_manager.add_agent_to_team(agent_id, team_id)

    message = f"Agent '{agent_id}' ({persona}) created successfully with {model_id_canonical}."
    return True, message, agent_id
# --- END _create_agent_internal ---


# --- create_agent_instance (Ensured full code included) ---
async def create_agent_instance(
    manager: 'AgentManager',
    agent_id_requested: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    system_prompt: str,
    persona: str,
    team_id: Optional[str] = None, temperature: Optional[float] = None,
    **kwargs
    ) -> Tuple[bool, str, Optional[str]]:
    """ Creates a dynamic agent instance. """
    if not persona:
        msg = "Missing required 'persona' for dynamic agent creation."
        logger.error(msg); return False, msg, None
    
    agent_config_data = {"system_prompt": system_prompt, "persona": persona}
    if provider: agent_config_data["provider"] = provider
    if model: agent_config_data["model"] = model
    if temperature is not None: agent_config_data["temperature"] = temperature
    agent_config_data.update(kwargs)
    
    success, message, created_agent_id = await _create_agent_internal(
        manager, agent_id_requested=agent_id_requested, agent_config_data=agent_config_data,
        is_bootstrap=False, team_id=team_id, loading_from_session=False
    )
    
    if success and created_agent_id:
        agent = manager.agents.get(created_agent_id)
        team = manager.state_manager.get_agent_team(created_agent_id)
        config_ui = agent.agent_config.get("config", {}) if agent else {}
        await manager.send_to_ui({"type": "agent_added", "agent_id": created_agent_id, "config": config_ui, "team": team})
        await manager.push_agent_status_update(created_agent_id)
    return success, message, created_agent_id
# --- END create_agent_instance ---


# --- delete_agent_instance (Ensured full code included) ---
async def delete_agent_instance(manager: 'AgentManager', agent_id: str) -> Tuple[bool, str]:
    """ Deletes a dynamic agent instance. """
    if not agent_id: return False, "Agent ID cannot be empty."
    if agent_id not in manager.agents: return False, f"Agent '{agent_id}' not found."
    if agent_id in manager.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."
    agent_instance = manager.agents.pop(agent_id, None)
    manager.state_manager.remove_agent_from_all_teams_state(agent_id)
    if agent_instance and agent_instance.llm_provider: await manager._close_provider_safe(agent_instance.llm_provider)
    message = f"Agent '{agent_id}' deleted."; logger.info(message)
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