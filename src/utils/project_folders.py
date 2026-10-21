# START OF FILE src/utils/project_folders.py
"""
A new project plan whose title matches an existing project (Round 2o).

2026-10-21 15:45: Admin AI planned "WiFi Snake Game" again. The title became 'WiFi_Snake_Game', the folder of the run the
night before, so the new run silently became one more session ('startup_1792568609' next to 'startup_1792509061') of the old
project, with an empty workspace and the old project's database record. Now the approval card asks the user:

* continue the existing project - same project folder; the files of its latest session are copied into the new session's
  workspace (never over a file that is already there), so the team builds on them;
* start a new project - a free name ('WiFi_Snake_Game_2'); the kickoff task created before the approval moves there.

Never raises: problems are logged and the caller gets an empty result.
"""
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SESSION_MARKERS = ("agent_session_data.json", "shared_workspace", "task_data")
# Files the kickoff writes for the NEW plan; the earlier ones are kept under another name or not copied
FRAMEWORK_WORKSPACE_FILES = {"PROJECT_STRUCTURE.md", "conftest.py"}
EARLIER_PLAN_NAME = "PREVIOUS_PROJECT_PLAN.md"
SKIPPED_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv", "venv", ".te_background", ".mypy_cache"}
MAX_LISTED_FILES = 40


def _workspace_files(workspace: Path) -> List[Path]:
    files: List[Path] = []
    if not workspace.is_dir():
        return files
    for path in sorted(workspace.rglob("*")):
        rel_parts = path.relative_to(workspace).parts
        if any(part in SKIPPED_DIRS for part in rel_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def _session_dirs(project_dir: Path) -> List[Path]:
    sessions = []
    if not project_dir.is_dir():
        return sessions
    for child in project_dir.iterdir():
        if child.is_dir() and any((child / marker).exists() for marker in SESSION_MARKERS):
            sessions.append(child)
    return sessions


def _last_change(session_dir: Path) -> float:
    newest = 0.0
    for path in session_dir.rglob("*"):
        try:
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def unique_project_name(projects_base: Path, name: str) -> str:
    """'name_2', 'name_3', ... - the first one without a folder under projects_base."""
    stem = re.sub(r"_\d+$", "", name) or name
    number = 2
    while (Path(projects_base) / f"{stem}_{number}").exists():
        number += 1
    return f"{stem}_{number}"


def existing_project_info(projects_base: Path, project_name: str, current_session: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Earlier sessions of a project with this name, or None if there are none. A session of the current server run counts
    only if its workspace already has files (it then held an earlier project with the same title).
    """
    try:
        project_dir = Path(projects_base) / project_name
        sessions = []
        for session_dir in _session_dirs(project_dir):
            files = _workspace_files(session_dir / "shared_workspace")
            if session_dir.name == current_session and not files:
                continue
            changed = _last_change(session_dir)
            sessions.append({
                "name": session_dir.name,
                "files": len(files),
                "last_changed": datetime.fromtimestamp(changed).isoformat(timespec="minutes") if changed else None,
                "_mtime": changed,
            })
        if not sessions:
            return None
        sessions.sort(key=lambda s: s["_mtime"])
        for session in sessions:
            session.pop("_mtime", None)
        return {
            "name": project_name,
            "sessions": sessions,
            "latest_session": sessions[-1]["name"],
            "suggested_new_name": unique_project_name(Path(projects_base), project_name),
        }
    except Exception as err:
        logger.warning(f"project_folders: could not inspect existing project '{project_name}': {err}")
        return None


def carry_over_workspace(projects_base: Path, project_name: str, from_session: Optional[str],
                         to_session: str) -> Tuple[Optional[str], List[str]]:
    """
    'Continue the existing project': copy the files of from_session's workspace (default: the latest other session) into
    to_session's workspace. Existing files are never overwritten; the earlier PROJECT_PLAN.md becomes
    PREVIOUS_PROJECT_PLAN.md and the framework files of the kickoff are not copied. Returns (session used, copied paths).
    """
    try:
        project_dir = Path(projects_base) / project_name
        if not from_session:
            candidates = [s for s in _session_dirs(project_dir) if s.name != to_session]
            if not candidates:
                return None, []
            from_session = max(candidates, key=_last_change).name
        if from_session == to_session:
            return None, []
        source = project_dir / from_session / "shared_workspace"
        target = project_dir / to_session / "shared_workspace"
        copied: List[str] = []
        for path in _workspace_files(source):
            rel = path.relative_to(source)
            rel_text = rel.as_posix()
            if rel_text in FRAMEWORK_WORKSPACE_FILES:
                continue
            destination = target / (EARLIER_PLAN_NAME if rel_text == "PROJECT_PLAN.md" else rel)
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied.append(destination.relative_to(target).as_posix())
        logger.info(f"project_folders: carried {len(copied)} file(s) of '{project_name}/{from_session}' over to '{to_session}'.")
        return from_session, copied
    except Exception as err:
        logger.error(f"project_folders: could not carry the workspace of '{project_name}' over: {err}", exc_info=True)
        return None, []


def move_pending_project(projects_base: Path, old_name: str, new_name: str, session_name: str) -> bool:
    """
    'Start a new project': move the task data created for the plan before the approval (the kickoff task) from
    old_name/session to new_name/session and give its tasks the new project name. True if task data was moved.
    """
    try:
        old_session = Path(projects_base) / old_name / session_name
        new_session = Path(projects_base) / new_name / session_name
        moved = False
        old_tasks = old_session / "task_data"
        if old_tasks.is_dir() and not (new_session / "task_data").exists():
            new_session.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_tasks), str(new_session / "task_data"))
            moved = True
            try:
                old_session.rmdir()  # only if nothing else is in it
            except OSError:
                pass
        else:
            new_session.mkdir(parents=True, exist_ok=True)
        if moved:
            rename_task_project(new_session / "task_data", old_name, new_name)
        return moved
    except Exception as err:
        logger.error(f"project_folders: could not move the pending project '{old_name}' to '{new_name}': {err}", exc_info=True)
        return False


def rename_task_project(task_data: Path, old_name: str, new_name: str) -> int:
    """Set project=new_name (and the kickoff title) on the tasks of old_name in this task data. Returns how many."""
    try:
        from tasklib import TaskWarrior  # type: ignore
    except ImportError:
        logger.warning("project_folders: tasklib is not installed - task project names were not changed.")
        return 0
    try:
        taskrc = task_data / ".taskrc"
        tw = TaskWarrior(data_location=str(task_data), taskrc_location=str(taskrc)) if taskrc.exists() else TaskWarrior(data_location=str(task_data))
        changed = 0
        for task in tw.tasks.filter(project=old_name):
            task["project"] = new_name
            description = task["description"] or ""
            if description.startswith(f"PROJECT KICK-OFF: {old_name}"):
                task["description"] = description.replace(f"PROJECT KICK-OFF: {old_name}", f"PROJECT KICK-OFF: {new_name}", 1)
            task.save()
            changed += 1
        return changed
    except Exception as err:
        logger.error(f"project_folders: could not rename the tasks of '{old_name}': {err}", exc_info=True)
        return 0


def carry_over_note(project_name: str, from_session: str, copied: List[str]) -> str:
    """What the PM is told when the project continues with carried-over files."""
    shown = ", ".join(copied[:MAX_LISTED_FILES]) + (f" (and {len(copied) - MAX_LISTED_FILES} more)" if len(copied) > MAX_LISTED_FILES else "")
    return (
        f"[Framework System Message]: The user chose to CONTINUE the existing project '{project_name}'. The files of its "
        f"latest session '{from_session}' were copied into your workspace: {shown or 'none'}. "
        + (f"The earlier plan is in {EARLIER_PLAN_NAME}. " if EARLIER_PLAN_NAME in copied else "")
        + "Plan the work ON TOP of these files: tasks change or extend them instead of creating them again."
    )

# END OF FILE src/utils/project_folders.py
