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

# --- No Project Managers through create_agent (Round 2p) ------------------------------------------------------------------
# 2026-10-21 18:32:13: after two Guardian stall reports about PM1, Admin AI called create_agent with target_agent_id 'PM2' and
# role 'Project Manager (WiFi_Snake_Game_Multiplayer)'. create_agent only makes workers, so this became worker W3 with a PM
# persona - one more agent on the single shared model, and nobody to give it work. PMs are created by project approval.
import re as _re
_PM_ROLE_RE = _re.compile(r"\bproject[\s_-]*manag(?:er|ement)\b|^\s*pm\s*\d*\s*$|\(@?pm_agent\)", _re.IGNORECASE)
_PM_ID_RE = _re.compile(r"^\s*pm\d*\s*$", _re.IGNORECASE)
PM_CREATE_REFUSAL = (
    "Error: create_agent makes WORKER agents only, so NO agent was created. A Project Manager is created by the framework "
    "when the user approves a project plan, and this project already has one. To help a stalled Project Manager, send it ONE "
    "message with send_message that names the concrete next step (for example which task_id to assign to which idle worker)."
)


def looks_like_project_manager_request(params: Dict[str, Any]) -> bool:
    """True if a create_agent call asks for a Project Manager (by role, persona or requested agent id)."""
    for key in ("role", "persona", "agent_role", "name"):
        value = params.get(key)
        if isinstance(value, str) and _PM_ROLE_RE.search(value):
            return True
    target = params.get("target_agent_id") or params.get("agent_id")
    return isinstance(target, str) and bool(_PM_ID_RE.match(target))


# Define valid actions - Added get_agent_details and set_agent_state
# --- Team id placeholders (Round 2h) --------------------------------------------------------------------------------------
# 2026-10-19 21:38-21:47 (qwen3.5:9b): PM1 called create_team without team_id (rejected), then create_agent and list_agents
# with team_id 'None'; the framework created a team literally named 'None'. A missing or placeholder team id now means the
# project's team 'team_<project>', the id the kick-off directive asks for.
NULL_TEAM_IDS = frozenset({"", "none", "null", "nil", "n/a", "na", "undefined", "team_none"})


def is_placeholder_team_id(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in NULL_TEAM_IDS)


def normalize_team_id(action: Optional[str], params: Dict[str, Any], project_name: Optional[str]) -> Optional[str]:
    """
    Replaces a placeholder team_id ('None', 'null', 'N/A', '') with 'team_<project>' for team actions, and fills in a
    missing team_id for create_team. A create_agent / list_agents call without team_id is left alone (the creator's team
    and "all agents" are used then). Returns a note when something was changed, else None.
    """
    if not project_name or action not in ("create_team", "create_agent", "add_agent_to_team", "list_agents"):
        return None
    has_key = "team_id" in params
    if not is_placeholder_team_id(params.get("team_id")):
        return None
    if not has_key and action != "create_team":
        return None
    default_team = f"team_{project_name}"
    original = params.get("team_id")
    params["team_id"] = default_team
    return f"team_id {original!r} replaced by the project team '{default_team}'"


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
    # Round 2w: the summary named send_message and get_team_status, which are not actions of this tool
    summary: Optional[str] = "Manages agents/teams (create_agent, delete_agent, create_team, list_agents, list_teams, get_agent_details, set_agent_state, ...) and can set agent states (not Admin AI). Use tool_information with sub_action for per-action help."
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
            name="target_agent_id",
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
            aliases=["sys_prompt", "prompt", "instructions", "instruction", "system"]
        ),
        ToolParameter(
            name="role",
            type="string",
            description="The exact kickoff role this agent fulfills (e.g., 'Coder', 'Tester'). Required for create_agent.",
            required=False,
            aliases=["agent_role", "job", "role_name"]
        ),
        ToolParameter(
            name="persona",
            type="string",
            description="Display name for create_agent. Should match the role or be a descriptive title.",
            required=False,
            aliases=["name", "display_name", "persona_name"]
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

        # Resolve common LLM hallucinated action names to valid ones
        ACTION_ALIASES = {
            "add_agent": "create_agent",
            "remove_agent": "delete_agent",
            "add_team": "create_team",
            "remove_team": "delete_team",
            "change_state": "set_agent_state",
            "update_state": "set_agent_state",
        }
        if action and action in ACTION_ALIASES:
            resolved = ACTION_ALIASES[action]
            logger.info(f"ManageTeamTool: Resolved action alias '{action}' -> '{resolved}' for agent '{agent_id}'")
            action = resolved
            params["action"] = resolved

        logger.info(f"'{agent_id}' requested ManageTeamTool action '{action}' with params: {params}")

        if not action or action not in VALID_ACTIONS:
            error_msg = (
                # Round 3v: tool calls are native - the 'action' goes into the call's arguments (not an "XML block")
                f"Error: Invalid or missing 'action'. Put a valid 'action' into the arguments of your `manage_team` tool call.\n"
                f"For example, these arguments:\n"
                f"{{\n"
                f"  \"action\": \"create_agent\",\n"
                f"  \"role\": \"Engineer\",\n"
                f"  \"persona\": \"A senior engineer\",\n"
                f"  \"system_prompt\": \"You are...\"\n"
                f"}}\n\n"
                f"Valid actions are: {', '.join(VALID_ACTIONS)}.\n"
                f"Do NOT just send thoughts without a valid tool call!"
            )
            logger.error(f"'{agent_id}' requested ManageTeamTool but action '{action}' is missing or invalid.")
            return {"status": "error", "action_requested": action, "message": error_msg, "result_data": None}

        team_note = normalize_team_id(action, params, project_name)  # Round 2h
        if team_note:
            logger.info(f"ManageTeamTool: {team_note} (agent '{agent_id}', action '{action}')")

        error_message = None
        missing = []

        if action == "create_agent":
            # Map aliases for system_prompt (e.g. 4B models often send 'sys_prompt' or 'prompt')
            sys_prompt_alt = (
                params.get("system_prompt")
                or params.get("sys_prompt")
                or params.get("prompt")
                or params.get("instructions")
                or params.get("instruction")
                or params.get("system_instructions")
                or params.get("system")
            )
            if sys_prompt_alt:
                params["system_prompt"] = sys_prompt_alt

            # Map aliases for role and persona
            role_alt = params.get("role") or params.get("agent_role") or params.get("job") or params.get("role_name")
            if role_alt:
                params["role"] = role_alt

            persona_alt = params.get("persona") or params.get("name") or params.get("display_name") or params.get("persona_name")
            if persona_alt:
                params["persona"] = persona_alt

            # If only role or persona is provided, use it for both
            role = params.get("role")
            persona = params.get("persona")
            if role and not persona:
                params["persona"] = role
            elif persona and not role:
                params["role"] = persona
                persona = params.get("persona")

            # Fallback for system_prompt if role is provided
            if not params.get("system_prompt") and role:
                params["system_prompt"] = f"You are a {role} for this project. Implement your assigned tasks with clean, tested code."

            if not params.get("role"): missing.append("'role'")
            if not params.get("system_prompt"): missing.append("'system_prompt'")
            if not params.get("persona"): missing.append("'persona'")
            if looks_like_project_manager_request(params):  # Round 2p
                error_message = PM_CREATE_REFUSAL
            elif missing: 
                error_message = (
                    f"Error: Missing required parameter(s) for 'create_agent': {', '.join(missing)}.\n"
                    f"You MUST provide the 'role' explicitly based on your kickoff plan. Do not omit these required fields.\n"
                    f"Example arguments of your `manage_team` tool call:\n"
                    f"{{\n"
                    f"  \"action\": \"create_agent\",\n"
                    f"  \"role\": \"Developer\",\n"
                    f"  \"persona\": \"Senior Developer\",\n"
                    f"  \"system_prompt\": \"You are an expert coder. Write clean python code.\"\n"
                    f"}}"
                )
        elif action == "delete_agent":
            if not params.get("target_agent_id"): error_message = "Error: Missing required 'target_agent_id' parameter for 'delete_agent'."
        elif action == "create_team":
            if not params.get("team_id"): error_message = "Error: Missing required 'team_id' parameter for 'create_team'."
        elif action == "delete_team":
            if not params.get("team_id"): error_message = "Error: Missing required 'team_id' parameter for 'delete_team'."
        elif action == "add_agent_to_team":
            if not params.get("target_agent_id"): missing.append("'target_agent_id'")
            if not params.get("team_id"): missing.append("'team_id'")
            if missing: error_message = f"Error: Missing required parameter(s) for 'add_agent_to_team': {', '.join(missing)}."
        elif action == "remove_agent_from_team":
            if not params.get("target_agent_id"): missing.append("'target_agent_id'")
            if not params.get("team_id"): missing.append("'team_id'")
            if missing: error_message = f"Error: Missing required parameter(s) for 'remove_agent_from_team': {', '.join(missing)}."
        elif action == "get_agent_details":
             if not params.get("target_agent_id"): error_message = "Error: Missing required 'target_agent_id' parameter for 'get_agent_details'."
        elif action == "set_agent_state":
            target_agent_id_for_state_change = params.get("target_agent_id")
            if not target_agent_id_for_state_change: missing.append("'target_agent_id'")
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
        # Round 2w: all states the workflow manager accepts (the list named only 4 of 9 PM and 4 of 7 worker states)
        from src.agents.constants import (PM_STATE_ACTIVATE_WORKERS, PM_STATE_AUDIT, PM_STATE_BUILD_TEAM_TASKS,
                                          PM_STATE_REPORT_CHECK, PM_STATE_STANDBY, WORKER_STATE_DECOMPOSE,
                                          WORKER_STATE_REPORT, WORKER_STATE_TEST)
        valid_pm_states = [PM_STATE_STARTUP, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS, PM_STATE_WORK,
                           PM_STATE_MANAGE, PM_STATE_REPORT_CHECK, PM_STATE_AUDIT, PM_STATE_STANDBY, DEFAULT_STATE]
        valid_worker_states = [WORKER_STATE_STARTUP, WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK, WORKER_STATE_TEST,
                               WORKER_STATE_REPORT, WORKER_STATE_WAIT, DEFAULT_STATE]
        
        common_header = """**Tool Name:** manage_team
**Description:** Essential tool for Project Managers to manage their worker team (creating, deleting, tracking state). Also allows Admins to create new PMs.
"""

        if sub_action == "create_agent":
            return common_header + f"""\
**Sub-Action: create_agent**
Creates a new worker agent for your team.
*   `<role>` (string, required): The EXACT role name from your kickoff plan that this agent fulfills (e.g., 'Coder', 'UI_Designer'). This MUST match the 'next role to create' instructions exactly.
*   `<persona>` (string, required): Display name for the agent. This can be the same as the role or a descriptive title.
*   `<system_prompt>` (string, required): The main instruction set defining the agent's role, capabilities, and goal.
*   Agent IDs are given by the framework (W1, W2, ...): do NOT send an `agent_id`. The new ID is in the result.
*   `<team_id>` (string, optional): The ID of the team to assign this agent to (e.g., `{team_id_placeholder}`).
*   `<provider>` and `<model>` (string, optional): LEAVE THESE OUT. The framework automatically picks a valid, available model for every new agent. Only set them if you were explicitly given an exact provider and model name; invented names are not available and are ignored.
*   `<temperature>` (float, optional): Model temperature (e.g., 0.7). Defaults to framework settings if omitted.
*   Example:
    ```json
    {{
      "action": "create_agent",
      "role": "UI_Designer",
      "persona": "UI/UX Designer",
      "system_prompt": "You are a UI designer. Your goal is to design a clean, responsive web interface."
    }}
    ```
*   **JSON Content Rules for Agent Creation:** Ensure all content is properly JSON escaped. Keep prompts clear and concise.
"""
        elif sub_action == "delete_agent":
            return common_header + f"""\
**Sub-Action: delete_agent**
Deletes an existing agent. Cannot delete bootstrap agents like Admin AI.
*   `<target_agent_id>` (string, required): The exact ID of the agent to be deleted.
*   Example: `{{"action": "delete_agent", "target_agent_id": "{worker_agent_id_placeholder}"}}`
"""
        elif sub_action == "create_team":
            return common_header + f"""\
**Sub-Action: create_team**
Creates a new team. The agent calling this action will automatically be added to the new team.
*   `<team_id>` (string, required): A unique ID for the new team (e.g., `{team_id_placeholder}`).
*   Example: `{{"action": "create_team", "team_id": "{team_id_placeholder}"}}`
"""
        elif sub_action == "delete_team":
            return common_header + f"""\
**Sub-Action: delete_team**
Deletes an existing team. Agents in the team will become team-less but will not be deleted.
*   `<team_id>` (string, required): The ID of the team to delete.
*   Example: `{{"action": "delete_team", "team_id": "{team_id_placeholder}"}}`
"""
        elif sub_action == "add_agent_to_team":
            return common_header + f"""\
**Sub-Action: add_agent_to_team**
Adds an existing agent to a team.
*   `<target_agent_id>` (string, required): The ID of the agent to add.
*   `<team_id>` (string, required): The ID of the team to add the agent to.
*   Example: `{{"action": "add_agent_to_team", "target_agent_id": "{worker_agent_id_placeholder}", "team_id": "{team_id_placeholder}"}}`
"""
        elif sub_action == "remove_agent_from_team":
            return common_header + f"""\
**Sub-Action: remove_agent_from_team**
Removes an agent from a team. The agent will not be deleted.
*   `<target_agent_id>` (string, required): The ID of the agent to remove.
*   `<team_id>` (string, required): The ID of the team to remove the agent from.
*   Example: `{{"action": "remove_agent_from_team", "target_agent_id": "{worker_agent_id_placeholder}", "team_id": "{team_id_placeholder}"}}`
"""
        elif sub_action == "list_agents":
            return common_header + f"""\
**Sub-Action: list_agents**
Lists active agents.
*   `<team_id>` (string, optional): If provided, filters the list to agents only within the specified team (e.g., `{team_id_placeholder}`).
*   Example (list all agents): `{{"action": "list_agents"}}`
*   Example (list agents in a specific team): `{{"action": "list_agents", "team_id": "{team_id_placeholder}"}}`
"""
        elif sub_action == "list_teams":
            return common_header + """\
**Sub-Action: list_teams**
Lists all currently defined teams.
*   Example: `{"action": "list_teams"}`
"""
        elif sub_action == "get_agent_details":
            return common_header + f"""\
**Sub-Action: get_agent_details**
Retrieves detailed information about a specific agent.
*   `<target_agent_id>` (string, required): The ID of the agent whose details are requested.
*   Example: `{{"action": "get_agent_details", "target_agent_id": "{pm_agent_id_placeholder}"}}`
"""
        elif sub_action == "set_agent_state":
            return common_header + f"""\
**Sub-Action: set_agent_state**
Changes a non-Admin AI agent's workflow state. If the agent is IDLE, this will also trigger its activation.
*   `<target_agent_id>` (string, required): The ID of the agent whose state is to be changed. **CRITICAL: This cannot be '{BOOTSTRAP_AGENT_ID}' (Admin AI).**
*   `<new_state>` (string, required): The target state for the agent.
    *   Valid states for Project Manager (PM) type agents: {', '.join(valid_pm_states)}
    *   Valid states for Worker type agents: {', '.join(valid_worker_states)}
    *   Short names are accepted too: 'work' means 'worker_work' for a worker and 'pm_work' for a PM, 'wait' means 'worker_wait', 'manage' means 'pm_manage'.
*   Example (Activate a worker agent by setting its state to 'work'):
    ```json
    {{
      "action": "set_agent_state",
      "target_agent_id": "{worker_agent_id_placeholder}",
      "new_state": "work"
    }}
    ```
"""
        elif sub_action:
             return f"Error: Sub-action '{sub_action}' is not recognized for the 'manage_team' tool."

        return common_header + """\
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
```json
{
  "action": "get_info",
  "tool_name": "manage_team",
  "sub_action": "ACTION_NAME"
}
```
"""
