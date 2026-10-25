# START OF FILE src/tools/command_hints.py
"""
Round 3n: notes for two command mistakes of 2026-10-25 (WiFi_Multiplayer_Snake_Game, Python).

* 03:47:16 and 03:47:49 W2 started `python src/server.py &`; src/server.py imports `from src.snake_game import ...`, so
  Python looked for a package 'src' inside src/ and stopped with "ModuleNotFoundError: No module named 'src'" - twice.
* 03:48:49 W2 ran `pkill -f "python src/server.py"`: the pattern also matches the shell that runs pkill (its own command
  line contains the pattern), so pkill killed that shell - exit code -15 - and the server it meant may still run.

command_notes() returns the notes for a command and its output (empty list if none). Never raises.
"""
import re
from pathlib import Path
from typing import List, Optional

_PYTHON_SCRIPT_RE = re.compile(r"(?:^|[;&|]\s*|\s)(?:\S*/)?python[\d.]*\s+(?:-u\s+)?(?P<script>(?P<package>[A-Za-z_]\w*)/"
                               r"(?:[A-Za-z_]\w*/)*(?P<module>[A-Za-z_]\w*)\.py)\b")
_SELF_KILL_RE = re.compile(r"\b(?:pkill|pgrep|killall)\b[^;&|]*\s-\w*f\w*\s+(?P<q>['\"]?)(?P<pattern>[^'\"]+)(?P=q)")


def _module_path(script: str) -> str:
    return script[:-3].replace("/", ".")


def command_notes(workspace: Optional[Path], command: Optional[str], returncode: Optional[int] = None,
                  output: Optional[str] = None) -> List[str]:
    notes: List[str] = []
    try:
        text = str(command or "")
        out = str(output or "")
        match = _PYTHON_SCRIPT_RE.search(text)
        if match:
            package, script = match.group("package"), match.group("script")
            imports_package = False
            if workspace is not None:
                try:
                    source = (Path(workspace) / script).read_text(encoding="utf-8", errors="replace")
                    imports_package = re.search(rf"^\s*(?:from|import)\s+{re.escape(package)}\.", source, re.MULTILINE) \
                        is not None
                except OSError:
                    pass
            if imports_package or f"No module named '{package}'" in out:
                notes.append(f"`python {script}` puts {package}/ itself on the import path, so `from {package}.<module> "
                             f"import ...` inside it fails with \"No module named '{package}'\". Run it from the project "
                             f"folder as a module instead: `python -m {_module_path(script)}` (add ' &' to keep a server "
                             "running). Do not change the imports for this.")
        kill = _SELF_KILL_RE.search(text)
        if kill and returncode in (-15, -9, 143, 137):
            pattern = kill.group("pattern").strip()
            safe = f"[{pattern[0]}]{pattern[1:]}" if pattern and pattern[0].isalnum() else pattern
            notes.append(f"`{text.strip()[:80]}` killed ITSELF (exit code {returncode}): the shell that ran it has "
                         f"'{pattern}' in its own command line, so the -f pattern matched it too. The process you meant may "
                         f"still be running. Use a pattern that cannot match itself: `pkill -f '{safe}'`.")
    except Exception:
        return notes
    return notes

# END OF FILE src/tools/command_hints.py
