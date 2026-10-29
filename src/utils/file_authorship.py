# START OF FILE src/utils/file_authorship.py
"""
Who last changed a workspace file, and for which task (Round 2n).

2026-10-20 23:25-23:55 (WiFi_Snake_Game, qwen3.5:9b): W1 wrote src/game_logic.py and src/app.py itself, found its own code
broken and sent complete corrected versions. Every one was refused "already exists": a new version renamed a class
(GameStateManager -> GameState) or dropped imports, so it did not "keep every name" of the old file. W1 then sent the
action 'force_overwrite_file_write' 5 times and was moved between worker_work and worker_report for 30 minutes.
At 23:26:25 PM1 marked Kick-off Task 1 finished although src/app.py (written by W1 for that task) used an undefined name:
the completion check only looked at Python files NAMED in the task description, and that task named only data/scores.db.

The tool executor records every successful file change here (path -> agent and the agent's active task):
* file_system 'write' replaces a file the SAME agent last wrote without force_overwrite (its own work, this server run);
* the completion check also checks the files the assignee changed while working on the task.

In memory only: after a restart nothing is known, so files are protected as before. Never raises.
"""
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]
MAX_RECORDS = 5000

_lock = threading.Lock()
_records: Dict[str, Dict[str, Any]] = {}
# Round 4e: the FIRST recorded change of each file (who made it) - the owner of a test file whose tests fail later
_first_records: Dict[str, Dict[str, Any]] = {}


def _key(path: PathLike) -> Optional[str]:
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def record_file_change(path: PathLike, agent_id: Optional[str], task_id: Optional[str] = None,
                       action: Optional[str] = None) -> None:
    """Remember that agent_id changed path (while working on task_id)."""
    key = _key(path)
    if not key or not agent_id:
        return
    with _lock:
        _records[key] = {"agent_id": str(agent_id), "task_id": str(task_id) if task_id else None,
                         "action": action, "time": time.time()}
        if key not in _first_records and len(_first_records) < MAX_RECORDS:
            _first_records[key] = dict(_records[key])
        if len(_records) > MAX_RECORDS:
            for old_key, _ in sorted(_records.items(), key=lambda item: item[1]["time"])[:len(_records) - MAX_RECORDS]:
                _records.pop(old_key, None)


def forget_file(path: PathLike) -> None:
    """The file was deleted or moved away."""
    key = _key(path)
    if key:
        with _lock:
            _records.pop(key, None)
            _first_records.pop(key, None)


def last_change(path: PathLike) -> Optional[Dict[str, Any]]:
    """{'agent_id', 'task_id', 'action', 'time'} of the last recorded change of path, or None."""
    key = _key(path)
    if not key:
        return None
    with _lock:
        record = _records.get(key)
        return dict(record) if record else None


def first_change(path: PathLike) -> Optional[Dict[str, Any]]:
    """Round 4e: {'agent_id', 'task_id', 'action', 'time'} of the FIRST recorded change of path in this server run, or None."""
    key = _key(path)
    if not key:
        return None
    with _lock:
        record = _first_records.get(key)
        return dict(record) if record else None


def last_writer(path: PathLike) -> Optional[str]:
    """The agent that last changed path in this server run, or None."""
    record = last_change(path)
    return record["agent_id"] if record else None


def _same_task(recorded: Optional[str], wanted: str) -> bool:
    if not recorded or not wanted:
        return False
    recorded, wanted = recorded.strip().lower(), wanted.strip().lower()
    if recorded == wanted:
        return True
    # task ids are UUIDs; a model sometimes copies only the first 8 characters
    short = min(len(recorded), len(wanted))
    return short >= 8 and recorded[:short] == wanted[:short]


def files_changed_for_task(task_id: Optional[str], workspace: Optional[PathLike] = None) -> List[Path]:
    """Existing files whose last change was made for task_id (inside workspace if given), oldest change first."""
    if not task_id:
        return []
    root = _key(workspace) if workspace else None
    with _lock:
        items = sorted(_records.items(), key=lambda item: item[1]["time"])
    found: List[Path] = []
    for key, record in items:
        if not _same_task(record.get("task_id"), str(task_id)):
            continue
        if root and not (key == root or key.startswith(root.rstrip(os.sep) + os.sep)):
            continue
        path = Path(key)
        try:
            if path.is_file():
                found.append(path)
        except OSError:
            continue
    return found


def reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _records.clear()
        _first_records.clear()


# --- Which files a tool call changed ------------------------------------------------------------------------------------------
_PATH_KEYS = ("filename", "path", "file_path", "filepath", "file", "file_name", "target_file")
_DESTINATION_KEYS = ("destination_path", "destination", "dest", "target_path", "new_path")


def _first_text(args: Dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def tool_call_base_path(tool_name: str, args: Dict[str, Any], agent_sandbox_path: Optional[PathLike],
                        project_name: Optional[str], session_name: Optional[str]) -> Optional[Path]:
    """The directory a file_system / code_editor call's relative paths refer to (same defaults as the tools)."""
    scope = str(args.get("scope") or ("shared" if project_name and session_name else "private")).strip().lower()
    if scope == "shared":
        if not project_name or not session_name:
            return None
        from src.config.settings import settings
        from src.tools.sandbox_runner import resolve_shared_workspace
        return resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_name), str(session_name))
    if scope == "private":
        return Path(agent_sandbox_path) if agent_sandbox_path else None
    if scope == "projects":
        from src.config.settings import settings
        return Path(settings.PROJECTS_BASE_DIR)
    return None


def record_tool_call_changes(tool_name: str, args: Dict[str, Any], agent_id: str, task_id: Optional[str],
                             agent_sandbox_path: Optional[PathLike], project_name: Optional[str],
                             session_name: Optional[str]) -> List[Path]:
    """
    Record the files a SUCCESSFUL file_system / code_editor change call touched. Returns the recorded paths.
    Deleting forgets a file; move/rename forgets the source and records the destination.
    """
    try:
        if not isinstance(args, dict):
            return []
        base = tool_call_base_path(tool_name, args, agent_sandbox_path, project_name, session_name)
        if base is None:
            return []
        action = str(args.get("action") or "").strip().strip("<>/\"' \t").lower()
        source = _first_text(args, _PATH_KEYS)
        destination = _first_text(args, _DESTINATION_KEYS)

        def full(rel: str) -> Path:
            return Path(base) / rel.lstrip("/\\")

        recorded: List[Path] = []
        if tool_name == "file_system" and action in ("delete", "delete_file", "remove_file", "remove"):
            if source:
                forget_file(full(source))
            return []
        if tool_name == "file_system" and action in ("move", "move_file", "rename", "rename_file"):
            if source:
                forget_file(full(source))
            if destination:
                record_file_change(full(destination), agent_id, task_id, action)
                recorded.append(full(destination))
            return recorded
        if tool_name == "file_system" and action == "copy":
            if destination:
                record_file_change(full(destination), agent_id, task_id, action)
                recorded.append(full(destination))
            return recorded
        if source:
            target = full(source)
            try:
                if target.is_dir():
                    return []
            except OSError:
                return []
            record_file_change(target, agent_id, task_id, action)
            recorded.append(target)
        return recorded
    except Exception as err:  # bookkeeping must never break a tool call
        logger.debug(f"file_authorship: could not record changes of {tool_name} call: {err}")
        return []

# END OF FILE src/utils/file_authorship.py
