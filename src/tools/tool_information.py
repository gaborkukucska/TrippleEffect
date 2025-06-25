# START OF FILE src/tools/tool_information.py
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from pathlib import Path

from src.tools.base import BaseTool, ToolParameter
# --- NEW: Import agent type constants ---
from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER
# --- END NEW ---


# Avoid circular import for type hinting
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

class ToolInformationTool(BaseTool):
    """
    Provides detailed usage information about available tools, respecting authorization levels.
    Accessible by all agent types.
    """
    name: str = "tool_information"
    auth_level: str = "worker" # Accessible by all agent types
    description: str = (
        "Retrieves information about tools accessible to the calling agent. "
        "Actions: 'list_tools' (provides names and summaries), 'get_info' (provides detailed usage)."
    )
    summary: str = "Lists accessible tools or gets detailed usage for a specific tool." # Add summary
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation to perform: 'list_tools' or 'get_info'.",
            required=True,
        ),
        ToolParameter(
            name="tool_name",
            type="string",
            description="For 'get_info': The name of the specific tool, or 'all' for detailed usage of all accessible tools. Not used by 'list_tools'.",
            required=False,
        ),
        ToolParameter( # New parameter
            name="sub_action",
            type="string",
            description="For 'get_info' on certain complex tools (like 'manage_team'): Specifies a sub-action to get detailed help for (e.g., 'create_agent').",
            required=False,
        ),
    ]

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path, # Not used by this tool
        manager: 'AgentManager', # Passed in by executor
        project_name: Optional[str] = None, # Not used by this tool
        session_name: Optional[str] = None, # Not used by this tool
        **kwargs: Any
        ) -> str:
        """Executes the tool information retrieval."""
        action = kwargs.get("action")
        tool_name_req = kwargs.get("tool_name", "all") # Default to 'all' for get_info
        sub_action_req = kwargs.get("sub_action") # Get the new sub_action

        valid_actions = ["list_tools", "get_info"]
        if not action or action not in valid_actions:
            return f"Error: Invalid or missing 'action'. Must be one of {valid_actions}."

        # Access the tool executor via the manager
        if not manager or not hasattr(manager, 'tool_executor'):
             logger.error(f"{self.name}: AgentManager instance not available or missing tool_executor.")
             return "Error: Internal configuration error - cannot access tool executor."

        # Get calling agent's type
        calling_agent = manager.agents.get(agent_id)
        agent_type = getattr(calling_agent, 'agent_type', AGENT_TYPE_WORKER) if calling_agent else AGENT_TYPE_WORKER # Default to worker

        try:
            # --- NEW: Handle list_tools action ---
            if action == "list_tools":
                authorized_tools_summary = []
                all_tool_names = sorted(list(manager.tool_executor.tools.keys()))

                for name in all_tool_names:
                    tool_instance = manager.tool_executor.tools.get(name)
                    if not tool_instance: continue

                    tool_auth_level = getattr(tool_instance, 'auth_level', 'worker')

                    # Check authorization
                    is_authorized = False
                    if agent_type == AGENT_TYPE_ADMIN: is_authorized = True
                    elif agent_type == AGENT_TYPE_PM: is_authorized = tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
                    elif agent_type == AGENT_TYPE_WORKER: is_authorized = tool_auth_level == AGENT_TYPE_WORKER

                    if is_authorized:
                        summary = getattr(tool_instance, 'summary', None) or tool_instance.description # Fallback to description
                        authorized_tools_summary.append(f"- {name}: {summary.strip()}")

                logger.info(f"{self.name}: Executed 'list_tools' for agent {agent_id} (Type: {agent_type}).")
                if not authorized_tools_summary:
                    return f"No tools are accessible for your agent type ({agent_type})."
                else:
                    return f"Tools available to you (Agent Type: {agent_type}):\n" + "\n".join(authorized_tools_summary)
            # --- END list_tools action ---

            elif action == "get_info":
                # Handle 'all' tools request (filtered by auth)
                if tool_name_req.lower() == 'all':
                    all_usage_info = []
                    authorized_tools = []
                    all_tool_names = sorted(list(manager.tool_executor.tools.keys()))

                    for name in all_tool_names:
                        tool_instance = manager.tool_executor.tools.get(name)
                        if not tool_instance:
                            continue

                        tool_auth_level = getattr(tool_instance, 'auth_level', 'worker')

                        # Check authorization based on agent type
                        is_authorized = False
                        if agent_type == AGENT_TYPE_ADMIN:
                            is_authorized = True
                        elif agent_type == AGENT_TYPE_PM:
                            is_authorized = tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
                        elif agent_type == AGENT_TYPE_WORKER:
                            is_authorized = tool_auth_level == AGENT_TYPE_WORKER

                        if is_authorized:
                            authorized_tools.append(name)
                            if hasattr(tool_instance, 'get_detailed_usage'):
                                try:
                                    # --- Pass agent_context to get_detailed_usage ---
                                    agent_context_for_tool = {
                                        "agent_id": agent_id,
                                        "agent_type": agent_type,
                                        "project_name": getattr(calling_agent, 'project_name_context', manager.current_project if manager else None),
                                        "team_id": manager.state_manager.get_agent_team(agent_id) if manager and hasattr(manager, 'state_manager') else None,
                                        "session_name": getattr(calling_agent, 'session_name', manager.current_session if manager else None)
                                    }
                                    usage = tool_instance.get_detailed_usage(agent_context=agent_context_for_tool)
                                    all_usage_info.append(f"--- Usage for Tool: {name} (Auth Level: {tool_auth_level}) ---\n{usage}\n--- End Usage ---\n")
                                except Exception as tool_usage_err:
                                    logger.error(f"Error getting detailed usage for tool '{name}': {tool_usage_err}", exc_info=True)
                                    all_usage_info.append(f"--- Error getting usage for Tool: {name}: {type(tool_usage_err).__name__} ---\n")
                            else:
                                all_usage_info.append(f"--- Usage information unavailable for Tool: {name} ---\n")

                    # Prepend the list of authorized tools
                    all_usage_info.insert(0, f"Tools available to you (Agent Type: {agent_type}): {authorized_tools}\n")

                    logger.info(f"{self.name}: Executed 'get_info' for 'all' (filtered) tools by agent {agent_id} (Type: {agent_type}).")
                    # Join and limit total length
                    MAX_ALL_USAGE_CHARS = 8000 # Define this constant if not already defined globally for the class/module
                    final_output = "\n".join(all_usage_info)
                    if len(final_output) > MAX_ALL_USAGE_CHARS:
                        final_output = final_output[:MAX_ALL_USAGE_CHARS] + "\n\n[... Tool usage details truncated due to length limit ...]"
                    return final_output

                # Handle specific tool request
                else: # This `else` is for `if tool_name_req.lower() == 'all':`
                    target_tool = manager.tool_executor.tools.get(tool_name_req)
                    if not target_tool:
                        # List only tools *authorized* for the calling agent
                        authorized_tools_list = []
                        all_tool_names = sorted(list(manager.tool_executor.tools.keys()))
                        for name in all_tool_names:
                            tool_instance = manager.tool_executor.tools.get(name)
                            if not tool_instance: continue
                            tool_auth_level = getattr(tool_instance, 'auth_level', 'worker')
                            is_authorized = False
                            if agent_type == AGENT_TYPE_ADMIN: is_authorized = True
                            elif agent_type == AGENT_TYPE_PM: is_authorized = tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
                            elif agent_type == AGENT_TYPE_WORKER: is_authorized = tool_auth_level == AGENT_TYPE_WORKER
                            if is_authorized: authorized_tools_list.append(name)

                        return f"Error: Tool '{tool_name_req}' not found or not authorized for your agent type ({agent_type}). Available authorized tools: {authorized_tools_list}"

                    # Check authorization for the specific tool
                    tool_auth_level = getattr(target_tool, 'auth_level', 'worker')
                    is_authorized = False
                    if agent_type == AGENT_TYPE_ADMIN: is_authorized = True
                    elif agent_type == AGENT_TYPE_PM: is_authorized = tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
                    elif agent_type == AGENT_TYPE_WORKER: is_authorized = tool_auth_level == AGENT_TYPE_WORKER

                    if not is_authorized:
                         return f"Error: Agent type '{agent_type}' is not authorized to access tool '{tool_name_req}' (requires level '{tool_auth_level}')."

                    # --- Prepare agent_context for get_detailed_usage ---
                    agent_context_for_tool = {
                        "agent_id": agent_id,
                        "agent_type": agent_type,
                        "project_name": getattr(calling_agent, 'project_name_context', manager.current_project if manager else None),
                        "team_id": manager.state_manager.get_agent_team(agent_id) if manager and hasattr(manager, 'state_manager') else None,
                        "session_name": getattr(calling_agent, 'session_name', manager.current_session if manager else None)
                    }

                    # Special handling for PM and manage_team can be simplified or moved to manage_team's get_detailed_usage
                    if tool_name_req == "manage_team" and agent_type == AGENT_TYPE_PM:
                        try:
                            # The get_detailed_usage method of ManageTeamTool itself should handle context if needed
                            usage_info = target_tool.get_detailed_usage(agent_context=agent_context_for_tool)
                            # If manage_team's get_detailed_usage already provides a concise version for PMs, this is enough.
                            # Otherwise, the existing special string formatting for PMs can be kept here or, ideally,
                            # moved into manage_team.get_detailed_usage.
                            # For now, let's assume manage_team.get_detailed_usage handles the context.
                            logger.info(f"{self.name}: Provided usage for 'manage_team' to PM agent {agent_id} using context.")
                            return f"--- Detailed Usage for Tool: {tool_name_req} (Auth Level: {tool_auth_level}) ---\n{usage_info}\n--- End Usage ---"
                        except Exception as e:
                            logger.error(f"Error during 'manage_team' info provision for PM {agent_id}: {e}", exc_info=True)
                            # Fall through to default behavior if specific handling fails

                    # Get and return usage info if authorized (default behavior)
                    try:
                        # Pass sub_action if provided, otherwise it defaults to None in target_tool.get_detailed_usage
                        usage_info = target_tool.get_detailed_usage(agent_context=agent_context_for_tool, sub_action=sub_action_req)
                        logger.info(f"{self.name}: Executed 'get_info' for tool '{tool_name_req}' (Sub-action: {sub_action_req or 'N/A'}) by agent {agent_id}.")
                        return f"--- Detailed Usage for Tool: {tool_name_req} {('(Sub-action: ' + sub_action_req + ')') if sub_action_req else ''} (Auth Level: {tool_auth_level}) ---\n{usage_info}\n--- End Usage ---"
                    except Exception as tool_usage_err:
                        logger.error(f"Error getting detailed usage for tool '{tool_name_req}' (Sub-action: {sub_action_req}): {tool_usage_err}", exc_info=True)
                        return f"Error retrieving usage for tool '{tool_name_req}' (Sub-action: {sub_action_req}): {type(tool_usage_err).__name__}"

        except Exception as e:
            logger.error(f"Unexpected error executing {self.name} (Action: {action}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing {self.name} ({action}): {type(e).__name__} - {e}"

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None) -> str: # Added agent_context to match BaseTool
        """Returns detailed usage instructions for the ToolInformationTool."""
        usage = """
        **Tool Name:** tool_information

        **Description:** Retrieves detailed usage instructions for tools accessible to the calling agent.

        **Actions & Parameters:**

        *   **action:** (string, required) - The operation: 'list_tools' or 'get_info'.
        *   **tool_name:** (string, optional) - Required only for `action='get_info'` if you want details for a *specific* tool. If omitted or set to 'all' for `get_info`, details for *all accessible* tools are returned. Not used by `list_tools`.

        **Example Calls:**

        *   List names and summaries of accessible tools:
            ```xml
            <tool_information>
              <action>list_tools</action>
            </tool_information>
            ```

        *   Get detailed usage for all accessible tools:
            ```xml
            <tool_information>
              <action>get_info</action>
              <tool_name>all</tool_name>
            </tool_information>
            ```
            *(Note: If tool_name is omitted, it defaults to 'all')*

        *   Get info for the 'file_system' tool:
            ```xml
            <tool_information>
              <action>get_info</action>
              <tool_name>file_system</tool_name>
            </tool_information>
            ```
        """
        return usage.strip()

# END OF FILE src/tools/tool_information.py