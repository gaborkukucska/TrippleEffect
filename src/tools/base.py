# START OF FILE src/tools/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field # Use Pydantic for structured parameters

# Consider a more structured way to define parameters if needed later
# For now, a dictionary or Pydantic model is suitable.

class ToolParameter(BaseModel):
    """Defines a single parameter for a tool."""
    name: str = Field(..., description="The name of the parameter.")
    type: str = Field(..., description="The expected type of the parameter (e.g., 'string', 'integer', 'boolean').")
    description: str = Field(..., description="A description of what the parameter represents.")
    required: bool = Field(default=True, description="Whether the parameter is required.")

class BaseTool(ABC):
    """
    Abstract Base Class for all tools that agents can use.

    Each tool must define its name, description, parameters, and implement
    the execute method.
    """
    name: str = "base_tool" # Unique identifier for the tool
    description: str = "Base tool description" # Description for LLMs/users
    parameters: List[ToolParameter] = [] # List of parameters the tool accepts

    @abstractmethod
    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Executes the tool's logic.

        Args:
            agent_id (str): The ID of the agent calling the tool.
            agent_sandbox_path (Path): The path to the agent's sandbox directory.
                                       Tools should restrict file operations to this path.
            **kwargs: The arguments provided for the tool, matching the defined parameters.

        Returns:
            Any: The result of the tool's execution. Should be serializable (e.g., string, dict).
                 Can also be an error message string.
        """
        pass

    def get_schema(self) -> Dict[str, Any]:
        """Returns a dictionary describing the tool's schema (name, description, parameters)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [param.dict() for param in self.parameters],
        }

# Example Usage (for illustration, not part of the base file):
#
# class MyTool(BaseTool):
#     name = "my_tool"
#     description = "Does something specific."
#     parameters = [
#         ToolParameter(name="input_file", type="string", description="Path to the input file.", required=True),
#         ToolParameter(name="iterations", type="integer", description="Number of iterations.", required=False)
#     ]
#
#     async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
#         input_file = kwargs.get("input_file")
#         iterations = kwargs.get("iterations", 1) # Default value
#         if not input_file:
#              return "Error: input_file parameter is required."
#
#         # --- Perform tool logic using input_file, iterations, agent_sandbox_path ---
#         # IMPORTANT: Ensure file operations respect agent_sandbox_path boundaries
#         # Example: full_path = agent_sandbox_path / input_file
#         # if not full_path.is_relative_to(agent_sandbox_path): return "Error: Path traversal detected"
#         # ... read/write full_path ...
#
#         result = f"Executed my_tool on {input_file} for {iterations} iterations by {agent_id}."
#         return result
