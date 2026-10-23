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
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
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
        if any(name == "." or (name and name not in stdlib) for name in imports):
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
            return problems  # no package.json: cannot judge (e.g. a plain browser page)
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


def link_problems(workspace: Path, path: Path, rel_path: Optional[str] = None) -> List[str]:
    """All link problems of one existing file (for the task completion check). Never raises."""
    try:
        root = Path(workspace).resolve()
        rel = rel_path or Path(path).resolve().relative_to(root).as_posix()
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
        return problems
    except Exception as err:
        logger.debug(f"link_problems({path}) failed: {err}")
        return []


def link_notes_text(workspace: Optional[Path], rel_path: Optional[str], source: Optional[str]) -> str:
    """The text appended to a write / edit that WAS saved when the file has link problems ('' otherwise)."""
    try:
        if not workspace or not rel_path or source is None or _language(str(rel_path)) is None:
            return ""
        root = Path(workspace).resolve()
        rel = str(rel_path).replace("\\", "/").strip()
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        problems: List[str] = []
        target_problem = test_target_problem(root, rel, source)
        if target_problem:
            problems.append(target_problem)
        problems.extend(export_problems(root, rel, source))
        problems.extend(missing_dependency_problems(root, rel, source))
        if not problems:
            return ""
        return ("\n[Framework check - the file WAS saved, but it does not work with the project yet (a task with this "
                "problem cannot be marked finished):]\n- " + "\n- ".join(problems))
    except Exception as err:
        logger.debug(f"link_notes_text({rel_path}) failed: {err}")
        return ""

# END OF FILE src/tools/project_links_check.py
