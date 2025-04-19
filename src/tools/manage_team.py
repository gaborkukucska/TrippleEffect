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
]

class ManageTeamTool(BaseTool):
    """
    Tool used by the Admin AI to dynamically manage agents and teams.
    This tool validates the request and signals the AgentManager to perform the actual action.
    It does NOT modify state directly. NO RESTART NEEDED.
    Provider/model are optional for create_agent; framework will select if omitted.
    """
    name: str = "ManageTeamTool"
    description: str = (
        "Manages agents and teams dynamically within the system. "
        f"Use one of the valid actions: {', '.join(VALID_ACTIONS)}. "
        "Provide required parameters for each action. For 'create_agent', 'provider' and 'model' are optional; if omitted, the system will select the best available model based on performance and availability. NO application restart is needed." # Updated description
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
            description="The unique ID for a team. Required for create_team, delete_team, add_agent_to_team, remove_agent_from_team. Optional for create_agent. **Also optional for 'list_agents' to filter by team.**",
            required=False,
        ),
        ToolParameter(
            name="provider",
            type="string",
            description="Optional: LLM provider name (e.g., 'openrouter', 'ollama'). If provided, must match the provider part of the chosen model.",
            required=False, # Now Optional
        ),
        ToolParameter(
            name="model",
            type="string",
            description="Optional: LLM model name specific to the provider (e.g., 'google/gemma-2-9b-it:free', 'ollama/llama3.2:3b-instruct-q4_K_M'). If provided, must be chosen from the 'Currently Available Models' list.",
            required=False, # Now Optional
        ),
        ToolParameter(
            name="system_prompt",
            type="string",
            description="The system prompt defining the agent's specific role and instructions (framework context is added automatically). Required for create_agent.",
            required=True, # Still Required for create_agent
        ),
        ToolParameter(
            name="persona",
            type="string",
            description="The display name for the agent. Required for create_agent.",
            required=True, # Still Required for create_agent
        ),
         ToolParameter(
            name="temperature",
            type="float",
            description="Optional temperature setting for create_agent (defaults will be used if omitted).",
            required=False,
        ),
        # Add other relevant agent config params if needed (e.g., "extra_args" as JSON string?)
    ]

    # --- UPDATED EXECUTE METHOD ---
    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Validates parameters for the requested management action.
        Checks required parameters based on the action, but allows provider/model
        to be omitted for create_agent.
        Returns a structured dictionary signaling the AgentManager to perform the action,
        OR returns a specific error message if validation fails.
        """
        action = kwargs.get("action")
        params = kwargs # Store all provided args

        logger.info(f"'{agent_id}' requested ManageTeamTool action '{action}' with params: {params}")

        if not action or action not in VALID_ACTIONS:
            error_msg = f"Error: Invalid or missing 'action'. Must be one of: {', '.join(VALID_ACTIONS)}."
            logger.error(error_msg)
            return {"status": "error", "action": action, "message": error_msg}

        # --- Specific Parameter Validation based on Action ---
        agent_id_param = params.get("agent_id")
        team_id_param = params.get("team_id")
        # provider_param = params.get("provider") # No longer strictly needed here
        # model_param = params.get("model")       # No longer strictly needed here
        prompt_param = params.get("system_prompt")
        persona_param = params.get("persona")

        error_message = None # Store validation error

        if action == "create_agent":
            # Provider and model are now optional here, checked later
            missing = []
            # Still require prompt and persona at this stage
            if not prompt_param: missing.append("'system_prompt'")
            if not persona_param: missing.append("'persona'")
            if missing:
                error_message = f"Error: Missing required parameter(s) for 'create_agent': {', '.join(missing)}."
        elif action == "delete_agent":
            if not agent_id_param:
                 error_message = "Error: Missing required 'agent_id' parameter for 'delete_agent'. Use 'list_agents' first if you don't have the exact ID."
        elif action == "create_team":
            if not team_id_param:
                 error_message = "Error: Missing required 'team_id' parameter for 'create_team'."
        elif action == "delete_team":
            if not team_id_param:
                 error_message = "Error: Missing required 'team_id' parameter for 'delete_team'."
        elif action == "add_agent_to_team":
             if not agent_id_param: error_message = "Error: Missing required 'agent_id' parameter for 'add_agent_to_team'."
             elif not team_id_param: error_message = "Error: Missing required 'team_id' parameter for 'add_agent_to_team'."
        elif action == "remove_agent_from_team":
            if not agent_id_param: error_message = "Error: Missing required 'agent_id' parameter for 'remove_agent_from_team'."
            elif not team_id_param: error_message = "Error: Missing required 'team_id' parameter for 'remove_agent_from_team'."
        # list_agents and list_teams have no required params here

        # Check for error message
        if error_message:
            logger.error(f"ManageTeamTool validation failed for agent '{agent_id}', action '{action}': {error_message}")
            return {"status": "error", "action": action, "message": error_message}

        # If validation passes, return success signal for AgentManager
        success_msg = f"Request for action '{action}' validated. Signaling manager to proceed."
        logger.info(success_msg)
        return {
            "status": "success",
            "action": action,
            "params": params, # Pass all original params
            "message": success_msg
        }
