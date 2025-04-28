# START OF FILE src/tools/manage_team.py
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter
import logging
import json # For potentially validating JSON config strings later

logger = logging.getLogger(__name__)

# Define valid actions - Added get_agent_details
VALID_ACTIONS = [
    "create_agent",
    "delete_agent",
    "create_team",
    "delete_team",
    "add_agent_to_team",
    "remove_agent_from_team",
    "list_agents",
    "list_teams",
    "get_agent_details", # New action
]

class ManageTeamTool(BaseTool):
    """
    Tool used by the Admin AI to dynamically manage agents and teams.
    This tool validates the request based on the specific action and signals
    the AgentManager to perform the actual action.
    It does NOT modify state directly. NO RESTART NEEDED.
    Provider/model are optional for create_agent; framework will select if omitted.
    """
    name: str = "manage_team" # Changed from ManageTeamTool to match XML tag
    auth_level: str = "pm" # Admin only
    summary: Optional[str] = "Manages agents and teams (create, delete, list, assign). (Admin only)"
    description: str = ( # Updated description
        "Manages agents and teams dynamically within the system. "
        f"Use one of the valid actions: {', '.join(VALID_ACTIONS)}. "
        "Provide required parameters based on the specific action chosen. For 'create_agent', 'provider' and 'model' are optional (system selects if omitted). Use 'get_agent_details' to retrieve detailed information about a specific agent. NO application restart is needed."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description=f"The management action to perform. Valid options: {', '.join(VALID_ACTIONS)}.",
            required=True, # Action is always required
        ),
        ToolParameter(
            name="agent_id",
            type="string",
            description="Agent ID. Required for: delete_agent, add_agent_to_team, remove_agent_from_team, get_agent_details. Optional for create_agent.", # Added get_agent_details requirement
            required=False, # Not universally required
        ),
        ToolParameter(
            name="team_id",
            type="string",
            description="Team ID. Required for: create_team, delete_team, add_agent_to_team, remove_agent_from_team. Optional for create_agent, list_agents.",
            required=False, # Not universally required
        ),
        ToolParameter(
            name="provider",
            type="string",
            description="Optional: LLM provider name for create_agent.",
            required=False, # Optional: Handled by auto-select
        ),
        ToolParameter(
            name="model",
            type="string",
            description="Optional: LLM model name for create_agent. Must be valid if provided.",
            required=False, # Optional: Handled by auto-select
        ),
        ToolParameter(
            name="system_prompt",
            type="string",
            description="System prompt for create_agent. Defines the agent's role.",
            required=False, # Required only for create_agent, checked in execute
        ),
        ToolParameter(
            name="persona",
            type="string",
            description="Display name for create_agent.",
            required=False, # Required only for create_agent, checked in execute
        ),
         ToolParameter(
            name="temperature",
            type="float",
            description="Optional temperature setting for create_agent.",
            required=False,
        ),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Validates parameters based on the specific management action requested.
        Returns a structured dictionary signaling the AgentManager to perform the action,
        OR returns a specific error message if validation fails for the given action.
        """
        action = kwargs.get("action")
        params = kwargs # Store all provided args (tool parser already extracted them)

        logger.info(f"'{agent_id}' requested ManageTeamTool action '{action}' with params: {params}")

        if not action or action not in VALID_ACTIONS:
            error_msg = f"Error: Invalid or missing 'action'. Must be one of: {', '.join(VALID_ACTIONS)}."
            logger.error(error_msg)
            return {"status": "error", "action": action, "message": error_msg}

        # --- Action-Specific Parameter Validation ---
        error_message = None
        missing = []

        if action == "create_agent":
            if not params.get("system_prompt"): missing.append("'system_prompt'")
            if not params.get("persona"): missing.append("'persona'")
            if missing: error_message = f"Error: Missing required parameter(s) for 'create_agent': {', '.join(missing)}."
        elif action == "delete_agent":
            if not params.get("agent_id"): error_message = "Error: Missing required 'agent_id' parameter for 'delete_agent'."
        elif action == "create_team":
            if not params.get("team_id"): error_message = "Error: Missing required 'team_id' parameter for 'create_team'."
        elif action == "delete_team":
            if not params.get("team_id"): error_message = "Error: Missing required 'team_id' parameter for 'delete_team'."
        elif action == "add_agent_to_team":
            if not params.get("agent_id"): missing.append("'agent_id'")
            if not params.get("team_id"): missing.append("'team_id'")
            if missing: error_message = f"Error: Missing required parameter(s) for 'add_agent_to_team': {', '.join(missing)}."
        elif action == "remove_agent_from_team":
            if not params.get("agent_id"): missing.append("'agent_id'")
            if not params.get("team_id"): missing.append("'team_id'")
            if missing: error_message = f"Error: Missing required parameter(s) for 'remove_agent_from_team': {', '.join(missing)}."
        # --- NEW: Validation for get_agent_details ---
        elif action == "get_agent_details":
             if not params.get("agent_id"): error_message = "Error: Missing required 'agent_id' parameter for 'get_agent_details'."
        # --- End NEW Validation ---
        # list_agents and list_teams have no specific required params checked here

        # Check for validation error
        if error_message:
            logger.error(f"ManageTeamTool validation failed for agent '{agent_id}', action '{action}': {error_message}")
            return {"status": "error", "action": action, "message": error_message}

        # If validation passes for the specific action, return success signal
        success_msg = f"Request for action '{action}' validated. Signaling manager to proceed."
        logger.info(success_msg)
        return {
            "status": "success",
            "action": action,
            "params": params, # Pass all original params parsed by core
            "message": success_msg
        }

    # --- Detailed Usage Method ---
    def get_detailed_usage(self) -> str:
        """Returns detailed usage instructions for the manage_team tool."""
        usage = """
        **Tool Name:** manage_team

        **Description:** Dynamically manages agents and teams within the system. Does not require application restart.

        **Actions & Parameters:**

        1.  **create_agent:** Creates a new agent.
            *   `<persona>` (string, required): Display name for the agent (e.g., 'Python Coder', 'Report Writer').
            *   `<system_prompt>` (string, required): The instructions defining the agent's role and task.
            *   `<agent_id>` (string, optional): Specify a custom ID. If omitted, a unique ID will be generated.
            *   `<team_id>` (string, optional): If provided, adds the new agent directly to this team.
            *   `<provider>` (string, optional): Specify LLM provider (e.g., 'openai', 'ollama', 'openrouter'). If omitted, the system selects automatically.
            *   `<model>` (string, optional): Specify LLM model (e.g., 'gpt-4o', 'ollama/llama3.2:8b'). If omitted, the system selects automatically. Must be valid for the chosen provider if specified.
            *   `<temperature>` (float, optional): Set model temperature (e.g., 0.7). Defaults vary.
            *   Example:
                ```xml
                <manage_team>
                  <action>create_agent</action>
                  <persona>Data Analyst</persona>
                  <system_prompt>Analyze the provided CSV data, identify trends, and generate a summary report.</system_prompt>
                  <team_id>data_analysis_team</team_id>
                  <model>openai/gpt-4o</model>
                </manage_team>
                ```

        2.  **delete_agent:** Deletes an existing agent. Cannot delete bootstrap agents (like 'admin_ai').
            *   `<agent_id>` (string, required): The exact ID of the agent to delete.
            *   Example: `<manage_team><action>delete_agent</action><agent_id>agent_171...</agent_id></manage_team>`

        3.  **create_team:** Creates a new, empty team.
            *   `<team_id>` (string, required): The unique ID for the new team (e.g., 'web_dev_crew').
            *   Example: `<manage_team><action>create_team</action><team_id>research_group</team_id></manage_team>`

        4.  **delete_team:** Deletes an existing team. Agents within the team are NOT deleted but become team-less.
            *   `<team_id>` (string, required): The ID of the team to delete.
            *   Example: `<manage_team><action>delete_team</action><team_id>old_project_team</team_id></manage_team>`

        5.  **add_agent_to_team:** Adds an existing agent to a team.
            *   `<agent_id>` (string, required): The ID of the agent to add.
            *   `<team_id>` (string, required): The ID of the team to add the agent to.
            *   Example: `<manage_team><action>add_agent_to_team</action><agent_id>agent_abc...</agent_id><team_id>frontend_devs</team_id></manage_team>`

        6.  **remove_agent_from_team:** Removes an agent from a team. The agent remains active but team-less.
            *   `<agent_id>` (string, required): The ID of the agent to remove.
            *   `<team_id>` (string, required): The ID of the team to remove the agent from.
            *   Example: `<manage_team><action>remove_agent_from_team</action><agent_id>agent_xyz...</agent_id><team_id>backend_devs</team_id></manage_team>`

        7.  **list_agents:** Lists currently active agents.
            *   `<team_id>` (string, optional): If provided, lists only agents in that specific team. Otherwise, lists all active agents.
            *   Example (List all): `<manage_team><action>list_agents</action></manage_team>`
            *   Example (List team): `<manage_team><action>list_agents</action><team_id>design_team</team_id></manage_team>`

        8.  **list_teams:** Lists all currently defined teams.
            *   No parameters needed.
            *   Example: `<manage_team><action>list_teams</action></manage_team>`

        9.  **get_agent_details:** Retrieves detailed information about a specific agent (config, status, history summary).
            *   `<agent_id>` (string, required): The exact ID of the agent to get details for.
            *   Example: `<manage_team><action>get_agent_details</action><agent_id>admin_ai</agent_id></manage_team>`

        **Important Notes:**
        *   Use exact `agent_id`s obtained from `create_agent` feedback or `list_agents` for reliable targeting.
        *   Team management actions (`create_team`, `delete_team`, `add_agent_to_team`, `remove_agent_from_team`) only affect team structures, not the agents themselves (except for team assignment).
        """
        return usage.strip()
