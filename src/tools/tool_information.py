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
            required=False, # Only required for get_info if not 'all'
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
                                    usage = tool_instance.get_detailed_usage()
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
                    MAX_ALL_USAGE_CHARS = 8000
                    final_output = "\n".join(all_usage_info)
                    if len(final_output) > MAX_ALL_USAGE_CHARS:
                        final_output = final_output[:MAX_ALL_USAGE_CHARS] + "\n\n[... Tool usage details truncated due to length limit ...]"
                    return final_output

                # Removed the problematic block that was here.
                # The `else` block below correctly handles specific tool requests.

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

                    # NEW LOGIC FOR PM and manage_team
                    if tool_name_req == "manage_team" and agent_type == AGENT_TYPE_PM:
                        try:
                            usage_info = target_tool.get_detailed_usage()
                            # Attempt to extract create_agent section
                            # This is a simplified extraction, actual markers might differ.
                            # Assuming usage string has sections like "1. **create_agent:** ... details ... \n2. **delete_agent:**"
                            # or XML examples like "<action>create_agent</action>..."

                            import re
                            extracted_section = None

                            # Try to find XML block for create_agent
                            xml_match = re.search(r"(<manage_team>\s*<action>create_agent</action>[\s\S]*?</manage_team>)", usage_info, re.MULTILINE)
                            if xml_match:
                                extracted_section = xml_match.group(1)
                                # Also try to get descriptive text before or around it if possible
                                # This part is highly dependent on the actual format of get_detailed_usage() for manage_team
                                # For now, we'll primarily focus on the XML block if found.
                                # A more robust solution would need to know the exact format of manage_team's detailed usage.

                                # Let's try to find a preceding descriptive paragraph for create_agent
                                # This is a guess, assuming a structure like:
                                # ... some text ...
                                # **create_agent:**
                                # Description of create_agent action.
                                # Parameters:
                                #   - agent_id: ...
                                #   - role: ...
                                # Example:
                                # <manage_team>
                                #   <action>create_agent</action>
                                #   ...
                                # </manage_team>
                                # ... next action ...

                                desc_pattern = r"(\*\*create_agent:\*\*(?:.|\n)*?)<manage_team>\s*<action>create_agent</action>"
                                desc_match = re.search(desc_pattern, usage_info, re.IGNORECASE | re.MULTILINE)
                                if desc_match:
                                     extracted_section = desc_match.group(1).strip() + "\n" + extracted_section

                            if extracted_section:
                                concise_message = (
                                    f"--- Key Usage for Tool: manage_team (Action: create_agent) ---\n"
                                    f"Focus on the 'create_agent' action. Below is the relevant excerpt:\n\n"
                                    f"{extracted_section.strip()}\n\n"
                                    f"--- End Key Usage ---"
                                )
                                logger.info(f"{self.name}: Provided concise 'create_agent' info for 'manage_team' to PM agent {agent_id}.")
                                return concise_message
                            else:
                                # Fallback if specific create_agent section not found easily
                                fallback_message = (
                                    f"--- Key Usage for Tool: manage_team (Auth Level: {tool_auth_level}) ---\n"
                                    "Action: create_agent.\n"
                                    "This action is used to create new worker agents and assign them to your team.\n"
                                    "Essential parameters typically include:\n"
                                    "  - `agent_id`: A unique identifier for the new agent (e.g., worker_projectname_1).\n"
                                    "  - `role`: A concise descriptor of the agent's function (e.g., 'FrontendDeveloper', 'Researcher').\n"
                                    "  - `persona`: A brief description of the agent's expertise and personality.\n"
                                    "  - `system_prompt`: Specific instructions for the worker, its goal, and expected output.\n"
                                    "  - `team_id`: Your current team's ID (e.g., team_projectname).\n"
                                    "Ensure your output is a single, complete XML block for the <manage_team> tool call.\n"
                                    "Full tool details are extensive; this is a focused summary for agent creation.\n"
                                    f"--- End Key Usage ---"
                                )
                                logger.info(f"{self.name}: Provided fallback concise 'create_agent' info for 'manage_team' to PM agent {agent_id}.")
                                return fallback_message
                        except Exception as e:
                            logger.error(f"Error during concise 'manage_team' info extraction for PM {agent_id}: {e}", exc_info=True)
                            # Fall through to default behavior if extraction fails badly

                    # Get and return usage info if authorized (default behavior)
                    try:
                        usage_info = target_tool.get_detailed_usage()
                        logger.info(f"{self.name}: Executed 'get_info' for tool '{tool_name_req}' by agent {agent_id}.")
                        return f"--- Detailed Usage for Tool: {tool_name_req} (Auth Level: {tool_auth_level}) ---\n{usage_info}\n--- End Usage ---"
                    except Exception as tool_usage_err:
                        logger.error(f"Error getting detailed usage for tool '{tool_name_req}': {tool_usage_err}", exc_info=True)
                        return f"Error retrieving usage for tool '{tool_name_req}': {type(tool_usage_err).__name__}"

        except Exception as e:
            logger.error(f"Unexpected error executing {self.name} (Action: {action}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing {self.name} ({action}): {type(e).__name__} - {e}"

    def get_detailed_usage(self) -> str:
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