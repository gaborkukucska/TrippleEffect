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
* Round 3m: a project without a test command the framework knows (no "test" script in package.json, no test_*.py) but
  with test files - 2026-10-24 22:12:41 (WiFi_Multiplayer_Snake, no package.json) W1 marked "Kick-off Task 8: Run automated
  tests ..." finished 14 seconds after `node tests/game.test.js` FAILED - needs a passing run of ANY test command (e.g. that
  `node tests/game.test.js`) of the current files.
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
_ANY_RUNS: Dict[str, Dict[str, Any]] = {}  # Round 3m: the latest run of any test command whose exit code is the tests'
_NAMED_TEST_FILE_RE = re.compile(r"\S+\.(?:m?js|cjs|ts|py)\b|::|\s-k\s|--grep|\s-g\s|--test-name-pattern")
# Round 3w: the noun is not part of a path - "Fix SyntaxError in tests/test_server.py" names a FILE in the tests folder, not
# the test suite (2026-10-27 00:37-08:04 W3's finished one-line fix was refused 26 times until the whole suite passed, while
# the other failures were in server.py)
_TEST_RUN_TASK_RE = re.compile(
    r"\b(?:run|runs|running|execute|executes|verify|verifies|fix|fixes|make)\b[^.\n]{0,60}?"
    r"(?<![/\\.\w])(?:tests?|test suite|npm test|pytest|mocha)\b(?![/\\.])", re.IGNORECASE)


def _key(workspace: Any) -> str:
    try:
        return str(Path(workspace).resolve())
    except (OSError, TypeError, ValueError):
        return str(workspace)


def exit_code_is_the_tests(command: Optional[str]) -> bool:
    """Round 3m: True for a test command whose exit code is the tests' own (no pipe, no '||', nothing after a ';')."""
    from src.tools.project_links_check import is_test_command
    if not is_test_command(command):
        return False
    text = str(command)
    if re.search(r"(?<!\|)\|(?!\|)", text):
        return False  # 'npm test | head' - the exit code is the pipe's, not the tests'
    if "||" in text:
        return False  # 'npm test || true', 'npm test || pytest' - the exit code is not (only) the tests'
    from src.tools.project_links_check import test_command_end
    end = test_command_end(text)
    if end is not None and re.search(r";", text[end:]):
        return False  # 'npm test; echo done' - the exit code is not the tests'
    return True


def is_full_test_run(command: Optional[str]) -> bool:
    """True for a test command that runs the whole suite (no single test file, test id or name filter)."""
    if not exit_code_is_the_tests(command):
        return False
    text = str(command)
    from src.tools.project_links_check import test_command_end
    end = test_command_end(text)
    # 'npx mocha tests/' or 'pytest tests' run everything; 'npx mocha tests/test_snake.js' runs one file
    if _NAMED_TEST_FILE_RE.search(text):
        return False
    # Round 3g: 2026-10-23 22:01 'npm test -- tests/test_player' (one folder of the tests) was recorded as a full run
    return end is None or not _test_path_arguments(text[end:])


_WHOLE_SUITE_DIRS = frozenset({"tests", "test", "spec", "__tests__", "."})
_FILTER_FLAGS_WITH_VALUE = frozenset({"-t", "--testNamePattern", "--testPathPattern", "-k", "-m", "--grep", "-g",
                                      "--spec", "--test-name-pattern", "--selectProjects", "--findRelatedTests"})


def _test_path_arguments(rest: str) -> list:
    """Round 3g: positional arguments after a test command that select PART of the tests (not 'tests/' or '.')."""
    import shlex
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    selected = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in ("&&", "&", "|"):
            break
        if token == "--" or re.match(r"^\d*[<>]", token) or ">" in token:
            continue  # npm's argument separator, redirections such as 2>&1
        if token.startswith("-"):
            flag = token.split("=", 1)[0]
            if flag in _FILTER_FLAGS_WITH_VALUE:
                if "=" not in token:
                    skip_next = True
                selected.append(token)
            continue
        if token.rstrip("/") in _WHOLE_SUITE_DIRS:
            continue
        selected.append(token)
    return selected


# Round 4e: pytest's short summary lines ("FAILED tests/x.py::T::test_a - AssertionError: ...", "ERROR tests/x.py - ...")
_PYTEST_PROBLEM_RE = re.compile(r"^(FAILED|ERROR)\s+([\w./\\-]+\.py)(?:::(\S+))?(?:\s+-\s+(.*))?\s*$", re.MULTILINE)


def failing_tests(output: Optional[str]) -> Dict[str, list]:
    """Round 4e: {test file: ['test name: message', ...]} from a pytest run's short summary (empty for other runners)."""
    found: Dict[str, list] = {}
    for kind, path, name, message in _PYTEST_PROBLEM_RE.findall(str(output or "")):
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        detail = " ".join((message or "").split())[:120]
        item = f"{name or '(the file)'}{' (error)' if kind == 'ERROR' else ''}" + (f": {detail}" if detail else "")
        found.setdefault(path, [])
        if item not in found[path]:
            found[path].append(item)
    return found


def record_test_run(workspace: Any, command: str, outcome: str, summary: str = "", cpu_busy: Optional[float] = None,
                    output: Optional[str] = None) -> None:
    """
    Remembers a FULL test run of this workspace (other commands are ignored). Never raises.
    Round 3f: cpu_busy - for a TIMEOUT, the CPU cores the run used in its last second (a loop that never ends, or waiting).
    Round 4e: output - the run's whole output, for the failing tests per file (record["failing"]).
    """
    try:
        if not workspace or not exit_code_is_the_tests(command):
            return
        from src.agents.tool_activity import workspace_fingerprint
        record = {
            "command": str(command).strip(), "outcome": outcome, "summary": " ".join(str(summary or "").split())[-400:],
            "fingerprint": workspace_fingerprint(workspace), "time": time.time(), "cpu_busy": cpu_busy,
            "failing": failing_tests(output) if outcome == FAILED else {},
        }
        _ANY_RUNS[_key(workspace)] = record  # Round 3m
        if not is_full_test_run(command):
            return
        _RUNS[_key(workspace)] = record
        logger.info(f"test_runs: full test run '{str(command).strip()[:80]}' in {workspace}: {outcome}.")
    except Exception as err:
        logger.debug(f"record_test_run failed: {err}")


def latest_test_run(workspace: Any) -> Optional[Dict[str, Any]]:
    return _RUNS.get(_key(workspace))


def latest_any_test_run(workspace: Any) -> Optional[Dict[str, Any]]:
    """Round 3m: the latest run of any test command (one file too) whose exit code is the tests' own."""
    return _ANY_RUNS.get(_key(workspace))


def clear_test_runs() -> None:
    """For tests."""
    _RUNS.clear()
    _ANY_RUNS.clear()


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
        from src.agents.tool_activity import workspace_fingerprint
        if not command:
            return _unknown_command_problem(workspace, workspace_fingerprint(workspace))  # Round 3m
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


def project_test_files(workspace: Any, limit: int = 20) -> list:
    """Round 3m: the test files of the workspace (relative paths, at most `limit`), outside node_modules and the like."""
    from src.tools.project_links_check import is_test_file
    skip = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "coverage", ".te_background"}
    found = []
    try:
        root = Path(workspace)
        for path in sorted(root.rglob("*")):
            if len(found) >= limit:
                break
            rel = path.relative_to(root)
            if set(rel.parts[:-1]) & skip or path.suffix.lower() not in (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".py"):
                continue
            if is_test_file(rel.as_posix()) and path.is_file():
                found.append(rel.as_posix())
    except (OSError, ValueError):
        pass
    return found


def _unknown_command_problem(workspace: Any, current: Any) -> Optional[str]:
    """
    Round 3m: for a project whose test command the framework does not know: None when it has no test files or the latest
    run of a test command (any, one file too) of the current files passed, else the reason.
    """
    test_files = project_test_files(workspace)
    if not test_files:
        return None
    run = latest_any_test_run(workspace)
    if run and current is not None and run.get("fingerprint") == current:
        if run.get("outcome") == PASSED:
            return None
        detail = f" Its output ended with: {run['summary']}" if run.get("summary") else ""
        state = "did not END - it timed out" if run.get("outcome") == TIMEOUT else "failed"
        return f"the tests do NOT pass: the last test run (`{run['command']}`) {state}.{detail}"
    shown = ", ".join(test_files[:3]) + (" ..." if len(test_files) > 3 else "")
    # Round 3x: `node --test` runs a node:test file and a plain script alike (and is the planned runner)
    example = (f"node --test {test_files[0]}" if not test_files[0].endswith(".py") else f"python -m pytest -q {test_files[0]}")
    return (f"no test run of the CURRENT files has passed yet. This project has no test command the framework knows (no "
            f"\"test\" script in package.json), so run its test file(s) ({shown}) directly with command_executor (e.g. "
            f"`{example}`), fix what fails, and mark the task finished when that run passes.")

# END OF FILE src/utils/test_runs.py
