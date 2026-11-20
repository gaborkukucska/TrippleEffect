# START OF FILE src/utils/kill_guard.py
"""
Round 5o: a command that stops processes stops only the project's own.

2026-11-20 11:45:07 PM1 restarted the game with `pkill -f "python.*main" 2>/dev/null; sleep 1; cd /workspace && python -m
src.main &`. The project's server was `src/main.py` - and so is TrippleEffect's (`python -m src.main`): the pattern matched
the framework, the framework shut down, and the run ended ("the system crashed").

Before a command runs, the processes its stop commands would reach are worked out from /proc (Linux):
  pkill / pgrep (-f: the full command line, else the process name; -x exact; -i any case), killall (names, -r regex),
  kill PID..., kill $(pgrep ...) / pgrep ... | xargs kill, ps ... | grep X | ... | xargs kill, fuser -k PORT/tcp,
  kill $(lsof -t -i:PORT) / lsof ... | xargs kill.
A process is the project's when its working folder is the workspace or inside it, and it is neither the framework nor one
of the framework's parents. A command that would reach any other process is NOT run; the refusal names those processes and
the project's processes the command matches (by pid), so the agent stops exactly those with `kill <pid>`.
Elsewhere than Linux, or when /proc cannot be read, nothing is judged.
"""
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\n|&(?!>))\s*")
_PORT_RE = re.compile(r"(?:-\w*i\s*(?:tcp)?:?|\s)(\d{2,5})(?:/tcp|/udp)?\b")
_STOP_WORDS = ("kill", "pkill", "killall", "fuser")


def _processes() -> Dict[int, Dict[str, Any]]:
    """pid -> {comm, cmdline, cwd} of every process /proc shows (those it lets us read)."""
    found: Dict[int, Dict[str, Any]] = {}
    proc = Path("/proc")
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            state = (pid_dir / "stat").read_text().rsplit(")", 1)[1].split()[0]
            if state in ("Z", "X"):
                continue
            comm = (pid_dir / "comm").read_text().strip()
            cmdline = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except (OSError, IndexError):
            continue
        try:
            cwd = os.readlink(pid_dir / "cwd")
        except OSError:
            cwd = ""
        found[int(pid_dir.name)] = {"comm": comm, "cmdline": cmdline, "cwd": cwd}
    return found


def _framework_pids() -> set:
    """The framework's process and its parents (stopping a parent stops the framework)."""
    pids = set()
    pid = os.getpid()
    for _ in range(64):
        if pid <= 1 or pid in pids:
            break
        pids.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return pids


def _in_workspace(info: Dict[str, Any], root: Path) -> bool:
    if not info.get("cwd"):
        return False
    try:
        cwd = Path(info["cwd"]).resolve()
    except OSError:
        return False
    return cwd == root or root in cwd.parents


def _words(segment: str) -> List[str]:
    """The words of a simple command without its redirections (`2>/dev/null`, `> log 2>&1`)."""
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    kept: List[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        if re.fullmatch(r"\d*(?:>>?|<|&>)", word):
            skip_next = True
            continue
        if re.match(r"^\d*(?:>>?|<|&>)", word):
            continue
        kept.append(word)
    return kept


def _name_matches(words: List[str], processes: Dict[int, Dict[str, Any]]) -> List[int]:
    """The pids `pkill` / `pgrep` with these arguments would select."""
    full = exact = ignore_case = False
    patterns: List[str] = []
    skip_next = False
    for word in words[1:]:
        if skip_next:
            skip_next = False
            continue
        if word.startswith("--"):
            full = full or word == "--full"
            exact = exact or word == "--exact"
            ignore_case = ignore_case or word == "--ignore-case"
            continue
        if word.startswith("-") and len(word) > 1:
            letters = word[1:]
            if letters.isdigit() or letters.isupper():  # -9, -TERM, -KILL
                continue
            full = full or "f" in letters
            exact = exact or "x" in letters
            ignore_case = ignore_case or "i" in letters
            if letters[-1] in "uUgGtsPF" or letters in ("signal",):  # options that take a value
                skip_next = True
            continue
        patterns.append(word)
    if not patterns:
        return []
    try:
        regex = re.compile(patterns[0], re.IGNORECASE if ignore_case else 0)
    except re.error:
        regex = re.compile(re.escape(patterns[0]), re.IGNORECASE if ignore_case else 0)
    hits = []
    for pid, info in processes.items():
        text = info["cmdline"] if full else info["comm"]
        if text and (regex.fullmatch(text) if exact else regex.search(text)):
            hits.append(pid)
    return hits


def _killall_matches(words: List[str], processes: Dict[int, Dict[str, Any]]) -> List[int]:
    regex_mode = any(w in ("-r", "--regexp") for w in words[1:])
    ignore_case = any(w in ("-I", "--ignore-case") for w in words[1:])
    names = [w for w in words[1:] if not w.startswith("-")]
    hits = []
    for pid, info in processes.items():
        argv0 = info["cmdline"].split(" ", 1)[0].rsplit("/", 1)[-1] if info["cmdline"] else ""
        for name in names:
            if regex_mode:
                try:
                    if re.search(name, info["comm"], re.IGNORECASE if ignore_case else 0):
                        hits.append(pid)
                        break
                except re.error:
                    continue
            elif (info["comm"] == name[:15] or argv0 == name) if not ignore_case else \
                    (info["comm"].lower() == name[:15].lower() or argv0.lower() == name.lower()):
                hits.append(pid)
                break
    return hits


def _port_pids(port: int, processes: Dict[int, Dict[str, Any]]) -> List[int]:
    """The pids that hold a listening TCP socket on this port."""
    inodes = set()
    for table in ("tcp", "tcp6"):
        try:
            lines = Path(f"/proc/net/{table}").read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            try:
                if len(fields) > 9 and fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == int(port):
                    inodes.add(f"socket:[{fields[9]}]")
            except ValueError:
                continue
    if not inodes:
        return []
    hits = []
    for pid in processes:
        try:
            fds = list(Path(f"/proc/{pid}/fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(fd) in inodes:
                    hits.append(pid)
                    break
            except OSError:
                continue
    return hits


def targets(command: str, processes: Dict[int, Dict[str, Any]]) -> Tuple[List[int], List[str]]:
    """(the pids the command's stop commands would reach, the stop commands) - see the module docstring."""
    text = str(command or "")
    if not re.search(r"\b(?:" + "|".join(_STOP_WORDS) + r")\b", text):
        return [], []
    pids: List[int] = []
    shown: List[str] = []
    stops = bool(re.search(r"\b(?:kill|pkill|killall)\b|\bfuser\b[^;&|]*\s-\w*k", text))
    for segment in _SEGMENT_SPLIT_RE.split(text):
        for part in segment.split("|"):
            part = part.strip()
            # what $( ... ) / ` ... ` inside a kill selects
            inner = re.findall(r"\$\(([^)]*)\)|`([^`]*)`", part)
            pieces = [part] + [a or b for a, b in inner]
            for piece in pieces:
                words = _words(re.sub(r"\$\([^)]*\)|`[^`]*`", "", piece) if piece is part else piece)
                if not words:
                    continue
                while words and words[0] in ("sudo", "nohup", "exec", "command"):
                    words = words[1:]
                if not words:
                    continue
                head = words[0].rsplit("/", 1)[-1]
                if head == "pkill" or (head == "pgrep" and stops):
                    hits = _name_matches(words, processes)
                elif head == "grep" and stops and re.search(r"\bxargs\s+(?:-\S+\s+)*kill\b|\bkill\b", text) \
                        and not any(w.startswith("-") and "v" in w[1:] and not w.startswith("--") for w in words[1:]):
                    # ps aux | grep X | awk '{print $2}' | xargs kill
                    pattern = [w for w in words[1:] if not w.startswith("-")]
                    hits = _name_matches(["pgrep", "-f"] + pattern[:1], processes) if pattern else []
                elif head == "killall":
                    hits = _killall_matches(words, processes)
                elif head == "kill":
                    hits = []
                    for word in words[1:]:
                        if re.fullmatch(r"-?\d+", word):
                            number = int(word)
                            if number <= 0:
                                hits.append(number)  # kill 0 / kill -1: a whole group / every process
                            elif number in processes:
                                hits.append(number)
                elif head in ("fuser", "lsof") and stops:
                    hits = []
                    for match in _PORT_RE.finditer(" " + " ".join(words[1:])):
                        hits.extend(_port_pids(int(match.group(1)), processes))
                else:
                    continue
                if hits:
                    pids.extend(h for h in hits if h not in pids)
                    if part not in shown:
                        shown.append(part)
    return pids, shown


def kill_guard_problem(command: str, workspace: Any) -> Optional[str]:
    """The refusal for a command that would stop a process that is not the project's, or None (see the module docstring)."""
    try:
        if not Path("/proc/self").exists():
            return None
        if not re.search(r"\b(?:" + "|".join(_STOP_WORDS) + r")\b", str(command or "")):
            return None
        processes = _processes()
        pids, stops = targets(command, processes)
        if not pids:
            return None
        root = Path(workspace).resolve()
        framework = _framework_pids()
        own: List[int] = []
        other: List[str] = []
        for pid in pids:
            if pid <= 0:
                other.append(f"every process of the group / of the user (`kill {pid}`)")
                continue
            info = processes.get(pid, {})
            cmdline = " ".join(str(info.get("cmdline", "")).split())[:90] or info.get("comm", "?")
            if pid in framework:
                other.append(f"pid {pid} `{cmdline}` - TrippleEffect ITSELF, the framework every agent runs in: stopping "
                             "it ends the whole run")
            elif _in_workspace(info, root):
                own.append(pid)
            else:
                other.append(f"pid {pid} `{cmdline}` - not started in the project's workspace")
        if not other:
            return None
        lines = "\n".join(f"- {entry}" for entry in other[:5])
        stop_text = "; ".join(f"`{s}`" for s in stops[:2]) or "this command"
        if own:
            mine = "\n".join(f"- pid {pid} `{' '.join(str(processes[pid]['cmdline']).split())[:90]}`" for pid in own[:6])
            advice = (f"The project's own processes it matches (started in the workspace):\n{mine}\nStop exactly those: "
                      f"`kill {' '.join(str(p) for p in own[:6])}` - then start the program again.")
        else:
            advice = ("None of the project's own processes match it: there is nothing of the project to stop. If a port is "
                      "in use, the framework's note on the start says which process holds it.")
        logger.warning(f"kill_guard: refused `{command}`: it would stop {len(other)} process(es) outside the project.")
        return (f"NOT run: {stop_text} would also stop processes that are NOT the project's:\n{lines}\n"
                "A name pattern (pkill -f, killall, pgrep) matches every program on this computer with that name - the "
                "framework itself is a Python program started as `src.main`. Stop the project's programs by their pid.\n"
                + advice)
    except Exception as err:
        logger.debug(f"kill_guard_problem failed: {err}")
        return None

# END OF FILE src/utils/kill_guard.py
