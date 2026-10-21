# START OF FILE src/tools/source_check.py
"""
Syntax checks for JavaScript, JSON and shell files (Round 2r).

2026-10-21 19:21-19:31 (WiFi_Snake_Multiplayer_Game, qwen3.5:9b): W1 wrote game-server/game-engine.js with a 'default:'
outside its switch block. Every 'write' answered "Successfully wrote ..." and every identical rewrite "already exists and
contains the exact same content ... considered successful": W1 rewrote the broken file 8 times, and at 19:31:22 Kick-off
Task 2 was marked finished although `node --check` failed with "SyntaxError: Unexpected token 'default'". Only Python files
were checked (src/tools/python_static_check.py, src/utils/task_files.py).

* check_source(source, filename): the syntax error of a .js/.mjs/.cjs file (`node --check`), a .json file (json module) or a
  .sh/.bash file (`bash -n`), or None. None also when the checker is not installed or cannot judge the file (JSX; JSON
  with comments such as tsconfig.json; a script for another shell): a check must never block valid work.
* write_note / edit_note / unchanged_note: the '[Framework check ...]' text for a tool result (the file IS saved: unlike a
  Python SyntaxError, the external check does not reject the write).
* file_problem(path, shown): a one-line defect for the task completion check (src/utils/task_files.py).

`node --check` on a .js file that uses import/export reports success even for broken code (Node 22 module detection), so
such a file is checked as an ES module (.mjs copy).
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional

logger = logging.getLogger(__name__)

JAVASCRIPT = "JavaScript"
JSON_KIND = "JSON"
SHELL = "shell script"
_JS_SUFFIXES = {".js": ".js", ".mjs": ".mjs", ".cjs": ".cjs"}
_SHELL_SUFFIXES = (".sh", ".bash")
_SHELLS = frozenset({"sh", "bash", "dash"})
_JSONC_NAME_RE = re.compile(r"^(?:tsconfig|jsconfig)(?:\..+)?\.json$", re.IGNORECASE)
_ESM_RE = re.compile(r"^[ \t]*(?:import[ \t]*[\w{*'\"]|export[ \t]*[\w{*])", re.MULTILINE)
_MODULE_ONLY_ERRORS = ("cannot use import statement outside a module", "unexpected token 'export'",
                       "await is only valid in async functions and the top level bodies of modules",
                       "cannot use 'import.meta' outside a module")
MAX_CHECK_BYTES = 1_000_000
CHECK_TIMEOUT_SECONDS = 15
CONTEXT_LINES_BEFORE = 2
_missing_checker_logged = set()


@dataclass
class SyntaxIssue:
    kind: str                 # JAVASCRIPT, JSON_KIND or SHELL
    message: str              # e.g. "SyntaxError: Unexpected token 'default'"
    line: Optional[int] = None
    column: Optional[int] = None  # 0-based
    width: int = 1

    def compact(self, shown_name: str) -> str:
        where = f" at line {self.line}" if self.line else ""
        return f"{shown_name} is not valid {self.kind} ({self.message}{where})."

    def detailed(self, source: str) -> str:
        where = f" (line {self.line})" if self.line else ""
        text = f"{self.message}{where}"
        lines = source.splitlines()
        if self.line and 1 <= self.line <= len(lines):
            width = len(str(self.line))
            for number in range(max(1, self.line - CONTEXT_LINES_BEFORE), self.line + 1):
                text += f"\n  {number:>{width}} | {lines[number - 1]}"
            if self.column is not None:
                text += "\n" + " " * (2 + width + 3 + self.column) + "^" * max(1, self.width)
        return text


def check_kind(filename: Optional[str]) -> Optional[str]:
    """The kind of check for this file name (JAVASCRIPT, JSON_KIND, SHELL) or None."""
    name = str(filename or "").replace("\\", "/")
    lower = name.lower()
    suffix = PurePosixPath(lower).suffix
    if suffix in _JS_SUFFIXES:
        return JAVASCRIPT
    if suffix == ".json":
        base = PurePosixPath(name).name
        if _JSONC_NAME_RE.match(base) or "/.vscode/" in f"/{lower}":
            return None  # JSON with comments
        return JSON_KIND
    if lower.endswith(_SHELL_SUFFIXES):
        return SHELL
    return None


def _executable(name: str) -> Optional[str]:
    path = shutil.which(name)
    if not path and name not in _missing_checker_logged:
        _missing_checker_logged.add(name)
        logger.info(f"source_check: '{name}' is not installed; files it would check are not syntax-checked.")
    return path


def _run_checker(command: List[str], source: str, suffix: str) -> Optional[subprocess.CompletedProcess]:
    """Writes source to a temporary file with this suffix and runs command + [file]. None if it could not run."""
    try:
        with tempfile.TemporaryDirectory(prefix="te_check_") as tmp:
            path = Path(tmp) / f"check{suffix}"
            path.write_text(source, encoding="utf-8")
            return subprocess.run(command + [str(path)], capture_output=True, text=True, timeout=CHECK_TIMEOUT_SECONDS,
                                  cwd=tmp)
    except (OSError, subprocess.SubprocessError, UnicodeError) as err:
        logger.debug(f"source_check: {command[0]} could not check the file: {err}")
        return None


def _node_issue(output: str) -> Optional[SyntaxIssue]:
    """The SyntaxIssue in `node --check` output, or None if the output names no error."""
    lines = output.splitlines()
    message = next((ln.strip() for ln in lines if re.match(r"^\s*[A-Za-z]*Error:", ln)), None)
    if not message:
        return None
    issue = SyntaxIssue(JAVASCRIPT, message)
    for index, line in enumerate(lines):
        match = re.match(r"^.+?:(\d+)\s*$", line)
        if match and "check" in line:
            issue.line = int(match.group(1))
            # node prints the source line and, below it, carets under the error
            if index + 2 < len(lines) and "^" in lines[index + 2] and not lines[index + 2].strip().strip("^"):
                caret_line = lines[index + 2]
                issue.column = caret_line.index("^")
                issue.width = caret_line.count("^")
            break
    return issue


def javascript_issue(source: str, filename: str = "file.js") -> Optional[SyntaxIssue]:
    node = _executable("node")
    if not node:
        return None
    suffix = _JS_SUFFIXES.get(PurePosixPath(str(filename).lower()).suffix, ".js")
    if suffix == ".js" and _ESM_RE.search(source):
        suffix = ".mjs"  # an ES module: see the module docstring
    result = _run_checker([node, "--check"], source, suffix)
    if result is None or result.returncode == 0:
        return None
    issue = _node_issue(result.stderr or result.stdout or "")
    if issue is None:
        return None
    low = issue.message.lower()
    if suffix == ".js" and any(text in low for text in _MODULE_ONLY_ERRORS):
        result = _run_checker([node, "--check"], source, ".mjs")
        if result is None or result.returncode == 0:
            return None
        issue = _node_issue(result.stderr or result.stdout or "")
        if issue is None:
            return None
        low = issue.message.lower()
    if "unexpected token '<'" in low:
        return None  # probably JSX, which node cannot parse
    return issue


def json_issue(source: str) -> Optional[SyntaxIssue]:
    text = source.lstrip("﻿")
    if not text.strip():
        return SyntaxIssue(JSON_KIND, "the file is empty")
    try:
        json.loads(text)
    except json.JSONDecodeError as err:
        return SyntaxIssue(JSON_KIND, err.msg, err.lineno, max(0, err.colno - 1), 1)
    except (ValueError, RecursionError) as err:
        return SyntaxIssue(JSON_KIND, str(err))
    return None


def _runs_in_posix_shell(source: str) -> bool:
    first = source.lstrip("﻿").split("\n", 1)[0].strip()
    if not first.startswith("#!"):
        return True
    words = first[2:].split()
    if not words:
        return True
    program = os.path.basename(words[0])
    if program == "env":
        program = next((os.path.basename(w) for w in words[1:] if not w.startswith("-")), "")
    return program in _SHELLS


def shell_issue(source: str) -> Optional[SyntaxIssue]:
    if not _runs_in_posix_shell(source):
        return None
    bash = _executable("bash")
    if not bash:
        return None
    result = _run_checker([bash, "-n"], source, ".sh")
    if result is None or result.returncode == 0:
        return None
    for line in (result.stderr or "").splitlines():
        match = re.search(r"line (\d+): (.+)$", line)
        if match:
            return SyntaxIssue(SHELL, match.group(2).strip(), int(match.group(1)))
    first = next((ln.strip() for ln in (result.stderr or "").splitlines() if ln.strip()), "")
    return SyntaxIssue(SHELL, first.split(": ", 1)[-1] or "bash -n failed") if first else None


def check_source(source: str, filename: Optional[str]) -> Optional[SyntaxIssue]:
    """The syntax issue of source as the file filename, or None (no issue, not a checked kind, or no checker)."""
    kind = check_kind(filename)
    if kind is None or source is None or len(source.encode("utf-8", "replace")) > MAX_CHECK_BYTES:
        return None
    try:
        if kind == JAVASCRIPT:
            return javascript_issue(source, str(filename))
        if kind == JSON_KIND:
            return json_issue(source)
        return shell_issue(source)
    except Exception as err:  # a check must never break a tool call
        logger.debug(f"source_check: checking '{filename}' failed: {err}")
        return None


def _note(source: str, filename: Optional[str], head: str, tail: str) -> str:
    issue = check_source(source, filename)
    if issue is None:
        return ""
    return f"\n[Framework check - {head} NOT valid {issue.kind}:]\n{issue.detailed(source)}\n{tail}"


def write_note(source: str, filename: Optional[str]) -> str:
    """Text appended to a successful 'write' of a checked file that has a syntax error ('' otherwise)."""
    return _note(source, filename, "the file WAS saved, but it is",
                 "Fix the line named above (code_editor or file_system 'replace_lines'); do not resend the whole file "
                 "unchanged.")


def edit_note(source: str, filename: Optional[str]) -> str:
    """Text appended to a successful edit of a checked file whose new text has a syntax error ('' otherwise)."""
    return _note(source, filename, "the edit WAS saved, but the file is now",
                 "Fix the line named above before you go on (read the file again first: line numbers may have changed).")


def unchanged_note(source: str, filename: Optional[str]) -> str:
    """Text appended to an 'identical content' write of a checked file that still has a syntax error."""
    return _note(source, filename, "the file is unchanged and STILL",
                 "Sending the same text again changes nothing: fix the line named above.")


def file_problem(path: Path, shown_name: str) -> Optional[str]:
    """A one-line defect of an existing checked file for the completion check, or None."""
    if check_kind(shown_name) is None:
        return None
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        return f"{shown_name}: cannot be read ({err})."
    if not source.strip():
        return f"{shown_name} is EMPTY."
    issue = check_source(source, shown_name)
    return issue.compact(shown_name) if issue else None

# END OF FILE src/tools/source_check.py
