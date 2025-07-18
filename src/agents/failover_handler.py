# START OF FILE src/agents/failover_handler.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set, TYPE_CHECKING
import logging
import time
import openai # Import openai exceptions
import aiohttp # Import aiohttp exceptions for provider-level check
import random # For selecting alternates if no performance data

# --- NEW: Import status and error constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR,
    KEY_RELATED_ERRORS, KEY_RELATED_STATUS_CODES, # Import error constants
    RETRYABLE_EXCEPTIONS # Add this import
)
# --- END NEW ---

# Import base Agent class for type hinting
from src.agents.core import Agent

# Import necessary components from other modules
from src.llm_providers.base import BaseLLMProvider
from src.config.settings import settings, model_registry # Import settings and registry
from src.agents.agent_lifecycle import PROVIDER_CLASS_MAP # Import map ONLY
from src.agents.agent_utils import sort_models_by_size_performance_id # Import the new sorter
from src.config.model_registry import ModelInfo # For type hinting

# Type hint AgentManager
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Define provider-level errors (connection, timeout, etc.)
PROVIDER_LEVEL_ERRORS = (
    aiohttp.ClientConnectorError,
    asyncio.TimeoutError,
    openai.APIConnectionError,
    # Add others if needed, e.g., certain auth errors affecting the whole provider
)

# Key-related errors imported from constants


# --- NEW: Helper Function to Check Provider Health ---
async def _check_provider_health(base_url: str, timeout: int = 3) -> bool:
    """Performs a quick health check on the provider's base URL."""
    if not base_url:
        return False
    check_url = base_url.rstrip('/') + "/" # Check root path
    logger.debug(f"Performing health check on: {check_url}")
    try:
        async with aiohttp.ClientSession() as session:
            # Use a HEAD request for efficiency, fallback to GET if needed
            async with session.head(check_url, timeout=timeout, allow_redirects=False) as response:
                # Consider any 2xx or 3xx status as reachable for basic check
                if 200 <= response.status < 400:
                    logger.debug(f"Health check successful for {check_url} (Status: {response.status})")
                    return True
                else:
                    # Try GET if HEAD failed or gave non-success status
                    logger.debug(f"HEAD request failed ({response.status}), trying GET for {check_url}")
                    async with session.get(check_url, timeout=timeout) as get_response:
                         if 200 <= get_response.status < 400:
                              logger.debug(f"Health check successful for {check_url} via GET (Status: {get_response.status})")
                              return True
                         else:
                              logger.warning(f"Health check failed for {check_url}. Status: {get_response.status}")
                              return False
    except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as conn_err:
        logger.warning(f"Health check connection failed for {check_url}: {conn_err}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during health check for {check_url}: {e}", exc_info=True)
        return False # Assume failed on unexpected error


# --- Helper Function to Select Alternate Models ---
async def _select_alternate_models(
    manager: 'AgentManager',
    provider: str,
    original_model: str,
    tried_models_on_key: Set[str],
    max_alternates: int = 3
) -> List[str]:
    """Selects up to max_alternates different models from the same provider."""
    logger.debug(f"Selecting alternate models for provider '{provider}', original: '{original_model}', tried: {tried_models_on_key}, max: {max_alternates}")
    
    candidate_model_infos: List[ModelInfo] = []
    all_provider_models_from_registry = model_registry.get_available_models_dict().get(provider, [])

    for model_data in all_provider_models_from_registry:
        model_id = model_data.get("id")
        if not model_id:
            continue

        if model_id == original_model:
            logger.debug(f"Skipping '{model_id}' for alternates: same as original_model.")
            continue
        if model_id in tried_models_on_key:
            logger.debug(f"Skipping '{model_id}' for alternates: already in tried_models_on_key.")
            continue

        # Tier check
        tier_compatible = True
        if settings.MODEL_TIER == "FREE" and provider == "openrouter":
            if ":free" not in model_id.lower():
                logger.debug(f"Skipping '{model_id}' for alternates: Does not match FREE tier for openrouter.")
                tier_compatible = False
        # Add other provider/tier specific checks if necessary for 'FREE' tier
        # elif settings.MODEL_TIER == "FREE" and provider == "some_other_provider":
        #     if "free_indicator" not in model_id.lower(): tier_compatible = False
        
        if tier_compatible:
            # Ensure 'provider' key is in model_data for the sorter
            model_data_copy = model_data.copy()
            model_data_copy["provider"] = provider # The specific provider name
            candidate_model_infos.append(model_data_copy)
        else:
            logger.debug(f"Skipping '{model_id}' due to tier incompatibility with '{settings.MODEL_TIER}'.")

    if not candidate_model_infos:
        logger.info(f"No suitable candidate models found for provider '{provider}' after initial filtering.")
        return []

    # Fetch performance metrics for the specific provider
    # The sorter expects metrics format: {provider_name: {model_id: {"score": ...}}}
    # PerformanceTracker stores by "base_provider/model_id"
    # We need to prepare metrics for the sorter for the *specific provider instance*.
    provider_metrics = {}
    base_provider_name = provider.split("-local-")[0].split("-proxy")[0] # Get base name like "ollama"
    
    all_metrics = manager.performance_tracker.get_all_metrics()
    provider_specific_metrics_for_sorter = {}

    for model_info_dict in candidate_model_infos:
        model_id_suffix = model_info_dict["id"]
        # Construct the key as used in PerformanceTracker
        # However, performance_tracker.get_metrics expects base_provider and model_id suffix.
        metrics = manager.performance_tracker.get_metrics(base_provider_name, model_id_suffix)
        if metrics:
            if provider not in provider_specific_metrics_for_sorter:
                provider_specific_metrics_for_sorter[provider] = {}
            provider_specific_metrics_for_sorter[provider][model_id_suffix] = metrics
        else: # Ensure a default entry if no metrics found, so sorter doesn't break
            if provider not in provider_specific_metrics_for_sorter:
                provider_specific_metrics_for_sorter[provider] = {}
            provider_specific_metrics_for_sorter[provider][model_id_suffix] = {"score": 0.0, "latency": float('inf'), "calls": 0}


    logger.debug(f"Candidate models for provider '{provider}' before sorting: {[m['id'] for m in candidate_model_infos]}")
    
    sorted_model_infos = sort_models_by_size_performance_id(
        candidate_model_infos,
        performance_metrics=provider_specific_metrics_for_sorter
    )
    
    logger.debug(f"Sorted models for provider '{provider}': {[m['id'] for m in sorted_model_infos]}")

    # Extract just the model IDs for the result, up to max_alternates
    alternates = [model_info["id"] for model_info in sorted_model_infos[:max_alternates]]
    
    logger.info(f"Selected {len(alternates)} alternates for provider '{provider}': {alternates} (sorted by size, perf, id)")
    return alternates

# --- Helper Function to Attempt Switching Agent ---
async def _try_switch_agent(
    manager: 'AgentManager',
    agent: Agent,
    target_provider: str,
    target_model: str,
    api_key_config: Optional[Dict[str, Any]] = None # Pass key config for remote
) -> bool:
    """Attempts to switch the agent to a new provider/model/key config."""
    agent_id = agent.agent_id
    current_provider = agent.provider_name
    current_model = agent.model
    # Use plain model name for UI message, but full provider/model for internal logs
    ui_status_model_name = target_model
    internal_log_target_name = f"{target_provider}/{target_model}"

    logger.info(f"Attempting switch for Agent '{agent_id}': Target='{internal_log_target_name}'")
    # Send clearer message to UI
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Trying model '{ui_status_model_name}' on provider '{target_provider}'..."})

    old_provider_instance = agent.llm_provider
    new_provider_instance = None
    api_key_used = None

    try:
        # Use the imported PROVIDER_CLASS_MAP directly
        ProviderClass = PROVIDER_CLASS_MAP.get(target_provider)
        if not ProviderClass:
            # Handle dynamically discovered local providers
            if target_provider.startswith("ollama-local-") or target_provider == "ollama-proxy":
                ProviderClass = PROVIDER_CLASS_MAP.get("ollama")
            elif target_provider.startswith("litellm-local-"):
                 ProviderClass = PROVIDER_CLASS_MAP.get("litellm") # Assuming litellm provider exists in map

            if not ProviderClass: # Check again after handling dynamic names
                 raise ValueError(f"Provider class not found for '{target_provider}' in PROVIDER_CLASS_MAP")

        # Prepare arguments for the provider class constructor
        current_agent_cfg = agent.agent_config.get("config", {})
        # Base args from agent config, excluding provider-specific and agent-specific ones
        provider_kwargs = {
            k: v for k, v in current_agent_cfg.items()
            if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'api_key', 'base_url', 'referer'] # Added agent_type exclusion
        }

        # Get base URL from registry for local, or settings for remote
        base_url = model_registry.get_reachable_provider_url(target_provider)
        # Note: get_provider_config is mainly for non-key config like referer
        provider_base_config = settings.get_provider_config(target_provider)

        final_provider_args = {**provider_kwargs, **provider_base_config} # Combine agent generic args + provider base config
        if base_url: # Override base_url if found in registry (especially for dynamic local)
            final_provider_args['base_url'] = base_url

        # Add API key config if provided (for remote providers)
        if api_key_config:
            final_provider_args.update(api_key_config) # Add/override key and potentially other key-specific settings
            api_key_used = api_key_config.get('api_key')
        elif target_provider.startswith("ollama"): # Special handling for local ollama key
             final_provider_args['api_key'] = 'ollama' # Required by some ollama clients

        # Remove None values before passing to constructor
        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

        # --- Explicitly remove agent_type just in case ---
        final_provider_args.pop('agent_type', None)
        # --- End explicit removal ---

        logger.debug(f"Instantiating {ProviderClass.__name__} with args: { {k: (v[:10]+'...' if k=='api_key' and isinstance(v, str) else v) for k,v in final_provider_args.items()} }")
        new_provider_instance = ProviderClass(**final_provider_args)

        # --- Update Agent State ---
        agent.provider_name = target_provider
        agent.model = target_model # Use the model ID the provider expects
        agent.llm_provider = new_provider_instance
        # Update last key used tracker
        if api_key_used:
            agent._last_api_key_used = api_key_used
        elif hasattr(agent, '_last_api_key_used'):
            delattr(agent, '_last_api_key_used') # Clear if switching to local

        # Update agent's stored config (use canonical ID if local)
        is_local = "-local-" in target_provider or "-proxy" in target_provider
        # Construct canonical ID ONLY for config storage
        config_model_id = f"{target_provider.split('-local-')[0].split('-proxy')[0]}/{target_model}" if is_local else target_model
        if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config and isinstance(agent.agent_config["config"], dict):
            agent.agent_config["config"].update({"provider": target_provider, "model": config_model_id}) # Store canonical ID in config
            logger.debug(f"Updated agent config dict: provider='{target_provider}', model='{config_model_id}'") # Log the config update

        # --- Cleanup ---
        # Close the old provider instance safely
        await manager._close_provider_safe(old_provider_instance)
        # Set status to idle, ready for the rescheduled cycle (done by caller)
        agent.set_status(AGENT_STATUS_IDLE)
        # Log success using internal name
        logger.info(f"Agent '{agent_id}' configuration switched successfully to '{internal_log_target_name}'.")
        return True # Indicate successful reconfiguration

    except Exception as switch_err:
        # Log failure using internal name
        logger.error(f"Failover switch failed for Agent '{agent_id}' -> '{internal_log_target_name}': {switch_err}", exc_info=True)
        # Attempt to close the newly created provider instance if it exists
        if new_provider_instance:
            await manager._close_provider_safe(new_provider_instance)
        # Restore old provider if possible (best effort) - This might be problematic if old one is truly broken
        # agent.provider_name = current_provider
        # agent.model = current_model
        # agent.llm_provider = old_provider_instance
        # Consider just leaving the agent in error state or trying next failover immediately
        return False


# --- Main Failover Handler ---
async def handle_agent_model_failover(manager: 'AgentManager', agent_id: str, last_error_obj: Exception) -> bool:
    """
    Handles failover with refined logic:
    1. Try all models on all discovered/configured local providers.
    2. If local fails, try external providers:
       a. Get an active, untried key.
       b. Try initial model + up to 3 alternates with that key.
       c. If models fail, check if the original error was key-related.
       d. If key-related, quarantine key, get next key, repeat step 2b.
       e. If not key-related (or no more keys), try next external provider (step 2a).
    3. If all options exhausted, set agent to ERROR.

    Returns: True if a new configuration was successfully set for the agent to retry, False otherwise.
    """
    agent = manager.agents.get(agent_id)
    if not agent:
        logger.error(f"Failover Error: Agent '{agent_id}' not found during failover.")
        return

    # --- Initialize Failover State ---
    # Use a dictionary attached to the agent instance to track state across potential retries *within* the failover sequence.
    if not hasattr(agent, '_failover_state') or not agent._failover_state:
        agent._failover_state = {
          "original_provider": agent.provider_name,
          "original_model": agent.model,
          "last_error_obj": last_error_obj, # Store the error that triggered this
          "tried_local_providers": set(), # Stores unique provider names (e.g., ollama-local-...)
          "tried_models_per_local_provider": {}, # Dict[str, Set[str]]
          "tried_external_providers": set(), # Stores base provider names (e.g., openrouter)
          "tried_keys_per_external_provider": {}, # Dict[str, Set[str]]
          "tried_models_per_external_key": {}, # Dict[str, Set[str]]
          "failover_attempt_count": 0, 
          "current_external_provider": None,
          # --- ADD THESE TWO LINES ---
          "tried_keys_on_current_external": set(), # Initialize as empty set
          "tried_models_on_current_external_key": set() # Initialize as empty set
          # --- END ADDED LINES ---
      }
    else: # Update error if this is somehow re-entrant
        agent._failover_state["last_error_obj"] = last_error_obj
    # --- ALSO INITIALIZE IF MISSING ON RE-ENTRY (DEFENSIVE) ---
        if "tried_keys_on_current_external" not in agent._failover_state:
            agent._failover_state["tried_keys_on_current_external"] = set()
        if "tried_models_on_current_external_key" not in agent._failover_state:
            agent._failover_state["tried_models_on_current_external_key"] = set()
    # --- END DEFENSIVE INITIALIZATION ---

    failover_state = agent._failover_state
    failover_state["failover_attempt_count"] += 1

    # --- NEW: Mark the FAILED provider/model as tried BEFORE searching ---
    failed_provider = agent.provider_name # Provider that just failed
    failed_model = agent.model          # Model that just failed
    is_failed_local = "-local-" in failed_provider or "-proxy" in failed_provider

    if is_failed_local:
        if failed_provider not in failover_state["tried_models_per_local_provider"]:
            failover_state["tried_models_per_local_provider"][failed_provider] = set()
        failover_state["tried_models_per_local_provider"][failed_provider].add(failed_model)
        logger.debug(f"Marked recently failed local model '{failed_provider}/{failed_model}' as tried in failover state.")
    else: # External provider
        # Need to associate with the key used, if available
        last_key_used = getattr(agent, '_last_api_key_used', None)
        if last_key_used:
            # Ensure the provider itself is tracked if this is the first key failure for it
            if failed_provider not in failover_state["tried_keys_per_external_provider"]:
                 failover_state["tried_keys_per_external_provider"][failed_provider] = set()
            failover_state["tried_keys_per_external_provider"][failed_provider].add(last_key_used)

            # Track the model failure for this specific key
            if last_key_used not in failover_state["tried_models_per_external_key"]:
                failover_state["tried_models_per_external_key"][last_key_used] = set()
            failover_state["tried_models_per_external_key"][last_key_used].add(failed_model)
            logger.debug(f"Marked recently failed external model '{failed_provider}/{failed_model}' (Key: ...{last_key_used[-4:]}) as tried in failover state.")
        else:
            # This case might be tricky - if we don't know the key, how to track?
            # Maybe add to a generic "tried_without_key" set for the provider?
            # For now, log a warning. This might need refinement if external providers without keys are common.
            logger.warning(f"Could not mark recently failed external model '{failed_provider}/{failed_model}' as tried: No last API key recorded.")
    # --- END NEW ---

    original_provider = failover_state["original_provider"] # Keep original for reference if needed
    original_model = failover_state["original_model"]
    # Use the error stored in the state for checks
    triggering_error_obj = failover_state["last_error_obj"]
    error_type_name = type(triggering_error_obj).__name__
    last_error_str = str(triggering_error_obj)

    logger.warning(f"Failover Handler (Attempt {failover_state['failover_attempt_count']}): Initiating for Agent '{agent_id}' (Original: {original_provider}/{original_model}) due to error: {error_type_name} - {last_error_str[:150]}")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Encountered '{error_type_name}'. Trying recovery..."})

    # --- Check if error warrants failover ---
    is_provider_level_error = isinstance(triggering_error_obj, PROVIDER_LEVEL_ERRORS)
    is_key_related_error = isinstance(triggering_error_obj, KEY_RELATED_ERRORS) or \
                         (isinstance(triggering_error_obj, openai.APIStatusError) and 
                          triggering_error_obj.status_code in KEY_RELATED_STATUS_CODES)
    
    # Only trigger failover for specific error types
    should_trigger_failover = (is_provider_level_error or 
                             is_key_related_error or
                             not isinstance(triggering_error_obj, RETRYABLE_EXCEPTIONS))
    
    if not should_trigger_failover:
        logger.info(f"Failover Handler: Error '{error_type_name}' does not warrant failover. Will retry with same configuration.")
        return False

    if is_provider_level_error:
        logger.warning(f"Error '{error_type_name}' suggests provider '{failed_provider}' is unreachable. Will skip trying models on this specific instance.")

    # Check overall attempt limit (heuristic)
    # Adjust limit based on number of providers/keys/models? For now, a high fixed limit.
    if failover_state["failover_attempt_count"] > settings.MAX_FAILOVER_ATTEMPTS * 10:
         fail_reason = f"[Failover Safety Limit Reached] Too many attempts ({failover_state['failover_attempt_count']}). Last error: {error_type_name}"
         logger.error(f"Agent '{agent_id}': {fail_reason}")
         agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
         # Keep state for debugging? Or clear it? Let's clear it for now.
         if hasattr(agent, '_failover_state'): del agent._failover_state
         return False # Indicate failover exhausted

    # --- 1. Try Local Providers ---
    logger.info("Failover Step 1: Trying available local providers...")
    all_available = model_registry.get_available_models_dict()
    # Get unique provider names that indicate local instances
    local_provider_names = sorted([p for p in all_available if "-local-" in p or "-proxy" in p])

    for local_provider in local_provider_names:
        # --- Skip this specific provider instance if it's the one that just failed with a provider-level error ---
        if local_provider == failed_provider and is_provider_level_error:
            logger.debug(f"Skipping local provider '{local_provider}': It just failed with a provider-level error ({error_type_name}).")
            # Ensure it's marked as tried so we don't loop back to it unnecessarily if failover continues
            failover_state["tried_local_providers"].add(local_provider)
            continue
        # --- Also skip if it was marked tried in a previous failover step (e.g., exhausted models) ---
        if local_provider in failover_state["tried_local_providers"]:
             logger.debug(f"Skipping local provider '{local_provider}': Already marked as tried in a previous step.")
             continue

        logger.info(f"Considering local provider: {local_provider}")

        # --- NEW: Perform health check ONLY if this provider *wasn't* the one that just failed with a provider-level error ---
        # If it *was* the one that failed with a provider-level error, we already know it's likely down.
        # If the failure was model-specific, we need to check if the provider is *still* up before trying alternates.
        provider_seems_healthy = True # Assume healthy unless check fails
        if not (local_provider == failed_provider and is_provider_level_error):
            provider_url = model_registry.get_reachable_provider_url(local_provider)
            if provider_url:
                logger.debug(f"Checking health of local provider '{local_provider}' at {provider_url} before trying models...")
                provider_seems_healthy = await _check_provider_health(provider_url)
                if not provider_seems_healthy:
                    logger.warning(f"Health check failed for local provider '{local_provider}'. Skipping model attempts.")
                    failover_state["tried_local_providers"].add(local_provider) # Mark as tried due to health check failure
                    continue # Move to the next local provider
            else:
                logger.warning(f"Could not get URL for local provider '{local_provider}' to perform health check. Assuming unhealthy.")
                provider_seems_healthy = False
                failover_state["tried_local_providers"].add(local_provider) # Mark as tried
                continue # Move to the next local provider

        # --- Proceed only if provider seems healthy ---
        logger.info(f"Trying models on local provider: {local_provider}")
        
        # Initialize tried_models_per_local_provider for the current provider if not already
        if local_provider not in failover_state["tried_models_per_local_provider"]:
            failover_state["tried_models_per_local_provider"][local_provider] = set()
        tried_on_this_provider = failover_state["tried_models_per_local_provider"][local_provider]

        # Get all models for this specific local provider instance
        all_models_for_this_local_provider_raw = all_available.get(local_provider, [])
        
        candidate_local_model_infos: List[ModelInfo] = []
        for model_data in all_models_for_this_local_provider_raw:
            model_id = model_data.get("id")
            if not model_id: continue
            if model_id in tried_on_this_provider:
                logger.debug(f"Skipping local model '{model_id}' on '{local_provider}': already tried.")
                continue
            
            model_data_copy = model_data.copy()
            model_data_copy["provider"] = local_provider # Specific provider name
            candidate_local_model_infos.append(model_data_copy)

        if not candidate_local_model_infos:
            logger.warning(f"No untried models found for local provider '{local_provider}'.")
        else:
            # Prepare performance metrics for these candidates
            local_provider_metrics_for_sorter = {}
            base_local_provider_name = local_provider.split("-local-")[0].split("-proxy")[0]
            
            for m_info_dict in candidate_local_model_infos:
                m_id_suffix = m_info_dict["id"]
                metrics = manager.performance_tracker.get_metrics(base_local_provider_name, m_id_suffix)
                if metrics:
                    if local_provider not in local_provider_metrics_for_sorter:
                         local_provider_metrics_for_sorter[local_provider] = {}
                    local_provider_metrics_for_sorter[local_provider][m_id_suffix] = metrics
                else: # Default if no metrics found
                    if local_provider not in local_provider_metrics_for_sorter:
                         local_provider_metrics_for_sorter[local_provider] = {}
                    local_provider_metrics_for_sorter[local_provider][m_id_suffix] = {"score": 0.0, "latency": float('inf'), "calls": 0}

            logger.debug(f"Sorting {len(candidate_local_model_infos)} candidate models for local provider '{local_provider}'.")
            sorted_local_models_to_try = sort_models_by_size_performance_id(
                candidate_local_model_infos,
                performance_metrics=local_provider_metrics_for_sorter
            )

            models_available_on_provider_tried = False
            for sorted_model_info in sorted_local_models_to_try:
                local_model_id_to_try = sorted_model_info["id"]
                # Redundant check, already filtered, but defensive:
                if local_model_id_to_try in tried_on_this_provider: continue

                models_available_on_provider_tried = True
                logger.info(f"Failover: Attempting switch to (comprehensively sorted) local model: {local_provider}/{local_model_id_to_try} "
                            f"(Size: {sorted_model_info.get('num_parameters_sortable', 0)}, Score: {sorted_model_info.get('performance_score', 0.0):.2f})")
                switched = await _try_switch_agent(manager, agent, local_provider, local_model_id_to_try, None)
                if switched:
                    logger.info(f"Failover handler successfully reconfigured agent '{agent_id}' to try '{local_provider}/{local_model_id_to_try}'.")
                    return True
                else:
                    logger.warning(f"Failover: _try_switch_agent failed for local model: {local_provider}/{local_model_id_to_try}")
                    failover_state["tried_models_per_local_provider"][local_provider].add(local_model_id_to_try)
            
            if not models_available_on_provider_tried:
                 logger.warning(f"No untried models found or switch failed for all models on local provider: {local_provider} after comprehensive sort.")
        
        # Mark the provider as fully tried *only after checking all its models*
        failover_state["tried_local_providers"].add(local_provider) # Mark provider tried after exhausting its models
        logger.warning(f"Exhausted all models for local provider: {local_provider}")

    logger.info("Failover Step 1: Finished trying local providers.")

    # --- 2. Try External Providers ---
    logger.info("Failover Step 2: Trying available external providers...")
    external_provider_order = ["openrouter", "openai"] # Define order
    current_model_tier = settings.MODEL_TIER

    for external_provider in external_provider_order:
        # --- Skip this specific provider instance if it's the one that just failed with a provider-level error ---
        # Note: Comparing base name (e.g., 'openrouter') with the potentially dynamic failed_provider name
        # This comparison might need refinement if failed_provider can be dynamic external. Assuming failed_provider is base name for external for now.
        if external_provider == failed_provider and is_provider_level_error:
             logger.debug(f"Skipping external provider '{external_provider}': It just failed with a provider-level error ({error_type_name}).")
             # Ensure it's marked as tried
             failover_state["tried_external_providers"].add(external_provider)
             continue
        # --- Also skip if it was marked tried in a previous failover step ---
        if external_provider in failover_state["tried_external_providers"]:
             logger.debug(f"Skipping external provider '{external_provider}': Already marked as tried in a previous step.")
             continue

        if external_provider not in all_available:
             logger.debug(f"Skipping external provider '{external_provider}': Not available/reachable.")
             continue

        # Check tier compatibility
        if current_model_tier == "FREE" and external_provider != "openrouter": # Only OpenRouter has free tier currently
             logger.info(f"Skipping external provider '{external_provider}': MODEL_TIER=FREE")
             continue

        logger.info(f"Trying external provider: {external_provider}")
        # Reset key tracking for this new provider
        failover_state["tried_keys_on_current_external"].clear()
        failover_state["current_external_provider"] = external_provider
        keys_available_for_provider = True

        while keys_available_for_provider:
            # Get next *active* key for this provider, skipping already tried ones
            next_key_config = None
            potential_keys = settings.PROVIDER_API_KEYS.get(external_provider, [])
            key_found = False

            if not potential_keys:
                 logger.warning(f"No API keys configured for provider '{external_provider}'. Cannot proceed.")
                 keys_available_for_provider = False
                 continue # Break key loop

            # Use ProviderKeyManager to get the next *valid* key, respecting quarantine
            # This simplifies the logic here significantly.
            logger.debug(f"Requesting next active key for '{external_provider}' from KeyManager.")
            next_key_config = await manager.key_manager.get_active_key_config(external_provider)

            if not next_key_config or not next_key_config.get('api_key'):
                 logger.warning(f"KeyManager returned no active keys for provider: {external_provider}")
                 keys_available_for_provider = False
                 continue # Break key loop

            current_key = next_key_config['api_key']

            # Check if we've already tried this key *in this failover sequence*
            if current_key in failover_state["tried_keys_on_current_external"]:
                 logger.warning(f"Key ending '...{current_key[-4:]}' for '{external_provider}' already tried in this failover sequence. Assuming key depletion.")
                 keys_available_for_provider = False # Assume we've cycled through all
                 continue # Break key loop

            logger.info(f"Trying key ending '...{current_key[-4:]}' for provider '{external_provider}'.")
            failover_state["tried_models_on_current_external_key"].clear()

            # Try initial model (or a default) with this key
            initial_model_to_try = None
            all_external_provider_models_raw = all_available.get(external_provider, [])

            # --- New Logic: Prioritize agent's original_model on new provider/key ---
            agent_original_model_on_this_provider = None
            if any(m_info['id'] == original_model for m_info in all_external_provider_models_raw):
                original_model_suitable_tier = False
                if current_model_tier == "ALL": original_model_suitable_tier = True
                elif current_model_tier == "FREE" and external_provider == "openrouter" and ":free" in original_model.lower(): original_model_suitable_tier = True
                
                if original_model_suitable_tier and original_model not in failover_state["tried_models_on_current_external_key"]:
                    agent_original_model_on_this_provider = original_model
                    logger.info(f"Prioritizing agent's original model '{agent_original_model_on_this_provider}' for '{external_provider}' on current key.")

            if agent_original_model_on_this_provider:
                initial_model_to_try = agent_original_model_on_this_provider
            else:
                # --- Existing Logic: Attempt to select initial model using comprehensive sort if original_model not prioritized ---
                logger.debug(f"Agent's original model not prioritized for '{external_provider}'. Attempting comprehensive sort.")
                candidate_external_model_infos: List[ModelInfo] = []
                for model_data in all_external_provider_models_raw:
                    m_id = model_data.get("id")
                    if not m_id: continue
                    if m_id == original_model: continue # Already considered or not suitable

                    if m_id in failover_state["tried_models_on_current_external_key"]:
                        logger.debug(f"Skipping model '{m_id}' for initial try on '{external_provider}': already tried on current key.")
                        continue

                    tier_compatible = False
                    if current_model_tier == "ALL": tier_compatible = True
                    elif current_model_tier == "FREE" and external_provider == "openrouter" and ":free" in m_id.lower(): tier_compatible = True

                    if not tier_compatible:
                        logger.debug(f"Skipping model '{m_id}' for initial try on '{external_provider}': not compatible with tier '{current_model_tier}'.")
                        continue

                    model_data_copy = model_data.copy()
                    model_data_copy["provider"] = external_provider # Specific provider name
                    candidate_external_model_infos.append(model_data_copy)

                if candidate_external_model_infos:
                    external_provider_metrics_for_sorter = {}
                    for m_info_dict_ext in candidate_external_model_infos:
                        m_id_suffix_ext = m_info_dict_ext["id"]
                        metrics_ext = manager.performance_tracker.get_metrics(external_provider, m_id_suffix_ext)
                        if metrics_ext:
                            if external_provider not in external_provider_metrics_for_sorter: external_provider_metrics_for_sorter[external_provider] = {}
                            external_provider_metrics_for_sorter[external_provider][m_id_suffix_ext] = metrics_ext
                        else:
                            if external_provider not in external_provider_metrics_for_sorter: external_provider_metrics_for_sorter[external_provider] = {}
                            external_provider_metrics_for_sorter[external_provider][m_id_suffix_ext] = {"score": 0.0, "latency": float('inf'), "calls": 0}
                    
                    sorted_external_models = sort_models_by_size_performance_id(
                        candidate_external_model_infos,
                        performance_metrics=external_provider_metrics_for_sorter
                    )
                    if sorted_external_models:
                        initial_model_to_try = sorted_external_models[0]["id"]
                        logger.info(f"Selected initial model '{initial_model_to_try}' for '{external_provider}' based on comprehensive sort "
                                    f"(Size: {sorted_external_models[0].get('num_parameters_sortable',0)}, Score: {sorted_external_models[0].get('performance_score',0.0):.2f}).")
                    else: logger.debug(f"Comprehensive sort yielded no models for '{external_provider}'.")
                else: logger.debug(f"No candidate models for comprehensive sort for '{external_provider}' after filtering.")

                # --- Fallback B (first from registry) if comprehensive sort also fails ---
                if not initial_model_to_try:
                    logger.debug(f"Comprehensive sort failed. Using Fallback B for '{external_provider}'.")
                    if all_external_provider_models_raw: # Ensure there are models to iterate
                        for m_info_fb in all_external_provider_models_raw:
                            m_id_fb = m_info_fb['id']
                            if m_id_fb == original_model: continue # Already considered

                            model_suitable_tier_fb = False
                            if current_model_tier == "ALL": model_suitable_tier_fb = True
                            elif current_model_tier == "FREE" and external_provider == "openrouter" and ":free" in m_id_fb.lower(): model_suitable_tier_fb = True

                            if not model_suitable_tier_fb: continue
                            if m_id_fb in failover_state["tried_models_on_current_external_key"]: continue
                            
                            initial_model_to_try = m_id_fb
                            logger.info(f"Selected initial model '{initial_model_to_try}' for '{external_provider}' by fallback B (first from registry).")
                            break
            
            # Attempt the selected initial_model_to_try
            if initial_model_to_try:
                 logger.info(f"Attempting initial model '{initial_model_to_try}' with current key for provider '{external_provider}'.")
                 switched = await _try_switch_agent(manager, agent, external_provider, initial_model_to_try, next_key_config)
                 if switched:
                      logger.info(f"Failover handler successfully reconfigured agent '{agent_id}' to try '{external_provider}/{initial_model_to_try}'.")
                      return True
                 else:
                      logger.warning(f"Failover: _try_switch_agent failed for external initial model: {external_provider}/{initial_model_to_try}")
                 failover_state["tried_models_on_current_external_key"].add(initial_model_to_try)
            else:
                 logger.warning(f"No suitable initial models available for provider '{external_provider}' and tier '{current_model_tier}' after all fallbacks.")

            # Try alternate models with this key if initial failed or wasn't available
            alternate_models = await _select_alternate_models(
                manager, external_provider, original_model, failover_state["tried_models_on_current_external_key"]
            )
            logger.info(f"Attempting {len(alternate_models)} alternate models with current key.")
            for alt_model in alternate_models:
                 # Ensure alternate respects tier
                 if current_model_tier == "FREE" and ":free" not in alt_model.lower() and external_provider == "openrouter":
                      logger.debug(f"Skipping alternate model '{alt_model}': Does not match FREE tier.")
                      continue

                 logger.debug(f"Failover: Attempting switch to external alternate model: {external_provider}/{alt_model}") # ADDED LOGGING
                 switched = await _try_switch_agent(manager, agent, external_provider, alt_model, next_key_config)
                 if switched: # Reconfiguration successful
                      logger.info(f"Failover handler successfully reconfigured agent '{agent_id}' to try alternate '{external_provider}/{alt_model}'.")
                      # CycleHandler will schedule the next attempt.
                      return True # Signal to CycleHandler
                 else: # ADDED LOGGING
                      logger.warning(f"Failover: _try_switch_agent failed for external alternate model: {external_provider}/{alt_model}") # ADDED LOGGING

                 # If switch failed or cycle fails later, mark model as tried for this key
                 failover_state["tried_models_on_current_external_key"].add(alt_model)
                 # failover_state["failover_attempt_count"] += 1 # REMOVE: Don't increment overall count for model switch failure

            # All models (initial + alternates) failed for this key. Check error reason.
            logger.warning(f"All model attempts failed for key ending '...{current_key[-4:]}' on provider '{external_provider}'.")
            failover_state["tried_keys_on_current_external"].add(current_key) # Mark key as tried for this failover sequence

            # Check if the *original* error that triggered failover was key-related OR provider-level
            is_key_related_error = isinstance(triggering_error_obj, KEY_RELATED_ERRORS) or \
                                   (isinstance(triggering_error_obj, openai.APIStatusError) and triggering_error_obj.status_code in KEY_RELATED_STATUS_CODES)

            # If the error was provider-level, we likely already marked the provider tried earlier.
            # If it was key-related (and not provider-level), quarantine the key.
            if is_key_related_error and not is_provider_level_error:
                logger.warning(f"Original error ({error_type_name}) was key-related. Quarantining key ending '...{current_key[-4:]}' and trying next key for '{external_provider}'.")
                await manager.key_manager.quarantine_key(external_provider, current_key)
            elif not is_provider_level_error: # Not key-related and not provider-level
                logger.info(f"Original error ({error_type_name}) was not key or provider-level. Trying next key for '{external_provider}' without quarantining.")
            # If it was provider-level, we don't quarantine the key, just move to the next provider (already handled by marking tried_external_providers)

            # Check if the provider is now depleted or marked tried
            # Use the base provider name for checking depletion and tried status
            provider_base_name = external_provider # Assuming external_provider is the base name like 'openrouter'
            if await manager.key_manager.is_provider_depleted(provider_base_name) or provider_base_name in failover_state["tried_external_providers"]:
                 logger.warning(f"Provider '{provider_base_name}' is now depleted of active keys or marked as tried.")
                 keys_available_for_provider = False # Exit key loop

        # If key loop finishes, all keys for this provider are exhausted or provider marked tried
        failover_state["tried_external_providers"].add(external_provider) # Ensure it's marked tried
        logger.warning(f"Exhausted all keys/attempts for external provider: {external_provider}")

    # --- 3. All Options Exhausted ---
    logger.error(f"Failover exhausted for Agent '{agent_id}'. No working local or external provider/model/key combination found.")
    fail_reason = f"[Failover Exhausted after {failover_state['failover_attempt_count']} attempts] Last error: {error_type_name}"
    agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
    if hasattr(agent, '_failover_state'): del agent._failover_state # Clean up state
    return False # Indicate failover exhausted

# END OF FILE src/agents/failover_handler.py
