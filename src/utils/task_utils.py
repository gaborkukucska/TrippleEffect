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
    True if the task is assigned to ``agent_id`` via the ``assignee`` field, or via the legacy
    ``+assigned`` / ``+<agent_id>`` tag pair.
    """
    assignee = get_task_field(task, "assignee")
    if assignee is not None and str(assignee).strip() == str(agent_id):
        return True
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
