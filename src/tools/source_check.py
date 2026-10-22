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
* write_note / edit_note / unchanged_note: the '[Framework check ...]' text for a tool result whose file IS saved.
* file_problem(path, shown): a one-line defect for the task completion check (src/utils/task_files.py).

Round 2x: the check BLOCKS like a Python SyntaxError (2026-10-22 11:44-12:19 W2 saved src/js/main_game_controller.js six
times with "SyntaxError: Unexpected token '{'" at line 52 - each save only got a note - and the completion check refused
the task at the end):
* blocking_issue(source, filename): the issue that rejects a 'write' or an edit (None for blank text: an empty file is
  not refused here; the completion check reports it).
* edit_rejection(filename, original, new_text): the refusal of an edit that turns a valid file into an invalid one, else
  None (a file that was already broken is never blocked: the edit may be its repair).
* write_rejection_text / issue_report: the texts of a rejected write.

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
# JSON files that by convention may hold comments (Round 2x: + ESLint, TSLint and dev container configs)
_JSONC_NAME_RE = re.compile(r"^(?:tsconfig|jsconfig)(?:\..+)?\.json$|^\.eslintrc\.json$|^tslint\.json$|"
                            r"^\.?devcontainer\.json$", re.IGNORECASE)
_ESM_RE = re.compile(r"^[ \t]*(?:import[ \t]*[\w{*'\"]|export[ \t]*[\w{*])", re.MULTILINE)
_MODULE_ONLY_ERRORS = ("cannot use import statement outside a module", "unexpected token 'export'",
                       "await is only valid in async functions and the top level bodies of modules",
                       "cannot use 'import.meta' outside a module")
MAX_CHECK_BYTES = 1_000_000
CHECK_TIMEOUT_SECONDS = 15
CONTEXT_LINES_BEFORE = 2
REPORT_LINES_BEFORE = 6  # Round 2x: a rejection shows more: the cause of "Unexpected token" is usually above the line
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

    def summary(self) -> str:
        """Round 2x: 'SyntaxError: Unexpected token '{' at line 52'."""
        return f"{self.message} at line {self.line}" if self.line else self.message

    def hint(self) -> str:
        """Round 2x: a hint for the commonest JavaScript / JSON mistakes, or ''."""
        low = self.message.lower()
        if self.kind == JAVASCRIPT:
            if "unexpected end of input" in low:
                return "Hint: a '{', '(' or '[' is never closed - usually a function, class or block misses its last '}'."
            if "unexpected token" in low or "missing ) after argument list" in low:
                return ("Hint: the error is usually just ABOVE that line: a function, method or block that is not closed "
                        "(a missing '}' or ')'), one '}' too many, or a missing ',' between object members.")
            if "unexpected identifier" in low:
                return "Hint: a ',' or ';' is probably missing before that word, or a string is not closed."
            if "invalid or unexpected token" in low:
                return "Hint: a string is not closed on that line, or the line holds a character that is not JavaScript."
        elif self.kind == JSON_KIND:
            return ("Hint: JSON allows no comments and no trailing commas, and every name and string needs double "
                    "quotes.")
        elif self.kind == SHELL and ("unexpected end of file" in low or "unexpected token" in low):
            return "Hint: an 'if', 'for', 'while', 'case' or '{' is not closed ('fi', 'done', 'esac', '}'), or a quote is missing."
        return ""

    def detailed(self, source: str, before: int = CONTEXT_LINES_BEFORE) -> str:
        where = f" (line {self.line})" if self.line else ""
        text = f"{self.message}{where}"
        lines = source.splitlines()
        if self.line and 1 <= self.line <= len(lines):
            width = len(str(self.line))
            for number in range(max(1, self.line - before), self.line + 1):
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
        if _JSONC_NAME_RE.match(base) or "/.vscode/" in f"/{lower}" or "/.devcontainer/" in f"/{lower}":
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
    """
    Text appended to a successful edit of a checked file whose new text has a syntax error ('' otherwise).
    Round 2x: an edit that breaks a valid file is rejected (edit_rejection), so this is only said after an edit of a file
    that was already invalid.
    """
    return _note(source, filename, "the edit WAS saved (the file was already invalid before it), but the file is STILL",
                 "Fix the line named above before you go on (read the file again first: line numbers may have changed).")


def unchanged_note(source: str, filename: Optional[str]) -> str:
    """Text appended to an 'identical content' write of a checked file that still has a syntax error."""
    return _note(source, filename, "the file is unchanged and STILL",
                 "Sending the same text again changes nothing: fix the line named above.")


def blocking_issue(source: str, filename: Optional[str]) -> Optional[SyntaxIssue]:
    """
    Round 2x: the syntax issue that rejects a 'write' or an edit of a checked file, or None. Blank text is never refused
    here (the completion check reports an EMPTY file); neither is a file the checker cannot judge (see check_source).
    """
    if source is None or not str(source).strip():
        return None
    return check_source(source, filename)


def issue_report(issue: SyntaxIssue, source: str, closing: Optional[str] = None) -> str:
    """Round 2x: the code around the error, a hint and a closing line - the counterpart of python_static_check.syntax_error_report."""
    parts = ["Code around the error:\n" + issue.detailed(source, REPORT_LINES_BEFORE)]
    hint = issue.hint()
    if hint:
        parts.append(hint)
    parts.append(closing or "Fix the line(s) named above and send the corrected text; do not resend the same text.")
    return "\n".join(parts)


def edit_rejection(filename: str, original: str, new_text: str) -> Optional[str]:
    """
    Round 2x: the message that refuses an edit (find_replace, replace_lines, insert_lines, append, code_editor, ...) whose
    result is not valid JavaScript / JSON / shell although the file was valid before, else None.
    """
    issue = blocking_issue(new_text, filename)
    if issue is None:
        return None
    if original and original.strip() and check_source(original, filename) is not None:
        return None  # the file was already broken: never block an attempt to repair it
    return (f"Edit rejected: the result would not be valid {issue.kind} ({issue.summary()}). No changes were saved; "
            f"'{filename}' is unchanged.\n" + issue_report(issue, new_text, "Change your new text so the file stays valid "
            "(read the file again if you used line numbers), and do not resend the same text."))


def write_rejection_text(filename: str, issue: SyntaxIssue) -> str:
    """Round 2x: the first sentence of a rejected 'write' of a checked file."""
    return (f"Write rejected: '{filename}' is not valid {issue.kind} ({issue.summary()}). No changes were saved.")


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
