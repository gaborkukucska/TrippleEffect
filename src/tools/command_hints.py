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
        note += (f"Run the JavaScript tests ({', '.join(js_tests[:3])}) with Node's built-in runner: `node --test tests/`; a "
                 "test file that calls describe() / test() takes them from it with `const { describe, test } = "
                 "require('node:test');`.")
        if planned == "node --test":
            note += " PROJECT_STRUCTURE.md says the project's JavaScript tests run with `node --test`: do not install jest."
        return note
    return note + "Use `node <file>` for a script, or create package.json only if your task asks for one."

# END OF FILE src/tools/command_hints.py
