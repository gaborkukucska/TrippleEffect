# START OF FILE src/tools/project_links_check.py
"""
Links between the files of a project (Round 3d).

2026-10-23 11:40-15:12 (Multiplayer_Snake_Game, qwen3.5:4b): W1 spent more than two hours on "Fix failing unit tests in
tests/test_gameLogic.js" and never finished. The test file never loaded src/game/gameLogic.js: it held its OWN copy of the
game logic (a `const gameLogic = {...}` object) and tested that copy, so "make the mocks match the implementation" could never
end. src/game/gameLogic.js exported nothing (a browser script), so a test could not have loaded it anyway. And
src/server/server.js required 'sqlite3', which package.json did not list: the integration run failed with "Cannot find module
'sqlite3'", yet "Kick-off Task 8: Run the tests ... to verify that everything works" was marked finished. None of this is a
syntax error, so the existing checks (python_static_check, source_check) saw nothing.

* test_target_problem(): a test file (JavaScript or Python) whose tests load NO project file - it tests a copy.
* export_problems(): a JavaScript file that takes a value from a project module (`const x = require('./m.js')`,
  `import {a} from './m.js'`) that exports nothing.
* missing_dependency_problems(): a JavaScript file that requires an npm package that package.json does not list.
* server_on_load_problems() (Round 3e): a test file that loads a project module which starts a server when it is loaded,
  so the test run never ends; test_hang_hint(): the text for an agent whose test command timed out (Round 3f: a busy
  process - a loop that never ends - or an idle one, measured by src/tools/sandbox_runner.py, and the last output line).
* python_import_language_problems() (Round 3q): a Python file that imports a module which exists only as a JavaScript file
  of the project ('from src.frontend.players import ...' for src/frontend/players.js); missing_module_language_hints(): the
  same for "No module named ..." in a test run's output. unloaded_browser_script_problem(): a browser script that no page
  of the project loads (a note on the write / edit only, link_notes_text).
* link_problems(workspace, path): all of these for one existing file (used by the task completion check,
  src/utils/task_files.py); link_notes_text(): the '[Framework check ...]' text for a write or an edit that WAS saved.

Every check is conservative: when it cannot judge (no package.json, no project files besides the tests, an import that may
be a third-party package) it reports nothing. A check never raises.
"""
import ast
import json
import logging
import re
import sys
import warnings
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build", "coverage",
                        ".te_background", ".mypy_cache"})
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})
MAX_PROJECT_FILES = 3000
MAX_SOURCE_BYTES = 1_000_000

_JS_TEST_NAME_RE = re.compile(r"(?:^test[_.-].+|.+[_.-](?:test|spec))\.(?:m?js|cjs|jsx|tsx?)$", re.IGNORECASE)
_PY_TEST_NAME_RE = re.compile(r"^(?:test_.+|.+_test)\.py$")
_JS_TEST_CODE_RE = re.compile(r"\b(?:test|it|describe)\s*\(|\bassert\b|\bexpect\s*\(")
_PY_TEST_CODE_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_\w*\s*\(|^\s*class\s+Test\w*", re.MULTILINE)

# require('x'), import ... from 'x', import('x'), import 'x'
_JS_SPEC_RE = re.compile(
    r"""\brequire\s*\(\s*(['"`])([^'"`\n]+)\1\s*\)"""
    r"""|\bimport\s*\(\s*(['"`])([^'"`\n]+)\3\s*\)"""
    r"""|\bimport\s+(?:[\w$*{}\s,]+?\s+from\s+)?(['"])([^'"\n]+)\5"""
    r"""|\bexport\s+[\w$*{}\s,]+?\s+from\s+(['"])([^'"\n]+)\7""")
# a require/import whose VALUE is used: const x = require(...), {a} = require(...), import x from / import {a} from
_JS_VALUE_SPEC_RE = re.compile(
    r"""(?:=\s*require\s*\(\s*(['"`])([^'"`\n]+)\1\s*\))"""
    r"""|(?:\bimport\s+[\w$*{}\s,]+?\s+from\s+(['"])([^'"\n]+)\3)""")
_JS_EXPORT_RE = re.compile(r"\bmodule\.exports\b|\bexports\.[\w$]+\s*=|\bexports\[|^\s*export\s+", re.MULTILINE)
_STRING_RE = re.compile(r"""(['"`])((?:(?!\1)[^\\\n]|\\.){2,300})\1""")

NODE_BUILTINS = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto", "dgram",
    "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net", "os",
    "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream", "string_decoder", "sys",
    "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
})
_PY_TEST_LIBRARIES = frozenset({"pytest", "unittest", "mock", "hypothesis", "_pytest", "doctest", "nose"})
_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def _language(rel_path: str) -> Optional[str]:
    lower = rel_path.lower()
    if lower.endswith(".py"):
        return "python"
    if lower.endswith(JS_SUFFIXES):
        return "javascript"
    return None


def is_test_file(rel_path: str) -> bool:
    """True for test_x.js / x.test.js / x.spec.ts / tests/x.js and test_x.py / x_test.py."""
    path = PurePosixPath(str(rel_path).replace("\\", "/"))
    language = _language(path.name)
    if language == "python":
        return bool(_PY_TEST_NAME_RE.match(path.name))
    if language == "javascript":
        return bool(_JS_TEST_NAME_RE.match(path.name)) or bool(set(p.lower() for p in path.parts[:-1]) & _TEST_DIRS)
    return False


def _strip_js_comments(source: str) -> str:
    """Block comments and whole-line // comments (enough to ignore commented-out requires; strings are kept)."""
    # line breaks of block comments are kept, so line numbers stay right (Round 3e)
    source = re.sub(r"/\*.*?\*/", lambda m: " " + "\n" * m.group(0).count("\n"), source, flags=re.DOTALL)
    return "\n".join("" if line.lstrip().startswith("//") else line for line in source.splitlines())


def _project_source_files(workspace: Path) -> Set[str]:
    """Relative paths of the project's code files (JavaScript/TypeScript/Python) that are NOT tests."""
    found: Set[str] = set()
    try:
        root = Path(workspace).resolve()
        for path in root.rglob("*"):
            if len(found) >= MAX_PROJECT_FILES:
                break
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if set(rel.parts[:-1]) & _SKIP_DIRS or rel.parts[0].startswith(".") or not path.is_file():
                continue
            rel_text = rel.as_posix()
            if _language(rel_text) and not is_test_file(rel_text) and rel.name != "conftest.py":
                found.add(rel_text)
    except OSError:
        pass
    return found


def _js_specs(source: str) -> List[str]:
    specs = []
    for match in _JS_SPEC_RE.finditer(_strip_js_comments(source)):
        spec = next((g for g in match.groups()[1::2] if g), None)
        if spec and spec not in specs:
            specs.append(spec.strip())
    return specs


def _resolve_js(workspace: Path, from_rel: str, spec: str) -> Optional[Path]:
    """The existing file a relative specifier points to, or None."""
    if not spec.startswith((".", "/")):
        return None
    root = Path(workspace).resolve()
    base = (root / from_rel).parent if not spec.startswith("/") else root
    target = (base / spec.lstrip("/")) if spec.startswith("/") else (base / spec)
    candidates = [target] + [Path(str(target) + ext) for ext in JS_SUFFIXES] + [target / f"index{ext}" for ext in (".js", ".mjs", ".cjs", ".ts")]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and resolved.is_relative_to(root):
                return resolved
        except (OSError, ValueError):
            continue
    return None


def _likely_target(rel_path: str, project_files: Iterable[str]) -> Optional[str]:
    """The project file whose name the test file's name contains (tests/test_gameLogic.js -> src/game/gameLogic.js)."""
    stem = PurePosixPath(rel_path).name.lower()
    for token in ("test_", "_test", ".test", "-test", ".spec", "_spec", "test-"):
        stem = stem.replace(token, "")
    stem = stem.rsplit(".", 1)[0]
    if len(stem) < 3:
        return None
    matches = sorted(p for p in project_files if PurePosixPath(p).stem.lower() == stem)
    return matches[0] if matches else None


def _relative_spec(from_rel: str, to_rel: str) -> str:
    from os.path import relpath
    spec = relpath(to_rel, PurePosixPath(from_rel).parent.as_posix() or ".").replace("\\", "/")
    return spec if spec.startswith(".") else f"./{spec}"


def _mentions_project_file(source: str, project_files: Set[str]) -> bool:
    """True if a string literal names a project code file (e.g. spawn('node', ['src/server.js']))."""
    names = {PurePosixPath(p).name for p in project_files}
    for match in _STRING_RE.finditer(source):
        text = match.group(2).replace("\\", "/")
        if any(text.endswith(p) for p in project_files) or PurePosixPath(text).name in names and "/" in text:
            return True
    return False


def _python_imports(source: str) -> Optional[List[str]]:
    """Top-level module names a Python source imports ('.' for a relative import), or None if it does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append("." if node.level else (node.module or "").split(".")[0])
    return names


# --- Round 3s: what a test is about, by the words of its name --------------------------------------------------------------
_GENERIC_NAME_TOKENS = frozenset({"test", "tests", "spec", "main", "index", "init", "src", "lib", "module", "file"})


def name_tokens(path: str) -> Set[str]:
    """Round 3s: the words of a file name without test markers and extension: 'tests/test_game_logic.py' -> {'game',
    'logic'}; 'src/core/snakeGame.js' -> {'snake', 'game'}. Words shorter than 3 letters and generic words are left out."""
    base = PurePosixPath(str(path).replace("\\", "/")).name.split(".")[0]
    words = re.findall(r"[a-z]+", re.sub(r"(?<=[a-z])(?=[A-Z])", "_", base).lower())
    return {word for word in words if len(word) >= 3 and word not in _GENERIC_NAME_TOKENS}


def subject_in_javascript(test_rel: str, project_files: Iterable[str]) -> Optional[str]:
    """
    Round 3s: the JavaScript file a (Python) test is about, when its name points at the project's JavaScript code and not at
    its Python code: the JavaScript file of the same name, else the one sharing most name words with the test while no
    Python file (outside the tests) shares any; with no Python code at all, the first JavaScript file. None otherwise.
    """
    files = [p for p in project_files if not set(PurePosixPath(p).parts[:-1]) & _TEST_DIRS]
    js_code = sorted(p for p in files if _language(p) == "javascript")
    py_code = [p for p in files if _language(p) == "python" and PurePosixPath(p).name not in ("__init__.py", "conftest.py")]
    if not js_code:
        return None
    same = _likely_target(test_rel, js_code)
    if same:
        return same
    if not py_code:
        return js_code[0]
    words = name_tokens(test_rel)
    if not words or any(words & name_tokens(p) for p in py_code):
        return None
    scored = sorted(((len(words & name_tokens(p)), p) for p in js_code), key=lambda item: (-item[0], item[1]))
    return scored[0][1] if scored and scored[0][0] else None


def _test_folder_helpers(workspace: Path, test_rel: str, names: List[str]) -> List[str]:
    """Round 3s: the files of the imported top-level modules when EVERY one of them lies inside a tests folder; [] if any
    import is relative, resolves outside the tests folders or cannot be resolved (a package)."""
    if not names or "." in names:
        return []
    root = Path(workspace).resolve()
    bases = [root, root / "src", (root / test_rel).parent]
    helpers: List[str] = []
    for name in dict.fromkeys(names):
        found = None
        for base in bases:
            for candidate in (base / f"{name}.py", base / name):
                try:
                    if candidate.exists() and candidate.resolve().is_relative_to(root):
                        found = candidate.resolve().relative_to(root).as_posix()
                        break
                except (OSError, ValueError):
                    continue
            if found:
                break
        if not found or not set(PurePosixPath(found).parts[:-1] or PurePosixPath(found).parts[:1]) & _TEST_DIRS:
            return []
        helpers.append(found)
    return helpers


def test_target_problem(workspace: Path, rel_path: str, source: Optional[str] = None) -> Optional[str]:
    """
    The problem of a test file whose tests load no project file (they test a copy written inside the test file), or None.
    JavaScript: no relative require/import and no string naming a project code file. Python: only the standard library and
    test libraries are imported (any other import may be the project or a package: not judged).
    """
    try:
        rel = str(rel_path).replace("\\", "/")
        if not is_test_file(rel):
            return None
        if source is None:
            source = (Path(workspace) / rel).read_text(encoding="utf-8")
        if not source or len(source) > MAX_SOURCE_BYTES:
            return None
        language = _language(rel)
        if language == "javascript" and not _JS_TEST_CODE_RE.search(source):
            return None
        if language == "python" and not _PY_TEST_CODE_RE.search(source):
            return None
        project_files = _project_source_files(workspace)
        if not project_files:
            return None  # nothing to test yet (tests written first) - cannot judge
        if language == "javascript":
            if any(spec.startswith((".", "/")) for spec in _js_specs(source)):
                return None
            if _mentions_project_file(source, project_files):
                return None
            target = _likely_target(rel, project_files)
            example = (f"`const {PurePosixPath(target).stem} = require('{_relative_spec(rel, target)}');` (and "
                       f"`module.exports = {{ ... }}` in {target})") if target else "`const m = require('../src/<file>.js');`"
            return (f"{rel} loads NO project file (no require/import of a project file), so its tests run against code "
                    "written inside the test file - a copy - and prove nothing about the project's code. Load the file "
                    f"under test, e.g. {example}, and test the real functions; delete the copied code from the test file.")
        imports = _python_imports(source)
        if imports is None:
            return None
        stdlib = set(getattr(sys, "stdlib_module_names", ())) | _PY_TEST_LIBRARIES | {"__future__"}
        others = [name for name in imports if name == "." or (name and name not in stdlib)]
        helpers = _test_folder_helpers(workspace, rel, others)
        if helpers:
            # Round 3s: 2026-10-26 09:59-11:17 tests/test_game_logic.py imported only tests/mock_game_logic.py ("Mock
            # implementations for JavaScript functions") - the game's code is src/core/snake_game.js
            js_target = subject_in_javascript(rel, project_files)
            advice = (_javascript_test_advice(workspace, js_target) if js_target else
                      "Import the code under test from the project (e.g. `from my_module import my_function`)")
            return (f"{rel} imports only modules inside the tests folder ({', '.join(helpers)}), not the project's code, so "
                    "its tests run against a copy (e.g. mock versions of the real functions) and prove nothing about the "
                    f"project. {advice}; delete the copied code.")
        if others:
            return None
        if _mentions_project_file(source, project_files):
            return None
        target = _likely_target(rel, project_files)
        example = (f"`from {PurePosixPath(target).stem} import ...`" if target else "`from my_module import my_function`")
        return (f"{rel} imports NO project module (only the standard library / test libraries), so its tests run against "
                "code written inside the test file - a copy - and prove nothing about the project's code. Import the code "
                f"under test, e.g. {example}, and delete the copied code from the test file.")
    except Exception as err:  # a check must never break a tool call
        logger.debug(f"test_target_problem({rel_path}) failed: {err}")
        return None


def export_problems(workspace: Path, rel_path: str, source: Optional[str] = None) -> List[str]:
    """JavaScript: project modules this file takes a value from (require/import) that export nothing."""
    problems: List[str] = []
    try:
        rel = str(rel_path).replace("\\", "/")
        if _language(rel) != "javascript":
            return problems
        if source is None:
            source = (Path(workspace) / rel).read_text(encoding="utf-8")
        root = Path(workspace).resolve()
        seen = set()
        for match in _JS_VALUE_SPEC_RE.finditer(_strip_js_comments(source or "")):
            spec = match.group(2) or match.group(4)
            if not spec or spec in seen:
                continue
            seen.add(spec)
            target = _resolve_js(root, rel, spec)
            if target is None or target.suffix.lower() not in (".js", ".mjs", ".cjs"):
                continue
            try:
                target_source = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not target_source.strip() or _JS_EXPORT_RE.search(_strip_js_comments(target_source)):
                continue
            shown = target.relative_to(root).as_posix()
            problems.append(
                f"{shown} exports NOTHING (no module.exports / export), so `{spec}` loaded in {rel} gives an empty object. "
                f"Add the exports to {shown} (e.g. `if (typeof module !== 'undefined') module.exports = {{ initGame, "
                "updateGame }};` for a file that also runs in the browser).")
    except Exception as err:
        logger.debug(f"export_problems({rel_path}) failed: {err}")
    return problems


def _package_name(spec: str) -> Optional[str]:
    """The npm package of a bare specifier ('socket.io/client' -> 'socket.io', '@a/b/c' -> '@a/b'), None if not one."""
    spec = spec.strip()
    if not spec or spec.startswith((".", "/", "node:", "http:", "https:", "data:", "file:", "#", "~")) or "://" in spec:
        return None
    parts = spec.split("/")
    if spec.startswith("@"):
        return "/".join(parts[:2]) if len(parts) >= 2 else None
    return parts[0]


def missing_dependency_problems(workspace: Path, rel_path: str, source: Optional[str] = None) -> List[str]:
    """JavaScript: npm packages this file requires/imports that the workspace's package.json does not list."""
    problems: List[str] = []
    try:
        rel = str(rel_path).replace("\\", "/")
        if _language(rel) != "javascript":
            return problems
        package_file = Path(workspace) / "package.json"
        if not package_file.is_file():
            # Round 4a: a Node.js file that REQUIRES an npm package while the project has no package.json and the package is
            # not installed cannot load. 2026-10-27 18:38 "Kick-off Task 4: ... src/server.js using WebSocket" was finished
            # with `require('ws')` and no package.json; every test of the server failed "Cannot find module 'ws'". (A plain
            # browser page has no require(), and an ES import may come from a browser import map, so only require() counts.)
            if source is None:
                source = (Path(workspace) / rel).read_text(encoding="utf-8")
            required = []
            for match in re.finditer(r"""\brequire\s*\(\s*(['"`])([^'"`\n]+)\1\s*\)""", _strip_js_comments(source or "")):
                name = _package_name(match.group(2))
                if not name or name.split("/")[0] in NODE_BUILTINS or name.startswith("node:") or name in required:
                    continue
                if (Path(workspace) / "node_modules" / name).exists():
                    continue
                required.append(name)
            for name in required:
                problems.append(
                    f"{rel} requires the npm package '{name}', but the project has NO package.json and '{name}' is not "
                    f"installed, so the file fails to load with \"Cannot find module '{name}'\". Create package.json in the "
                    f"workspace root with \"dependencies\": {{\"{name}\": \"*\"}} and run `npm install`, or use only "
                    "Node's built-in modules (http, fs, path, events, ...).")
            return problems
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return problems  # an invalid package.json is reported by its own syntax check
        if not isinstance(package, dict):
            return problems
        listed = set()
        for section in _DEPENDENCY_SECTIONS:
            if isinstance(package.get(section), dict):
                listed.update(package[section].keys())
        own_name = package.get("name")
        if source is None:
            source = (Path(workspace) / rel).read_text(encoding="utf-8")
        missing = []
        for spec in _js_specs(source or ""):
            name = _package_name(spec)
            if not name or name.split("/")[0] in NODE_BUILTINS or name in listed or name == own_name:
                continue
            if name not in missing:
                missing.append(name)
        for name in missing:
            problems.append(
                f"{rel} requires the package '{name}', which package.json does NOT list in its dependencies: `npm install` "
                f"does not install it and the program fails with \"Cannot find module '{name}'\". Add '{name}' to the "
                "\"dependencies\" of package.json, or use a module the project already lists.")
    except Exception as err:
        logger.debug(f"missing_dependency_problems({rel_path}) failed: {err}")
    return problems


# --- Round 3e: a module that starts a server when it is loaded ------------------------------------------------------------
# 2026-10-23 18:09-19:02 (Multiplayer_Snake_Game): tests/test_api.js did `const app = require('../src/server')`, and
# src/server.js called `server.listen(config.port, ...)` at its top level. Loading it started the server, so mocha never
# ended: 16 runs of `npm test` / `npx mocha tests/` timed out (60-120 s each), W3 and PM1 spent 50 minutes on it and
# "Kick-off Task 9: Run all tests ..." was marked finished anyway.
_TOP_LEVEL_LISTEN_RE = re.compile(r"^(?:await\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.listen\s*\(", re.MULTILINE)
_RUN_DIRECTLY_GUARDS = ("require.main === module", "require.main == module", "require.main !== module",
                        "module.parent", "import.meta.url", "import.meta.main", "process.argv[1]")
_TEST_COMMAND_RE = re.compile(
    r"\b(?:npm\s+(?:run\s+)?test|npm\s+t\b|yarn\s+test|pnpm\s+test|npx\s+(?:mocha|jest|vitest)|mocha|jest|vitest|"
    r"node\s+--test|pytest|python3?\s+-m\s+(?:pytest|unittest))\b")
# Round 3f: a test FILE run directly ('node test/game.test.js', 'python tests/test_x.py'). 2026-10-23 20:47 and 21:24 such
# runs timed out and the agent was told about interactive prompts and servers instead of why a test run does not end.
_DIRECT_TEST_RUN_RE = re.compile(
    r"(?:^|[\s;&|(])(?:node|nodejs|bun|deno\s+(?:run|test)|python3?|py|npx\s+(?:ts-node|tsx))"
    r"(?:\s+-{1,2}[\w-]+(?:=\S+)?)*\s+(['\"]?)(?P<path>[^\s'\";&|()]+)\1")
# CPU cores a timed-out process group used in its last second (sandbox_runner.SandboxResult.cpu_busy)
BUSY_CPU_RATE = 0.5
IDLE_CPU_RATE = 0.05


def starts_server_on_load(source: Optional[str]) -> Optional[str]:
    """
    The top-level 'x.listen(' call of a JavaScript source (e.g. 'server.listen(' with its line number) if the module starts
    a server as soon as it is loaded - a listen call at the start of a line, with no "run directly" guard such as
    `if (require.main === module)` anywhere in the file - else None.
    """
    if not source:
        return None
    stripped = _strip_js_comments(source)
    for match in _TOP_LEVEL_LISTEN_RE.finditer(stripped):
        if _inside_run_directly_guard(stripped, match.start()):
            continue
        line = stripped.count("\n", 0, match.start()) + 1
        return f"`{match.group(1)}.listen(` at line {line}"
    return None


def _inside_run_directly_guard(source: str, position: int) -> bool:
    """
    Round 3f: True if the listen call at `position` is inside a "run directly" guard (`if (require.main === module) {`
    still open there, or the guard on the same line). 2026-10-23 21:26 W2 ADDED a guarded listen to src/server/server.js
    and kept the unguarded one above it: any guard in the file used to hide both.
    """
    before = source[:position]
    starts = [(before.rfind(guard), guard) for guard in _RUN_DIRECTLY_GUARDS]
    start, guard = max(starts)
    if start < 0:
        return False
    between = before[start + len(guard):]
    if "\n" not in between:
        return True  # if (require.main === module) server.listen(...)
    return between.count("{") > between.count("}")


def _mocha_exits(workspace: Path) -> bool:
    """True if the project runs mocha with --exit (package.json "test" script or a .mocharc with exit: true)."""
    root = Path(workspace)
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        script = str(((package or {}).get("scripts") or {}).get("test") or "")
        if "--exit" in script:
            return True
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        pass
    for name in (".mocharc.js", ".mocharc.cjs", ".mocharc.json", ".mocharc.yml", ".mocharc.yaml", ".mocharc.jsonc"):
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"""["']?exit["']?\s*[:=]\s*true""", text):
            return True
    return False


def server_on_load_problems(workspace: Path, rel_path: str, source: Optional[str] = None) -> List[str]:
    """Round 3e: a TEST file that loads a project module which starts a server when it is loaded (the run never ends)."""
    problems: List[str] = []
    try:
        rel = str(rel_path).replace("\\", "/")
        if _language(rel) != "javascript" or not is_test_file(rel):
            return problems
        if source is None:
            source = (Path(workspace) / rel).read_text(encoding="utf-8")
        if not _JS_TEST_CODE_RE.search(source or "") or _mocha_exits(workspace):
            return problems
        root = Path(workspace).resolve()
        seen = set()
        for spec in _js_specs(source or ""):
            target = _resolve_js(root, rel, spec)
            if target is None or target in seen or target.suffix.lower() not in (".js", ".mjs", ".cjs"):
                continue
            seen.add(target)
            try:
                listen = starts_server_on_load(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if not listen:
                continue
            shown = target.relative_to(root).as_posix()
            problems.append(
                f"{rel} loads {shown}, which STARTS THE SERVER as soon as it is loaded ({listen}), so the test run never "
                f"ends (`npm test` / mocha / `node --test` time out). In {shown} start the server only when the file is "
                "run directly - `if (require.main === module) { server.listen(...); }` - and export app/server for the "
                "tests (or close the server in an `after()` hook).")
    except Exception as err:
        logger.debug(f"server_on_load_problems({rel_path}) failed: {err}")
    return problems


def _direct_test_runs(command: str) -> List["re.Match"]:
    """Round 3f: the matches of a test FILE run directly ('node test/game.test.js')."""
    return [m for m in _DIRECT_TEST_RUN_RE.finditer(command) if is_test_file(m.group("path"))]


# Round 3f: 2026-10-23 test/game.test.js (WiFi_Snake_Multiplayer_Game) did `global.process = { exit: function(code) {...} }`
# and ended with `if (testsFailed > 0) { process.exit(1); }` - a failing run would have ended with exit code 0 ("passed").
_PROCESS_OVERRIDE_RE = re.compile(r"^\s*(?:global|globalThis|window)\.process\s*=(?!=)", re.MULTILINE)


def process_override_problems(rel_path: str, source: Optional[str]) -> List[str]:
    """Round 3f: a JavaScript TEST file that replaces Node's `process` object (its exit code no longer reports failures)."""
    try:
        rel = str(rel_path).replace("\\", "/")
        if _language(rel) != "javascript" or not is_test_file(rel) or not source:
            return []
        stripped = _strip_js_comments(source)
        match = _PROCESS_OVERRIDE_RE.search(stripped)
        if not match:
            return []
        line = stripped.count("\n", 0, match.start()) + 1
        return [f"{rel} replaces Node's `process` object (`{match.group(0).strip()}` at line {line}): process.exit() and the "
                "exit code no longer work, so a FAILING test run ends with exit code 0 (it looks passed) or never ends. "
                "Do not assign global.process; to test code that calls process.exit, pass a function in or stub only "
                "`process.exit` and restore it afterwards."]
    except Exception as err:
        logger.debug(f"process_override_problems({rel_path}) failed: {err}")
        return []


# --- Round 3q: a Python import of a JavaScript file -----------------------------------------------------------------------
# 2026-10-25 22:53:46 (WiFi_Snake_Game, qwen3.5:4b): W2 wrote tests/test_players.py with `from src.frontend.players import
# (Player, players, ...)` for src/frontend/players.js. Every run failed with "ModuleNotFoundError: No module named
# 'src.frontend.players'" (exit code 2) and the framework's diagnosis said nothing. PM1 then made a task to "fix the test
# imports", W1 wrote src/frontend/players.py - a Python copy of players.js - and spent 23:21-23:50 making that copy fit the
# test (no full test run ever passed). The project's own plan said "Testing: pytest for Python modules".
MAX_LANGUAGE_PROBLEMS = 3


def _python_import_targets(source: str) -> Optional[List[tuple]]:
    """(module, line) of every absolute import of a Python source ('from a.b import c' also gives 'a.b.c'), None if it does
    not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    targets: List[tuple] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            targets.append((node.module, node.lineno))
            targets.extend((f"{node.module}.{alias.name}", node.lineno) for alias in node.names if alias.name != "*")
    return sorted(set(targets), key=lambda item: (item[1], item[0]))


def _python_module_exists(bases: List[Path], parts: List[str]) -> bool:
    for base in bases:
        target = base.joinpath(*parts)
        if Path(str(target) + ".py").is_file() or target.is_dir():  # a module, a package or a namespace package
            return True
    return False


def _installed_module(name: str) -> bool:
    """True if a top-level module of that name is importable here (an installed package). Only for one-part names: the
    framework's own 'src' package must not hide a project's src/."""
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def other_language_module_file(workspace: Path, module: str, from_rel: Optional[str] = None) -> Optional[str]:
    """
    Round 3q: the project's JavaScript/TypeScript file that a Python import of `module` points to when NO Python module or
    package of that name exists (searched from the workspace root, src/ - the framework conftest adds both - and the
    importing file's folder), else None. 'src.frontend.players' -> 'src/frontend/players.js'; a one-part name ('players')
    also matches a JavaScript file of that name anywhere in the project when no players.py exists and the name is neither in
    the standard library nor installed. Never raises.
    """
    try:
        parts = [part for part in str(module or "").split(".") if part]
        if not parts or not all(part.isidentifier() for part in parts):
            return None
        if parts[0] in set(getattr(sys, "stdlib_module_names", ())) | _PY_TEST_LIBRARIES:
            return None
        root = Path(workspace).resolve()
        bases = [root, root / "src"]
        if from_rel:
            bases.append((root / str(from_rel)).parent)
        if _python_module_exists(bases, parts):
            return None
        if len(parts) == 1 and _installed_module(parts[0]):
            return None  # 'import tasklib': the installed package, whatever files the project has
        for base in bases:
            target = base.joinpath(*parts)
            for suffix in JS_SUFFIXES:
                candidate = Path(str(target) + suffix)
                try:
                    if candidate.is_file() and candidate.resolve().is_relative_to(root):
                        return candidate.resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
        if len(parts) == 1:
            project_files = _project_source_files(root)
            if any(PurePosixPath(p).name == f"{parts[0]}.py" for p in project_files):
                return None
            matches = sorted(p for p in project_files
                             if _language(p) == "javascript" and PurePosixPath(p).stem == parts[0])
            if matches:
                return matches[0]
    except Exception as err:
        logger.debug(f"other_language_module_file({module}) failed: {err}")
    return None


def _javascript_test_advice(workspace: Path, js_rel: str) -> str:
    stem = PurePosixPath(js_rel).stem
    test_rel = f"tests/{stem}.test.js"
    advice = (f"JavaScript is tested with a JavaScript test run by Node.js (e.g. {test_rel} with "
              f"`const {stem} = require('{_relative_spec(test_rel, js_rel)}');`, run with `node --test tests/`)")
    try:
        source = (Path(workspace) / js_rel).read_text(encoding="utf-8")
        if len(source) <= MAX_SOURCE_BYTES and not _JS_EXPORT_RE.search(_strip_js_comments(source)):
            advice += (f"; {js_rel} exports nothing yet, so it needs e.g. `if (typeof module !== 'undefined') "
                       "module.exports = { ... };` at its end (the browser ignores that line)")
    except (OSError, UnicodeDecodeError):
        pass
    return advice


def python_import_language_problems(workspace: Path, rel_path: str, source: Optional[str] = None) -> List[str]:
    """Round 3q: imports of a Python file whose module exists only as a JavaScript/TypeScript file of the project."""
    try:
        rel = str(rel_path).replace("\\", "/")
        if _language(rel) != "python":
            return []
        if source is None:
            source = (Path(workspace) / rel).read_text(encoding="utf-8")
        if not source or len(source) > MAX_SOURCE_BYTES:
            return []
        targets = _python_import_targets(source)
        if not targets:
            return []
        problems: List[str] = []
        reported: Set[str] = set()
        for module, line in targets:
            js_rel = other_language_module_file(workspace, module, rel)
            if not js_rel or js_rel in reported:
                continue
            reported.add(js_rel)
            problems.append(
                f"{rel} imports '{module}' (line {line}), but that module is JavaScript code: {js_rel}. Python cannot import "
                f"a JavaScript file, so this fails with \"ModuleNotFoundError: No module named '{module}'\"" +
                (" and no test in the file runs" if is_test_file(rel) else "") + ". "
                f"{_javascript_test_advice(workspace, js_rel)}; pytest tests only Python code. Do NOT write a Python copy "
                f"of {js_rel} to make the import work.")
            if len(problems) >= MAX_LANGUAGE_PROBLEMS:
                break
        return problems
    except Exception as err:
        logger.debug(f"python_import_language_problems({rel_path}) failed: {err}")
        return []


# Round 3q: 2026-10-25 the finished WiFi_Snake_Game's src/frontend/index.html loaded only game.js: src/frontend/players.js
# and src/frontend/leaderboard.js (Kick-off Tasks 4 and 5, both "finished") were never loaded by any page, so their code
# never ran. A note (not a completion refusal: the page may belong to another task and be written later).
_SCRIPT_SRC_RE = re.compile(r"""<script\b[^>]*?\bsrc\s*=\s*(['"])([^'"]+)\1""", re.IGNORECASE)
_BROWSER_CODE_RE = re.compile(r"\b(?:document|window|localStorage|sessionStorage|navigator)\s*\.")
MAX_HTML_FILES = 50


def _project_files_with_suffix(root: Path, suffixes: tuple, limit: int) -> List[Path]:
    found: List[Path] = []
    try:
        for path in sorted(root.rglob("*")):
            if len(found) >= limit:
                break
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if set(rel.parts[:-1]) & _SKIP_DIRS or rel.parts[0].startswith(".") or not path.is_file():
                continue
            if path.suffix.lower() in suffixes:
                found.append(path)
    except OSError:
        pass
    return found


def unloaded_browser_script_problem(workspace: Path, rel_path: str, source: Optional[str]) -> Optional[str]:
    """
    Round 3q: a browser script (.js that uses document/window/localStorage and requires nothing) of a project whose HTML
    pages load local scripts, when no page loads it and no other JavaScript file names it. None otherwise. Never raises.
    """
    try:
        rel = str(rel_path).replace("\\", "/")
        if not rel.lower().endswith(".js") or is_test_file(rel) or not source or len(source) > MAX_SOURCE_BYTES:
            return None
        code = _strip_js_comments(source)
        if not _BROWSER_CODE_RE.search(code) or re.search(r"\brequire\s*\(", code):
            return None
        root = Path(workspace).resolve()
        target = (root / rel).resolve()
        pages: List[str] = []
        for page in _project_files_with_suffix(root, (".html", ".htm"), MAX_HTML_FILES):
            text = page.read_text(encoding="utf-8", errors="replace")
            local = [m.group(2).split("?")[0].split("#")[0] for m in _SCRIPT_SRC_RE.finditer(text)
                     if not re.match(r"^(?:[a-z]+:)?//", m.group(2), re.IGNORECASE)]
            if not local:
                continue
            for spec in local:
                base = root if spec.startswith("/") else page.parent
                if (base / spec.lstrip("/")).resolve() == target:
                    return None
            pages.append(page.relative_to(root).as_posix())
        if not pages:
            return None  # no page loads local scripts: not a browser project the check understands
        name = PurePosixPath(rel).name
        for other in _project_files_with_suffix(root, JS_SUFFIXES, MAX_PROJECT_FILES):
            if other.resolve() != target and name in other.read_text(encoding="utf-8", errors="replace"):
                return None  # imported, required or loaded by another script
        from os.path import relpath
        spec = relpath(rel, PurePosixPath(pages[0]).parent.as_posix() or ".").replace("\\", "/")
        shown = ", ".join(pages[:3])
        return (f"{rel} is browser code, but no page loads it: {shown} {'has' if len(pages) == 1 else 'have'} no "
                f"`<script src=\"{spec}\">`, so its code never runs. Add `<script src=\"{spec}\"></script>` to the page that "
                "uses it, before the script that calls its functions.")
    except Exception as err:
        logger.debug(f"unloaded_browser_script_problem({rel_path}) failed: {err}")
        return None


_NO_MODULE_RE = re.compile(r"No module named '([\w.]+)'")


def missing_module_language_hints(workspace: Optional[Path], output: Optional[str]) -> List[str]:
    """Round 3q: for "No module named 'x.y'" in a test run's output, the hint when x.y is a JavaScript file of the project."""
    hints: List[str] = []
    try:
        if not workspace or not output:
            return hints
        seen: Set[str] = set()
        for match in _NO_MODULE_RE.finditer(str(output)):
            module = match.group(1)
            js_rel = other_language_module_file(Path(workspace), module)
            if not js_rel or js_rel in seen:
                continue
            seen.add(js_rel)
            hints.append(f"The tests import '{module}', but that module is JavaScript ({js_rel}): Python cannot import a "
                         f"JavaScript file - that is the ModuleNotFoundError. {_javascript_test_advice(Path(workspace), js_rel)}"
                         f"; pytest tests only Python code. Do NOT write a Python copy of {js_rel}.")
            if len(hints) >= MAX_LANGUAGE_PROBLEMS:
                break
    except Exception as err:
        logger.debug(f"missing_module_language_hints failed: {err}")
    return hints


def is_test_command(command: Optional[str]) -> bool:
    """True for a command that runs tests (npm test, npx mocha, jest, node --test, pytest, node test/x.test.js, ...)."""
    if not command:
        return False
    text = str(command)
    return bool(_TEST_COMMAND_RE.search(text)) or bool(_direct_test_runs(text))


def test_command_end(command: Optional[str]) -> Optional[int]:
    """Round 3f: the position right after the LAST test invocation in the command (for '... || true' checks), or None."""
    if not command:
        return None
    text = str(command)
    ends = [m.end() for m in _TEST_COMMAND_RE.finditer(text)] + [m.end() for m in _direct_test_runs(text)]
    return max(ends) if ends else None


def last_output_line(output: Optional[str]) -> str:
    """Round 3f: the last non-empty line of a command's output (at most 200 characters), '' if none."""
    for line in reversed(str(output or "").splitlines()):
        line = line.strip()
        if line and not line.startswith("...[") and not line.startswith("--- "):
            return line if len(line) <= 200 else line[:197] + "..."
    return ""


def cpu_state(cpu_busy: Optional[float]) -> Optional[str]:
    """Round 3f: 'busy', 'idle' or None (unknown / in between) for the CPU use of a timed-out process group."""
    if not isinstance(cpu_busy, (int, float)) or isinstance(cpu_busy, bool):
        return None
    if cpu_busy >= BUSY_CPU_RATE:
        return "busy"
    if cpu_busy <= IDLE_CPU_RATE:
        return "idle"
    return None


def busy_loop_text(cpu_busy: float, output: Optional[str]) -> str:
    """Round 3f: what a timed-out command that kept the CPU busy means."""
    last = last_output_line(output)
    where = (f" It printed last: '{last}' - the code that runs right AFTER that line is where it loops." if last
             else " It printed nothing, so it loops before its first output (e.g. while a module is loaded).")
    return (f"the process was BUSY when it was stopped (it used {min(cpu_busy, 9.99) * 100:.0f}% of a CPU core), so it was "
            "not waiting for anything: the code is stuck in a loop that never ends - a `while`/`for` loop whose exit "
            "condition never becomes true (e.g. it searches for a free cell on a board whose size is still 0, or a counter "
            "that is never changed), or a function that keeps calling itself." + where)


def test_hang_hint(workspace: Optional[Path], command: Optional[str], cpu_busy: Optional[float] = None,
                   output: Optional[str] = None) -> str:
    """
    Round 3e: what to tell an agent whose TEST command timed out ('' for other commands): the test files that load a module
    which starts a server on load, or the usual causes of a test run that does not end.
    Round 3f: cpu_busy (CPU cores used in the last second before the stop) tells a loop that never ends (busy) from a
    process that waits for something left open (idle); output is the command's output so far (its last line is quoted).
    """
    try:
        if not is_test_command(command):
            return ""
        state = cpu_state(cpu_busy)
        if state == "busy":
            return ("\n[Framework hint - why the TEST run did not end:] " + busy_loop_text(cpu_busy, output)
                    + " Read that code and fix the loop. Running the tests again (or with a longer timeout) will time out "
                    "again.")
        last = last_output_line(output)
        printed = f" It printed last: '{last}'." if last else ""
        found: List[str] = []
        if workspace and Path(workspace).is_dir():
            root = Path(workspace).resolve()
            for path in sorted(root.rglob("*")):
                try:
                    rel = path.relative_to(root)
                except ValueError:
                    continue
                if set(rel.parts[:-1]) & _SKIP_DIRS or not path.is_file() or not is_test_file(rel.as_posix()):
                    continue
                for problem in server_on_load_problems(root, rel.as_posix()):
                    if problem not in found:
                        found.append(problem)
                from src.utils.code_map import open_timer_problems  # Round 4a: a timer the test starts and never stops
                for problem in open_timer_problems(root, rel.as_posix()):
                    if problem not in found:
                        found.append(problem)
                if len(found) >= 3:
                    break
        if found:
            return ("\n[Framework hint - why the TEST run did not end:]\n- " + "\n- ".join(found)
                    + "\nRunning the same test command again (or with a longer timeout) will time out again: fix this first.")
        if state == "idle":
            return ("\n[Framework hint - the TEST process was IDLE (0% CPU) when it was stopped, so it waited for something "
                    "left open:] usually a server that starts listening when its file is loaded (start it only under "
                    "`if (require.main === module)`), an open database connection or socket, or a timer/setInterval that is "
                    "never cleared. Close them in an after()/afterAll() hook, or run mocha with `--exit`." + printed
                    + " Running the same command again with a longer timeout will not help.")
        return ("\n[Framework hint - a TEST run that does not end either loops forever or keeps something open:] a "
                "`while`/`for` loop whose exit condition never becomes true or a function that keeps calling itself; or a "
                "server that starts listening when its file is loaded (start it only under `if (require.main === module)`), "
                "an open database connection or socket, or a timer/setInterval that is never cleared (close them in an "
                "after()/afterAll() hook, or run mocha with `--exit`)." + printed
                + " Running the same command again with a longer timeout will not help.")
    except Exception as err:
        logger.debug(f"test_hang_hint failed: {err}")
        return ""


# --- Round 3g: a jest setup file that jest also runs as a test file -------------------------------------------------------
# 2026-10-23 (WiFi_Snake_Game) jest.config.js had testMatch ['**/tests/**/*.js'] and setupFilesAfterEnv
# ['<rootDir>/tests/setup.js']: jest ran tests/setup.js as a test suite ("Your test suite must contain at least one test"),
# and PM1 created the task "Add at least one test to tests/setup.js".
_JEST_CONFIG_NAMES = ("jest.config.js", "jest.config.cjs", "jest.config.mjs")
_JEST_LIST_RE = r"{key}\s*:\s*\[([^\]]*)\]"
_QUOTED_STRING_RE = re.compile(r"""['"`]([^'"`]+)['"`]""")


def _glob_regex(pattern: str) -> Optional["re.Pattern"]:
    """A regex for a simple glob ('**/tests/**/*.js'); None for patterns with extglob/brace syntax."""
    if any(ch in pattern for ch in "(){}[]!"):
        return None
    pattern = pattern.replace("<rootDir>/", "").lstrip("./")
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile("^" + out + "$")


def jest_setup_match_problems(workspace: Path, rel_path: str) -> List[str]:
    """
    Round 3g: for jest.config.js or a jest setup file - a setup file (setupFiles / setupFilesAfterEnv) that the config's
    testMatch also matches, so jest runs it as a test suite. Only explicit, simple testMatch globs are judged.
    """
    try:
        rel = str(rel_path).replace("\\", "/")
        root = Path(workspace)
        config_text = None
        for name in _JEST_CONFIG_NAMES:
            if (root / name).is_file():
                config_text = (root / name).read_text(encoding="utf-8")
                break
        if config_text is None:
            return []
        from src.utils.test_diagnosis import _jest_setup_files
        setup_files = _jest_setup_files(root)
        if rel not in _JEST_CONFIG_NAMES and rel not in setup_files:
            return []
        # whole-line // comments only: a glob such as '**/tests/**/*.js' looks like a /* block comment */
        stripped = "\n".join(line for line in config_text.splitlines() if not line.lstrip().startswith("//"))
        match_block = re.search(_JEST_LIST_RE.format(key="testMatch"), stripped)
        if not match_block:
            return []
        globs = [g for g in (_glob_regex(p) for p in _QUOTED_STRING_RE.findall(match_block.group(1))) if g]
        ignore_block = re.search(_JEST_LIST_RE.format(key="testPathIgnorePatterns"), stripped)
        ignores = _QUOTED_STRING_RE.findall(ignore_block.group(1)) if ignore_block else []
        problems = []
        for setup in setup_files:
            if not any(g.match(setup) for g in globs):
                continue
            ignored = False
            for pattern in ignores:
                try:
                    if re.search(pattern.replace("<rootDir>", ""), "/" + setup):
                        ignored = True
                        break
                except re.error:
                    continue
            if ignored:
                continue
            problems.append(
                f"jest runs the setup file {setup} as a TEST file: testMatch in the jest config matches it, so jest reports "
                "'Your test suite must contain at least one test' (and any test written into it runs before every test "
                f"file). Exclude it - add '<rootDir>/{setup}' to testPathIgnorePatterns - or move it out of the tests "
                "folder (e.g. jest.setup.js) and point setupFilesAfterEnv there. Never add tests to a setup file.")
        return problems
    except Exception as err:
        logger.debug(f"jest_setup_match_problems({rel_path}) failed: {err}")
        return []


# --- Round 4d: a page that loads files that do not exist, a README whose commands cannot work -----------------------------------
# 2026-10-28 20:2x (WiFi_Snake_Game) W2 wrote src/index/index.html with `<script src="game.js">` and `<script src="auth.js">`:
# they resolve to src/index/game.js and src/index/auth.js, but the files are src/game.js and src/auth.js - the page could not
# load the game. Its README told the user to run `npm install` and `npm start` in a project without a package.json.
_LINK_HREF_RE = re.compile(r"""<link\b[^>]*?\bhref\s*=\s*(['"])([^'"]+)\1""", re.IGNORECASE)
_REMOTE_RE = re.compile(r"^(?:[a-z][\w+.-]*:|//|#|\{|\$)", re.IGNORECASE)
_README_NAME_RE = re.compile(r"^readme(?:\.[\w-]+)?\.md$", re.IGNORECASE)


def is_page_file(rel_path: str) -> bool:
    return str(rel_path).lower().endswith((".html", ".htm"))


def is_readme_file(rel_path: str) -> bool:
    return bool(_README_NAME_RE.match(PurePosixPath(str(rel_path).replace("\\", "/")).name))


def html_reference_problems(workspace: Path, rel_path: str, source: Optional[str] = None) -> List[str]:
    """
    Round 4d: the local `<script src>` / `<link href>` files of a page that neither exist nor are planned
    (PROJECT_STRUCTURE.md), with the file of the same name the project has. Absolute ('/x.js') and remote references are
    not judged (the server decides where they point). Never raises.
    """
    problems: List[str] = []
    try:
        root = Path(workspace).resolve()
        rel = str(rel_path).replace("\\", "/")
        if source is None:
            source = (root / rel).read_text(encoding="utf-8", errors="replace")
        if not source or len(source) > MAX_SOURCE_BYTES:
            return problems
        from src.utils.program_run_check import planned_files
        planned = set(planned_files(root))
        page_dir = (root / rel).parent
        seen = set()
        for pattern in (_SCRIPT_SRC_RE, _LINK_HREF_RE):
            for match in pattern.finditer(source):
                spec = match.group(2).split("?")[0].split("#")[0].strip()
                if not spec or spec in seen or _REMOTE_RE.match(spec) or spec.startswith("/"):
                    continue
                seen.add(spec)
                if pattern is _LINK_HREF_RE and not re.search(r"\.(?:css|js|mjs|json|webmanifest)$", spec, re.IGNORECASE):
                    continue  # icons, pages and the like
                target = (page_dir / spec).resolve()
                try:
                    target_rel = target.relative_to(root).as_posix()
                except ValueError:
                    continue
                if target.exists() or target_rel in planned:
                    continue
                name = PurePosixPath(spec).name
                same = [p for p in _project_files_with_suffix(root, (PurePosixPath(name).suffix.lower(),), MAX_PROJECT_FILES)
                        if p.name == name and not is_test_file(p.relative_to(root).as_posix())]
                same += [root / p for p in planned if PurePosixPath(p).name == name and not (root / p).exists()]
                if same:
                    from os.path import relpath
                    better = relpath(same[0], page_dir).replace("\\", "/")
                    fix = f" The project's {name} is {same[0].relative_to(root).as_posix()}: use `{better}`."
                else:
                    fix = " Create that file, or point the reference at the file that exists."
                problems.append(f"{rel} loads `{spec}`, which is {target_rel} - that file does NOT exist, so the page "
                                f"cannot load it.{fix}")
    except Exception as err:
        logger.debug(f"html_reference_problems({rel_path}) failed: {err}")
    return problems


def link_problems(workspace: Path, path: Path, rel_path: Optional[str] = None) -> List[str]:
    """All link problems of one existing file (for the task completion check). Never raises."""
    try:
        root = Path(workspace).resolve()
        rel = rel_path or Path(path).resolve().relative_to(root).as_posix()
        if is_page_file(rel):
            return html_reference_problems(root, rel)  # Round 4d
        if is_readme_file(rel):
            from src.utils.program_run_check import readme_command_problems
            return readme_command_problems(root, rel)  # Round 4d
        if _language(rel) is None:
            return []
        source = Path(path).read_text(encoding="utf-8")
        if not source.strip() or len(source) > MAX_SOURCE_BYTES:
            return []
        problems: List[str] = []
        target_problem = test_target_problem(root, rel, source)
        if target_problem:
            problems.append(target_problem)
        problems.extend(export_problems(root, rel, source))
        problems.extend(missing_dependency_problems(root, rel, source))
        problems.extend(server_on_load_problems(root, rel, source))  # Round 3e
        problems.extend(process_override_problems(rel, source))  # Round 3f
        problems.extend(jest_setup_match_problems(root, rel))  # Round 3g
        problems.extend(python_import_language_problems(root, rel, source))  # Round 3q
        return problems
    except Exception as err:
        logger.debug(f"link_problems({path}) failed: {err}")
        return []


def link_notes_text(workspace: Optional[Path], rel_path: Optional[str], source: Optional[str]) -> str:
    """The text appended to a write / edit that WAS saved when the file has link problems ('' otherwise)."""
    try:
        if not workspace or not rel_path or source is None:
            return ""
        root = Path(workspace).resolve()
        rel = str(rel_path).replace("\\", "/").strip()
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        if is_page_file(rel) or is_readme_file(rel):
            # Round 4d: a page's missing script / style files, a README's commands that cannot work
            if is_page_file(rel):
                found = html_reference_problems(root, rel, source)
            else:
                from src.utils.program_run_check import readme_command_problems
                found = readme_command_problems(root, rel)
            if not found:
                return ""
            return ("\n[Framework check - the file WAS saved, but it does not work with the project yet (a task with this "
                    "problem cannot be marked finished):]\n- " + "\n- ".join(found))
        if _language(str(rel_path)) is None:
            return ""
        problems: List[str] = []
        target_problem = test_target_problem(root, rel, source)
        if target_problem:
            problems.append(target_problem)
        problems.extend(export_problems(root, rel, source))
        problems.extend(missing_dependency_problems(root, rel, source))
        problems.extend(server_on_load_problems(root, rel, source))  # Round 3e
        problems.extend(process_override_problems(rel, source))  # Round 3f
        problems.extend(jest_setup_match_problems(root, rel))  # Round 3g
        problems.extend(python_import_language_problems(root, rel, source))  # Round 3q
        text = ""
        if problems:
            text = ("\n[Framework check - the file WAS saved, but it does not work with the project yet (a task with this "
                    "problem cannot be marked finished):]\n- " + "\n- ".join(problems))
        notes = []
        unloaded = unloaded_browser_script_problem(root, rel, source)  # Round 3q: a note only, not a completion problem
        if unloaded:
            notes.append(unloaded)
        if is_test_file(rel) and _language(rel) == "javascript":
            # Round 3y: a JavaScript test that uses a module's INNER variables or names the module does not export (notes)
            from src.utils.code_map import missing_names_hint, module_internal_hint, open_timer_problems
            notes.extend(hint for hint in (missing_names_hint(root, rel, source), module_internal_hint(root, rel, source))
                         if hint)
            notes.extend(open_timer_problems(root, rel, source))  # Round 4a: a timer it starts and never stops
        if notes:
            text += "\n[Framework note - the file WAS saved:]\n- " + "\n- ".join(notes)
        return text
    except Exception as err:
        logger.debug(f"link_notes_text({rel_path}) failed: {err}")
        return ""

# END OF FILE src/tools/project_links_check.py
