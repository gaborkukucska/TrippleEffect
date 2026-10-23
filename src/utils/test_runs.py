# START OF FILE src/utils/test_runs.py
"""
The latest full test run of each workspace, and the check that a "run the tests" task really ran them (Round 3e).

2026-10-23 18:41:23 (Multiplayer_Snake_Game): PM1 marked "Kick-off Task 9: Run all tests with npm test and perform end-to-end
verification" finished 90 seconds after its own `npm test` had timed out (the tests never ended: they loaded a module that
started the server). 2026-10-23 12:42:28 (the run before): "Kick-off Task 8: Run the tests and the program to verify that
everything works" was marked finished while the integration run failed with "Cannot find module 'sqlite3'". The completion
check looked only at the files' syntax, never at the tests' result.

* command_executor / test_runner call record_test_run() after every FULL test run (npm test, npx mocha tests/, pytest, ...;
  a run of one named test file is not a full run).
* verification_problem(): for a task whose description says to run / verify / fix the tests, the reason it may not be
  marked finished yet - the last full run failed or did not end, or no full run of the CURRENT files passed - else None.
  The current files are compared by the workspace fingerprint (src/agents/tool_activity.workspace_fingerprint), taken after
  the run, so a change after the run asks for a new run.
Records live in memory: after a restart the tests are simply run again.
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PASSED, FAILED, TIMEOUT = "passed", "failed", "timeout"
_RUNS: Dict[str, Dict[str, Any]] = {}
_NAMED_TEST_FILE_RE = re.compile(r"\S+\.(?:m?js|cjs|ts|py)\b|::|\s-k\s|--grep|\s-g\s|--test-name-pattern")
_TEST_RUN_TASK_RE = re.compile(
    r"\b(?:run|runs|running|execute|executes|verify|verifies|fix|fixes|make)\b[^.\n]{0,60}?"
    r"\b(?:tests?|test suite|npm test|pytest|mocha)\b", re.IGNORECASE)


def _key(workspace: Any) -> str:
    try:
        return str(Path(workspace).resolve())
    except (OSError, TypeError, ValueError):
        return str(workspace)


def is_full_test_run(command: Optional[str]) -> bool:
    """True for a test command that runs the whole suite (no single test file, test id or name filter)."""
    from src.tools.project_links_check import is_test_command
    if not is_test_command(command):
        return False
    text = str(command)
    if re.search(r"(?<!\|)\|(?!\|)", text):
        return False  # 'npm test | head' - the exit code is the pipe's, not the tests'
    from src.tools.project_links_check import test_command_end
    end = test_command_end(text)
    if end is not None and re.search(r"\|\||;", text[end:]):
        return False  # 'npm test || true', 'npm test; echo done' - the exit code is not the tests'
    # 'npx mocha tests/' or 'pytest tests' run everything; 'npx mocha tests/test_snake.js' runs one file
    return not _NAMED_TEST_FILE_RE.search(text)


def record_test_run(workspace: Any, command: str, outcome: str, summary: str = "", cpu_busy: Optional[float] = None) -> None:
    """
    Remembers a FULL test run of this workspace (other commands are ignored). Never raises.
    Round 3f: cpu_busy - for a TIMEOUT, the CPU cores the run used in its last second (a loop that never ends, or waiting).
    """
    try:
        if not workspace or not is_full_test_run(command):
            return
        from src.agents.tool_activity import workspace_fingerprint
        _RUNS[_key(workspace)] = {
            "command": str(command).strip(), "outcome": outcome, "summary": " ".join(str(summary or "").split())[-400:],
            "fingerprint": workspace_fingerprint(workspace), "time": time.time(), "cpu_busy": cpu_busy,
        }
        logger.info(f"test_runs: full test run '{str(command).strip()[:80]}' in {workspace}: {outcome}.")
    except Exception as err:
        logger.debug(f"record_test_run failed: {err}")


def latest_test_run(workspace: Any) -> Optional[Dict[str, Any]]:
    return _RUNS.get(_key(workspace))


def clear_test_runs() -> None:
    """For tests."""
    _RUNS.clear()


def is_test_run_task(description: Optional[str]) -> bool:
    """True for a task that says to run, verify or fix the tests ('Run all tests with npm test ...')."""
    return bool(description) and bool(_TEST_RUN_TASK_RE.search(str(description)))


def project_test_command(workspace: Any) -> Optional[str]:
    """The command that runs this project's tests: 'npm test' (a "test" script), pytest for Python tests, else None."""
    try:
        root = Path(workspace)
        package_file = root / "package.json"
        if package_file.is_file():
            try:
                package = json.loads(package_file.read_text(encoding="utf-8"))
                script = ((package or {}).get("scripts") or {}).get("test")
                if isinstance(script, str) and script.strip() and "no test specified" not in script:
                    return "npm test"
            except (OSError, ValueError, UnicodeDecodeError, AttributeError):
                pass
        skip = {"node_modules", ".git", ".venv", "venv", "__pycache__"}
        for path in root.rglob("test_*.py"):
            if not set(path.relative_to(root).parts[:-1]) & skip:
                return "python -m pytest -q"
    except Exception as err:
        logger.debug(f"project_test_command failed: {err}")
    return None


def verification_problem(workspace: Any, description: Optional[str]) -> Optional[str]:
    """
    Why a task that says to run / verify / fix the tests may not be marked finished yet, or None (also when the task is not
    such a task or the project has no test command the framework knows).
    """
    try:
        if not workspace or not is_test_run_task(description):
            return None
        command = project_test_command(workspace)
        if not command:
            return None
        from src.agents.tool_activity import workspace_fingerprint
        run = latest_test_run(workspace)
        current = workspace_fingerprint(workspace)
        if run and current is not None and run.get("fingerprint") == current:
            if run.get("outcome") == PASSED:
                return None
            if run.get("outcome") == TIMEOUT:
                from src.tools.project_links_check import test_hang_hint
                return (f"the tests do NOT pass: the last full test run (`{run['command']}`) did not END - it timed out."
                        + test_hang_hint(workspace, run["command"], run.get("cpu_busy"), run.get("summary")))
            detail = f" Its output ended with: {run['summary']}" if run.get("summary") else ""
            return f"the tests do NOT pass: the last full test run (`{run['command']}`) failed.{detail}"
        return (f"no full test run of the CURRENT files has passed yet. Run the whole test suite (`{command}`) with "
                "command_executor, fix what fails, and mark the task finished when that run passes.")
    except Exception as err:
        logger.debug(f"verification_problem failed: {err}")
        return None

# END OF FILE src/utils/test_runs.py
