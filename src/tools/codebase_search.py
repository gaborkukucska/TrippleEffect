# START OF FILE src/tools/codebase_search.py
import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter
from src.tools.sandbox_runner import resolve_shared_workspace
from src.config.settings import settings

logger = logging.getLogger(__name__)

MAX_RESULT_LINES = 50
# Minified JS/CSS can put megabytes on one line; cap each line returned to the model.
MAX_LINE_CHARS = 300
SEARCH_TIMEOUT_SECONDS = 30
# Directories that are almost never what an agent is looking for and can be huge.
DEFAULT_EXCLUDED_DIRS = (".git", "node_modules", "__pycache__", ".venv", "venv", ".te_background")


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
        if not query or not isinstance(query, str):
            return {"status": "error", "message": "Missing required 'query' parameter."}

        default_scope = "shared" if project_name and session_name else "private"
        scope = str(kwargs.get("scope") or default_scope).lower()

        base_path: Optional[Path] = None
        if scope == "private":
            base_path = Path(agent_sandbox_path)
            if not base_path.is_dir(): return {"status": "error", "message": f"Sandbox does not exist: {base_path}"}
        elif scope == "shared":
            if not project_name or not session_name: return {"status": "error", "message": "Missing project/session context."}
            base_path = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, project_name, session_name)
            if not base_path.is_dir(): return {"status": "error", "message": f"Shared workspace does not exist yet: no file has been written for project '{project_name}' in this session, so there is nothing to search."}  # Round 2g
        elif scope == "projects":
            base_path = Path(settings.PROJECTS_BASE_DIR)
            if not base_path.is_dir(): return {"status": "error", "message": "Projects dir does not exist."}
        else:
            return {"status": "error", "message": "Invalid scope. Use 'private', 'shared', or 'projects'."}

        sub_path = str(kwargs.get("path") or "")
        # Resolve before the containment check: Path.is_relative_to() is purely
        # lexical, so 'a/../../..' used to pass it and escape the scope.
        base_resolved = base_path.resolve()
        search_path = (base_path / sub_path).resolve()

        if not search_path.exists() or not (search_path == base_resolved or search_path.is_relative_to(base_resolved)):
            return {"status": "error", "message": f"Invalid or non-existent path: {sub_path} in scope {scope}"}

        include_pattern = kwargs.get("include_pattern")
        exclude_pattern = kwargs.get("exclude_pattern")

        # Only skip a default-excluded directory when the agent did not point the search into it.
        rel_parts = set(search_path.relative_to(base_resolved).parts) if search_path != base_resolved else set()
        cmd: List[str] = ["grep", "-rnIE"]
        for d in DEFAULT_EXCLUDED_DIRS:
            if d not in rel_parts:
                cmd.append(f"--exclude-dir={d}")
        if include_pattern:
            cmd.append(f"--include={include_pattern}")
        if exclude_pattern:
            cmd.append(f"--exclude={exclude_pattern}")
        # '-e' + '--' stop a query or path beginning with '-' being parsed as an option (Review S7).
        cmd += ["-e", query, "--", str(search_path)]

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(base_path)
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=SEARCH_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {"status": "error", "message": f"Search timed out after {SEARCH_TIMEOUT_SECONDS}s. Narrow it with 'path' or 'include_pattern'."}

            output = stdout.decode('utf-8', errors='replace').strip()
            err_output = stderr.decode('utf-8', errors='replace').strip()

            # Show paths relative to the scope root instead of absolute host paths.
            prefix = str(search_path if search_path.is_dir() else search_path.parent) + "/"
            display_prefix = "" if search_path == base_resolved else str(search_path.relative_to(base_resolved)) + "/"
            if search_path.is_dir():
                output = output.replace(prefix, display_prefix)

            # grep exits 2 on errors such as unreadable files, but may still have found matches.
            if process.returncode in (0, 2) and output:
                lines = output.split('\n')
                total = len(lines)
                lines = [ln if len(ln) <= MAX_LINE_CHARS else ln[:MAX_LINE_CHARS] + " …[line truncated]" for ln in lines[:MAX_RESULT_LINES]]
                output = '\n'.join(lines)
                if total > MAX_RESULT_LINES:
                    output += f"\n\n... [Truncated {total - MAX_RESULT_LINES} more lines. Please refine your search query.]"
                message = "Search completed."
                if process.returncode == 2 and err_output:
                    message += f" Some files could not be searched: {err_output[:300]}"
                return {"status": "success", "message": message, "results": output}
            elif process.returncode == 1 or (process.returncode == 0 and not output):
                return {"status": "success", "message": "No matches found.", "results": ""}
            else:
                return {"status": "error", "message": f"Search failed with error: {err_output}"}

        except Exception as e:
            logger.error(f"Codebase search failed: {e}", exc_info=True)
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except Exception:
                    pass
            return {"status": "error", "message": f"Codebase search failed: {e}"}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        usage = """
        **Tool Name:** codebase_search

        **Description:**
        Searches the entire project codebase for a specific string or regular expression. Use this instead of reading files manually to find function definitions, class names, or specific strings.
        `.git`, `node_modules`, `__pycache__` and virtualenv folders are skipped unless you point `path` into them.

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
# END OF FILE src/tools/codebase_search.py
