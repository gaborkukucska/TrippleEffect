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

RUNNER_FUNCTIONS = ("describe", "it", "test", "beforeEach", "afterEach", "beforeAll", "afterAll")
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
    functions = names or ["describe", "test"]
    text = (f"{rel} calls {', '.join(f'`{n}`' for n in list(missing))} but nothing provides "
            f"{'it' if len(list(missing)) == 1 else 'them'}: the project has no package.json \"test\" script with jest or "
            "mocha, so the file does not even load (\"ReferenceError: ... is not defined\"). Take them from Node's built-in "
            f"test runner, which needs no install: add `const {{ {', '.join(functions)} }} = require('node:test');`")
    if "expect" in list(missing):
        text += (" and `const assert = require('node:assert');` at the top of the file. Node has no `expect`: write "
                 "`assert.strictEqual(actual, expected)` for toBe and `assert.deepStrictEqual(actual, expected)` for toEqual")
    else:
        text += " at the top of the file"
    return text + ". Then run the tests with `node --test tests/` - do not install jest or create a package.json for this."


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

# END OF FILE src/utils/js_test_checks.py
