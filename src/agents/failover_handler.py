# START OF FILE src/agents/failover_handler.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set
import logging
import time
import openai # Import openai exceptions

# Import necessary components from other modules
from src.agents.core import Agent, AGENT_STATUS_IDLE, AGENT_STATUS_ERROR
from src.llm_providers.base import BaseLLMProvider
from src.config.settings import settings, model_registry
from src.agents.cycle_handler import MAX_FAILOVER_ATTEMPTS

# Import PROVIDER_CLASS_MAP from the refactored lifecycle module
from src.agents.agent_lifecycle import PROVIDER_CLASS_MAP, OllamaProvider # Import map and specific provider

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
    original_model_key = f"{original_provider}/{original_model}"
    error_type_name = type(last_error_obj).__name__
    last_error_str = str(last_error_obj)

    logger.warning(f"Failover Handler: Initiating failover for '{original_model_key}' on agent '{agent_id}' due to error: {error_type_name} - {last_error_str[:150]}")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Encountered '{error_type_name}'. Trying recovery..."})

    failed_models_this_cycle = getattr(agent, '_failed_models_this_cycle', set())
    key_cycled_successfully = False

    # --- 1. Attempt Key Cycling (if applicable) ---
    is_remote_provider = original_provider not in ["ollama", "litellm"]
    # Define key-related errors (might need refinement based on provider specifics)
    key_related_errors = (
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.RateLimitError,
    )
    key_related_status_codes = [401, 403, 429]

    is_key_related = isinstance(last_error_obj, key_related_errors) or \
                     (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code in key_related_status_codes)

    if is_remote_provider and is_key_related:
        logger.info(f"Failover Handler: Detected key-related error ({error_type_name}) for remote provider '{original_provider}'. Attempting key cycling.")
        failed_key_value: Optional[str] = getattr(agent.llm_provider, 'api_key', None) # Get key from current provider instance

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
                    # Re-create provider instance with the NEW key config
                    ProviderClass = PROVIDER_CLASS_MAP.get(original_provider)
                    if not ProviderClass: raise ValueError(f"Provider class not found: {original_provider}")
                    # Get other config, excluding old key/base_url if they came from key manager
                    current_agent_cfg = agent.agent_config.get("config", {})
                    provider_kwargs = {k: v for k, v in current_agent_cfg.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
                    final_provider_args = {**next_key_config, **provider_kwargs} # Combine new key config + other args
                    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

                    new_provider_instance = ProviderClass(**final_provider_args)
                    agent.llm_provider = new_provider_instance
                    agent._last_api_key_used = new_key_value # Update the key tracker

                    await manager._close_provider_safe(old_provider_instance)
                    agent.set_status(AGENT_STATUS_IDLE)
                    await manager.schedule_cycle(agent, 0) # Reset retry count for new key
                    logger.info(f"Failover Handler: Agent '{agent_id}' successfully switched key for '{original_provider}'. Rescheduled cycle.")
                    key_cycled_successfully = True # Flag success
                except Exception as key_cycle_err:
                     logger.error(f"Failover Handler: Agent '{agent_id}': Error during key cycle switch for '{original_provider}': {key_cycle_err}", exc_info=True)
                     # Let it proceed to model failover if key cycling instantiation fails
            else:
                logger.warning(f"Failover Handler: No alternative non-quarantined keys available for '{original_provider}'. Proceeding to model failover.")
        else:
            logger.warning(f"Failover Handler: Could not determine failed key for '{original_provider}' or key cycling not applicable. Proceeding to model failover.")
    else:
         logger.debug(f"Failover Handler: Error '{error_type_name}' not key-related or provider '{original_provider}' is local. Skipping key cycling.")

    # --- 2. Model/Provider Failover (if key cycling didn't happen or failed) ---
    if not key_cycled_successfully:
        # Check overall failover attempt limit
        if len(failed_models_this_cycle) >= MAX_FAILOVER_ATTEMPTS:
            fail_reason = f"[Failover Limit Reached after {len(failed_models_this_cycle)} models/keys] Last error on {original_model_key}: {error_type_name}"
            logger.error(f"Agent '{agent_id}': Max failover attempts ({MAX_FAILOVER_ATTEMPTS}) reached. Setting to ERROR.")
            agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear();
            return # Stop failover process

        # Select the next model/provider
        next_provider, next_model = await _select_next_failover_model(manager, agent, failed_models_this_cycle)

        if next_provider and next_model:
            next_model_key = f"{next_provider}/{next_model}"
            logger.info(f"Failover Handler: Failing over '{agent_id}' from '{original_model_key}' to model: {next_model_key}")
            await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Switching to {next_provider}/{next_model}"})
            old_provider_instance = agent.llm_provider
            try:
                # Get config for the *new* provider/model
                new_provider_config = {}
                api_key_used = None
                if next_provider in ["ollama", "litellm"]:
                     new_provider_config = settings.get_provider_config(next_provider)
                     # Add dummy key if using openai lib for Ollama
                     if next_provider == 'ollama' and PROVIDER_CLASS_MAP.get(next_provider) == OllamaProvider:
                          new_provider_config['api_key'] = 'ollama'
                else: # Remote provider
                     key_config = await manager.key_manager.get_active_key_config(next_provider)
                     if key_config is None: raise ValueError(f"Could not get active key config for failover provider {next_provider}")
                     new_provider_config = key_config
                     api_key_used = new_provider_config.get('api_key')

                # Get agent-specific args (excluding provider/model/key related)
                current_agent_cfg = agent.agent_config.get("config", {})
                provider_kwargs = {k: v for k, v in current_agent_cfg.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
                final_provider_args = {**new_provider_config, **provider_kwargs};
                final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None};

                NewProviderClass = PROVIDER_CLASS_MAP.get(next_provider);
                if not NewProviderClass: raise ValueError(f"Provider class not found for {next_provider}");

                # Instantiate new provider
                new_provider_instance = NewProviderClass(**final_provider_args);

                # Update agent attributes
                agent.provider_name = next_provider; agent.model = next_model;
                agent.llm_provider = new_provider_instance;
                if api_key_used: agent._last_api_key_used = api_key_used
                else: hasattr(agent, '_last_api_key_used') and delattr(agent, '_last_api_key_used')

                # Update agent's stored config reflect the change
                if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config and isinstance(agent.agent_config["config"], dict):
                    agent.agent_config["config"].update({"provider": next_provider, "model": next_model});

                await manager._close_provider_safe(old_provider_instance);
                agent.set_status(AGENT_STATUS_IDLE);
                await manager.schedule_cycle(agent, 0); # Reset retry count for new model/provider
                logger.info(f"Failover Handler: Agent '{agent_id}' failover successful to {next_model_key}. Rescheduled cycle.");
            except Exception as failover_err:
                fail_reason = f"[Failover attempt failed during switch to {next_model_key}: {failover_err}] Last error: {error_type_name}"
                logger.error(f"Failover Handler: Error during failover switch for '{agent_id}' to {next_model_key}: {failover_err}", exc_info=True)
                # Add the attempted failover model to the failed set for this cycle
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.add(next_model_key)
                logger.error(f"Failover Handler: Agent '{agent_id}' failover switch failed. Setting agent to permanent ERROR state.")
                agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear(); # Clear failed list on permanent error
        else:
            # No alternative model found
            fail_reason = f"[No alternative models found after {len(failed_models_this_cycle)} attempts] Last error on {original_model_key}: {error_type_name}"
            logger.error(f"Failover Handler: No alternative models available for '{agent_id}'. Setting agent to ERROR.")
            agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear(); # Clear failed list


async def _select_next_failover_model(manager: 'AgentManager', agent: Agent, already_failed: Set[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Selects the next available model for failover, prioritizing local, then free, then paid.
    Checks key availability for remote providers. Skips models in already_failed set.
    """
    # (Logic remains the same as previous version)
    logger.debug(f"Failover Selection: Selecting next model for '{agent.agent_id}'. Current: {agent.provider_name}/{agent.model}. Failed this sequence: {already_failed}")

    available_models_dict = model_registry.get_available_models_dict()
    current_model_tier = settings.MODEL_TIER # FREE or ALL

    # Tier Preference Order
    provider_tiers = {
        "local": ["ollama", "litellm"],
        "free": ["openrouter"], # Assuming OpenRouter handles free tier tagging primarily
        "paid": ["openrouter", "openai"] # Add other paid providers here
    }

    # Iterate through tiers
    for tier_name in ["local", "free", "paid"]:
        if tier_name == "free" and current_model_tier == "PAID_ONLY": continue # Skip free if config limits to paid
        if tier_name == "paid" and current_model_tier == "FREE": continue # Skip paid if config limits to free

        logger.debug(f"Failover Selection: Checking tier '{tier_name}'...")
        for provider in provider_tiers[tier_name]:
            if provider not in model_registry._reachable_providers: continue # Skip unreachable

            # Check key availability for remote (non-local) providers
            if provider not in ["ollama", "litellm"]:
                 is_depleted = await manager.key_manager.is_provider_depleted(provider)
                 if is_depleted:
                     logger.warning(f"Failover Selection: Skipping provider '{provider}' in tier '{tier_name}': All keys quarantined.")
                     continue # Skip this provider if all keys are blocked

            # Check models within the provider
            models_list = available_models_dict.get(provider, [])
            # Sort models alphabetically for deterministic selection within a tier/provider
            sorted_model_info = sorted(models_list, key=lambda m: m.get('id', ''))

            for model_info in sorted_model_info:
                model_id = model_info.get("id")
                if not model_id: continue

                # Construct the unique key for checking against failures
                failover_key = f"{provider}/{model_id}"

                # Skip if this model has already failed in this specific failover sequence
                if failover_key in already_failed:
                    # logger.debug(f"Failover Selection: Skipping model '{failover_key}' as it already failed in this sequence.")
                    continue

                # Specific check for free tier models if we are in the 'free' tier loop
                if tier_name == "free":
                    is_free_model = ":free" in model_id.lower() if provider == "openrouter" else False # Add checks for other providers if needed
                    if not is_free_model:
                        # logger.debug(f"Failover Selection: Skipping non-free model '{failover_key}' while checking 'free' tier.")
                        continue # Skip non-free models when specifically looking for free ones

                # Found a suitable model
                logger.info(f"Failover Selection: Found next model (Tier: {tier_name}): {provider}/{model_id}")
                return provider, model_id

    # No suitable model found in any tier
    logger.warning(f"Failover Selection: Could not find any suitable alternative model for agent '{agent.agent_id}' across all tiers that hasn't already failed ({already_failed}).")
    return None, None
