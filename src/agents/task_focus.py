# START OF FILE src/agents/task_focus.py
"""
A worker whose task is done is told so - and which work belongs to other tasks (Round 3p).

2026-10-25 20:15:32 (WiFi_Snake_Game_With_Leaderboard, qwen3.5:4b on a laptop, ~10 output tokens/s): W1 wrote src/game.js for
"Kick-off Task 1: Implement the core game engine in src/game.js including snake movement, food spawning, collision detection,
and game loop with debug logging" - all of it, in one write. It then stayed in worker_work for 40 minutes (13 cycles of 2-5
minutes each) adding a leaderboard with localStorage, high scores and multiplayer state to src/game.js: the work of
"Kick-off Task 3: ... src/player_manager.js with localStorage-based persistence for high scores and leaderboards" and
"Kick-off Task 5: ... src/state_sync.js". Seven code_editor calls, four of them failed. The watchdog's message at cycle 10
("If you are stuck or have completed your work here ...") did not say that the task's file was complete.

task_done_hint(): for a worker in worker_work whose current task names files that ALL exist and pass the framework's
completion checks (src/utils/task_files.task_completion_problems), the note that the task looks done (with the exact
modify_task call) and the files of other open tasks it must not build. '' otherwise. Never raises.
Round 3q: not for a task that may not be marked finished yet because its tests do not pass (src/utils/test_runs.
verification_problem - the same check modify_task applies).
"""
import logging
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

MAX_OTHER_TASKS_SHOWN = 4
_CLOSED_PROGRESS = frozenset({"decomposed", "finished", "completed", "complete", "done", "closed", "deleted"})


def _shown(text: Any, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _current_task(tasks: List[Any], agent: Any) -> Optional[Any]:
    from src.utils.task_utils import get_task_field
    for attr in ("active_task_id", "current_task_id"):
        wanted = str(getattr(agent, attr, "") or "").strip()
        if wanted:
            for task in tasks:
                if str(get_task_field(task, "uuid", "")) == wanted:
                    return task
    return None


def task_done_hint(manager: Any, agent: Any) -> str:
    """Round 3p: see the module docstring."""
    try:
        from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_WORK
        if getattr(agent, "agent_type", None) != AGENT_TYPE_WORKER or getattr(agent, "state", None) != WORKER_STATE_WORK:
            return ""
        project_name = getattr(manager, "current_project", None)
        session_name = getattr(manager, "current_session", None)
        if not project_name or not session_name:
            return ""
        from src.config.settings import settings
        from src.tools.sandbox_runner import resolve_shared_workspace
        from src.tools.project_management import ProjectManagementTool
        from src.utils.task_utils import get_task_field
        from src.utils.task_files import file_paths_in_text, find_in_workspace, task_completion_problems
        from src.utils.file_authorship import files_changed_for_task
        workspace = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_name), str(session_name))
        tw = ProjectManagementTool()._get_taskwarrior_instance(str(project_name), str(session_name))
        if tw is None or not Path(workspace).is_dir():
            return ""
        pending = list(tw.tasks.pending())
        task = _current_task(pending, agent)
        if task is None:
            return ""
        description = str(get_task_field(task, "description", "") or "")
        named = file_paths_in_text(description)
        if not named:
            return ""  # a task without files: nothing to judge
        found = [rel for rel in named if find_in_workspace(Path(workspace), rel) is not None]
        if len(found) != len(named):
            return ""  # a named file is still missing: the task is not done
        task_uuid = str(get_task_field(task, "uuid", "") or "")
        if task_completion_problems(description, Path(workspace), files_changed_for_task(task_uuid, workspace)):
            return ""  # the files still have defects (the completion check names them)
        # Round 3q: 2026-10-25 23:28:02-23:46:50 the hint told W1 four times that "Fix test imports: ..." was DONE while
        # every `python -m pytest -q` failed - and each of W1's 8 modify_task calls was refused with "no full test run of
        # the CURRENT files has passed yet". The hint names a task done only when marking it finished would be accepted.
        tags = {str(tag) for tag in (get_task_field(task, "tags") or [])}
        if "project_kickoff" not in tags:
            from src.utils.test_runs import verification_problem
            if verification_problem(workspace, description):
                return ""
        others: List[str] = []
        for other in pending:
            if other is task or str(get_task_field(other, "uuid", "")) == task_uuid:
                continue
            tags = {str(tag) for tag in (get_task_field(other, "tags") or [])}
            progress = str(get_task_field(other, "task_progress", "todo") or "todo").strip().lower()
            if "project_kickoff" in tags or progress in _CLOSED_PROGRESS:
                continue
            other_text = str(get_task_field(other, "description", "") or "")
            other_files = [f for f in file_paths_in_text(other_text) if f not in named]
            if other_files:
                others.append(f"{', '.join(other_files[:3])} ('{_shown(other_text, 70)}')")
            if len(others) >= MAX_OTHER_TASKS_SHOWN:
                break
        text = (f"[Framework check]: The file(s) your task names ({', '.join(found)}) exist and pass the framework's checks. "
                f"If they do what your task asks ('{_shown(description)}'), your task is DONE: mark it finished "
                f"(project_management action 'modify_task', task_id='{task_uuid}', task_progress='finished') and report to "
                "your PM (request_state 'worker_report'). Do not keep adding features that the task does not ask for.")
        if others:
            text += (" These belong to OTHER tasks - do not build them in your files: " + "; ".join(others) + ".")
        return text
    except Exception as err:
        logger.debug(f"task_done_hint failed: {err}")
        return ""

# END OF FILE src/agents/task_focus.py
