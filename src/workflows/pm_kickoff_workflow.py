# START OF FILE src/workflows/pm_kickoff_workflow.py
import logging
import json
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .base import BaseWorkflow, WorkflowResult
from src.agents.constants import (
    AGENT_TYPE_PM,
    PM_STATE_STARTUP, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS,
    AGENT_STATUS_IDLE
)
from src.config.settings import settings
from src.tools.sandbox_runner import resolve_shared_workspace
from src.utils.framework_conftest import FRAMEWORK_CONFTEST, FRAMEWORK_CONFTEST_MARKER, refresh_framework_conftest

if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

logger = logging.getLogger(__name__)


# --- Project structure parsing (Round 1c) -------------------------------------------
# The PM's "project_structure" is free-form JSON. The harness must accept every
# reasonable shape and must never turn a file name into a directory: a
# {"dir": "__init__.py"} entry once produced src/__init__.py/ as a folder and blocked
# the worker that later tried to write the real file.

# Extension-less names that are files, not directories.
_KNOWN_FILE_NAMES = frozenset({
    "Makefile", "Dockerfile", "LICENSE", "Procfile", "Gemfile", "Rakefile", "Vagrantfile",
    "Jenkinsfile", "Pipfile", "CNAME", "README", "CHANGELOG",
    ".gitignore", ".gitattributes", ".dockerignore", ".editorconfig", ".env", ".npmrc",
    ".prettierrc", ".eslintrc", ".babelrc", ".nvmrc", ".python-version", ".flake8",
})
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*$")


# Round 1v: the plan said "in the root directory" and the PM wrote {"dir": "root/temp_convert.py", "files": ["temp_convert.py"]}.
# The framework created root/ and an empty directory root/temp_convert.py/, and the tests could not import the module.
_ROOT_ALIASES = frozenset({"root", "project_root", "workspace_root", "shared_workspace", "<root>", "$root"})
# A "dir" whose last part ends in one of these is a file even if the PM gave it children.
_DEFINITE_FILE_EXTENSIONS = frozenset({
    ".py", ".md", ".txt", ".rst", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".json",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".csv", ".xml", ".sql",
})


def _strip_root_alias(rel: str) -> str:
    """'root/x.py' -> 'x.py', 'root' -> '' (the workspace root itself); anything else unchanged."""
    parts = rel.split("/") if rel else []
    if parts and parts[0].lower() in _ROOT_ALIASES:
        return "/".join(parts[1:])
    return rel


def _is_definitely_file(rel: str) -> bool:
    return PurePosixPath(rel).suffix.lower() in _DEFINITE_FILE_EXTENSIONS


def looks_like_file(name: str) -> bool:
    """True if the last path component reads as a file name (has an extension or is a known file)."""
    base = PurePosixPath(name.replace("\\", "/").rstrip("/")).name
    if not base:
        return False
    if base in _KNOWN_FILE_NAMES:
        return True
    if base.startswith(".") and base.count(".") == 1:
        return False  # dot-directory such as .github / .vscode
    return bool(_EXTENSION_RE.search(base))


def _clean_rel(name: Any) -> Optional[str]:
    """Normalises one path fragment; None if not a string, absolute, or escaping the workspace."""
    if not isinstance(name, str):
        return None
    raw = name.strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts = [p for p in PurePosixPath(raw).parts if p not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts)  # "" means the parent itself (e.g. "dir": ".")


def plan_project_structure(structure: Any) -> Tuple[List[str], List[str], List[str]]:
    """
    Parses a kickoff "project_structure" into (directories, planned_files, notes).

    Accepted shapes (any mix, nested through "children"):
      {"dir": "src", "children": [...], "files": ["main.py", {"file": "x.py"}]}
      {"file": "README.md"}  /  {"files": [...]}  /  "src/"  /  "main.py"
    A "dir" entry whose name looks like a file (and has no children) is recorded as
    a planned file. Paths are POSIX-style and relative to the workspace root.
    """
    dirs: List[str] = []
    files: List[str] = []
    notes: List[str] = []

    def add_dir(rel: str) -> None:
        if rel and rel not in dirs:
            dirs.append(rel)

    def add_file(rel: str) -> None:
        if rel and rel not in files:
            files.append(rel)
            parent = str(PurePosixPath(rel).parent)
            if parent not in (".", ""):
                add_dir(parent)

    def join(parent: str, child: str) -> str:
        return f"{parent}/{child}" if parent and child else (parent or child)

    def walk(items: Any, parent: str) -> None:
        if isinstance(items, (str, dict)):
            items = [items]
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, str):
                rel = _clean_rel(item)
                if rel and not parent:
                    rel = _strip_root_alias(rel)
                if not rel:
                    continue
                full = join(parent, rel)
                if looks_like_file(rel) and not item.rstrip().endswith("/"):
                    add_file(full)
                else:
                    add_dir(full)
                continue
            if not isinstance(item, dict):
                continue
            if "dir" in item or "directory" in item or "folder" in item:
                raw = item.get("dir", item.get("directory", item.get("folder")))
                rel = _clean_rel(raw)
                if rel is None:
                    if raw not in (None, ""):
                        notes.append(f"Skipped unsafe or invalid directory entry: {raw!r}")
                    continue
                if rel and not parent:
                    stripped = _strip_root_alias(rel)
                    if stripped != rel:
                        notes.append(f"'{rel}': '{rel.split('/')[0]}' means the workspace root; recorded as '{stripped or '.'}'.")
                        rel = stripped
                full = join(parent, rel)
                has_children = bool(item.get("children")) or bool(item.get("files"))
                if rel and looks_like_file(rel) and (not has_children or _is_definitely_file(rel)):
                    notes.append(f"'{full}' was listed as a directory but looks like a file; recorded as a planned file.")
                    add_file(full)
                    if has_children:  # its "children" belong next to it, in its parent directory
                        file_parent = str(PurePosixPath(full).parent)
                        file_parent = "" if file_parent == "." else file_parent
                        walk(item.get("children", []), file_parent)
                        walk(item.get("files", []), file_parent)
                    continue
                add_dir(full)
                walk(item.get("children", []), full)
                walk(item.get("files", []), full)
                continue
            if "file" in item or "path" in item or "name" in item:
                raw = item.get("file", item.get("path", item.get("name")))
                rel = _clean_rel(raw)
                if rel and not parent:
                    rel = _strip_root_alias(rel)
                if rel:
                    add_file(join(parent, rel))
                elif raw not in (None, ""):
                    notes.append(f"Skipped unsafe or invalid file entry: {raw!r}")
                continue
            if "files" in item or "children" in item:
                walk(item.get("files", []), parent)
                walk(item.get("children", []), parent)

    walk(structure, "")
    return dirs, files, notes


# --- Planned layout normalisation + pytest scaffold (Round 1u) --------------------------------------------------------------
# 2026-10-16 18:05: the PM planned temp_convert.py and test_temp_convert.py at the workspace ROOT but also declared empty src/ and
# tests/ directories. Workers then moved files around, rewrote conftest.py again and again, and the finished project failed all
# 15 tests with ModuleNotFoundError. The framework now makes the layout consistent and owns the import path.
# Round 3r: FRAMEWORK_CONFTEST and FRAMEWORK_CONFTEST_MARKER live in src/utils/framework_conftest.py (imported above; one
# module per file, and an earlier framework version of a project's conftest.py is updated). Both names stay importable from
# this module.
REEXPORTED_CONFTEST_NAMES = (FRAMEWORK_CONFTEST, FRAMEWORK_CONFTEST_MARKER)
_TEST_FILE_RE = re.compile(r"^(test_.+|.+_test)\.py$", re.IGNORECASE)
_ROOT_PY_KEEP = frozenset({"conftest.py", "setup.py", "manage.py", "noxfile.py", "tasks.py", "wsgi.py", "asgi.py"})


# Round 2y: names that legitimately exist in several directories of one project; never treated as the same file
_SHARED_FILE_NAMES = frozenset({
    "__init__.py", "conftest.py", "setup.py", "main.py", "app.py", "utils.py", "config.py", "models.py", "views.py",
    "index.js", "index.mjs", "index.ts", "index.tsx", "index.jsx", "index.html", "main.js", "main.ts", "app.js", "app.ts",
    "utils.js", "config.js", "package.json", "tsconfig.json", "README.md", "readme.md", "styles.css", "style.css",
    "Makefile", "Dockerfile", ".gitignore", ".env",
})


def duplicate_structure_files(structure_files: List[str], task_files: List[str]) -> Tuple[List[str], List[str]]:
    """
    Round 2y: (structure files to drop, notes). A file a kick-off task names with its directory is the same file as a
    project_structure file with that name in another directory - when the structure has exactly one file of that name
    and no task names it. The task's path is kept: the worker builds from the task text and the completion check reads
    it. 2026-10-22 14:46: the structure had src/game/game.js and src/storage/player_manager.js, Kick-off Task 1 said
    src/game.js and storage/player_manager.js, and PROJECT_STRUCTURE.md listed all four files to create.
    """
    drop: List[str] = []
    notes: List[str] = []
    structure_set, task_set = set(structure_files), set(task_files)
    for named in task_files:
        name = PurePosixPath(named).name
        if named in structure_set:
            continue
        same_name = [f for f in structure_files if PurePosixPath(f).name == name]
        if name in _SHARED_FILE_NAMES:
            # Round 4d: a shared name counts as the same file only in a folder named after it (src/index/index.html for
            # the task's src/index.html, 2026-10-28 20:05 - the page was built there and loaded "game.js" from src/index/)
            same_name = [f for f in same_name if PurePosixPath(f).parent.name == PurePosixPath(f).stem
                         and PurePosixPath(f).parent.name != PurePosixPath(named).parent.name]
        if len(same_name) == 1 and same_name[0] not in task_set and same_name[0] not in drop:
            drop.append(same_name[0])
            notes.append(f"Planned file '{same_name[0]}' dropped: the kick-off task names this file '{named}', so that is "
                         "the one path to create.")
    return drop, notes


def prune_dirs_of_dropped_files(dirs: List[str], files: List[str], dropped: List[str]) -> Tuple[List[str], List[str]]:
    """
    Round 3o: (directories, notes) without the directories that were planned only for files the kick-off DROPPED - no kept
    file and no kept sub-directory lies under them. 2026-10-25 13:27:20 'src/game/snake_game.py' and 'src/server/server.py'
    were dropped for the tasks' 'src/snake_game.py' and 'src/server.py', but src/game/ and src/server/ were still created and
    listed in PROJECT_STRUCTURE.md: W1 listed both, PM1 looked for src/server/server.py and src/server/__init__.py.
    A directory the plan lists without any file (e.g. 'assets/') is kept.
    """
    def under(path: str, directory: str) -> bool:
        return path.startswith(directory.rstrip("/") + "/")

    removed: List[str] = []
    for directory in sorted(dirs, key=lambda d: (-d.count("/"), d)):  # deepest first
        if not any(under(f, directory) for f in dropped):
            continue
        if any(under(f, directory) for f in files):
            continue
        if any(under(other, directory) for other in dirs if other != directory and other not in removed):
            continue
        removed.append(directory)
    notes = [f"Directory '{d}/' not created: it was planned only for file(s) that were dropped." for d in sorted(removed)]
    return [d for d in dirs if d not in removed], notes


# --- Round 3c: the tests of JavaScript code are JavaScript -------------------------------------------------------------------
# 2026-10-22 23:24 the snake game was JavaScript (src/game.js, src/player.js, src/server.js), but the plan said "Write pytest
# tests" and the structure planned tests/test_game.py, test_player.py and test_leaderboard.py. The framework wrote a pytest
# conftest.py, and W1 spent 9 hours on a Python copy of the game (src/game.py) so that pytest had something to import.
_JS_EXTENSIONS = frozenset({".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"})
_TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)(test_[^/]+|[^/]+_test\.py|[^/]+\.(test|spec)\.[^/]+|conftest\.py)$",
                           re.IGNORECASE)
JS_TEST_LANGUAGE_NOTE = ("FRAMEWORK NOTE: the code of this project is JavaScript, so its tests are JavaScript too (e.g. "
                         "tests/game.test.js, run with `node --test tests/*.test.js`). Never write Python tests or a Python copy of "
                         "the code for them; a task that says 'pytest' means these JavaScript tests.")


from src.utils.js_test_checks import node_test_command  # noqa: E402 - Round 4f


def align_test_language(planned_files: List[str]) -> Tuple[List[str], Optional[str]]:
    """
    Round 3c: (planned files, note). When the planned CODE is JavaScript/TypeScript only, planned Python test files (and a
    conftest.py) are dropped and the note says to write JavaScript tests. Anything else is returned unchanged with None.
    """
    tests = [f for f in planned_files if _TEST_PATH_RE.search(f)]
    code_exts = {PurePosixPath(f).suffix.lower() for f in planned_files
                 if f not in tests and PurePosixPath(f).suffix.lower() in (_JS_EXTENSIONS | {".py"})}
    if not code_exts or not code_exts <= _JS_EXTENSIONS:
        return planned_files, None
    python_tests = [f for f in tests if f.lower().endswith(".py")]
    if not python_tests:
        return planned_files, None
    return [f for f in planned_files if f not in python_tests], JS_TEST_LANGUAGE_NOTE


# --- Round 3s: Python tests planned for JavaScript code - in a project with Python code too ---------------------------------
# 2026-10-26 09:29:08 (WiFi_Snake_Game) the game was JavaScript (src/core/snake_game.js, src/utils/score_manager.js, ...) and
# its only Python file was src/server/server.py, but "Kick-off Task 7: Write pytest tests for game logic (movement, collision,
# score calculation) in tests/test_game_logic.py" asked for pytest. Round 3c handles JavaScript-only projects; here W3 wrote
# Python "mocks" of the game and a "Python bridge module" (src/core/snake_game.py) for 80 minutes. A planned Python test whose
# name points at the JavaScript code becomes a JavaScript test - in the planned files AND in the kick-off task texts, which
# are written before the structure is read.
MIXED_TEST_LANGUAGE_NOTE = ("FRAMEWORK NOTE: tests are written in the language of the code they test. The JavaScript code of "
                            "this project is tested with JavaScript tests (run with `node --test tests/*.test.js`); pytest tests only "
                            "its Python files. Never write a Python copy of JavaScript code for a test.")


def javascript_test_renames(planned_files: List[str]) -> Dict[str, str]:
    """
    Round 3s: {planned Python test file: JavaScript test file} for the Python tests whose subject is the project's
    JavaScript code (src/tools/project_links_check.subject_in_javascript): tests/test_game_logic.py ->
    tests/game_logic.test.js. Python tests of Python code stay.
    """
    from src.tools.project_links_check import subject_in_javascript
    tests = [f for f in planned_files if _TEST_PATH_RE.search(f)]
    code = [f for f in planned_files if f not in tests]
    renames: Dict[str, str] = {}
    for test in tests:
        path = PurePosixPath(test)
        if path.suffix.lower() != ".py" or path.name == "conftest.py" or not subject_in_javascript(test, code):
            continue
        base = re.sub(r"^test_|_test$", "", path.stem) or path.stem
        renames[test] = (path.parent / f"{base}.test.js").as_posix()
    return renames


def renamed_task_text(text: str, renames: Dict[str, str], planned_files: List[str]) -> str:
    """Round 3s: a kick-off task text with the renamed test files and a note on which language tests what."""
    from src.tools.project_links_check import subject_in_javascript
    changed = []
    for old, new in renames.items():
        pattern = re.compile(r"(?<![\w./-])" + re.escape(old) + r"(?![\w])")
        if pattern.search(text):
            text = pattern.sub(new, text)
            changed.append((old, new))
    if not changed:
        return text
    code = [f for f in planned_files if not _TEST_PATH_RE.search(f)]
    subjects = list(dict.fromkeys(filter(None, (subject_in_javascript(old, code) for old, _new in changed))))
    python_code = [f for f in code if f.endswith(".py") and PurePosixPath(f).name != "__init__.py"]
    return (f"{text} [Framework note: the code these tests check is JavaScript ({', '.join(subjects[:3])}), so they are "
            f"JavaScript tests ({', '.join(new for _old, new in changed)}, run with "
            f"`{node_test_command(files=[new for _old, new in changed])}`), not pytest tests"
            + (f"; pytest tests only the Python files ({', '.join(python_code[:3])})" if python_code else "")
            + ". Never write a Python copy of the JavaScript code.]")


# --- Round 3y: a task that says "pytest" for a JavaScript test file ---------------------------------------------------------
# 2026-10-27 11:27 (WiFi_Snake_Game) the plan named JavaScript test files from the start, so nothing was renamed (Round 3s),
# but the task texts kept the plan's words: "Kick-off Task 6: Write pytest tests for game logic in tests/test_game.js" (the
# same for tasks 7 and 8). W3 wrote PYTHON into tests/test_game.js (`import pytest` - "SyntaxError: Unexpected token
# 'import'"), then tests/test_game.py and src/game.py, and spent 2h20m on the task.
_PYTEST_WORDS_RE = re.compile(r"\b(?:pytest|py\.test|unittest)(?:\s+(?:unit\s+)?tests?)?\b|\bpython\s+(?:unit\s+)?tests?\b",
                              re.IGNORECASE)


def test_language_task_text(text: str, planned_files: List[str]) -> str:
    """
    Round 3y: a kick-off task text that asks for pytest / Python tests but names only JavaScript test files gets JavaScript
    words and a note naming the code those tests check; a task to run the tests of a project with Python AND JavaScript tests
    gets a note that both are run. Any other text is returned unchanged.
    """
    try:
        from src.utils.task_files import file_paths_in_text
        if "[Framework note: " in text and "JavaScript test" in text:
            return text  # already explained (Round 3s rename note)
        named = file_paths_in_text(text)
        js_tests = [p for p in named if _TEST_PATH_RE.search(p) and PurePosixPath(p).suffix.lower() in _JS_EXTENSIONS]
        py_tests = [p for p in named if _TEST_PATH_RE.search(p) and p.lower().endswith(".py")]
        if not _PYTEST_WORDS_RE.search(text):
            return text
        code = [f for f in planned_files if not _TEST_PATH_RE.search(f)]
        if js_tests and not py_tests:
            new_text = _PYTEST_WORDS_RE.sub("JavaScript tests", text)
            from src.utils.code_map import _subject_by_name
            js_code = [f for f in code if PurePosixPath(f).suffix.lower() in _JS_EXTENSIONS]
            subjects = list(dict.fromkeys(filter(None, (_subject_by_name(test, js_code) for test in js_tests))))
            about = f" of {', '.join(subjects[:3])}" if subjects else ""
            return (f"{new_text} [Framework note: {', '.join(js_tests[:3])} {'is a JavaScript test' if len(js_tests) == 1 else 'are JavaScript tests'}"
                    f"{about}: write {'it' if len(js_tests) == 1 else 'them'} in JavaScript with Node's built-in runner "
                    "(`const { describe, it, beforeEach } = require('node:test'); const assert = require('node:assert');`), "
                    f"run with `{node_test_command(files=js_tests)}`. Never write Python into a .js file or a Python copy "
                    "of the code.]")
        from src.utils.test_runs import is_test_run_task
        planned_js_tests = [f for f in planned_files if _TEST_PATH_RE.search(f)
                            and PurePosixPath(f).suffix.lower() in _JS_EXTENSIONS]
        planned_py_tests = [f for f in planned_files if _TEST_PATH_RE.search(f) and f.lower().endswith(".py")
                            and PurePosixPath(f).name != "conftest.py"]
        if not named or (not js_tests and not py_tests):
            if is_test_run_task(text) and planned_js_tests:
                if planned_py_tests:
                    how = ("this project has Python tests (run with `python -m pytest -q`) AND JavaScript tests (run with "
                           f"`{node_test_command(files=planned_js_tests)}`): run BOTH; pytest cannot run the .js tests")
                else:
                    how = ("the tests of this project are JavaScript: run them with "
                           f"`{node_test_command(files=planned_js_tests)}`, not pytest")
                return f"{text} [Framework note: {how}.]"
        return text
    except Exception as err:
        logger.debug(f"test_language_task_text failed: {err}")
        return text


def with_root_files(planned_files: List[str], root_names: List[str]) -> List[str]:
    """
    Round 2v: the planned files plus the root files the tasks say to create ("Create server.py ..."), unless a planned file
    already has that name in some directory (the structure put it there, e.g. 'src/server.py').
    """
    planned = list(planned_files)
    names = {PurePosixPath(path).name for path in planned}
    for name in root_names:
        if name not in names:
            planned.append(name)
            names.add(name)
    return planned


# Round 3f: files the tools of a project read from the workspace ROOT only. 2026-10-23 19:56 (WiFi_Snake_Multiplayer_Game) the
# plan put package.json in config/; W1 wrote config/package.json, `npm test` found no package.json, W1 wrote a second one at
# the root, and the project kept two different package.json files.
ROOT_ONLY_FILE_NAMES = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", ".mocharc.json", ".mocharc.js", ".mocharc.yml",
    "jest.config.js", "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini",
})


def root_only_files(files: List[str]) -> Tuple[List[str], List[str]]:
    """
    Round 3f: (files, notes). A root-only file (package.json, requirements.txt, ...) planned ONLY inside a directory moves to
    the workspace root. Several files of that name (e.g. client/package.json and server/package.json) stay as they are.
    """
    notes: List[str] = []
    result = list(files)
    for name in sorted(ROOT_ONLY_FILE_NAMES):
        same = [f for f in result if PurePosixPath(f).name == name]
        if len(same) != 1 or len(PurePosixPath(same[0]).parts) == 1:
            continue
        result[result.index(same[0])] = name
        notes.append(f"Planned file '{same[0]}' moved to the workspace root ('{name}'): the project's tools read it only there.")
    return result, notes


def normalize_planned_layout(dirs: List[str], files: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """
    Makes a planned layout consistent: Python test files always live in tests/; other Python files planned at the workspace
    root move to src/ when the plan declares a src/ directory; Round 3f: a root-only file planned only inside a directory
    (config/package.json) moves to the root. Returns (dirs, files, notes).
    """
    new_dirs = list(dirs)
    new_files: List[str] = []
    notes: List[str] = []
    has_src = "src" in new_dirs or any(d.startswith("src/") for d in new_dirs)

    def add_dir(rel: str) -> None:
        if rel not in new_dirs:
            new_dirs.append(rel)

    for rel in files:
        pure = PurePosixPath(rel)
        target = rel
        if _TEST_FILE_RE.match(pure.name) and pure.parts[0] != "tests":
            target = f"tests/{pure.name}"
        elif len(pure.parts) == 1 and pure.suffix == ".py" and has_src and pure.name not in _ROOT_PY_KEEP:
            target = f"src/{pure.name}"
        if target != rel:
            notes.append(f"Planned file '{rel}' moved to '{target}' to keep tests/ and src/ consistent.")
            add_dir(str(PurePosixPath(target).parent))
        if target not in new_files:
            new_files.append(target)
    new_files, root_notes = root_only_files(new_files)  # Round 3f
    return new_dirs, new_files, notes + root_notes


_MANAGER_ROLE_RE = re.compile(r"(^|[^a-z0-9])(pm\d*|project[\s_\-]*manager|projectmanager)($|[^a-z0-9])", re.IGNORECASE)


# Round 2i: PM1 (2026-10-20 07:26) wrote roles as objects ({"name": "Coder", "description": "..."}) and tasks with "title"
# instead of "description".
_ROLE_NAME_KEYS = ("name", "role", "title", "id")
TASK_TEXT_KEYS = ("description", "title", "task", "name", "summary")


def role_name_of(role: Any) -> Any:
    """A role given as an object becomes its name; anything else is returned unchanged."""
    if isinstance(role, dict):
        for key in _ROLE_NAME_KEYS:
            value = role.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return role


def task_text_of(task_info: Any) -> str:
    """The text of a kick-off task: 'description', else 'title' / 'task' / 'name' / 'summary' ('' if none)."""
    if not isinstance(task_info, dict):
        return ""
    for key in TASK_TEXT_KEYS:
        value = task_info.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def sanitize_worker_roles(roles: List[Any]) -> Tuple[List[Any], List[str]]:
    """
    Returns (kept_roles, dropped_manager_roles). The PM is already the project manager, so manager-type
    roles (e.g. 'Project_Manager', 'PM1_Project_Manager') must not become worker agents. Duplicate roles
    (case-insensitive) are removed; non-string entries are passed through unchanged.
    """
    kept: List[Any] = []
    dropped: List[str] = []
    seen = set()
    for role in roles:
        if not isinstance(role, str):
            kept.append(role)
            continue
        name = role.strip()
        if not name:
            continue
        if _MANAGER_ROLE_RE.search(name):
            dropped.append(name)
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(name)
    return kept, dropped


def _render_definitions(value: Any, indent: int = 0) -> List[str]:
    """Renders code_base_definitions (string, list or dict from the model) as Markdown lines."""
    pad = "  " * indent
    if value is None:
        return []
    if isinstance(value, str):
        return [(pad + line) if line else "" for line in value.splitlines()]
    if isinstance(value, dict):
        out: List[str] = []
        for k, v in value.items():
            if isinstance(v, (dict, list, tuple)):
                out.append(f"{pad}- **{k}**:")
                out.extend(_render_definitions(v, indent + 1))
            else:
                out.append(f"{pad}- **{k}**: {v}")
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            if isinstance(v, dict) and v and all(not isinstance(x, (dict, list, tuple)) for x in v.values()):
                out.append(pad + "- " + "; ".join(f"{k}: {x}" for k, x in v.items()))
            elif isinstance(v, (dict, list, tuple)):
                out.append(pad + "-")
                out.extend(_render_definitions(v, indent + 1))
            else:
                out.append(f"{pad}- {v}")
        return out
    return [pad + str(value)]


# --- Round 3c: tests, README and verification wait for the code ------------------------------------------------------------
# 2026-10-22 23:24:14 (qwen3.5:4b) the kick-off plan gave dependencies only to the last task; "Write pytest tests ... in
# tests/" and "Write README.md" depended on nothing. W1 started the tests at 00:20, while the player, server and UI code did
# not exist yet, and was still on them at 09:52; W2 wrote a README for code nobody had written.
_VERIFY_TASK_RE = re.compile(r"^\W*(?:run|execute|verify|verification|final|integration|end-to-end|smoke)\b", re.IGNORECASE)
_TEST_WORD_RE = re.compile(r"\b(?:tests?|pytest|jest|unit[- ]?tests?|test suite|test cases?|specs?)\b", re.IGNORECASE)
_WRITE_WORD_RE = re.compile(r"\b(?:write|add|create|implement|extend|develop)\b", re.IGNORECASE)
_DOCS_TASK_RE = re.compile(r"\breadme\b|\bdocumentation\b|\bdocs/|\busage guide\b", re.IGNORECASE)


def kickoff_task_kind(text: str) -> str:
    """'verify', 'tests', 'docs' or 'code' for a kick-off task description."""
    text = text or ""
    if _VERIFY_TASK_RE.search(text):
        return "verify"
    if _TEST_WORD_RE.search(text) and _WRITE_WORD_RE.search(text):
        return "tests"
    if _DOCS_TASK_RE.search(text):
        return "docs"
    return "code"


def infer_missing_dependencies(task_info_list: List[Dict[str, Any]]) -> List[str]:
    """
    Round 3c: a tests or docs task without any dependency gets the earlier CODE tasks as dependencies; a verification task
    without any dependency gets all earlier tasks. A dependency the PM gave is never changed. Changes task_info_list in place
    and returns notes for the log.
    """
    notes: List[str] = []
    earlier: List[Tuple[str, str]] = []  # (id, kind)
    for info in task_info_list:
        task_id = str(info.get("id") or "").strip()
        kind = kickoff_task_kind(str(info.get("description") or ""))
        if task_id and not info.get("depends_on"):
            if kind in ("tests", "docs"):
                needed = [tid for tid, k in earlier if k == "code"]
            elif kind == "verify":
                needed = [tid for tid, _k in earlier]
            else:
                needed = []
            if needed:
                info["depends_on"] = needed
                notes.append(f"'{task_id}' ({kind}) had no dependency: it now waits for {', '.join(needed)}.")
        if task_id:
            earlier.append((task_id, kind))
    return notes


# Round 2p: 2026-10-21 18:01:07 the kickoff plan said "depends_on": ["task_1..6"] for the README task; the id was dropped as
# unknown, so W2 wrote a 14 KB README for code that did not exist yet. A range of task ids is expanded.
_TASK_RANGE_RE = re.compile(r"^(?P<prefix>[A-Za-z_]*?)(?P<start>\d+)\s*(?:\.\.\.?|-|\u2013|\bto\b|through)\s*(?P<prefix2>[A-Za-z_]*?)(?P<end>\d+)$",
                            re.IGNORECASE)
MAX_TASK_RANGE = 50


def expand_task_range(token: str) -> List[str]:
    """'task_1..6', 'task_1-task_6', 'task_1 to 6' -> ['task_1', ..., 'task_6']; anything else is returned unchanged."""
    match = _TASK_RANGE_RE.match(str(token).strip())
    if not match:
        return [token]
    prefix, prefix2 = match.group("prefix"), match.group("prefix2")
    start, end = int(match.group("start")), int(match.group("end"))
    if prefix2 and prefix2.lower() != prefix.lower():
        return [token]
    if end < start or end - start > MAX_TASK_RANGE:
        return [token]
    return [f"{prefix}{number}" for number in range(start, end + 1)]


PYTHON_TEST_LINES = [
    "Run tests from the workspace root with `python -m pytest -q`. A framework-owned conftest.py already makes src/ importable: write",
    "`from my_module import my_function` in tests and NEVER create or edit the root conftest.py.",
]
# Round 2r: 2026-10-21 the Node.js project (WiFi_Snake_Multiplayer_Game) was told to use pytest and a framework conftest.py
# that was never written (the conftest is only written when Python files are planned, see _write_test_scaffold).
OTHER_STACK_TEST_LINES = [
    "Run tests and other commands from the workspace root, with the tools of the project's tech stack.",
]
# Round 3q: 2026-10-25 WiFi_Snake_Game planned src/backend/server.py AND src/frontend/*.js; the structure file said only "Run
# tests ... with `python -m pytest -q` ... write `from my_module import my_function`", and W2 wrote a Python test that
# imported src/frontend/players.js ("from src.frontend.players import ..."). A project with Python AND JavaScript files is
# told which tool tests which language.
MIXED_STACK_TEST_LINES = [
    "pytest tests only the Python files. A Python test can NOT import a JavaScript file: test JavaScript files with a",
    "JavaScript test run by Node.js (e.g. tests/players.test.js that loads '../src/players.js' with require, run with",
    "`node --test tests/*.test.js`), and never write a Python copy of a JavaScript file.",
]
_JAVASCRIPT_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")


EXISTING_FILES_NOTE_CARRIED = ("These planned files ALREADY EXIST (carried over from the project's earlier session): read them "
                               "first and change them; do not create them again:")
# Round 3b: an update of a running project lists the project's current files
EXISTING_FILES_NOTE_UPDATE = ("These files ALREADY EXIST (the project's current files): read a file before you change it; never "
                              "create one of them again or make a copy of it:")


def render_project_structure_md(dirs: List[str], planned_files: List[str], code_base_definitions: Any = None,
                                existing_files: Optional[List[str]] = None, python_project: Optional[bool] = None,
                                existing_note: str = EXISTING_FILES_NOTE_CARRIED) -> str:
    """
    Builds PROJECT_STRUCTURE.md: standards, then a tree of directories and planned files. Round 2o: planned files that
    already exist (a continued project's files carried over from its earlier session) are listed as existing, not as
    files to create. Round 2r: the pytest / conftest.py note only for a Python project (default: Python files are
    planned - the same rule that writes the framework conftest.py). Round 3q: + which tool tests which language when Python
    AND JavaScript files are planned.
    """
    existing = [f for f in planned_files if f in set(existing_files or [])]
    to_create = [f for f in planned_files if f not in existing]
    if python_project is None:
        python_project = any(str(f).endswith(".py") for f in planned_files)
    test_lines = PYTHON_TEST_LINES if python_project else OTHER_STACK_TEST_LINES
    if python_project and any(str(f).lower().endswith(_JAVASCRIPT_SUFFIXES) for f in planned_files):
        test_lines = PYTHON_TEST_LINES + MIXED_STACK_TEST_LINES  # Round 3q
    lines = [
        "# Project Directory Structure",
        "",
        "This file was auto-generated by the framework based on the PM's kickoff plan.",
        "**All workers MUST place their files within the directories listed below.**",
        "Do NOT create arbitrary new top-level directories without PM approval.",
        "Directories already exist. Files listed under 'Files To Create' do NOT exist yet: create each one with file_system 'write'",
        "at exactly that path. Paths never contain notes in brackets.",
        *test_lines,
        "",
    ]
    rendered = _render_definitions(code_base_definitions)
    if rendered:
        lines += ["## Code Base Definitions & Standards", ""] + rendered + [""]

    tree: Dict[str, Any] = {}
    for d in dirs:
        node = tree
        for part in d.split("/"):
            node = node.setdefault(part + "/", {})
    for f in planned_files:
        node = tree
        parts = f.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node.setdefault(parts[-1], None)

    def emit(node: Dict[str, Any], depth: int) -> None:
        for name in sorted(node, key=lambda n: (not n.endswith("/"), n.lower())):
            lines.append("  " * (depth + 1) + name)
            if isinstance(node[name], dict):
                emit(node[name], depth + 1)

    lines += ["## Directory Layout", "", "```", "./  (workspace root)"]
    emit(tree, 0)
    lines += ["```", ""]
    if to_create:
        # Round 1v: a separate list instead of 'name  (planned)' in the tree (W3 copied the bracket note into real paths)
        lines += ["## Files To Create", "", "These files are planned but do NOT exist yet:", ""]
        lines += [f"- {f}" for f in to_create] + [""]
    if existing:
        lines += ["## Existing Files", "", existing_note, ""]
        lines += [f"- {f}" for f in existing] + [""]
    return "\n".join(lines)


class PMKickoffWorkflow(BaseWorkflow):
    """
    Triggered by a PM agent in PM_STATE_STARTUP outputting a kickoff plan (JSON).
    This workflow processes the plan, creates tasks in TaskWarrior, stores the
    required roles, and transitions the PM to the team building state.
    """
    name: str = "pm_project_kickoff"
    trigger_tag_name: str = "kickoff_plan"  # PM directly outputs this new tag
    allowed_agent_type: Optional[str] = AGENT_TYPE_PM
    allowed_agent_state: Optional[str] = PM_STATE_STARTUP
    description: str = (
        "Processes a PM's kickoff plan from JSON input, creating tasks "
        "in TaskWarrior, storing the identified unique roles, auto-creating the "
        "project directory structure, and transitioning the PM to the team "
        "building state."
    )
    expected_json_schema: str = (
        "{\n"
        '  "roles": ["First Unique Role (e.g., Coder)", "Second Unique Role (e.g., Technical_Writer)"],\n'
        '  "tasks": [\n'
        '    {"id": "task_1", "description": "High-level kick-off task 1 description"},\n'
        '    {"id": "task_2", "depends_on": "task_1", "description": "High-level kick-off task 2 description"}\n'
        '  ],\n'
        '  "code_base_definitions": "Define the tech stack, code conventions, testing framework, and specific code base constraints here.",\n'
        '  "project_structure": [\n'
        '    {"dir": "src", "files": ["__init__.py", "main.py"]},\n'
        '    {"dir": "tests", "files": ["test_main.py"]},\n'
        '    {"dir": "docs"},\n'
        '    {"file": "README.md"}\n'
        '  ]\n'
        "}"
    )

    async def execute(
        self,
        manager: 'AgentManager',
        agent: 'Agent',  # This will be the PM agent
        data_input: Any  # This is the parsed JSON dictionary
    ) -> WorkflowResult:
        logger.info(f"Executing PMKickoffWorkflow for PM agent '{agent.agent_id}'.")

        if not isinstance(data_input, dict):
            return WorkflowResult(
                success=False,
                message="Invalid kickoff_plan data format. Expected a JSON object.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )

        # --- Extract Roles ---
        role_names: List[str] = data_input.get("roles", [])
        dropped_roles: List[str] = []
        if isinstance(role_names, list):
            role_names = [role_name_of(role) for role in role_names]  # Round 2i
            role_names, dropped_roles = sanitize_worker_roles(role_names)
            if dropped_roles:
                logger.warning(f"PMKickoffWorkflow: Dropped manager-type role(s) {dropped_roles} from kickoff_plan of agent '{agent.agent_id}' (the PM is the project manager).")

        # Round 3b: the kickoff of an UPDATE of a running project (src/workflows/project_update.py): the PM's existing team
        # does the work, so the plan needs no roles then
        project_update = getattr(agent, "_project_update", None)
        if not isinstance(project_update, dict):
            project_update = None
        existing_workers: List[Any] = []
        if project_update:
            from .project_update import team_workers
            existing_workers = team_workers(manager, agent)
            if existing_workers and not isinstance(role_names, list):
                role_names = []
            logger.info(f"PMKickoffWorkflow: kickoff of update {project_update.get('number')} for '{agent.agent_id}'; its team "
                        f"has {len(existing_workers)} worker(s).")

        if (not role_names or not isinstance(role_names, list)) and not existing_workers:
            logger.warning(f"PMKickoffWorkflow: No roles found in kickoff_plan from agent '{agent.agent_id}'.")
            return WorkflowResult(
                success=False,
                message=("No roles were provided in the kickoff_plan. Please provide a list of required roles." + (f" Manager roles ({', '.join(dropped_roles)}) are not allowed because you are already the project manager; list only worker roles such as 'Coder'." if dropped_roles else "")),
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )
        logger.info(f"PMKickoffWorkflow: Extracted {len(role_names)} unique roles for agent '{agent.agent_id}': {role_names}")

        # --- Extract Tasks ---
        tasks_list = data_input.get("tasks", [])
        task_descriptions: List[str] = []
        task_info_list: List[dict] = []
        
        if isinstance(tasks_list, list):
            for task_info in tasks_list:
                if isinstance(task_info, dict) and task_text_of(task_info):  # Round 2i: 'title' etc. count too
                    raw_desc = " ".join(task_text_of(task_info).splitlines()).strip()
                    dep_val = task_info.get("depends_on") or task_info.get("depends") or task_info.get("dependencies")
                    task_id_val = task_info.get("id") or task_info.get("task_id")
                    task_descriptions.append(raw_desc)
                    task_info_list.append({
                        "id": task_id_val,
                        "depends_on": dep_val,
                        "description": raw_desc
                    })
        
        if not task_descriptions:
            logger.warning(f"PMKickoffWorkflow: No tasks found in kickoff_plan from agent '{agent.agent_id}'.")
            return WorkflowResult(
                success=False,
                message="No tasks were provided in the kickoff_plan. Please provide a list of kick-off tasks.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )
        logger.info(f"PMKickoffWorkflow: Extracted {len(task_descriptions)} tasks for agent '{agent.agent_id}': {task_descriptions}")
        for note in infer_missing_dependencies(task_info_list):  # Round 3c
            logger.warning(f"PMKickoffWorkflow: kickoff plan of '{agent.agent_id}': {note}")

        created_tasks_info = []
        failed_tasks_info = []
        all_tasks_created_successfully = True

        project_context = agent.agent_config.get("config", {}).get("project_name_context")
        if not project_context:
            project_context = manager.current_project
            logger.warning(f"PMKickoffWorkflow: project_name_context not found in PM agent config. Using manager.current_project: {project_context}")
        
        if not project_context:
            logger.error(f"PMKickoffWorkflow: Critical error - project context is missing for PM '{agent.agent_id}'. Cannot create tasks.")
            return WorkflowResult(
                success=False,
                message="Critical error: Project context is missing for the PM agent. Cannot create tasks.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP, 
                next_agent_status=AGENT_STATUS_IDLE
            )

        # --- Mark Initial Project Task as Decomposed ---
        decomposed_kickoff_uuids: List[str] = []  # Round 2r
        try:
            from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
            if TASKLIB_AVAILABLE and project_context and manager.current_session:
                pm_tool = ProjectManagementTool()
                tw = pm_tool._get_taskwarrior_instance(project_context, manager.current_session)
                if tw:
                    # Find any pending tasks tagged with 'project_kickoff'
                    initial_tasks = tw.tasks.pending().filter('+project_kickoff', project=project_context)
                    for task in initial_tasks:
                        task['task_progress'] = 'decomposed'
                        task.save()
                        decomposed_kickoff_uuids.append(str(task['uuid']))
                        logger.info(f"PMKickoffWorkflow: Marked initial project task '{task['uuid']}' as decomposed.")
        except Exception as e:
            logger.warning(f"PMKickoffWorkflow: Failed to mark initial project task as decomposed: {e}")

        # Round 3s: Python tests planned for JavaScript code are JavaScript tests - fixed before the tasks are written
        test_renames: Dict[str, str] = {}
        try:
            test_renames, language_planned = self._javascript_test_plan(data_input.get("project_structure", []),
                                                                        task_descriptions)
            if test_renames:
                for task_info in task_info_list:
                    task_info["description"] = renamed_task_text(task_info["description"], test_renames, language_planned)
                task_descriptions = [task_info["description"] for task_info in task_info_list]
                logger.warning(f"PMKickoffWorkflow: kickoff plan of '{agent.agent_id}': Python test file(s) for JavaScript "
                               f"code planned as JavaScript tests: {test_renames}.")
            # Round 3y: "pytest" in a task that names JavaScript test files (nothing to rename) - its words are fixed too
            renamed_files = [test_renames.get(f, f) for f in language_planned]
            for task_info in task_info_list:
                fixed = test_language_task_text(task_info["description"], renamed_files)
                if fixed != task_info["description"]:
                    logger.warning(f"PMKickoffWorkflow: kickoff plan of '{agent.agent_id}': test task text corrected for the "
                                   f"test language: '{task_info['description'][:80]}'.")
                    task_info["description"] = fixed
            task_descriptions = [task_info["description"] for task_info in task_info_list]
        except Exception as language_err:
            logger.warning(f"PMKickoffWorkflow: test language check failed ({language_err}); plan kept as it is.")
            test_renames = {}

        # --- Create Kickoff Sub-tasks ---
        task_label = f"Update {project_update.get('number')} Task" if project_update else "Kick-off Task"  # Round 3b
        created_task_ids: set = set()      # plan ids (aliases) of tasks created in THIS attempt
        created_task_uuids: List[str] = [] # tasks this attempt really created (not skipped duplicates), for rollback
        for i, task_info in enumerate(task_info_list):
            task_desc = task_info["description"]
            task_tool_args = {
                "action": "add_task",
                "description": f"{task_label} {i+1}: {task_desc}",
                "priority": "H",
                "project_filter": project_context,
                "tags": ["kickoff", "pm_decomposed", f"task_order_{i+1}"] + ([f"update_{project_update.get('number')}"] if project_update else [])
            }
            if task_info.get("id"):
                # Always a string: the task tool only registers an alias for string ids, and a numeric "1" would
                # otherwise be read as a Taskwarrior row number by later dependencies.
                task_tool_args["task_id"] = str(task_info["id"]).strip()
            if task_info.get("depends_on"):
                # Round 1n: keep only dependencies on tasks created earlier in THIS plan. An invented id such as
                # "temp_structure_init" (observed 2026-10-15 15:17:54) used to make the task tool fail, which failed the
                # whole kickoff after two tasks already existed; the retry then created a second, different set.
                kept_deps, dropped_deps = self._sanitize_kickoff_dependencies(task_info["depends_on"], created_task_ids)
                if dropped_deps:
                    logger.warning(f"PMKickoffWorkflow: Dropped unknown dependency id(s) {dropped_deps} for task '{task_desc[:60]}' (not tasks created earlier in this plan).")
                if kept_deps:
                    task_tool_args["depends"] = ",".join(kept_deps)
            try:
                logger.debug(f"PMKickoffWorkflow: Attempting to create task via ToolExecutor: {task_tool_args}")
                task_result = await manager.tool_executor.execute_tool(
                    agent_id=agent.agent_id, 
                    agent_sandbox_path=agent.sandbox_path,
                    tool_name="project_management",
                    tool_args=task_tool_args,
                    project_name=project_context, 
                    session_name=manager.current_session,
                    manager=manager 
                )

                if isinstance(task_result, dict) and task_result.get("status") == "success":
                    task_id = task_result.get("task_id", "N/A")
                    task_uuid = task_result.get("task_uuid", "N/A")
                    created_tasks_info.append(f"Task '{task_desc[:30]}...' (ID: {task_id}, UUID: {task_uuid})")
                    if task_info.get("id"):
                        created_task_ids.add(str(task_info["id"]).strip())
                    # The task tool answers "success" for a skipped near-duplicate and returns the EXISTING task's uuid:
                    # that task is not ours and must never be rolled back.
                    if "skipped to prevent duplication" not in str(task_result.get("message", "")).lower() and task_uuid != "N/A":
                        created_task_uuids.append(str(task_uuid))
                    logger.info(f"PMKickoffWorkflow: Successfully created task (ID: {task_id}): {task_desc}")
                else:
                    all_tasks_created_successfully = False
                    error_detail = task_result.get("message", "Unknown error") if isinstance(task_result, dict) else str(task_result)
                    failed_tasks_info.append(f"Task '{task_desc[:30]}...': {error_detail}")
                    logger.error(f"PMKickoffWorkflow: Failed to create task '{task_desc}': {error_detail}. Full result: {task_result}")

            except Exception as e:
                all_tasks_created_successfully = False
                failed_tasks_info.append(f"Task '{task_desc[:30]}...': Exception - {str(e)}")
                logger.error(f"PMKickoffWorkflow: Exception creating task '{task_desc}': {e}", exc_info=True)

        # --- Create Project Directory Structure ---
        structure_list = data_input.get("project_structure", [])
        code_base_definitions = data_input.get("code_base_definitions")
        # Round 2a: files the kick-off tasks name with a directory (e.g. 'tests/test_x.py') are planned files too. On
        # 2026-10-18 the structure listed only 'src/': no tests/ directory and no conftest.py were created.
        from src.utils.task_files import created_root_files, file_paths_in_text
        task_named_files: List[str] = []
        root_files_to_create: List[str] = []  # Round 2v: "Create server.py ..." - a file in the workspace root
        for task_text in task_descriptions:
            for named_path in file_paths_in_text(task_text, with_directory_only=True):
                if named_path not in task_named_files:
                    task_named_files.append(named_path)
            for root_name in created_root_files(task_text):
                if root_name not in root_files_to_create:
                    root_files_to_create.append(root_name)
        
        created_dirs: List[str] = []
        if (structure_list or task_named_files or root_files_to_create or project_update) and project_context and manager.current_session:
            workspace_path = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_context), str(manager.current_session))
            try:
                structure_items = list(structure_list) if isinstance(structure_list, list) else ([structure_list] if structure_list else [])
                planned_dirs, planned_files, notes = plan_project_structure(structure_items + task_named_files)
                planned_dirs, planned_files, layout_notes = normalize_planned_layout(planned_dirs, planned_files)
                notes = list(notes) + layout_notes
                planned_before_drops = list(planned_files)  # Round 3o
                # Round 2y: one file, two paths (structure vs. task text): keep the task's path
                structure_only = normalize_planned_layout(*plan_project_structure(structure_items)[:2])[1]
                task_only = normalize_planned_layout(*plan_project_structure(task_named_files)[:2])[1]
                dropped_files, duplicate_notes = duplicate_structure_files(structure_only, task_only)
                planned_files = [f for f in planned_files if f not in dropped_files]
                notes += duplicate_notes
                planned_files = with_root_files(planned_files, root_files_to_create)  # Round 2v
                if test_renames:  # Round 3s
                    renamed_files: List[str] = []
                    for planned in planned_files:
                        target = test_renames.get(planned, planned)
                        if target not in renamed_files:
                            renamed_files.append(target)
                    planned_files = renamed_files
                    python_code = [f for f in planned_files if f.endswith(".py") and not _TEST_PATH_RE.search(f)]
                    language_note_3s = MIXED_TEST_LANGUAGE_NOTE if python_code else JS_TEST_LANGUAGE_NOTE
                    notes.append("Python test file(s) for JavaScript code planned as JavaScript tests: "
                                 + ", ".join(f"{old} -> {new}" for old, new in test_renames.items()) + ".")
                    code_base_definitions = (f"{code_base_definitions}\n\n{language_note_3s}" if isinstance(code_base_definitions, str)
                                             else [code_base_definitions, language_note_3s] if code_base_definitions
                                             else language_note_3s)
                planned_files, language_note = align_test_language(planned_files)  # Round 3c
                if language_note:
                    notes.append("the code is JavaScript: the planned Python test files were dropped (JavaScript tests instead).")
                    code_base_definitions = (f"{code_base_definitions}\n\n{language_note}" if isinstance(code_base_definitions, str)
                                             else [code_base_definitions, language_note] if code_base_definitions else language_note)
                # Round 3o: no empty directories for the files that were dropped
                all_dropped = list(dropped_files) + [f for f in planned_before_drops if f not in planned_files
                                                     and f not in dropped_files]
                planned_dirs, prune_notes = prune_dirs_of_dropped_files(planned_dirs, planned_files, all_dropped)
                notes += prune_notes
                for note in notes:
                    logger.warning(f"PMKickoffWorkflow: project_structure from PM '{agent.agent_id}': {note}")
                created_dirs = self._create_directory_structure(planned_dirs, workspace_path)
                self._write_test_scaffold(workspace_path, planned_files)
                listed_files, existing_note = planned_files, EXISTING_FILES_NOTE_CARRIED
                if project_update:
                    # Round 3b: the manifest of an update lists the project's current files too (as existing files)
                    from src.utils.project_folders import work_files
                    listed_files = planned_files + [f for f in work_files(workspace_path) if f not in planned_files]
                    existing_note = EXISTING_FILES_NOTE_UPDATE
                if created_dirs or listed_files:
                    # Generate PROJECT_STRUCTURE.md manifest (directories + planned files)
                    self._write_project_structure_md(workspace_path, created_dirs, code_base_definitions, listed_files,
                                                     existing_note=existing_note)
                    logger.info(f"PMKickoffWorkflow: Auto-created {len(created_dirs)} directories, recorded {len(planned_files)} planned files and wrote PROJECT_STRUCTURE.md for project '{project_context}'.")
                else:
                    logger.info("PMKickoffWorkflow: project_structure was present but contained no directories or files.")
            except Exception as e:
                logger.error(f"PMKickoffWorkflow: Failed to create project directory structure: {e}", exc_info=True)
                # Non-fatal — continue with the rest of the kickoff
        else:
            logger.warning(f"PMKickoffWorkflow: No project_structure section found in kickoff plan from PM '{agent.agent_id}'. Skipping directory auto-creation.")

        if all_tasks_created_successfully:
            logger.info(f"PMKickoffWorkflow: All tasks created for PM '{agent.agent_id}'. Preparing successful result with reschedule.")
            agent.clear_history()
            agent._last_system_prompt_state = None  # Force fresh prompt generation after history clear
            logger.info(f"PMKickoffWorkflow: Cleared history for PM agent '{agent.agent_id}' before transitioning to PM_STATE_BUILD_TEAM_TASKS.")

            # Store the identified roles and tasks on the agent for the next state
            agent.kick_off_roles = role_names
            agent.kick_off_tasks = task_descriptions
            agent.target_worker_agents_for_build = len(role_names)  # *** FIX: Set the target number of agents to build ***
            agent.successfully_created_agent_count_for_build = 0 # Reset counter
            logger.info(f"PMKickoffWorkflow: Stored {len(role_names)} roles, {len(task_descriptions)} tasks, and set target agent count to {len(role_names)} on agent '{agent.agent_id}'.")

            # --- BEGIN MODIFICATION: Mark initial Admin AI task as done ---
            initial_project_task_uuid_to_complete = None
            # Round 2r: a kick-off task marked 'decomposed' above stays open as the parent of the kick-off tasks; the lookup
            # below hides decomposed tasks and only logged "No suitable non-completed initial task found" (every run).
            if decomposed_kickoff_uuids:
                logger.info(f"PMKickoffWorkflow: Initial project task(s) {decomposed_kickoff_uuids} are marked 'decomposed' and "
                            "stay open as the parent of the kick-off tasks; nothing to complete.")
            else:
                logger.info(f"PMKickoffWorkflow: Attempting to find and complete the initial project plan task for project '{project_context}' assigned to PM '{agent.agent_id}'.")
                try:
                    list_tasks_args = {
                        "action": "list_tasks",
                        "project_filter": project_context,
                        "tags_filter": "project_kickoff,auto_created_by_framework"
                    }
                    logger.debug(f"PMKickoffWorkflow: Listing tasks with args: {list_tasks_args}")
                    list_result = await manager.tool_executor.execute_tool(
                        agent_id="framework", # System action
                        agent_sandbox_path=agent.sandbox_path, # PM's sandbox for context
                        tool_name="project_management",
                        tool_args=list_tasks_args,
                        project_name=project_context,
                        session_name=manager.current_session,
                        manager=manager
                    )

                    if isinstance(list_result, dict) and list_result.get("status") == "success":
                        tasks = list_result.get("tasks", [])
                        logger.debug(f"PMKickoffWorkflow: Found {len(tasks)} candidate tasks for initial project plan task.")
                        found_matching_tasks = []
                        for task_item in tasks:
                            if task_item.get("status") != "completed":
                                found_matching_tasks.append(task_item)

                        if len(found_matching_tasks) > 1:
                            logger.warning(f"PMKickoffWorkflow: Multiple ({len(found_matching_tasks)}) non-completed initial tasks found for PM '{agent.agent_id}' in project '{project_context}'. Will attempt to complete the first one found: {found_matching_tasks[0].get('uuid')}")

                        if found_matching_tasks:
                            initial_project_task_uuid_to_complete = found_matching_tasks[0].get("uuid")
                            logger.info(f"PMKickoffWorkflow: Identified initial project plan task to complete. UUID: {initial_project_task_uuid_to_complete}, Description: '{found_matching_tasks[0].get('description', '')[:50]}...'")
                        else:
                            logger.warning(f"PMKickoffWorkflow: No suitable non-completed initial task found for PM '{agent.agent_id}' in project '{project_context}'. It might have been completed manually or does not exist.")
                    else:
                        error_msg = list_result.get("message", "Unknown error") if isinstance(list_result, dict) else str(list_result)
                        logger.error(f"PMKickoffWorkflow: Failed to list tasks to find initial project plan task: {error_msg}")

                except Exception as e_list:
                    logger.error(f"PMKickoffWorkflow: Exception while trying to list tasks for initial project plan task: {e_list}", exc_info=True)

            if initial_project_task_uuid_to_complete:
                try:
                    complete_task_args = {
                        "action": "complete_task",
                        "task_id": initial_project_task_uuid_to_complete
                    }
                    logger.debug(f"PMKickoffWorkflow: Attempting to complete task with args: {complete_task_args}")
                    complete_result = await manager.tool_executor.execute_tool(
                        agent_id="framework", # System action
                        agent_sandbox_path=agent.sandbox_path, # PM's sandbox for context
                        tool_name="project_management",
                        tool_args=complete_task_args,
                        project_name=project_context,
                        session_name=manager.current_session,
                        manager=manager
                    )
                    if isinstance(complete_result, dict) and complete_result.get("status") == "success":
                        logger.info(f"PMKickoffWorkflow: Successfully marked initial project plan task '{initial_project_task_uuid_to_complete}' as completed.")
                    else:
                        error_msg = complete_result.get("message", "Unknown error") if isinstance(complete_result, dict) else str(complete_result)
                        logger.error(f"PMKickoffWorkflow: Failed to complete initial project plan task '{initial_project_task_uuid_to_complete}': {error_msg}")
                except Exception as e_complete:
                    logger.error(f"PMKickoffWorkflow: Exception while trying to complete initial project plan task '{initial_project_task_uuid_to_complete}': {e_complete}", exc_info=True)
            # --- END MODIFICATION ---

            if project_update:
                # Round 3b: the update's tasks go to the PM's EXISTING team: straight to pm_activate_workers (which starts
                # with a fresh history and lists the unassigned tasks and the team itself). A team without workers is built
                # first, as for a new project.
                agent._project_update = None
                agent._last_project_update = project_update
                if existing_workers:
                    from .project_update import worker_labels
                    labels = ", ".join(worker_labels(existing_workers))
                    agent.target_worker_agents_for_build = len(existing_workers)
                    logger.info(f"PMKickoffWorkflow: update {project_update.get('number')} of '{project_context}': "
                                f"{len(task_descriptions)} task(s) created; the existing team ({labels}) does them.")
                    return WorkflowResult(
                        success=True,
                        message=(f"Update {project_update.get('number')}: all {len(task_descriptions)} tasks created by PM "
                                 f"'{agent.agent_id}'; its team ({labels}) carries them out. Details: {'; '.join(created_tasks_info)}"),
                        workflow_name=self.name,
                        next_agent_state=PM_STATE_ACTIVATE_WORKERS,
                        next_agent_status=AGENT_STATUS_IDLE,
                        ui_message_data={
                            "type": "info",
                            "agent_id": agent.agent_id,
                            "content": (f"PM '{agent.agent_id}' planned update {project_update.get('number')} with "
                                        f"{len(task_descriptions)} tasks for its team ({labels}). Moving to assign them."),
                        },
                        tasks_to_schedule=[(agent, 0)]
                    )

            # NOTE: create_team and tool_information auto-execution has been REMOVED
            # from this workflow. The cycle_handler.py handles these operations when
            # the PM naturally calls those tools, preventing double-execution that
            # caused confusion and duplicate actions.
            team_id = f"team_{project_context}"

            # Build the kickoff plan summary as a JSON block
            first_role_to_create = role_names[0] if role_names else "Worker"
            plan_summary = json.dumps({"roles": role_names, "tasks": task_info_list}, indent=2)

            final_directive_content = (
                "[Framework System Message]\n"
                "Your previous state 'pm_startup' and its associated plan are complete.\n\n"
                "**MASTER KICKOFF PLAN SUMMARY**\n"
                f"```json\n{plan_summary}\n```\n\n"
                f"You are NOW in state 'pm_build_team_tasks'.\n\n"
                f"**YOUR WORKFLOW:**\n"
                f'Step 1: Create a team using the manage_team tool with action "create_team" and team_id "{team_id}"\n'
                f"Step 2: The framework will automatically retrieve create_agent tool info for you.\n"
                f"Step 3: Create one worker agent per role listed above. Do NOT create duplicate roles.\n"
                f"Step 4: Once all roles are filled, request state change to 'pm_activate_workers'.\n\n"
                f"Your MANDATORY FIRST ACTION is Step 1: Create the team '{team_id}'.\n"
                f"Your FIRST role to create after the team is formed will be: **'{first_role_to_create}'**."
            )

            agent.message_history.append({"role": "user", "content": final_directive_content})
            logger.info(f"PMKickoffWorkflow: Injected directive user message for PM agent '{agent.agent_id}' for state PM_STATE_BUILD_TEAM_TASKS, detailing {len(task_descriptions)} kick-off tasks with explicit numbering.")

            return WorkflowResult(
                success=True,
                message=f"All {len(task_descriptions)} kick-off tasks created successfully by PM '{agent.agent_id}'. Details: {'; '.join(created_tasks_info)}",
                workflow_name=self.name,
                next_agent_state=PM_STATE_BUILD_TEAM_TASKS,
                next_agent_status=AGENT_STATUS_IDLE,
                ui_message_data={
                    "type": "info", 
                    "agent_id": agent.agent_id, 
                    "content": f"PM '{agent.agent_id}' successfully decomposed plan and created {len(task_descriptions)} kick-off tasks. Stored task count for build phase. Moving to build team."
                },
                tasks_to_schedule=[(agent, 0)]
            )
        else:
            # Round 1n: the PM will resubmit the whole plan after a failure. Remove what this attempt created, otherwise
            # the retry (usually with different task wording) leaves duplicates behind.
            removed_count = self._rollback_created_tasks(project_context, manager.current_session, created_task_uuids)
            error_summary = f"PM '{agent.agent_id}' failed to create some kick-off tasks. Successfully created: {len(created_tasks_info)}. Failed: {len(failed_tasks_info)} - Details: {'; '.join(failed_tasks_info)}"
            if removed_count:
                error_summary += f" The {removed_count} task(s) created during this attempt were removed again; submit the COMPLETE kickoff plan again."
            logger.warning(f"PMKickoffWorkflow: Some tasks failed for PM '{agent.agent_id}'. Preparing failure result with reschedule. Summary: {error_summary}")
            return WorkflowResult(
                success=False,
                message=error_summary,
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP, 
                next_agent_status=AGENT_STATUS_IDLE,
                ui_message_data={"type": "error", "agent_id": agent.agent_id, "content": error_summary},
                tasks_to_schedule=[(agent, 0)] 
            )

    @staticmethod
    def _sanitize_kickoff_dependencies(raw: Any, created_ids: set) -> Tuple[List[str], List[str]]:
        """
        Splits a kickoff task's depends_on (string, comma list, JSON-ish list) into (kept, dropped). Only ids of tasks
        created earlier in the same plan are kept; anything else (invented ids, Taskwarrior row numbers, stale
        aliases) is dropped so the task is still created, just without that dependency.
        """
        items = [str(x) for x in raw] if isinstance(raw, (list, tuple, set)) else [str(raw)]
        tokens: List[str] = []
        for item in items:
            cleaned = item.replace("[", "").replace("]", "").replace('"', "").replace("'", "")
            tokens.extend(t.strip() for t in cleaned.split(",") if t.strip())
        kept: List[str] = []
        dropped: List[str] = []
        for token in [t for raw_token in tokens for t in expand_task_range(raw_token)]:
            if token.lower() in ("none", "null"):
                continue
            if token in kept:
                continue
            (kept if token in created_ids else dropped).append(token)
        return kept, dropped

    def _rollback_created_tasks(self, project_context: Any, session: Any, uuids: List[str]) -> int:
        """Deletes tasks created by a failed kickoff attempt and their aliases. Returns how many were removed."""
        if not uuids or not project_context or not session:
            return 0
        removed = 0
        try:
            from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
            if not TASKLIB_AVAILABLE:
                return 0
            pm_tool = ProjectManagementTool()
            tw = pm_tool._get_taskwarrior_instance(project_context, session)
            if not tw:
                return 0
            for task_uuid in uuids:
                try:
                    tw.tasks.get(uuid=task_uuid).delete()
                    removed += 1
                except Exception as e:
                    logger.warning(f"PMKickoffWorkflow: Rollback could not delete task '{task_uuid}': {e}")
            try:
                aliases = pm_tool._load_aliases(project_context, session)
                stale = [name for name, target in aliases.items() if target in set(uuids)]
                for name in stale:
                    aliases.pop(name, None)
                if stale:
                    pm_tool._save_aliases(project_context, session, aliases)
            except Exception as e:
                logger.warning(f"PMKickoffWorkflow: Rollback could not clean task aliases: {e}")
            logger.warning(f"PMKickoffWorkflow: Rolled back {removed} of {len(uuids)} task(s) created by the failed kickoff attempt.")
        except Exception as e:
            logger.warning(f"PMKickoffWorkflow: Rollback of failed kickoff attempt failed: {e}")
        return removed

    def _create_directory_structure(self, planned_dirs: List[str], workspace_path: Path) -> List[str]:
        """
        Creates the planned directories (already parsed and sanitised by
        plan_project_structure) under the shared workspace. Never creates a
        directory over an existing file. Returns the relative paths created or
        already present.
        """
        created: List[str] = []
        root = workspace_path.resolve()
        for rel in planned_dirs:
            target = (workspace_path / rel).resolve()
            if not (target == root or target.is_relative_to(root)):
                logger.warning(f"PMKickoffWorkflow: Skipping directory outside workspace: {rel}")
                continue
            if target.exists() and not target.is_dir():
                logger.warning(f"PMKickoffWorkflow: Not creating directory '{rel}': a file with that name exists.")
                continue
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as e:  # e.g. a parent path component is a file
                logger.warning(f"PMKickoffWorkflow: Could not create directory '{rel}': {e}")
                continue
            created.append(rel)
            logger.debug(f"PMKickoffWorkflow: Created directory: {rel}")
        return created

    @staticmethod
    def _javascript_test_plan(structure: Any, task_descriptions: List[str]) -> Tuple[Dict[str, str], List[str]]:
        """Round 3s: (javascript_test_renames of the planned files, the planned files) from the plan's structure and the
        files its task texts name - the same layout the structure step builds later."""
        from src.utils.task_files import file_paths_in_text
        named: List[str] = []
        for text in task_descriptions:
            for path in file_paths_in_text(text, with_directory_only=True):
                if path not in named:
                    named.append(path)
        items = list(structure) if isinstance(structure, list) else ([structure] if structure else [])
        dirs, files, _notes = plan_project_structure(items + named)
        _dirs, files, _layout = normalize_planned_layout(dirs, files)
        return javascript_test_renames(files), files

    def _write_test_scaffold(self, workspace_path: Path, planned_files: List[str]) -> None:
        """Writes the framework-owned root conftest.py when Python files are planned (never over an existing one)."""
        try:
            if not any(f.endswith(".py") for f in planned_files):
                return
            conftest = workspace_path / "conftest.py"
            if conftest.exists():
                refresh_framework_conftest(workspace_path)  # Round 3r: an earlier framework version is updated
                return
            workspace_path.mkdir(parents=True, exist_ok=True)
            conftest.write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
            logger.info(f"PMKickoffWorkflow: Wrote framework-owned conftest.py to {conftest}")
        except OSError as e:
            logger.warning(f"PMKickoffWorkflow: Could not write conftest.py: {e}")

    def _write_project_structure_md(self, workspace_path: Path, created_dirs: List[str],
                                    code_base_definitions: Any = None,
                                    planned_files: Optional[List[str]] = None,
                                    existing_note: str = EXISTING_FILES_NOTE_CARRIED) -> None:
        """
        Write a PROJECT_STRUCTURE.md file into the workspace root that documents
        the pre-defined directory layout and planned files. Workers are instructed
        to read this file before creating any new files or directories.
        """
        existing_files = [f for f in (planned_files or []) if (workspace_path / f).is_file()]  # Round 2o: a continued project
        # Round 2r: the pytest / conftest.py note only when that conftest.py is there or Python files are planned
        python_project = (any(str(f).endswith(".py") for f in (planned_files or []))
                          or (workspace_path / "conftest.py").is_file())
        content = render_project_structure_md(created_dirs, planned_files or [], code_base_definitions, existing_files,
                                              python_project=python_project, existing_note=existing_note)
        workspace_path.mkdir(parents=True, exist_ok=True)
        md_path = workspace_path / "PROJECT_STRUCTURE.md"
        md_path.write_text(content, encoding="utf-8")
        logger.info(f"PMKickoffWorkflow: Wrote PROJECT_STRUCTURE.md to {md_path}")
# END OF FILE src/workflows/pm_kickoff_workflow.py
