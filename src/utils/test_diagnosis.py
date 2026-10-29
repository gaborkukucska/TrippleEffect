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
from pathlib import Path, PurePath
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
    """
    Round 3k: failed test names plus 'did not run: <file>' for test files that did not run.
    Round 3m: + the failed tests of other runners (other_failed_tests) when jest and pytest name none.
    """
    names = set(name for _f, name in jest_failed_tests(output)) | set(pytest_failed_tests(output))
    if not names:
        names = set(other_failed_tests(output))
    names |= {f"did not run: {path}" for path in suite_failures(output)}
    return tuple(sorted(names))


# --- Round 3m: the failed tests of other test runners -----------------------------------------------------------------------
# 2026-10-24 22:15-23:09 (WiFi_Multiplayer_Snake, no package.json) W1's tests/game.test.js was its own small runner, run with
# `node tests/game.test.js` 30+ times: "✗ initializeGameState creates valid snake: Expected isRunning to be false initially"
# and "Failed tests:\n  - <name>: <error>". Neither jest nor pytest output, so the "same tests keep failing" check never
# counted a run - for 55 minutes W1 toggled `isRunning` in src/game.js while the TEST was wrong.
_TAP_NOT_OK_RE = re.compile(r"^\s*not ok \d+(?:\s+-)?\s+(.+?)\s*(?:#.*)?$")
_MARKED_FAIL_RE = re.compile(r"^\s*(?:✗|✖|✘|×|❌)\s+(.+?)\s*$")
_UNITTEST_FAIL_RE = re.compile(r"^(?:FAIL|ERROR): (\w+ \([\w.]+\))\s*$")
_LABELLED_FAIL_RE = re.compile(r"^\s*FAIL(?:ED)?:\s+(.+?)\s*$")
_FAILED_HEADING_RE = re.compile(r"^\s*(?:failed|failing)\s+tests?\s*:?\s*$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+?)\s*$")
_MOCHA_FAILING_RE = re.compile(r"^\s*\d+ failing\s*$")
_MOCHA_ITEM_RE = re.compile(r"^\s*\d+\) (.+?)\s*$")
_DURATION_RE = re.compile(r"\s*(?:\(\d+(?:\.\d+)?\s*m?s\)|\s\d+(?:\.\d+)?\s*ms)$")  # (0.5ms), vitest's " 5ms"
_NOT_TEST_NAMES = frozenset({"failing tests", "failing tests:", "failed tests", "failed tests:"})


def _clean_test_name(text: str) -> str:
    """'<name>: <error>' -> '<name>', without a trailing duration '(0.5ms)'."""
    name = _DURATION_RE.sub("", str(text or "").strip())
    if ": " in name:
        name = name.split(": ", 1)[0].strip()
    return name[:200]


def other_failed_tests(output: Optional[str]) -> List[str]:
    """
    Round 3m: the names of the failed tests of runners other than jest and pytest, in order, without duplicates: TAP
    (`node --test`: 'not ok 2 - moves left'), mocha ('1 failing' then '1) Game\\n     moves left:'), unittest
    ('FAIL: test_x (tests.test_game.T)'), and hand-written runners ('✗ <name>: <error>', 'FAIL: <name>', a 'Failed tests:'
    list of '- <name>: <error>').
    """
    found: List[str] = []

    def add(raw: str) -> None:
        name = _clean_test_name(raw)
        if name and name.lower() not in _NOT_TEST_NAMES and name not in found:
            found.append(name)

    lines = str(output or "").splitlines()
    in_failed_list = in_mocha = False
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if _FAILED_HEADING_RE.match(line):
            in_failed_list = True
            continue
        if in_failed_list:
            bullet = _BULLET_RE.match(line)
            if bullet:
                add(bullet.group(1))
                continue
            in_failed_list = False
        if _MOCHA_FAILING_RE.match(line):
            in_mocha = True
            continue
        if in_mocha:
            item = _MOCHA_ITEM_RE.match(line)
            if item:
                parts = [item.group(1)]
                while not parts[-1].endswith(":") and index < len(lines) and len(parts) < 6 and lines[index].strip():
                    parts.append(lines[index].strip())
                    index += 1
                add(" ".join(part.rstrip(":").strip() for part in parts))
                continue
        for pattern in (_TAP_NOT_OK_RE, _UNITTEST_FAIL_RE, _MARKED_FAIL_RE, _LABELLED_FAIL_RE):
            match = pattern.match(line)
            if match:
                add(match.group(1))
                break
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
        hints.append(runner_globals_hint(workspace, text, match.group(1)))
    if workspace:
        hints.extend(inverted_check_hints(Path(workspace), text))  # Round 3m
        # Round 3q: 2026-10-25 22:53:59 "No module named 'src.frontend.players'" for src/frontend/players.js got no hint
        from src.tools.project_links_check import missing_module_language_hints
        hints.extend(missing_module_language_hints(Path(workspace), text))
    return hints


def _node_command(workspace: Optional[Path]) -> str:
    """Round 4f: the `node --test` command naming the project's JavaScript test files (Node 22 cannot run a folder)."""
    from src.utils.js_test_checks import node_test_command
    return node_test_command(workspace)


def runner_globals_hint(workspace: Optional[Path], output: str, name: str) -> str:
    """
    Round 3x: the hint for "ReferenceError: describe is not defined". It said "run it with `npm test`, or `npx jest
    <file>`" also for a project without package.json whose plan runs JavaScript tests with `node --test` (2026-10-27: 35
    minutes of npm / npx jest / jest.config.js / package.json). Now: what provides those functions in THIS project.
    """
    from src.utils.js_test_checks import js_test_files, missing_runner_globals, package_test_runner, runner_globals_advice
    if workspace:
        runner = package_test_runner(workspace)
        if runner in ("jest", "vitest", "jasmine", "mocha"):
            return (f"`{name}` is not defined: this test file uses the globals of {runner}, which only the project's test "
                    f"command provides. Run it with `npm test` (package.json runs {runner}), not with `node <file>`.")
        named = [f for f in js_test_files(workspace) if f in output]
        for rel in named or js_test_files(workspace):
            missing = missing_runner_globals(workspace, rel)
            if missing:
                return runner_globals_advice(workspace, rel, missing)
    return (f"`{name}` is not defined: the test file uses a test runner's functions without taking them from anywhere. "
            "Take them from Node's built-in runner (`const { describe, test, beforeEach } = require('node:test');` at the "
            f"top of the file, run with `{_node_command(workspace)}`), or run the file with the project's test command if "
            "its package.json \"test\" script runs jest or mocha.")


# --- Round 3m: a check that throws exactly when the value is RIGHT ----------------------------------------------------------
# 2026-10-24 22:15-23:09 tests/game.test.js line 88: `if (!gameState.isRunning) { throw new Error('Expected isRunning to be
# false initially'); }` - it throws exactly when isRunning IS false. W1 (and PM1, who rewrote the task as "set isRunning to
# false (line 53 in game.js)") changed src/game.js for 55 minutes; W1 thought about it for 39,553 characters in one reply.
_EXPR = r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[[^\]\n]+\])*"
_JS_GUARDED_THROW_RE = re.compile(
    r"\bif\s*\(\s*(?P<cond>[^()\n]+?)\s*\)\s*\{?\s*\n?\s*throw\s+(?:new\s+)?\w*Error\(\s*(?P<q>['\"`])(?P<msg>.*?)(?P=q)")
_PY_GUARDED_RAISE_RE = re.compile(
    r"\bif\s+(?P<cond>[^:\n]+?)\s*:\s*\n?\s*raise\s+\w*(?:Error|Exception)\(\s*(?P<q>['\"])(?P<msg>.*?)(?P=q)")
_EXPECTED_BOOL_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s+(?:to\s+be|should\s+be|must\s+be)\s+(?:initially\s+)?(true|false|truthy|falsy)\b",
    re.IGNORECASE)
_TEST_SOURCE_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".py")


def _check_parts(cond: str, python: bool) -> Optional[Tuple[str, bool, str]]:
    """(checked expression, the value for which the check throws, the corrected condition) of a simple check, or None."""
    cond = cond.strip()
    if python:
        match = re.fullmatch(rf"not\s+({_EXPR})", cond)
        if match:
            return match.group(1), False, match.group(1)
        match = re.fullmatch(rf"({_EXPR})\s+(is not|is|==|!=)\s+(True|False)", cond)
        if match:
            equal = match.group(2) in ("is", "==")
            value = match.group(3) == "True"
            fixed_op = {"is": "is not", "is not": "is", "==": "!=", "!=": "=="}[match.group(2)]
            return match.group(1), value if equal else not value, f"{match.group(1)} {fixed_op} {match.group(3)}"
        if re.fullmatch(_EXPR, cond):
            return cond, True, f"not {cond}"
        return None
    match = re.fullmatch(rf"!\s*({_EXPR})", cond)
    if match:
        return match.group(1), False, match.group(1)
    match = re.fullmatch(rf"({_EXPR})\s*(===|==|!==|!=)\s*(true|false)", cond)
    if match:
        equal = match.group(2) in ("===", "==")
        value = match.group(3) == "true"
        fixed_op = {"===": "!==", "==": "!=", "!==": "===", "!=": "=="}[match.group(2)]
        return match.group(1), value if equal else not value, f"{match.group(1)} {fixed_op} {match.group(3)}"
    if re.fullmatch(_EXPR, cond):
        return cond, True, f"!{cond}"
    return None


def _test_sources(root: Path, limit: int = 100) -> List[Path]:
    from src.tools.project_links_check import is_test_file
    found: List[Path] = []
    try:
        for path in sorted(root.rglob("*")):
            if len(found) >= limit:
                break
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if set(rel.parts[:-1]) & _SNAPSHOT_SKIP or path.suffix.lower() not in _TEST_SOURCE_SUFFIXES:
                continue
            if is_test_file(rel.as_posix()) and path.is_file() and path.stat().st_size <= 300_000:
                found.append(path)
    except OSError:
        pass
    return found


def inverted_check_hints(root: Path, output: str) -> List[str]:
    """
    Round 3m: for a failed message such as 'Expected isRunning to be false' that the output shows, the test files' checks
    `if (!x.isRunning) throw new Error('Expected isRunning to be false ...')` (Python: `if not x: raise ...`) that throw
    exactly when the value IS what the message expects. At most 3. Never raises.
    """
    hints: List[str] = []
    try:
        text = str(output or "")
        if not _EXPECTED_BOOL_RE.search(text):
            return hints
        for path in _test_sources(root):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            python = path.suffix.lower() == ".py"
            for match in (_PY_GUARDED_RAISE_RE if python else _JS_GUARDED_THROW_RE).finditer(source):
                message = match.group("msg")
                shown = message.split("${", 1)[0].split("{", 1)[0] if match.group("q") == "`" or python else message
                if len(shown.strip()) < 8 or shown not in text:
                    continue  # this check did not fail in the run
                expected = _EXPECTED_BOOL_RE.search(message)
                parts = _check_parts(match.group("cond"), python)
                if not expected or not parts:
                    continue
                expression, throws_for, fixed = parts
                subject = expected.group(1)
                last_name = re.findall(r"[A-Za-z_$][\w$]*", expression)[-1]  # gameState.isRunning, s['isRunning']
                if last_name.lower() != subject.lower():
                    continue
                wanted = expected.group(2).lower() in ("true", "truthy")
                if throws_for != wanted:
                    continue
                line = source.count("\n", 0, match.start()) + 1
                rel = path.relative_to(root).as_posix()
                word = "true" if wanted else "false"
                keyword = "raise" if python else "throw"
                hints.append(
                    f"{rel} line {line}: `if {'' if python else '('}{match.group('cond').strip()}{':' if python else ')'}` "
                    f"{keyword}s '{' '.join(message.split())[:100]}' exactly when {subject} IS {word} - the check is "
                    f"INVERTED, so this test fails when the code is RIGHT. Fix the condition in the test (`if "
                    f"{'' if python else '('}{fixed}{':' if python else ')'}`); do not change the code for it.")
                if len(hints) >= 3:
                    return hints
    except Exception as err:
        logger.debug(f"inverted_check_hints failed: {err}")
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
                  "`send.return_value`), an inverted check (`if (!x) throw new Error('Expected x to be false')` throws "
                  "exactly when x IS false), a move the code rightly refuses (a snake heading right cannot turn straight "
                  "back LEFT into its own body), or an expectation that contradicts the task or another test")
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


# --- Round 3o: a test that calls the project's code in a way the code does not define ---------------------------------------
# 2026-10-25 13:35-13:40 (Laptop_Snake_Multiplayer, Python): W2's tests/test_snake.py called
# `gs.spawn_food(position=Point(5, 5))` ("TypeError: GameState.spawn_food() got an unexpected keyword argument 'position'"),
# used HTTPServer and BaseHTTPRequestHandler without importing them ("NameError: name 'BaseHTTPRequestHandler' is not defined",
# the whole file did not run) - and W2 changed src/snake_game.py to fit its tests. Such an error says exactly what is wrong
# and where; the framework now says it too, with the definition the code really has.
_E_LINE_RE = re.compile(r"^E\s+(?P<kind>(?:\w+\.)*\w*(?:Error|Exception))\s*:\s*(?P<msg>.+?)\s*$")
_LOCATION_AFTER_RE = re.compile(r"^(?P<path>[^\s:]+\.py):(?P<line>\d+): (?P<kind>[\w.]+)\s*$")
_LOCATION_BEFORE_RE = re.compile(r"^(?P<path>[^\s:]+\.py):(?P<line>\d+): in \S+")
_SUMMARY_ERROR_RE = re.compile(
    r"^(?:FAILED|ERROR) \S+ - (?P<kind>(?:\w+\.)*\w*(?:Error|Exception)): (?P<msg>.+?)\s*$", re.MULTILINE)
_SECTION_LINE_RE = re.compile(r"^(?:_{3,}|={3,})")
_UNEXPECTED_KW_RE = re.compile(r"^(?P<qual>[\w.<>]+)\(\) got an unexpected keyword argument '(?P<arg>\w+)'")
_MISSING_ARGS_RE = re.compile(r"^(?P<qual>[\w.<>]+)\(\) missing \d+ required (?:positional|keyword-only) arguments?: (?P<args>.+)$")
_TOO_MANY_RE = re.compile(r"^(?P<qual>[\w.<>]+)\(\) takes (?:from \d+ to )?(?P<takes>\d+) positional arguments? "
                          r"but (?P<given>\d+) (?:were|was) given")
_NO_ATTRIBUTE_RE = re.compile(r"^'(?P<cls>\w+)' object has no attribute '(?P<attr>\w+)'")
_MODULE_NO_ATTRIBUTE_RE = re.compile(r"^module '(?P<mod>[\w.]+)' has no attribute '(?P<attr>\w+)'")
_CANNOT_IMPORT_RE = re.compile(r"^cannot import name '(?P<name>\w+)' from '(?P<mod>[\w.]+)'")
_NAME_ERROR_RE = re.compile(r"^name '(?P<name>\w+)' is not defined")
MAX_API_HINTS = 4
# where a name a test forgot to import usually comes from (checked with hasattr, never guessed)
_STDLIB_IMPORT_MODULES = ("http.server", "json", "threading", "time", "socket", "socketserver", "unittest.mock", "random",
                          "dataclasses", "typing", "collections", "enum", "pathlib", "io", "urllib.request", "os", "sys",
                          "asyncio", "math", "datetime", "http.client", "tempfile", "contextlib", "functools", "itertools")


def failure_locations(output: Optional[str]) -> List[Tuple[str, str, Optional[Tuple[str, int]]]]:
    """
    Round 3o: (exception kind, message, (file, line) where it was raised or None) of the errors in a pytest output, in
    order, without duplicates: the 'E   TypeError: ...' lines with the 'tests/x.py:194: TypeError' line after them (or the
    'tests/x.py:315: in <module>' line before them), plus the short summary's 'FAILED/ERROR ... - Kind: message' lines.
    """
    found: List[Tuple[str, str, Optional[Tuple[str, int]]]] = []
    seen = set()
    lines = str(output or "").splitlines()
    before: Optional[Tuple[str, int]] = None
    for index, line in enumerate(lines):
        if _SECTION_LINE_RE.match(line):
            before = None
            continue
        located = _LOCATION_BEFORE_RE.match(line)
        if located:
            before = (located.group("path"), int(located.group("line")))
            continue
        error = _E_LINE_RE.match(line)
        if not error:
            continue
        location = None
        for following in lines[index + 1:index + 60]:
            if _SECTION_LINE_RE.match(following):
                break
            after = _LOCATION_AFTER_RE.match(following)
            if after:
                location = (after.group("path"), int(after.group("line")))
                break
        location = location or before
        key = (error.group("kind").split(".")[-1], error.group("msg"))
        if key not in seen:
            seen.add(key)
            found.append((key[0], key[1], location))
    for summary in _SUMMARY_ERROR_RE.finditer(str(output or "")):
        key = (summary.group("kind").split(".")[-1], summary.group("msg"))
        if key[1].endswith("...") or any(key[0] == k and key[1] == m for k, m, _l in found):
            continue  # cut to the terminal width, or already found with its location
        if key not in seen:
            seen.add(key)
            found.append((key[0], key[1], None))
    return found


def _signature(node: Any) -> str:
    import ast
    try:
        text = f"def {node.name}({ast.unparse(node.args)})"
        if getattr(node, "returns", None) is not None:
            text += f" -> {ast.unparse(node.returns)}"
    except Exception:
        text = f"def {getattr(node, 'name', '?')}(...)"
    return text if len(text) <= 200 else text[:197] + "..."


def project_definitions(root: Path, limit: int = 200) -> Dict[str, Any]:
    """
    Round 3o: the functions, classes (with their methods and attributes) and module names of the project's Python files -
    test files and conftest.py excluded. {'functions': {name: [(rel, line, signature)]}, 'classes': {name: [{'rel', 'line',
    'methods': {name: (line, signature)}, 'attributes': [...]}]}, 'modules': {dotted name: (rel, [top-level names])}}.
    """
    import ast
    import warnings
    from src.tools.project_links_check import is_test_file
    result: Dict[str, Any] = {"functions": {}, "classes": {}, "modules": {}}
    try:
        files = []
        for path in sorted(Path(root).rglob("*.py")):
            if len(files) >= limit:
                break
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if set(rel.parts[:-1]) & _SNAPSHOT_SKIP or rel.name == "conftest.py" or is_test_file(rel.as_posix()):
                continue
            if path.is_file() and path.stat().st_size <= 300_000:
                files.append((path, rel.as_posix()))
        for path, rel in files:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
                continue
            top_names: List[str] = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    top_names.append(node.name)
                    result["functions"].setdefault(node.name, []).append((rel, node.lineno, _signature(node)))
                elif isinstance(node, ast.ClassDef):
                    top_names.append(node.name)
                    methods: Dict[str, Tuple[int, str]] = {}
                    attributes: List[str] = []
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            methods[item.name] = (item.lineno, _signature(item))
                            for sub in ast.walk(item):
                                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and \
                                        sub.value.id == "self" and isinstance(sub.ctx, ast.Store) and sub.attr not in attributes:
                                    attributes.append(sub.attr)
                        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                            if item.target.id not in attributes:
                                attributes.append(item.target.id)
                        elif isinstance(item, ast.Assign):
                            for target in item.targets:
                                if isinstance(target, ast.Name) and target.id not in attributes:
                                    attributes.append(target.id)
                    result["classes"].setdefault(node.name, []).append(
                        {"rel": rel, "line": node.lineno, "methods": methods, "attributes": attributes})
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    top_names += [t.id for t in targets if isinstance(t, ast.Name)]
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    top_names += [(alias.asname or alias.name).split(".")[0] for alias in node.names if alias.name != "*"]
            dotted = rel[:-3].replace("/", ".")
            for name in {dotted, dotted[4:] if dotted.startswith("src.") else dotted, dotted.split(".")[-1]}:
                result["modules"].setdefault(name, (rel, top_names))
    except Exception as err:
        logger.debug(f"project_definitions failed: {err}")
    return result


def _member_names(cls: Dict[str, Any], limit: int = 16) -> str:
    names = [name for name in cls["attributes"] if not name.startswith("_")]
    names += [f"{name}()" for name in cls["methods"] if not name.startswith("_") and name not in names]
    shown = ", ".join(names[:limit]) + (" ..." if len(names) > limit else "")
    return shown or "no public attributes"


def _resolve_callable(defs: Dict[str, Any], qual: str) -> Optional[Tuple[str, int, str]]:
    """(rel, line, signature) of 'Class.method', 'function', 'module.function' or a class (its __init__), or None."""
    parts = [part for part in qual.split(".") if part and part != "<locals>"]
    if not parts:
        return None
    if len(parts) >= 2:
        owner, member = parts[-2], parts[-1]
        for cls in defs["classes"].get(owner, []):
            if member in cls["methods"]:
                line, signature = cls["methods"][member]
                return cls["rel"], line, signature
        module = defs["modules"].get(owner)
        if module:
            for rel, line, signature in defs["functions"].get(member, []):
                if rel == module[0]:
                    return rel, line, signature
    name = parts[-1]
    if name == "__init__":
        return None
    functions = defs["functions"].get(name, [])
    if len(functions) == 1:
        return functions[0]
    classes = defs["classes"].get(name, [])
    if len(classes) == 1:
        cls = classes[0]
        if "__init__" in cls["methods"]:
            line, signature = cls["methods"]["__init__"]
            return cls["rel"], line, signature
        if cls["attributes"]:
            return cls["rel"], cls["line"], f"class {name} with the fields {', '.join(cls['attributes'][:12])}"
    return None


def _stdlib_source(name: str) -> Optional[str]:
    import importlib
    for module_name in _STDLIB_IMPORT_MODULES:
        try:
            if hasattr(importlib.import_module(module_name), name):
                return module_name
        except Exception:
            continue
    return None


# --- Round 3r: objects a module creates once, at import, that every test shares ------------------------------------------
# 2026-10-26 07:41-09:05 (WiFi_Snake_Game, qwen3.5:4b) W2's tests imported register_player, get_player and get_leaderboard
# from src/api.py, which creates `player_manager = PlayerManager()` and `leaderboard = Leaderboard()` when it is imported.
# Its autouse fixtures deleted config/players.json and config/leaderboard.json before each test - after both objects had
# already loaded them - so players registered by one test were still registered in the next ("assert 3 == 2", "assert 2 is
# None") and the two entries an earlier run had saved stayed on the leaderboard ("assert 2 == 0"). 25 test runs in 85
# minutes; the diagnosis only said that 1 of 5 failed tests passed alone.
MAX_SHARED_STATE_HINTS = 2
_DATA_FILE_RE = re.compile(r"^(?![/\\])[\w.\-/]+\.(?:json|csv|db|sqlite3?|txt|pkl|pickle|dat)$")


def _python_module_path(root: Path, test_rel: str, module: str) -> Optional[Path]:
    """The project file of an absolute import in a test (searched from the root, src/ and the test's folder)."""
    parts = [part for part in str(module).split(".") if part]
    if not parts:
        return None
    for base in (root, root / "src", (root / test_rel).parent):
        target = base.joinpath(*parts)
        for candidate in (Path(str(target) + ".py"), target / "__init__.py"):
            try:
                if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()):
                    return candidate
            except (OSError, ValueError):
                continue
    return None


def _parse_python(path: Path):
    import ast
    import warnings
    try:
        if path.stat().st_size > 300_000:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return None


def _data_files_of(root: Path, rel: str) -> List[str]:
    """Relative data file names (config/players.json) written as string literals in a project file."""
    import ast
    tree = _parse_python(root / rel)
    found: List[str] = []
    for node in ast.walk(tree) if tree is not None else []:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _DATA_FILE_RE.match(node.value.strip()):
            if node.value.strip() not in found:
                found.append(node.value.strip())
    return found[:3]


def _class_level_containers(root: Path, classes: Dict[str, Any], names: List[str]) -> List[Dict[str, Any]]:
    """Round 3r: the dict/list/set class attributes the instances of these project classes share
    (src/tools/python_static_check.shared_mutable_class_attributes), with the file of each class."""
    from src.tools.python_static_check import shared_mutable_class_attributes
    found: List[Dict[str, Any]] = []
    for name in dict.fromkeys(names):
        for definition in classes.get(name, [])[:1]:
            tree = _parse_python(root / definition["rel"])
            if tree is None:
                continue
            for item in shared_mutable_class_attributes(tree):
                if item["class"] == name:
                    found.append(dict(item, rel=definition["rel"]))
    return found


def shared_module_state_hints(root: Optional[Path], output: Optional[str]) -> List[str]:
    """
    Round 3r: for failed pytest tests whose file imports from a project module that creates instances of project classes
    at import (`player_manager = PlayerManager()`) which the imported functions use (or the test imports directly), the
    hint that every test shares those objects - and the data files their classes read, if any. Never raises.
    """
    import ast
    hints: List[str] = []
    try:
        if not root or not pytest_failed_tests(output):
            return hints
        root = Path(root)
        test_files = [rel for rel in _failing_test_files(str(output)) if rel.endswith(".py")][:5]
        if not test_files:
            return hints
        classes = project_definitions(root)["classes"]
        if not classes:
            return hints
        seen = set()
        for test_rel in test_files:
            tree = _parse_python(root / test_rel)
            if tree is None:
                continue
            for node in tree.body:
                if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                    continue
                module_path = _python_module_path(root, test_rel, node.module)
                if module_path is None or module_path in seen:
                    continue
                seen.add(module_path)
                module_tree = _parse_python(module_path)
                if module_tree is None:
                    continue
                shared: Dict[str, Tuple[int, str]] = {}
                for statement in module_tree.body:
                    if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
                        func = statement.value.func
                        class_name = func.id if isinstance(func, ast.Name) else (
                            func.attr if isinstance(func, ast.Attribute) else None)
                        if class_name in classes:
                            for target in statement.targets:
                                if isinstance(target, ast.Name):
                                    shared[target.id] = (statement.lineno, class_name)
                if not shared:
                    continue
                imported = {alias.name for alias in node.names}
                users = [statement.name for statement in module_tree.body
                         if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name in imported
                         and {n.id for n in ast.walk(statement) if isinstance(n, ast.Name)} & set(shared)]
                if not users and not imported & set(shared):
                    continue
                rel_module = module_path.resolve().relative_to(root.resolve()).as_posix()
                ordered = sorted(shared.items(), key=lambda item: item[1][0])[:3]
                created = " and ".join(f"`{name} = {cls}()` (line {line})" for name, (line, cls) in ordered)
                first_name, (_line, first_class) = ordered[0]
                used_by = f" - {', '.join(users[:4])} use{'s' if len(users) == 1 else ''} them" if users else ""
                text = (f"{rel_module} creates {created} ONCE, when it is imported, so every test shares these objects"
                        f"{used_by}. What one test adds (e.g. a registered player or a score) is still there in the next "
                        "test, and a fixture that deletes a data file does not reset them: they read it at import, before "
                        "any fixture ran.")
                data_files: List[str] = []
                for _name, (_l, cls) in ordered:
                    for definition in classes.get(cls, [])[:1]:
                        for data_file in _data_files_of(root, definition["rel"])[:2]:  # the first ones of each class
                            if data_file not in data_files:
                                data_files.append(data_file)
                if data_files:
                    text += (f" They also read and write the project's own data file(s) {', '.join(data_files[:4])}, so "
                             "what earlier test runs saved there comes back.")
                class_level = _class_level_containers(root, classes, [cls for _n, (_l, cls) in ordered])
                text += (f" Give each test fresh objects in an autouse fixture: delete the data files first, then e.g. "
                         f"`import {node.module}` and `{node.module}.{first_name} = {first_class}()`.")
                if class_level:
                    shown_items = "; ".join(f"{item['class']}.{item['name']} ({item['rel']} line {item['line']})"
                                            for item in class_level[:2])
                    first = class_level[0]
                    text += (f" A new instance alone does not help here: {shown_items} is a CLASS attribute that all "
                             f"instances share - it belongs in __init__ (`self.{first['name']} = ...`); until that is fixed "
                             f"the fixture must empty it too (`{first['class']}.{first['name']}.clear()`).")
                text += " Do not change the assertions for this."
                hints.append(text)
                if len(hints) >= MAX_SHARED_STATE_HINTS:
                    return hints
    except Exception as err:
        logger.debug(f"shared_module_state_hints failed: {err}")
    return hints


def api_mismatch_hints(root: Optional[Path], output: Optional[str]) -> List[str]:
    """
    Round 3o: for errors in a pytest output that say the code was called in a way it does not define (an unexpected or
    missing argument, an attribute or name that does not exist, an import of a name a module does not have), what the
    project's code really defines and whether the TEST or the code made the call. At most MAX_API_HINTS. Never raises.
    """
    hints: List[str] = []
    try:
        if not root or not output:
            return hints
        errors = [e for e in failure_locations(output)
                  if e[0] in ("TypeError", "AttributeError", "ImportError", "ModuleNotFoundError", "NameError")]
        if not errors:
            return hints
        from src.tools.project_links_check import is_test_file
        defs = project_definitions(Path(root))

        def where(location: Optional[Tuple[str, int]]) -> Tuple[str, Optional[bool]]:
            if not location:
                return "A test", None
            path = location[0].replace("\\", "/")
            return f"{path} line {location[1]}", bool(is_test_file(path))

        def verdict(in_test: Optional[bool], location: Optional[Tuple[str, int]], kind: str = "") -> str:
            if in_test and kind == "NameError":
                return " Fix the test file - the code is not the problem."
            if in_test:
                return (" So the TEST is wrong here: change the test to match the code - do not change the code to fit "
                        "the test.")
            if in_test is False and location:
                return f" So the code itself makes this wrong call: fix line {location[1]} of {location[0]}."
            return " Check whether the test or the code makes this call, and fix the caller."

        for kind, message, location in errors:
            if len(hints) >= MAX_API_HINTS:
                break
            place, in_test = where(location)
            hint = None
            match = _UNEXPECTED_KW_RE.match(message)
            if match:
                found = _resolve_callable(defs, match.group("qual"))
                if found:
                    hint = (f"{place} calls `{match.group('qual')}()` with the argument '{match.group('arg')}', but "
                            f"{found[0]} line {found[1]} defines `{found[2]}` - it has no '{match.group('arg')}'.")
            if hint is None:
                match = _MISSING_ARGS_RE.match(message) or _TOO_MANY_RE.match(message)
                if match:
                    found = _resolve_callable(defs, match.group("qual"))
                    if found:
                        hint = (f"{place} calls `{match.group('qual')}()` in a way it does not take ({message.strip()}): "
                                f"{found[0]} line {found[1]} defines `{found[2]}`.")
            if hint is None:
                match = _NO_ATTRIBUTE_RE.match(message)
                if match:
                    classes = defs["classes"].get(match.group("cls"), [])
                    if len(classes) == 1:
                        cls = classes[0]
                        hint = (f"{place} uses `.{match.group('attr')}` of a {match.group('cls')} object, but class "
                                f"{match.group('cls')} ({cls['rel']} line {cls['line']}) has no '{match.group('attr')}'. "
                                f"It has: {_member_names(cls)}.")
            if hint is None:
                match = _MODULE_NO_ATTRIBUTE_RE.match(message) or _CANNOT_IMPORT_RE.match(message)
                if match:
                    wanted = match.groupdict().get("attr") or match.groupdict().get("name")
                    module = defs["modules"].get(match.group("mod")) or defs["modules"].get(match.group("mod").split(".")[-1])
                    if module:
                        names = [n for n in module[1] if not n.startswith("_")]
                        shown = ", ".join(names[:16]) + (" ..." if len(names) > 16 else "")
                        hint = (f"{place} needs '{wanted}' from '{match.group('mod')}', but {module[0]} defines no "
                                f"'{wanted}'. It defines: {shown or 'nothing public'}.")
            if hint is None:
                match = _NAME_ERROR_RE.match(message)
                if match:
                    name = match.group("name")
                    sources = []
                    for module_name, (rel, names) in defs["modules"].items():
                        if name in names and "." not in module_name and (name in defs["functions"] or name in defs["classes"]):
                            sources.append(f"from {module_name} import {name}")
                            break
                    stdlib = None if sources else _stdlib_source(name)
                    if stdlib:
                        sources.append(f"from {stdlib} import {name}")
                    if in_test:
                        hint = (f"{place} uses `{name}`, but the test file never imports or defines it"
                                + (f" - add `{sources[0]}` at the top" if sources else "") + ".")
                    elif location:
                        hint = (f"{place} uses `{name}`, which that file never imports or defines"
                                + (f" - add `{sources[0]}`" if sources else "") + ".")
            if hint:
                hints.append(hint + verdict(in_test, location, kind))
    except Exception as err:
        logger.debug(f"api_mismatch_hints failed: {err}")
    return hints


# --- Round 3u: errors that say the TEST calls code the project does not have ------------------------------------------------
# 2026-10-26 19:21 (WiFi_Snake_Multiplayer) "Kick-off Task 7: Write pytest tests ..." was marked finished with 74 failing tests:
# they called Game.update(), Game.pause(), Game.check_collision() and Server.test_client(), which do not exist, and built
# Player / ScoreEntry / LeaderboardEntry without arguments their constructors require. The next task ("Run pytest tests and
# deploy ...") then spent 2 hours changing src/game_logic.py to fit those tests.
_NOT_PROJECT_TYPES = frozenset({"NoneType", "dict", "list", "str", "int", "float", "tuple", "set", "frozenset", "bool",
                                "bytes", "function", "method", "module", "type", "object", "builtin_function_or_method"})


def calls_missing_code(root: Optional[Path], output: Optional[str]) -> List[Tuple[str, str, Tuple[str, int]]]:
    """
    Round 3u: the errors of a pytest output that were raised IN a test file and say that the test calls code that does not
    exist: an unexpected / missing / extra argument, an attribute an object (not None, a dict, a list ...) does not have, a
    name the project's module does not define, or a name the test never imports. (kind, message, (file, line)) in order.
    Errors raised inside the project's code, assertion failures and missing packages are not included. Never raises.
    """
    found: List[Tuple[str, str, Tuple[str, int]]] = []
    try:
        if not output:
            return found
        from src.tools.project_links_check import is_test_file
        defs = None
        for kind, message, location in failure_locations(output):
            if not location or not is_test_file(location[0].replace("\\", "/")):
                continue
            wrong = False
            if kind == "TypeError":
                wrong = bool(_UNEXPECTED_KW_RE.match(message) or _MISSING_ARGS_RE.match(message)
                             or _TOO_MANY_RE.match(message))
            elif kind == "AttributeError":
                match = _NO_ATTRIBUTE_RE.match(message)
                if match:
                    wrong = match.group("cls") not in _NOT_PROJECT_TYPES
                else:
                    match = _MODULE_NO_ATTRIBUTE_RE.match(message)
                    if match and root:
                        defs = defs if defs is not None else project_definitions(Path(root))
                        module = match.group("mod")
                        wrong = module in defs["modules"] or module.split(".")[-1] in defs["modules"]
            elif kind == "ImportError":
                match = _CANNOT_IMPORT_RE.match(message)
                if match and root:
                    defs = defs if defs is not None else project_definitions(Path(root))
                    module = match.group("mod")
                    wrong = module in defs["modules"] or module.split(".")[-1] in defs["modules"]
            elif kind == "NameError":
                wrong = bool(_NAME_ERROR_RE.match(message))
            if wrong:
                found.append((kind, message, location))
    except Exception as err:
        logger.debug(f"calls_missing_code failed: {err}")
    return found


# --- Round 3u: where a test run that did not end was stuck (pytest's faulthandler dump) ---------------------------------------
# the progress dots of `pytest -q` may stand before it on the same line ('.Timeout (0:00:15)!')
_FAULTHANDLER_TIMEOUT_RE = re.compile(r"Timeout \((?P<after>\d+:\d\d:\d\d(?:\.\d+)?)\)!")
_FAULTHANDLER_FRAME_RE = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+) in (?P<func>\S+)')
_NOT_PROJECT_DIRS = frozenset({"site-packages", "dist-packages", "node_modules", ".venv", "venv", "lib", "_pytest", "pluggy"})


def _project_frame(root: Path, raw_path: str) -> Optional[str]:
    """The workspace-relative path of a frame's file if it is a project file, else None."""
    if not raw_path or raw_path.startswith("<"):
        return None  # '<frozen runpy>', '<string>'
    try:
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        rel = path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if set(rel.parts[:-1]) & _NOT_PROJECT_DIRS or not (root / rel).is_file():
        return None
    return rel.as_posix()


def _source_line(root: Path, rel: str, line: int) -> str:
    try:
        lines = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[line - 1].strip() if 1 <= line <= len(lines) else ""
    except OSError:
        return ""


def hung_test_note(root: Optional[Path], output: Optional[str]) -> Optional[str]:
    """
    Round 3u: for the output of a pytest run that did not end, where the test was stuck - from the stack pytest's
    faulthandler prints for a test that runs too long (src/tools/sandbox_runner.with_pytest_hang_report): the test and the
    project code line it was running. None when the output has no such stack of a project file. Never raises.
    """
    try:
        text = str(output or "")
        if not root or not text:
            return None
        root = Path(root)
        for timeout_match in _FAULTHANDLER_TIMEOUT_RE.finditer(text):
            frames: List[Tuple[str, int, str]] = []
            for raw in text[timeout_match.end():].splitlines()[:200]:
                if raw.startswith("Thread ") or raw.startswith("Current thread") or _FAULTHANDLER_TIMEOUT_RE.search(raw):
                    if frames:
                        break  # the first thread with project frames is the one that ran the test
                    continue
                frame = _FAULTHANDLER_FRAME_RE.match(raw)
                if not frame:
                    if frames and raw.strip() and not raw.startswith(" "):
                        break
                    continue
                rel = _project_frame(root, frame.group("path"))
                if rel:
                    frames.append((rel, int(frame.group("line")), frame.group("func")))
            if not frames:
                continue
            from src.tools.project_links_check import is_test_file
            stuck_rel, stuck_line, stuck_func = frames[0]  # most recent call first
            test = next((f for f in frames if is_test_file(f[0]) and f[2].startswith("test")), None)
            code = _source_line(root, stuck_rel, stuck_line)
            where = (f"{stuck_func}() at {stuck_rel} line {stuck_line}" + (f": `{code}`" if code else ""))
            if test and test[:2] != (stuck_rel, stuck_line):
                head = f"The test `{test[2]}` ({test[0]} line {test[1]}) did not end: after {timeout_match.group('after')} it was still running {where}."
            elif test:
                head = f"The test `{test[2]}` did not end: after {timeout_match.group('after')} it was still at {where}."
            else:
                head = f"The test run did not end: after {timeout_match.group('after')} it was still running {where}."
            end = frames.index(test) if test in frames else len(frames)
            chain = [f"{rel} line {line} in {func}()" for rel, line, func in frames[1:end][:3]]  # between the two
            return ("[Framework check - where the test run was stuck:]\n" + head
                    + (f"\nCalled from: {'; '.join(chain)}." if chain else "")
                    + "\nFix THAT code path: a loop that never ends (a game loop, `while True`, a wait for a flag nothing sets), or "
                      "a wait for something that never comes (a server, a socket, input, a thread) - a test must call code "
                      "that returns. Running the tests again or with a longer timeout will not help.")
    except Exception as err:
        logger.debug(f"hung_test_note failed: {err}")
    return None


# --- Round 3o: a test run in which no test ran -----------------------------------------------------------------------------
# 2026-10-25 13:28:56 W1 (Kick-off Task 1: src/snake_game.py) ran `pytest -q` before any test file existed: "Test command failed
# with exit code 5". W1 then tried to write tests/test_snake.py (W2's task; refused) and ran the same command twice more.
_NO_TESTS_RAN_RE = re.compile(r"\bno tests ran\b|\bcollected 0 items\b|No tests found, exiting with code")


def no_tests_note(root: Optional[Path], output: Optional[str]) -> Optional[str]:
    """Round 3o: the note for a test run in which NO test ran (pytest exit code 5, jest 'No tests found'), else None."""
    try:
        text = str(output or "")
        if not _NO_TESTS_RAN_RE.search(text) or re.search(r"\b\d+ (?:passed|failed)\b", text):
            return None
        from src.utils.test_runs import project_test_files
        test_files = project_test_files(root) if root else []
        if test_files:
            shown = ", ".join(test_files[:3]) + (" ..." if len(test_files) > 3 else "")
            return (f"[Framework check - NO test ran although the project has test file(s) ({shown}):]\n"
                    "The test runner collected none of them. pytest collects only files named test_*.py or *_test.py, "
                    "functions named test_* and classes named Test* without __init__; jest only files that match its "
                    "testMatch (e.g. *.test.js). Check the file and function names, or run one file directly "
                    f"(`python -m pytest -q {test_files[0]}`).")
        example = ""
        try:
            from src.tools.project_links_check import is_test_file
            for path in sorted(Path(root).rglob("*.py")) if root else []:
                rel = path.relative_to(root).as_posix()
                if not (set(PurePath(rel).parts[:-1]) & _SNAPSHOT_SKIP) and not is_test_file(rel) \
                        and PurePath(rel).name != "conftest.py":
                    example = f" (e.g. `python -c \"import {rel[:-3].replace('/', '.')}\"`)"
                    break
        except Exception:
            example = ""
        return ("[Framework check - NO test ran: the project has no test file yet (this is pytest's exit code 5 / jest's "
                "'No tests found'), so this says nothing about your code.]\n"
                "If writing the tests is another task (see your task list and PROJECT_STRUCTURE.md), leave it to that "
                "task and do NOT write them yourself. Check your own code another way - import or run it"
                f"{example} - and finish your task. Do not run the test command again until a test file exists.")
    except Exception as err:
        logger.debug(f"no_tests_note failed: {err}")
        return None


# --- Round 3t: data files the test run itself wrote --------------------------------------------------------------------
# 2026-10-26 13:18-15:44 (WiFi_Snake_Game) src/leaderboard.py kept its entries in os.path.join(os.path.dirname(__file__),
# 'leaderboard.json') = src/leaderboard.json, while reset_leaderboard() - called by the tests' autouse fixture - deleted
# 'leaderboard.json' relative to the folder the tests run in, where no such file was. Every Leaderboard() read the entries of
# all earlier tests and runs ("assert 4 == 1", "assert 6 == 3"); 19 full test runs failed and the diagnosis only said that
# 4 of 5 failed tests passed alone.
_WRITTEN_DATA_SUFFIXES = frozenset({".json", ".csv", ".db", ".sqlite", ".sqlite3", ".txt", ".pkl", ".pickle", ".dat"})
_NOT_DATA_FILES = frozenset({"package.json", "package-lock.json", "tsconfig.json", "jsconfig.json", "composer.json",
                             "requirements.txt", ".eslintrc.json", "jest.config.json", "coverage-final.json"})
_WRITTEN_SKIP_DIRS = _SNAPSHOT_SKIP | frozenset({"coverage", ".nyc_output", "htmlcov", ".te_background"})
MAX_WRITTEN_DATA_FILES = 3


def data_files_written_since(root: Path, since: float, limit: int = 3000) -> List[str]:
    """Round 3t: the project's data files (json, csv, db, txt, ...) changed or created at or after `since`."""
    found: List[str] = []
    checked = 0
    try:
        for path in sorted(Path(root).rglob("*")):
            checked += 1
            if checked > limit:
                break
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if set(rel.parts[:-1]) & _WRITTEN_SKIP_DIRS or rel.parts[0].startswith(".") or not path.is_file():
                continue
            if path.suffix.lower() not in _WRITTEN_DATA_SUFFIXES or path.name in _NOT_DATA_FILES:
                continue
            if path.stat().st_mtime >= since:
                found.append(rel.as_posix())
    except OSError:
        pass
    return found


def _same_name_elsewhere(root: Path, written: str) -> List[str]:
    """Round 3t: 'file line N' of string literals that are just the written file's NAME (a path relative to the folder the
    tests run in) in the project's Python files, when that name does not exist at the workspace root."""
    import ast
    name = PurePath(written).name
    if PurePath(written).parent == PurePath(".") or (Path(root) / name).exists():
        return []
    places: List[str] = []
    for path in sorted(Path(root).rglob("*.py")):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if set(PurePath(rel).parts[:-1]) & _SNAPSHOT_SKIP:
            continue
        tree = _parse_python(path)
        for node in ast.walk(tree) if tree is not None else []:
            if isinstance(node, ast.Constant) and node.value == name:
                places.append(f"{rel} line {node.lineno}")
                break
        if len(places) >= 2:
            break
    return places


def written_data_note(root: Optional[Path], output: Optional[str], since: Optional[float]) -> Optional[str]:
    """
    Round 3t: for a FAILED test run, the note that the run wrote the project's own data files (so the tests share what is in
    them), and where the code names such a file by a bare name that points at another place; None otherwise. Never raises.
    """
    try:
        if not root or not since or not (pytest_failed_tests(output) or jest_failed_tests(output)):
            return None
        written = data_files_written_since(Path(root), since)[:MAX_WRITTEN_DATA_FILES]
        if not written:
            return None
        lines = [f"[Framework check - this test run WROTE the project's data file(s) {', '.join(written)}:]",
                 "Tests that use a real data file share what is in it: each test (and each later run) starts with the "
                 "data that earlier tests left there, so counts and contents depend on the order of the tests. Give the "
                 "tests their own temporary file instead - e.g. pass `tmp_path / '<file name>'` as the storage path, or "
                 "`monkeypatch` the path the code uses - and delete nothing in the project from a test."]
        for rel in written:
            for place in _same_name_elsewhere(Path(root), rel):
                lines.append(f"- '{PurePath(rel).name}' ({place}) is a path relative to the folder the tests run in (the "
                             f"workspace root), where no such file is - but the file the code reads and writes is {rel}. "
                             f"Deleting or opening '{PurePath(rel).name}' there does not touch {rel}.")
        return "\n".join(lines)
    except Exception as err:
        logger.debug(f"written_data_note failed: {err}")
        return None


_NODE_NOT_OK_RE = re.compile(r"^(\s*)not ok \d+ - (.+?)\s*$")
_NODE_LOCATION_RE = re.compile(r"^\s*location: '(.+?):\d+:\d+'\s*$")


def node_test_failed_files(root: Any, output: Optional[str]) -> List[str]:
    """
    Round 3y: the workspace test files of the failed tests in a `node --test` (TAP) output: the `location:` of each `not ok`
    block, and a `not ok N - tests/x.js` line that names a file (the file failed as a whole). The hints of Round 3x only read
    jest's output, so a failed `node --test` run - the runner PROJECT_STRUCTURE.md recommends - never got them.
    """
    found: List[str] = []
    try:
        base = Path(root).resolve()
        lines = str(output or "").splitlines()
        for index, line in enumerate(lines):
            match = _NODE_NOT_OK_RE.match(line)
            if not match:
                continue
            candidates = [match.group(2)]
            for follow in lines[index + 1:index + 14]:
                if follow.strip() == "...":
                    break
                location = _NODE_LOCATION_RE.match(follow)
                if location:
                    candidates.append(location.group(1))
                    break
            for candidate in candidates:
                path = Path(candidate)
                try:
                    rel = (path.resolve() if path.is_absolute() else (base / candidate).resolve()).relative_to(base).as_posix()
                except (OSError, ValueError):
                    continue
                if rel.endswith((".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")) and (base / rel).is_file() \
                        and rel not in found:
                    found.append(rel)
    except Exception as err:
        logger.debug(f"node_test_failed_files failed: {err}")
    return found


def js_test_mistake_hints(root: Path, output: str) -> List[str]:
    """
    Round 3x: mistakes in the failing JavaScript test files themselves (src/utils/js_test_checks.py): an exported variable
    the test REPLACES while the module keeps using its own, objects compared with === / !==, names the test never defines.
    Round 3y: variables INSIDE a loaded module that the test sets or reads by name, and names it takes that the module does
    not export (src/utils/code_map.py).
    """
    from src.utils.js_test_checks import object_identity_hints, reassigned_export_hints, undefined_name_hints
    files = [rel for rel in _failing_test_files(output) if not rel.endswith(".py")]
    files += [rel for rel in node_test_failed_files(root, output) if rel not in files]  # Round 3y: node --test output
    if not files:
        return []
    hints = (reassigned_export_hints(root, files) + object_identity_hints(root, output, files)
             + undefined_name_hints(root, output, files))
    # Round 3y: the module's INNER variables used by the test, and names the module does not export (src/utils/code_map.py)
    from src.utils.code_map import missing_names_hint, module_internal_hint
    for rel in files[:4]:
        for hint in (missing_names_hint(root, rel), module_internal_hint(root, rel)):
            if hint and hint not in hints:
                hints.append(hint)
    return hints


async def diagnose_failed_test_run(workspace: Any, command: str, output: str, run_started_at: Optional[float] = None) -> str:
    """
    The framework's notes for a FAILED test run ('' if none): failed tests that pass alone, the same tests failing again
    and again (Round 3g+), and known traps in the output.
    Round 3o: + calls that do not match the project's code (api_mismatch_hints) and runs in which no test ran.
    Round 3r: + objects a module creates at import that all tests share (shared_module_state_hints).
    Round 3t: + the project's data files the run itself wrote (written_data_note; run_started_at = when the run began).
    Cached per (workspace, workspace fingerprint, failing tests). Never raises.
    """
    try:
        if not workspace:
            return ""
        root = Path(workspace)
        # Round 3t: before the run-alone check below writes the data files again
        written_note = written_data_note(root, output, run_started_at)
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
        if written_note:
            parts.append(written_note)  # Round 3t
        no_tests = no_tests_note(root, output)  # Round 3o
        if no_tests:
            parts.append(no_tests)
        api_hints = api_mismatch_hints(root, output)  # Round 3o
        if api_hints:
            parts.append("[Framework check - calls that do not match the project's code:]\n"
                         + "\n".join(f"- {hint}" for hint in api_hints))
        state_hints = shared_module_state_hints(root, output)  # Round 3r
        if state_hints:
            parts.append("[Framework check - state the tests share:]\n" + "\n".join(f"- {hint}" for hint in state_hints))
        js_hints = js_test_mistake_hints(root, output)  # Round 3x
        if js_hints:
            parts.append("[Framework check - mistakes in the test file itself:]\n"
                         + "\n".join(f"- {hint}" for hint in js_hints))
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
