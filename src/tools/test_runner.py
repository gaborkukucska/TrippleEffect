import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

from src.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

class TestRunnerTool(BaseTool):
    name: str = "test_runner"
    auth_level: str = "worker"
    summary: Optional[str] = "Execute test suites (e.g., pytest, npm test) and capture output."
    description: str = "Safely executes test commands in the project sandbox with a timeout to verify code correctness."
    parameters: List[ToolParameter] = [
        ToolParameter(name="command", type="string", description="The test command to run (e.g., 'pytest', 'npm run test:unit').", required=True),
        ToolParameter(name="working_dir", type="string", description="Optional subdirectory to run the tests in.", required=False),
        ToolParameter(name="timeout", type="integer", description="Timeout in seconds (default 30, max 120).", required=False),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Dict[str, Any]:
        command = kwargs.get("command")
        if not command:
            return {"status": "error", "message": "Missing required 'command' parameter."}

        sub_path = kwargs.get("working_dir", "")
        run_path = agent_sandbox_path / sub_path
        
        if not run_path.exists() or not run_path.is_relative_to(agent_sandbox_path):
            return {"status": "error", "message": f"Invalid or non-existent working directory: {sub_path}"}

        timeout = int(kwargs.get("timeout", 30))
        timeout = max(5, min(120, timeout)) # clamp between 5 and 120 seconds

        logger.info(f"Agent {agent_id} running test command '{command}' in {run_path} with {timeout}s timeout.")

        try:
            start_time = time.time()
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(run_path)
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    process.kill()
                return {
                    "status": "error", 
                    "message": f"Test command timed out after {timeout} seconds.",
                    "suggestion": "The tests took too long to run. Check for infinite loops or increase the timeout parameter."
                }

            execution_time = time.time() - start_time
            stdout = stdout_bytes.decode('utf-8', errors='replace').strip()
            stderr = stderr_bytes.decode('utf-8', errors='replace').strip()

            result = {
                "command": command,
                "exit_code": process.returncode,
                "execution_time_seconds": round(execution_time, 2)
            }

            if stdout:
                # Truncate if insanely long
                if len(stdout) > 10000:
                    stdout = stdout[:5000] + "\n...[TRUNCATED]...\n" + stdout[-5000:]
                result["stdout"] = stdout
            
            if stderr:
                if len(stderr) > 10000:
                    stderr = stderr[:5000] + "\n...[TRUNCATED]...\n" + stderr[-5000:]
                result["stderr"] = stderr

            if process.returncode == 0:
                result["status"] = "success"
                result["message"] = "Test command completed successfully."
            else:
                result["status"] = "error"
                result["message"] = f"Test command failed with exit code {process.returncode}."

            return result

        except Exception as e:
            logger.error(f"TestRunner failed: {e}", exc_info=True)
            return {"status": "error", "message": f"TestRunner failed: {e}"}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        usage = """
        **Tool Name:** test_runner

        **Description:**
        Executes a test command (like `pytest` or `npm test`) inside your project directory to verify that your code works before reporting tasks as finished.

        **Parameters:**
        *   `<command>` (string, required): The shell command to run.
        *   `<working_dir>` (string, optional): A subdirectory to run the command in.
        *   `<timeout>` (integer, optional): Maximum execution time in seconds.

        **Example XML Call:**
        ```xml
        <test_runner>
          <command>pytest tests/test_api.py</command>
        </test_runner>
        ```
        """
        return usage.strip()
