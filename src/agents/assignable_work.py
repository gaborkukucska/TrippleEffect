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
