# START OF FILE src/tools/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from pathlib import Path

# Tool Parameter Definition
class ToolParameter(BaseModel):
    """Defines a single parameter for a tool."""
    name: str = Field(..., description="The name of the parameter.")
    type: str = Field(..., description="The expected type of the parameter (e.g., 'string', 'integer', 'boolean').")
    description: str = Field(..., description="A description of what the parameter represents.")
    required: bool = Field(default=True, description="Whether the parameter is required.")

# Abstract Base Class for Tools
class BaseTool(ABC):
    """
    Abstract Base Class for all tools that agents can use.

    Each tool must define its name, description, parameters, and implement
    the execute method.
    """
    name: str = "base_tool" # Unique identifier for the tool
    description: str = "Base tool description" # Detailed description for LLMs/users
    summary: Optional[str] = None # Optional brief summary for listing tools
    parameters: List[ToolParameter] = [] # List of parameters the tool accepts
    auth_level: str = "worker" # Authorization level: 'admin', 'pm', 'worker' (default: worker)

    # --- *** MODIFIED EXECUTE SIGNATURE *** ---
    @abstractmethod
    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        # Add optional context parameters for tools that need session info
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
        ) -> Any:
        """
        Executes the tool's logic.

        Args:
            agent_id (str): The ID of the agent calling the tool.
            agent_sandbox_path (Path): The path to the agent's private sandbox directory.
            project_name (Optional[str]): The name of the current project context (if available).
            session_name (Optional[str]): The name of the current session context (if available).
            **kwargs: The arguments provided for the tool, matching the defined parameters.

        Returns:
            Any: The result of the tool's execution. Should typically be a string for agent history,
                 or a specific structure if handled specially by AgentManager (like ManageTeamTool).
        """
        pass
    # --- *** END MODIFIED SIGNATURE *** ---

    @abstractmethod
    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None) -> str:
        """
        Returns a detailed string explaining how to use the tool, including actions,
        parameters, and examples. This is intended for on-demand requests by LLMs.

        Args:
            agent_context (Optional[Dict[str, Any]]): Information about the calling agent,
                                                     such as agent_id, type, project_name, team_id.
                                                     Tools can use this to provide more relevant examples.
        """
        pass

    def get_schema(self) -> Dict[str, Any]:
        """Returns a dictionary describing the tool's schema (name, description, parameters)."""
        return {
            "name": self.name,
            "description": self.description,
            "summary": self.summary or self.description, # Fallback to description if summary is None
            "parameters": [param.dict() for param in self.parameters],
            "auth_level": self.auth_level # Include auth_level in schema
        }
