# START OF FILE src/agents/cycle_components/cycle_context.py
import time
from typing import TYPE_CHECKING, Optional, List, Dict, Any, Set
from pydantic import BaseModel, Field
import asyncio # For asyncio.Lock

# Import base types and Agent class if needed for type hinting
from src.llm_providers.base import MessageDict, ToolResultDict

# Forward references for Agent and AgentManager
if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager

class CycleContext(BaseModel):
    """
    Holds the state and variables relevant to a single agent processing cycle.
    Used to pass data between different components of the CycleHandler.
    """
    agent: 'Agent' = Field(..., description="The Agent instance being processed.")
    manager: 'AgentManager' = Field(..., description="The AgentManager instance.")
    retry_count: int = Field(default=0, description="Current retry count for this cycle.")
    turn_count: int = Field(default=0, description="Tracks the number of turns within a single, continuous cycle.")
    
    # Cycle Execution State
    history_for_call: List[MessageDict] = Field(default_factory=list, description="Message history prepared for the LLM call.")
    final_system_prompt: str = Field(default="", description="The system prompt used for this cycle.")
    start_time: float = Field(default_factory=time.perf_counter, description="Cycle start time for duration calculation.")
    llm_call_duration_ms: float = Field(default=0.0, description="Duration of the LLM call in milliseconds.")
    
    # Outcome Flags
    cycle_completed_successfully: bool = Field(default=False, description="True if the cycle completed without needing retry or failover.")
    trigger_failover: bool = Field(default=False, description="True if the cycle should trigger a failover.")
    needs_reactivation_after_cycle: bool = Field(default=False, description="True if the agent needs to be reactivated for another cycle.")
    
    # Action Tracking
    action_taken_this_cycle: bool = Field(default=False, description="True if the agent took a meaningful action (tool, state change, plan).")
    executed_tool_successfully_this_cycle: bool = Field(default=False, description="True if at least one tool executed successfully in this cycle.")
    state_change_requested_this_cycle: bool = Field(default=False, description="True if a state change was requested by the agent.")
    plan_submitted_this_cycle: bool = Field(default=False, description="True if Admin AI submitted a plan this cycle.") # Still relevant if other agents can submit plans
    thought_produced_this_cycle: bool = Field(default=False, description="True if a <think> block was processed this cycle.")
    
    # Error Handling
    last_error_obj: Optional[Any] = Field(default=None, description="Exception object if an error occurred.")
    last_error_content: str = Field(default="", description="String content of the last error.")
    is_retryable_error_type: bool = Field(default=False, description="True if the error is of a retryable type.")
    is_key_related_error: bool = Field(default=False, description="True if the error is key-related (e.g., auth, rate limit).")
    is_provider_level_error: bool = Field(default=False, description="True if error suggests provider is unreachable.")

    # Provider and Model Info for this cycle
    current_provider_name: Optional[str] = Field(default=None, description="Provider name used for this cycle.")
    current_model_name: Optional[str] = Field(default=None, description="Model name used for this cycle.")
    current_model_key_for_tracking: Optional[str] = Field(default=None, description="Combined provider/model key for tracking failures.")
    
    # Configuration (copied for reference)
    max_retries_for_cycle: int = Field(default=3, description="Max retries allowed for this type of cycle.")
    retry_delay_for_cycle: float = Field(default=5.0, description="Delay between retries for this type of cycle.")
    
    # Misc
    current_db_session_id: Optional[int] = Field(default=None, description="Current database session ID.")

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def update_refs(cls, **localns: Any) -> None:
        """
        Class method to update forward references.
        Pydantic v1 uses update_forward_refs, v2 uses model_rebuild.
        This provides a consistent way to call it.
        """
        # Pydantic v2 introduced model_rebuild for this.
        # Pydantic v1 had update_forward_refs.
        # We check for model_rebuild first as it's the newer way.
        if hasattr(cls, 'model_rebuild'):
            # For Pydantic v2, localns are implicitly available or passed differently.
            # The `force=True` might be needed if there are complex circular dependencies
            # that Pydantic can't resolve automatically.
            cls.model_rebuild(_types_namespace=localns, force=True) # Pass localns if model_rebuild signature allows
        elif hasattr(cls, 'update_forward_refs'): # Pydantic v1
            cls.update_forward_refs(**localns)
        else:
            # Fallback logging if neither method is found (e.g., unexpected Pydantic version)
            import logging as temp_logging # Use temp_logging to avoid circular deps if main logging isn't fully set up
            temp_logger_ref = temp_logging.getLogger("CycleContext_ForwardRef_Update")
            temp_logger_ref.warning(
                "Could not find 'model_rebuild' (Pydantic v2+) or 'update_forward_refs' (Pydantic v1) "
                "on CycleContext model. ForwardRef resolution for Agent/AgentManager might fail."
            )

# Ensure that the update_refs method is available at the class level
# No standalone call to update_forward_refs here; it's called from agents.__init__