# START OF FILE src/utils/test_diagnosis.py
"""
What a FAILED test run means, beyond its own output (Round 3g).

2026-10-23 21:53 - 2026-10-24 09:37 (WiFi_Snake_Game, qwen3.5:4b, 12 hours): the project never finished because of its tests.

* From 01:18 to 09:37 four workers "fixed" src/game.js because "should increase score when food is eaten" and three other
  tests failed (Expected 10, Received 0). The code was right: Game.initGame() loads the game state saved in localStorage,
  and an EARLIER test had saved a finished game (isGameOver: true), so update() returned at once. Each failing test PASSED
  when run alone. Nobody ever ran one alone.
* jest's testMatch ('**/tests/**/*.js') matched the setup file tests/setup.js, so jest reported "Your test suite must contain
  at least one test" - and PM1 created the task "Add at least one test to tests/setup.js".
* `expect(send).toHaveBeenCalledWith(JSON.stringify({ timestamp: expect.any(Number) }))` can never pass: JSON.stringify turns
  the matcher into {"inverse":false}. The worker read it as "the test expects an inverse boolean".
* jsdom has no canvas: canvas.getContext('2d') returns null. Workers spent hours on canvas mocks in tests/setup.js.

diagnose_failed_test_run(): for a failed jest or pytest run, re-runs each failing test ALONE (at most MAX_ISOLATED_TESTS,
within ISOLATION_BUDGET_SECONDS) and reports the ones that pass alone - they depend on state an earlier test left behind -
plus output_hints() for the known traps above. The result is cached per workspace state, so the same failing run is not
re-checked. Never raises.
"""
import json
import logging
import re
import shlex
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_ISOLATED_TESTS = 5
ISOLATION_BUDGET_SECONDS = 120.0
ISOLATED_RUN_TIMEOUT = 60.0
_CACHE: Dict[Tuple[str, Any, Tuple[str, ...]], str] = {}
_CACHE_LIMIT = 50

_JEST_FAIL_FILE_RE = re.compile(r"^\s*FAIL\s+(\S+\.(?:m?js|cjs|jsx|tsx?))\s*(?:\(|$)", re.MULTILINE)
_JEST_FAILED_TEST_RE = re.compile(r"^\s*● (.+ › .+?)\s*$")
_PYTEST_FAILED_RE = re.compile(r"^FAILED (\S+::\S+?)(?: - .*)?$", re.MULTILINE)
_JEST_PASSED_RE = re.compile(r"Tests:\s+(?:.*?,\s*)?(\d+) passed")
_JEST_FAILED_COUNT_RE = re.compile(r"Tests:\s+(?:.*?,\s*)?(\d+) failed")
_PYTEST_PASSED_RE = re.compile(r"\b(\d+) passed\b")
_PYTEST_FAILED_COUNT_RE = re.compile(r"\b(\d+) (?:failed|error)")


# --- which tests failed ---------------------------------------------------------------------------------------------------
def jest_failed_tests(output: Optional[str]) -> List[Tuple[str, str]]:
    """(test file, full test name) of the failed jest tests in the output, in order, without duplicates."""
    found: List[Tuple[str, str]] = []
    current_file: Optional[str] = None
    for line in str(output or "").splitlines():
        file_match = _JEST_FAIL_FILE_RE.match(line)
        if file_match:
            current_file = file_match.group(1)
            continue
        test_match = _JEST_FAILED_TEST_RE.match(line)
        if test_match and current_file:
            name = " ".join(part.strip() for part in test_match.group(1).split(" › "))
            item = (current_file, name)
            if item not in found:
                found.append(item)
    return found


_JEST_SUITE_FAILED_RE = re.compile(r"^\s*● Test suite failed to run\s*$")
_PYTEST_ERROR_RE = re.compile(r"^ERROR (\S+?\.py)(?:::\S+)?(?: - .*)?$", re.MULTILINE)


def suite_failures(output: Optional[str]) -> List[str]:
    """
    Round 3k: test FILES that did not run at all - jest's "● Test suite failed to run" (a missing module, a syntax or config
    error) and pytest's "ERROR tests/x.py" (a collection error). 2026-10-24 14:41-16:34 (WiFi_Snake_Game) every one of W1's
    49 test runs ended like this (`Cannot find module '@babel/preset-env'`, `ws.Server is not a constructor`), so the
    "same tests keep failing" check never counted them: they have no test names.
    """
    found: List[str] = []
    current_file: Optional[str] = None
    for line in str(output or "").splitlines():
        file_match = _JEST_FAIL_FILE_RE.match(line)
        if file_match:
            current_file = file_match.group(1)
            continue
        if current_file and _JEST_SUITE_FAILED_RE.match(line) and current_file not in found:
            found.append(current_file)
    for match in _PYTEST_ERROR_RE.finditer(str(output or "")):
        if match.group(1) not in found:
            found.append(match.group(1))
    return found


def _all_failures(output: str) -> Tuple[str, ...]:
    """Round 3k: failed test names plus 'did not run: <file>' for test files that did not run."""
    names = set(name for _f, name in jest_failed_tests(output)) | set(pytest_failed_tests(output))
    names |= {f"did not run: {path}" for path in suite_failures(output)}
    return tuple(sorted(names))


def pytest_failed_tests(output: Optional[str]) -> List[str]:
    """The node ids of the failed pytest tests ('FAILED tests/test_x.py::test_a - ...' lines)."""
    found: List[str] = []
    for match in _PYTEST_FAILED_RE.finditer(str(output or "")):
        if match.group(1) not in found:
            found.append(match.group(1))
    return found


def _jest_script(workspace: Path) -> Optional[str]:
    try:
        package = json.loads((Path(workspace) / "package.json").read_text(encoding="utf-8"))
        script = ((package or {}).get("scripts") or {}).get("test")
        return script if isinstance(script, str) else None
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        return None


def _runner(workspace: Path, command: str, output: str) -> Optional[str]:
    """'jest', 'pytest' or None for the failed test run."""
    text = str(command or "")
    script = _jest_script(workspace) or ""
    if "pytest" in text or "unittest" in text:
        return "pytest" if pytest_failed_tests(output) else None
    if ("jest" in text or (re.search(r"\bnpm\s+(?:run\s+)?test\b|\bnpm\s+t\b", text) and "jest" in script)) \
            and jest_failed_tests(output):
        return "jest"
    return None


_PYTEST_PREFIX_RE = re.compile(r"((?:\S*python[\d.]*\s+-m\s+)?pytest)\b")


def _isolated_command(runner: str, workspace: Path, test_file: str, name: str, command: str = "") -> str:
    if runner == "pytest":
        # the same interpreter as the agent's run ('.venv/bin/python -m pytest', 'python3 -m pytest', 'pytest')
        match = _PYTEST_PREFIX_RE.search(str(command or ""))
        prefix = match.group(1) if match else "python -m pytest"
        return f"{prefix} -q -p no:cacheprovider {shlex.quote(name)}"
    pattern = "^" + re.escape(name) + "$"
    if "jest" in (_jest_script(workspace) or ""):
        return f"npm test -- {shlex.quote(test_file)} -t {shlex.quote(pattern)}"
    return f"npx jest {shlex.quote(test_file)} -t {shlex.quote(pattern)}"


def _passed_alone(runner: str, returncode: Optional[int], output: str) -> bool:
    if returncode != 0:
        return False
    if runner == "jest":
        passed = _JEST_PASSED_RE.search(output)
        failed = _JEST_FAILED_COUNT_RE.search(output)
        return bool(passed and int(passed.group(1)) >= 1 and not (failed and int(failed.group(1)) > 0))
    passed = _PYTEST_PASSED_RE.search(output)
    failed = _PYTEST_FAILED_COUNT_RE.search(output)
    return bool(passed and int(passed.group(1)) >= 1 and not (failed and int(failed.group(1)) > 0))


def _reads_saved_state(workspace: Path, test_files: List[str]) -> bool:
    """True if the project code or the tests use localStorage/sessionStorage (the most common state shared by tests)."""
    try:
        root = Path(workspace)
        candidates = [root / f for f in test_files]
        src = root / "src"
        if src.is_dir():
            candidates += [p for p in src.rglob("*.js") if "node_modules" not in p.parts][:50]
        for path in candidates:
            try:
                if re.search(r"\b(?:localStorage|sessionStorage)\b", path.read_text(encoding="utf-8", errors="replace")):
                    return True
            except OSError:
                continue
    except Exception:
        return False
    return False


async def isolation_check(workspace: Path, command: str, output: str,
                          budget: float = ISOLATION_BUDGET_SECONDS) -> Optional[str]:
    """
    The '[Framework check ...]' text for failed tests that PASS when run alone, or None (also for runners other than jest
    and pytest, or when every test fails alone too).
    """
    runner = _runner(workspace, command, output)
    if not runner:
        return None
    if runner == "jest":
        failed = jest_failed_tests(output)
        items = [(test_file, name, f"{test_file}: {name}") for test_file, name in failed]
    else:
        items = [("", node, node) for node in pytest_failed_tests(output)]
    if not items:
        return None
    from src.tools.sandbox_runner import run_sandboxed
    started = time.monotonic()
    passed_alone: List[str] = []
    checked = 0
    for test_file, name, shown in items[:MAX_ISOLATED_TESTS]:
        remaining = budget - (time.monotonic() - started)
        if remaining < 5:
            break
        result = await run_sandboxed(_isolated_command(runner, workspace, test_file, name, command), Path(workspace),
                                     min(ISOLATED_RUN_TIMEOUT, remaining), tool_name="test_diagnosis",
                                     agent_id="framework")
        checked += 1
        if result.status == "completed" and _passed_alone(runner, result.returncode,
                                                          f"{result.stdout}\n{result.stderr}"):
            passed_alone.append(shown)
    logger.info(f"test_diagnosis: {len(passed_alone)} of {checked} failed test(s) passed when run alone in {workspace}.")
    if not passed_alone:
        return None
    listed = "\n".join(f"  - {name}" for name in passed_alone)
    storage = ""
    test_files = sorted({f for f, _n, _s in items if f})
    if runner == "jest" and _reads_saved_state(workspace, test_files):
        storage = (" This project uses localStorage: a test that saves a game state (or a score) leaves it for the next "
                   "test, which then loads it. Clear it before every test: `beforeEach(() => { localStorage.clear(); })`.")
    reset = "`beforeEach`" if runner == "jest" else "a fixture (or setup_method)"
    return (f"[Framework check - these failed tests PASS when run ALONE ({len(passed_alone)} of {checked} checked):]\n"
            f"{listed}\n"
            "So the code they test is NOT the problem: they fail only after other tests ran, because an earlier test left "
            "state behind (localStorage/sessionStorage, a global or module-level variable, a shared object, a mock that "
            f"was not reset) that the later test reads.{storage} Reset that state in {reset} instead of changing the code. "
            "Do NOT rewrite the source file for these tests.")


# --- known traps in the output ----------------------------------------------------------------------------------------------
_SETUP_KEYS_RE = re.compile(r"setupFiles(?:AfterEnv)?\s*:\s*\[([^\]]*)\]")
_QUOTED_RE = re.compile(r"""['"`]([^'"`]+)['"`]""")


def _jest_setup_files(workspace: Path) -> List[str]:
    """The setupFiles / setupFilesAfterEnv paths of the jest config (jest.config.js or package.json 'jest')."""
    found: List[str] = []
    root = Path(workspace)
    texts: List[str] = []
    for name in ("jest.config.js", "jest.config.cjs", "jest.config.mjs", "jest.config.json"):
        try:
            texts.append((root / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        jest = (package or {}).get("jest")
        if isinstance(jest, dict):
            for key in ("setupFiles", "setupFilesAfterEnv"):
                for value in jest.get(key) or []:
                    if isinstance(value, str):
                        found.append(value)
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        pass
    for text in texts:
        for block in _SETUP_KEYS_RE.findall(text):
            found.extend(_QUOTED_RE.findall(block))
    cleaned = []
    for value in found:
        value = value.replace("<rootDir>/", "").lstrip("./")
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def output_hints(workspace: Optional[Path], output: Optional[str]) -> List[str]:
    """Hints for known traps in the output of a failed test run (each one sentence or two)."""
    text = str(output or "")
    hints: List[str] = []
    if not text:
        return hints
    if "Your test suite must contain at least one test" in text:
        setup_files = _jest_setup_files(Path(workspace)) if workspace else []
        empty_suites = [m.group(1) for m in re.finditer(
            r"^\s*FAIL\s+(\S+)[^\n]*\n(?:[^\n]*\n){0,3}?\s*Your test suite must contain at least one test", text, re.MULTILINE)]
        named = [f for f in empty_suites if f in setup_files] or setup_files
        files = ", ".join(named) if named else "a helper or setup file"
        hints.append(f"jest runs {files} as a TEST file (its testMatch pattern matches it), but it is a setup/helper file "
                     "with no tests - that is the 'Your test suite must contain at least one test' failure. Do NOT add tests "
                     "to it: exclude it from testMatch (e.g. testPathIgnorePatterns: ['/node_modules/', "
                     "'<rootDir>/tests/setup.js']) or move it out of the tests folder (e.g. to jest.setup.js and point "
                     "setupFilesAfterEnv there).")
    if re.search(r'\\?"inverse\\?"\s*:\s*false', text):
        hints.append("`{\"inverse\":false}` in an expected value means an asymmetric matcher such as `expect.any(Number)` "
                     "was put INSIDE `JSON.stringify(...)` - JSON.stringify turns it into {\"inverse\":false}, so that test "
                     "can never pass. Compare the parsed object instead: `expect(JSON.parse(send.mock.calls[0][0]))"
                     ".toEqual({ type: 'x', timestamp: expect.any(Number) })`. The code under test is fine.")
    if re.search(r"HTMLCanvasElement\.prototype\.getContext|getContext\(\S*\)\W{0,20}(?:returns? |is )?null|"
                 r"Cannot read propert(?:y|ies) of null \(reading '(?:fillRect|clearRect|fillStyle|strokeStyle|beginPath|"
                 r"arc|fill|fillText|strokeRect|drawImage|getImageData|measureText|font)'\)", text):
        hints.append("jsdom has NO canvas: `canvas.getContext('2d')` returns null in jest. Give canvases a fake context once, "
                     "in the test setup file: `HTMLCanvasElement.prototype.getContext = () => ({ fillRect() {}, clearRect() "
                     "{}, beginPath() {}, arc() {}, fill() {}, stroke() {}, fillText() {}, strokeRect() {}, moveTo() {}, "
                     "lineTo() {}, closePath() {}, measureText: () => ({ width: 0 }) });` - not a canvas element per test.")
    missing = []
    for found in re.finditer(r"Cannot find module '([^'./][^']*)'", text):
        name = found.group(1)
        package = "/".join(name.split("/")[:2]) if name.startswith("@") else name.split("/")[0]
        if package not in missing:
            missing.append(package)
    if missing:
        # Round 3k: 2026-10-24 jest.config.js used babel-jest with '@babel/preset-env', which was not installed
        installs = " ".join(missing[:4])
        hints.append(f"The npm package(s) {', '.join(missing[:4])} are not installed, so the test file cannot even load. "
                     f"Either install them (`npm install --save-dev {installs}`, which also adds them to package.json) or "
                     "remove what needs them (e.g. a babel `transform` in jest.config.js that plain Node.js code does not "
                     "need).")
    match = re.search(r"ReferenceError: (describe|it|test|expect|beforeEach) is not defined", text)
    if match:
        hints.append(f"`{match.group(1)}` is not defined: this test file uses the globals of a test runner (jest or mocha). "
                     "Run it with the project's test command (`npm test`, or `npx jest <file>`), not with `node <file>`.")
    return hints


# --- Round 3g+: the same tests keep failing although files change -----------------------------------------------------------
# 2026-10-24 10:07-11:43 (WiFi_Snake_Multiplayer_Game, Python) W1 wrote tests/test_game/test_game.py once and then changed
# src/game.py 45 times in 1.5 hours, because the same 8 tests kept failing: the TESTS were wrong (they expected the snake to
# eat food that is placed at random, `body[1] == (-2, 0)` after a turn, a collision for a straight snake). W2 changed
# tests/test_network/test_network.py 32 times while its mocks answered `send()` instead of `recv()`.
STUCK_RUNS = 4
_CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_SNAPSHOT_SKIP = frozenset({"node_modules", ".git", "__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build",
                            "coverage", ".te_background", ".mypy_cache"})
_STREAKS: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}


def _code_snapshot(root: Path) -> Dict[str, Tuple[int, int]]:
    """{relative path: (mtime_ns, size)} of the code and test files of the workspace (at most 2000)."""
    snapshot: Dict[str, Tuple[int, int]] = {}
    try:
        for path in root.rglob("*"):
            if len(snapshot) >= 2000:
                break
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if set(rel.parts[:-1]) & _SNAPSHOT_SKIP or path.suffix.lower() not in _CODE_SUFFIXES or not path.is_file():
                continue
            stat = path.stat()
            snapshot[rel.as_posix()] = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        pass
    return snapshot


def _failing_test_files(output: str) -> Tuple[str, ...]:
    files = {f for f, _n in jest_failed_tests(output)} | {node.split("::", 1)[0] for node in pytest_failed_tests(output)}
    files |= set(suite_failures(output))  # Round 3k
    return tuple(sorted(files))


def stuck_note(root: Path, output: str) -> Optional[str]:
    """
    Round 3g+: the note for tests that have failed in exactly the same way in STUCK_RUNS (or more) runs in a row although
    code or test files were changed between the runs (repeated every 3 more runs), else None. Never raises.
    """
    try:
        from src.tools.project_links_check import is_test_file
        failing = _all_failures(output)  # Round 3k: + test files that did not run
        if not failing:
            return None
        key = (str(root.resolve()), _failing_test_files(output))
        snapshot = _code_snapshot(root)
        previous = _STREAKS.get(key)
        if previous and previous["failing"] == failing:
            changed = {rel for rel in set(snapshot) | set(previous["snapshot"])
                       if snapshot.get(rel) != previous["snapshot"].get(rel)}
            if changed:
                previous["streak"] += 1
                previous["changed"] |= changed
            previous["snapshot"] = snapshot
            record = previous
        else:
            record = {"failing": failing, "snapshot": snapshot, "streak": 1, "changed": set()}
            _STREAKS[key] = record
        streak = record["streak"]
        if streak < STUCK_RUNS or (streak - STUCK_RUNS) % 3:
            return None
        changed = sorted(record["changed"])
        tests_changed = [rel for rel in changed if is_test_file(rel)]
        shown = ", ".join(changed[:6]) + (" ..." if len(changed) > 6 else "")
        not_run = [name[len("did not run: "):] for name in failing if name.startswith("did not run: ")]
        if not_run and len(not_run) == len(failing):
            # Round 3k: no test ran at all - the suites fail before the first test
            return (f"[Framework check - the test file(s) {', '.join(not_run[:5])} have not RUN at all in the last {streak} "
                    f"runs, although {shown} changed in between:]\n"
                    "Every run stops before the first test ('Test suite failed to run' / a collection ERROR), so changing "
                    "the code or the assertions cannot help. Read the FIRST error under it and fix exactly that: a package "
                    "that is not installed (add it to package.json / requirements.txt and install it), a wrong "
                    "require/import path, or a broken test configuration (jest.config.js, babel). Remove configuration "
                    "the project does not need (e.g. a babel transform without babel installed).")
        head = (f"[Framework check - the same {len(failing)} test(s) have failed in each of the last {streak} runs, "
                f"although {shown} changed in between:]\n")
        causes = ("a test that expects a fixed result from RANDOM values (e.g. food placed at random: set the position in "
                  "the test first), a mock of the wrong method (the code reads the reply with `recv()`, the test sets "
                  "`send.return_value`), or an expectation that contradicts the task or another test")
        if not tests_changed:
            return head + ("Only the code changed - the test file(s) did not. So the TEST may be what is wrong. Before the "
                           "next change, compare each failing assertion with your task description. Typical wrong tests: "
                           f"{causes}. Do not bend the code to an impossible assertion: fix the test (or report it to your "
                           "PM with send_message if the test belongs to another task).")
        return head + ("These changes do not reach the cause. Before the next change, read the FIRST failing assertion and "
                       "the code it calls line by line. If the assertion cannot be true, fix the assertion - typical: "
                       f"{causes}.")
    except Exception as err:
        logger.debug(f"stuck_note failed: {err}")
        return None


async def diagnose_failed_test_run(workspace: Any, command: str, output: str) -> str:
    """
    The framework's notes for a FAILED test run ('' if none): failed tests that pass alone, the same tests failing again
    and again (Round 3g+), and known traps in the output.
    Cached per (workspace, workspace fingerprint, failing tests). Never raises.
    """
    try:
        if not workspace:
            return ""
        root = Path(workspace)
        from src.agents.tool_activity import workspace_fingerprint
        failing = tuple(name for _f, name in jest_failed_tests(output)) + tuple(pytest_failed_tests(output))
        key = (str(root.resolve()), workspace_fingerprint(root), failing)
        if failing and key in _CACHE:
            return _CACHE[key]
        parts: List[str] = []
        isolated = None
        if failing and _isolation_worth_running(root, output):
            isolated = await isolation_check(root, command, output)
            _remember_isolation(root, output, found=bool(isolated))
        if isolated:
            parts.append(isolated)
        stuck = stuck_note(root, output)
        if stuck:
            parts.append(stuck)
        hints = output_hints(root, output)
        if hints:
            parts.append("[Framework hints for this test output:]\n" + "\n".join(f"- {hint}" for hint in hints))
        text = ("\n\n" + "\n\n".join(parts)) if parts else ""
        if failing:
            if len(_CACHE) >= _CACHE_LIMIT:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = text
        return text
    except Exception as err:
        logger.debug(f"diagnose_failed_test_run failed: {err}")
        return ""


# Round 3g+: no second isolation check of the same failing tests while the test files are unchanged (2026-10-24 the check ran
# 53 times for W1's and W2's runs and never found a test that passed alone: their tests were wrong, not order-dependent).
_ISOLATION_DONE: Dict[Tuple[str, Tuple[str, ...]], Tuple[Tuple[str, ...], Dict[str, Tuple[int, int]]]] = {}


def _test_files_snapshot(root: Path, output: str) -> Dict[str, Tuple[int, int]]:
    snapshot = {}
    for rel in _failing_test_files(output):
        try:
            stat = (root / rel).stat()
            snapshot[rel] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            snapshot[rel] = (0, 0)
    return snapshot


def _isolation_worth_running(root: Path, output: str) -> bool:
    failing = tuple(sorted(set(name for _f, name in jest_failed_tests(output)) | set(pytest_failed_tests(output))))
    done = _ISOLATION_DONE.get((str(root.resolve()), _failing_test_files(output)))
    return not (done and done[0] == failing and done[1] == _test_files_snapshot(root, output))


def _remember_isolation(root: Path, output: str, found: bool) -> None:
    key = (str(root.resolve()), _failing_test_files(output))
    if found:
        _ISOLATION_DONE.pop(key, None)  # it found something: check again next time
        return
    failing = tuple(sorted(set(name for _f, name in jest_failed_tests(output)) | set(pytest_failed_tests(output))))
    _ISOLATION_DONE[key] = (failing, _test_files_snapshot(root, output))


def clear_cache() -> None:
    """For tests."""
    _CACHE.clear()
    _STREAKS.clear()
    _ISOLATION_DONE.clear()

# END OF FILE src/utils/test_diagnosis.py
