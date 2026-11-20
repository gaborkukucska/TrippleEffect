# START OF FILE src/utils/cg_command_check.py
"""
Round 5p: a Constitutional Guardian concern about GP008 (Safe Command Execution) on a message whose commands only work on
the project's own files is cleared by the framework.

2026-11-20 20:22 PM1 told W1 to read its test file's imports (`python -c "import ast; ... open('tests/...').read() ..."`),
`cat src/game_logic.py`, change an import and run `pytest tests/test_game_logic/`. The Guardian (qwen3.5:4b) answered
"GP008: ... read arbitrary files outside the project workspace boundaries (e.g. `cat src/game_logic.py`)"; GP008 is a
critical principle, so the concern went to the user and the WHOLE system paused for 78 minutes until the user answered.

command_executor runs every command in the project's workspace, and the framework itself refuses stop commands that reach
outside the project (Round 5o, kill_guard). A GP008-only concern is cleared when the message names commands and every one
of them is ordinary project work: no absolute path but /workspace, no `~` or `..`, no sudo / su, no recursive or forced
rm, no chmod / chown, no download (curl / wget / pip download into a shell), nothing piped into a shell, no disk, network
or system administration (dd, mkfs, mount, iptables, systemctl, shutdown, reboot, ssh, scp, nc), no fork bomb.
"""
import logging
import re
from typing import List

logger = logging.getLogger(__name__)

_PRINCIPLE_RE = re.compile(r"\bGP\d{3}\b")
_FENCE_RE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_UNSAFE_RE = re.compile(
    r"\bsudo\b|\bsu\s|\brm\s+-\w*[rRf]|\bchmod\b|\bchown\b|\bcurl\b|\bwget\b|\|\s*(?:ba|z|da)?sh\b|\bsh\s+-c\b|"
    r"\beval\b|\bdd\b|\bmkfs|\bmount\b|\biptables\b|\bsystemctl\b|\bservice\s+\w+\s+(?:stop|restart)|\bshutdown\b|"
    r"\breboot\b|\bssh\b|\bscp\b|\bnc\s|\bnetcat\b|:\(\)\s*\{|\bcrontab\b|\buseradd\b|\bpasswd\b|\bkillall\b|"
    r"\bpkill\b|\bkill\s+-9\s+-1\b|https?://|\bftp://")
# an absolute path (not /workspace) - "tests/x.py" and "a/b" have a word character before the slash
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.:/])/(?!workspace\b)[A-Za-z_.~]")


def commands_in(text: str) -> List[str]:
    """The commands (and code) a message quotes: fenced blocks line by line, then `inline` spans."""
    found: List[str] = []
    rest = str(text or "")
    for block in _FENCE_RE.findall(rest):
        found.extend(line.strip() for line in block.splitlines() if line.strip())
    rest = _FENCE_RE.sub(" ", rest)
    found.extend(span.strip() for span in _INLINE_RE.findall(rest) if span.strip())
    return found


def unsafe_command(command: str) -> bool:
    text = str(command or "")
    return bool(_UNSAFE_RE.search(text) or _ABSOLUTE_PATH_RE.search(text) or "~" in text or ".." in text)


def project_only_gp008_concern(concern: str, held_text: str) -> bool:
    """True when 'concern' names only GP008 and every command 'held_text' quotes works on the project's own files."""
    try:
        principles = set(_PRINCIPLE_RE.findall(str(concern or "")))
        if principles != {"GP008"}:
            return False
        commands = commands_in(held_text)
        if not commands:
            return False
        return not any(unsafe_command(command) for command in commands)
    except Exception as err:
        logger.debug(f"project_only_gp008_concern failed: {err}")
        return False

# END OF FILE src/utils/cg_command_check.py
