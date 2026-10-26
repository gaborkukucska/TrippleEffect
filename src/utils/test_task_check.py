# START OF FILE src/utils/test_task_check.py
"""
Round 3u: a task whose Python tests call code the project does not have is not finished.

2026-10-26 (WiFi_Snake_Multiplayer, qwen3.5:4b): "Kick-off Task 7: Write pytest tests for game logic, server, player
management, scoring, and leaderboard in tests/" was marked finished at 19:21:08 with 74 failing tests and 9 errors. The tests
called Game.update(), Game.pause(), Game.check_collision(), Game.reset() and a Server.test_client() that do not exist, and
built Player, ScoreEntry and LeaderboardEntry without arguments their constructors require. The completion check looked at
the files only (they were valid Python), and a "write tests" task needs no passing run (Round 3e asks that only of tasks that
say to run, verify or fix the tests - a new test may rightly fail on a bug in the code). The next task, "Kick-off Task 9: Run
pytest tests and deploy ...", then spent 2 h 20 min changing src/game_logic.py to fit those tests.

A test that calls a method the class does not have, or a constructor with arguments it does not take, tests nothing: it fails
before it checks a result. Before such a task is marked finished, the framework runs its Python test files (named in the task,
changed for it, or - for a task that writes tests - the test files in the folder it names) and refuses while an error raised
IN a test file says the test calls code that does not exist (src/utils/test_diagnosis.calls_missing_code). Assertion
failures do not block: a new test may find a real bug. A run that does not end blocks with where it was stuck.
"""
import logging
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)

COMPLETION_TEST_TIMEOUT = 90.0
MAX_TEST_FILES = 12
MAX_SHOWN_ERRORS = 6
_SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".te_background"})
_TEST_WRITING_TASK_RE = re.compile(
    r"\b(?:write|writes|writing|create|creates|creating|add|adds|adding|implement|implements|implementing|develop|"
    r"develops|build|builds)\b[^.\n]{0,80}?\b(?:tests?|test suites?|test cases?|test files?)\b", re.IGNORECASE)
_DIRECTORY_RE = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)+)(?=$|[\s,;:)'\"`])")


def is_test_writing_task(description: Optional[str]) -> bool:
    """True for a task that says to write / create / add tests ('Write pytest tests for ... in tests/')."""
    return bool(description) and bool(_TEST_WRITING_TASK_RE.search(str(description)))


def named_directories(description: Optional[str]) -> List[str]:
    """Directories a text names with a trailing slash ('tests/', 'tests/unit/'), without the slash, in order."""
    found: List[str] = []
    for match in _DIRECTORY_RE.finditer(str(description or "")):
        rel = match.group(1).rstrip("/")
        parts = PurePosixPath(rel).parts
        if not parts or ".." in parts or rel in found:
            continue
        found.append(rel)
    return found


def task_python_test_files(workspace: Any, description: Optional[str], changed_files: Optional[Iterable[Any]] = None) -> List[str]:
    """
    The Python test files of a task (workspace-relative, at most MAX_TEST_FILES): test files the description names with a
    directory or the worker changed for the task, and - for a task that writes tests - the test files in a folder the
    description names ('... in tests/'). conftest.py files are not tests. Never raises.
    """
    found: List[str] = []
    try:
        from src.tools.project_links_check import is_test_file
        from src.utils.task_files import file_paths_in_text, find_in_workspace
        root = Path(workspace).resolve()

        def add(path: Optional[Path]) -> None:
            if path is None or len(found) >= MAX_TEST_FILES:
                return
            try:
                path = Path(path).resolve()
                if not path.is_file() or not path.is_relative_to(root):
                    return
                rel = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                return
            if (rel.endswith(".py") and PurePosixPath(rel).name != "conftest.py" and is_test_file(rel)
                    and not set(PurePosixPath(rel).parts[:-1]) & _SKIP_DIRS and rel not in found):
                found.append(rel)

        for rel in file_paths_in_text(description or "", with_directory_only=True):
            add(find_in_workspace(root, rel))
        for changed in changed_files or []:
            add(Path(changed))
        if is_test_writing_task(description):
            for rel_dir in named_directories(description):
                base = (root / rel_dir).resolve()
                if base.is_dir() and base.is_relative_to(root):
                    for path in sorted(base.rglob("*.py")):
                        add(path)
    except Exception as err:
        logger.debug(f"task_python_test_files failed: {err}")
    return found


def _shown(message: str, limit: int = 160) -> str:
    text = " ".join(str(message).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


async def test_task_problem(workspace: Any, description: Optional[str], changed_files: Optional[Iterable[Any]] = None,
                            agent_id: str = "framework") -> Optional[str]:
    """
    Why a task whose Python tests call code the project does not have may not be marked finished yet, or None (no Python
    test files, commands disabled, the tests only fail on results, or the check could not run). Runs the task's test files
    once with pytest (COMPLETION_TEST_TIMEOUT). Never raises.
    """
    try:
        if not workspace:
            return None
        root = Path(workspace).resolve()
        files = task_python_test_files(root, description, changed_files)
        if not files:
            return None
        from src.tools.sandbox_runner import run_sandboxed
        from src.utils.framework_conftest import refresh_framework_conftest
        refresh_framework_conftest(root)  # Round 3r: the same conftest as the agents' own pytest runs
        command = "python -m pytest -q --tb=short -p no:cacheprovider " + " ".join(shlex.quote(f) for f in files)
        result = await run_sandboxed(command, root, COMPLETION_TEST_TIMEOUT, tool_name="completion_check",
                                     agent_id=agent_id)
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        shown_command = f"python -m pytest -q {' '.join(shlex.quote(f) for f in files)}"
        if result.status == "timeout":
            from src.utils.test_diagnosis import hung_test_note
            stuck = hung_test_note(root, output)
            return (f"its tests do not END: `{shown_command}` was still running after {COMPLETION_TEST_TIMEOUT:.0f} s."
                    + (f"\n{stuck}" if stuck else " A test calls code that never returns (a loop that never ends, a server, "
                       "a wait for input): run the tests and fix that test or code."))
        if result.status != "completed":
            return None  # blocked (DISABLE_COMMAND_EXECUTION) or not started: never block the task for that
        from src.utils.test_diagnosis import calls_missing_code, api_mismatch_hints
        wrong = calls_missing_code(root, output)
        if not wrong:
            return None
        lines = [f"- {location[0]} line {location[1]}: {kind}: {_shown(message)}"
                 for kind, message, location in wrong[:MAX_SHOWN_ERRORS]]
        if len(wrong) > MAX_SHOWN_ERRORS:
            lines.append(f"- ... and {len(wrong) - MAX_SHOWN_ERRORS} more")
        hints = api_mismatch_hints(root, output)
        logger.info(f"test_task_check: {len(wrong)} test error(s) that call missing code in {files}.")
        if is_test_writing_task(description):
            advice = ("Read the code file the test uses and make each test call what it REALLY defines (names, "
                      "arguments). Do not change working code to fit a test. A test that fails because the code computes a "
                      "WRONG result is fine here - name that bug in your report to the PM.")
        else:
            advice = ("Make the code and its tests agree: if the task asks for what the test calls, add it to the code; "
                      "otherwise change the test to call what the code really defines. A test that fails because the code "
                      "computes a WRONG result must be fixed in the code.")
        return (f"its tests call code the project does NOT have, so they fail before they test anything "
                f"(`{shown_command}`):\n" + "\n".join(lines)
                + ("\nWhat the code really defines:\n" + "\n".join(f"- {hint}" for hint in hints) if hints else "")
                + "\n" + advice)
    except Exception as err:
        logger.warning(f"test_task_check: the check failed ({err}); not blocking.")
        return None

# END OF FILE src/utils/test_task_check.py
