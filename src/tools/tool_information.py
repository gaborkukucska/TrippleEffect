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

        valid_actions = ["list_tools", "get_info"]
        
        # Check for common mistakes and provide helpful suggestions
        action_suggestions = {
            "list": "list_tools",
            "get": "get_info",
            "help": "get_info",
            "info": "get_info",
            "tools": "list_tools",
            "show_tools": "list_tools",
            "available_tools": "list_tools"
        }
        
        if not action:
            return {"status": "error", "message": f"Missing required 'action' parameter. Must be one of: {', '.join(valid_actions)}."}
        
        if action not in valid_actions:
            if action in action_suggestions:
                suggested_action = action_suggestions[action]
                return {"status": "error", "message": f"Invalid action '{action}'. Did you mean '{suggested_action}'? Valid actions are: {', '.join(valid_actions)}."}
            else:
                return {"status": "error", "message": f"Invalid action '{action}'. Valid actions are: {', '.join(valid_actions)}."}

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

                # Create a detailed message with actual tool names and descriptions
                tools_details = []
                tools_details.append(f"Available tools for agent type '{agent_type}' ({len(tools_list)} total):\n")
                
                for tool_info in tools_list:
                    tools_details.append(f"• **{tool_info['name']}**: {tool_info['summary']}")
                
                tools_details.append(f"\nTo get detailed usage for any tool, use: <tool_information><action>get_info</action><tool_name>TOOL_NAME</tool_name></tool_information>")
                
                return {"status": "success", "message": "\n".join(tools_details)}

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
            
            # Combine all usage information into a single response
            combined_usage = "\n\n" + "="*50 + "\n\n".join([info["usage"] for info in usage_info])
            return {"status": "success", "message": combined_usage}

        # Special case: Admin AI commonly mistakes "list_tools" as a tool name instead of an action
        if tool_name_req == "list_tools":
            return {
                "status": "error", 
                "message": f"❌ Common mistake detected! 'list_tools' is NOT a tool name - it's an ACTION within the tool_information tool.\n\nCorrect usage:\n<tool_information><action>list_tools</action></tool_information>\n\nNOT:\n<tool_information><action>get_info</action><tool_name>list_tools</tool_name></tool_information>"
            }
        
        tool = manager.tool_executor.tools.get(tool_name_req)
        if not tool or not self._is_authorized(agent_type, tool.auth_level):
            # Provide more helpful error message with actual available tools
            available_tools = [name for name, t in manager.tool_executor.tools.items() if self._is_authorized(agent_type, t.auth_level)]
            return {
                "status": "error", 
                "message": f"Tool '{tool_name_req}' not found or not authorized.\n\nAvailable tools: {', '.join(available_tools)}\n\nTip: Use <tool_information><action>list_tools</action></tool_information> to see all available tools with descriptions."
            }

        usage_dict = self._get_single_tool_usage_dict(agent_id, agent_type, tool_name_req, sub_action_req, tool, manager)
        # Return the actual usage information in the message field
        return {"status": "success", "message": usage_dict["usage"]}

    def _get_single_tool_usage_dict(self, agent_id: str, agent_type: str, tool_name: str, sub_action: Optional[str], tool: BaseTool, manager: 'AgentManager') -> Dict[str, Any]:
        agent_context = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "project_name": manager.current_project,
            "team_id": manager.state_manager.get_agent_team(agent_id) if hasattr(manager, 'state_manager') else None
        }
        
        # CRITICAL FIX: Enhanced tool information retrieval with better error handling and logging
        fallback_attempts = []
        final_usage = None
        
        try:
            # Attempt 1: Try the new signature first (with agent_context and sub_action)
            logger.debug(f"ToolInformation: Attempting full signature for {tool_name} with sub_action={sub_action}")
            usage = tool.get_detailed_usage(agent_context=agent_context, sub_action=sub_action)
            if usage and usage.strip():
                final_usage = usage
                logger.debug(f"ToolInformation: SUCCESS - Full signature worked for {tool_name}")
            else:
                fallback_attempts.append("Full signature returned empty/None")
                raise ValueError("Empty usage returned from full signature")
                
        except TypeError as te:
            # If TypeError (wrong signature), try fallback approaches
            fallback_attempts.append(f"Full signature TypeError: {str(te)[:100]}")
            logger.debug(f"ToolInformation: Full signature failed for {tool_name}, trying agent_context only: {te}")
            
            try:
                # Attempt 2: Try with just agent_context
                usage = tool.get_detailed_usage(agent_context=agent_context)
                if usage and usage.strip():
                    final_usage = usage
                    logger.debug(f"ToolInformation: SUCCESS - Agent context signature worked for {tool_name}")
                else:
                    fallback_attempts.append("Agent context signature returned empty/None")
                    raise ValueError("Empty usage returned from agent context signature")
                    
            except TypeError as te2:
                fallback_attempts.append(f"Agent context TypeError: {str(te2)[:100]}")
                logger.debug(f"ToolInformation: Agent context signature failed for {tool_name}, trying no params: {te2}")
                
                try:
                    # Attempt 3: Try with no parameters (old signature)
                    usage = tool.get_detailed_usage()
                    if usage and usage.strip() and usage.strip() not in [
                        "Detailed usage is available via the tool's description.", 
                        "Usage info retrieved.", 
                        "No detailed usage available."
                    ]:
                        final_usage = usage
                        logger.debug(f"ToolInformation: SUCCESS - No params signature worked for {tool_name}")
                    else:
                        fallback_attempts.append(f"No params returned generic/empty: '{usage}'")
                        raise ValueError("Generic or empty usage returned from no params signature")
                        
                except Exception as e3:
                    fallback_attempts.append(f"No params exception: {str(e3)[:100]}")
                    logger.warning(f"ToolInformation: All get_detailed_usage signatures failed for {tool_name}")
                    
            except Exception as e2:
                fallback_attempts.append(f"Agent context exception: {str(e2)[:100]}")
                logger.warning(f"ToolInformation: Agent context and subsequent fallbacks failed for {tool_name}: {e2}")
                
        except Exception as e:
            fallback_attempts.append(f"Full signature exception: {str(e)[:100]}")
            logger.error(f"ToolInformation: Unexpected error during full signature attempt for {tool_name}: {e}", exc_info=True)
        
        # If all attempts failed, use schema generation as final fallback
        if not final_usage:
            logger.info(f"ToolInformation: All method calls failed for {tool_name}, generating from schema. Attempts: {fallback_attempts}")
            final_usage = self._generate_usage_from_schema(tool, tool_name)
            
            # Add diagnostic information to help debug tool implementation issues
            diagnostic_info = f"\n\n**DIAGNOSTIC INFO for {tool_name}:**\n"
            diagnostic_info += f"- Available methods: {[method for method in dir(tool) if not method.startswith('_')]}\n"
            diagnostic_info += f"- Has get_detailed_usage: {hasattr(tool, 'get_detailed_usage')}\n"
            diagnostic_info += f"- Fallback attempts: {len(fallback_attempts)}\n"
            for i, attempt in enumerate(fallback_attempts, 1):
                diagnostic_info += f"- Attempt {i}: {attempt}\n"
            
            final_usage += diagnostic_info
        
        return {"tool_name": tool_name, "usage": final_usage}

    def _generate_usage_from_schema(self, tool: BaseTool, tool_name: str) -> str:
        """Generate detailed usage information from a tool's schema when get_detailed_usage fails."""
        try:
            schema_info = []
            schema_info.append(f"**Tool Name:** {tool_name}")
            schema_info.append(f"**Description:** {tool.description}")
            
            if hasattr(tool, 'summary') and tool.summary:
                schema_info.append(f"**Summary:** {tool.summary}")
            
            schema_info.append("**Parameters:**")
            
            if tool.parameters:
                for param in tool.parameters:
                    param_line = f"  - **{param.name}** ({param.type})"
                    if param.required:
                        param_line += " - **Required**"
                    else:
                        param_line += " - *Optional*"
                    param_line += f": {param.description}"
                    schema_info.append(param_line)
            else:
                schema_info.append("  - No parameters required")
                
            schema_info.append(f"\n**Example Usage:**")
            schema_info.append(f"```xml")
            schema_info.append(f"<{tool_name}>")
            if tool.parameters:
                for param in tool.parameters:
                    if param.required:
                        example_value = "example_value" if param.type == "string" else ("1" if param.type in ["integer", "number"] else "true")
                        schema_info.append(f"  <{param.name}>{example_value}</{param.name}>")
            schema_info.append(f"</{tool_name}>")
            schema_info.append(f"```")
            
            return "\n".join(schema_info)
        except Exception as e:
            logger.error(f"Error generating usage from schema for {tool_name}: {e}", exc_info=True)
            return f"**Tool Name:** {tool_name}\n**Description:** {tool.description}\n**Error:** Unable to generate detailed usage information."

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
