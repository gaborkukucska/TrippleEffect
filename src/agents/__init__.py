# START OF FILE src/agents/__init__.py

# Import core classes first, making them available in this package's namespace
from .core import Agent
from .manager import AgentManager # AgentManager is now fully defined

# Import cycle components (these might also have forward refs, ensure they are handled)
from .cycle_components import (
    CycleContext,
    PromptAssembler,
    LLMCaller,
    CycleOutcomeDeterminer,
    NextStepScheduler
)

# Import WorkflowResult to update its references
from src.workflows.base import WorkflowResult

# --- Call the update_refs methods on models that need it ---
try:
    # Update CycleContext with fully defined Agent and AgentManager
    CycleContext.update_refs(Agent=Agent, AgentManager=AgentManager)
    # Update WorkflowResult with fully defined Agent
    # AgentManager might not be directly in WorkflowResult's fields but good practice if it could be via **kwargs
    WorkflowResult.update_refs(Agent=Agent, AgentManager=AgentManager) # AgentManager might not be strictly needed by WorkflowResult but doesn't hurt
except Exception as e:
    import logging as temp_logging # Use a temporary logger if main logging isn't configured yet
    temp_logger = temp_logging.getLogger("agents_init_forward_ref_debug")
    temp_logger.error(f"Error calling update_refs in src.agents.__init__.py: {e}", exc_info=True)
    # temp_logger.error(f"  Type of Agent: {type(Agent)}")
    # temp_logger.error(f"  Type of AgentManager: {type(AgentManager)}")
    # temp_logger.error(f"  Type of WorkflowResult: {type(WorkflowResult)}")
    # raise # Re-raise if you want to halt on error

__all__ = [
    "Agent",
    "AgentManager",
    "CycleContext",
    "PromptAssembler",
    "LLMCaller",
    "CycleOutcomeDeterminer",
    "NextStepScheduler",
    "WorkflowResult", # Export WorkflowResult if it's useful outside
]

import logging
logger = logging.getLogger(__name__)
logger.debug("src.agents package initialized, and forward references for CycleContext and WorkflowResult updated.")