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
from typing import Any, Iterable, List, Optional, Tuple

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
    from src.utils.task_files import strip_framework_notes  # Round 4g: the framework's notes are not the task
    return bool(description) and bool(_TEST_WRITING_TASK_RE.search(strip_framework_notes(description)))


def named_directories(description: Optional[str]) -> List[str]:
    """Directories a text names with a trailing slash ('tests/', 'tests/unit/'), without the slash, in order."""
    from src.utils.task_files import strip_framework_notes  # Round 4g
    found: List[str] = []
    for match in _DIRECTORY_RE.finditer(strip_framework_notes(description)):
        rel = match.group(1).rstrip("/")
        parts = PurePosixPath(rel).parts
        if not parts or ".." in parts or rel in found:
            continue
        found.append(rel)
    return found


def task_python_test_files(workspace: Any, description: Optional[str], changed_files: Optional[Iterable[Any]] = None,
                           suffixes: tuple = (".py",)) -> List[str]:
    """
    The Python test files of a task (workspace-relative, at most MAX_TEST_FILES): test files the description names with a
    directory or the worker changed for the task, and - for a task that writes tests - the test files in a folder the
    description names ('... in tests/'). conftest.py files are not tests. Never raises.
    Round 3x: `suffixes` - the JavaScript test files of the task with JS_TEST_SUFFIXES.
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
            if (rel.endswith(suffixes) and PurePosixPath(rel).name != "conftest.py" and is_test_file(rel)
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
                    for path in sorted(base.rglob("*")):
                        if path.suffix in suffixes:
                            add(path)
    except Exception as err:
        logger.debug(f"task_python_test_files failed: {err}")
    return found


def _shown(message: str, limit: int = 160) -> str:
    text = " ".join(str(message).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


# Round 3x: 2026-10-27 09:27:12 "Kick-off Task 3: Write automated tests ... in tests/game.test.js" was marked finished while
# that file called describe() / test() that nothing provided - it did not even load ("ReferenceError: describe is not
# defined"). A JavaScript test file of the task must at least get its test-runner functions from somewhere.
JS_TEST_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")


def javascript_test_problem(workspace: Any, description: Optional[str],
                            changed_files: Optional[Iterable[Any]] = None) -> Optional[str]:
    """Why the task's JavaScript test files cannot run (runner functions nothing provides), or None. Reads files only."""
    try:
        from src.utils.js_test_checks import missing_runner_globals, runner_globals_advice
        root = Path(workspace).resolve()
        for rel in task_python_test_files(root, description, changed_files, suffixes=JS_TEST_SUFFIXES):
            missing = missing_runner_globals(root, rel)
            if missing:
                logger.info(f"test_task_check: {rel} uses {missing} that nothing provides.")
                return f"its tests cannot run: {runner_globals_advice(root, rel, missing)}"
    except Exception as err:
        logger.debug(f"javascript_test_problem failed: {err}")
    return None


# --- Round 4g: the task's JavaScript tests are RUN too -------------------------------------------------------------------------
# 2026-10-30 (WiFi_Snake_Game, qwen3.5:4b) the framework's own note in "Kick-off Task 6: Write pytest tests for game logic in
# tests/game.test.js [Framework note: ... run with `node --test tests/*.test.js` ...]" made the JavaScript test tasks count as
# tasks that RUN the tests, so they needed a passing run, and W3 / W4 bent the finished src/game.js and src/leaderboard.js of
# other workers to fit their tests. Without that note they are test-WRITING tasks, judged like Python test tasks: the tests
# must load, end and call code that exists; a test that fails on a wrong RESULT may stay (the run-all task routes it to the
# code's author, Round 4e). Until now only the runner functions of a JavaScript test were checked, not a run.
_JS_NOT_OK_RE = re.compile(r"^(\s*)not ok \d+ - (.+?)\s*$")
_JS_FIELD_RE = re.compile(r"^\s*(location|failureType|error|name): (.*)$")
_JS_FRAME_RE = re.compile(r"\(?((?:[^\s()]+/)?[\w.-]+\.(?:m?js|cjs|jsx|tsx?)):(\d+):\d+\)?")
_JS_WRONG_CALL_RE = re.compile(r"^(?:[\w$.\[\]'\"]+) is not (?:a function|a constructor|iterable)$|^[\w$]+ is not defined$")
_JS_LOAD_ERROR_RE = re.compile(r"^#\s*((?:\w*Error|Error)(?: \[[\w_]+\])?: .+)$")
_JS_LOAD_AT_RE = re.compile(r"^#\s*((?:[^\s:]+/)?[\w.-]+\.(?:m?js|cjs|jsx|tsx?)):(\d+)\s*$")


def _js_relative(root: Path, raw: str) -> Optional[str]:
    try:
        path = Path(raw.strip("'\""))
        path = path if path.is_absolute() else root / path
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def javascript_run_findings(root: Path, output: str, files: List[str]) -> Tuple[List[Tuple[str, str]], List[Tuple[str, int, str]]]:
    """
    From a `node --test <files>` (TAP) output: ([(test file, error)] of task files that did not even LOAD, [(test file, line,
    error)] of tests that call code that does not exist - "x.y is not a function / not a constructor", "name is not
    defined" raised IN a task test file). Assertion failures and errors raised inside the project's code are not included.
    """
    lines = output.splitlines()
    load_failures: List[Tuple[str, str]] = []
    wrong_calls: List[Tuple[str, int, str]] = []
    previous_end = 0
    for index, line in enumerate(lines):
        match = _JS_NOT_OK_RE.match(line)
        if not match:
            continue
        indent, name = len(match.group(1)), match.group(2)
        fields: dict = {}
        stack_frames: List[Tuple[str, int]] = []
        in_stack = False
        for follow in lines[index + 1:index + 60]:
            stripped = follow.strip()
            if stripped == "..." and len(follow) - len(follow.lstrip()) <= indent + 2:
                break
            field = _JS_FIELD_RE.match(follow)
            if field and field.group(1) not in fields:
                fields[field.group(1)] = field.group(2).strip().strip("'\"")
                in_stack = False
                continue
            if stripped.startswith("stack:"):
                in_stack = True
                continue
            if in_stack:
                frame = _JS_FRAME_RE.search(stripped)
                if frame:
                    stack_frames.append((frame.group(1), int(frame.group(2))))
        named = _js_relative(root, name)
        if named in files and fields.get("failureType") == "testCodeFailure":
            # the whole FILE failed: the error is in the '# ' lines Node printed before this result
            error, at = "", None
            for earlier in lines[previous_end:index]:
                where = _JS_LOAD_AT_RE.match(earlier.strip())
                if where and at is None:
                    at = where.group(2)
                found = _JS_LOAD_ERROR_RE.match(earlier.strip())
                if found and not error:
                    error = found.group(1)
            if error:
                load_failures.append((named, error + (f" (line {at})" if at else "")))
            previous_end = index + 1
            continue
        if indent == 0:
            previous_end = index + 1
        message = fields.get("error", "")
        if fields.get("name") not in ("TypeError", "ReferenceError") or not _JS_WRONG_CALL_RE.match(message):
            continue
        frame = stack_frames[0] if stack_frames else None
        rel = _js_relative(root, frame[0]) if frame else None
        if rel in files:
            item = (rel, frame[1], f"{fields['name']}: {message}")
            if item not in wrong_calls:
                wrong_calls.append(item)
    return load_failures, wrong_calls


async def javascript_run_problem(workspace: Any, files: List[str], description: Optional[str],
                                 agent_id: str = "framework") -> Optional[str]:
    """Round 4g: why the task's JavaScript test files may not be finished after a `node --test` run of them, or None."""
    try:
        root = Path(workspace).resolve()
        if not files:
            return None
        from src.tools.sandbox_runner import run_sandboxed
        command = "node --test " + " ".join(shlex.quote(f) for f in files)
        result = await run_sandboxed(command, root, COMPLETION_TEST_TIMEOUT, tool_name="completion_check",
                                     agent_id=agent_id)
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        if result.status == "timeout":
            from src.tools.project_links_check import test_hang_hint
            return (f"its tests do not END: `{command}` was still running after {COMPLETION_TEST_TIMEOUT:.0f} s."
                    + test_hang_hint(root, command, result.cpu_busy, output))
        if result.status != "completed" or result.returncode in (0, 126, 127) or "TAP version" not in output:
            return None  # passed, blocked, or Node is not there: never block the task for that
        load_failures, wrong_calls = javascript_run_findings(root, output, files)
        from src.utils.js_test_checks import missing_runner_globals, runner_globals_advice, shadowed_module_hints, undefined_name_hints
        if load_failures:
            parts = []
            for rel, error in load_failures[:MAX_SHOWN_ERRORS]:
                parts.append(f"- {rel}: {_shown(error)}")
            hints: List[str] = []
            for rel, _error in load_failures:
                missing = missing_runner_globals(root, rel)
                if missing:
                    hints.append(runner_globals_advice(root, rel, missing))
            hints += [h for h in undefined_name_hints(root, output, [rel for rel, _e in load_failures]) if h not in hints]
            return (f"its test file{'s do' if len(load_failures) > 1 else ' does'} not even LOAD (`{command}`), so no test "
                    "in it runs:\n" + "\n".join(parts)
                    + ("\nWhy:\n" + "\n".join(f"- {hint}" for hint in hints) if hints else "")
                    + "\nFix the test file; the code under test is not wrong for this.")
        if not wrong_calls:
            return None  # the tests run; failing results may be real bugs of the code
        shown = [f"- {rel} line {line}: {_shown(error)}" for rel, line, error in wrong_calls[:MAX_SHOWN_ERRORS]]
        if len(wrong_calls) > MAX_SHOWN_ERRORS:
            shown.append(f"- ... and {len(wrong_calls) - MAX_SHOWN_ERRORS} more")
        hints = []
        failing_files = list(dict.fromkeys(rel for rel, _line, _error in wrong_calls))
        for rel in failing_files[:4]:
            hints += shadowed_module_hints(root, rel)
            from src.utils.code_map import missing_names_hint
            missing = missing_names_hint(root, rel)
            if missing and missing not in hints:
                hints.append(missing)
        hints += [h for h in undefined_name_hints(root, output, failing_files) if h not in hints]
        logger.info(f"test_task_check: {len(wrong_calls)} JavaScript test error(s) that call missing code in {files}.")
        advice = ("Read the code file the test uses and make each test call what it REALLY defines (names, arguments). Do not "
                  "change working code to fit a test. A test that fails because the code computes a WRONG result is fine "
                  "here - name that bug in your report to the PM." if is_test_writing_task(description) else
                  "Make the code and its tests agree: if the task asks for what the test calls, add it to the code; otherwise "
                  "change the test to call what the code really defines.")
        return (f"its tests call code that does NOT exist where they call it, so they fail before they test anything "
                f"(`{command}`):\n" + "\n".join(shown)
                + ("\nWhy:\n" + "\n".join(f"- {hint}" for hint in hints) if hints else "") + "\n" + advice)
    except Exception as err:
        logger.warning(f"test_task_check: the JavaScript run check failed ({err}); not blocking.")
        return None


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
        js_problem = javascript_test_problem(root, description, changed_files)  # Round 3x
        if js_problem:
            return js_problem
        js_files = task_python_test_files(root, description, changed_files, suffixes=JS_TEST_SUFFIXES)
        js_run = await javascript_run_problem(root, js_files, description, agent_id)  # Round 4g
        if js_run:
            return js_run
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
