# START OF FILE src/agents/cycle_components/outcome_determiner.py
import logging
import openai # For specific error types
from typing import TYPE_CHECKING

from src.agents.constants import (
    KEY_RELATED_ERRORS, KEY_RELATED_STATUS_CODES,
    RETRYABLE_EXCEPTIONS, RETRYABLE_STATUS_CODES
)
# Import PROVIDER_LEVEL_ERRORS from failover_handler for consistency
from src.agents.failover_handler import PROVIDER_LEVEL_ERRORS


if TYPE_CHECKING:
    from src.agents.cycle_components.cycle_context import CycleContext

logger = logging.getLogger(__name__)

class CycleOutcomeDeterminer:
    """
    Determines the outcome of an agent's processing cycle based on errors
    or successful completion from the LLM call. Sets flags in CycleContext
    to guide the next steps (retry, failover, success).
    """

    def determine_cycle_outcome(self, context: 'CycleContext') -> None:
        """
        Analyzes the last_error_obj in the context and sets appropriate
        flags (is_retryable_error_type, is_key_related_error, trigger_failover).

        Args:
            context: The CycleContext object for the current agent cycle.
        """
        agent_id = context.agent.agent_id
        last_error_obj = context.last_error_obj

        if last_error_obj is None:
            # No error occurred during the LLM call or stream processing.
            # This implies the LLM call itself was successful.
            # Further checks (e.g., if agent took meaningful action) happen
            # in NextStepScheduler or the main CycleHandler loop.
            logger.debug(f"OutcomeDeterminer: No error object found for agent '{agent_id}'. Assuming LLM call was successful.")
            context.cycle_completed_successfully = True # Mark that the LLM interaction part was fine
            return

        error_type_name = type(last_error_obj).__name__
        logger.info(f"OutcomeDeterminer: Analyzing error for agent '{agent_id}'. Error type: {error_type_name}, Content: {context.last_error_content[:100]}")

        # 1. Check for Provider-Level Errors (should trigger failover immediately)
        if isinstance(last_error_obj, PROVIDER_LEVEL_ERRORS):
            logger.warning(f"OutcomeDeterminer: Agent '{agent_id}' encountered provider-level error: {error_type_name}. Marking for failover.")
            context.is_retryable_error_type = False
            context.is_key_related_error = False
            context.is_provider_level_error = True
            context.trigger_failover = True
            return

        # 2. Check for Key-Related Errors (should trigger failover/key cycle)
        is_api_status_error_key_related = (
            isinstance(last_error_obj, openai.APIStatusError) and
            getattr(last_error_obj, 'status_code', None) in KEY_RELATED_STATUS_CODES
        )
        if isinstance(last_error_obj, KEY_RELATED_ERRORS) or is_api_status_error_key_related:
            logger.warning(f"OutcomeDeterminer: Agent '{agent_id}' encountered key-related/rate-limit error: {error_type_name}. Marking for failover/key cycle.")
            context.is_retryable_error_type = False # Not retryable with the same key immediately
            context.is_key_related_error = True
            context.trigger_failover = True # KeyManager will handle quarantine/cycling
            return

        # 3. Check for other Retryable Errors (retry same config up to limit)
        is_api_status_error_retryable = (
            isinstance(last_error_obj, openai.APIStatusError) and
            getattr(last_error_obj, 'status_code', None) in RETRYABLE_STATUS_CODES
        )
        if isinstance(last_error_obj, RETRYABLE_EXCEPTIONS) or is_api_status_error_retryable:
            logger.warning(f"OutcomeDeterminer: Agent '{agent_id}' encountered retryable error: {error_type_name}. Marking as retryable.")
            context.is_retryable_error_type = True
            context.is_key_related_error = False
            context.trigger_failover = False # Will be retried by CycleHandler first
            return

        # 4. All other errors (non-retryable, non-key, non-provider) -> Trigger Failover
        logger.warning(f"OutcomeDeterminer: Agent '{agent_id}' encountered non-retryable/unknown error: {error_type_name}. Marking for failover.")
        context.is_retryable_error_type = False
        context.is_key_related_error = False
        context.trigger_failover = True