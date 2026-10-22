# START OF FILE src/tools/rejected_drafts.py
"""
The text of a rejected 'write' of a NEW Python file (Round 2v).

2026-10-22 10:34-10:38 W1 (qwen3.5:9b) wrote server.py (14-16k characters, ~3,000 output tokens per reply) four times. Each
write was rejected for one small syntax error ('{' never closed, then a missing comma), so the file never existed, and each
time W1 had to send the whole file again - a minute per reply, with new small mistakes every time. After three rejected
writes the worker was forced to report.

Now the rejected text is kept (per file and agent, for DRAFT_TTL_SECONDS). While the file does not exist, file_system
'find_replace' and 'replace_lines' on it change that kept text; as soon as it is valid Python the file is created with the
normal 'write' checks. Nothing is ever written to disk before the text is valid, so "a rejected write saves nothing" still
holds.
"""
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

DRAFT_TTL_SECONDS = 1800
MAX_DRAFTS = 50

_drafts: Dict[str, Tuple[str, str, float]] = {}  # resolved path -> (agent id, text, time kept)


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


def clear() -> None:
    """For tests."""
    _drafts.clear()


def kept_note(filename: str) -> str:
    """What a rejected write of a new file is told about the kept text."""
    return (f" You do NOT have to send the whole file again: your rejected text of '{filename}' is kept. Fix only the faulty "
            f"line(s) with file_system 'find_replace' (filename '{filename}', find_text = the wrong text, replace_text = "
            f"the corrected text) or 'replace_lines' (the line numbers shown below); '{filename}' is created as soon as "
            "the text is valid Python.")


DRAFT_CLOSING = ("Fix the faulty line(s) with find_replace or replace_lines (the rejected text is kept), or send the COMPLETE "
                 "corrected file with 'write'. Do not resend the same text.")

# END OF FILE src/tools/rejected_drafts.py
