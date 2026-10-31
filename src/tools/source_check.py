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

Round 3u: pip requirements files (requirements.txt, requirements-dev.txt, requirements/base.txt, ...) are checked too
(requirements_issue): 2026-10-26 19:26 W2 wrote requirements.txt as a Markdown document (headings, ``` fences around the
package lines); `pip install -r requirements.txt` stops at the first fence, and the Dockerfile and deploy.sh it wrote
both run that command.

Round 4m: edit_rejection(..., replaced) - the texts an edit replaced (ReplacedText, replaced_lines) - adds "[Framework
check - why the edit broke the file:]" for JavaScript / JSON: range_balance_note (the replaced range opens a block that
closes after it, or closes one that opened before it, and the new text does not) and duplicate_declaration_note (a name
the new text declares that a line it did not replace declares too). bracket_events scans code only (not strings, template
text, comments or regular expressions).
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
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

JAVASCRIPT = "JavaScript"
JSON_KIND = "JSON"
SHELL = "shell script"
REQUIREMENTS = "pip requirements"  # Round 3u
_REQUIREMENTS_NAME_RE = re.compile(r"^(?:[\w.-]*[-_.])?requirements(?:[-_.][\w.-]*)?\.(?:txt|in)$", re.IGNORECASE)
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
        elif self.kind == REQUIREMENTS:
            return ("Hint: a requirements file holds ONE package per line (e.g. 'Flask==3.0.0' or 'flask-socketio>=5.3'); a "
                    "line starting with '#' is a comment. It is NOT Markdown: no ``` fences, no '- ' bullets, no prose. Put "
                    "explanations in README.md.")
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
    if _REQUIREMENTS_NAME_RE.match(PurePosixPath(name).name) or (
            suffix == ".txt" and "requirements" in PurePosixPath(lower).parts[:-1]):
        return REQUIREMENTS  # Round 3u
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


# --- Round 3u: pip requirements files ------------------------------------------------------------------------------------
_REQ_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")
_REQ_URL_OR_PATH_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|git\+|hg\+|svn\+|bzr\+|\.{1,2}[\\/]|\.$|[\\/~]|[A-Za-z]:[\\/])")
_REQ_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\s*\[\s*[A-Za-z0-9._,\s-]*\])?")
_REQ_SPEC = r"(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9*+!._-]+"
_REQ_SPECS_RE = re.compile(r"^\s*(?:\(?\s*" + _REQ_SPEC + r"(?:\s*,\s*" + _REQ_SPEC + r")*\s*\)?)?\s*$")
_REQ_ENV_VAR_RE = re.compile(r"\$\{[A-Z0-9_]+\}")


def _requirement_line_ok(line: str) -> bool:
    """True for one logical line of a pip requirements file (comment already removed)."""
    if re.match(r"^--?[A-Za-z]", line):
        return True  # an option: -r other.txt, -e ., --index-url ..., -c constraints.txt (a Markdown '- ' bullet is not)
    if _REQ_URL_OR_PATH_RE.match(line):
        return True  # a URL, a VCS link or a local path
    line = re.split(r"\s+--?[A-Za-z]", line, maxsplit=1)[0].strip()  # per-requirement options ('--hash=sha256:...')
    requirement = _REQ_ENV_VAR_RE.sub("0", line.split(";", 1)[0]).strip()  # a marker after ';'; pip expands ${VAR}
    if not requirement:
        return False
    if "@" in requirement:
        name, _at, url = requirement.partition("@")
        return bool(_REQ_NAME_RE.fullmatch(name.strip())) and bool(url.strip())  # 'pkg @ https://...'
    try:
        from packaging.requirements import Requirement, InvalidRequirement
        try:
            Requirement(requirement)
            return True
        except InvalidRequirement:
            pass  # pip also reads the older form 'pkg (>=1.0)'
    except ImportError:  # pragma: no cover - packaging comes with pip and pytest
        pass
    match = _REQ_NAME_RE.match(requirement)
    return bool(match) and bool(_REQ_SPECS_RE.match(requirement[match.end():]))


def requirements_issue(source: str) -> Optional[SyntaxIssue]:
    """
    Round 3u: the first line of a pip requirements file that pip cannot read, as a SyntaxIssue, or None. Lines continued
    with a trailing backslash are joined; blank lines and '#' comments are skipped.
    """
    text = source.lstrip("\ufeff")
    logical: List[tuple] = []
    pending, start = "", 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if not pending:
            start = number
        if raw.rstrip().endswith("\\") and not raw.lstrip().startswith("#"):
            pending += raw.rstrip()[:-1] + " "
            continue
        logical.append((start, pending + raw))
        pending = ""
    if pending:
        logical.append((start, pending))
    for number, raw in logical:
        line = _REQ_COMMENT_RE.sub("", raw).strip()
        if not line:
            continue
        if not _requirement_line_ok(line):
            shown = line if len(line) <= 60 else line[:57] + "..."
            return SyntaxIssue(REQUIREMENTS, f"'{shown}' is not a package requirement", number)
    return None


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
        if kind == REQUIREMENTS:
            return requirements_issue(source)  # Round 3u
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


def edit_rejection(filename: str, original: str, new_text: str,
                   replaced: Optional[Sequence["ReplacedText"]] = None) -> Optional[str]:
    """
    Round 2x: the message that refuses an edit (find_replace, replace_lines, insert_lines, append, code_editor, ...) whose
    result is not valid JavaScript / JSON / shell although the file was valid before, else None.
    Round 4m: `replaced` - the texts the edit replaced (ReplacedText) - adds replacement_notes: which block the replaced
    range cuts through, or where a name the new text declares is declared a second time.
    """
    issue = blocking_issue(new_text, filename)
    if issue is None:
        return None
    if original and original.strip() and check_source(original, filename) is not None:
        return None  # the file was already broken: never block an attempt to repair it
    return (f"Edit rejected: the result would not be valid {issue.kind} ({issue.summary()}). No changes were saved; "
            f"'{filename}' is unchanged.\n" + issue_report(issue, new_text, "Change your new text so the file stays valid "
            "(read the file again if you used line numbers), and do not resend the same text.")
            + replacement_notes(issue, new_text, replaced))


# --- Round 4m: why a replacement broke a valid file -------------------------------------------------------------------
# 2026-10-31 05:39-07:25 (run 3, qwen3.5:4b): about 50 edits of JavaScript tests were rejected with "Unexpected token '}'",
# "Unexpected end of input" or "Identifier 'mockSocket' has already been declared". The new texts were complete,
# balanced blocks - the replaced line RANGES were not: e.g. lines 25-31 opened an `it(..., () => {` whose `});` was line
# 32, so line 32 was left closing nothing; or the range ended one line before the old `const mockSocket = ...` the new
# text rewrote. The rejection only showed the end of the file, and W1 re-sent the same range up to 4 times.

_OPENERS = "([{"
_CLOSERS = ")]}"
_PAIRS = {")": "(", "]": "[", "}": "{"}
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = ("return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await", "throw", "delete")
_ALREADY_DECLARED_RE = re.compile(r"Identifier '([\w$]+)' has already been declared")
MAX_DECLARATION_LINES = 4


@dataclass
class ReplacedText:
    """Round 4m: one replacement of an edit - the file text it was made in, the replaced span in it, and the new text."""
    before: str
    start: int            # character offsets of the replaced text in `before`
    end: int
    new_text: str
    label: str = ""       # e.g. "Chunk 1: " - put in front of the sentences about this replacement


def replaced_lines(before: str, start_line: int, end_line: int, new_text: str, label: str = "") -> Optional[ReplacedText]:
    """Round 4m: the ReplacedText of lines start_line..end_line (1-based, inclusive) of `before`, or None (no such lines)."""
    lines = str(before or "").splitlines(True)
    if start_line < 1 or end_line < start_line or start_line > len(lines):
        return None
    start = sum(len(line) for line in lines[:start_line - 1])
    end = start + sum(len(line) for line in lines[start_line - 1:min(end_line, len(lines))])
    return ReplacedText(before, start, end, str(new_text or ""), label)


def bracket_events(source: str) -> List[tuple]:
    """
    Round 4m: (bracket, offset) of every bracket in JavaScript / JSON CODE - not in strings, template text, comments or
    regular expression literals. A '}' that ends a template literal's ${...} is not reported (nor its '${').
    """
    events: List[tuple] = []
    text = str(source or "")
    length = len(text)
    braces: List[str] = []      # "{" for a code block, "${" for a template interpolation
    in_template = False
    last_significant = ""       # the last code character / word before a '/', to tell a regex from a division
    i = 0
    while i < length:
        char = text[i]
        if in_template:
            if char == "\\":
                i += 2
                continue
            if char == "`":
                in_template = False
                last_significant = ")"
            elif char == "$" and i + 1 < length and text[i + 1] == "{":
                braces.append("${")
                in_template = False
                last_significant = "{"
                i += 2
                continue
            i += 1
            continue
        if char in "'\"":
            j = i + 1
            while j < length and text[j] != char and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            last_significant = ")"
            continue
        if char == "`":
            in_template = True
            i += 1
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "/":
            newline = text.find("\n", i)
            i = length if newline == -1 else newline
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "*":
            close = text.find("*/", i + 2)
            i = length if close == -1 else close + 2
            continue
        if char == "/" and (not last_significant or last_significant in _REGEX_BEFORE
                            or last_significant in _REGEX_KEYWORDS):
            j, in_class = i + 1, False
            while j < length and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            if j < length and text[j] == "/":
                i = j + 1
                last_significant = ")"
                continue
        if char in _OPENERS:
            if char == "{":
                braces.append("{")
            events.append((char, i))
        elif char in _CLOSERS:
            if char == "}" and braces and braces[-1] == "${":
                braces.pop()
                in_template = True
                i += 1
                continue
            if char == "}" and braces:
                braces.pop()
            events.append((char, i))
        if char.isalnum() or char in "_$":
            j = i
            while j < length and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last_significant = text[i:j]
            i = j
            continue
        if not char.isspace():
            last_significant = char
        i += 1
    return events


def _unmatched(source: str):
    """Round 4m: (closers that close nothing in `source`, openers `source` leaves open) - each a list of (bracket, offset)."""
    stack: List[tuple] = []
    closers: List[tuple] = []
    for char, offset in bracket_events(source):
        if char in _OPENERS:
            stack.append((char, offset))
        elif stack:
            stack.pop()
        else:
            closers.append((char, offset))
    return closers, stack


def _range_brackets(item: ReplacedText):
    """
    Round 4m: for the replaced span of item.before: (closers in the span whose opener is BEFORE it, as (closer, offset,
    opener offset)), (openers in the span closed only AFTER it, as (opener, offset, closer offset or None)).
    """
    stack: List[tuple] = []
    closes_outer: List[tuple] = []
    opens_outer: List[tuple] = []
    for char, offset in bracket_events(item.before):
        if char in _OPENERS:
            stack.append((char, offset))
            continue
        opener = stack.pop() if stack else None
        if item.start <= offset < item.end and (opener is None or opener[1] < item.start):
            closes_outer.append((char, offset, opener[1] if opener else None))
        elif opener is not None and offset >= item.end and item.start <= opener[1] < item.end:
            opens_outer.append((opener[0], opener[1], offset))
    opens_outer.extend((char, offset, None) for char, offset in stack if item.start <= offset < item.end)
    opens_outer.sort(key=lambda entry: entry[1])
    return closes_outer, opens_outer


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _line_text(text: str, line: int) -> str:
    lines = text.splitlines()
    shown = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
    return shown if len(shown) <= 90 else shown[:87] + "..."


def _brackets(chars: str) -> str:
    return " ".join(f"'{char}'" for char in chars)


def range_balance_note(item: ReplacedText) -> str:
    """
    Round 4m: a sentence list (one '- ' line each) when the replaced span and the new text do not open and close the same
    brackets - the span cuts through a block (it opens one that closes after it, or closes one that opened before it) and
    the new text does not do the same - else ''.
    """
    closes_outer, opens_outer = _range_brackets(item)
    new_closers, new_openers = _unmatched(item.new_text)
    old_close = "".join(entry[0] for entry in closes_outer)
    old_open = "".join(entry[0] for entry in opens_outer)
    new_close = "".join(entry[0] for entry in new_closers)
    new_open = "".join(entry[0] for entry in new_openers)
    if old_close == new_close and old_open == new_open:
        return ""
    before = item.before
    first = _line_of(before, item.start)
    last = _line_of(before, max(item.start, item.end - 1))
    span = f"line {first}" if first == last else f"lines {first}-{last}"
    where = f"the text you replace ({span})"
    subject = f"{item.label}{where}" if item.label else where[:1].upper() + where[1:]
    notes: List[str] = []
    if old_open != new_open and opens_outer:
        open_line = _line_of(before, opens_outer[0][1])
        close_offsets = [entry[2] for entry in opens_outer if entry[2] is not None]
        if close_offsets:
            close_line = _line_of(before, max(close_offsets))
            fix = (f"Include line {close_line} in what you replace (replace lines {first}-{close_line})" if close_line > last
                   else "Include that closing line in what you replace")
            notes.append(f"{subject} opens {_brackets(old_open)} (line {open_line}: `{_line_text(before, open_line)}`) that "
                         f"{'is' if len(old_open) == 1 else 'are'} closed only AFTER it, by line {close_line} "
                         f"(`{_line_text(before, close_line)}`), which you do not replace. "
                         + ("Your new text closes everything it opens, so that line would be left closing nothing. "
                            if not new_open else f"Your new text leaves {_brackets(new_open)} open instead. ")
                         + f"{fix}, or end your new text WITHOUT that closing text.")
    elif new_open and not old_open:
        notes.append(f"Your new text leaves {_brackets(new_open)} open (it never closes {'it' if len(new_open) == 1 else 'them'}), "
                     f"but {where} closes everything it opens: add the closing text at the end of your new text.")
    if old_close != new_close and closes_outer:
        close_line = _line_of(before, closes_outer[-1][1])
        opener_offsets = [entry[2] for entry in closes_outer if entry[2] is not None]
        opened = ""
        if opener_offsets:
            open_line = _line_of(before, min(opener_offsets))
            opened = f" that opened BEFORE it, at line {open_line} (`{_line_text(before, open_line)}`)"
        notes.append(f"{subject} closes {_brackets(old_close)} (line {close_line}: `{_line_text(before, close_line)}`)"
                     f"{opened}. "
                     + ("Your new text does not close it, so that block would never be closed. "
                        if not new_close else f"Your new text closes {_brackets(new_close)} instead. ")
                     + f"End your new text with the same closing text, or stop what you replace before line {close_line}.")
    elif new_close and not old_close:
        notes.append(f"Your new text closes {_brackets(new_close)} that it never opens, but {where} does not: the file "
                     "would get a closing bracket too many. Remove it from the end of your new text, or include the line "
                     "that opens that block in what you replace.")
    return "".join(f"\n- {note}" for note in notes)


def _declaration_re(name: str):
    word = re.escape(name)
    return re.compile(r"\b(?:const|let|var|class|function\s*\*?)\s+" + word + r"(?![\w$])|"
                      r"\b(?:const|let|var)\s*[{\[][^}\]=]*(?<![\w$.])" + word + r"(?![\w$])[^}\]=]*[}\]]\s*=")


def duplicate_declaration_note(issue: SyntaxIssue, result: str, replaced: Sequence[ReplacedText]) -> str:
    """
    Round 4m: for "Identifier 'X' has already been declared": the lines of the result that declare X, and whether the new
    text declares it while a line that was NOT replaced still does ('' otherwise).
    """
    found = _ALREADY_DECLARED_RE.search(issue.message or "")
    if not found:
        return ""
    name = found.group(1)
    pattern = _declaration_re(name)
    lines = str(result or "").splitlines()
    declared = [number for number, line in enumerate(lines, 1)
                if pattern.search(line) and not line.lstrip().startswith(("//", "*"))]
    if len(declared) < 2:
        return ""
    in_new = sum(1 for item in replaced for line in item.new_text.splitlines() if pattern.search(line))
    shown = "; ".join(f"line {number}: `{_line_text(result, number)}`" for number in declared[:MAX_DECLARATION_LINES])
    text = f"\n- '{name}' is declared {len(declared)} times in the result ({shown})."
    if in_new and len(declared) > in_new:
        text += (f" Your new text declares it, and a line you did NOT replace declares it too. If that line is the OLD "
                 "version of the code you are rewriting, include it in what you replace (a longer line range or search "
                 "text); if it is different code, give the name in your new text another name.")
    elif in_new >= 2:
        text += " Your new text itself declares it more than once: keep one declaration."
    return text


def replacement_notes(issue: SyntaxIssue, result: str, replaced: Optional[Sequence[ReplacedText]]) -> str:
    """
    Round 4m: the '[Framework check ...]' text added to a rejected edit of a JavaScript / JSON file: range_balance_note for
    each replacement and duplicate_declaration_note ('' if there is nothing to say). Never raises.
    """
    if not replaced or issue.kind not in (JAVASCRIPT, JSON_KIND):
        return ""
    try:
        notes = "".join(range_balance_note(item) for item in replaced)
        if issue.kind == JAVASCRIPT:
            notes += duplicate_declaration_note(issue, result, replaced)
    except Exception as err:  # a note must never break the rejection itself
        logger.debug(f"replacement_notes failed: {err}")
        return ""
    if not notes:
        return ""
    return "\n[Framework check - why the edit broke the file:]" + notes


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
