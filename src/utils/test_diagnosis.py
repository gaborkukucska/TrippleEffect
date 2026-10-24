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
    match = re.search(r"ReferenceError: (describe|it|test|expect|beforeEach) is not defined", text)
    if match:
        hints.append(f"`{match.group(1)}` is not defined: this test file uses the globals of a test runner (jest or mocha). "
                     "Run it with the project's test command (`npm test`, or `npx jest <file>`), not with `node <file>`.")
    return hints


async def diagnose_failed_test_run(workspace: Any, command: str, output: str) -> str:
    """
    The framework's notes for a FAILED test run ('' if none): failed tests that pass alone, and known traps in the output.
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
        isolated = await isolation_check(root, command, output) if failing else None
        if isolated:
            parts.append(isolated)
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


def clear_cache() -> None:
    """For tests."""
    _CACHE.clear()

# END OF FILE src/utils/test_diagnosis.py
