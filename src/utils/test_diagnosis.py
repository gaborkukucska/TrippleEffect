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
        hints.append(f"`{match.group(1)}` is not defined: this test file uses the globals of a test runner (jest or mocha). "
                     "Run it with the project's test command (`npm test`, or `npx jest <file>`), not with `node <file>`.")
    if workspace:
        hints.extend(inverted_check_hints(Path(workspace), text))  # Round 3m
        # Round 3q: 2026-10-25 22:53:59 "No module named 'src.frontend.players'" for src/frontend/players.js got no hint
        from src.tools.project_links_check import missing_module_language_hints
        hints.extend(missing_module_language_hints(Path(workspace), text))
    return hints


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


async def diagnose_failed_test_run(workspace: Any, command: str, output: str) -> str:
    """
    The framework's notes for a FAILED test run ('' if none): failed tests that pass alone, the same tests failing again
    and again (Round 3g+), and known traps in the output.
    Round 3o: + calls that do not match the project's code (api_mismatch_hints) and runs in which no test ran.
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
        no_tests = no_tests_note(root, output)  # Round 3o
        if no_tests:
            parts.append(no_tests)
        api_hints = api_mismatch_hints(root, output)  # Round 3o
        if api_hints:
            parts.append("[Framework check - calls that do not match the project's code:]\n"
                         + "\n".join(f"- {hint}" for hint in api_hints))
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
