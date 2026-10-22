# START OF FILE src/tools/rejected_drafts.py
"""
The text of a rejected 'write' of a NEW Python file (Round 2v) - or, since Round 2x, of a new JavaScript, JSON or shell
file (their syntax check blocks too: src/tools/source_check.py).

2026-10-22 10:34-10:38 W1 (qwen3.5:9b) wrote server.py (14-16k characters, ~3,000 output tokens per reply) four times. Each
write was rejected for one small syntax error ('{' never closed, then a missing comma), so the file never existed, and each
time W1 had to send the whole file again - a minute per reply, with new small mistakes every time. After three rejected
writes the worker was forced to report.

Now the rejected text is kept (per file and agent, for DRAFT_TTL_SECONDS). While the file does not exist, file_system
'find_replace' and 'replace_lines' on it change that kept text; as soon as it is valid Python the file is created with the
normal 'write' checks. Nothing is ever written to disk before the text is valid, so "a rejected write saves nothing" still
holds.

Round 2x: an 'append' to the missing file adds to the kept text, so "write the first part, then append the rest" also
works when the first part alone is not valid yet (a JSON file, a class split in two).

Round 2y: the line of each rejection is recorded (note_rejection). 2026-10-22 14:51-14:53 W1 sent src/game.js three times
as a complete new file; the error moved from line 102 to 117 to 134 - real progress - but three failed tool calls in a
row made the scheduler force W1 to report. A rejection whose error is further down than the previous one is progress
(take_progress), for at most MAX_PROGRESS_STEPS rejections in a row; repeated complete rewrites are told to stop.
"""
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DRAFT_TTL_SECONDS = 1800
MAX_DRAFTS = 50

_drafts: Dict[str, Tuple[str, str, float]] = {}  # resolved path -> (agent id, text, time kept)
_streaks: Dict[str, Tuple[str, List[int]]] = {}  # Round 2y: resolved path -> (agent id, error lines of its rejections)
_progress: Dict[str, bool] = {}  # Round 2y: agent id -> a rejection moved the error further down since the last check
MAX_PROGRESS_STEPS = 6


def _key(path: Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _expire(now: float) -> None:
    for key in [k for k, (_, _, kept) in _drafts.items() if now - kept > DRAFT_TTL_SECONDS]:
        _drafts.pop(key, None)


def keep(path: Path, agent_id: str, text: str, now: Optional[float] = None) -> None:
    """Keep the rejected text of path for agent_id (replaces an earlier one)."""
    now = time.time() if now is None else now
    _expire(now)
    _drafts[_key(path)] = (str(agent_id), str(text), now)
    while len(_drafts) > MAX_DRAFTS:
        oldest = min(_drafts, key=lambda k: _drafts[k][2])
        _drafts.pop(oldest, None)


def get(path: Path, agent_id: str, now: Optional[float] = None) -> Optional[str]:
    """The kept text of path if agent_id's rejected write of it is still kept, else None."""
    now = time.time() if now is None else now
    _expire(now)
    entry = _drafts.get(_key(path))
    if entry is None or entry[0] != str(agent_id):
        return None
    return entry[1]


def forget(path: Path) -> None:
    _drafts.pop(_key(path), None)
    _streaks.pop(_key(path), None)  # Round 2y: the file exists now


def clear() -> None:
    """For tests."""
    _drafts.clear()
    _streaks.clear()
    _progress.clear()


def note_rejection(path: Path, agent_id: str, line: Optional[int]) -> List[int]:
    """
    Round 2y: record that agent_id's text of the missing file path was rejected for an error at `line`. Returns the error
    lines of its rejections in a row (oldest first). If the error is further down than the previous one - and this is at
    most the MAX_PROGRESS_STEPS-th step - the agent made progress (see take_progress).
    """
    key, agent = _key(path), str(agent_id)
    entry = _streaks.get(key)
    lines = list(entry[1]) if entry is not None and entry[0] == agent else []
    number = int(line) if isinstance(line, int) and not isinstance(line, bool) and line > 0 else 0
    previous = lines[-1] if lines else None
    lines.append(number)
    _streaks[key] = (agent, lines)
    if previous is not None and number > previous and len(lines) - 1 <= MAX_PROGRESS_STEPS:
        _progress[agent] = True
    return lines


def take_progress(agent_id: str) -> bool:
    """Round 2y: True (once) if one of agent_id's rejections moved its error further down since the last call."""
    return _progress.pop(str(agent_id), False)


def repeated_write_note(filename: str, lines: List[int]) -> str:
    """Round 2y: the warning for the second and later rejected complete 'write' of the same new file ('' before)."""
    if len(lines) < 2:
        return ""
    shown = ", ".join(str(n) for n in lines if n) or "unknown lines"
    return (f" This is your rejection number {len(lines)} of '{filename}' in a row (errors at lines {shown}). Do NOT send "
            f"the whole file again - every complete rewrite takes a minute and brings new mistakes: fix ONLY the line named "
            f"below with 'replace_lines' or 'find_replace' on '{filename}' (your text from this write is kept).")


def language_of(filename: Optional[str]) -> str:
    """Round 2x: 'Python', 'JavaScript', 'JSON' or 'shell script' - what a kept text of this file must be valid as."""
    if str(filename or "").endswith(".py"):
        return "Python"
    from src.tools.source_check import check_kind
    return check_kind(filename) or "Python"


def draft_problem(text: str, filename: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """
    Round 2x: (summary, report, line) of the syntax error that keeps text from being written as filename, or None if it
    is valid (a .py file: Python; a checked file: src/tools/source_check.blocking_issue). Round 2y: + the error line.
    """
    if str(filename).endswith(".py"):
        from src.tools.python_static_check import parse_python, syntax_error_report
        try:
            parse_python(text)
            return None
        except SyntaxError as err:
            return f"{err.msg} at line {err.lineno}", syntax_error_report(text, err, closing=DRAFT_CLOSING), err.lineno
    from src.tools.source_check import blocking_issue, issue_report
    issue = blocking_issue(text, filename)
    if issue is None:
        return None
    return issue.summary(), issue_report(issue, text, DRAFT_CLOSING), issue.line


def kept_note(filename: str) -> str:
    """What a rejected write of a new file is told about the kept text."""
    return (f" You do NOT have to send the whole file again: your rejected text of '{filename}' is kept. Fix only the faulty "
            f"line(s) with file_system 'find_replace' (filename '{filename}', find_text = the wrong text, replace_text = "
            f"the corrected text) or 'replace_lines' (the line numbers shown below), or add a missing end with 'append'; "
            f"'{filename}' is created as soon as the text is valid {language_of(filename)}.")


DRAFT_CLOSING = ("Fix the faulty line(s) with find_replace or replace_lines (the rejected text is kept), or send the COMPLETE "
                 "corrected file with 'write'. Do not resend the same text.")

# END OF FILE src/tools/rejected_drafts.py
