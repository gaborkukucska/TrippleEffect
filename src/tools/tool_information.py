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
        agent_sandbox_path: Path,
        manager: 'AgentManager',
        **kwargs: Any
    ) -> Dict[str, Any]:
        action = kwargs.get("action")
        tool_name_req = kwargs.get("tool_name", "all")
        sub_action_req = kwargs.get("sub_action")

        if not action or action not in ["list_tools", "get_info"]:
            return {"status": "error", "message": "Invalid or missing 'action'. Must be 'list_tools' or 'get_info'."}

        if not manager or not hasattr(manager, 'tool_executor'):
             return {"status": "error", "message": "Internal configuration error: cannot access tool executor."}

        calling_agent = manager.agents.get(agent_id)
        agent_type = getattr(calling_agent, 'agent_type', AGENT_TYPE_WORKER)

        try:
            if action == "list_tools":
                tools_list = []
                for name, tool in sorted(manager.tool_executor.tools.items()):
                    if self._is_authorized(agent_type, tool.auth_level):
                        summary = getattr(tool, 'summary', tool.description)
                        tools_list.append({"name": name, "summary": summary.strip()})

                return {"status": "success", "message": f"Found {len(tools_list)} tools for agent type '{agent_type}'.", "tools": tools_list}

            elif action == "get_info":
                return self._get_info(agent_id, agent_type, tool_name_req, sub_action_req, manager)

        except Exception as e:
            logger.error(f"Error in ToolInformationTool action '{action}': {e}", exc_info=True)
            return {"status": "error", "message": f"Unexpected error: {e}"}

    def _is_authorized(self, agent_type: str, tool_auth_level: str) -> bool:
        if agent_type == AGENT_TYPE_ADMIN: return True
        if agent_type == AGENT_TYPE_PM: return tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
        return tool_auth_level == AGENT_TYPE_WORKER

    def _get_info(self, agent_id: str, agent_type: str, tool_name_req: str, sub_action_req: Optional[str], manager: 'AgentManager') -> Dict[str, Any]:
        if tool_name_req.lower() == 'all':
            usage_info = []
            for name, tool in sorted(manager.tool_executor.tools.items()):
                if self._is_authorized(agent_type, tool.auth_level):
                    usage_info.append(self._get_single_tool_usage_dict(agent_id, agent_type, name, sub_action_req, tool, manager))
            return {"status": "success", "message": f"Usage info for {len(usage_info)} authorized tools.", "usage_details": usage_info}

        tool = manager.tool_executor.tools.get(tool_name_req)
        if not tool or not self._is_authorized(agent_type, tool.auth_level):
            return {"status": "error", "message": f"Tool '{tool_name_req}' not found or not authorized."}

        usage_dict = self._get_single_tool_usage_dict(agent_id, agent_type, tool_name_req, sub_action_req, tool, manager)
        return {"status": "success", "message": "Usage info retrieved.", "usage": usage_dict}

    def _get_single_tool_usage_dict(self, agent_id: str, agent_type: str, tool_name: str, sub_action: Optional[str], tool: BaseTool, manager: 'AgentManager') -> Dict[str, Any]:
        agent_context = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "project_name": manager.current_project,
            "team_id": manager.state_manager.get_agent_team(agent_id) if hasattr(manager, 'state_manager') else None
        }
        try:
            usage = tool.get_detailed_usage(agent_context=agent_context, sub_action=sub_action)
            return {"tool_name": tool_name, "usage": usage}
        except Exception as e:
            return {"tool_name": tool_name, "usage": f"Error retrieving usage: {e}"}

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