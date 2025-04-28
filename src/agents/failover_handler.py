# START OF FILE src/agents/failover_handler.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set, TYPE_CHECKING
import logging
import time
import openai # Import openai exceptions
import random # For selecting alternates if no performance data

# --- NEW: Import status and error constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR,
    KEY_RELATED_ERRORS, KEY_RELATED_STATUS_CODES # Import error constants
)
# --- END NEW ---

# Import base Agent class for type hinting
from src.agents.core import Agent

# Import necessary components from other modules
from src.llm_providers.base import BaseLLMProvider
from src.config.settings import settings, model_registry # Import settings and registry
from src.agents.agent_lifecycle import PROVIDER_CLASS_MAP # Import map ONLY

# Type hint AgentManager
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Key-related errors imported from constants

# --- Helper Function to Select Alternate Models ---
async def _select_alternate_models(
    manager: 'AgentManager',
    provider: str,
    original_model: str,
    tried_models_on_key: Set[str],
    max_alternates: int = 3
) -> List[str]:
    """Selects up to max_alternates different models from the same provider."""
    alternates = []
    available_provider_models = model_registry.get_available_models_dict().get(provider, [])
    if not available_provider_models:
        return []

    # Filter out already tried models and the original model
    potential_alternates = [
        m['id'] for m in available_provider_models
        if m['id'] != original_model and m['id'] not in tried_models_on_key
    ]

    # TODO: Enhance selection based on performance/characteristics if available
    # For now, just shuffle and pick
    random.shuffle(potential_alternates)
    alternates = potential_alternates[:max_alternates]
    logger.debug(f"Selected alternates for provider '{provider}': {alternates} (from {len(potential_alternates)} potential)")
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
    target_model_log_name = f"{target_provider}/{target_model}" # For logging

    logger.info(f"Attempting switch for Agent '{agent_id}': Target='{target_model_log_name}'")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Trying model {target_model_log_name}..."})

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
        canonical_model_id = f"{target_provider.split('-local-')[0].split('-proxy')[0]}/{target_model}" if is_local else target_model
        if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config and isinstance(agent.agent_config["config"], dict):
            agent.agent_config["config"].update({"provider": target_provider, "model": canonical_model_id})
            logger.debug(f"Updated agent config: provider='{target_provider}', model='{canonical_model_id}'")

        # --- Cleanup and Reschedule ---
        await manager._close_provider_safe(old_provider_instance)
        agent.set_status(AGENT_STATUS_IDLE)
        await manager.schedule_cycle(agent, 0) # Reset retry count for new config
        logger.info(f"Agent '{agent_id}' failover switch successful to '{target_model_log_name}'. Rescheduled cycle.")
        return True

    except Exception as switch_err:
        logger.error(f"Failover switch failed for Agent '{agent_id}' -> '{target_model_log_name}': {switch_err}", exc_info=True)
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
async def handle_agent_model_failover(manager: 'AgentManager', agent_id: str, last_error_obj: Exception):
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
             "tried_models_on_current_local": set(),
             "tried_external_providers": set(), # Stores base provider names (e.g., openrouter)
             "tried_keys_on_current_external": set(), # Stores actual key values for the current external provider
             "tried_models_on_current_external_key": set(),
             "failover_attempt_count": 0, # Overall attempts across providers/keys/models
             "current_external_provider": None, # Track which external provider we are cycling keys for
             "external_key_index": {} # Track index per provider: {'openrouter': 0, 'openai': 0}
         }
    else: # Update error if this is somehow re-entrant (shouldn't be with current cycle logic)
         agent._failover_state["last_error_obj"] = last_error_obj

    failover_state = agent._failover_state
    failover_state["failover_attempt_count"] += 1

    original_provider = failover_state["original_provider"]
    original_model = failover_state["original_model"]
    # Use the error stored in the state for checks
    triggering_error_obj = failover_state["last_error_obj"]
    error_type_name = type(triggering_error_obj).__name__
    last_error_str = str(triggering_error_obj)

    logger.warning(f"Failover Handler (Attempt {failover_state['failover_attempt_count']}): Initiating for Agent '{agent_id}' (Original: {original_provider}/{original_model}) due to error: {error_type_name} - {last_error_str[:150]}")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Encountered '{error_type_name}'. Trying recovery..."})

    # Check overall attempt limit (heuristic)
    # Adjust limit based on number of providers/keys/models? For now, a high fixed limit.
    if failover_state["failover_attempt_count"] > settings.MAX_FAILOVER_ATTEMPTS * 10:
         fail_reason = f"[Failover Safety Limit Reached] Too many attempts ({failover_state['failover_attempt_count']}). Last error: {error_type_name}"
         logger.error(f"Agent '{agent_id}': {fail_reason}")
         agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
         if hasattr(agent, '_failover_state'): del agent._failover_state # Clean up state
         return

    # --- 1. Try Local Providers ---
    logger.info("Failover Step 1: Trying available local providers...")
    all_available = model_registry.get_available_models_dict()
    # Get unique provider names that indicate local instances
    local_provider_names = sorted([p for p in all_available if "-local-" in p or "-proxy" in p])

    for local_provider in local_provider_names:
        if local_provider in failover_state["tried_local_providers"]:
            continue # Skip already tried provider

        logger.info(f"Trying local provider: {local_provider}")
        failover_state["tried_models_on_current_local"].clear()
        local_models = all_available.get(local_provider, [])
        sorted_local_models = sorted([m['id'] for m in local_models])

        for local_model in sorted_local_models:
            # Construct canonical ID for checking against failover state if needed, though model ID should be sufficient here
            # canonical_local_id = f"{local_provider.split('-local-')[0].split('-proxy')[0]}/{local_model}"

            # Skip if already tried (redundant with clear() above, but safe)
            if local_model in failover_state["tried_models_on_current_local"]:
                continue

            # Attempt the switch - local providers don't need key config
            switched = await _try_switch_agent(manager, agent, local_provider, local_model, None)
            if switched:
                if hasattr(agent, '_failover_state'): del agent._failover_state # Success, clear state
                return # Success!

            # If switch failed, mark model as tried for this provider
            failover_state["tried_models_on_current_local"].add(local_model)
            failover_state["failover_attempt_count"] += 1 # Increment overall counter

        # If all models on this provider failed, mark provider as tried
        failover_state["tried_local_providers"].add(local_provider)
        logger.warning(f"Exhausted all models for local provider: {local_provider}")

    logger.info("Failover Step 1: Finished trying local providers.")

    # --- 2. Try External Providers ---
    logger.info("Failover Step 2: Trying available external providers...")
    external_provider_order = ["openrouter", "openai"] # Define order
    current_model_tier = settings.MODEL_TIER

    for external_provider in external_provider_order:
        if external_provider in failover_state["tried_external_providers"]:
            continue # Skip already tried provider base name

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
            provider_models = all_available.get(external_provider, [])
            # Prefer original model if available and suitable for tier
            if any(m['id'] == original_model for m in provider_models):
                 if current_model_tier == "ALL" or (current_model_tier == "FREE" and ":free" in original_model.lower()):
                      initial_model_to_try = original_model
            # Fallback to first available model respecting tier
            if not initial_model_to_try and provider_models:
                 for m_info in provider_models:
                      m_id = m_info['id']
                      if current_model_tier == "ALL" or (current_model_tier == "FREE" and ":free" in m_id.lower()):
                           initial_model_to_try = m_id
                           break

            model_switch_successful = False
            if initial_model_to_try:
                 logger.info(f"Attempting initial model '{initial_model_to_try}' with current key.")
                 switched = await _try_switch_agent(manager, agent, external_provider, initial_model_to_try, next_key_config)
                 if switched:
                      if hasattr(agent, '_failover_state'): del agent._failover_state # Success
                      return # Success!
                 failover_state["tried_models_on_current_external_key"].add(initial_model_to_try)
                 failover_state["failover_attempt_count"] += 1
            else:
                 logger.warning(f"No suitable initial models available for provider '{external_provider}' and tier '{current_model_tier}'.")

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

                 switched = await _try_switch_agent(manager, agent, external_provider, alt_model, next_key_config)
                 if switched:
                      if hasattr(agent, '_failover_state'): del agent._failover_state # Success
                      return # Success!
                 failover_state["tried_models_on_current_external_key"].add(alt_model)
                 failover_state["failover_attempt_count"] += 1

            # All models (initial + alternates) failed for this key. Check error reason.
            logger.warning(f"All model attempts failed for key ending '...{current_key[-4:]}' on provider '{external_provider}'.")
            failover_state["tried_keys_on_current_external"].add(current_key) # Mark key as tried for this failover sequence

            # Check if the *original* error that triggered failover was key-related
            is_key_related_error = isinstance(triggering_error_obj, KEY_RELATED_ERRORS) or \
                                   (isinstance(triggering_error_obj, openai.APIStatusError) and triggering_error_obj.status_code in KEY_RELATED_STATUS_CODES)

            if is_key_related_error:
                logger.warning(f"Original error ({error_type_name}) was key-related. Quarantining key ending '...{current_key[-4:]}' and trying next key for '{external_provider}'.")
                await manager.key_manager.quarantine_key(external_provider, current_key)
                # Loop continues to get next key via get_active_key_config
            else:
                logger.info(f"Original error ({error_type_name}) was not key-related. Trying next key for '{external_provider}' without quarantining.")
                # Loop continues to get next key via get_active_key_config

            # Check if the provider is now depleted after potential quarantine
            if await manager.key_manager.is_provider_depleted(external_provider):
                 logger.warning(f"Provider '{external_provider}' is now depleted of active keys.")
                 keys_available_for_provider = False # Exit key loop

        # If key loop finishes, all keys for this provider are exhausted
        failover_state["tried_external_providers"].add(external_provider)
        logger.warning(f"Exhausted all keys for external provider: {external_provider}")

    # --- 3. All Options Exhausted ---
    logger.error(f"Failover exhausted for Agent '{agent_id}'. No working local or external provider/model/key combination found.")
    fail_reason = f"[Failover Exhausted after {failover_state['failover_attempt_count']} attempts] Last error: {error_type_name}"
    agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
    if hasattr(agent, '_failover_state'): del agent._failover_state # Clean up state

# END OF FILE src/agents/failover_handler.py
