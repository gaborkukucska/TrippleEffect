# START OF FILE src/workflows/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Tuple, TYPE_CHECKING
from pydantic import BaseModel, Field
import xml.etree.ElementTree as ET

if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager


class WorkflowResult(BaseModel):
    success: bool = Field(..., description="Indicates if the workflow step executed successfully.")
    message: str = Field(..., description="A message summarizing the outcome of the workflow step.")
    workflow_name: str = Field(..., description="The name of the workflow that was executed.")
    next_agent_state: Optional[str] = Field(default=None, description="The state the calling agent should transition to.")
    next_agent_status: Optional[str] = Field(default=None, description="The operational status the calling agent should be set to.")
    ui_message_data: Optional[Dict[str, Any]] = Field(default=None, description="Data to be sent to the UI, if any.")
    tasks_to_schedule: Optional[List[Tuple['Agent', int]]] = Field(default=None, description="List of (Agent, retry_count) tuples for cycles to be scheduled by the manager.")

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def PydanticConfig(cls):
         return cls.Config

    @classmethod
    def update_refs(cls, **localns: Any) -> None:
        """
        Class method to update forward references.
        Pydantic v1 uses update_forward_refs, v2 uses model_rebuild.
        This provides a consistent way to call it.
        """
        if hasattr(cls, 'model_rebuild'): # Pydantic v2+
            # For Pydantic v2, _types_namespace is used to pass in the local/global namespace
            cls.model_rebuild(_types_namespace=localns, force=True)
        elif hasattr(cls, 'update_forward_refs'): # Pydantic v1
            cls.update_forward_refs(**localns)
        else:
            # Fallback logging if neither method is found
            import logging as temp_logging
            temp_logger_ref = temp_logging.getLogger("WorkflowResult_ForwardRef_Update")
            temp_logger_ref.warning(
                "Could not find 'model_rebuild' (Pydantic v2+) or 'update_forward_refs' (Pydantic v1) "
                "on WorkflowResult model. ForwardRef resolution might fail."
            )


class BaseWorkflow(ABC):
    name: str = "base_workflow"
    trigger_tag_name: str = "unknown_workflow_trigger"
    allowed_agent_type: Optional[str] = None
    allowed_agent_state: Optional[str] = None
    description: str = "Base workflow description."
    expected_xml_schema: str = "<trigger_tag_name><param>value</param></trigger_tag_name>" # For documentation & prompt injection

    @abstractmethod
    async def execute(
        self,
        manager: 'AgentManager',
        agent: 'Agent',
        # MODIFIED: data_input can be ET.Element for strict XML workflows,
        # or Dict[str, str] for more lenient ones like plan creation.
        data_input: Any
    ) -> WorkflowResult:
        """
        Executes the core logic of the workflow.

        Args:
            manager: The AgentManager instance.
            agent: The Agent instance that triggered the workflow.
            data_input: The data extracted from the trigger. For XML-based triggers,
                        this is typically an ET.Element. For 'plan' workflow, this
                        will be a dict like {'title': str, 'raw_plan_body': str}.
        Returns:
            WorkflowResult: An object detailing the outcome and next steps.
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}', trigger='{self.trigger_tag_name}')>"