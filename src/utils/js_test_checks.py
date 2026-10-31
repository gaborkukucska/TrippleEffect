# START OF FILE src/utils/js_test_checks.py
"""
Round 3x: what is wrong with a JavaScript TEST FILE itself, found by reading it (no run needed).

2026-10-27 09:24-10:04 (WiFi_Snake_Game, qwen3.5:4b; plan: "Testing: node --test for JavaScript tests", no package.json):
* tests/game.test.js used describe() / test() / beforeEach() without taking them from anywhere, so it did not even load
  ("ReferenceError: describe is not defined"). The framework's hint said "run it with npm test or npx jest" - there was no
  package.json - and two workers spent 35 minutes on npm (exit 254), npx jest, a jest.config.js and a package.json. The task
  "Write automated tests ..." had been marked finished at 09:27 with that file. The fix is one line:
  `const { describe, test, beforeEach } = require('node:test');`.
* Once jest ran it, 26 of 35 tests failed for two mistakes in the TEST file, and the framework said nothing:
  - resetGameState() did `game.gameState = { ... }`: it replaces the module's exported property, but src/game.js keeps
    using its own `let gameState` - the module's functions never see the test's object;
  - assertEqual() compared with `!==`, so two objects with the same content were never equal
    ("expected [object Object], got [object Object]").

missing_runner_globals() / runner_globals_advice(): the test-runner functions a file uses that nothing provides, and how to
take them from Node's built-in runner. reassigned_export_hints() and object_identity_hints(): the two test mistakes above,
named with their lines. All functions never raise.
"""
import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Round 4g: + before / after (node:test's names; 2026-10-30 01:26 tests/player.test.js called `after(() => {...})` without
# taking it from node:test, and the hint said "Define it in the test file (e.g. the helper it means)")
RUNNER_FUNCTIONS = ("describe", "it", "test", "beforeEach", "afterEach", "beforeAll", "afterAll", "before", "after")
# Round 4g: node:test has no beforeAll / afterAll - its names for them are before / after
NODE_TEST_NAMES = {"beforeAll": "before", "afterAll": "after"}
_GLOBAL_RUNNERS = ("jest", "vitest", "jasmine", "mocha")  # a test script with these makes describe/it/test globals
_JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_PROVIDER_RE = re.compile(
    r"""(?:require\(\s*['"]|from\s+['"])(?:node:test|@jest/globals|mocha|vitest|jasmine|chai|node:assert|assert)['"]""")
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip_comments(source: str) -> str:
    """Block comments and // comments at the start of a line (line numbers are kept)."""
    source = re.sub(r"/\*.*?\*/", lambda m: " " + "\n" * m.group(0).count("\n"), source, flags=re.DOTALL)
    return "\n".join("" if line.lstrip().startswith("//") else line for line in source.splitlines())


def package_test_runner(root: Any) -> Optional[str]:
    """The runner of package.json's "test" script: 'jest', 'vitest', 'jasmine', 'mocha', 'node --test' or 'other'; None without one."""
    try:
        package = json.loads(_read(Path(root) / "package.json") or "null")
        script = ((package or {}).get("scripts") or {}).get("test") if isinstance(package, dict) else None
    except (ValueError, AttributeError):
        return None
    if not isinstance(script, str) or not script.strip() or "no test specified" in script:
        return None
    for runner in _GLOBAL_RUNNERS:
        if re.search(rf"\b{runner}\b", script):
            return runner
    if re.search(r"\bnode\b[^&|;]*--test\b", script):
        return "node --test"
    return "other"


def _defined_locally(source: str, name: str) -> bool:
    return bool(re.search(rf"\b(?:function\s+{name}\s*\(|(?:const|let|var)\s+{name}\s*=)", source)) or bool(
        re.search(rf"(?:const|let|var)\s*\{{[^}}]*\b{name}\b[^}}]*\}}\s*=", source))


def runner_functions_used(source: str) -> List[str]:
    """The test-runner functions a test file calls (describe/it/test/before*/after*), plus 'expect' when it is called."""
    clean = _strip_comments(source)
    used = [name for name in RUNNER_FUNCTIONS if re.search(rf"^\s*{name}(?:\.\w+)?\s*\(", clean, re.MULTILINE)]
    if re.search(r"(?<![\w.$])expect\s*\(", clean):
        used.append("expect")
    return used


def missing_runner_globals(root: Any, rel: str) -> List[str]:
    """
    The runner functions `rel` uses that NOTHING provides: not taken from node:test / jest / mocha / ... in the file, not
    defined in it, and no package.json "test" script with a runner that makes them globals. [] for other files.
    """
    try:
        if not str(rel).endswith(_JS_SUFFIXES):
            return []
        source = _read(Path(root) / rel)
        if not source:
            return []
        used = runner_functions_used(source)
        if not used:
            return []
        runner = package_test_runner(root)
        if runner in ("jest", "vitest", "jasmine"):
            return []
        provided_by_import = bool(_PROVIDER_RE.search(source))
        missing = []
        for name in used:
            if _defined_locally(source, name):
                continue
            if name == "expect":
                if runner != "mocha" and not re.search(r"""['"](?:@jest/globals|vitest|chai|expect)['"]""", source):
                    missing.append(name)
                continue
            if runner == "mocha" or (provided_by_import and re.search(rf"\b{name}\b", _imported_names(source))):
                continue
            missing.append(name)
        return missing
    except Exception as err:
        logger.debug(f"missing_runner_globals failed: {err}")
        return []


def _imported_names(source: str) -> str:
    """The names in `{ ... }` of require()/import statements of test-runner modules (as one text)."""
    names = re.findall(r"""\{([^}]*)\}\s*=\s*require\(\s*['"](?:node:test|@jest/globals|mocha|vitest)['"]""", source)
    names += re.findall(r"""import\s*\{([^}]*)\}\s*from\s*['"](?:node:test|@jest/globals|mocha|vitest)['"]""", source)
    return " ".join(names)


def planned_js_runner(root: Any) -> Optional[str]:
    """The JavaScript test runner PROJECT_STRUCTURE.md names: 'node --test', 'jest', 'mocha', 'vitest' or None."""
    text = _read(Path(root) / "PROJECT_STRUCTURE.md")
    if re.search(r"node\s+--test|node:test", text):
        return "node --test"
    for runner in ("jest", "vitest", "mocha"):
        if re.search(rf"\b{runner}\b", text, re.IGNORECASE):
            return runner
    return None


# --- Round 4l: no other test runner where the plan says node --test ----------------------------------------------------------
# 2026-10-31 02:38-02:39 (WiFi_Snake_Multiplayer_Game; PROJECT_STRUCTURE.md: "Testing framework: node --test for all tests. No
# additional frameworks or libraries") the framework told W3 to take describe/it from node:test and not to install jest;
# W3 was about to, but PM1 told it to "configure Jest". W3 wrote package.json and jest.config.js, ran `npm install --save-dev
# jest` and later `npm install --save-dev jest-environment-jsdom` (jest 29 with jsdom 30) and spent 1 h 45 min on jest runs.
OTHER_JS_RUNNERS = ("jest", "mocha", "vitest", "jasmine", "ava", "tap")
_RUNNER_PACKAGE_RE = re.compile(
    r"^(?:@(?:jest|vitest|mocha)/[\w.-]+|(?:" + "|".join(OTHER_JS_RUNNERS) + r")(?:-[\w.-]+)?|"
    r"(?:babel|ts)-jest|@types/(?:jest|mocha))$", re.IGNORECASE)
_INSTALL_RE = re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:install|i|add|ci)\b([^;&|\n]*)")
_NPX_RE = re.compile(r"\bnpx\s+(?:(?:--yes|-y|-p\s+\S+)\s+)*(@?[\w./-]+)")


def _package_base(token: str) -> str:
    token = token.strip().strip("'\"")
    if token.startswith("@"):
        return "@" + token[1:].split("@", 1)[0]
    return token.split("@", 1)[0]


def _declared_packages(root: Path) -> set:
    try:
        data = json.loads(_read(root / "package.json") or "{}")
    except ValueError:
        return set()
    names: set = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        if isinstance(data.get(key), dict):
            names.update(str(name).lower() for name in data[key])
    return names


def unplanned_runner_install_message(root: Any, command: Any) -> Optional[str]:
    """
    Round 4l: the refusal for a command that installs (npm/pnpm/yarn install/add) or runs through npx a JavaScript test
    runner other than the one PROJECT_STRUCTURE.md plans, when it plans Node's built-in runner (`node --test`); None
    otherwise. A runner the project's package.json already lists is allowed (the project uses it). Never raises.
    """
    try:
        root = Path(root)
        text = str(command or "")
        if not re.search(r"\b(?:npm|pnpm|yarn|npx)\b", text) or planned_js_runner(root) != "node --test":
            return None
        wanted: List[str] = []
        for match in _INSTALL_RE.finditer(text):
            for token in match.group(1).split():
                if token.startswith("-"):
                    continue
                base = _package_base(token)
                if _RUNNER_PACKAGE_RE.match(base) and base not in wanted:
                    wanted.append(base)
        for match in _NPX_RE.finditer(text):
            base = _package_base(match.group(1))
            if _RUNNER_PACKAGE_RE.match(base) and base not in wanted:
                wanted.append(base)
        declared = _declared_packages(root)
        wanted = [name for name in wanted if name.lower() not in declared]
        if not wanted:
            return None
        return (f"NOTHING was installed or run: PROJECT_STRUCTURE.md plans Node's BUILT-IN test runner (`node --test`) for "
                f"this project, which needs no install, and {', '.join(wanted)} is another test framework. Write the tests "
                "with `const { describe, it, beforeEach } = require('node:test');` and `const assert = require('node:assert');` "
                f"and run them with `{node_test_command(root)}`. The approved plan decides the test runner: if your PM asked "
                "for jest (or another runner), tell your PM this instead of installing it.")
    except Exception as err:
        logger.debug(f"unplanned_runner_install_message failed: {err}")
        return None


def runner_globals_advice(root: Any, rel: str, missing: Iterable[str]) -> str:
    """How to fix a test file whose runner functions nothing provides (Node's built-in runner, no install)."""
    missing = list(missing)
    planned = planned_js_runner(root)
    if planned in ("jest", "vitest", "mocha"):
        return (f"{rel} calls {', '.join(f'`{n}`' for n in missing)} but nothing provides them: PROJECT_STRUCTURE.md runs "
                f"the JavaScript tests with {planned}, and the project has no package.json \"test\" script for it. Create "
                f"package.json with `\"scripts\": {{\"test\": \"{planned}\"}}`, run `npm install --save-dev {planned}`, then "
                "run the tests with `npm test`.")
    names = [n for n in missing if n != "expect"]
    functions = list(dict.fromkeys(NODE_TEST_NAMES.get(n, n) for n in names)) or ["describe", "test"]
    text = (f"{rel} calls {', '.join(f'`{n}`' for n in list(missing))} but nothing provides "
            f"{'it' if len(list(missing)) == 1 else 'them'}: the project has no package.json \"test\" script with jest or "
            "mocha, so the file does not even load (\"ReferenceError: ... is not defined\"). Take them from Node's built-in "
            f"test runner, which needs no install: add `const {{ {', '.join(functions)} }} = require('node:test');`")
    if "expect" in list(missing):
        text += (" and `const assert = require('node:assert');` at the top of the file. Node has no `expect`: write "
                 "`assert.strictEqual(actual, expected)` for toBe and `assert.deepStrictEqual(actual, expected)` for toEqual")
    else:
        text += " at the top of the file"
    renamed = [n for n in names if n in NODE_TEST_NAMES]
    if renamed:  # Round 4g
        text += (". node:test has no " + " / ".join(f"`{n}`" for n in renamed) + ": write "
                 + " / ".join(f"`{NODE_TEST_NAMES[n]}`" for n in renamed) + " instead")
    return text + (f". Then run the tests with `{node_test_command(root)}` - do not install jest or create a package.json "
                   "for this.")


# --- a test that REPLACES an exported value --------------------------------------------------------------------------------
_REQUIRE_RE = re.compile(r"""(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*['"](\.{1,2}/[^'"]+)['"]\s*\)""")
_IMPORT_RE = re.compile(r"""import\s+(?:\*\s+as\s+)?([A-Za-z_$][\w$]*)\s+from\s+['"](\.{1,2}/[^'"]+)['"]""")


def _module_file(root: Path, test_rel: str, spec: str) -> Optional[Path]:
    base = (root / PurePosixPath(test_rel).parent / spec)
    for candidate in (base, *(Path(str(base) + s) for s in _JS_SUFFIXES), base / "index.js"):
        try:
            if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()):
                return candidate
        except OSError:
            continue
    return None


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _exported_names(module_source: str) -> List[str]:
    names: List[str] = []
    block = re.search(r"module\.exports\s*=\s*\{(.*?)\}\s*;?", module_source, re.DOTALL)
    if block:
        for item in re.split(r",", _strip_comments(block.group(1))):
            item = item.strip()
            match = re.fullmatch(r"([A-Za-z_$][\w$]*)(?:\s*:\s*([A-Za-z_$][\w$]*))?", item)
            if match and (match.group(2) is None or match.group(2) == match.group(1)):
                names.append(match.group(1))
    names += re.findall(r"(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=\s*\1\b", module_source)
    return names


def reassigned_export_hints(root: Any, test_files: Iterable[str], limit: int = 2) -> List[str]:
    """
    For JS test files that assign `<module alias>.<name> = ...` where <name> is a variable the module declares at its top
    level, exports and uses itself: the hint that the module keeps using its own variable.
    """
    hints: List[str] = []
    try:
        root = Path(root)
        for test_rel in list(test_files)[:5]:
            if not str(test_rel).endswith(_JS_SUFFIXES):
                continue
            test_source = _strip_comments(_read(root / test_rel))
            for match in list(_REQUIRE_RE.finditer(test_source)) + list(_IMPORT_RE.finditer(test_source)):
                alias, spec = match.group(1), match.group(2)
                module_path = _module_file(root, test_rel, spec)
                if module_path is None:
                    continue
                module_source = _strip_comments(_read(module_path))
                exported = set(_exported_names(module_source))
                seen = set()
                for assign in re.finditer(rf"(?<![\w$.]){re.escape(alias)}\.([A-Za-z_$][\w$]*)\s*=(?!=)", test_source):
                    name = assign.group(1)
                    if name in seen or name not in exported:
                        continue
                    declared = re.search(rf"^(?:let|var|const)\s+{re.escape(name)}\s*=", module_source, re.MULTILINE)
                    if not declared or len(re.findall(rf"(?<![\w$.]){re.escape(name)}\b", module_source)) < 3:
                        continue  # not a variable of the module that its own code uses
                    seen.add(name)
                    module_rel = module_path.resolve().relative_to(root.resolve()).as_posix()
                    hints.append(
                        f"{test_rel} line {_line_of(test_source, assign.start())} REPLACES `{alias}.{name}` "
                        f"(`{alias}.{name} = ...`), but {module_rel} keeps using its OWN `{name}` (line "
                        f"{_line_of(module_source, declared.start())}): module.exports only handed out a reference when the "
                        f"file was loaded, so the module's functions never see the test's new value and work on the old "
                        f"one. Change the existing object instead of replacing it (`Object.assign({alias}.{name}, {{ ... }})` "
                        f"or set its properties one by one), or give {module_rel} a reset function the tests call in "
                        "beforeEach. The code under test is not wrong for this.")
                    if len(hints) >= limit:
                        return hints
    except Exception as err:
        logger.debug(f"reassigned_export_hints failed: {err}")
    return hints


# --- Round 4l: a module's state the test cannot reach -----------------------------------------------------------------------
# 2026-10-31 (WiFi_Snake_Multiplayer_Game) 11 of 15 failing tests in tests/game.test.js, and the framework said nothing:
# * src/game.js: `let isGameOver = false;` ... `isGameOver = true;` in gameOver() ... `module.exports = { ..., isGameOver }`.
#   The export is a COPY made when the file was loaded: `assert.strictEqual(isGameOver, true)` after gameOver() read false.
# * tests/game.test.js kept `let testNextDirection = 'UP'` (and testSnake, testScore ...) and set `testNextDirection = 'DOWN'`
#   before moveSnake(): the module moves by ITS `nextDirection`, so the snake went up and `testNextDirection` stayed the
#   test's own value.
_DESTRUCTURED_REQUIRE_RE = re.compile(
    r"""(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['"](\.{1,2}/[^'"]+)['"]\s*\)""")
_TOP_LET_RE = re.compile(r"^(?:let|var)\s+([A-Za-z_$][\w$]*)\s*=", re.MULTILINE)
_STANDIN_RE = re.compile(r"^(?:let|var)\s+((?:test|mock|fake|stub|current)([A-Z][\w$]*))\s*=", re.MULTILINE)
_FUNCTION_HEAD_RE = re.compile(
    r"function\s+([A-Za-z_$][\w$]*)|([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")


def _exported_functions(module_source: str) -> List[str]:
    """The keys of module.exports that are functions: `name` of a function declaration, `name: () => ...`,
    `name: function ...` (not a top-level let/var exported by value)."""
    block = re.search(r"module\.exports\s*=\s*\{(.*?)\}\s*;?\s*$", module_source, re.DOTALL | re.MULTILINE)
    keys: List[str] = []
    if block:
        for item in re.split(r",\s*(?=[A-Za-z_$][\w$]*\s*(?::|,|$))", block.group(1).strip()):
            match = re.match(r"\s*([A-Za-z_$][\w$]*)\s*(?::\s*(.*))?$", item, re.DOTALL)
            if not match:
                continue
            name, value = match.group(1), (match.group(2) or "").strip()
            if value:
                if re.match(r"(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)", value):
                    keys.append(name)
                elif re.fullmatch(r"[A-Za-z_$][\w$]*", value) and re.search(rf"\bfunction\s+{re.escape(value)}\s*\(", module_source):
                    keys.append(name)
            elif re.search(rf"\bfunction\s+{re.escape(name)}\s*\(", module_source) or re.search(
                    rf"^(?:const|let|var)\s+{re.escape(name)}\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>)",
                    module_source, re.MULTILINE):
                keys.append(name)
    return keys


def _required_modules(root: Path, test_rel: str, test_source: str) -> List[Tuple[Optional[str], List[str], Path]]:
    """(alias or None, destructured names, module path) for each project module the test file requires or imports."""
    found: List[Tuple[Optional[str], List[str], Path]] = []
    for match in list(_REQUIRE_RE.finditer(test_source)) + list(_IMPORT_RE.finditer(test_source)):
        module_path = _module_file(root, test_rel, match.group(2))
        if module_path is not None:
            found.append((match.group(1), [], module_path))
    for match in _DESTRUCTURED_REQUIRE_RE.finditer(test_source):
        module_path = _module_file(root, test_rel, match.group(2))
        if module_path is None:
            continue
        names = []
        for item in match.group(1).split(","):
            item = item.strip()
            local = re.fullmatch(r"([A-Za-z_$][\w$]*)(?:\s*:\s*([A-Za-z_$][\w$]*))?", item)
            if local:
                names.append(local.group(1))
        found.append((None, names, module_path))
    return found


def _assertion_line(test_source: str, name: str, alias: Optional[str]) -> Optional[int]:
    """The first line of the test that checks `name` (or `alias.name`) with assert / expect, or None."""
    ref = rf"(?:{re.escape(alias)}\.)?" if alias else ""
    for match in re.finditer(rf"^.*\b(?:assert|expect)\b.*(?<![\w$]){ref}{re.escape(name)}\b.*$", test_source, re.MULTILINE):
        return _line_of(test_source, match.start())
    return None


def _enclosing_function(source: str, index: int) -> Optional[str]:
    """The name of the function the text at `index` is in: the nearest function head above it."""
    for match in reversed(list(_FUNCTION_HEAD_RE.finditer(source, 0, index))):
        return match.group(1) or match.group(2)
    return None


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def value_export_hints(root: Any, test_files: Iterable[str], limit: int = 2) -> List[str]:
    """
    Round 4l: a module that exports a top-level `let`/`var` BY VALUE (`module.exports = { isGameOver }`) and sets that
    variable again in its own functions, while a failing test reads the exported name: the export never changes.
    """
    hints: List[str] = []
    try:
        root = Path(root)
        seen = set()
        for test_rel in list(test_files)[:5]:
            if not str(test_rel).endswith(_JS_SUFFIXES):
                continue
            test_source = _strip_comments(_read(root / test_rel))
            for alias, names, module_path in _required_modules(root, test_rel, test_source):
                module_source = _strip_comments(_read(module_path))
                exported = set(_exported_names(module_source))
                for declared in _TOP_LET_RE.finditer(module_source):
                    name = declared.group(1)
                    if name not in exported or (module_path, name) in seen:
                        continue
                    used = name in names or (alias and re.search(rf"(?<![\w$.]){re.escape(alias)}\.{re.escape(name)}\b",
                                                                 test_source))
                    if not used:
                        continue
                    reassigned = next((m for m in re.finditer(rf"^[ \t]+.*?(?<![\w$.]){re.escape(name)}\s*=(?!=)",
                                                              module_source, re.MULTILINE)), None)
                    if reassigned is None:
                        continue
                    seen.add((module_path, name))
                    module_rel = _rel(root, module_path)
                    where = _enclosing_function(module_source, reassigned.start())
                    setter = f"{where}() sets" if where else "the module sets"
                    test_line = _assertion_line(test_source, name, alias)
                    hints.append(
                        f"{module_rel} exports `{name}` as a VALUE: `module.exports` copied `let {name}` (line "
                        f"{_line_of(module_source, declared.start())}) once, when the file was loaded, and {setter} the "
                        f"module's own variable later (line {_line_of(module_source, reassigned.start())}) - the exported "
                        f"`{name}` never changes, so {test_rel}" + (f" (line {test_line})" if test_line else "")
                        + f" always reads the first value. Export a function that returns the current value instead "
                        f"(`{name}: () => {name}` in module.exports, or a `get...()` function) and call it - in the tests "
                        f"and in every file that uses `{name}`. This is a mistake in {module_rel}, not in the test.")
                    if len(hints) >= limit:
                        return hints
    except Exception as err:
        logger.debug(f"value_export_hints failed: {err}")
    return hints


def standin_variable_hints(root: Any, test_files: Iterable[str], limit: int = 2) -> List[str]:
    """
    Round 4l: a test file's own top-level `let testX` / `mockX` / `fakeX` ... standing in for a module's inner `let x`,
    which the test sets: the module's functions never see it, and checking it checks the test's own value.
    """
    hints: List[str] = []
    try:
        root = Path(root)
        for test_rel in list(test_files)[:5]:
            if not str(test_rel).endswith(_JS_SUFFIXES):
                continue
            test_source = _strip_comments(_read(root / test_rel))
            modules = [(module_path, _strip_comments(_read(module_path)))
                       for _alias, _names, module_path in _required_modules(root, test_rel, test_source)]
            pairs: List[Tuple[str, int, str, str, int, int]] = []
            for standin in _STANDIN_RE.finditer(test_source):
                variable, rest = standin.group(1), standin.group(2)
                inner = rest[0].lower() + rest[1:]
                sets = [m for m in re.finditer(rf"^[ \t]+(?<![\w$.]){re.escape(variable)}\s*=(?!=)", test_source,
                                               re.MULTILINE)]
                if not sets:
                    continue
                for module_path, module_source in modules:
                    declared = re.search(rf"^(?:let|var)\s+{re.escape(inner)}\s*=", module_source, re.MULTILINE)
                    if declared:
                        pairs.append((variable, _line_of(test_source, standin.start()), inner, _rel(root, module_path),
                                      _line_of(module_source, declared.start()), _line_of(test_source, sets[0].start())))
                        break
            if not pairs:
                continue
            variables = ", ".join(f"`{p[0]}`" for p in pairs[:4])
            first = pairs[0]
            module_rel = first[3]
            module_source = next(src for path, src in modules if _rel(root, path) == module_rel)
            functions = _exported_functions(module_source)
            setters = [n for n in functions if re.match(r"(?:set|change|update|init|reset|start|move)(?:[A-Z_]|$)", n)][:3]
            getters = [n for n in functions if re.match(r"(?:get|is|has)[A-Z_]", n)][:3]
            how = ""
            if setters or getters:
                how = (" Set the state through the module's own functions"
                       + (f" ({', '.join(f'{n}()' for n in setters)})" if setters else "")
                       + " and read it through what it exports"
                       + (f" ({', '.join(f'{n}()' for n in getters)})" if getters else "") + ".")
            if len(pairs) == 1:
                own = (f"{test_rel} keeps its OWN variable `{first[0]}` (line {first[1]}) as if it were {module_rel}'s "
                       f"inner variable `{first[2]}` (line {first[4]})")
            else:
                own = (f"{test_rel} keeps its OWN variables {variables} (e.g. `{first[0]}`, line {first[1]}) as if they "
                       f"were {module_rel}'s inner variables (`{first[2]}`, line {first[4]})")
            hints.append(
                f"{own}: the module's functions only use THEIR variables, so setting `{first[0]}` (line {first[5]}) "
                f"changes nothing for them, and an assertion on `{first[0]}` only checks the test's own value.{how} The "
                "code under test is not wrong for this.")
            if len(hints) >= limit:
                return hints
    except Exception as err:
        logger.debug(f"standin_variable_hints failed: {err}")
    return hints


# --- objects compared with === / !== ----------------------------------------------------------------------------------------
_OBJECT_PAIR_RE = re.compile(r"\[object Object\][^\n]{0,80}\[object Object\]")
_IDENTITY_HELPER_RE = re.compile(
    r"function\s+([A-Za-z_$][\w$]*)\s*\(\s*([A-Za-z_$][\w$]*)\s*,\s*([A-Za-z_$][\w$]*)[^)]*\)\s*\{[^{}]{0,200}?"
    r"\b(?:\2\s*!==?\s*\3|\3\s*!==?\s*\2)\b")
_DEEP_HELPER_RE = re.compile(
    r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{[^{}]{0,300}?(?:JSON\.stringify|deepStrictEqual|deepEqual)")


def object_identity_hints(root: Any, output: Optional[str], test_files: Iterable[str]) -> List[str]:
    """The hint for failures that print `[object Object]` for both values: objects compared with === / !==."""
    try:
        if not _OBJECT_PAIR_RE.search(str(output or "")):
            return []
        root = Path(root)
        for test_rel in list(test_files)[:5]:
            source = _strip_comments(_read(root / test_rel))
            helper = _IDENTITY_HELPER_RE.search(source)
            if not helper:
                continue
            deep = _DEEP_HELPER_RE.search(source)
            instead = (f"use `{deep.group(1)}` (line {_line_of(source, deep.start())}) for objects and arrays"
                       if deep else "compare objects and arrays by content, e.g. `assert.deepStrictEqual(actual, expected)`")
            return [f"`expected [object Object], got [object Object]`: {test_rel}'s `{helper.group(1)}` (line "
                    f"{_line_of(source, helper.start())}) compares with `!==`, which is only true for the very SAME object - "
                    f"two objects with the same content are never equal that way. In the tests that compare objects or "
                    f"arrays, {instead}. The code under test is not wrong for this."]
        return ["`expected [object Object], got [object Object]`: the test compares two OBJECTS with === / !== (or a helper "
                "that does), which is only true for the very same object. Compare their content instead: "
                "`assert.deepStrictEqual(actual, expected)`, jest's `toEqual`, or a helper that compares "
                "JSON.stringify(actual) with JSON.stringify(expected). The code under test is not wrong for this."]
    except Exception as err:
        logger.debug(f"object_identity_hints failed: {err}")
        return []


# --- a name the test file uses but never defines --------------------------------------------------------------------------
_REFERENCE_ERROR_RE = re.compile(r"ReferenceError: ([A-Za-z_$][\w$]*) is not defined")
_FRAME_RE = re.compile(r"\(?((?:[\w.-]+/)*[\w.-]+\.(?:m?js|cjs|jsx|tsx?)):(\d+)(?::\d+)?\)?")


def undefined_name_hints(root: Any, output: Optional[str], test_files: Iterable[str], limit: int = 3) -> List[str]:
    """
    For "ReferenceError: X is not defined" raised IN a test file: the line, and - when a module the test requires exports X -
    how to reach it (`game.gameState`). Runner functions (describe, test, ...) are left to missing_runner_globals().
    """
    hints: List[str] = []
    try:
        text = str(output or "")
        root = Path(root)
        files = [f for f in test_files if str(f).endswith(_JS_SUFFIXES)]
        seen = set()
        for match in _REFERENCE_ERROR_RE.finditer(text):
            name = match.group(1)
            if name in seen or name in RUNNER_FUNCTIONS or name == "expect":
                continue
            window = text[match.end():match.end() + 1500]
            location = next(((f, int(m.group(2))) for m in _FRAME_RE.finditer(window)
                             for f in files if m.group(1).endswith(f)), None)
            if location is None:
                continue
            seen.add(name)
            test_rel, line = location
            source = _strip_comments(_read(root / test_rel))
            reach = ""
            for req in list(_REQUIRE_RE.finditer(source)) + list(_IMPORT_RE.finditer(source)):
                module_path = _module_file(root, test_rel, req.group(2))
                if module_path is not None and name in _exported_names(_strip_comments(_read(module_path))):
                    module_rel = module_path.resolve().relative_to(root.resolve()).as_posix()
                    reach = f" {module_rel} exports it: write `{req.group(1)}.{name}`."
                    break
            hints.append(f"{test_rel} line {line} uses `{name}`, which the test file never defines or imports, so that "
                         f"test stops with \"ReferenceError: {name} is not defined\" before it checks anything.{reach or ' Define it in the test file (e.g. the helper it means) or take it from the module the test requires.'}"
                         " The code under test is not wrong for this.")
            if len(hints) >= limit:
                break
    except Exception as err:
        logger.debug(f"undefined_name_hints failed: {err}")
    return hints


# --- Round 4g: a local variable that HIDES the module ------------------------------------------------------------------------
# 2026-10-30 tests/leaderboard.test.js loaded `const leaderboard = require('../src/leaderboard');` and five tests declared
# `const leaderboard = [ {...}, ... ];` for their data, then called `leaderboard.formatLeaderboard(leaderboard)`: inside those
# tests the name is the ARRAY, so "leaderboard.formatLeaderboard is not a function" although src/leaderboard.js exports it.
_ALIAS_REQUIRE_RE = re.compile(r"""^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*['"](\.{1,2}/[^'"]+)['"]\s*\)""",
                               re.MULTILINE)


def shadowed_module_hints(root: Any, test_rel: str, limit: int = 2) -> List[str]:
    """For a JS test file: the blocks that declare a variable with the name of a module the file loads at its top and then
    call `<name>.<function>` on that local value. Never raises."""
    hints: List[str] = []
    try:
        if not str(test_rel).endswith(_JS_SUFFIXES):
            return hints
        from src.utils.code_map import _blank_strings_and_comments, _line_depths
        root = Path(root)
        source = _read(root / test_rel)
        if not source:
            return hints
        clean = _blank_strings_and_comments(source)
        depths = _line_depths(clean)
        for match in _ALIAS_REQUIRE_RE.finditer(source):
            alias, spec = match.group(1), match.group(2)
            if depths[_line_of(source, match.start()) - 1] != 0:
                continue  # only a module loaded at the top of the file
            module_path = _module_file(root, test_rel, spec)
            if module_path is None:
                continue
            module_rel = module_path.resolve().relative_to(root.resolve()).as_posix()
            lines = []
            for local in re.finditer(rf"(?<![\w$.])(?:const|let|var)\s+{re.escape(alias)}\s*=", clean):
                line = _line_of(clean, local.start())
                if depths[line - 1] == 0:
                    continue
                # the block the local lives in: from its declaration to the '}' that closes that block
                depth, end = 0, len(clean)
                for index in range(local.end(), len(clean)):
                    if clean[index] == "{":
                        depth += 1
                    elif clean[index] == "}":
                        if depth == 0:
                            end = index
                            break
                        depth -= 1
                use = re.search(rf"(?<![\w$.]){re.escape(alias)}\.([A-Za-z_$][\w$]*)\s*\(", clean[local.end():end])
                if use:
                    lines.append((line, use.group(1)))
            if lines:
                shown = ", ".join(str(line) for line, _name in lines[:4]) + (" ..." if len(lines) > 4 else "")
                hints.append(f"{test_rel} line{'s' if len(lines) > 1 else ''} {shown} declare{'' if len(lines) > 1 else 's'} "
                             f"a local `{alias}` (`const {alias} = ...`) that HIDES the module `{alias}` (line "
                             f"{_line_of(source, match.start())}, {module_rel}): in {'those tests' if len(lines) > 1 else 'that test'} "
                             f"`{alias}.{lines[0][1]}(...)` is "
                             f"looked up on the local value, so it \"is not a function\" although {module_rel} may export it. "
                             f"Give the local value another name (e.g. `{alias}Data`). The code under test is not wrong for this.")
                if len(hints) >= limit:
                    break
    except Exception as err:
        logger.debug(f"shadowed_module_hints({test_rel}) failed: {err}")
    return hints


def js_test_files(root: Any, limit: int = 20) -> List[str]:
    """The JavaScript test files of the workspace (relative), outside node_modules and the like."""
    found: List[str] = []
    skip = {"node_modules", ".git", ".te_background", "dist", "build", "coverage"}
    try:
        from src.tools.project_links_check import is_test_file
        root = Path(root)
        for path in sorted(root.rglob("*")):
            if len(found) >= limit:
                break
            rel = path.relative_to(root)
            if set(rel.parts[:-1]) & skip or path.suffix not in _JS_SUFFIXES or not path.is_file():
                continue
            if is_test_file(rel.as_posix()):
                found.append(rel.as_posix())
    except (OSError, ValueError):
        pass
    return found

# --- Round 4f: the `node --test` command that runs THIS project's test files ------------------------------------------------
# 2026-10-29 23:14-23:46 (WiFi_Snake_Game) package.json said "test": "node --test" and the tests were tests/*.spec.js: Node's
# runner without file names looks only for *.test.js, *-test.js, *_test.js, test-*.js, test.js and test/ folders, so `npm
# test` ran 0 tests and exited 0 - two "verify all components" tasks were finished on it. And the framework's own advice
# `node --test tests/` does not work in Node 22 (it loads the folder as ONE module: "tests 1, fail 1"): the files are named.
NODE_DEFAULT_TEST_NAMES = "*.test.js, *-test.js, *_test.js, test-*.js, test.js or files in a test/ folder"
_COMPOUND_SUFFIX_RE = re.compile(r"(\.(?:test|spec)\.[cm]?[jt]sx?)$", re.IGNORECASE)


def node_test_command(root: Any = None, files: Optional[List[str]] = None) -> str:
    """
    `node --test ...` naming this project's JavaScript test files: one folder with one kind of name -> a shell pattern
    (tests/*.spec.js, tests/test_*.js); a few files -> the files; else a quoted pattern Node expands itself (Node 21+).
    Without test files: `node --test tests/*.test.js`.
    """
    try:
        if files is None:
            files = js_test_files(root, limit=200) if root is not None else []
        files = [str(f).replace("\\", "/") for f in files if str(f).strip()]
        if not files:
            return "node --test tests/*.test.js"
        folders = {str(PurePosixPath(f).parent) for f in files}
        names = [PurePosixPath(f).name for f in files]
        compound = {(_COMPOUND_SUFFIX_RE.search(n).group(1).lower() if _COMPOUND_SUFFIX_RE.search(n) else None) for n in names}
        if len(folders) == 1:
            folder = folders.pop()
            prefix = "" if folder in ("", ".") else folder + "/"
            if len(compound) == 1 and None not in compound:
                return f"node --test {prefix}*{compound.pop()}"
            suffixes = {PurePosixPath(n).suffix for n in names}
            if all(n.startswith("test_") for n in names) and len(suffixes) == 1:
                return f"node --test {prefix}test_*{suffixes.pop()}"
        if len(files) <= 6:
            return "node --test " + " ".join(files)
        top = sorted({f.split("/", 1)[0] for f in files if "/" in f})
        suffix = compound.pop() if len(compound) == 1 and None not in compound else ".js"
        return f'node --test "{top[0] if len(top) == 1 else "**"}/**/*{suffix}"'
    except Exception:
        return "node --test tests/*.test.js"

# END OF FILE src/utils/js_test_checks.py
