# START OF FILE src/tools/manage_team.py
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter
import logging
import json # For potentially validating JSON config strings later

logger = logging.getLogger(__name__)

# Define valid actions
VALID_ACTIONS = [
    "create_agent",
    "delete_agent",
    "create_team",
    "delete_team",
    "add_agent_to_team",
    "remove_agent_from_team",
    "list_agents",
    "list_teams",
    # "get_agent_info", # Maybe add later
]

class ManageTeamTool(BaseTool):
    """
    Tool used by the Admin AI to dynamically manage agents and teams.
    This tool validates the request and signals the AgentManager to perform the actual action.
    It does NOT modify state directly. NO RESTART NEEDED.
    """
    name: str = "ManageTeamTool"
    description: str = (
        "Manages agents and teams dynamically within the system. "
        f"Use one of the valid actions: {', '.join(VALID_ACTIONS)}. "
        "Provide required parameters for each action. NO application restart is needed."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description=f"The management action to perform. Valid options: {', '.join(VALID_ACTIONS)}.",
            required=True,
        ),
        ToolParameter(
            name="agent_id",
            type="string",
            description="The unique ID for an agent. Required for delete_agent, add_agent_to_team, remove_agent_from_team. Optional for create_agent (if omitted, one will be generated).",
            required=False,
        ),
        ToolParameter(
            name="team_id",
            type="string",
            description="The unique ID for a team. Required for create_team, delete_team, add_agent_to_team, remove_agent_from_team. Also used optionally during create_agent.",
            required=False,
        ),
        ToolParameter(
            name="provider",
            type="string",
            description="LLM provider name (e.g., 'openrouter', 'ollama', 'openai'). Required for create_agent.",
            required=False,
        ),
        ToolParameter(
            name="model",
            type="string",
            description="LLM model name specific to the provider. Required for create_agent.",
            required=False,
        ),
        ToolParameter(
            name="system_prompt",
            type="string",
            description="The system prompt defining the agent's role and instructions. Required for create_agent.",
            required=False,
        ),
        ToolParameter(
            name="persona",
            type="string",
            description="The display name for the agent. Required for create_agent.",
            required=False,
        ),
         ToolParameter(
            name="temperature",
            type="float",
            description="Optional temperature setting for create_agent (defaults will be used if omitted).",
            required=False,
        ),
        # Add other relevant agent config params if needed (e.g., "extra_args" as JSON string?)
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Validates parameters for the requested management action.
        Returns a structured dictionary signaling the AgentManager to perform the action.

        Args:
            agent_id (str): The ID of the agent calling the tool (usually admin_ai).
            agent_sandbox_path (Path): The path to the caller's sandbox (not used here).
            **kwargs: Arguments containing 'action' and other relevant parameters.

        Returns:
            Dict[str, Any]: A dictionary containing status, action, validated params, and a message.
                            This dictionary is processed by the AgentManager.
        """
        action = kwargs.get("action")
        params = kwargs # Store all provided args

        logger.info(f"'{agent_id}' requested ManageTeamTool action '{action}' with params: {params}")

        if not action or action not in VALID_ACTIONS:
            error_msg = f"Error: Invalid or missing 'action'. Must be one of: {', '.join(VALID_ACTIONS)}."
            logger.error(error_msg)
            return {"status": "error", "action": action, "message": error_msg}

        # --- Parameter Validation based on Action ---
        required_params = []
        if action == "create_agent":
            required_params = ["provider", "model", "system_prompt", "persona"] # agent_id/team_id are optional
        elif action == "delete_agent":
            required_params = ["agent_id"]
        elif action == "create_team":
            required_params = ["team_id"]
        elif action == "delete_team":
            required_params = ["team_id"]
        elif action == "add_agent_to_team":
            required_params = ["agent_id", "team_id"]
        elif action == "remove_agent_from_team":
            required_params = ["agent_id", "team_id"]
        # list_agents, list_teams have no required params

        missing = [p for p in required_params if p not in params or not params[p]]
        if missing:
            error_msg = f"Error: Missing required parameters for action '{action}': {', '.join(missing)}."
            logger.error(error_msg)
            return {"status": "error", "action": action, "message": error_msg}

        # Optional: Add more specific validation (e.g., check agent_id format?)

        # If validation passes, return success signal for AgentManager
        success_msg = f"Request for action '{action}' validated. Signaling manager to proceed."
        logger.info(success_msg)
        return {
            "status": "success",
            "action": action,
            "params": params, # Pass all provided params to manager
            "message": success_msg # This becomes the tool result for the Admin AI's history
        }
