# START OF FILE src/tools/test_runner.py
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter
from src.tools.sandbox_runner import run_sandboxed, resolve_shared_workspace
from src.config.settings import settings

logger = logging.getLogger(__name__)

# Characters kept per stream (head + tail) in the result returned to the model.
MAX_STREAM_CHARS = 10000


def _head_tail(text: str, limit: int = MAX_STREAM_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n...[TRUNCATED]...\n" + text[-half:]


class TestRunnerTool(BaseTool):
    """
    Runs a project's test command. Execution goes through the shared sandbox
    runner (kill-switch, scrubbed environment, process-group timeout) and the
    command is checked by the policy gate in ToolExecutor beforehand.
    """
    name: str = "test_runner"
    auth_level: str = "worker"
    summary: Optional[str] = "Execute test suites (e.g., pytest, npm test) and capture output."
    description: str = "Executes test commands in the project workspace with a timeout to verify code correctness."
    parameters: List[ToolParameter] = [
        ToolParameter(name="command", type="string", description="The test command to run (e.g., 'pytest', 'npm run test:unit').", required=True),
        ToolParameter(name="working_dir", type="string", description="Optional subdirectory to run the tests in.", required=False),
        ToolParameter(name="timeout", type="integer", description="Timeout in seconds (default 30, max 120).", required=False),
        ToolParameter(name="scope", type="string", description="Target scope: 'private', 'shared', or 'projects'. Defaults to 'shared'.", required=False),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, project_name: Optional[str] = None, session_name: Optional[str] = None, **kwargs: Any) -> Any:
        command = kwargs.get("command")
        if not command or not isinstance(command, str) or not command.strip():
            return {"status": "error", "message": "Missing required 'command' parameter."}

        default_scope = "shared" if project_name and session_name else "private"
        scope = str(kwargs.get("scope") or default_scope).lower()

        base_path: Optional[Path] = None
        if scope == "private":
            base_path = Path(agent_sandbox_path)
            if not base_path.is_dir(): return {"status": "error", "message": f"Sandbox does not exist: {base_path}"}
        elif scope == "shared":
            if not project_name or not session_name: return {"status": "error", "message": "Missing project/session context."}
            base_path = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, project_name, session_name)
            if not base_path.is_dir(): return {"status": "error", "message": f"Shared workspace does not exist yet: no file has been written for project '{project_name}' in this session, so there are no tests to run."}  # Round 2g
        elif scope == "projects":
            base_path = Path(settings.PROJECTS_BASE_DIR)
            if not base_path.is_dir(): return {"status": "error", "message": "Projects dir does not exist."}
        else:
            return {"status": "error", "message": "Invalid scope. Use 'private', 'shared', or 'projects'."}

        sub_path = str(kwargs.get("working_dir") or "")
        # Resolve before the containment check: Path.is_relative_to() is purely
        # lexical, so 'a/../../..' used to pass it and escape the scope.
        base_resolved = base_path.resolve()
        run_path = (base_path / sub_path).resolve()

        if not run_path.is_dir() or not (run_path == base_resolved or run_path.is_relative_to(base_resolved)):
            return {"status": "error", "message": f"Invalid or non-existent working directory: {sub_path} in scope {scope}"}

        try:
            timeout = int(kwargs.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(5, min(120, timeout))  # clamp between 5 and 120 seconds

        logger.info(f"Agent {agent_id} running test command '{command}' in {run_path} with {timeout}s timeout.")

        try:
            res = await run_sandboxed(command, run_path, timeout, tool_name=self.name, agent_id=agent_id)
        except Exception as e:
            logger.error(f"TestRunner failed: {e}", exc_info=True)
            return {"status": "error", "message": f"TestRunner failed: {e}"}

        if res.status == "blocked":
            return {"status": "error", "message": res.message}
        if res.status == "error":
            return {"status": "error", "message": f"TestRunner failed: {res.message}"}
        if res.status == "timeout":
            result = {
                "status": "error",
                "message": f"Test command timed out after {timeout} seconds.",
                "suggestion": "The tests took too long to run. Check for infinite loops or increase the timeout parameter.",
            }
            if res.stdout.strip():
                result["partial_stdout"] = res.stdout.strip()[-1500:]
            if res.stderr.strip():
                result["partial_stderr"] = res.stderr.strip()[-1500:]
            return result

        stdout = res.stdout.strip()
        stderr = res.stderr.strip()
        result: Dict[str, Any] = {
            "command": command,
            "exit_code": res.returncode,
            "execution_time_seconds": res.duration_seconds,
        }
        if stdout:
            result["stdout"] = _head_tail(stdout)
        if stderr:
            result["stderr"] = _head_tail(stderr)

        if res.returncode == 0:
            result["status"] = "success"
            result["message"] = "Test command completed successfully."
        else:
            result["status"] = "error"
            result["message"] = f"Test command failed with exit code {res.returncode}."

        return result

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        usage = """
        **Tool Name:** test_runner

        **Description:**
        Executes a test command (like `pytest` or `npm test`) inside your project directory to verify that your code works before reporting tasks as finished.

        **Parameters:**
        *   `<command>` (string, required): The shell command to run.
        *   `<working_dir>` (string, optional): A subdirectory to run the command in.
        *   `<timeout>` (integer, optional): Maximum execution time in seconds (default 30, at most 120).
        *   `<scope>` (string, optional): 'private', 'shared', or 'projects'. Default: 'shared'.

        **Example JSON Call:**
```json
        {
          "command": "pytest tests/test_api.py",
          "scope": "shared"
        }
```
        """
        return usage.strip()
# END OF FILE src/tools/test_runner.py
