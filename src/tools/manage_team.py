# START OF FILE src/tools/manage_team.py
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter
import logging
import json # For potentially validating JSON config strings later

# Import agent type and state constants to list them in usage
from src.agents.constants import (
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE,
    WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED,
    BOOTSTRAP_AGENT_ID, DEFAULT_STATE
)


logger = logging.getLogger(__name__)

# Define valid actions - Added get_agent_details and set_agent_state
VALID_ACTIONS = [
    "create_agent",
    "delete_agent",
    "create_team",
    "delete_team",
    "add_agent_to_team",
    "remove_agent_from_team",
    "list_agents",
    "list_teams",
    "get_agent_details",
    "set_agent_state", # New action
]

class ManageTeamTool(BaseTool):
    """
    Tool used by the Admin AI or PM to dynamically manage agents and teams.
    This tool validates the request based on the specific action and signals
    the AgentManager/InteractionHandler to perform the actual action.
    It does NOT modify state directly. NO RESTART NEEDED.
    Provider/model are optional for create_agent; framework will select if omitted.
    Can now set agent states (e.g., to activate workers), but NOT for Admin AI.
    """
    name: str = "manage_team"
    auth_level: str = "pm" # PMs and Admins can use this
    summary: Optional[str] = "Manages agents/teams (CRUD, list, assign) and can set agent states (not Admin AI)."
    description: str = (
        "Manages agents and teams dynamically. "
        f"Valid actions: {', '.join(VALID_ACTIONS)}. "
        "Provide required parameters based on the action. For 'create_agent', 'provider' and 'model' are optional. 'set_agent_state' can change an agent's workflow state (e.g., to 'work' or 'worker_wait'), but CANNOT be used to change the state of the Admin AI ('admin_ai')."
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
            description="Agent ID. Required for: delete_agent, add_agent_to_team, remove_agent_from_team, get_agent_details, set_agent_state.",
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
        # --- NEW Parameter for set_agent_state ---
        ToolParameter(
            name="new_state",
            type="string",
            description="The target state for the agent (e.g., 'work', 'worker_wait', 'conversation'). Required for 'set_agent_state'. See tool usage for valid states per agent type.",
            required=False,
        ),
        # --- End NEW Parameter ---
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Validates parameters based on the specific management action requested.
        Returns a structured dictionary signaling the AgentManager/InteractionHandler to perform the action,
        OR returns a specific error message if validation fails for the given action.
        """
        action = kwargs.get("action")
        params = kwargs 

        logger.info(f"'{agent_id}' requested ManageTeamTool action '{action}' with params: {params}")

        if not action or action not in VALID_ACTIONS:
            error_msg = f"Error: Invalid or missing 'action'. Must be one of: {', '.join(VALID_ACTIONS)}."
            logger.error(error_msg)
            return {"status": "error", "action_requested": action, "message": error_msg, "result_data": None}

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
        elif action == "get_agent_details":
             if not params.get("agent_id"): error_message = "Error: Missing required 'agent_id' parameter for 'get_agent_details'."
        elif action == "set_agent_state":
            target_agent_id_for_state_change = params.get("agent_id")
            if not target_agent_id_for_state_change: missing.append("'agent_id'")
            if not params.get("new_state"): missing.append("'new_state'")
            if missing: error_message = f"Error: Missing required parameter(s) for 'set_agent_state': {', '.join(missing)}."
            # --- NEW: Restrict changing Admin AI's state ---
            elif target_agent_id_for_state_change == BOOTSTRAP_AGENT_ID:
                error_message = f"Error: Action 'set_agent_state' cannot be used to change the state of the Admin AI ('{BOOTSTRAP_AGENT_ID}'). Admin AI manages its own state."
            # --- END NEW Restriction ---


        if error_message:
            logger.error(f"ManageTeamTool validation failed for agent '{agent_id}', action '{action}': {error_message}")
            return {"status": "error", "action_requested": action, "message": error_message, "result_data": None}

        success_msg = f"Request for action '{action}' validated. Signaling InteractionHandler to proceed."
        logger.info(success_msg)
        return {
            "status": "success_signal_to_handler", 
            "action_to_perform": action, 
            "action_params": params, 
            "message": success_msg 
        }

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """
        Returns detailed usage instructions for the manage_team tool.
        If sub_action is specified, returns details for that specific sub-action.
        Otherwise, returns a summary of available sub-actions.
        """
        project_name_placeholder = agent_context.get('project_name', '{project_name}') if agent_context else '{project_name}'
        team_id_placeholder = agent_context.get('team_id', f'team_{project_name_placeholder}') if agent_context else f'team_{project_name_placeholder}'
        worker_agent_id_placeholder = f"worker_{project_name_placeholder}_1"
        pm_agent_id_placeholder = f"pm_{project_name_placeholder}"

        # Define valid states for PMs and Workers to list in the usage instructions
        valid_pm_states = [PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE, DEFAULT_STATE]
        valid_worker_states = [WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT, DEFAULT_STATE]

        sub_action_details = {
            "create_agent": f"""
        **Sub-Action: create_agent**
        Creates a new agent.
        *   `<persona>` (string, required): Display name.
        *   `<system_prompt>` (string, required): Agent's role instructions.
        *   `<agent_id>` (string, optional): Custom ID (e.g., `{worker_agent_id_placeholder}`), else generated.
        *   `<team_id>` (string, optional): Assigns to this team if provided (e.g., `{team_id_placeholder}`).
        *   `<provider>` (string, optional): LLM provider (e.g., 'ollama', 'openrouter'). Auto-selected if omitted.
        *   `<model>` (string, optional): LLM model (e.g., 'ollama/llama3.2:8b'). Auto-selected if omitted.
        *   `<temperature>` (float, optional): Model temperature.
        *   Example:
            ```xml
            <manage_team>
              <action>create_agent</action>
              <agent_id>{worker_agent_id_placeholder}</agent_id>
              <persona>Researcher</persona>
              <system_prompt>Research topics and provide summaries.</system_prompt>
              <team_id>{team_id_placeholder}</team_id>
            </manage_team>
            ```
        *   **XML Content Rules for Agent Creation:** Ensure plain text within tags, escape <, >, & if necessary. Keep prompts concise.
            """,
            "delete_agent": f"""
        **Sub-Action: delete_agent**
        Deletes an existing agent (not bootstrap agents).
        *   `<agent_id>` (string, required): Exact ID of the agent.
        *   Example: `<manage_team><action>delete_agent</action><agent_id>{worker_agent_id_placeholder}</agent_id></manage_team>`
            """,
            "create_team": f"""
        **Sub-Action: create_team**
        Creates a new team.
        *   `<team_id>` (string, required): Unique ID for the team.
        *   Example: `<manage_team><action>create_team</action><team_id>{team_id_placeholder}</team_id></manage_team>`
            """,
            "delete_team": f"""
        **Sub-Action: delete_team**
        Deletes an existing team.
        *   `<team_id>` (string, required): ID of the team to delete.
        *   Example: `<manage_team><action>delete_team</action><team_id>{team_id_placeholder}</team_id></manage_team>`
            """,
            "add_agent_to_team": f"""
        **Sub-Action: add_agent_to_team**
        Adds an agent to a team.
        *   `<agent_id>` (string, required): Agent ID.
        *   `<team_id>` (string, required): Team ID.
        *   Example: `<manage_team><action>add_agent_to_team</action><agent_id>{worker_agent_id_placeholder}</agent_id><team_id>{team_id_placeholder}</team_id></manage_team>`
            """,
            "remove_agent_from_team": f"""
        **Sub-Action: remove_agent_from_team**
        Removes an agent from a team.
        *   `<agent_id>` (string, required): Agent ID.
        *   `<team_id>` (string, required): Team ID.
        *   Example: `<manage_team><action>remove_agent_from_team</action><agent_id>{worker_agent_id_placeholder}</agent_id><team_id>{team_id_placeholder}</team_id></manage_team>`
            """,
            "list_agents": f"""
        **Sub-Action: list_agents**
        Lists active agents.
        *   `<team_id>` (string, optional): Filters by team (e.g., `{team_id_placeholder}`).
        *   Example (all): `<manage_team><action>list_agents</action></manage_team>`
        *   Example (team): `<manage_team><action>list_agents</action><team_id>{team_id_placeholder}</team_id></manage_team>`
            """,
            "list_teams": f"""
        **Sub-Action: list_teams**
        Lists all defined teams.
        *   Example: `<manage_team><action>list_teams</action></manage_team>`
            """,
            "get_agent_details": f"""
        **Sub-Action: get_agent_details**
        Retrieves detailed info about an agent.
        *   `<agent_id>` (string, required): Agent ID.
        *   Example: `<manage_team><action>get_agent_details</action><agent_id>{pm_agent_id_placeholder}</agent_id></manage_team>`
            """,
            "set_agent_state": f"""
        **Sub-Action: set_agent_state**
        Changes a non-Admin AI agent's workflow state and activates it if idle.
        *   `<agent_id>` (string, required): The ID of the agent whose state to change. **Cannot be '{BOOTSTRAP_AGENT_ID}'**.
        *   `<new_state>` (string, required): The target state.
            *   Valid states for Project Managers (PMs): {', '.join(valid_pm_states)}
            *   Valid states for Worker Agents: {', '.join(valid_worker_states)}
        *   Example (Activate a worker to 'work' state):
            ```xml
            <manage_team>
              <action>set_agent_state</action>
              <agent_id>{worker_agent_id_placeholder}</agent_id>
              <new_state>work</new_state>
            </manage_team>
            ```
        *   Example (Set PM to 'pm_manage' state):
            ```xml
            <manage_team>
              <action>set_agent_state</action>
              <agent_id>{pm_agent_id_placeholder}</agent_id>
              <new_state>pm_manage</new_state>
            </manage_team>
            ```
            """
        }

        if sub_action and sub_action in sub_action_details:
            return sub_action_details[sub_action].strip()
        elif sub_action:
            return f"Error: Sub-action '{sub_action}' is not recognized for the 'manage_team' tool. Valid sub-actions are: {', '.join(sub_action_details.keys())}."
        else:
            summary_usage = f"""
        **Tool Name:** manage_team
        **Description:** Dynamically manages agents and teams, and can set agent workflow states (except for the Admin AI).

        This tool has multiple sub-actions. To get detailed help for a specific sub-action, use the `tool_information` tool again with the `sub_action` parameter.

        **Available Sub-Actions:**
        """
            for sa_name in sub_action_details.keys():
                # Extract a brief summary for each sub_action (e.g., first line of its detail)
                brief_desc = sub_action_details[sa_name].strip().split('\n')[1].strip() # Second line is usually the description
                summary_usage += f"  - **{sa_name}**: {brief_desc}\n"

            summary_usage += f"""
        **Example to get details for 'create_agent':**
        ```xml
        <tool_information>
          <action>get_info</action>
          <tool_name>manage_team</tool_name>
          <sub_action>create_agent</sub_action>
        </tool_information>
        ```
        """
            return summary_usage.strip()