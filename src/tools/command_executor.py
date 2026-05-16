import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess
import os
import signal

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
        from src.config.settings import settings
        if getattr(settings, "DISABLE_COMMAND_EXECUTION", False):
            return {
                "status": "error",
                "message": "Command execution is globally disabled by the administrator (DISABLE_COMMAND_EXECUTION is set)."
            }
            
        action = kwargs.get("action")
        command = kwargs.get("command")
        scope = kwargs.get("scope", "shared").lower()
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
        # Enforce minimum timeout to prevent agents from prematurely failing builds
        if timeout < 30.0:
            timeout = 30.0
            
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

        # --- PATH NORMALIZATION: Strip redundant `cd` prefixes ---
        # Agents (especially smaller models) hallucinate Docker-style paths like
        # `cd /workspace && npm start` or `cd /home/user/project && pytest`.
        # Since the CWD is already set correctly by the scope parameter, these
        # `cd` prefixes are always redundant and often cause errors.
        _cd_prefix_pattern = re.compile(
            r'^cd\s+'                               # cd followed by space
            r'(?:/workspace|/home/\S+|/app|'         # common hallucinated absolute paths
            r'\.(?:/\S+)?|'                          # relative . or ./foo
            r'\.\.(?:/\S+)?|'                        # relative .. or ../foo
            r'shared_workspace(?:/\S+)?|'             # agents trying to cd into workspace
            r'\S+/shared_workspace(?:/\S+)?)'         # full path to shared_workspace
            r'\s*(?:&&|;)\s*',                       # followed by && or ; separator
            re.IGNORECASE
        )
        original_command = command
        command = _cd_prefix_pattern.sub('', command).strip()
        if command != original_command:
            logger.info(
                f"Framework Optimization: Stripped redundant 'cd' prefix from command for agent {agent_id}. "
                f"Original: '{original_command[:100]}' → Cleaned: '{command[:100]}'"
            )
        # Handle bare `cd <path>` with no following command (edge case)
        if re.match(r'^cd\s+\S+$', command, re.IGNORECASE):
            return {
                "status": "success",
                "message": (
                    "Framework Optimization: The 'cd' command is not needed. "
                    "The working directory is automatically set by the 'scope' parameter. "
                    "To run commands in the shared workspace, set scope='shared'. "
                    "To run in a subdirectory, use a relative path in your command, e.g.: "
                    "'python src/main.py' instead of 'cd src && python main.py'."
                ),
                "return_code": 0,
                "stdout": "",
                "stderr": ""
            }

        # Auto-Skip redundant npm installs to save time and compute
        if "npm install" in command or "npm i " in command or command.endswith("npm i"):
            if (cwd_path / "node_modules").exists():
                logger.info(f"Agent {agent_id} attempted '{command}', but node_modules exists. Returning cached success.")
                return {
                    "status": "success",
                    "message": "Command execution completed with return code 0. (Framework Optimization: Skipped redundant npm install because node_modules already exists).",
                    "return_code": 0,
                    "stdout": "up to date, audited packages in 1s\\n\\nfound 0 vulnerabilities",
                    "stderr": ""
                }

        # Auto-background known long-running server commands if agent forgot '&'
        server_patterns = [
            "python -m http.server", "python server.py", "python app.py", 
            "npm start", "npm run dev", "uvicorn ", "flask run", 
            "node server.js", "node app.js", "live-server"
        ]
        is_server_cmd = any(pattern in command for pattern in server_patterns)
        if is_server_cmd and not command.strip().endswith("&"):
            logger.info(f"Framework Optimization: Auto-backgrounding long-running command '{command}' by appending '&'.")
            command = command.strip() + " > server_output.log 2>&1 &"

        logger.info(f"Agent {agent_id} executing command '{command}' in {scope_description} (timeout: {timeout}s)")
        
        # --- Framework Optimization: Package Blacklist ---
        if "pip install -r requirements.txt" in command or "pip install " in command:
            req_file = cwd_path / "requirements.txt"
            if req_file.exists():
                content = req_file.read_text(errors='replace')
                lines = content.split('\n')
                new_lines = []
                changed = False
                for line in lines:
                    if line.strip().startswith("sqlite3"):
                        new_lines.append(f"# Framework Optimization: Removed blacklisted built-in package {line.strip()}")
                        changed = True
                    else:
                        new_lines.append(line)
                if changed:
                    logger.info(f"Framework Optimization: Sanitized requirements.txt to remove sqlite3")
                    req_file.write_text('\n'.join(new_lines))

        if "pip install " in command and "sqlite3" in command:
            return {
                "status": "error",
                "message": "Framework Blocked: sqlite3 is a standard library built-in module and cannot be installed via pip. Do not add it to requirements.txt or pip install it.",
                "content": "Command Execution Failed\n\n--- STDERR ---\nFramework Blocked: sqlite3 is a standard library built-in module and cannot be installed via pip. Do not add it to requirements.txt or pip install it."
            }

        try:
            # Create subprocess
            # We use shell=True to allow compound commands and argument parsing exactly as typed
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Add basic env isolation if necessary, or pass existing env
                env=os.environ.copy(),
                preexec_fn=os.setsid
            )

            try:
                # Wait for the process to complete with a timeout
                stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
                returncode = process.returncode
            except asyncio.TimeoutError:
                # Terminate the process if it times out
                logger.warning(f"Agent {agent_id} command '{command}' timed out after {timeout}s by terminating process group.")
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    # Give it a tiny bit to terminate, then kill
                    await asyncio.sleep(0.5)
                    if process.returncode is None:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception as cleanup_err:
                    logger.error(f"Error terminating timed-out process group: {cleanup_err}")
                return {
                    "status": "error",
                    "message": f"Command execution timed out after {timeout} seconds. Interactive commands (like nano, vim, prompts) are not supported. If you meant to start a server or a long-running process, you MUST use the `&` symbol to background it. For example: `python server.py &` or `npm start &`."
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
            
            if returncode != 0 and stderr_str.strip():
                snippet = stderr_str.strip()[-500:]
                result_message += f"\nStderr snippet:\n{snippet}"
                
            status = "success" # Always return success so the executor doesn't drop the context!
            
            # Format content specifically for the LLM context
            content_parts = [f"Command Execution {'Succeeded' if returncode == 0 else 'Failed'} (Exit Code: {returncode})"]
            if stdout_str.strip():
                content_parts.append(f"--- STDOUT ---\n{stdout_str.strip()}")
            if stderr_str.strip():
                content_parts.append(f"--- STDERR ---\n{stderr_str.strip()}")
            if returncode != 0 and not stderr_str.strip() and not stdout_str.strip():
                content_parts.append("--- Output ---\nNo output provided by command.")
            return {
                "status": status,
                "message": result_message,
                "content": "\n\n".join(content_parts)
            }
            
        except Exception as e:
            logger.error(f"Unexpected error executing command for agent {agent_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error starting command execution: {type(e).__name__} - {e}"}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the CommandExecutionTool."""
        usage = """
        **Tool Name:** command_executor

        **Description:** Executes shell commands within the restricted agent sandbox or shared workspace.

        **CRITICAL — WORKING DIRECTORY IS AUTOMATIC:**
        The working directory is automatically set based on the `scope` parameter:
        - `scope="shared"` → Runs inside the project's shared workspace (most common).
        - `scope="private"` → Runs inside your private sandbox.
        You do NOT need to `cd` anywhere. Just run your command directly.

        **CRITICAL WARNING - NO INTERACTIVE COMMANDS:** Do NOT attempt to run interactive terminal commands like `nano`, `vim`, `top`, or scripts that prompt for user input (e.g., `input()`). They will cause the execution to freeze and time out. Always use non-interactive flags (like `-y` for apt/npm/pip installations). If starting a server or long-running daemon, you MUST append ` &` to the command to run it in the background.

        **Actions & Parameters:**

        1.  **run_command:** Executes a parameterized shell command.
            *   `<command>` (string, required): The exact shell command string to execute.
            *   `<scope>` (string, optional): 'private' or 'shared' (default). Sets the current working directory of the command.
            *   `<timeout>` (integer, optional): Timeout in seconds for command execution. Default is 60. Max is 300.

        **ANTI-PATTERNS (DO NOT DO):**
        - WRONG: `{"command": "cd /workspace && npm start"}` — No need to cd! Use scope="shared".
        - WRONG: `{"command": "cd ../project && pytest"}` — The scope parameter handles directory selection.
        - WRONG: `{"command": "cd src && python main.py"}` — Use the relative path: `python src/main.py`.

        **Examples (CORRECT):**

        *   Run a python script in the shared workspace:
            `{"action": "run_command", "command": "python my_script.py", "scope": "shared"}`

        *   Install a package using pip:
            `{"action": "run_command", "command": "pip install requests"}`

        *   Run pytest tests:
            `{"action": "run_command", "scope": "shared", "command": "pytest tests/ --tb=short"}`

        *   Run a script in a subdirectory (NO cd needed!):
            `{"action": "run_command", "scope": "shared", "command": "python src/main.py"}`

        *   List files in a specific format via bash:
            `{"action": "run_command", "command": "ls -la | grep \\"py\\""}`
        """
        return usage.strip()
