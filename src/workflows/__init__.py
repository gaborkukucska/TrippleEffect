# START OF FILE src/workflows/__init__.py
"""
This package contains workflow definitions and the base workflow class
for orchestrating multi-step agent processes within the framework.
"""

from .base import BaseWorkflow, WorkflowResult
from .project_creation_workflow import ProjectCreationWorkflow
from .pm_kickoff_workflow import PMKickoffWorkflow # Added import

__all__ = [
    "BaseWorkflow",
    "WorkflowResult",
    "ProjectCreationWorkflow",
    "PMKickoffWorkflow", # Added to __all__
]

import logging
logger = logging.getLogger(__name__)
logger.debug("src.workflows package initialized.")