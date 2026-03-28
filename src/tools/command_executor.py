import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess
import os

from src.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

class CommandExecutionTool(BaseTool):
    """
    Executes shell commands in an isolated environment.
    """
    name: str = "command_executor"
    auth_level: str = "worker"
    summary: Optional[str] = "Executes shell commands (npm, pip, python, bash, etc.) in a restricted sandbox."
    description: str = (
        "Allows executing shell commands within the agent's isolated sandbox. "
        "Useful for installing packages, running tests, executing python/node scripts, "
        "and performing system operations. Interactive commands (like nano, vim, or prompts waiting for input) "
        "are NOT supported and will hang. Do not try to run them. Commands have a strict timeout. "
        "The working directory is locked to the allowed scope (e.g., your private sandbox)."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation: 'run_command'.",
            required=True,
        ),
        ToolParameter(
            name="command",
            type="string",
            description="The shell command string to execute (e.g., 'pip install requests', 'pytest tests/', 'python script.py').",
            required=True,
        ),
        ToolParameter(
            name="scope",
            type="string",
            description="Target scope for execution: 'private' (sandbox) or 'shared'. Defaults to 'private'. Determines the working directory.",
            required=False,
        ),
        ToolParameter(
            name="timeout",
            type="integer",
            description="Timeout in seconds for command execution. Default is 60. Max is 300.",
            required=False,
        ),
    ]

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        
        action = kwargs.get("action")
        command = kwargs.get("command")
        scope = kwargs.get("scope", "private").lower()
        timeout = float(kwargs.get("timeout", 60.0))
        
        if not action or action != "run_command":
            return {"status": "error", "message": "Invalid action. Must be 'run_command'."}
            
        if not command:
            return {"status": "error", "message": "The 'command' parameter is required."}
            
        if scope not in ["private", "shared"]:
            return {"status": "error", "message": "Invalid 'scope'. Must be 'private' or 'shared'."}
            
        # Enforce maximum timeout to prevent hanging the system
        if timeout > 300.0:
            timeout = 300.0
            
        from src.config.settings import settings

        # Determine working directory (CWD) based on scope
        cwd_path: Optional[Path] = None
        scope_description = ""
        
        if scope == "private":
            cwd_path = agent_sandbox_path
            scope_description = f"agent {agent_id}'s sandbox"
        elif scope == "shared":
            if not project_name or not session_name:
                return {"status": "error", "message": "Cannot use 'shared' scope - project/session context is missing."}
            cwd_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "shared_workspace"
            scope_description = f"shared workspace"
            
        if cwd_path is None:
            return {"status": "error", "message": "Failed to determine working directory for command execution."}
            
        # Ensure CWD exists
        if not cwd_path.exists():
             try:
                 await asyncio.to_thread(cwd_path.mkdir, parents=True, exist_ok=True)
             except Exception as e:
                 return {"status": "error", "message": f"Could not create missing working directory {cwd_path}: {e}"}

        logger.info(f"Agent {agent_id} executing command '{command}' in {scope_description} (timeout: {timeout}s)")
        
        try:
            # Create subprocess
            # We use shell=True to allow compound commands and argument parsing exactly as typed
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Add basic env isolation if necessary, or pass existing env
                env=os.environ.copy()
            )

            try:
                # Wait for the process to complete with a timeout
                stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
                returncode = process.returncode
            except asyncio.TimeoutError:
                # Terminate the process if it times out
                logger.warning(f"Agent {agent_id} command '{command}' timed out after {timeout}s by terminating process.")
                try:
                    process.terminate()
                    # Give it a tiny bit to terminate, then kill
                    await asyncio.sleep(0.5)
                    if process.returncode is None:
                        process.kill()
                except Exception as cleanup_err:
                    logger.error(f"Error terminating timed-out process: {cleanup_err}")
                return {
                    "status": "error",
                    "message": f"Command execution timed out after {timeout} seconds. Interactive commands (like nano, vim, prompts) or long-running processes are not supported and will hang until timeout."
                }
                
            stdout_str = stdout_bytes.decode('utf-8', errors='replace')
            stderr_str = stderr_bytes.decode('utf-8', errors='replace')
            
            # Truncate extremely long outputs
            MAX_OUTPUT_CHARS = 10000
            truncated_stdout = False
            truncated_stderr = False
            
            if len(stdout_str) > MAX_OUTPUT_CHARS:
                stdout_str = stdout_str[:MAX_OUTPUT_CHARS] + f"\n...[STDOUT TRUNCATED: ({len(stdout_str)} chars exceeded limit)]..."
                truncated_stdout = True
                
            if len(stderr_str) > MAX_OUTPUT_CHARS:
                stderr_str = stderr_str[:MAX_OUTPUT_CHARS] + f"\n...[STDERR TRUNCATED: ({len(stderr_str)} chars exceeded limit)]..."
                truncated_stderr = True

            result_message = f"Command execution completed with return code {returncode}."
            if truncated_stdout or truncated_stderr:
                result_message += " (Output was truncated due to length limits)."
                
            status = "success" if returncode == 0 else "error"
            
            return {
                "status": status,
                "message": result_message,
                "return_code": returncode,
                "stdout": stdout_str,
                "stderr": stderr_str
            }
            
        except Exception as e:
            logger.error(f"Unexpected error executing command for agent {agent_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error starting command execution: {type(e).__name__} - {e}"}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the CommandExecutionTool."""
        usage = """
        **Tool Name:** command_executor

        **Description:** Executes shell commands within the restricted agent sandbox or shared workspace.

        **CRITICAL WARNING - NO INTERACTIVE COMMANDS:** Do NOT attempt to run interactive terminal commands like `nano`, `vim`, `top`, or scripts that prompt for user input (e.g., `input()`). They will cause the execution to freeze and time out. Always use non-interactive flags (like `-y` for apt/npm/pip installations).

        **Actions & Parameters:**

        1.  **run_command:** Executes a parameterized shell command.
            *   `<command>` (string, required): The exact shell command string to execute.
            *   `<scope>` (string, optional): 'private' (default) or 'shared'. Sets the current working directory of the command.
            *   `<timeout>` (integer, optional): Max execution time in seconds (default 60, max 300).

        **Examples:**
        
        *   Run a python script:
            `<command_executor><action>run_command</action><command>python my_script.py</command></command_executor>`
            
        *   Install a package using pip:
            `<command_executor><action>run_command</action><command>pip install requests</command></command_executor>`

        *   Run pytest tests:
            `<command_executor><action>run_command</action><scope>shared</scope><command>pytest tests/ --tb=short</command></command_executor>`
            
        *   List files in a specific format via bash:
            `<command_executor><action>run_command</action><command>ls -la | grep "py"</command></command_executor>`
        """
        return usage.strip()
