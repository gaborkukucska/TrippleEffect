import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings

logger = logging.getLogger(__name__)

class CodebaseSearchTool(BaseTool):
    name: str = "codebase_search"
    auth_level: str = "worker"
    summary: Optional[str] = "Search for text/regex across the entire project codebase."
    description: str = "Extremely fast text and regex search across the project files to find function definitions, variables, and code."
    parameters: List[ToolParameter] = [
        ToolParameter(name="query", type="string", description="The exact text or regex pattern to search for.", required=True),
        ToolParameter(name="path", type="string", description="Optional subdirectory to restrict search to (e.g., 'src/agents').", required=False),
        ToolParameter(name="include_pattern", type="string", description="Optional glob pattern for files to include (e.g., '*.py').", required=False),
        ToolParameter(name="exclude_pattern", type="string", description="Optional glob pattern for files to exclude.", required=False),
        ToolParameter(name="scope", type="string", description="Target scope: 'private', 'shared', or 'projects'. Defaults to 'shared'.", required=False),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, project_name: Optional[str] = None, session_name: Optional[str] = None, **kwargs: Any) -> Any:
        query = kwargs.get("query")
        if not query:
            return {"status": "error", "message": "Missing required 'query' parameter."}

        default_scope = "shared" if project_name and session_name else "private"
        scope = kwargs.get("scope", default_scope).lower()

        base_path: Optional[Path] = None
        if scope == "private":
            base_path = agent_sandbox_path
            if not base_path.is_dir(): return {"status": "error", "message": f"Sandbox does not exist: {base_path}"}
        elif scope == "shared":
            if not project_name or not session_name: return {"status": "error", "message": "Missing project/session context."}
            safe_project_name = project_name.replace(" ", "_").strip()
            base_path = settings.PROJECTS_BASE_DIR / safe_project_name / session_name / "shared_workspace"
            if not base_path.is_dir(): return {"status": "error", "message": "Shared workspace does not exist."}
        elif scope == "projects":
            base_path = settings.PROJECTS_BASE_DIR
            if not base_path.is_dir(): return {"status": "error", "message": "Projects dir does not exist."}
        else:
            return {"status": "error", "message": "Invalid scope. Use 'private', 'shared', or 'projects'."}

        sub_path = kwargs.get("path", "")
        search_path = base_path / sub_path
        
        if not search_path.exists() or not search_path.is_relative_to(base_path):
            return {"status": "error", "message": f"Invalid or non-existent path: {sub_path} in scope {scope}"}

        include_pattern = kwargs.get("include_pattern")
        exclude_pattern = kwargs.get("exclude_pattern")

        try:
            # We will use grep as a fallback if ripgrep is not available, but let's just use grep
            cmd = ["grep", "-rnIE", query, str(search_path)]
            
            if include_pattern:
                cmd.insert(1, f"--include={include_pattern}")
            if exclude_pattern:
                cmd.insert(1, f"--exclude={exclude_pattern}")

            # Run search asynchronously
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(base_path)
            )

            stdout, stderr = await process.communicate()
            output = stdout.decode('utf-8', errors='replace').strip()
            err_output = stderr.decode('utf-8', errors='replace').strip()

            if process.returncode == 0:
                lines = output.split('\n')
                if len(lines) > 50:
                    output = '\n'.join(lines[:50]) + f"\n\n... [Truncated {len(lines) - 50} more lines. Please refine your search query.]"
                return {"status": "success", "message": "Search completed.", "results": output}
            elif process.returncode == 1:
                return {"status": "success", "message": "No matches found.", "results": ""}
            else:
                return {"status": "error", "message": f"Search failed with error: {err_output}"}

        except Exception as e:
            logger.error(f"Codebase search failed: {e}", exc_info=True)
            return {"status": "error", "message": f"Codebase search failed: {e}"}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        usage = """
        **Tool Name:** codebase_search

        **Description:**
        Searches the entire project codebase for a specific string or regular expression. Use this instead of reading files manually to find function definitions, class names, or specific strings.

        **Parameters:**
        *   `<query>` (string, required): The text or regex to search for.
        *   `<path>` (string, optional): A subdirectory to limit the search.
        *   `<include_pattern>` (string, optional): Glob pattern (e.g. '*.py') to only search specific files.
        *   `<exclude_pattern>` (string, optional): Glob pattern to ignore specific files.
        *   `<scope>` (string, optional): 'private', 'shared', or 'projects'. Default: 'shared'.

        **Example JSON Call:**
        ```json
        {
          "query": "def __init__",
          "include_pattern": "*.py",
          "scope": "shared"
        }
        ```
        """
        return usage.strip()
