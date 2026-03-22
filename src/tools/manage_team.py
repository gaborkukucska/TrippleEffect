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
    summary: Optional[str] = "Manages agents/teams (create_agent, set_agent_state, send_message, get_team_status) and can set agent states (not Admin AI). Use tool_information with sub_action for per-action help."
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
            description="System prompt for create_agent. Defines the agent's responsibilities.",
            required=False,
        ),
        ToolParameter(
            name="role",
            type="string",
            description="The exact kickoff role this agent fulfills (e.g., 'Coder', 'Tester'). Required for create_agent.",
            required=False,
        ),
        ToolParameter(
            name="persona",
            type="string",
            description="Display name for create_agent. Should match the role or be a descriptive title.",
            required=False,
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

    async def execute(self, agent_id: str, agent_sandbox_path: Path, project_name: Optional[str] = None, session_name: Optional[str] = None, **kwargs: Any) -> Any:
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
            # If only role or persona is provided, use it for both
            role = params.get("role")
            persona = params.get("persona")
            if role and not persona:
                params["persona"] = role
            elif persona and not role:
                params["role"] = persona
                persona = params.get("persona")
                
            if not params.get("role"): missing.append("'role'")
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
        worker_agent_id_placeholder = "W1"
        pm_agent_id_placeholder = "PM1"
        
        # Define valid states for PMs and Workers to list in the usage instructions
        valid_pm_states = [PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE, DEFAULT_STATE]
        valid_worker_states = [WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT, DEFAULT_STATE]
        
        common_header = """**Tool Name:** manage_team
**Description:** Essential tool for Project Managers to manage their worker team (creating, deleting, tracking state). Also allows Admins to create new PMs.
"""

        if sub_action == "create_agent":
            return common_header + f"""
**Sub-Action: create_agent**
Creates a new worker agent for your team.
*   `<role>` (string, required): The EXACT role name from your kickoff plan that this agent fulfills (e.g., 'Coder', 'UI_Designer'). This MUST match the 'next role to create' instructions exactly.
*   `<persona>` (string, required): Display name for the agent. This can be the same as the role or a descriptive title.
*   `<system_prompt>` (string, required): The main instruction set defining the agent's role, capabilities, and goal.
*   `<agent_id>` (string, optional): A custom unique ID for the agent (e.g., `{worker_agent_id_placeholder}`). If omitted, one will be generated.
*   `<team_id>` (string, optional): The ID of the team to assign this agent to (e.g., `{team_id_placeholder}`).
*   `<provider>` (string, optional): Specify an LLM provider (e.g., 'ollama-local').
*   `<model>` (string, optional): Specify an LLM model (e.g., 'qwen3:14b').
*   `<temperature>` (float, optional): Model temperature (e.g., 0.7). Defaults to framework settings if omitted.
*   Example:
    ```xml
    <manage_team>
      <action>create_agent</action>
      <role>UI_Designer</role>
      <persona>UI/UX Designer</persona>
      <system_prompt>You are a UI designer. Your goal is to design a clean, responsive web interface.</system_prompt>
      <provider>ollama-local</provider>
      <model>qwen3:14b</model>
    </manage_team>
    ```
*   **XML Content Rules for Agent Creation:** Ensure all content within tags like `<system_prompt>` or `<persona>` is plain text. If you need to include characters like `<`, `>`, or `&` within these text blocks, they MUST be XML escaped (e.g., `&lt;` for `<`, `&gt;` for `>`, `&amp;` for `&`). Keep prompts clear and concise.
"""
        elif sub_action == "delete_agent":
            return common_header + f"""
**Sub-Action: delete_agent**
Deletes an existing agent. Cannot delete bootstrap agents like Admin AI.
*   `<agent_id>` (string, required): The exact ID of the agent to be deleted.
*   Example: `<manage_team><action>delete_agent</action><agent_id>{worker_agent_id_placeholder}</agent_id></manage_team>`
"""
        elif sub_action == "create_team":
            return common_header + f"""
**Sub-Action: create_team**
Creates a new team. The agent calling this action will automatically be added to the new team.
*   `<team_id>` (string, required): A unique ID for the new team (e.g., `{team_id_placeholder}`).
*   Example: `<manage_team><action>create_team</action><team_id>{team_id_placeholder}</team_id></manage_team>`
"""
        elif sub_action == "delete_team":
            return common_header + f"""
**Sub-Action: delete_team**
Deletes an existing team. Agents in the team will become team-less but will not be deleted.
*   `<team_id>` (string, required): The ID of the team to delete.
*   Example: `<manage_team><action>delete_team</action><team_id>{team_id_placeholder}</team_id></manage_team>`
"""
        elif sub_action == "add_agent_to_team":
            return common_header + f"""
**Sub-Action: add_agent_to_team**
Adds an existing agent to a team.
*   `<agent_id>` (string, required): The ID of the agent to add.
*   `<team_id>` (string, required): The ID of the team to add the agent to.
*   Example: `<manage_team><action>add_agent_to_team</action><agent_id>{worker_agent_id_placeholder}</agent_id><team_id>{team_id_placeholder}</team_id></manage_team>`
"""
        elif sub_action == "remove_agent_from_team":
            return common_header + f"""
**Sub-Action: remove_agent_from_team**
Removes an agent from a team. The agent will not be deleted.
*   `<agent_id>` (string, required): The ID of the agent to remove.
*   `<team_id>` (string, required): The ID of the team to remove the agent from.
*   Example: `<manage_team><action>remove_agent_from_team</action><agent_id>{worker_agent_id_placeholder}</agent_id><team_id>{team_id_placeholder}</team_id></manage_team>`
"""
        elif sub_action == "list_agents":
            return common_header + f"""
**Sub-Action: list_agents**
Lists active agents.
*   `<team_id>` (string, optional): If provided, filters the list to agents only within the specified team (e.g., `{team_id_placeholder}`).
*   Example (list all agents): `<manage_team><action>list_agents</action></manage_team>`
*   Example (list agents in a specific team): `<manage_team><action>list_agents</action><team_id>{team_id_placeholder}</team_id></manage_team>`
"""
        elif sub_action == "list_teams":
            return common_header + f"""
**Sub-Action: list_teams**
Lists all currently defined teams.
*   Example: `<manage_team><action>list_teams</action></manage_team>`
"""
        elif sub_action == "get_agent_details":
            return common_header + f"""
**Sub-Action: get_agent_details**
Retrieves detailed information about a specific agent.
*   `<agent_id>` (string, required): The ID of the agent whose details are requested.
*   Example: `<manage_team><action>get_agent_details</action><agent_id>{pm_agent_id_placeholder}</agent_id></manage_team>`
"""
        elif sub_action == "set_agent_state":
            return common_header + f"""
**Sub-Action: set_agent_state**
Changes a non-Admin AI agent's workflow state. If the agent is IDLE, this will also trigger its activation.
*   `<agent_id>` (string, required): The ID of the agent whose state is to be changed. **CRITICAL: This cannot be '{BOOTSTRAP_AGENT_ID}' (Admin AI).**
*   `<new_state>` (string, required): The target state for the agent.
    *   Valid states for Project Manager (PM) type agents: {', '.join(valid_pm_states)}
    *   Valid states for Worker type agents: {', '.join(valid_worker_states)}
*   Example (Activate a worker agent by setting its state to 'work'):
    ```xml
    <manage_team>
      <action>set_agent_state</action>
      <agent_id>{worker_agent_id_placeholder}</agent_id>
      <new_state>work</new_state>
    </manage_team>
    ```
"""
        elif sub_action:
             return f"Error: Sub-action '{sub_action}' is not recognized for the 'manage_team' tool."

        return common_header + """
**CRITICAL FOR PLANNING:** You MUST check `list_agents` to see the current composition of the team BEFORE attempting to `create_agent` to avoid fulfilling the same role twice.

**Available Sub-Actions Summary:**
1.  **create_agent:** Creates a new worker agent for your team.
2.  **delete_agent:** Deletes an existing agent.
3.  **create_team:** Creates a new team.
4.  **delete_team:** Deletes an existing team.
5.  **add_agent_to_team:** Adds an existing agent to a team.
6.  **remove_agent_from_team:** Removes an agent from a team.
7.  **list_agents:** Lists active agents.
8.  **list_teams:** Lists all currently defined teams.
9.  **get_agent_details:** Retrieves detailed information about a specific agent.
10. **set_agent_state:** Changes a non-Admin AI agent's workflow state.

**To get detailed instructions and parameter lists for a specific action, call:**
<tool_information>
  <action>get_info</action>
  <tool_name>manage_team</tool_name>
  <sub_action>ACTION_NAME</sub_action>
</tool_information>
"""