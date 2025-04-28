# START OF FILE src/tools/system_help.py
import asyncio
import datetime
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import BASE_DIR # Import BASE_DIR from settings

# Avoid circular import for type hinting
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Define the logs directory relative to BASE_DIR
LOGS_DIRECTORY = BASE_DIR / "logs"

class SystemHelpTool(BaseTool):
    """
    Provides system-level information like current time, searching application logs, or getting detailed tool usage.
    Useful for context, debugging, and dynamic help. Intended primarily for the Admin AI.
    """
    name: str = "system_help"
    auth_level: str = "admin" # <<< NEW: Set auth level
    description: str = (
        "Provides system-level information. Actions: "
        "'get_time' (retrieves current UTC date and time), "
        "'search_logs' (searches the latest application log file for specific text, optionally filtering by agent ID)."
        # Removed get_tool_info
    )
    summary: Optional[str] = "Provides system time or searches logs. (Admin only)" # Add summary
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation to perform: 'get_time' or 'search_logs'.",
            required=True,
        ),
        # Removed tool_name parameter
        ToolParameter(
            name="log_query",
            type="string",
            description="Text to search for in the log file. Required for 'search_logs'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="max_log_lines",
            type="integer",
            description="Maximum number of matching log lines to return. Defaults to 20.",
            required=False,
        ),
        ToolParameter(
            name="agent_id_filter",
            type="string",
            description="Optional: Filter log search to lines containing this specific agent ID.",
            required=False,
        ),
    ]

    # --- Main execution method ---
    async def execute(
        self,
        agent_id: str, # The agent calling the tool (likely admin_ai)
        agent_sandbox_path: Path, # Not used by this tool
        manager: 'AgentManager', # Passed in by executor
        project_name: Optional[str] = None, # Not used by this tool
        session_name: Optional[str] = None, # Not used by this tool
        **kwargs: Any
        ) -> Any:
        """Executes the system help action."""
        action = kwargs.get("action")
        valid_actions = ["get_time", "search_logs"] # Removed get_tool_info

        if not action or action not in valid_actions:
            return f"Error: Invalid or missing 'action'. Must be one of {valid_actions}."

        try:
            if action == "get_time":
                # Use timezone-aware UTC time
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                # ISO 8601 format is standard and includes offset
                formatted_time = now_utc.isoformat(sep=' ', timespec='seconds')
                logger.info(f"SystemHelpTool: Executed 'get_time' by agent {agent_id}. Result: {formatted_time}")
                return f"Current system time (UTC): {formatted_time}"

            elif action == "search_logs":
                log_query = kwargs.get("log_query")
                max_lines_str = kwargs.get("max_log_lines", "20")
                agent_filter = kwargs.get("agent_id_filter")

                if not log_query:
                    return "Error: 'log_query' parameter is required for 'search_logs' action."

                try:
                    max_lines = int(max_lines_str)
                    if max_lines <= 0: max_lines = 20
                except ValueError:
                    logger.warning(f"Invalid 'max_log_lines' value '{max_lines_str}' provided by {agent_id}. Defaulting to 20.")
                    max_lines = 20

                logger.info(f"Agent {agent_id} initiating log search. Query: '{log_query[:50]}...', MaxLines: {max_lines}, AgentFilter: {agent_filter}")
                search_result = await self._search_logs_safe(log_query, max_lines, agent_filter)

                if isinstance(search_result, list):
                    if not search_result:
                        return f"Log search completed. No lines found matching query: '{log_query}'" + (f" and agent filter: '{agent_filter}'." if agent_filter else ".")
                    else:
                        # Limit the output length as well to avoid huge responses
                        MAX_TOTAL_CHARS = 4000
                        output_str = f"Log search results for '{log_query}' (Filter: {agent_filter or 'None'}, Max Lines: {max_lines}):\n---\n"
                        current_len = len(output_str)
                        lines_added = 0
                        for line in search_result:
                            if current_len + len(line) + 1 > MAX_TOTAL_CHARS:
                                output_str += f"\n[... Results truncated due to length limit ({MAX_TOTAL_CHARS} chars) ...]"
                                break
                            output_str += line + "\n"
                            current_len += len(line) + 1
                            lines_added += 1
                        output_str += "---"
                        logger.info(f"Log search for agent {agent_id} returned {lines_added} lines (truncated: {lines_added < len(search_result)}).")
                        return output_str
                else: # It's an error string
                    return search_result

            # Removed get_tool_info action logic

        except Exception as e:
            logger.error(f"Unexpected error executing SystemHelpTool (Action: {action}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing SystemHelpTool ({action}): {type(e).__name__} - {e}"

    # --- Detailed Usage Method ---
    def get_detailed_usage(self) -> str:
        """Returns detailed usage instructions for the SystemHelpTool."""
        usage = """
        **Tool Name:** system_help

        **Description:** Provides system-level information. (Admin Only)

        **Actions:**

        1.  **get_time:**
            *   Retrieves the current system time in UTC ISO 8601 format.
            *   No parameters needed.
            *   Example Call:
                ```xml
                <system_help>
                  <action>get_time</action>
                </system_help>
                ```

        2.  **search_logs:**
            *   Searches the most recent application log file (`logs/app_*.log`) for lines containing specific text.
            *   Parameters:
                *   `<log_query>` (string, required): The text to search for (case-insensitive).
                *   `<max_log_lines>` (integer, optional, default: 20): Maximum number of matching lines to return.
                *   `<agent_id_filter>` (string, optional): Only return lines associated with this specific agent ID.
            *   Example Call (Search for 'error' related to 'agent_123'):
                ```xml
                <system_help>
                  <action>search_logs</action>
                  <log_query>error</log_query>
                  <agent_id_filter>agent_123</agent_id_filter>
                  <max_log_lines>50</max_log_lines>
                </system_help>
                ```
        """
        return usage.strip()

    # --- Log Search Helper ---
    async def _search_logs_safe(self, query: str, max_lines: int, agent_filter: Optional[str]) -> List[str] | str:
        """
        Safely searches the latest log file for lines matching the query and optional agent filter.

        Returns:
            List[str]: A list of matching log lines (up to max_lines).
            str: An error message if the log file cannot be found or accessed.
        """
        if not LOGS_DIRECTORY.is_dir():
            logger.error(f"Log directory not found: {LOGS_DIRECTORY}")
            return f"Error: Log directory not found at configured path."

        try:
            # Find the most recent .log file
            log_files = list(LOGS_DIRECTORY.glob("*.log"))
            if not log_files:
                logger.warning("No log files found in logs directory.")
                return "Error: No application log files found to search."

            latest_log_file = max(log_files, key=lambda p: p.stat().st_mtime)
            logger.debug(f"Searching latest log file: {latest_log_file.name}")

            matching_lines = []
            query_lower = query.lower()
            # Compile regex for agent filter if provided for slightly better performance
            agent_filter_regex = None
            if agent_filter:
                 # Look for variations like 'Agent agent_id:', "'agent_id'", `Agent {agent_id}` etc. Be flexible.
                 agent_filter_regex = re.compile(re.escape(agent_filter))

            def read_and_filter_sync():
                lines_buffer = []
                try:
                    with open(latest_log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        # Read lines efficiently - consider reading in chunks or using deque for large files if performance is critical
                        # For simplicity now, read all lines but process lazily if possible
                        for line in reversed(list(f)): # Read reversed to get recent matches first
                            if len(lines_buffer) >= max_lines:
                                break # Stop reading once we have enough matches

                            line_stripped = line.strip()
                            if not line_stripped: continue # Skip empty lines

                            # Apply agent filter first if present
                            agent_match = True # Assume match if no filter
                            if agent_filter_regex:
                                 if not agent_filter_regex.search(line_stripped):
                                     agent_match = False

                            # Apply query filter if agent filter passed (or wasn't applied)
                            if agent_match and query_lower in line_stripped.lower():
                                lines_buffer.append(line_stripped)
                except Exception as read_err:
                     # Return error indication from within the thread function
                     logger.error(f"Error reading log file {latest_log_file.name}: {read_err}", exc_info=True)
                     return f"Error reading log file: {read_err}"

                return lines_buffer # Return the found lines


            # Run the file reading and filtering in a separate thread
            result = await asyncio.to_thread(read_and_filter_sync)

            # Check if the result is an error string from the thread
            if isinstance(result, str):
                 return result

            # Reverse the buffer to get chronological order (since we read reversed)
            return result[::-1]

        except FileNotFoundError:
            logger.error(f"Latest log file not found during search (unexpected): {latest_log_file.name}")
            return "Error: Could not find the log file during search."
        except PermissionError:
            logger.error(f"Permission denied when trying to access log file: {latest_log_file.name}")
            return "Error: Permission denied when accessing log file."
        except Exception as e:
            logger.error(f"Unexpected error searching log file: {e}", exc_info=True)
            return f"Error searching logs: {type(e).__name__}"
