# START OF FILE src/workflows/project_update.py
"""
Round 3b: more work on an EXISTING project - after the user tested it, or whenever the user asks for changes.

2026-10-22 20:12 the user tested the finished Multiplayer_Snake_Game and asked for fixes and new features ("Let's now make it
better..."). Admin AI read the project's files and wrote a good plan, titled "Multiplayer Snake Game Enhancements". The plan
workflow only knew NEW projects: it created PM2 and, on approval, the empty project 'Multiplayer_Snake_Game_Enhancements' -
a copy without the game's files, next to PM1 and its team (W1, W2), who had built the game and sat idle.

Now a plan for a project that already runs in this session is an UPDATE of that project:

* the plan names the project ({"title": "...", "project": "Multiplayer_Snake_Game"}), or its title starts with the project's
  name ("Multiplayer_Snake_Game_Enhancements");
* the project's PM gets an update task and the user approves the update on the approval card (or starts it as a new project
  after all);
* on approval the plan is added to PROJECT_PLAN.md as "## Update N", and the PM plans the update's tasks (pm_startup, with
  the project's current files) and gives them to its EXISTING team - same project, same session, same workspace.

Never raises into the caller: problems are logged and reported in the results.
"""
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.agents.constants import (
    AGENT_TYPE_PM, AGENT_TYPE_WORKER, AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, PM_STATE_STARTUP, ADMIN_STATE_CONVERSATION,
)
from src.config.settings import settings
from .base import WorkflowResult

if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

logger = logging.getLogger(__name__)

# Keys of the plan JSON that name the existing project the plan changes
UPDATE_PROJECT_KEYS = ("project", "update_project", "existing_project", "project_name")
UPDATE_TASK_TAG = "project_update"
UPDATE_START_WAIT_SECONDS = 90.0   # how long the approval waits for the PM to finish a running cycle
UPDATE_POLL_SECONDS = 0.5
MAX_LISTED_EXISTING_FILES = 60
UPDATE_MODES = ("update", "continue", "approve")
NEW_PROJECT_MODES = ("new", "new_project")

# --- the plan text --------------------------------------------------------------------------------------------------------------
# 20:13:34 the plan began "Now I have a thorough understanding of the codebase. Let me create the project plan." and that
# sentence went into the kick-off task, PROJECT_PLAN.md and the approval card.
_OBJECTIVE_RE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*objective\b", re.IGNORECASE | re.MULTILINE)
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s", re.MULTILINE)
_ANNOUNCEMENT_RE = re.compile(
    r"\b(?:let me|let's|i will|i'll|i am going to|i'm going to|here is|here's|below is|now i|now that i|i now have|"
    r"i have a (?:thorough|good|clear|full) understanding)\b", re.IGNORECASE)
MAX_PREAMBLE_CHARS = 400
MAX_ANNOUNCEMENT_CHARS = 240


def strip_plan_preamble(text: Optional[str]) -> Tuple[str, str]:
    """
    (plan, removed text). Text before the plan's '**Objective:**' line is removed when it is short and holds no list; without
    an Objective line, a short first paragraph that only announces the plan ("Let me create the plan.") is removed.
    """
    plan = str(text or "")
    if not plan.strip():
        return plan, ""
    match = _OBJECTIVE_RE.search(plan)
    if match:
        before = plan[:match.start()].strip()
        if before and len(before) <= MAX_PREAMBLE_CHARS and not _LIST_LINE_RE.search(before):
            return plan[match.start():].strip(), before
        return plan.strip(), ""
    parts = re.split(r"\n[ \t]*\n", plan.strip(), maxsplit=1)
    if (len(parts) == 2 and parts[1].strip() and len(parts[0]) <= MAX_ANNOUNCEMENT_CHARS
            and _ANNOUNCEMENT_RE.search(parts[0]) and not _LIST_LINE_RE.search(parts[0])):
        return parts[1].strip(), parts[0].strip()
    return plan.strip(), ""


# --- which project a plan updates -----------------------------------------------------------------------------------------------
def _norm(name: Any) -> str:
    from src.utils.project_folders import sanitize_project_title
    return sanitize_project_title(name if isinstance(name, str) else "").lower()


def _pm_project(agent: Any) -> Optional[str]:
    config = (getattr(agent, "agent_config", None) or {}).get("config", {}) or {}
    project = config.get("project_name_context")
    return project if isinstance(project, str) and project.strip() else None


def _pm_session(manager: Any, agent: Any) -> Optional[str]:
    config = (getattr(agent, "agent_config", None) or {}).get("config", {}) or {}
    return config.get("session_name") or getattr(manager, "current_session", None)


def started_project_pms(manager: Any) -> Dict[str, Any]:
    """Project name -> its PM, for the projects of this run whose plan the user approved (their PM is not waiting)."""
    found: Dict[str, Any] = {}
    for agent in list((getattr(manager, "agents", None) or {}).values()):
        if getattr(agent, "agent_type", None) != AGENT_TYPE_PM or getattr(agent, "_awaiting_project_approval", False):
            continue
        project = _pm_project(agent)
        if project:
            found[project] = agent  # agents are kept in creation order: a later PM of the same project wins
    return found


def find_update_target(manager: Any, plan_data: Any, title: str) -> Optional[Tuple[str, Any, str]]:
    """
    (project, its PM, why) when the plan changes a project that runs in this session, else None. The plan names it under one
    of UPDATE_PROJECT_KEYS, or its (sanitized) title is the project's name or starts with it ("<name>_Enhancements").
    """
    projects = started_project_pms(manager)
    if not projects:
        return None
    by_norm = {_norm(project): project for project in projects}
    if isinstance(plan_data, dict):
        for key in UPDATE_PROJECT_KEYS:
            value = plan_data.get(key)
            project = by_norm.get(_norm(value)) if isinstance(value, str) and value.strip() else None
            if project:
                return project, projects[project], f"the plan names it ('{key}': '{value}')"
    wanted = _norm(title)
    best: Optional[str] = None
    for norm, project in by_norm.items():
        if norm and (wanted == norm or wanted.startswith(norm + "_")) and (best is None or len(norm) > len(_norm(best))):
            best = project
    if best:
        return best, projects[best], f"the plan's title '{title}' starts with the project's name"
    return None


# --- the team and the workspace -------------------------------------------------------------------------------------------------
def team_workers(manager: Any, pm: Any) -> List[Any]:
    """The worker agents of the PM's team."""
    try:
        team_id = manager.state_manager.get_agent_team(pm.agent_id)
        if not team_id:
            return []
        return [a for a in manager.state_manager.get_agents_in_team(team_id) if getattr(a, "agent_type", None) == AGENT_TYPE_WORKER]
    except Exception as err:
        logger.warning(f"project_update: could not list the team of '{getattr(pm, 'agent_id', '?')}': {err}")
        return []


def worker_labels(workers: List[Any]) -> List[str]:
    labels = []
    for worker in workers:
        role = getattr(worker, "role", None) or getattr(worker, "persona", None) or "worker"
        labels.append(f"{worker.agent_id} ({role})")
    return labels


def _workspace(project: str, session: str) -> Path:
    from src.tools.sandbox_runner import resolve_shared_workspace
    return resolve_shared_workspace(settings.PROJECTS_BASE_DIR, project, session)


def update_overview(project: str, number: int, title: str, plan: str, existing_files: List[str], workers: List[str]) -> str:
    """What the PM plans from in pm_startup for an update (its {task_description})."""
    team = f" ({', '.join(workers)})" if workers else ""
    lines = [
        f"PROJECT UPDATE {number} of the EXISTING project '{project}': {title}",
        "",
        f"The user asked for the changes below. The project's files ALREADY EXIST in the shared workspace (listed below) and "
        f"your team already exists{team}. Plan ONLY the work of this update:",
        "- every task changes or extends EXISTING files, or adds new ones, and names each file it changes or adds; never "
        "recreate, copy or restart the project;",
        "- add or extend automated tests for the changed behaviour;",
        "- update the README where the usage changes;",
        "- the last task runs ALL the project's tests and the program, to verify that everything still works;",
        "- 'roles': the roles the tasks need. Your existing workers do the work (new workers are only created if your team "
        "has none);",
        "- 'project_structure': only the directories and files that are new; the existing ones stay where they are.",
        "",
        "Update plan:",
        (plan or "").strip(),
    ]
    if existing_files:
        shown = existing_files[:MAX_LISTED_EXISTING_FILES]
        lines += ["", "Existing project files:"] + [f"- {name}" for name in shown]
        if len(existing_files) > len(shown):
            lines.append(f"- ... and {len(existing_files) - len(shown)} more")
    return "\n".join(lines)


def append_plan_section(workspace: Path, project: str, number: int, title: str, plan: str) -> bool:
    """Adds '## Update N: title' with the plan to PROJECT_PLAN.md (the original plan stays). True if written."""
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / "PROJECT_PLAN.md"
        section = f"## Update {number}: {title}\n\n{(plan or '').strip()}\n"
        if path.is_file():
            existing = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(existing.rstrip("\n") + "\n\n" + section, encoding="utf-8")
        else:
            path.write_text(f"# Project Plan: {project}\n\n{section}", encoding="utf-8")
        return True
    except OSError as err:
        logger.error(f"project_update: could not add update {number} to PROJECT_PLAN.md of '{project}': {err}")
        return False


# --- the update task -------------------------------------------------------------------------------------------------------------
async def _add_update_task(manager: Any, pm: Any, project: str, session: str, number: int, title: str, plan: str) -> Tuple[Optional[str], str]:
    """(task uuid or None, detail)"""
    tool_args = {
        "action": "add_task",
        "description": f"PROJECT UPDATE {number}: {title}\n\nUpdate Plan:\n{plan}",
        "priority": "H",
        "project_filter": project,
        # 'project_kickoff': the PM's own task, like the kick-off task (never assignable; marked 'decomposed' by the kickoff)
        "tags": ["project_kickoff", UPDATE_TASK_TAG, f"update_{number}", "auto_created_by_framework", f"+{pm.agent_id}"],
        "assignee_agent_id": pm.agent_id,
    }
    try:
        result = await manager.tool_executor.execute_tool(
            agent_id="framework", agent_sandbox_path=pm.sandbox_path, tool_name="project_management", tool_args=tool_args,
            project_name=project, session_name=session, manager=manager)
    except Exception as err:
        logger.error(f"project_update: could not create the task of update {number} for '{project}': {err}", exc_info=True)
        return None, f"the update task could not be created: {err}"
    if isinstance(result, dict) and result.get("status") == "success" and not result.get("is_duplicate"):
        return result.get("task_uuid"), f"update task {result.get('task_id', '?')} created for {pm.agent_id}"
    detail = result.get("message", "unknown error") if isinstance(result, dict) else str(result)
    logger.error(f"project_update: the task of update {number} for '{project}' was not created: {detail}")
    return None, f"the update task was not created: {detail}"


async def _delete_update_task(manager: Any, pm: Any, update: Dict[str, Any]) -> None:
    task_uuid = update.get("task_uuid")
    if not task_uuid:
        return
    try:
        await manager.tool_executor.execute_tool(
            agent_id="framework", agent_sandbox_path=pm.sandbox_path, tool_name="project_management",
            tool_args={"action": "modify_task", "task_id": task_uuid, "task_progress": "deleted"},
            project_name=update.get("project"), session_name=update.get("session"), manager=manager)
    except Exception as err:
        logger.warning(f"project_update: could not remove the task of the replaced update {update.get('number')}: {err}")


# --- planning an update (Admin AI's plan) ----------------------------------------------------------------------------------------
async def plan_project_update(manager: 'AgentManager', admin: 'Agent', pm: 'Agent', project: str, title: str, plan: str,
                              why: str, workflow_name: str = "project_creation") -> WorkflowResult:
    """
    The plan is an update of 'project': the update task goes to its PM and the approval card asks the user. Nothing starts
    before the approval; a newer update plan for the same PM replaces one that waits.
    """
    session = _pm_session(manager, pm) or ""
    title = (title or "").strip() or f"Update of {project}"
    earlier = getattr(pm, "_pending_update", None)
    number = int(getattr(pm, "_project_update_count", 0) or 0) + 1
    replaced_note = ""
    if isinstance(earlier, dict):
        await _delete_update_task(manager, pm, earlier)
        replaced_note = f" It replaces the earlier update plan '{earlier.get('title')}', which was never approved."
    logger.info(f"project_update: the plan '{title}' is update {number} of the running project '{project}' ({pm.agent_id}): {why}.")
    task_uuid, task_detail = await _add_update_task(manager, pm, project, session, number, title, plan)
    workers = worker_labels(team_workers(manager, pm))
    card = {
        "type": "project_pending_approval",
        "project_title": project,
        "plan_content": plan,
        "pm_agent_id": pm.agent_id,
        "message": f"Update {number} of project '{project}' ({pm.agent_id}) has been planned. Please approve to start.",
        "existing_project": None,
        "update": {"number": number, "title": title, "project": project, "workers": workers},
    }
    pm._pending_update = {
        "number": number, "title": title, "plan": plan, "project": project, "session": session, "task_uuid": task_uuid,
        "planned_at": time.time(), "card": card,
    }
    pm._approval_card = card
    if admin is not None:
        # Admin AI gets no workflow result in its history: it is told what happens, so it does not plan a copy again
        admin.message_history.append({"role": "system", "content": (
            f"[Framework Note]: Your plan '{title}' is UPDATE {number} of the existing project '{project}'. The user approves "
            f"it on the approval card (or by typing continue; new starts it as a separate project); then {pm.agent_id} and its team" + (f" ({', '.join(workers)})" if workers else "")
            + " make the changes in the project's own workspace. You do NOT plan it again, create a new project or start "
            "the work yourself.")})
    message = (f"Update {number} of project '{project}' planned for {pm.agent_id} ({task_detail}). It waits for the user's "
               f"approval; then {pm.agent_id} and its team make the changes in the project's workspace.{replaced_note}")
    logger.info(f"project_update: {message}")
    return WorkflowResult(success=True, message=message, workflow_name=workflow_name, next_agent_state=ADMIN_STATE_CONVERSATION,
                          next_agent_status=AGENT_STATUS_IDLE, ui_message_data=card)


# --- approving an update ----------------------------------------------------------------------------------------------------------
def _cycle_running(manager: Any, agent: Any) -> bool:
    lock = (getattr(manager, "_agent_cycle_locks", None) or {}).get(agent.agent_id)
    return bool(lock is not None and lock.locked())


async def wait_until_idle(manager: Any, agent: Any, timeout: Optional[float] = None, poll: Optional[float] = None) -> bool:
    """
    True once the agent has no running cycle and is idle (or in error); False after the timeout (default
    UPDATE_START_WAIT_SECONDS, polled every UPDATE_POLL_SECONDS).
    """
    timeout = UPDATE_START_WAIT_SECONDS if timeout is None else timeout
    poll = UPDATE_POLL_SECONDS if poll is None else poll
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if not _cycle_running(manager, agent) and getattr(agent, "status", None) in (AGENT_STATUS_IDLE, AGENT_STATUS_ERROR):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll)


# Per-project counters and records of a PM that must start fresh for an update (a finished project left them set)
_PM_UPDATE_RESETS = (
    ("_project_completed", False), ("_pm_audit_attempt_count", 0), ("_pm_audit_cycle_count", 0), ("_audit_refusals", None),
    ("kick_off_roles", None), ("kick_off_tasks", None), ("target_worker_agents_for_build", 0),
    ("successfully_created_agent_count_for_build", 0), ("unassigned_tasks_summary", None), ("_all_kickoff_task_uuids", None),
    ("_periodic_cycle_count", 0), ("_manage_unproductive_cycles", 0), ("_pm_report_check_cycle_count", 0),
    ("_seen_waiting_workers", None),
)


def reset_pm_for_update(pm: Any) -> None:
    for name, value in _PM_UPDATE_RESETS:
        if value is None:
            value = set() if name in ("_all_kickoff_task_uuids", "_seen_waiting_workers") else ([] if name != "_audit_refusals" else {})
        setattr(pm, name, value)
    if hasattr(pm, "_manage_cycle_cooldown_until"):
        try:
            delattr(pm, "_manage_cycle_cooldown_until")
        except AttributeError:
            pass


async def approve_project_update(manager: 'AgentManager', pm: 'Agent', mode: Optional[str]) -> Dict[str, Any]:
    """
    The user's answer to an update card: {"ok", "message", "status" (HTTP code), "start_pm" (a new project's PM to approve)}.
    mode 'update' (default; also 'continue' / 'approve' / none): the PM plans and runs the update with its team.
    mode 'new': the plan becomes a NEW project after all (a new PM; it is approved right away by the caller).
    """
    update = getattr(pm, "_pending_update", None)
    if not isinstance(update, dict):
        return {"ok": False, "message": f"No update of a project waits for approval at '{pm.agent_id}'.", "status": 409}
    choice = str(mode or "update").strip().lower()
    if choice in NEW_PROJECT_MODES:
        return await _start_update_as_new_project(manager, pm, update)
    if choice not in UPDATE_MODES:
        return {"ok": False, "message": f"Unknown choice '{mode}' for the update of '{update.get('project')}': use 'update' or 'new'.",
                "status": 400}
    if not await wait_until_idle(manager, pm):
        return {"ok": False, "status": 409, "message": (
            f"{pm.agent_id} is still busy with its current step, so update {update.get('number')} of '{update.get('project')}' "
            "did not start yet. Approve it again in a minute.")}

    number, title, plan = int(update.get("number") or 1), str(update.get("title") or ""), str(update.get("plan") or "")
    project, session = str(update.get("project")), str(update.get("session") or getattr(manager, "current_session", "") or "")
    pm._pending_update = None
    pm._project_update_count = number

    if getattr(manager, "current_project", None) != project or getattr(manager, "current_session", None) != session:
        logger.info(f"project_update: setting the context to '{project}/{session}' for update {number}.")
        await manager.set_project_session_context(project, session, loading=False)

    workspace = _workspace(project, session)
    append_plan_section(workspace, project, number, title, plan)
    from src.utils.project_folders import work_files
    existing = work_files(workspace)
    workers = worker_labels(team_workers(manager, pm))

    reset_pm_for_update(pm)
    pm.initial_plan_description = update_overview(project, number, title, plan, existing, workers)
    pm._project_update = {"number": number, "title": title, "task_uuid": update.get("task_uuid"), "project": project}
    pm._awaiting_project_approval = False
    pm.clear_history()
    pm._last_system_prompt_state = None
    manager.workflow_manager.change_state(pm, PM_STATE_STARTUP)
    if getattr(pm, "status", None) != AGENT_STATUS_IDLE:
        pm.set_status(AGENT_STATUS_IDLE)
    logger.info(f"project_update: update {number} of '{project}' approved; {pm.agent_id} plans it with {len(existing)} existing "
                f"file(s) and its team {workers or '(no workers)'}.")
    asyncio.create_task(manager.schedule_cycle(pm))
    team_text = f" and its team ({', '.join(workers)})" if workers else ""
    return {"ok": True, "status": 200, "message": (
        f"Update {number} of project '{project}' approved: {pm.agent_id}{team_text} now plan and make the changes in the "
        "project's own workspace.")}


async def _start_update_as_new_project(manager: 'AgentManager', pm: 'Agent', update: Dict[str, Any]) -> Dict[str, Any]:
    """The user chose to start the update plan as a NEW project: the update task goes, a new PM is planned for it."""
    from src.agents.constants import BOOTSTRAP_AGENT_ID
    from src.utils.project_folders import sanitize_project_title, unique_project_name
    from .project_creation_workflow import ProjectCreationWorkflow

    project = str(update.get("project"))
    title = sanitize_project_title(update.get("title")) or f"{project}_Update_{update.get('number') or 1}"
    if title.lower() == project.lower() or (Path(settings.PROJECTS_BASE_DIR) / title).exists():
        title = unique_project_name(Path(settings.PROJECTS_BASE_DIR), title)
    pm._pending_update = None
    await _delete_update_task(manager, pm, update)
    admin = (getattr(manager, "agents", None) or {}).get(BOOTSTRAP_AGENT_ID)
    result = await ProjectCreationWorkflow().execute(
        manager, admin, {"title": title, "_raw_plan_body_": str(update.get("plan") or ""), "_force_new": True})
    new_pm = (result.ui_message_data or {}).get("pm_agent_id") if result.success else None
    if not new_pm:
        return {"ok": False, "status": 500, "message": f"The plan could not be started as the new project '{title}': {result.message}"}
    logger.info(f"project_update: the user started the update plan of '{project}' as the NEW project '{title}' ({new_pm}).")
    return {"ok": True, "status": 200, "start_pm": new_pm, "message": f"The plan starts as the new project '{title}' ({new_pm})."}

# END OF FILE src/workflows/project_update.py
