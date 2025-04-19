# START OF FILE src/agents/failover_handler.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set, TYPE_CHECKING
import logging
import time
import openai # Import openai exceptions

# --- NEW: Import status constants ---
from src.agents.constants import AGENT_STATUS_IDLE, AGENT_STATUS_ERROR
# --- END NEW ---

# Import base Agent class for type hinting
from src.agents.core import Agent

# Import necessary components from other modules
from src.llm_providers.base import BaseLLMProvider
from src.config.settings import settings, model_registry
from src.agents.cycle_handler import MAX_FAILOVER_ATTEMPTS

# Import PROVIDER_CLASS_MAP from the refactored lifecycle module
from src.agents.agent_lifecycle import PROVIDER_CLASS_MAP, OllamaProvider # Import map and specific provider

# Type hint AgentManager
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)


async def handle_agent_model_failover(manager: 'AgentManager', agent_id: str, last_error_obj: Exception):
    """
    Handles failover: tries key cycling for relevant errors/providers first,
    then selects the next best model/provider if cycling fails or isn't applicable.

    Args:
        manager: The AgentManager instance.
        agent_id: The ID of the agent requiring failover.
        last_error_obj: The actual exception object that triggered the failover.
    """
    agent = manager.agents.get(agent_id)
    if not agent:
        logger.error(f"Failover Error: Agent '{agent_id}' not found during failover.")
        return

    original_provider = agent.provider_name
    original_model = agent.model
    # Use the canonical model ID (from agent config) for logging/tracking failures
    original_model_key = agent.agent_config.get("config", {}).get("model", f"{original_provider}/{original_model}")
    error_type_name = type(last_error_obj).__name__
    last_error_str = str(last_error_obj)

    logger.warning(f"Failover Handler: Initiating failover for '{original_model_key}' on agent '{agent_id}' due to error: {error_type_name} - {last_error_str[:150]}")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Encountered '{error_type_name}'. Trying recovery..."})

    failed_models_this_cycle = getattr(agent, '_failed_models_this_cycle', set())
    key_cycled_successfully = False

    # --- 1. Attempt Key Cycling (if applicable) ---
    is_remote_provider = original_provider not in ["ollama", "litellm"]
    key_related_errors = ( openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError, )
    key_related_status_codes = [401, 403, 429]
    is_key_related = isinstance(last_error_obj, key_related_errors) or \
                     (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code in key_related_status_codes)

    if is_remote_provider and is_key_related:
        logger.info(f"Failover Handler: Detected key-related error ({error_type_name}) for remote provider '{original_provider}'. Attempting key cycling.")
        # Use the key tracker attribute on the agent object
        failed_key_value: Optional[str] = getattr(agent, '_last_api_key_used', None)

        if failed_key_value:
            await manager.key_manager.quarantine_key(original_provider, failed_key_value)
            logger.info(f"Failover Handler: Attempting to get next active key for '{original_provider}'...")
            next_key_config = await manager.key_manager.get_active_key_config(original_provider)

            if next_key_config and next_key_config.get('api_key') != failed_key_value:
                new_key_value = next_key_config.get('api_key', 'HIDDEN')
                logger.info(f"Failover Handler: Found new key ending '...{new_key_value[-4:]}' for '{original_provider}'. Retrying same model '{original_model}'.")
                await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Trying next API key for {original_provider}..."})
                old_provider_instance = agent.llm_provider
                try:
                    ProviderClass = PROVIDER_CLASS_MAP.get(original_provider)
                    if not ProviderClass: raise ValueError(f"Provider class not found: {original_provider}")
                    current_agent_cfg = agent.agent_config.get("config", {})
                    provider_kwargs = {k: v for k, v in current_agent_cfg.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
                    final_provider_args = {**next_key_config, **provider_kwargs}
                    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

                    new_provider_instance = ProviderClass(**final_provider_args)
                    agent.llm_provider = new_provider_instance
                    agent._last_api_key_used = new_key_value # Update key tracker

                    await manager._close_provider_safe(old_provider_instance)
                    agent.set_status(AGENT_STATUS_IDLE) # Uses imported constant
                    await manager.schedule_cycle(agent, 0)
                    logger.info(f"Failover Handler: Agent '{agent_id}' successfully switched key for '{original_provider}'. Rescheduled cycle.")
                    key_cycled_successfully = True
                except Exception as key_cycle_err:
                     logger.error(f"Failover Handler: Agent '{agent_id}': Error during key cycle switch for '{original_provider}': {key_cycle_err}", exc_info=True)
            else: logger.warning(f"Failover Handler: No alternative non-quarantined keys available for '{original_provider}'. Proceeding to model failover.")
        else: logger.warning(f"Failover Handler: Could not determine failed key for '{original_provider}' or key cycling not applicable. Proceeding to model failover.")
    else: logger.debug(f"Failover Handler: Error '{error_type_name}' not key-related or provider '{original_provider}' is local. Skipping key cycling.")

    # --- 2. Model/Provider Failover ---
    if not key_cycled_successfully:
        if len(failed_models_this_cycle) >= MAX_FAILOVER_ATTEMPTS:
            fail_reason = f"[Failover Limit Reached after {len(failed_models_this_cycle)} models/keys] Last error on {original_model_key}: {error_type_name}"
            logger.error(f"Agent '{agent_id}': Max failover attempts ({MAX_FAILOVER_ATTEMPTS}) reached. Setting to ERROR.")
            agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason}) # Uses imported constant
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear();
            return

        next_provider, next_model_suffix = await _select_next_failover_model(manager, agent, failed_models_this_cycle)

        if next_provider and next_model_suffix:
            # Construct canonical ID for logging and config update
            next_model_canonical = f"{next_provider}/{next_model_suffix}" if next_provider in ["ollama", "litellm"] else next_model_suffix
            logger.info(f"Failover Handler: Failing over '{agent_id}' from '{original_model_key}' to model: {next_model_canonical}")
            await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Switching to {next_model_canonical}"})
            old_provider_instance = agent.llm_provider
            try:
                new_provider_config = {}; api_key_used = None
                if next_provider in ["ollama", "litellm"]:
                     new_provider_config = settings.get_provider_config(next_provider)
                     if next_provider == 'ollama' and PROVIDER_CLASS_MAP.get(next_provider) == OllamaProvider: new_provider_config['api_key'] = 'ollama'
                else:
                     key_config = await manager.key_manager.get_active_key_config(next_provider)
                     if key_config is None: raise ValueError(f"Could not get active key config for failover provider {next_provider}")
                     new_provider_config = key_config; api_key_used = new_provider_config.get('api_key')

                current_agent_cfg = agent.agent_config.get("config", {})
                provider_kwargs = {k: v for k, v in current_agent_cfg.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
                final_provider_args = {**new_provider_config, **provider_kwargs}; final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None};

                NewProviderClass = PROVIDER_CLASS_MAP.get(next_provider);
                if not NewProviderClass: raise ValueError(f"Provider class not found for {next_provider}");
                new_provider_instance = NewProviderClass(**final_provider_args);

                # Update agent attributes
                agent.provider_name = next_provider
                agent.model = next_model_suffix # Set agent model to the ID provider expects (no prefix for local)
                agent.llm_provider = new_provider_instance;
                if api_key_used: agent._last_api_key_used = api_key_used
                elif hasattr(agent, '_last_api_key_used'): delattr(agent, '_last_api_key_used')

                # Update agent's stored config to reflect the change (using canonical ID)
                if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config and isinstance(agent.agent_config["config"], dict):
                    agent.agent_config["config"].update({"provider": next_provider, "model": next_model_canonical});

                await manager._close_provider_safe(old_provider_instance);
                agent.set_status(AGENT_STATUS_IDLE); # Uses imported constant
                await manager.schedule_cycle(agent, 0);
                logger.info(f"Failover Handler: Agent '{agent_id}' failover successful to {next_model_canonical}. Rescheduled cycle.");
            except Exception as failover_err:
                fail_reason = f"[Failover attempt failed during switch to {next_model_canonical}: {failover_err}] Last error: {error_type_name}"
                logger.error(f"Failover Handler: Error during failover switch for '{agent_id}' to {next_model_canonical}: {failover_err}", exc_info=True)
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.add(next_model_canonical)
                logger.error(f"Failover Handler: Agent '{agent_id}' failover switch failed. Setting agent to permanent ERROR state.")
                agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason}) # Uses imported constant
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear();
        else:
            fail_reason = f"[No alternative models found after {len(failed_models_this_cycle)} attempts] Last error on {original_model_key}: {error_type_name}"
            logger.error(f"Failover Handler: No alternative models available for '{agent_id}'. Setting agent to ERROR.")
            agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason}) # Uses imported constant
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear();


async def _select_next_failover_model(manager: 'AgentManager', agent: Agent, already_failed: Set[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Selects the next available model for failover, prioritizing local, then free, then paid.
    Checks key availability for remote providers. Skips models in already_failed set.
    Returns model_id *without* prefix for local providers.
    """
    logger.debug(f"Failover Selection: Selecting next model for '{agent.agent_id}'. Current: {agent.provider_name}/{agent.model}. Failed this sequence: {already_failed}")
    available_models_dict = model_registry.get_available_models_dict()
    current_model_tier = settings.MODEL_TIER
    provider_tiers = { "local": ["ollama", "litellm"], "free": ["openrouter"], "paid": ["openrouter", "openai"] }

    for tier_name in ["local", "free", "paid"]:
        if tier_name == "paid" and current_model_tier == "FREE": continue # Skip paid if config limits to free

        logger.debug(f"Failover Selection: Checking tier '{tier_name}'...")
        for provider in provider_tiers[tier_name]:
            if provider not in model_registry._reachable_providers: continue

            if provider not in ["ollama", "litellm"]:
                 is_depleted = await manager.key_manager.is_provider_depleted(provider)
                 if is_depleted: logger.warning(f"Failover Selection: Skipping provider '{provider}' in tier '{tier_name}': All keys quarantined."); continue

            models_list = available_models_dict.get(provider, [])
            sorted_model_info = sorted(models_list, key=lambda m: m.get('id', ''))

            for model_info in sorted_model_info:
                model_id_suffix = model_info.get("id") # This is the ID *without* prefix
                if not model_id_suffix: continue

                # Construct the canonical ID for checking against failures
                canonical_failover_key = f"{provider}/{model_id_suffix}" if provider in ["ollama", "litellm"] else model_id_suffix

                if canonical_failover_key in already_failed: continue

                if tier_name == "free":
                    is_free_model = ":free" in model_id_suffix.lower() if provider == "openrouter" else False
                    if not is_free_model: continue

                logger.info(f"Failover Selection: Found next model (Tier: {tier_name}): {canonical_failover_key}")
                return provider, model_id_suffix # Return suffix for processing

    logger.warning(f"Failover Selection: Could not find any suitable alternative model for agent '{agent.agent_id}' across all tiers that hasn't already failed ({already_failed}).")
    return None, None
