# START OF FILE src/agents/assignable_work.py
"""
Open work that nobody is doing (Round 2h).

2026-10-19 19:13-19:46 (qwen3.5:9b, project Temp_Converter_CLI): the kick-off created 5 tasks and worker W1, but the PM never
managed to assign a task. Its replies in pm_manage only thought ("Next action: modify_task for ba793058... with
assignee_agent_id='W1'") and the framework answered with "All workers appear to be busy. You should transition to standby"
(they were not: W1 was idle in worker_startup). After 3 such cycles the PM was forced into pm_standby, and the standby
watchdog only wakes a PM for inbox messages or workers in worker_wait, so the project stopped with 5 'todo' tasks, no
assignee and an idle worker.

These helpers find exactly that situation - unassigned tasks that are ready to start, and idle workers of the PM's team -
and write the concrete call the PM has to make.
"""
import json
import logging
import re
from typing import Any, Iterable, List, Optional, Tuple

from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_TYPE_WORKER, WORKER_STATE_STARTUP, WORKER_STATE_WAIT,
)
from src.utils.task_utils import get_task_field, unfinished_dependencies

logger = logging.getLogger(__name__)

ASSIGNMENT_CHECK_TAG = "[Framework Assignment Check]"
MAX_LISTED_TASKS = 6
_NOT_ASSIGNABLE_PROGRESS = frozenset({"decomposed", "finished", "completed", "complete", "done", "closed", "deleted"})
_IDLE_WORKER_STATES = frozenset({WORKER_STATE_STARTUP, WORKER_STATE_WAIT})
_TASK_ORDER_RE = re.compile(r"^task_order_(\d+)$")


def _tags(task: Any) -> List[str]:
    tags = get_task_field(task, "tags")
    if not tags:
        return []
    try:
        return [str(tag) for tag in tags]
    except TypeError:
        return []


def _order_key(task: Any) -> Tuple[int, str]:
    for tag in _tags(task):
        match = _TASK_ORDER_RE.match(tag)
        if match:
            return int(match.group(1)), ""
    return 10 ** 6, str(get_task_field(task, "entry", "") or "")


def assignable_tasks(tasks: Iterable[Any]) -> List[Any]:
    """Pending tasks without an assignee that are ready to start (no unfinished dependency), in task order."""
    result = []
    for task in tasks or []:
        if str(get_task_field(task, "status", "pending")).strip().lower() != "pending":
            continue
        if str(get_task_field(task, "task_progress", "todo")).strip().lower() in _NOT_ASSIGNABLE_PROGRESS:
            continue
        if str(get_task_field(task, "assignee", "") or "").strip():
            continue
        if "project_kickoff" in _tags(task):
            continue
        if unfinished_dependencies(task):
            continue
        result.append(task)
    return sorted(result, key=_order_key)


def idle_worker_ids(workers: Iterable[Any], pending_tasks: Iterable[Any]) -> List[str]:
    """Ids of idle workers (worker_startup / worker_wait) that have no unfinished task assigned to them."""
    busy = set()
    for task in pending_tasks or []:
        assignee = str(get_task_field(task, "assignee", "") or "").strip()
        progress = str(get_task_field(task, "task_progress", "todo")).strip().lower()
        if assignee and progress not in _NOT_ASSIGNABLE_PROGRESS:
            busy.add(assignee)
    ids = []
    for worker in workers or []:
        if getattr(worker, "agent_type", None) != AGENT_TYPE_WORKER:
            continue
        if getattr(worker, "status", None) != AGENT_STATUS_IDLE or getattr(worker, "state", None) not in _IDLE_WORKER_STATES:
            continue
        agent_id = getattr(worker, "agent_id", None)
        if isinstance(agent_id, str) and agent_id and agent_id not in busy:
            ids.append(agent_id)
    return ids


def activation_view(manager: Any, pm_agent: Any, task_summaries: Iterable[Any]) -> Optional[dict]:
    """
    Round 3c: for the PM's activation directive - {"blocked": uuids of the listed tasks that wait for an UNFINISHED
    dependency, "free_workers": the team's workers without an open ready task (None without workers)}, or None if the task store or the team
    cannot be read (the caller then keeps its old rule). Never raises.
    """
    try:
        project = getattr(manager, "current_project", None)
        session = getattr(manager, "current_session", None)
        state_manager = getattr(manager, "state_manager", None)
        if not project or not session or state_manager is None:
            return None
        from src.tools.project_management import ProjectManagementTool, open_ready_tasks_of, team_worker_ids
        tw = ProjectManagementTool()._get_taskwarrior_instance(project, session)
        if tw is None:
            return None
        blocked = set()
        for summary in task_summaries or []:
            uuid = summary.get("uuid") if isinstance(summary, dict) else None
            if not uuid:
                continue
            try:
                task = tw.tasks.get(uuid=uuid)
            except Exception:
                continue
            if unfinished_dependencies(task):
                blocked.add(uuid)
        pending = list(tw.tasks.pending())
        team = team_worker_ids(manager, pm_agent.agent_id)
        free = [w for w in team if not open_ready_tasks_of(pending, w)] if team else None
        return {"blocked": blocked, "free_workers": free}
    except Exception as error:
        logger.debug(f"activation_view failed for '{getattr(pm_agent, 'agent_id', '?')}': {error}")
        return None


def unassigned_open_tasks(pending_tasks: Iterable[Any]) -> List[Any]:
    """Round 4b: pending tasks without an assignee that are not finished or decomposed (ready or not), without the kick-off
    task, in task order."""
    result = []
    for task in pending_tasks or []:
        if str(get_task_field(task, "status", "pending")).strip().lower() != "pending":
            continue
        if str(get_task_field(task, "task_progress", "todo")).strip().lower() in _NOT_ASSIGNABLE_PROGRESS:
            continue
        if str(get_task_field(task, "assignee", "") or "").strip():
            continue
        if "project_kickoff" in _tags(task):
            continue
        result.append(task)
    return sorted(result, key=_order_key)


def _dependency_uuids(task: Any) -> List[str]:
    uuids = []
    for dep in get_task_field(task, "depends") or []:
        uuid = get_task_field(dep, "uuid", None) if not isinstance(dep, str) else dep
        if uuid:
            uuids.append(str(uuid))
    return uuids


def unassigned_task_summaries(manager: Any) -> Optional[List[dict]]:
    """
    Round 4b: [{"uuid", "description", "depends"}] of the project's unassigned open tasks, read from the task store, or None
    if the store cannot be read. Never raises.
    2026-10-28 18:08:42 (granite4.2:8b) PM1 listed its tasks with tags_filter='assigned' but without tags_filter_mode
    'exclude', got 0 tasks and the framework told it "No unassigned tasks found" - with 5 kick-off tasks unassigned. PM1
    created its own copy of Kick-off Task 1, assigned it, and was told "All kick-off tasks have now been assigned". Kick-off
    Task 1, which every other task waits for, was never assigned. The activation directives now read the store.
    """
    try:
        project = getattr(manager, "current_project", None)
        session = getattr(manager, "current_session", None)
        if not project or not session:
            return None
        from src.tools.project_management import ProjectManagementTool
        tw = ProjectManagementTool()._get_taskwarrior_instance(project, session)
        if tw is None:
            return None
        summaries = []
        for task in unassigned_open_tasks(list(tw.tasks.pending())):
            uuid = get_task_field(task, "uuid", None)
            if not uuid:
                continue
            summaries.append({"uuid": str(uuid), "description": str(get_task_field(task, "description", "") or ""),
                              "depends": _dependency_uuids(task)})
        return summaries
    except Exception as error:
        logger.debug(f"unassigned_task_summaries failed: {error}")
        return None


def activation_task_list(manager: Any, agent: Any) -> str:
    """
    Round 4b: after a PM's list_tasks in pm_activate_workers - replaces agent.unassigned_tasks_summary (the tasks the PM's
    call showed) with the task store's unassigned open tasks and returns a note if the call's filters hid some ('' if not,
    or if the store cannot be read: the call's result is kept then). Never raises.
    """
    try:
        stored = unassigned_task_summaries(manager)
        if stored is None:
            return ""
        listed = {t.get("uuid") for t in getattr(agent, "unassigned_tasks_summary", None) or [] if isinstance(t, dict)}
        hidden = [t for t in stored if t["uuid"] not in listed]
        agent.unassigned_tasks_summary = stored
        known = getattr(agent, "_all_kickoff_task_uuids", None)
        if not isinstance(known, set):
            known = set()
            agent._all_kickoff_task_uuids = known
        known.update(t["uuid"] for t in stored)
        if not hidden:
            return ""
        logger.info(f"assignable_work: PM '{getattr(agent, 'agent_id', '?')}' list_tasks hid {len(hidden)} unassigned "
                    f"task(s); the activation directive lists the task store's {len(stored)}.")
        return (f"\n(Your list_tasks call showed {len(stored) - len(hidden)} of them - its filters hid {len(hidden)}. "
                "This list is read from the task store.)")
    except Exception as error:
        logger.debug(f"activation_task_list failed: {error}")
        return ""


def activation_remaining_tasks(manager: Any, agent: Any, assigned_uuid: Any = None) -> None:
    """
    Round 4b: after a PM's assignment in pm_activate_workers - agent.unassigned_tasks_summary becomes what the task store
    still has unassigned (the summary only knew the tasks the PM's list_tasks call showed). Kept as it is if the store
    cannot be read. Never raises.
    """
    try:
        stored = unassigned_task_summaries(manager)
        if stored is None:
            return
        agent.unassigned_tasks_summary = stored
        known = getattr(agent, "_all_kickoff_task_uuids", None)
        if not isinstance(known, set):
            known = set()
            agent._all_kickoff_task_uuids = known
        known.update(t["uuid"] for t in stored)
        if assigned_uuid:
            known.add(str(assigned_uuid))
    except Exception as error:
        logger.debug(f"activation_remaining_tasks failed: {error}")


def assignment_hint(tasks: List[Any], worker_ids: List[str]) -> str:
    """The message that tells a PM which tasks wait for a worker, and the exact call that assigns the first one."""
    if not tasks or not worker_ids:
        return ""
    first_uuid = str(get_task_field(tasks[0], "uuid", ""))
    example = json.dumps({"action": "modify_task", "task_id": first_uuid, "assignee_agent_id": worker_ids[0]})
    lines = [
        f"{ASSIGNMENT_CHECK_TAG} {len(tasks)} task(s) are not assigned to anyone and worker(s) {', '.join(worker_ids)} "
        f"{'is' if len(worker_ids) == 1 else 'are'} idle, so nothing is being worked on. Workers are NOT busy and there is "
        "nothing to wait for. Assign a task now by calling the `project_management` tool, for example with exactly:",
        example,
        "Unassigned tasks that are ready to start (use these task_id values):",
    ]
    for task in tasks[:MAX_LISTED_TASKS]:
        description = " ".join(str(get_task_field(task, "description", "")).split())
        if len(description) > 100:
            description = description[:97] + "..."
        lines.append(f"- {get_task_field(task, 'uuid', '')}: {description}")
    if len(tasks) > MAX_LISTED_TASKS:
        lines.append(f"- ... and {len(tasks) - MAX_LISTED_TASKS} more (see `list_tasks`).")
    return "\n".join(lines)


def find_assignable_work(manager: Any, pm_agent: Any) -> Tuple[List[Any], List[str]]:
    """
    (assignable tasks, idle worker ids) for a PM's current project and team, or ([], []) if either is missing or the
    task store cannot be read. Never raises.
    """
    try:
        project = getattr(manager, "current_project", None)
        session = getattr(manager, "current_session", None)
        state_manager = getattr(manager, "state_manager", None)
        if not project or not session or state_manager is None:
            return [], []
        team_id = state_manager.get_agent_team(pm_agent.agent_id)
        if not team_id:
            return [], []
        workers = [a for a in state_manager.get_agents_in_team(team_id) if getattr(a, "agent_type", None) == AGENT_TYPE_WORKER]
        if not workers:
            return [], []
        from src.tools.project_management import ProjectManagementTool
        tw = ProjectManagementTool()._get_taskwarrior_instance(project, session)
        if tw is None:
            return [], []
        pending = list(tw.tasks.pending())
        tasks = assignable_tasks(pending)
        if not tasks:
            return [], []
        return tasks, idle_worker_ids(workers, pending)
    except Exception as error:
        logger.debug(f"find_assignable_work failed for '{getattr(pm_agent, 'agent_id', '?')}': {error}")
        return [], []


def assignment_hint_for(manager: Any, pm_agent: Any) -> Optional[str]:
    """The assignment hint for a PM, or None if no idle worker waits for an unassigned task."""
    tasks, worker_ids = find_assignable_work(manager, pm_agent)
    if tasks and worker_ids:
        return assignment_hint(tasks, worker_ids)
    return None


# --- A worker that parks with its own open task (Round 2l) ----------------------------------------------------------------
# 2026-10-20 16:30-16:40 (Browser_Snake_Game): W1 wrote README.md for Kick-off Task 6, reported "COMPLETED" to PM1 and
# requested worker_wait 8 times without marking the task finished. Each time the watchdog saw its open task and started
# it again a minute later, until PM1 marked the task finished itself. A worker that asks for worker_wait while one of its
# own tasks is open and ready is now told to mark it finished first (twice per task at most, then the request is allowed).
WORKER_WAIT_GUARD_TAG = "[Framework Wait Check]"
MAX_WAIT_REFUSALS_PER_TASK = 2


def own_open_ready_tasks(pending_tasks: Iterable[Any], worker_id: str) -> List[Any]:
    """Pending tasks of this worker that are not finished/decomposed and do not wait for an unfinished dependency."""
    result = []
    for task in pending_tasks or []:
        if str(get_task_field(task, "assignee", "") or "").strip() != worker_id:
            continue
        if str(get_task_field(task, "status", "pending")).strip().lower() != "pending":
            continue
        if str(get_task_field(task, "task_progress", "todo")).strip().lower() in _NOT_ASSIGNABLE_PROGRESS:
            continue
        if unfinished_dependencies(task):
            continue
        result.append(task)
    return sorted(result, key=_order_key)


def worker_wait_block_message(manager: Any, agent: Any, requested_state: Optional[str]) -> Optional[str]:
    """
    The message that refuses a worker's request for worker_wait while one of its own tasks is still open and ready, or
    None (other agents and states, no such task, task store unreadable, or this task was refused twice already).
    """
    if getattr(agent, "agent_type", None) != AGENT_TYPE_WORKER or requested_state != WORKER_STATE_WAIT:
        return None
    try:
        project = getattr(manager, "current_project", None)
        session = getattr(manager, "current_session", None)
        if not project or not session:
            return None
        from src.tools.project_management import ProjectManagementTool
        tw = ProjectManagementTool()._get_taskwarrior_instance(project, session)
        if tw is None:
            return None
        tasks = own_open_ready_tasks(list(tw.tasks.pending()), agent.agent_id)
    except Exception as error:
        logger.debug(f"worker_wait_block_message failed for '{getattr(agent, 'agent_id', '?')}': {error}")
        return None
    if not tasks:
        return None
    task = tasks[0]
    uuid = str(get_task_field(task, "uuid", ""))
    refusals = getattr(agent, "_wait_refusals", None)
    if not isinstance(refusals, dict):
        refusals = {}
    if refusals.get(uuid, 0) >= MAX_WAIT_REFUSALS_PER_TASK:
        return None
    refusals[uuid] = refusals.get(uuid, 0) + 1
    agent._wait_refusals = refusals
    description = " ".join(str(get_task_field(task, "description", "")).split())
    if len(description) > 100:
        description = description[:97] + "..."
    progress = str(get_task_field(task, "task_progress", "todo"))
    finish_call = json.dumps({"action": "modify_task", "task_id": uuid, "task_progress": "finished"})
    return (
        f"{WORKER_WAIT_GUARD_TAG} You did NOT go to worker_wait: your task {uuid} ('{description}') is still '{progress}', "
        "so it is not finished and you would be started on it again right away.\n"
        f"- If the work is done: first call the `project_management` tool with exactly {finish_call} (the framework "
        "checks your files), then request worker_wait.\n"
        f"- If work remains: call the 'request_state' tool with state='worker_work' and task_id='{uuid}'."
    )


# --- A PM that asks for the audit while work is open (Round 2p) -----------------------------------------------------------
# 2026-10-21 18:10:09 (WiFi_Snake_Game_Multiplayer): PM1 requested pm_audit with 6 of 7 kick-off tasks open. Entering pm_audit
# cleared its history; it listed the tasks, found nothing to audit and went back to pm_manage 3.5 minutes later. pm_audit is
# for a finished project: the request is refused (twice per open-task count, then allowed) with the open tasks listed.
PM_AUDIT_GUARD_TAG = "[Framework Audit Check]"
MAX_AUDIT_REFUSALS = 2


def open_project_tasks(pending_tasks: Iterable[Any], pm_id: Optional[str] = None) -> List[Any]:
    """Pending tasks that are not finished or decomposed, without the kick-off task and tasks of the PM itself."""
    result = []
    for task in pending_tasks or []:
        if str(get_task_field(task, "status", "pending")).strip().lower() != "pending":
            continue
        if str(get_task_field(task, "task_progress", "todo")).strip().lower() in _NOT_ASSIGNABLE_PROGRESS:
            continue
        if "project_kickoff" in _tags(task):
            continue
        if pm_id and str(get_task_field(task, "assignee", "") or "").strip() == pm_id:
            continue
        result.append(task)
    return sorted(result, key=_order_key)


# --- Round 4d: a finished project nobody audits ---------------------------------------------------------------------------
# 2026-10-28 23:36-2026-10-29 09:10 (WiFi_Snake_Game, qwen3.5:4b) PM1 went to pm_standby at 23:36:18 while W1 fixed a test; W1
# finished at 23:37:57 and went to worker_wait at 23:38:11 without a message. Every task was finished, every agent idle - and
# for 9.5 hours nothing happened: the standby watchdog wakes a PM only for an inbox message, a NEW waiting worker (W1 counted
# as seen since 21:03, although it had worked twice since) or unassigned tasks; only pm_manage moved a PM to pm_audit. The
# project was never audited and the user never heard that it was done.
def _end_timestamp(task: Any) -> float:
    end = get_task_field(task, "end", None)
    try:
        return float(end.timestamp()) if hasattr(end, "timestamp") else float(end or 0)
    except (TypeError, ValueError, OverflowError, OSError):
        return 0.0


def _project_task_store(manager: Any) -> Any:
    project = getattr(manager, "current_project", None)
    session = getattr(manager, "current_session", None)
    if not project or not session:
        return None
    from src.tools.project_management import ProjectManagementTool
    return ProjectManagementTool()._get_taskwarrior_instance(project, session)


def busy_team_workers(manager: Any, pm_agent: Any) -> List[str]:
    """Round 4d: ids of the PM's team workers that are working (not idle, or in a working state)."""
    from src.agents.constants import (WORKER_STATE_DECOMPOSE, WORKER_STATE_REPORT, WORKER_STATE_TEST,
                                      WORKER_STATE_WORK)
    working_states = {WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK, WORKER_STATE_TEST, WORKER_STATE_REPORT}
    state_manager = getattr(manager, "state_manager", None)
    team_id = state_manager.get_agent_team(pm_agent.agent_id) if state_manager is not None else None
    if not team_id:
        return []
    return [a.agent_id for a in state_manager.get_agents_in_team(team_id)
            if getattr(a, "agent_type", None) == AGENT_TYPE_WORKER
            and (getattr(a, "status", None) != AGENT_STATUS_IDLE or getattr(a, "state", None) in working_states)]


def finished_project_end(manager: Any, pm_agent: Any) -> Optional[float]:
    """
    Round 4d: when EVERY task of the PM's project is finished (none open, at least one finished) - the time the last one
    was finished (seconds since the epoch), else None. Never raises.
    """
    try:
        tw = _project_task_store(manager)
        if tw is None:
            return None
        if open_project_tasks(list(tw.tasks.pending()), getattr(pm_agent, "agent_id", None)):
            return None
        ends = [_end_timestamp(task) for task in tw.tasks.completed()
                if str(get_task_field(task, "task_progress", "finished")).strip().lower() != "deleted"]
        return max(ends) if ends else None
    except Exception as error:
        logger.debug(f"finished_project_end failed for '{getattr(pm_agent, 'agent_id', '?')}': {error}")
        return None


def project_needs_audit(manager: Any, pm_agent: Any) -> bool:
    """Round 4d: True when every task is finished, no worker of the team works, and a task finished after the PM's last
    audit (pm._audited_at, set when it enters pm_audit). Never raises."""
    try:
        if busy_team_workers(manager, pm_agent):
            return False
        last_end = finished_project_end(manager, pm_agent)
        return last_end is not None and last_end > float(getattr(pm_agent, "_audited_at", 0) or 0)
    except Exception as error:
        logger.debug(f"project_needs_audit failed for '{getattr(pm_agent, 'agent_id', '?')}': {error}")
        return False


def newly_waiting_workers(pm_agent: Any, waiting_workers: Iterable[Any], last_wake: float) -> set:
    """
    Round 4d: ids of the waiting workers the PM has not seen waiting yet - not in the set stored at its last wake, or back in
    worker_wait (agent._state_entered_at) after that wake and after the PM went to standby.
    """
    seen = getattr(pm_agent, "_seen_waiting_workers", set()) or set()
    since = max(float(last_wake or 0), float(getattr(pm_agent, "_state_entered_at", 0) or 0))
    return {w.agent_id for w in waiting_workers
            if w.agent_id not in seen or float(getattr(w, "_state_entered_at", 0) or 0) > since}


def pm_audit_block_message(manager: Any, agent: Any, requested_state: Optional[str]) -> Optional[str]:
    """The message refusing a PM's request for pm_audit while project tasks are open, or None."""
    if getattr(agent, "agent_type", None) != "pm" or requested_state != "pm_audit":
        return None
    try:
        project = getattr(manager, "current_project", None)
        session = getattr(manager, "current_session", None)
        if not project or not session:
            return None
        from src.tools.project_management import ProjectManagementTool
        tw = ProjectManagementTool()._get_taskwarrior_instance(project, session)
        if tw is None:
            return None
        tasks = open_project_tasks(list(tw.tasks.pending()), agent.agent_id)
    except Exception as error:
        logger.debug(f"pm_audit_block_message failed for '{getattr(agent, 'agent_id', '?')}': {error}")
        return None
    if not tasks:
        return None
    refusals = getattr(agent, "_audit_refusals", None)
    if not isinstance(refusals, dict):
        refusals = {}
    if refusals.get(len(tasks), 0) >= MAX_AUDIT_REFUSALS:
        return None
    refusals[len(tasks)] = refusals.get(len(tasks), 0) + 1
    agent._audit_refusals = refusals
    lines = [f"{PM_AUDIT_GUARD_TAG} You did NOT go to pm_audit: {len(tasks)} project task(s) are still open, so the project is not "
             "finished. pm_audit is only for verifying a FINISHED project. Stay in pm_manage: assign the ready tasks to idle "
             "workers and wait for the workers' reports. Open tasks:"]
    for task in tasks[:MAX_LISTED_TASKS]:
        description = " ".join(str(get_task_field(task, "description", "")).split())
        if len(description) > 90:
            description = description[:87] + "..."
        assignee = str(get_task_field(task, "assignee", "") or "").strip() or "nobody"
        progress = str(get_task_field(task, "task_progress", "todo"))
        lines.append(f"- {get_task_field(task, 'uuid', '')} ({progress}, assigned to {assignee}): {description}")
    if len(tasks) > MAX_LISTED_TASKS:
        lines.append(f"- ... and {len(tasks) - MAX_LISTED_TASKS} more.")
    hint = assignment_hint_for(manager, agent)
    if hint:
        lines.append(hint)
    return "\n".join(lines)
