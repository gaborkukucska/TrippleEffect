# START OF FILE src/agents/failover_handler.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set
import logging
import time # Added back for logging expiry time

# Import necessary components from other modules
from src.agents.core import Agent, AGENT_STATUS_IDLE, AGENT_STATUS_ERROR
from src.llm_providers.base import BaseLLMProvider
from src.config.settings import settings, model_registry
from src.agents.cycle_handler import MAX_FAILOVER_ATTEMPTS # Import constant

# Import PROVIDER_CLASS_MAP from the refactored manager (or define it here)
# Using TYPE_CHECKING to avoid circular imports at runtime
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager # Ensure this import is correct

# Re-define PROVIDER_CLASS_MAP here or import from manager if structure allows
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    # TODO: Add LiteLLMProvider when implemented
}

logger = logging.getLogger(__name__)


# --- This is the function that needs to be correctly defined ---
async def handle_agent_model_failover(manager: 'AgentManager', agent_id: str, last_error: str):
    """
    Handles the failover process for an agent after retries/key cycling failed.
    Selects the next best model, switches the provider instance, and reschedules.

    Args:
        manager: The AgentManager instance.
        agent_id: The ID of the agent requiring failover.
        last_error: The description of the last error encountered.
    """
    agent = manager.agents.get(agent_id)
    if not agent:
        logger.error(f"Failover Error: Agent '{agent_id}' not found during failover attempt.")
        return

    original_provider = agent.provider_name
    original_model = agent.model
    original_model_key = f"{original_provider}/{original_model}"
    logger.warning(f"Failover Handler: Initiating model/provider switch for '{original_model_key}' on agent '{agent_id}' due to error: {last_error}")
    await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Attempting model/provider failover for {original_model_key}..."})

    failed_models_this_cycle = getattr(agent, '_failed_models_this_cycle', set())

    # --- Quarantine the failed key (if applicable) ---
    # Note: Key cycling logic moved here from manager.py
    failed_key_value: Optional[str] = None
    key_cycled = False # Flag to track if we successfully cycled a key
    if original_provider not in ["ollama", "litellm"]:
        # Attempt to get the key used from the provider instance
        if hasattr(agent.llm_provider, 'api_key') and isinstance(agent.llm_provider.api_key, str):
            failed_key_value = agent.llm_provider.api_key
            await manager.key_manager.quarantine_key(original_provider, failed_key_value)

            # Attempt Key Cycling *before* checking failover limit or selecting new model
            logger.info(f"Failover Handler: Attempting key cycling for provider '{original_provider}' on agent '{agent_id}'...")
            next_key_config = await manager.key_manager.get_active_key_config(original_provider)

            if next_key_config and next_key_config.get('api_key') != failed_key_value:
                new_key_value = next_key_config.get('api_key')
                logger.info(f"Failover Handler: Found new active key for provider '{original_provider}'. Retrying model '{original_model}' with new key.")
                await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Trying next API key for {original_provider}..."})
                old_provider_instance = agent.llm_provider
                try:
                    provider_kwargs = {k: v for k, v in agent.agent_config.get("config", {}).items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
                    final_provider_args = {**next_key_config, **provider_kwargs}
                    final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
                    ProviderClass = PROVIDER_CLASS_MAP.get(original_provider)
                    if not ProviderClass: raise ValueError(f"Provider class not found for {original_provider}")
                    new_provider_instance = ProviderClass(**final_provider_args)
                    agent.llm_provider = new_provider_instance
                    await manager._close_provider_safe(old_provider_instance) # Use manager's helper
                    agent.set_status(AGENT_STATUS_IDLE)
                    await manager.schedule_cycle(agent, 0) # Reset retry count for new key
                    logger.info(f"Failover Handler: Agent '{agent_id}' successfully switched key for provider '{original_provider}'. Rescheduled cycle for model '{original_model}'.")
                    key_cycled = True # Mark that key cycling was successful
                    # return # Exit failover process, retry with new key handles it - ** DO NOT RETURN YET, let failover limit check run **
                except Exception as key_cycle_err:
                     logger.error(f"Failover Handler: Agent '{agent_id}': Error during key cycling switch for provider '{original_provider}': {key_cycle_err}", exc_info=True)
                     # Fall through to model/provider failover if key cycling instantiation fails
            else:
                logger.info(f"Failover Handler: No other non-quarantined keys available for provider '{original_provider}'. Proceeding to model/provider failover.")
        else:
             logger.debug(f"Failover Handler: Skipping key cycling for local provider '{original_provider}' or could not determine failed key.")
    # --- End Key Cycling Attempt ---


    # --- Model/Provider Failover (Proceed if key cycling didn't happen or wasn't applicable) ---
    if not key_cycled:
        # Check overall failover attempt limit ONLY if we didn't successfully cycle a key
        if len(failed_models_this_cycle) >= MAX_FAILOVER_ATTEMPTS:
            fail_reason = f"[Failover Limit Reached after {len(failed_models_this_cycle)} models/keys tried] Last error on {original_model_key}: {last_error}"
            logger.error(f"Agent '{agent_id}': Max failover attempts ({MAX_FAILOVER_ATTEMPTS}) reached for this task sequence. Setting to ERROR.")
            agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear(); return

        # Select the next model/provider
        next_provider, next_model = await _select_next_failover_model(manager, agent, failed_models_this_cycle)

        if next_provider and next_model:
            next_model_key = f"{next_provider}/{next_model}"
            logger.info(f"Failover Handler: Failing over '{agent_id}' from '{original_model_key}' to model: {next_model_key}")
            await manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Failover: Switching to {next_provider}/{next_model}"})
            old_provider_instance = agent.llm_provider
            try:
                if next_provider in ["ollama", "litellm"]: provider_config = settings.get_provider_config(next_provider)
                else:
                    provider_config = await manager.key_manager.get_active_key_config(next_provider)
                    if provider_config is None: raise ValueError(f"Could not get active key config for selected failover provider {next_provider}")
                agent.provider_name = next_provider; agent.model = next_model;
                if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config and isinstance(agent.agent_config["config"], dict):
                    agent.agent_config["config"].update({"provider": next_provider, "model": next_model});
                provider_kwargs = {k: v for k, v in agent.agent_config.get("config", {}).items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
                final_provider_args = {**provider_config, **provider_kwargs};
                final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None};
                NewProviderClass = PROVIDER_CLASS_MAP.get(next_provider);
                if not NewProviderClass: raise ValueError(f"Provider class not found for {next_provider}");
                new_provider_instance = NewProviderClass(**final_provider_args);
                agent.llm_provider = new_provider_instance;
                await manager._close_provider_safe(old_provider_instance); # Use manager's helper
                agent.set_status(AGENT_STATUS_IDLE); await manager.schedule_cycle(agent, 0); # Use manager's schedule method
                logger.info(f"Failover Handler: Agent '{agent_id}' failover successful to {next_model_key}. Rescheduled cycle.");
            except Exception as failover_err:
                fail_reason = f"[Failover attempt failed during switch to {next_model_key}: {failover_err}] Last operational error: {last_error}"
                logger.error(f"Failover Handler: Error during failover switch for '{agent_id}' to {next_model_key}: {failover_err}", exc_info=True)
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.add(next_model_key)
                logger.error(f"Failover Handler: Agent '{agent_id}' failover switch failed. Setting agent to permanent ERROR state for this task sequence.")
                agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear();
        else:
            # No alternative model found after checking local and available external
            fail_reason = f"[No alternative models found after {len(failed_models_this_cycle)} failover attempts] Last error on {original_model_key}: {last_error}"
            logger.error(f"Failover Handler: No alternative models available for '{agent_id}' after trying {len(failed_models_this_cycle)} model(s)/key(s). Setting agent to permanent ERROR state.")
            agent.set_status(AGENT_STATUS_ERROR); await manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": fail_reason})
            if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear();


async def _select_next_failover_model(manager: 'AgentManager', agent: Agent, already_failed: Set[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    (Async) Selects the next available model for failover, prioritizing local providers
    and checking remote provider key availability. Skips models already failed in this sequence.
    """
    logger.debug(f"Failover Selection: Selecting next model for agent '{agent.agent_id}'. Current: {agent.provider_name}/{agent.model}. Failed this sequence: {already_failed}")

    available_models_dict = model_registry.get_available_models_dict()
    current_model_tier = settings.MODEL_TIER

    # --- 1. Try Local Providers First ---
    local_providers = ["ollama", "litellm"]
    logger.debug(f"Failover Selection: Checking local providers: {local_providers}")
    for provider in local_providers:
        if provider in model_registry._reachable_providers and provider in available_models_dict:
            models_list = available_models_dict.get(provider, [])
            sorted_model_ids = sorted([m.get('id') for m in models_list if m.get('id')])
            for model_id in sorted_model_ids:
                failover_key = f"{provider}/{model_id}" # Use full key for tracking
                if failover_key not in already_failed:
                    logger.info(f"Failover Selection: Found next model (Local): {provider}/{model_id}")
                    return provider, model_id
            # else: logger.debug(f"Failover Selection: Local provider '{provider}' has models, but all have failed in this sequence.")
        # else: logger.debug(f"Failover Selection: Local provider '{provider}' not reachable or has no models.")


    # --- 2. Try External Providers (Respecting Tier and Key Availability) ---
    external_providers = ["openrouter", "openai"] # Add others if needed
    free_models: List[Tuple[str, str]] = []
    paid_models: List[Tuple[str, str]] = []
    available_external_providers = []

    for provider in external_providers:
        if provider in model_registry._reachable_providers and provider in available_models_dict:
            is_depleted = await manager.key_manager.is_provider_depleted(provider) # Use await
            if not is_depleted:
                available_external_providers.append(provider)
            else:
                logger.warning(f"Failover Selection: Skipping external provider '{provider}': all keys quarantined.")

    for provider in available_external_providers:
        models_list = available_models_dict.get(provider, [])
        for model_info in models_list:
            model_id = model_info.get("id")
            if not model_id: continue
            failover_key = f"{provider}/{model_id}" # Use provider/model key
            if failover_key in already_failed: continue
            is_free = ":free" in model_id.lower() if provider == "openrouter" else False
            if is_free: free_models.append((provider, model_id))
            else: paid_models.append((provider, model_id))

    free_models.sort(key=lambda x: x[1]); paid_models.sort(key=lambda x: x[1])
    logger.debug(f"Failover Selection: Checking available/non-depleted external providers. Free: {len(free_models)}. Paid: {len(paid_models)}. Tier: {current_model_tier}")

    if current_model_tier != "PAID_ONLY":
        logger.debug("Failover Selection: Checking available Free external models...")
        for provider, model_id in free_models:
             logger.info(f"Failover Selection: Found next model (External Free): {provider}/{model_id}"); return provider, model_id

    if current_model_tier != "FREE":
        logger.debug("Failover Selection: Checking available Paid external models...")
        for provider, model_id in paid_models:
            logger.info(f"Failover Selection: Found next model (External Paid): {provider}/{model_id}"); return provider, model_id

    logger.warning(f"Failover Selection: Could not find any suitable alternative model for agent '{agent.agent_id}' that hasn't already failed ({already_failed}).")
    return None, None
