# START OF FILE src/utils/task_utils.py
"""
Safe field access for tasklib ``Task`` objects.

tasklib's ``Task`` is NOT a dict:
  * it has no ``.get()``;
  * ``'field' in task`` is ALWAYS False (no ``__contains__``, and ``__getitem__`` deliberately stops
    integer iteration, so Python's fallback membership test never matches);
  * ``task['field']`` is the only reliable accessor and returns None for fields that are not set.

These helpers also accept plain dicts (e.g. parsed ``list_tasks`` JSON), so callers need not care which
one they were handed.
"""
from typing import Any, List, Tuple


def get_task_field(task: Any, key: str, default: Any = None) -> Any:
    """Return task[key], or ``default`` if the task is None, the field is missing/None, or unreadable."""
    if task is None:
        return default
    try:
        value = task[key]
    except Exception:
        return default
    return default if value is None else value


def task_is_assigned_to(task: Any, agent_id: str) -> bool:
    """
    True if the task is assigned to ``agent_id`` via the ``assignee`` field, or - for a task without an assignee - via
    the legacy ``+assigned`` / ``+<agent_id>`` tag pair.
    """
    assignee = get_task_field(task, "assignee")
    if assignee is not None and str(assignee).strip():
        # Round 3c: the assignee field decides; the tags of an earlier assignee stay on a reassigned task (2026-10-23)
        return str(assignee).strip() == str(agent_id)
    tags = get_task_field(task, "tags")
    if tags:
        try:
            tag_set = set(tags)
        except TypeError:
            return False
        return "assigned" in tag_set and (agent_id in tag_set or f"+{agent_id}" in tag_set)
    return False


# --- Dependency check (Round 1v) ---------------------------------------------------------------------------------------
# 2026-10-16 20:01/20:03: the PM assigned "write the tests" (depends on task 1) and "write the README" (depends on task 2)
# while task 1 was still in progress. The framework started both workers at once; the README was written before any test
# existed and was marked finished, the test task never finished.
_DEPENDENCY_DONE_STATUSES = frozenset({"completed", "deleted"})
# 'decomposed' does not block: its sub-tasks carry the dependency (project_management transfers it on decomposition).
_DEPENDENCY_DONE_PROGRESS = frozenset({"finished", "completed", "complete", "done", "closed", "deleted", "decomposed"})


def unfinished_dependencies(task: Any) -> List[Tuple[str, str]]:
    """
    (uuid, description) of every task that ``task`` depends on and that is not finished yet. Works for tasklib Tasks
    (whose ``depends`` holds Task objects). Entries that cannot be judged (e.g. bare uuid strings) never block.
    """
    blocking: List[Tuple[str, str]] = []
    deps = get_task_field(task, "depends")
    if not deps:
        return blocking
    try:
        items = list(deps)
    except TypeError:
        return blocking
    for dep in items:
        if dep is None or isinstance(dep, (str, bytes)):
            continue
        status = str(get_task_field(dep, "status", "")).strip().lower()
        progress = str(get_task_field(dep, "task_progress", "")).strip().lower()
        if status in _DEPENDENCY_DONE_STATUSES or progress in _DEPENDENCY_DONE_PROGRESS:
            continue
        description = " ".join(str(get_task_field(dep, "description", "")).split())
        blocking.append((str(get_task_field(dep, "uuid", "?")), description))
    return blocking


# --- Round 3y: a worker does not START a task that waits for unfinished tasks ---------------------------------------------
# 2026-10-27 11:56:46 (WiFi_Snake_Game) W4 was working on Kick-off Task 5 (src/api.py, 'doing') when its report ended; it asked
# for worker_work with the id of its other task, "Kick-off Task 10: Write ... tests for API endpoints in tests/test_api.py" -
# which depends on Task 5 - and got it. W4 wrote tests for an API that was still being written and marked Task 10 finished at
# 13:20 while Task 5 was still open; Task 5 went to W1 later.
_CLOSED_PROGRESS = frozenset({"finished", "completed", "complete", "done", "closed", "deleted", "decomposed"})


def _shown(text: Any, limit: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def ready_task_of(tasks: Any, agent_id: str, exclude_uuid: Any = None) -> Any:
    """The worker's first open task (assigned to it, not closed, not the kick-off task) that waits for nothing, or None."""
    for task in tasks or []:
        try:
            if exclude_uuid and str(get_task_field(task, "uuid", "")) == str(exclude_uuid):
                continue
            if not task_is_assigned_to(task, agent_id):
                continue
            tags = get_task_field(task, "tags") or []
            if "project_kickoff" in set(tags):
                continue
            if str(get_task_field(task, "task_progress", "todo") or "todo").strip().lower() in _CLOSED_PROGRESS:
                continue
            if str(get_task_field(task, "status", "pending") or "pending").strip().lower() != "pending":
                continue
            if unfinished_dependencies(task):
                continue
            return task
        except Exception:
            continue
    return None


def waiting_task_start_refusal(task: Any, task_ref: Any, agent_id: str, pending_tasks: Any = None) -> str:
    """
    Round 3y: the refusal for a worker that asks to work on `task` while it depends on unfinished tasks ('' if it waits for
    nothing). Names what it waits for and the worker's own task that it can work on now (or worker_wait).
    """
    blocking = unfinished_dependencies(task)
    if not blocking:
        return ""
    names = "; ".join(f"'{_shown(description, 70)}' ({uuid})" for uuid, description in blocking[:3])
    ready = ready_task_of(pending_tasks, agent_id, exclude_uuid=get_task_field(task, "uuid", None))
    if ready is not None:
        then = (f"Work on your task '{_shown(get_task_field(ready, 'description', ''))}' instead: call the 'request_state' "
                f"tool with state='worker_work' and task_id='{get_task_field(ready, 'uuid', '?')}'.")
    else:
        then = ("You have no task you can work on now: call the 'request_state' tool with state='worker_wait'. You are "
                "started automatically when it can begin.")
    return (f"Task '{task_ref}' ('{_shown(get_task_field(task, 'description', ''))}') can NOT be worked on yet: it waits for "
            f"unfinished task(s) {names}, whose files are still changing. {then}")
