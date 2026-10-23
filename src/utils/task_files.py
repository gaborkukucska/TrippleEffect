# START OF FILE src/utils/task_files.py
"""
Files named in task descriptions (Round 2a).

2026-10-18 07:11-16:29 (Temp_Converter_CLI): all four kick-off tasks ended 'finished', but src/main.py held only a
docstring (main() had no code) and tests/test_temp_convert.py held test functions with docstrings and no asserts. PM1 marked
tasks 2 and 3 finished because the files EXISTED. The write tool had already reported both defects ("contains only a
docstring and no code"), but nothing stopped the tasks from being closed.

* file_paths_in_text(): paths such as 'src/main.py' or 'tests/test_x.py' in a task description.
* task_completion_problems(): defects of the existing Python files a task names; a task with problems must not be
  marked finished.
* Round 2n: task_completion_problems() also checks the Python files the assignee changed while working on the task
  (src/utils/file_authorship.py). 2026-10-20 23:26:25 PM1 marked "Kick-off Task 1: Setup Flask backend, SQLite schema
  ... in data/scores.db" finished: the description named no .py file, so src/app.py, which W1 had written for it and which
  used the undefined name 'current_direction', was never checked.
* The kick-off also uses file_paths_in_text(): when the PM's project_structure lists no files, the files named in the
  kick-off tasks are the planned files (that run had no tests/ directory and no conftest.py, while PROJECT_STRUCTURE.md
  said a framework conftest.py existed).
* Round 3d: the links between files are checked too (src/tools/project_links_check.py): a test file that loads no project
  file (2026-10-23: tests/test_gameLogic.js tested its own copy of the game logic for two hours), a JavaScript module
  loaded for its value that exports nothing, and an npm package that package.json does not list ('sqlite3').
* Round 2r: JavaScript (.js/.mjs/.cjs), JSON and shell (.sh/.bash) files are checked too (src/tools/source_check.py).
  2026-10-21 19:31:22 Kick-off Task 2 ("Implement WebSocket handlers in game-server/game-engine.js ...") was marked
  finished although game-engine.js failed `node --check` with "SyntaxError: Unexpected token 'default'".
"""
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

PATH_EXTENSIONS = ("py", "md", "txt", "rst", "json", "toml", "cfg", "ini", "yaml", "yml", "js", "ts", "html", "css", "sh", "csv")
_FILE_PATH_RE = re.compile(
    r"(?<![\w./\\-])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:" + "|".join(PATH_EXTENSIONS) + r"))(?![\w/-])"
)
_SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".mypy_cache"})
MAX_PROBLEMS = 8


def file_paths_in_text(text: Optional[str], with_directory_only: bool = False) -> List[str]:
    """Relative file paths in text, in order, without duplicates. Absolute paths and paths with '..' are ignored."""
    found: List[str] = []
    for match in _FILE_PATH_RE.finditer(str(text or "")):
        raw = match.group(1)
        path = raw[2:] if raw.startswith("./") else raw
        parts = PurePosixPath(path).parts
        if not parts or ".." in parts or path.startswith("/") or path.startswith("."):
            continue
        if with_directory_only and len(parts) < 2:
            continue
        if path not in found:
            found.append(path)
    return found


# Round 2v: a file in the workspace ROOT that a task says to create. 2026-10-22 10:29 the kick-off tasks said "Create server.py
# ...", "Create index.html file ..." and "Create README.md ...", but only tests/test_game.py (a path with a directory) became
# a planned file: PROJECT_STRUCTURE.md listed no server.py, and W1 first tried 'backend/server.py'.
_CREATED_ROOT_FILE_RE = re.compile(
    r"\b(?:create|write|build|implement|add|generate|make|develop)\s+"
    r"(?:(?:a|an|the|new|single|one|simple|basic|concise|minimal|small)\s+)*(?:file\s+)?[`'\"]?"
    r"(?P<name>[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:" + "|".join(PATH_EXTENSIONS) + r"))[`'\"]?(?![\w/.-])",
    re.IGNORECASE)


def created_root_files(text: Optional[str]) -> List[str]:
    """Round 2v: file names WITHOUT a directory that the text says to create/write/build ... ("Create server.py ...")."""
    found: List[str] = []
    for match in _CREATED_ROOT_FILE_RE.finditer(str(text or "")):
        name = match.group("name")
        if name.startswith(".") or name in found:
            continue
        found.append(name)
    return found


def find_in_workspace(workspace: Path, rel_path: str) -> Optional[Path]:
    """The file rel_path in workspace; a bare file name is also found in a sub-directory when exactly one file has it."""
    try:
        root = Path(workspace).resolve()
        candidate = (root / rel_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return candidate
        if "/" in rel_path:
            return None
        hits = [p for p in root.rglob(rel_path)
                if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & _SKIP_DIRS)]
        return hits[0] if len(hits) == 1 else None
    except (OSError, ValueError):
        return None


def python_file_problems(path: Path, shown_name: str, workspace: Optional[Path] = None) -> List[str]:
    """Defects of one Python file: empty, not parseable, or the notes of python_code_notes()."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        return [f"{shown_name}: cannot be read ({err})."]
    if not source.strip():
        return [f"{shown_name} is EMPTY."]
    from src.tools.python_static_check import parse_python  # Round 2m: quiet parse
    try:
        parse_python(source)
    except SyntaxError as err:
        return [f"{shown_name} is not valid Python ({err.msg} at line {err.lineno})."]
    from src.tools.python_static_check import python_code_notes_for
    return [f"{shown_name}: {note}" for note in python_code_notes_for(source, shown_name, workspace)]


def task_completion_problems(description: Optional[str], workspace: Optional[Path],
                             changed_files: Optional[Iterable[Path]] = None) -> List[str]:
    """
    Problems that must be fixed before a task may be marked finished: defects in the EXISTING Python files the task
    description names with a directory ('src/main.py'). Files that do not exist are not checked (a description may name
    files of other tasks), and bare names are skipped: they are usually usage examples ('python temp_convert.py 100 C')
    of another task's file. Round 2n: changed_files are Python files the assignee changed while working on this task;
    they are checked as well (the framework root conftest.py excepted). Round 2r: the same for JavaScript, JSON and shell
    files (syntax only, src/tools/source_check.py).
    """
    if not workspace or (not description and not changed_files):
        return []
    from src.tools.source_check import check_kind, file_problem
    problems: List[str] = []
    try:
        root = Path(workspace).resolve()

        def checked(rel_or_name: str) -> bool:
            if rel_or_name.endswith(".py"):
                return PurePosixPath(rel_or_name).name != "conftest.py"
            return check_kind(rel_or_name) is not None

        to_check: List[Path] = []
        for rel_path in file_paths_in_text(description or "", with_directory_only=True):
            if not checked(rel_path):
                continue
            path = find_in_workspace(root, rel_path)
            if path is not None and path not in to_check:
                to_check.append(path)
        for changed in changed_files or []:
            try:
                path = Path(changed).resolve()
                if (not checked(str(path).replace("\\", "/")) or not path.is_file() or not path.is_relative_to(root)
                        or path.relative_to(root) == Path("conftest.py")):
                    continue
            except (OSError, ValueError):
                continue
            if path not in to_check:
                to_check.append(path)
        from src.tools.project_links_check import link_problems  # Round 3d
        for path in to_check:
            shown = str(path.relative_to(root)).replace("\\", "/")
            if path.suffix == ".py":
                found = python_file_problems(path, shown, root)
            else:
                other = file_problem(path, shown)
                found = [other] if other else []
            if not found:
                # Round 3d: a test that tests a copy, a module that exports nothing, an npm package package.json lacks
                found = link_problems(root, path, shown)
            for problem in found:
                if problem not in problems:
                    problems.append(problem)
    except Exception as err:  # the check must never break task updates
        logger.warning(f"task_completion_problems: check failed ({err}); not blocking.")
        return []
    return problems[:MAX_PROBLEMS]


def completion_refusal_message(task_label: str, problems: Iterable[str]) -> str:
    lines = "\n".join(f"- {p}" for p in problems)
    return (f"Task '{task_label}' was NOT marked finished: its files still have these defects:\n{lines}\n"
            "Fix them first (file_system 'write' / 'replace_lines' or code_editor), then mark the task finished again. "
            "A PM should send the task back to its worker with send_message instead of marking it finished.")

# END OF FILE src/utils/task_files.py
