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
        if _NPM_RUN_RE.search(text) and _NO_PACKAGE_JSON_RE.search(out):
            notes.append(npm_without_package_json_note(workspace, text))
        # Round 4f: exit code 0, but no test ran (node --test without file names finds no *.spec.js file)
        from src.utils.test_runs import exit_code_is_the_tests, ran_no_tests
        if returncode == 0 and exit_code_is_the_tests(text) and ran_no_tests(out):
            from src.utils.test_runs import no_tests_problem
            notes.append("This run passed only because " + no_tests_problem(workspace, text.strip()[:80])
                         .replace("the last full test run", "the command", 1) if workspace is not None else
                         f"`{text.strip()[:80]}` ran NO tests (exit code 0 means nothing here): name the test files.")
    except Exception:
        return notes
    return notes


# --- Round 3x: npm without a package.json ----------------------------------------------------------------------------------
# 2026-10-27 09:26-09:53 `npm test` ended with exit code 254 eight times ("Could not read package.json"): the project had no
# package.json - its plan runs JavaScript tests with `node --test`. The workers took the output for a broken test setup and
# installed jest, wrote a jest.config.js and a package.json.
_NPM_RUN_RE = re.compile(r"(?:^|[;&|]\s*)npm\s+(?:test|t|start|run(?:-script)?)\b")
_NO_PACKAGE_JSON_RE = re.compile(r"Could not read package\.json|ENOENT[^\n]{0,120}package\.json", re.IGNORECASE)


def npm_without_package_json_note(workspace: Optional[Path], command: str) -> str:
    """What `npm test` / `npm start` without a package.json means, and what runs this project's tests instead."""
    planned = None
    js_tests: List[str] = []
    try:
        if workspace is not None:
            from src.utils.js_test_checks import js_test_files, planned_js_runner
            planned = planned_js_runner(workspace)
            js_tests = js_test_files(workspace, limit=5)
    except Exception:
        pass
    note = (f"`{command.strip()[:40]}` needs a package.json, and this project has none: npm ran nothing (exit code 254 is "
            "npm's \"file not found\"). ")
    if planned in ("jest", "vitest", "mocha"):
        return note + (f"PROJECT_STRUCTURE.md runs the JavaScript tests with {planned}: create package.json with "
                       f"`\"scripts\": {{\"test\": \"{planned}\"}}`, run `npm install --save-dev {planned}`, then `npm test`.")
    if js_tests:
        from src.utils.js_test_checks import node_test_command  # Round 4f: the files named, not a folder
        note += (f"Run the JavaScript tests ({', '.join(js_tests[:3])}) with Node's built-in runner: "
                 f"`{node_test_command(workspace)}`; a "
                 "test file that calls describe() / test() takes them from it with `const { describe, test } = "
                 "require('node:test');`.")
        if planned == "node --test":
            note += " PROJECT_STRUCTURE.md says the project's JavaScript tests run with `node --test`: do not install jest."
        return note
    return note + "Use `node <file>` for a script, or create package.json only if your task asks for one."


# --- Round 5i: what a command started with ' &' did in its first seconds ----------------------------------------------------
# 2026-11-17 19:30:17-19:41:18 (WiFi_Snake_Multiplayer) W3 started `python server/server.py &` four times. Each result said
# "Command started in the background ... Read that file to check on it"; each server had crashed within a second with
# "OSError: [Errno 98] Address already in use" - nobody read the logs until the PM's audit at 19:47. In between W3 ran
# `ps aux | grep ...` six times and `pkill`, `lsof`, `fuser`, guessing. The framework looks once, shortly after the start.
BACKGROUND_PEEK_SECONDS = 2.5
_ADDRESS_IN_USE_RE = re.compile(r"EADDRINUSE|address already in use", re.IGNORECASE)


def _log_text(workspace: Path, log_rel: str, limit: int = 20000) -> str:
    try:
        text = (Path(workspace) / log_rel).read_text(encoding="utf-8", errors="replace")
        return text[-limit:]
    except OSError:
        return ""


def _started_port(workspace: Path, command: str, output: str) -> Optional[int]:
    """The port of the program a background command starts: from its error output, else from the file it runs."""
    try:
        from src.utils.program_run_check import _FILE_COMMAND_RE, _port_from_output, server_entry, server_port
        port = _port_from_output(output)
        if port:
            return port
        match = _FILE_COMMAND_RE.search(command or "")
        candidates = []
        if match:
            target = match.group(2).lstrip("./")
            cd = re.match(r"^\s*cd\s+([\w./-]+)\s*(?:&&|;)", command or "")
            candidates = [f"{cd.group(1).strip('/')}/{target}" if cd else target, target]
        entry = server_entry(workspace)
        if entry:
            candidates.append(entry[1])
        for rel in candidates:
            if (Path(workspace) / rel).is_file():
                port = server_port(workspace, rel)
                if port:
                    return port
    except Exception:
        return None
    return None


def _port_in_use_text(workspace: Path, command: str, output: str) -> str:
    """Who holds the port of an 'Address already in use' - a running copy of the project, another program, or nobody."""
    from src.utils.program_run_check import port_open, port_owner_pid, process_start_time
    port = _started_port(workspace, command, output)
    if not port:
        return ("\nIts port is already in use: either a copy started earlier still runs (stop that one first) or the "
                "program itself opens the same port twice (e.g. an HTTP server AND a socket server on one port).")
    if not port_open(port):
        return (f"\nNothing holds port {port} now, so the program itself opened port {port} TWICE (e.g. an HTTP server for "
                "the page AND a second socket server on the same port): ONE server must do both. Starting it again gives "
                "the same error.")
    pid = port_owner_pid(port, workspace)
    if pid:
        import time as _time
        started = process_start_time(pid)
        when = f", started at {_time.strftime('%H:%M:%S', _time.localtime(started))}" if started else ""
        return (f"\nPort {port} is held by a copy of THIS project's program that still runs (pid {pid}{when}) - it runs the "
                f"code as it was when it started. Stop it with `kill {pid}`, then start the program again (and check that "
                "this result says it keeps running).")
    return (f"\nPort {port} is held by ANOTHER program (not one of this project). Do not stop it: make the project use a "
            "free port.")


async def background_start_note(workspace: Optional[Path], log_rel: Optional[str], pgid: Optional[int], command: str,
                                 wait: Optional[float] = None) -> str:
    """
    Round 5i: the framework's look at a command started with ' &' after `wait` seconds - still running (with its first
    output), or already ENDED with the error it printed (and who holds the port of an "Address already in use").
    '' when it cannot tell. Never raises.
    """
    try:
        import asyncio
        if not workspace or not log_rel:
            return ""
        wait = BACKGROUND_PEEK_SECONDS if wait is None else float(wait)
        await asyncio.sleep(max(0.0, wait))
        from src.tools.sandbox_runner import group_alive
        alive = group_alive(pgid)
        text = _log_text(Path(workspace), log_rel)
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if alive:
            shown = "\n".join(lines[-3:])
            return (f"[Framework check {wait:.1f} s after the start:] the background process is still RUNNING."
                    + (f" Its output so far ({log_rel}):\n{shown}" if shown else " It printed nothing yet."))
        if not lines:
            return (f"[Framework check {wait:.1f} s after the start:] the background process has already ENDED, without "
                    "any output - it does NOT keep running (a server must: e.g. its start function is never called, or "
                    "it only defines code).")
        from src.utils.program_run_check import error_excerpt
        note = (f"[Framework check {wait:.1f} s after the start:] the background process has already ENDED - it is NOT "
                f"running. Its output ({log_rel}):\n{error_excerpt(text, workspace)}")
        from src.utils.program_run_check import known_cause_hint
        cause = known_cause_hint(text)  # Round 5j
        if cause:
            note += cause
        elif _ADDRESS_IN_USE_RE.search(text):
            note += _port_in_use_text(Path(workspace), command, text)
        else:
            note += "\nFix that error and start it again."
        return note
    except Exception:
        return ""

# END OF FILE src/tools/command_hints.py
