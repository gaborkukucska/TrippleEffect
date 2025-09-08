# START OF FILE src/tools/system_help.py
import asyncio
import datetime
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import BASE_DIR

if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

LOGS_DIRECTORY = BASE_DIR / "logs"

class SystemHelpTool(BaseTool):
    name: str = "system_help"
    auth_level: str = "admin"
    summary: Optional[str] = "Provides system time or searches logs. (Admin only)"
    description: str = "Provides system-level information. Actions: 'get_time', 'search_logs'."
    parameters: List[ToolParameter] = [
        ToolParameter(name="action", type="string", description="The operation: 'get_time' or 'search_logs'.", required=True),
        ToolParameter(name="log_query", type="string", description="Text to search for in logs.", required=False),
        ToolParameter(name="max_log_lines", type="integer", description="Max log lines to return (default 20).", required=False),
        ToolParameter(name="agent_id_filter", type="string", description="Filter logs by agent ID.", required=False),
    ]

    async def execute(self, agent_id: str, manager: 'AgentManager', **kwargs: Any) -> Dict[str, Any]:
        action = kwargs.get("action")
        if not action or action not in ["get_time", "search_logs"]:
            return {"status": "error", "message": "Invalid action. Must be 'get_time' or 'search_logs'."}

        try:
            if action == "get_time":
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                formatted_time = now_utc.isoformat(sep=' ', timespec='seconds')
                return {"status": "success", "time": formatted_time}

            elif action == "search_logs":
                log_query = kwargs.get("log_query")
                if not log_query:
                    return {"status": "error", "message": "'log_query' is required for 'search_logs'."}

                max_lines = int(kwargs.get("max_log_lines", 20))
                agent_filter = kwargs.get("agent_id_filter")

                search_result = await self._search_logs_safe(log_query, max_lines, agent_filter)

                if isinstance(search_result, str): # Error case
                    return {"status": "error", "message": search_result}

                return {"status": "success", "message": f"Found {len(search_result)} log line(s).", "logs": search_result}

        except Exception as e:
            logger.error(f"Error in SystemHelpTool action '{action}': {e}", exc_info=True)
            return {"status": "error", "message": f"Unexpected error: {e}"}

    async def _search_logs_safe(self, query: str, max_lines: int, agent_filter: Optional[str]) -> List[str] | str:
        if not LOGS_DIRECTORY.is_dir():
            return "Error: Log directory not found."

        try:
            log_files = list(LOGS_DIRECTORY.glob("*.log"))
            if not log_files:
                return "Error: No log files found."

            latest_log_file = max(log_files, key=lambda p: p.stat().st_mtime)

            matching_lines = []
            query_lower = query.lower()
            agent_filter_regex = re.compile(re.escape(agent_filter)) if agent_filter else None

            def read_and_filter():
                lines = []
                with open(latest_log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in reversed(list(f)):
                        if len(lines) >= max_lines: break
                        if agent_filter_regex and not agent_filter_regex.search(line): continue
                        if query_lower in line.lower():
                            lines.append(line.strip())
                return lines

            result = await asyncio.to_thread(read_and_filter)
            return result[::-1]
        except Exception as e:
            return f"Error searching logs: {e}"

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """
        Returns detailed usage information for the system_help tool.
        """
        usage = """
        **Tool Name:** system_help

        **Description:**
        Provides system-level information. This tool is essential for understanding the current operational context and for debugging issues by searching logs.

        **Actions & Parameters:**

        *   **action: 'get_time'**
            *   **Description:** Retrieves the current time in UTC.
            *   **Parameters:** None

        *   **action: 'search_logs'**
            *   **Description:** Searches the latest log file for specific information.
            *   **Parameters:**
                *   **log_query** (string, required): The text or pattern to search for in the logs.
                *   **max_log_lines** (integer, optional): The maximum number of matching log lines to return. Defaults to 20.
                *   **agent_id_filter** (string, optional): Filters the log search to only include lines related to a specific agent ID.

        **Example XML Calls:**

        *   To get the current time:
            ```xml
            <system_help>
              <action>get_time</action>
            </system_help>
            ```

        *   To search logs for errors related to 'admin_ai':
            ```xml
            <system_help>
              <action>search_logs</action>
              <log_query>error</log_query>
              <agent_id_filter>admin_ai</agent_id_filter>
            </system_help>
            ```
        """
        return usage.strip()
