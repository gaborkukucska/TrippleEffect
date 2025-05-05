# START OF FILE src/agents/failover_handler.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set, TYPE_CHECKING
import logging
import time
import openai # Import openai exceptions
import aiohttp # Import aiohttp exceptions for provider-level check
import random # For selecting alternates if no performance data

# --- Import status and error constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR,
    KEY_RELATED_ERRORS, KEY_RELATED_STATUS_CODES
)
# --- End NEW ---

# Import base Agent class for type hinting
from src.agents.core import Agent

# Import necessary components from other modules
from src.llm_providers.base import BaseLLMProvider
from src.config.settings import settings, model_registry # Import settings and registry
# --- ** FIX: Import _extract_model_size_b and PROVIDER_CLASS_MAP ** ---
from src.agents.agent_lifecycle import PROVIDER_CLASS_MAP, _extract_model_size_b
# --- End FIX ---

# Type hint AgentManager
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Define provider-level errors (connection, timeout, etc.) - Unchanged
PROVIDER_LEVEL_ERRORS = (
    aiohttp.ClientConnectorError,
    asyncio.TimeoutError,
    openai.APIConnectionError,
)

# Key-related errors imported from constants (Unchanged)

# --- Helper Function to Check Provider Health (Unchanged from previous correct version) ---
async def _check_provider_health(base_url: str, timeout: int = 3) -> bool:
    """Performs a quick health check on the provider's base URL."""
    if not base_url: return False
    check_url = base_url.rstrip('/') + "/"; logger.debug(f"Performing health check on: {check_url}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(check_url, timeout=timeout, allow_redirects=False) as response:
                if 200 <= response.status < 400: logger.debug(f"Health check successful (HEAD {response.status})"); return True
                else:
                    logger.debug(f"HEAD failed ({response.status}), trying GET")
                    async with session.get(check_url, timeout=timeout) as get_response: # <<< FIX: Moved to new line
                         if 200 <= get_response.status < 400: logger.debug(f"Health check successful (GET {get_response.status})"); return True
                         else: logger.warning(f"Health check failed for {check_url} (GET Status: {get_response.status})"); return False
    except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as conn_err: logger.warning(f"Health check connection failed for {check_url}: {conn_err}"); return False
    except Exception as e: logger.error(f"Unexpected error during health check for {check_url}: {e}", exc_info=True); return False
# --- END Helper Function ---


# --- Helper Function to Select Alternate Models (MODIFIED for tier handling) ---
async def _select_alternate_models(
    provider_name: str, # The specific provider instance name (e.g., ollama-local, openrouter)
    original_model_suffix: str, # Just the model suffix (e.g., llama3, gpt-4o)
    tried_models_on_this_provider: Set[str], # Models already tried for this specific provider instance/key
    model_tier: str, # Current MODEL_TIER setting
    all_available_models: Dict[str, List[Dict]], # Full dict from registry
    max_alternates: int = 3
) -> List[str]:
    """
    Selects up to max_alternates different models from the same provider instance,
    respecting the model tier.
    """
    alternates = []
    # Get all models listed under this specific provider in the full registry data
    provider_models = all_available_models.get(provider_name, [])
    if not provider_models:
        logger.debug(f"Select Alternates: No models found in registry for provider '{provider_name}'.")
        return []

    logger.debug(f"Select Alternates: Checking {len(provider_models)} models on '{provider_name}' for alternates (Tier: {model_tier}).")
    potential_alternates = []
    for model_info in provider_models:
        model_suffix = model_info.get("id")
        if not model_suffix: continue

        # Skip original and already tried models
        if model_suffix == original_model_suffix or model_suffix in tried_models_on_this_provider:
            continue

        # Check tier compliance
        is_local = "-local-" in provider_name or provider_name == "ollama-proxy" or provider_name == "ollama-local"
        is_free_remote = provider_name == "openrouter" and ":free" in model_suffix.lower()
        tier_compliant = False
        if model_tier == "LOCAL":
            tier_compliant = is_local
        elif model_tier == "FREE":
            tier_compliant = is_local or is_free_remote
        elif model_tier == "ALL":
            tier_compliant = True

        if tier_compliant:
            potential_alternates.append(model_suffix)
        else:
            logger.debug(f"Select Alternates: Skipping '{model_suffix}' on '{provider_name}': Does not meet TIER='{model_tier}'.")

    # Shuffle and pick
    random.shuffle(potential_alternates)
    alternates = potential_alternates[:max_alternates]
    logger.debug(f"Selected alternates for provider '{provider_name}' (Tier: {model_tier}): {alternates} (from {len(potential_alternates)} potential)")
    return alternates

# --- Helper Function to Attempt Switching Agent (Unchanged from previous correct version) ---
async def _try_switch_agent(
    manager: 'AgentManager',
    agent: Agent,
    target_provider: str, # Specific instance name (e.g., ollama-local, openrouter)
    target_model_suffix: str, # Just the suffix (e.g., llama3, gpt-4o)
    api_key_config: Optional[Dict[str, Any]] = None
) -> bool:
    """Attempts to switch the agent to a new provider/model/key config."""
    agent_id = agent.agent_id
    current_provider = agent.provider_name
    current_model_canonical = agent.agent_config.get("config",{}).get("model","?") # Get canonical from stored config

    # Determine canonical ID and base provider for logging/storage
    is_target_local = target_provider.startswith(("ollama-", "litellm-"))
    target_base_provider = target_provider.split('-local-')[0].split('-proxy')[0] if is_target_local else target_provider
    target_model_canonical = f"{target_base_provider}/{target_model_suffix}" if is_target_local else target_model_suffix
    internal_log_target_name = target_model_canonical # Use canonical for logging

    logger.info(f"Attempting switch for Agent '{agent_id}': Target='{internal_log_target_name}' (Provider Instance: '{target_provider}')")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Trying '{target_model_suffix}' on '{target_provider}'..."})

    old_provider_instance = agent.llm_provider
    new_provider_instance = None
    api_key_used = None

    try:
        ProviderClass = PROVIDER_CLASS_MAP.get(target_base_provider) # Get class from base name
        if not ProviderClass: raise ValueError(f"Provider class not found for base '{target_base_provider}'")

        # Prepare args
        current_agent_cfg = agent.agent_config.get("config", {})
        provider_kwargs = {k: v for k, v in current_agent_cfg.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'api_key', 'base_url', 'referer']}
        provider_base_config = settings.get_provider_config(target_provider) # Get non-key config for specific instance/base
        final_provider_args = {**provider_kwargs, **provider_base_config}

        # Get base URL from registry for local, override if found
        if is_target_local:
             base_url = model_registry.get_reachable_provider_url(target_provider)
             if base_url: final_provider_args['base_url'] = base_url

        # Add API key config if provided (for remote)
        if api_key_config:
            final_provider_args.update(api_key_config)
            api_key_used = api_key_config.get('api_key')
        elif target_base_provider == 'ollama': final_provider_args['api_key'] = 'ollama' # Add special key if needed

        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        final_provider_args.pop('agent_type', None) # Ensure agent_type not passed

        logger.debug(f"Instantiating {ProviderClass.__name__} with args: { {k: (v[:10]+'...' if k=='api_key' and isinstance(v, str) else v) for k,v in final_provider_args.items()} }")
        new_provider_instance = ProviderClass(**final_provider_args)

        # --- Update Agent State ---
        agent.provider_name = target_provider # Store specific instance name
        agent.model = target_model_suffix # Store suffix used by provider
        agent.llm_provider = new_provider_instance
        if api_key_used: agent._last_api_key_used = api_key_used
        elif hasattr(agent, '_last_api_key_used'): delattr(agent, '_last_api_key_used')

        # Update agent's stored config with canonical model ID
        if hasattr(agent, 'agent_config') and "config" in agent.agent_config:
            agent.agent_config["config"].update({"provider": target_provider, "model": target_model_canonical})
            logger.debug(f"Updated agent config dict: provider='{target_provider}', model='{target_model_canonical}'")

        await manager._close_provider_safe(old_provider_instance)
        agent.set_status(AGENT_STATUS_IDLE) # Ready for retry by cycle handler
        logger.info(f"Agent '{agent_id}' configuration switched successfully to '{internal_log_target_name}'.")
        return True

    except Exception as switch_err:
        logger.error(f"Failover switch failed for Agent '{agent_id}' -> '{internal_log_target_name}': {switch_err}", exc_info=True)
        if new_provider_instance: await manager._close_provider_safe(new_provider_instance)
        # Do not restore old provider, let failover continue
        return False


# --- Main Failover Handler (Unchanged from previous correct version) ---
async def handle_agent_model_failover(manager: 'AgentManager', agent_id: str, last_error_obj: Exception) -> bool:
    """
    Handles failover respecting MODEL_TIER (LOCAL, FREE, ALL).

    Returns: True if a new configuration was successfully set, False otherwise.
    """
    agent = manager.agents.get(agent_id)
    if not agent: logger.error(f"Failover Error: Agent '{agent_id}' not found."); return False

    # --- Initialize/Update Failover State ---
    if not hasattr(agent, '_failover_state') or not agent._failover_state:
         agent._failover_state = {
             "original_provider": agent.provider_name,
             "original_model_canonical": agent.agent_config.get("config",{}).get("model","?"),
             "last_error_obj": last_error_obj,
             "tried_local_providers": set(),
             "tried_models_per_local_provider": {},
             "tried_external_providers": set(),
             "tried_keys_per_external_provider": {},
             "tried_models_per_external_key": {},
             "failover_attempt_count": 0,
         }
    else:
         agent._failover_state["last_error_obj"] = last_error_obj
    failover_state = agent._failover_state
    failover_state["failover_attempt_count"] += 1
    # --- End State Init/Update ---

    # --- Mark Failed ---
    failed_provider = agent.provider_name
    failed_model_suffix = agent.model
    is_failed_local = failed_provider.startswith(("ollama-", "litellm-"))
    last_key_used = getattr(agent, '_last_api_key_used', None)
    if is_failed_local:
        if failed_provider not in failover_state["tried_models_per_local_provider"]: failover_state["tried_models_per_local_provider"][failed_provider] = set()
        failover_state["tried_models_per_local_provider"][failed_provider].add(failed_model_suffix)
        logger.debug(f"Failover: Marked failed local model '{failed_provider}/{failed_model_suffix}' as tried.")
    else:
        failed_provider_base = failed_provider
        if last_key_used:
            if failed_provider_base not in failover_state["tried_keys_per_external_provider"]: failover_state["tried_keys_per_external_provider"][failed_provider_base] = set()
            if last_key_used not in failover_state["tried_models_per_external_key"]: failover_state["tried_models_per_external_key"][last_key_used] = set()
            failover_state["tried_models_per_external_key"][last_key_used].add(failed_model_suffix)
            logger.debug(f"Failover: Marked failed external model '{failed_provider_base}/{failed_model_suffix}' tried for key ...{last_key_used[-4:]}.")
        else: logger.warning(f"Failover: Could not mark failed external model '{failed_provider_base}/{failed_model_suffix}': No last key recorded.")
    # --- End Mark Failed ---

    original_provider = failover_state["original_provider"]
    original_model_canonical = failover_state["original_model_canonical"]
    triggering_error_obj = failover_state["last_error_obj"]
    error_type_name = type(triggering_error_obj).__name__
    last_error_str = str(triggering_error_obj)
    current_model_tier = settings.MODEL_TIER

    logger.warning(f"Failover Handler (Attempt {failover_state['failover_attempt_count']}): Agent '{agent_id}' (Original: {original_provider}/{original_model_canonical}, Tier: {current_model_tier}) Error: {error_type_name} - {last_error_str[:150]}")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover (Tier: {current_model_tier}): Error '{error_type_name}'. Recovering..."})

    is_provider_level_error = isinstance(triggering_error_obj, PROVIDER_LEVEL_ERRORS)
    is_key_related_error = isinstance(triggering_error_obj, KEY_RELATED_ERRORS) or \
                           (isinstance(triggering_error_obj, openai.APIStatusError) and triggering_error_obj.status_code in KEY_RELATED_STATUS_CODES)

    if is_key_related_error and not is_provider_level_error and last_key_used:
        logger.warning(f"Key-related error ({error_type_name}). Quarantining key ...{last_key_used[-4:]} for '{failed_provider}'.")
        await manager.key_manager.quarantine_key(failed_provider, last_key_used)

    if is_provider_level_error:
        logger.warning(f"Provider-level error '{error_type_name}' suggests '{failed_provider}' is unreachable. Will skip.")
        if is_failed_local: failover_state["tried_local_providers"].add(failed_provider)
        else: failover_state["tried_external_providers"].add(failed_provider)

    # Safety limit check
    if failover_state["failover_attempt_count"] > settings.MAX_FAILOVER_ATTEMPTS * 10:
         fail_reason = f"[Failover Safety Limit Reached] ({failover_state['failover_attempt_count']}). Last error: {error_type_name}"
         logger.error(f"Agent '{agent_id}': {fail_reason}"); agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
         if hasattr(agent, '_failover_state'): del agent._failover_state
         return False

    all_available_models = model_registry.get_available_models_dict()

    # --- 1. Try Local Providers ---
    logger.info(f"Failover Step 1 (Tier: {current_model_tier}): Trying available local providers...")
    local_provider_names = sorted([p for p in all_available_models if p.startswith(("ollama-", "litellm-"))])
    for local_provider in local_provider_names:
        if local_provider in failover_state["tried_local_providers"]: continue
        provider_seems_healthy = True
        if not (local_provider == failed_provider and is_provider_level_error):
            provider_url = model_registry.get_reachable_provider_url(local_provider)
            if provider_url: provider_seems_healthy = await _check_provider_health(provider_url)
            else: provider_seems_healthy = False; logger.warning(f"Could not get URL for local '{local_provider}' to health check.")
        if not provider_seems_healthy: logger.warning(f"Health check failed for local '{local_provider}'. Skipping."); failover_state["tried_local_providers"].add(local_provider); continue

        logger.info(f"Failover: Trying models on healthy local provider: {local_provider}")
        local_models = all_available_models.get(local_provider, [])
        sorted_local_models = sorted([m['id'] for m in local_models], key=_extract_model_size_b, reverse=True) # Use imported function

        if local_provider not in failover_state["tried_models_per_local_provider"]: failover_state["tried_models_per_local_provider"][local_provider] = set()
        for local_model_suffix in sorted_local_models:
            if local_model_suffix in failover_state["tried_models_per_local_provider"][local_provider]: continue
            logger.debug(f"Failover: Attempting switch to local model: {local_provider}/{local_model_suffix}")
            switched = await _try_switch_agent(manager, agent, local_provider, local_model_suffix, None)
            if switched:
                logger.info(f"Failover handler successfully reconfigured agent '{agent_id}' to try local '{local_provider}/{local_model_suffix}'.")
                if hasattr(agent, '_failover_state'): del agent._failover_state
                return True
            failover_state["tried_models_per_local_provider"][local_provider].add(local_model_suffix)
        failover_state["tried_local_providers"].add(local_provider)
        logger.warning(f"Failover: Exhausted all models for local provider: {local_provider}")
    logger.info("Failover Step 1: Finished trying local providers.")

    # --- 2. Try External Providers (Only if TIER is FREE or ALL) ---
    if current_model_tier in ["FREE", "ALL"]:
        logger.info(f"Failover Step 2 (Tier: {current_model_tier}): Trying available external providers...")
        external_provider_order = ["openrouter", "openai"]
        for external_provider_base in external_provider_order:
            if external_provider_base in failover_state["tried_external_providers"]: continue
            if external_provider_base not in all_available_models:
                 logger.debug(f"Failover: Skipping external '{external_provider_base}': Not available (filtered by tier).")
                 failover_state["tried_external_providers"].add(external_provider_base); continue
            if not settings.is_provider_configured(external_provider_base):
                logger.debug(f"Failover: Skipping external '{external_provider_base}': No keys configured.")
                failover_state["tried_external_providers"].add(external_provider_base); continue

            logger.info(f"Failover: Trying external provider: {external_provider_base}")
            if external_provider_base not in failover_state["tried_keys_per_external_provider"]: failover_state["tried_keys_per_external_provider"][external_provider_base] = set()

            while True:
                logger.debug(f"Requesting next active key for '{external_provider_base}' from KeyManager.")
                next_key_config = await manager.key_manager.get_active_key_config(external_provider_base)
                if not next_key_config or not next_key_config.get('api_key'): logger.warning(f"KeyManager returned no active keys for '{external_provider_base}'. Exhausted."); break
                current_key = next_key_config['api_key']; current_key_short = f"...{current_key[-4:]}"
                if current_key not in failover_state["tried_models_per_external_key"]: failover_state["tried_models_per_external_key"][current_key] = set()
                logger.info(f"Failover: Trying key {current_key_short} for provider '{external_provider_base}'.")

                original_is_remote = not ("-local-" in original_provider or original_provider == "ollama-proxy")
                original_model_suffix_for_alts = original_model_canonical if original_is_remote else "---"
                if '/' in original_model_suffix_for_alts and not is_failed_local: original_model_suffix_for_alts = original_model_canonical.split('/',1)[1]

                alternate_models = await _select_alternate_models(
                    external_provider_base, original_model_suffix_for_alts,
                    failover_state["tried_models_per_external_key"].get(current_key, set()),
                    current_model_tier, all_available_models
                )

                models_to_try_with_key = []
                original_suffix_available = any(m['id'] == original_model_suffix_for_alts for m in all_available_models.get(external_provider_base,[]))
                if original_suffix_available and original_model_suffix_for_alts != "---" and original_model_suffix_for_alts not in failover_state["tried_models_per_external_key"].get(current_key, set()):
                     models_to_try_with_key.append(original_model_suffix_for_alts)
                models_to_try_with_key.extend([m for m in alternate_models if m not in models_to_try_with_key])

                if not models_to_try_with_key: logger.warning(f"No suitable models found for key {current_key_short} on '{external_provider_base}' (Tier: {current_model_tier}). Trying next key."); continue

                logger.debug(f"Failover: Candidates for key {current_key_short} on '{external_provider_base}': {models_to_try_with_key}")
                key_succeeded = False
                for model_suffix_to_try in models_to_try_with_key:
                    logger.debug(f"Failover: Attempting switch: {external_provider_base}/{model_suffix_to_try} with key {current_key_short}")
                    switched = await _try_switch_agent(manager, agent, external_provider_base, model_suffix_to_try, next_key_config)
                    if switched:
                        logger.info(f"Failover successful: Agent '{agent_id}' to '{external_provider_base}/{model_suffix_to_try}' key {current_key_short}.")
                        if hasattr(agent, '_failover_state'): del agent._failover_state
                        return True
                    failover_state["tried_models_per_external_key"][current_key].add(model_suffix_to_try)

                logger.warning(f"All model attempts failed for key {current_key_short} on '{external_provider_base}'.")
                failover_state["tried_keys_per_external_provider"][external_provider_base].add(current_key) # Mark key tried in this sequence

            failover_state["tried_external_providers"].add(external_provider_base)
            logger.warning(f"Failover: Exhausted all keys/attempts for external provider: {external_provider_base}")
    else:
         logger.info("Failover Step 2: Skipping external providers because MODEL_TIER=LOCAL.")


    # --- 3. All Options Exhausted ---
    logger.error(f"Failover exhausted for Agent '{agent_id}' (Tier: {current_model_tier}). No working combination found.")
    fail_reason = f"[Failover Exhausted after {failover_state['failover_attempt_count']} attempts] Last error: {error_type_name}"
    agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
    if hasattr(agent, '_failover_state'): del agent._failover_state
    return False

# END OF FILE src/agents/failover_handler.py