# START OF FILE src/utils/source_health.py
"""
Round 5f: the framework does not start with a source file that does not compile.

2026-11-15 19:40-23:04 (WiFi_Snake_Multiplayer_Game, 3 h 24 min, 1,373 model calls) ran with two framework files that were
not valid Python (uncommitted edits): src/tools/project_links_check.py (line 1142, `"type": "module"` inside a "..." string)
and src/utils/test_diagnosis.py (line 382, the same; line 422, a comment broken over three lines). Both are imported lazily
and every caller catches the error, so the run went on WITHOUT them: the task completion check failed 24 times ("check
failed (...); not blocking"), the kick-off test-language check once, and the diagnosis of failed test runs every time. The
only trace was a WARNING line per use in a 241 MB log.

* broken_sources(): the .py files under src/ that do not compile, with the error (nothing is written: no .pyc files).
* check_sources_or_raise(): called first in the startup sequence (src/main.py). It raises unless ALLOW_BROKEN_SOURCES=true.
"""
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_SKIP_PARTS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}


def broken_sources(root: Optional[Path] = None) -> List[str]:
    """'path:line: message' for every .py file under <root>/src that does not compile (root: the repository)."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    problems: List[str] = []
    for path in sorted((base / "src").rglob("*.py")):
        if _SKIP_PARTS.intersection(path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            problems.append(f"{path.relative_to(base).as_posix()}: cannot be read ({err})")
            continue
        try:
            compile(source, str(path), "exec", dont_inherit=True)
        except SyntaxError as err:  # IndentationError and TabError are SyntaxErrors
            problems.append(f"{path.relative_to(base).as_posix()}:{err.lineno}: {type(err).__name__}: {err.msg}")
        except (ValueError, TypeError) as err:  # e.g. null bytes
            problems.append(f"{path.relative_to(base).as_posix()}: {type(err).__name__}: {err}")
    return problems


def check_sources_or_raise(root: Optional[Path] = None) -> List[str]:
    """Logs every framework file that does not compile; raises RuntimeError for any unless ALLOW_BROKEN_SOURCES=true."""
    problems = broken_sources(root)
    if not problems:
        return problems
    for problem in problems:
        logger.critical(f"Source check: {problem}")
    if str(os.getenv("ALLOW_BROKEN_SOURCES", "")).strip().lower() in ("1", "true", "yes", "on"):
        logger.critical(f"Source check: {len(problems)} framework file(s) do not compile; starting anyway "
                        "(ALLOW_BROKEN_SOURCES=true). The checks in these files are OFF for this run.")
        return problems
    raise RuntimeError(
        f"{len(problems)} framework file(s) do not compile, so the checks in them would silently be off for the whole run: "
        + "; ".join(problems[:5]) + ". Fix them (git diff shows local edits) or set ALLOW_BROKEN_SOURCES=true to start anyway.")

# END OF FILE src/utils/source_health.py
