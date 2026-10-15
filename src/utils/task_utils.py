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
from typing import Any


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
