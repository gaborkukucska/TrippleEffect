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
            description="The unique ID for a team. Required for create_team, delete_team, add_agent_to_team, remove_agent_from_team. Optional for create_agent. **Also optional for 'list_agents' to filter by team.**", # Updated description
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
            description="LLM model name specific to the provider (must be from the allowed list). Required for create_agent.", # Added note about allowed list
            required=False,
        ),
        ToolParameter(
            name="system_prompt",
            type="string",
            description="The system prompt defining the agent's specific role and instructions (framework context is added automatically). Required for create_agent.", # Added note about framework context
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

# Replace the existing execute method:
    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Validates parameters for the requested management action.
        Returns a structured dictionary signaling the AgentManager to perform the action,
        OR returns a specific error message if required parameters for delete/remove are missing.

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
        agent_id_param = params.get("agent_id") # Get agent_id specifically for checks
        team_id_param = params.get("team_id")   # Get team_id specifically for checks

        if action == "create_agent":
            required_params = ["provider", "model", "system_prompt", "persona"]
        elif action == "delete_agent":
            required_params = ["agent_id"]
            # *** ADDED SPECIFIC CHECK ***
            if not agent_id_param:
                 error_msg = "Error: Missing required 'agent_id' parameter for 'delete_agent'. Use 'list_agents' first if you don't have the exact ID."
                 logger.error(f"{agent_id} failed delete_agent call: {error_msg}")
                 return {"status": "error", "action": action, "message": error_msg}
            # *** END SPECIFIC CHECK ***
        elif action == "create_team":
            required_params = ["team_id"]
        elif action == "delete_team":
            required_params = ["team_id"]
            # Optional: Add check if team_id is missing? Assumed present by logic flow.
        elif action == "add_agent_to_team":
            required_params = ["agent_id", "team_id"]
            # Optional: Add checks if agent_id or team_id are missing?
        elif action == "remove_agent_from_team":
            required_params = ["agent_id", "team_id"]
             # *** ADDED SPECIFIC CHECK ***
            if not agent_id_param:
                 error_msg = "Error: Missing required 'agent_id' parameter for 'remove_agent_from_team'."
                 logger.error(f"{agent_id} failed remove_agent_from_team call: {error_msg}")
                 return {"status": "error", "action": action, "message": error_msg}
            if not team_id_param:
                 error_msg = "Error: Missing required 'team_id' parameter for 'remove_agent_from_team'."
                 logger.error(f"{agent_id} failed remove_agent_from_team call: {error_msg}")
                 return {"status": "error", "action": action, "message": error_msg}
            # *** END SPECIFIC CHECK ***

        # Generic check for other required params (like for create_agent)
        missing = [p for p in required_params if p not in params or not params[p]]
        if missing:
            # This check might be redundant now for delete/remove, but keep for create_agent etc.
            error_msg = f"Error: Missing required parameters for action '{action}': {', '.join(missing)}."
            logger.error(error_msg)
            return {"status": "error", "action": action, "message": error_msg}


        # If validation passes, return success signal for AgentManager
        success_msg = f"Request for action '{action}' validated. Signaling manager to proceed."
        logger.info(success_msg)
        return {
            "status": "success",
            "action": action,
            "params": params, # Pass all original params
            "message": success_msg
        }
