# START OF FILE src/agents/cycle_components/llm_caller.py
import logging
import json
import asyncio
from typing import TYPE_CHECKING, AsyncGenerator, Dict, Any, Optional, List

from src.llm_providers.base import ToolResultDict
# Import specific constants if needed, or rely on CycleContext for error types
from src.agents.constants import (
    AGENT_TYPE_PM, AGENT_TYPE_WORKER, PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE,
    WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT
)


if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.cycle_components.cycle_context import CycleContext
    from src.config.settings import Settings # For accessing token limits

logger = logging.getLogger(__name__)

class LLMCaller:
    """
    Handles the direct interaction with the LLM provider for an agent's cycle.
    It receives the prepared messages and configuration, makes the API call,
    and streams back events (chunks, errors, status).
    """

    def __init__(self, settings: 'Settings'): # Pass settings for token limits
        self._settings = settings

    async def call_llm_provider(
        self,
        context: 'CycleContext'
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Calls the LLM provider and streams back events.

        Args:
            context: The CycleContext object for the current agent cycle.

        Yields:
            Dict[str, Any]: Events from the LLM provider (response_chunk, error, status).
        """
        agent = context.agent
        
        if not agent.llm_provider:
            logger.error(f"LLMCaller: Agent '{agent.agent_id}' has no LLM Provider set.")
            context.last_error_obj = ValueError("LLM Provider not set for agent.")
            context.last_error_content = "[LLMCaller Error]: LLM Provider not configured."
            context.trigger_failover = True # Should trigger failover if provider is missing
            yield {"type": "error", "content": context.last_error_content, "_exception_obj": context.last_error_obj, "agent_id": agent.agent_id}
            return

        logger.info(f"LLMCaller: Agent '{agent.agent_id}' starting LLM call via {agent.provider_name}. Model: {agent.model}. History length: {len(context.history_for_call)}.")

        max_tokens_override = None
        # Determine max_tokens based on agent type and state from settings
        if agent.agent_type == AGENT_TYPE_PM:
            if agent.state == PM_STATE_STARTUP:
                max_tokens_override = self._settings.PM_STARTUP_STATE_MAX_TOKENS
            elif agent.state == PM_STATE_WORK:
                max_tokens_override = self._settings.PM_WORK_STATE_MAX_TOKENS
            elif agent.state == PM_STATE_MANAGE:
                max_tokens_override = self._settings.PM_MANAGE_STATE_MAX_TOKENS
        elif agent.agent_type == AGENT_TYPE_WORKER:
            if agent.state == WORKER_STATE_STARTUP:
                max_tokens_override = self._settings.WORKER_STARTUP_STATE_MAX_TOKENS
            elif agent.state == WORKER_STATE_WORK:
                max_tokens_override = self._settings.WORKER_WORK_STATE_MAX_TOKENS
            elif agent.state == WORKER_STATE_WAIT:
                 max_tokens_override = self._settings.WORKER_WAIT_STATE_MAX_TOKENS
        
        if max_tokens_override is not None:
            logger.debug(f"LLMCaller: Agent '{agent.agent_id}' in state '{agent.state}' applying max_tokens limit of {max_tokens_override}.")


        # --- LLM Call ---
        provider_stream = None
        try:
            provider_stream = agent.llm_provider.stream_completion(
                messages=context.history_for_call,
                model=agent.model, # Use the model currently set on the agent
                temperature=agent.temperature,
                max_tokens=max_tokens_override, # Pass the determined max_tokens
                **agent.provider_kwargs # Pass other stored provider-specific kwargs
            )

            async for event in provider_stream:
                # Augment event with agent_id if not present
                if "agent_id" not in event:
                    event["agent_id"] = agent.agent_id
                
                # Store error details in context if an error event is received
                if event.get("type") == "error":
                    context.last_error_obj = event.get('_exception_obj', ValueError(event.get('content', 'Unknown LLM Error')))
                    context.last_error_content = event.get("content", f"[LLMCaller Error]: Unknown error from {agent.provider_name}")
                    # Further error type determination (retryable, key-related) will happen in OutcomeDeterminer
                yield event

        except Exception as e:
            # This catches errors if the stream_completion call itself fails before iterating
            logger.error(f"LLMCaller: Error during stream_completion call for agent '{agent.agent_id}': {e}", exc_info=True)
            context.last_error_obj = e
            context.last_error_content = f"[LLMCaller Error]: Failed to initiate stream - {type(e).__name__}"
            # Assume this is a provider-level or unhandled error, likely needs failover
            context.trigger_failover = True
            yield {"type": "error", "content": context.last_error_content, "_exception_obj": e, "agent_id": agent.agent_id}
        
        logger.debug(f"LLMCaller: Finished processing LLM call for agent '{agent.agent_id}'.")
