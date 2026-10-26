# START OF FILE src/tools/command_executor.py
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.tools.base import BaseTool, ToolParameter
from src.tools.sandbox_runner import run_sandboxed, resolve_shared_workspace

logger = logging.getLogger(__name__)

# Characters of stdout/stderr returned to the model (head + tail are kept).
MAX_OUTPUT_CHARS = 10000

# Bare "npm install" / "npm i" / "npm ci" with flags only (no package names).
_BARE_NPM_INSTALL_RE = re.compile(r"^npm\s+(?:install|i|ci)(?:\s+--?[\w-]+(?:=\S+)?)*\s*$")

# --- PATH NORMALIZATION: Strip redundant `cd` prefixes ---
# Agents (especially smaller models) hallucinate Docker-style paths like
# `cd /workspace && npm start` or `cd /home/user/project && pytest`.
# Since the CWD is already set correctly by the scope parameter, these
# `cd` prefixes are always redundant and often cause errors.
_CD_PREFIX_PATTERN = re.compile(
    r'^cd\s+'                               # cd followed by space
    r'(?:/workspace|/home/\S+|/app|'         # common hallucinated absolute paths
    r'\.(?:/\S+)?|'                          # relative . or ./foo
    r'\.\.(?:/\S+)?|'                        # relative .. or ../foo
    r'shared_workspace(?:/\S+)?|'             # agents trying to cd into workspace
    r'\S+/shared_workspace(?:/\S+)?)'         # full path to shared_workspace
    r'\s*(?:&&|;)\s*',                       # followed by && or ; separator
    re.IGNORECASE
)

# Auto-background known long-running server commands if the agent forgot '&'.
_SERVER_PATTERNS = [
    "python -m http.server", "python server.py", "python app.py",
    "npm start", "npm run dev", "uvicorn ", "flask run",
    "node server.js", "node app.js", "live-server"
]


def _truncate_head_tail(text: str, limit: int, label: str) -> Tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return (
        text[:half]
        + f"\n...[{label} TRUNCATED: {len(text)} chars exceeded the {limit} char limit; showing first and last {half}]...\n"
        + text[-half:]
    ), True


def _npm_install_is_current(cwd: Path) -> bool:
    """
    True when node_modules was installed after the last change to package.json
    and package-lock.json. npm writes node_modules/.package-lock.json on every
    install, so its mtime marks the last successful install.
    """
    marker = cwd / "node_modules" / ".package-lock.json"
    pkg = cwd / "package.json"
    if not marker.is_file() or not pkg.is_file():
        return False
    try:
        marker_mtime = marker.stat().st_mtime
        newest_manifest = pkg.stat().st_mtime
        lock = cwd / "package-lock.json"
        if lock.is_file():
            newest_manifest = max(newest_manifest, lock.stat().st_mtime)
        return marker_mtime >= newest_manifest
    except OSError:
        return False


# Round 2f: other names small models use for the one action of this tool
RUN_COMMAND_ALIASES = frozenset({"run", "execute", "exec", "shell", "bash", "run_shell", "execute_command", "run_cmd",
                                 "command", "cmd", "run_tests", "run_test"})


class CommandExecutionTool(BaseTool):
    """
    Executes shell commands for agents through the shared sandbox runner
    (scrubbed environment, kill-switch, process-group timeouts, bounded output).
    Commands are additionally checked by the deterministic policy gate in
    ToolExecutor before they reach this tool.
    """
    name: str = "command_executor"
    auth_level: str = "worker"
    summary: Optional[str] = "Executes shell commands (npm, pip, python, bash, etc.) in your workspace or sandbox."
    description: str = (
        "Executes a shell command with its working directory set to your shared workspace or private sandbox. "
        "Useful for installing packages, running tests, executing python/node scripts, "
        "and performing system operations. Interactive commands (like nano, vim, or prompts waiting for input) "
        "are NOT supported: stdin is closed, so they fail immediately. Commands have a strict timeout. "
        "End a command with ' &' to run a server or other long-running process in the background."
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
            description="Working directory: 'shared' (project shared workspace, default when you are in a project) or 'private' (your sandbox).",
            required=False,
        ),
        ToolParameter(
            name="timeout",
            type="integer",
            description="Timeout in seconds for command execution. Default is 60. Min 30, max 300.",
            required=False,
        ),
    ]

    def params_not_required_for(self, args: Dict[str, Any]) -> set:
        """Round 2f: a call with a 'command' but no action can only mean the one action, 'run_command'."""
        if not (args or {}).get("action") and (args or {}).get("command"):
            return {"action"}
        return set()

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        from src.config.settings import settings

        action = kwargs.get("action")
        command = kwargs.get("command")
        default_scope = "shared" if project_name and session_name else "private"
        scope = str(kwargs.get("scope") or default_scope).lower()
        try:
            timeout = float(kwargs.get("timeout", 60.0))
        except (TypeError, ValueError):
            timeout = 60.0

        if isinstance(action, str):
            action = action.strip().strip("<>/\"' \t").lower()  # Round 2f: 'run_command>' (W1, 6x on 2026-10-19)
        if action in RUN_COMMAND_ALIASES or (not action and command):
            action = "run_command"
        if not action or action != "run_command":
            return {"status": "error", "message": "Invalid action. Must be 'run_command'."}

        if not command or not isinstance(command, str) or not command.strip():
            return {"status": "error", "message": "The 'command' parameter is required."}

        if scope not in ["private", "shared"]:
            return {"status": "error", "message": "Invalid 'scope'. Must be 'private' or 'shared'."}

        # Enforce maximum timeout to prevent hanging the system
        if timeout > 300.0:
            timeout = 300.0
        # Enforce minimum timeout to prevent agents from prematurely failing builds
        if timeout < 30.0:
            timeout = 30.0

        # Determine working directory (CWD) based on scope
        cwd_path: Optional[Path] = None
        scope_description = ""

        if scope == "private":
            cwd_path = Path(agent_sandbox_path)
            scope_description = f"agent {agent_id}'s sandbox"
        elif scope == "shared":
            if not project_name or not session_name:
                return {"status": "error", "message": "Cannot use 'shared' scope - project/session context is missing."}
            cwd_path = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, project_name, session_name)
            scope_description = "shared workspace"

        if cwd_path is None:
            return {"status": "error", "message": "Failed to determine working directory for command execution."}

        # Ensure CWD exists
        if not cwd_path.exists():
            try:
                await asyncio.to_thread(cwd_path.mkdir, parents=True, exist_ok=True)
            except Exception as e:
                return {"status": "error", "message": f"Could not create missing working directory {cwd_path}: {e}"}

        original_command = command
        command = _CD_PREFIX_PATTERN.sub('', command).strip()
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

        # Skip a bare `npm install` only when node_modules is provably current
        # (Review R3: the old check skipped any install, even with new packages,
        # and returned fabricated npm output).
        if _BARE_NPM_INSTALL_RE.match(command) and _npm_install_is_current(cwd_path):
            logger.info(f"Agent {agent_id} ran '{command}', but node_modules is newer than package.json/package-lock.json. Skipping.")
            return {
                "status": "success",
                "message": (
                    "Command skipped by the framework (exit code 0): node_modules is already up to date — "
                    "it was installed after the last change to package.json/package-lock.json. "
                    "To add a dependency run 'npm install <package>', which is always executed."
                ),
                "return_code": 0,
                "stdout": "",
                "stderr": ""
            }

        # Auto-background known long-running server commands if agent forgot '&'
        is_server_cmd = any(pattern in command for pattern in _SERVER_PATTERNS)
        auto_backgrounded = False
        if is_server_cmd and not command.strip().endswith("&"):
            auto_backgrounded = True
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
                    logger.info("Framework Optimization: Sanitized requirements.txt to remove sqlite3")
                    req_file.write_text('\n'.join(new_lines))

        if "pip install " in command and "sqlite3" in command:
            return {
                "status": "error",
                "message": "Framework Blocked: sqlite3 is a standard library built-in module and cannot be installed via pip. Do not add it to requirements.txt or pip install it.",
                "content": "Command Execution Failed\n\n--- STDERR ---\nFramework Blocked: sqlite3 is a standard library built-in module and cannot be installed via pip. Do not add it to requirements.txt or pip install it."
            }

        if "pytest" in command:
            # Round 3r: an earlier framework conftest.py loaded src/x.py twice (as 'src.x' and as 'x')
            from src.utils.framework_conftest import refresh_framework_conftest
            refresh_framework_conftest(cwd_path)
        run_started_at = time.time()  # Round 3t: for the data files a test run writes
        result = await run_sandboxed(command, cwd_path, timeout, tool_name=self.name, agent_id=agent_id)

        if result.status == "blocked":
            return {"status": "error", "message": result.message}
        if result.status == "error":
            return {"status": "error", "message": f"Error starting command execution: {result.message}"}
        if result.status == "timeout":
            # Round 3e: a TEST run that does not end is not a server to put in the background: say why it hangs
            from src.tools.project_links_check import is_test_command, test_hang_hint, cpu_state, busy_loop_text
            from src.utils.test_runs import record_test_run, TIMEOUT
            cpu_busy = getattr(result, "cpu_busy", None)  # Round 3f: busy (a loop) or idle (waiting)
            if is_test_command(command):
                record_test_run(cwd_path, command, TIMEOUT, (result.stdout or "")[-400:], cpu_busy=cpu_busy)
                tail = (result.stdout or "").strip()[-800:]
                message = (f"The TEST command timed out after {timeout:.0f} seconds and its process group was terminated: "
                           "the test run did not END." + (f" Output so far (end):\n{tail}" if tail else "")
                           + test_hang_hint(cwd_path, command, cpu_busy, result.stdout))
                response = {"status": "error", "message": message}
                if (result.stderr or "").strip():
                    response["content"] = f"--- PARTIAL STDERR (last 1500 chars) ---\n{result.stderr.strip()[-1500:]}"
                return response
            partial = []
            if result.stdout.strip():
                partial.append(f"--- PARTIAL STDOUT (last 1500 chars) ---\n{result.stdout.strip()[-1500:]}")
            if result.stderr.strip():
                partial.append(f"--- PARTIAL STDERR (last 1500 chars) ---\n{result.stderr.strip()[-1500:]}")
            if cpu_state(cpu_busy) == "busy":
                # Round 3f: a busy process waits for no input and is no server: it loops
                message = (f"Command execution timed out after {timeout:.0f} seconds and its process group was terminated: "
                           + busy_loop_text(cpu_busy, result.stdout) + " Fix that loop in the code - running the command "
                           "again, with a longer timeout or with ' &', will not help.")
            else:
                message = (
                    f"Command execution timed out after {timeout:.0f} seconds and its process group was terminated. "
                    "Interactive commands (nano, vim, input prompts) are not supported: stdin is closed. "
                    "To run a server or other long-running process, end the command with ' &' "
                    "(for example 'python server.py &'). The framework detaches it and writes its output "
                    "to a log file under '.te_background/' that you can read later."
                )
            response = {"status": "error", "message": message}
            if partial:
                response["content"] = "\n\n".join(partial)
            return response

        returncode = result.returncode
        if not result.background_log:
            # Round 3e: the latest FULL test run of the workspace (src/utils/test_runs.py)
            from src.utils.test_runs import record_test_run, PASSED, FAILED
            record_test_run(cwd_path, command, PASSED if returncode == 0 else FAILED,
                            ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[-400:])
        stdout_str, truncated_stdout = _truncate_head_tail(result.stdout, MAX_OUTPUT_CHARS, "STDOUT")
        stderr_str, truncated_stderr = _truncate_head_tail(result.stderr, MAX_OUTPUT_CHARS, "STDERR")
        truncated_stdout = truncated_stdout or result.stdout_truncated
        truncated_stderr = truncated_stderr or result.stderr_truncated

        result_message = f"Command execution completed with return code {returncode}."
        if result.background_log:
            log_hint = "server_output.log" if auto_backgrounded else result.background_log
            result_message = (
                f"Command started in the background (shell exit code {returncode}). "
                f"Its output is being written to '{log_hint}' (relative to the working directory). "
                "Read that file to check on it; do not start the same process again."
            )
        if truncated_stdout or truncated_stderr:
            result_message += " (Output was truncated due to length limits)."

        if returncode != 0 and stderr_str.strip():
            snippet = stderr_str.strip()[-500:]
            result_message += f"\nStderr snippet:\n{snippet}"

        status = "success"  # Always return success so the executor doesn't drop the context!

        # Format content specifically for the LLM context
        content_parts = [f"Command Execution {'Succeeded' if returncode == 0 else 'Failed'} (Exit Code: {returncode})"]
        if stdout_str.strip():
            content_parts.append(f"--- STDOUT ---\n{stdout_str.strip()}")
        if stderr_str.strip():
            content_parts.append(f"--- STDERR ---\n{stderr_str.strip()}")
        if returncode != 0 and not stderr_str.strip() and not stdout_str.strip():
            content_parts.append("--- Output ---\nNo output provided by command.")
        # Round 3n: `python src/x.py` with `from src.` imports, a `pkill -f` that killed itself (src/tools/command_hints.py)
        from src.tools.command_hints import command_notes
        notes = command_notes(cwd_path, command, returncode, f"{result.stdout or ''}\n{result.stderr or ''}")
        if notes:
            content_parts.append("[Framework notes on this command:]\n" + "\n".join(f"- {note}" for note in notes))
        if not result.background_log:
            # Round 3g: failed tests that pass alone, and known traps in the output (src/utils/test_diagnosis.py)
            diagnosis = await self._test_diagnosis(cwd_path, command, returncode, result.stdout, result.stderr,
                                                   run_started_at)
            if diagnosis:
                content_parts.append(diagnosis.strip())
        return {
            "status": status,
            "message": result_message,
            "content": "\n\n".join(content_parts)
        }

    @staticmethod
    async def _test_diagnosis(cwd_path: Path, command: str, returncode: Optional[int], stdout: Optional[str],
                              stderr: Optional[str], run_started_at: Optional[float] = None) -> str:
        """Round 3g: the framework's notes for a FAILED test run ('' for other commands or a passing run). Never raises."""
        try:
            from src.tools.project_links_check import is_test_command
            from src.utils.test_diagnosis import (diagnose_failed_test_run, jest_failed_tests, pytest_failed_tests,
                                                  suite_failures, other_failed_tests)
            if not is_test_command(command):
                return ""
            output = f"{stdout or ''}\n{stderr or ''}"
            # a piped run ('npm test 2>&1 | head -100') ends with the pipe's exit code: the output tells
            # (Round 3m: + the failed tests of other runners - TAP, mocha, unittest, a hand-written runner)
            if returncode == 0 and not jest_failed_tests(output) and not pytest_failed_tests(output) \
                    and not suite_failures(output) and not other_failed_tests(output):
                return ""
            return await diagnose_failed_test_run(cwd_path, command, output, run_started_at)
        except Exception as err:
            logger.debug(f"CommandExecutionTool: test diagnosis failed: {err}")
            return ""

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the CommandExecutionTool."""
        usage = """
        **Tool Name:** command_executor

        **Description:** Executes shell commands in your project's shared workspace or your private sandbox.

        **CRITICAL — WORKING DIRECTORY IS AUTOMATIC:**
        The working directory is automatically set based on the `scope` parameter:
        - `scope="shared"` → Runs inside the project's shared workspace (default when you are in a project).
        - `scope="private"` → Runs inside your private sandbox.
        You do NOT need to `cd` anywhere. Just run your command directly.

        **CRITICAL WARNING - NO INTERACTIVE COMMANDS:** Do NOT run interactive terminal commands like `nano`, `vim`, `top`, or scripts that prompt for user input (e.g., `input()`). Standard input is closed, so they fail or time out. Always use non-interactive flags (like `-y` for apt/npm/pip installations).

        **BACKGROUND PROCESSES:** To start a server or other long-running process, end the command with ` &`. The framework detaches it and writes its output to a log file in `.te_background/`; the result tells you the exact file name.

        **SECURITY:** Commands run with a minimal environment (no API keys or tokens) and are checked by a policy gate. Commands that use sudo, pipe downloads into a shell, read credentials, touch the framework installation, or delete files outside your workspace are blocked.

        **Actions & Parameters:**

        1.  **run_command:** Executes a parameterized shell command.
            *   `<command>` (string, required): The exact shell command string to execute.
            *   `<scope>` (string, optional): 'shared' (default in a project) or 'private'. Sets the working directory of the command.
            *   `<timeout>` (integer, optional): Timeout in seconds for command execution. Default is 60. Min 30, max 300.

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

        *   Start a development server in the background:
            `{"action": "run_command", "scope": "shared", "command": "python -m http.server 8080 &"}`

        *   Run a script in a subdirectory (NO cd needed!):
            `{"action": "run_command", "scope": "shared", "command": "python src/main.py"}`

        *   List files in a specific format via bash:
            `{"action": "run_command", "command": "ls -la | grep \\"py\\""}`
        """
        return usage.strip()
# END OF FILE src/tools/command_executor.py
