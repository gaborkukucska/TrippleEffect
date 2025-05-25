# START OF FILE src/agents/cycle_components/__init__.py
"""
This package contains components that make up the agent processing cycle logic,
refactored from the original CycleHandler.
"""

# Import the components themselves
from .cycle_context import CycleContext
from .prompt_assembler import PromptAssembler
from .llm_caller import LLMCaller
from .outcome_determiner import CycleOutcomeDeterminer
from .next_step_scheduler import NextStepScheduler

# --- REMOVE THE CycleContext.update_forward_refs() CALL FROM HERE ---

__all__ = [
    "CycleContext",
    "PromptAssembler",
    "LLMCaller",
    "CycleOutcomeDeterminer",
    "NextStepScheduler",
]

import logging
logger = logging.getLogger(__name__)
logger.debug("src.agents.cycle_components package initialized.") # Removed mention of update_forward_refs