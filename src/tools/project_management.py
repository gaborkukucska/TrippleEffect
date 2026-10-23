# START OF FILE src/tools/project_management.py
import logging
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING # Import TYPE_CHECKING
import re
import json
import asyncio
from src.api.websocket_manager import broadcast

# Import tasklib safely
try:
    from tasklib import TaskWarrior, Task
    TASKLIB_AVAILABLE = True
except ImportError:
    from typing import Any
    TaskWarrior: Any = None
    Task: Any = None
    TASKLIB_AVAILABLE = False


from .base import BaseTool, ToolParameter
from src.config.settings import BASE_DIR
from typing import List
from src.utils.task_utils import get_task_field

logger = logging.getLogger(__name__)


# --- Input hygiene for values that end up inside Taskwarrior's line-oriented data files -------------
# Taskwarrior stores one task per line and writes each tag as an attribute KEY (tags_<tag>:"x") without
# escaping. A tag that contains a line break (e.g. the model passing assignee_agent_id="\nW1\n") therefore
# splits a record across lines and every later `task export` fails with "Unrecognized Taskwarrior file
# format ... at line N" (observed 2026-10-15 10:22). These helpers keep such values out of the database
# and repair files that were already damaged.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")
_WS_RE = re.compile(r"\s+")
_LITERAL_ESCAPE_RE = re.compile(r"\\[nrt]")
_TAGS_ATTR_RE = re.compile(r'(?:(?<=\s)|(?<=\[))tags:"([^"]*)"')
_ASSIGNEE_ATTR_RE = re.compile(r'(?:(?<=\s)|(?<=\[))assignee:"([^"]*)"')
_TAG_FLAG_RE = re.compile(r'(\s)(tags_[^\s:"]+:"x")')


def clean_agent_id(value: Any) -> Optional[str]:
    """Returns the agent id without surrounding whitespace/quotes, or None if it is not a plausible id."""
    if value is None:
        return None
    text = str(value).strip().strip("'\"").strip()
    return text if _AGENT_ID_RE.match(text) else None


# Round 2t: only workers carry out tasks. 2026-10-21 20:54:22 PM1 assigned "Kick-off Task 9: Write docs/SETUP.md" to itself;
# a PM cannot write files, so the task would never be done (and no worker was activated for it).
FRAMEWORK_CALLERS = frozenset({"framework", "system"})
_PM_LIKE_ID_RE = re.compile(r"^pm(?:\d+|[_-].*)?$", re.IGNORECASE)


def non_worker_role(agent_id: Optional[str], manager: Any = None) -> Optional[str]:
    """
    'a Project Manager' / 'the Admin AI' / 'the Constitutional Guardian' if agent_id is not a worker, else None. A known
    agent is judged by its agent_type, an unknown id by its name (PM1, pm_..., admin_ai, constitutional_guardian_ai).
    """
    if not agent_id:
        return None
    from src.agents.constants import (AGENT_TYPE_ADMIN, AGENT_TYPE_PM, BOOTSTRAP_AGENT_ID,
                                      CONSTITUTIONAL_GUARDIAN_AGENT_ID)
    if agent_id == CONSTITUTIONAL_GUARDIAN_AGENT_ID:
        return "the Constitutional Guardian"
    agents = getattr(manager, "agents", None)
    agent = agents.get(agent_id) if isinstance(agents, dict) else None
    if agent is not None:
        agent_type = getattr(agent, "agent_type", None)
        if agent_type == AGENT_TYPE_PM:
            return "a Project Manager"
        if agent_type == AGENT_TYPE_ADMIN:
            return "the Admin AI"
        return None
    if agent_id == BOOTSTRAP_AGENT_ID:
        return "the Admin AI"
    if _PM_LIKE_ID_RE.match(agent_id):
        return "a Project Manager"
    return None


def team_worker_ids(manager: Any, agent_id: Optional[str]) -> List[str]:
    """The workers of agent_id's team (all workers if it has no team). Never raises."""
    try:
        from src.agents.constants import AGENT_TYPE_WORKER
        state_manager = getattr(manager, "state_manager", None)
        team_id = state_manager.get_agent_team(agent_id) if state_manager is not None and agent_id else None
        if team_id:
            members = state_manager.get_agents_in_team(team_id) or []
        else:
            agents = getattr(manager, "agents", None)
            members = list(agents.values()) if isinstance(agents, dict) else []
        return [a.agent_id for a in members if getattr(a, "agent_type", None) == AGENT_TYPE_WORKER and getattr(a, "agent_id", None)]
    except Exception:
        return []


# --- Round 3c: one task at a time per worker; no second task for work that is already finished ------------------------------
# 2026-10-22 23:33-00:13 (qwen3.5:4b, Multiplayer_Snake_Game): during the kick-off PM1 gave Kick-off Tasks 1, 2, 3, 4 and 5 to
# W1 and only Task 6 (the README) to W2. W2 finished the README at 00:42 and then had nothing to do for hours, while W1's queue
# held four tasks (Task 2 was moved to W2 only at 06:25). At 01:59 PM1 created "Kick-off Task 8: Write README.md ..." -
# the README Task 6 had finished at 00:42 - and W2 spent 50 minutes finding that the README was done.
_OPEN_EXCLUDED_PROGRESS = frozenset({"decomposed", "finished", "completed", "complete", "done", "closed", "deleted"})
FINISHED_DUPLICATE_RATIO = 0.8
_TASK_LABEL_RE = re.compile(r"^\s*(?:kick-?off\s+task|update\s+\d+\s+task|task)\s*\d+\s*[:.\-]\s*", re.IGNORECASE)


def _is_pm_caller(caller_id: Optional[str], manager: Any) -> bool:
    agents = getattr(manager, "agents", None)
    caller = agents.get(caller_id) if isinstance(agents, dict) and caller_id else None
    from src.agents.constants import AGENT_TYPE_PM
    return caller is not None and getattr(caller, "agent_type", None) == AGENT_TYPE_PM


def _task_tags(task: Any) -> set:
    try:
        return {str(tag) for tag in (get_task_field(task, "tags") or [])}
    except TypeError:
        return set()


def open_ready_tasks_of(tasks: Any, worker_id: str, skip_uuid: Optional[str] = None) -> List[Any]:
    """The worker's pending tasks that are not finished / decomposed and wait for no unfinished dependency."""
    from src.utils.task_utils import unfinished_dependencies
    result = []
    for task in tasks or []:
        if str(get_task_field(task, "assignee", "") or "").strip() != worker_id:
            continue
        if skip_uuid and str(get_task_field(task, "uuid", "")) == str(skip_uuid):
            continue
        if str(get_task_field(task, "status", "pending")).strip().lower() != "pending":
            continue
        if str(get_task_field(task, "task_progress", "todo")).strip().lower() in _OPEN_EXCLUDED_PROGRESS:
            continue
        if "project_kickoff" in _task_tags(task):
            continue
        try:
            if unfinished_dependencies(task):
                continue
        except Exception:
            pass
        result.append(task)
    return result


def busy_worker_assignment_error(tw: Any, assignee: str, task: Any, caller_id: Optional[str], manager: Any,
                                 task_ref: str, outcome: str = "was NOT changed") -> Optional[Dict[str, Any]]:
    """
    Round 3c: the refusal for a PM giving a READY task to a worker that already has an open READY task, or None. A task
    that waits for unfinished dependencies may still be queued for a busy worker (it starts that worker when it is ready).
    Only PM calls are checked; framework calls and workers (e.g. sub-tasks of a decomposition) pass. Never raises.
    """
    try:
        if caller_id in FRAMEWORK_CALLERS or not _is_pm_caller(caller_id, manager):
            return None
        from src.utils.task_utils import unfinished_dependencies
        if task is not None and unfinished_dependencies(task):
            return None
        pending = list(tw.tasks.pending())
        task_uuid = get_task_field(task, "uuid") if task is not None else None
        current = open_ready_tasks_of(pending, assignee, skip_uuid=task_uuid)
        if not current:
            return None
        busy = current[0]
        description = " ".join(str(get_task_field(busy, "description", "")).split())
        if len(description) > 90:
            description = description[:87] + "..."
        free = [w for w in team_worker_ids(manager, caller_id) if w != assignee and not open_ready_tasks_of(pending, w)]
        text = (f"{task_ref} {outcome}: '{assignee}' already has an open task ('{description}', "
                f"{get_task_field(busy, 'task_progress', 'todo')}, {get_task_field(busy, 'uuid', '?')}). A worker works on ONE "
                "task at a time, so a second ready task would only wait in its queue.")
        if free:
            text += f" Give this task to a worker without a task: {', '.join(free)}."
        else:
            text += (" All your workers have a task now: leave this task unassigned - you are woken up when a worker is free "
                     "(or, if the tasks can be done in parallel, create one more worker with the manage_team tool).")
        logger.warning(f"ProjectManagementTool: '{caller_id}' tried to give {task_ref} to the busy worker '{assignee}'. Refused.")
        return {"status": "error", "error_type": "busy_worker", "message": text}
    except Exception as err:
        logger.debug(f"busy_worker_assignment_error failed: {err}")
        return None


def task_core_text(title: Any, description: Any) -> str:
    """
    Round 3c: the text that says what a task is, for comparisons: title and description (one of them when it repeats the
    other - 01:59 'Kick-off Task 8: Write README.md ...: Write README.md ...'), without a 'Kick-off Task N:' label.
    """
    def collapse(first: str, second: str) -> Optional[str]:
        a, b = first.strip(), second.strip()
        if a and b and (a.lower() in b.lower() or b.lower() in a.lower()):
            return max((a, b), key=len)
        return None

    parts = [" ".join(str(part).split()) for part in (title, description) if part and str(part).strip()]
    parts = [_TASK_LABEL_RE.sub("", part).strip() for part in parts]
    if len(parts) == 1 and ": " in parts[0]:
        # a stored description 'Title: Description' whose description repeats its title
        head, _sep, tail = parts[0].partition(": ")
        parts = [collapse(head, tail) or parts[0]]
    if len(parts) == 2:
        merged = collapse(parts[0], parts[1])
        if merged:
            parts = [merged]
    return ": ".join(part for part in parts if part)


def finished_duplicate_error(tw: Any, title: Any, description: Any, project: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Round 3c: the refusal for a new task whose work a FINISHED task of the project already did (the same text, apart from
    a 'Kick-off Task N:' label), or None. Never raises.
    """
    try:
        import difflib
        wanted = task_core_text(title, description).lower()
        if len(wanted) < 12:
            return None
        finished = tw.tasks.filter(status="completed")
        if project:
            finished = finished.filter(project=project)
        for task in finished:
            if str(get_task_field(task, "task_progress", "finished")).strip().lower() == "deleted":
                continue
            done_text = str(get_task_field(task, "description", "") or "")
            done = task_core_text(None, done_text).lower()
            if len(done) < 12:
                continue
            ratio = difflib.SequenceMatcher(None, wanted, done).ratio()
            if ratio >= FINISHED_DUPLICATE_RATIO:
                uuid = get_task_field(task, "uuid", "?")
                logger.warning(f"ProjectManagementTool: new task '{description[:80]}' repeats the FINISHED task {uuid} ({ratio:.2f}). Refused.")
                return {"status": "error", "error_type": "finished_duplicate", "message": (
                    f"The task was NOT created: the same work is already FINISHED in task {uuid} ('{' '.join(done_text.split())[:100]}'). "
                    "Do not create it again. If that finished work has a problem, create a task that names the problem and "
                    "the file(s) to fix (e.g. 'Fix the failing start command in README.md: ...').")}
        return None
    except Exception as err:
        logger.debug(f"finished_duplicate_error failed: {err}")
        return None


STARTED_PROGRESS = ("doing", "in_progress")


def blocked_start_note(task, task_ref: str) -> str:
    """
    Round 2x: the note for a request to set a task to 'doing' / 'in_progress' while it depends on unfinished tasks ('' if
    it does not). Such a task cannot be started: its worker is started automatically once they are finished. 2026-10-22
    11:34:52 PM1 assigned Kick-off Task 2 to W1 with task_progress 'doing' while Task 1 was unfinished; for 45 minutes
    the task list showed it 'doing' and PM1 took W1 for busy although it never started.
    """
    from src.utils.task_utils import unfinished_dependencies
    try:
        blocking = unfinished_dependencies(task)
    except Exception:
        return ""
    if not blocking:
        return ""
    names = "; ".join(f"'{' '.join(description.split())[:60]}' ({uuid[:8]})" for uuid, description in blocking[:3])
    return (f" Note: task_progress was NOT set to started: {task_ref} depends on unfinished task(s) {names}, so it cannot "
            "start yet. It stays waiting; its assignee is started automatically as soon as they are finished.")


def non_worker_assignment_error(assignee: str, caller_id: Optional[str], manager: Any, task_ref: str,
                                outcome: str = "was NOT changed") -> Optional[Dict[str, Any]]:
    """Round 2t: the refusal for an agent assigning a task to a PM / Admin AI / the Guardian, or None (framework calls pass)."""
    if caller_id in FRAMEWORK_CALLERS:
        return None
    role = non_worker_role(assignee, manager)
    if not role:
        return None
    workers = team_worker_ids(manager, caller_id)
    target = f"one of your workers: {', '.join(workers)}" if workers else "a worker (create one first if your team has none)"
    logger.warning(f"ProjectManagementTool: '{caller_id}' tried to assign {task_ref} to '{assignee}' ({role}). Refused.")
    return {"status": "error", "error_type": "non_worker_assignee", "message": (
        f"{task_ref} {outcome}: '{assignee}' is {role}, and only workers carry out tasks - they write the files, "
        f"which Project Managers, the Admin AI and the Guardian cannot do. Assign it to {target}.")}


def clean_tag(value: Any) -> str:
    """Makes a value safe to use as a Taskwarrior tag: no quotes/brackets/commas, whitespace becomes '_'."""
    text = str(value).replace('"', "").replace("'", "").replace("[", "").replace("]", "").replace(",", "")
    return _WS_RE.sub("_", text.strip())


def _clean_tags_attr(match: "re.Match") -> str:
    parts: List[str] = []
    for element in match.group(1).split(","):
        element = _WS_RE.sub("_", _LITERAL_ESCAPE_RE.sub("", element).strip())
        if element and element not in parts:
            parts.append(element)
    return 'tags:"' + ",".join(parts) + '"'


def _clean_assignee_attr(match: "re.Match") -> str:
    cleaned = _LITERAL_ESCAPE_RE.sub("", match.group(1)).strip()
    return 'assignee:"' + (cleaned or match.group(1)) + '"'


def _clean_record(record: str) -> str:
    record = _TAGS_ATTR_RE.sub(_clean_tags_attr, record)
    record = _ASSIGNEE_ATTR_RE.sub(_clean_assignee_attr, record)
    seen: set = set()

    def _drop_duplicate_flag(m: "re.Match") -> str:
        if m.group(2) in seen:
            return ""
        seen.add(m.group(2))
        return m.group(1) + m.group(2)

    return _TAG_FLAG_RE.sub(_drop_duplicate_flag, record)


def repair_taskwarrior_records(lines: List[str]) -> List[str]:
    """
    Best-effort repair of Taskwarrior pending.data / completed.data lines:
      * drops empty lines and surrounding whitespace;
      * re-joins a record that a stray line break split over several lines (a record is "[...]");
      * removes line-break escapes/whitespace from tag values and duplicate tag flags.
    Well-formed records are returned unchanged.
    """
    records: List[str] = []
    partial: Optional[str] = None
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if partial is not None:
            if s.startswith("["):
                records.append(partial)  # unterminated fragment that cannot be completed: keep as is
                partial = None
            else:
                partial += s
                if s.endswith("]"):
                    records.append(partial)
                    partial = None
                continue
        if s.startswith("[") and not s.endswith("]"):
            partial = s
            continue
        records.append(s)
    if partial is not None:
        records.append(partial)
    return [_clean_record(r) if r.startswith("[") else r for r in records]

# Round 2r: with native tool calling the model fills EVERY optional parameter of the schema. 2026-10-21 19:31:18 PM1 sent
# modify_task {'task_progress': 'finished', 'priority': '', 'description': '', 'assignee_agent_id': '', ...}: the empty
# description reached Taskwarrior ("Cannot add a task that is blank"), the call failed 3 times and PM1 was set to 'error'
# for good. An empty value of an optional field means "not given" (an empty task_progress even reset a task to 'todo').
MODIFY_OPTIONAL_FIELDS = ("task_progress", "status", "description", "priority", "tags", "depends",
                          "assignee_agent_id", "assignee", "title")


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def drop_blank_fields(kwargs: Dict[str, Any], keys=MODIFY_OPTIONAL_FIELDS) -> List[str]:
    """Removes the given keys whose value is None, '' (or only spaces) or empty; returns the removed keys."""
    dropped = []
    for key in keys:
        if key in kwargs and _is_blank(kwargs[key]):
            kwargs.pop(key)
            dropped.append(key)
    return dropped


# Round 2w: names a model gives the assignee. 2026-10-22 11:08 PM1 sent modify_task with 'new_assignee_value': 'W1' and
# 'add_assignee_agent_id': 'W1' (and the action 'add_assignee_agent_to_task'); all were refused, PM1 went to 'error'.
ASSIGNEE_ALIAS_KEYS = ("assignee", "task_assignee", "new_assignee", "new_assignee_value", "add_assignee_agent_id",
                       "assignee_id", "assign_to", "assigned_to", "assign_to_agent", "worker_id", "worker", "target_agent_id")


def normalize_assignee_alias(kwargs: Dict[str, Any]) -> Optional[str]:
    """
    Round 2w: copies the first non-empty assignee alias into 'assignee_agent_id' when that is missing or empty.
    Returns the alias key used, or None.
    """
    if not _is_blank(kwargs.get("assignee_agent_id")):
        return None
    for key in ASSIGNEE_ALIAS_KEYS:
        if key in kwargs and not _is_blank(kwargs[key]):
            kwargs["assignee_agent_id"] = kwargs[key]
            return key
    return None


def resolve_task_reference(tw, aliases: Dict[str, str], ref: str):
    """
    Round 2w: the task an id names - an alias, an integer ID, a UUID, or a UUID with 1-2 characters wrong
    (match_task_by_uuid_prefix). Raises LookupError if there is no single such task. A malformed UUID is never handed
    to Taskwarrior.
    """
    text = str(ref or "").strip()
    text = aliases.get(text, text)
    if text.isdigit():
        return tw.tasks.get(id=int(text))
    if UUID_RE.match(text.lower()):
        try:
            return tw.tasks.get(uuid=text)
        except Exception as err:
            if "exist" not in str(err).lower():
                raise
    matches = match_task_by_uuid_prefix(tw.tasks.all(), text)
    if len(matches) == 1:
        return matches[0]
    raise LookupError(f"no task matches '{ref}'")


def taskwarrior_error_summary(err: Any) -> str:
    """The error lines of a Taskwarrior/tasklib exception without the 'Command used: task rc...' line."""
    lines = [line.strip() for line in str(err).splitlines() if line.strip()]
    kept = [line for line in lines if not line.lower().startswith("command used")]
    errors = [line for line in kept if not line.lower().startswith("note:")]
    return " ".join(errors or kept) or type(err).__name__

# Round 1v: W1 sent priority='normal' and the whole modify_task call was rejected.
_PRIORITY_WORDS = {
    "H": "H", "HIGH": "H", "URGENT": "H", "CRITICAL": "H", "TOP": "H", "IMPORTANT": "H",
    "M": "M", "MEDIUM": "M", "MED": "M", "MID": "M", "NORMAL": "M", "MODERATE": "M", "DEFAULT": "M", "STANDARD": "M",
    "L": "L", "LOW": "L", "MINOR": "L", "TRIVIAL": "L",
    "": "", "NONE": "", "NULL": "",
}
INVALID_PRIORITY_HINT = "Valid priorities are: H (high), M (medium), L (low)."


def normalize_priority(value: Any) -> Optional[str]:
    """'H', 'M', 'L', '' (no priority) or None if the value is not a priority."""
    return _PRIORITY_WORDS.get(str(value if value is not None else "").strip().upper())


def match_task_by_uuid_prefix(tasks, text: str) -> list:
    """
    Resolves a UUID the model has copied imperfectly. Small models often drop or alter characters in the MIDDLE
    of a UUID but copy the start correctly, so the lookup tries the text as a prefix and then its first 8 hex
    characters. Returns the matching tasks: one task = success, several = ambiguous (the caller refuses),
    none = not found.
    """
    needle_full = str(text or "").strip().lower()
    all_tasks = list(tasks)
    typo = _uuid_typo_match(all_tasks, needle_full)
    seen = []
    for needle in (needle_full, needle_full[:8]):
        if len(needle) < 8 or needle in seen or not re.match(r"^[0-9a-f-]+$", needle):
            continue
        seen.append(needle)
        matches = [t for t in all_tasks if str(t['uuid']).lower().startswith(needle)]
        if len(matches) == 1:
            if needle != needle_full:
                logger.info(f"ProjectManagementTool: Resolved imperfect task id '{text}' to UUID '{matches[0]['uuid']}' by its first 8 characters.")
            return matches
        if len(matches) > 1:
            return matches
    if typo is not None:
        logger.info(f"ProjectManagementTool: Resolved mistyped task id '{text}' to UUID '{typo['uuid']}' (1-2 characters differ).")
        return [typo]
    return []


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UUID_LIKE_RE = re.compile(r"^(?=.{34,38}$)[0-9a-f]+(?:-[0-9a-f]+){3,5}$")  # Round 2v: a UUID with a character too few/many
MAX_UUID_TYPOS = 2


def _uuid_typo_match(tasks, needle: str):
    """
    Round 2f: the ONE task whose UUID differs from a complete, well-formed UUID in at most 2 characters (PM1 wrote
    '3b7e308d-...' for '2b7e308d-...' on 2026-10-19 10:13, W2 '...-9f1c-...' for '...-9f0c-...' the day before). None if no
    task or more than one task is that close.
    Round 2v: also a UUID with a character too few or too many (W1 wrote 'c73067a-df34-...' for 'c73067a6-df34-...' on
    2026-10-22 10:36:56; Taskwarrior read it as a subtraction: "Cannot subtract strings").
    """
    if not UUID_RE.match(needle or "") and not UUID_LIKE_RE.match(needle or ""):
        return None
    close = []
    for task in tasks:
        uuid = str(task['uuid']).lower()
        if edit_distance_at_most(uuid, needle, MAX_UUID_TYPOS):
            close.append(task)
    return close[0] if len(close) == 1 else None


def edit_distance_at_most(a: str, b: str, limit: int) -> bool:
    """Round 2v: True if a can be turned into b with at most `limit` inserted, deleted or changed characters."""
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, char_b in enumerate(b, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


MAX_LISTED_OPEN_TASKS = 8


def open_tasks_listing(tw, limit: int = MAX_LISTED_OPEN_TASKS) -> str:
    """
    Round 2h: the open tasks with their exact UUIDs, for a "task not found" answer. PM1 (2026-10-19 19:36) invented the id
    'temp_conversion_logic_cli_tool_implementation_and_unit_tests' after a context reset and was only told to list the
    tasks, which costs another 1-3 minute reply on a 9b model. '' if there are none or the store cannot be read.
    """
    try:
        tasks = [t for t in tw.tasks.pending() if str(t['task_progress'] or '').strip().lower() != 'decomposed']
    except Exception:
        return ""
    if not tasks:
        return ""
    lines = ["Open tasks (use one of these exact task_id values):"]
    for task in tasks[:limit]:
        description = " ".join(str(task['description'] or '').split())
        if len(description) > 90:
            description = description[:87] + "..."
        assignee = str(task['assignee'] or '').strip() or "unassigned"
        lines.append(f"- {task['uuid']} [{task['task_progress'] or 'todo'}, {assignee}]: {description}")
    if len(tasks) > limit:
        lines.append(f"- ... and {len(tasks) - limit} more (use action='list_tasks').")
    return "\n".join(lines)


class ProjectManagementTool(BaseTool):
    name = "project_management"
    auth_level: str = "worker"
    # Round 2w: the summary named actions that do not exist (get_tasks, get_project_state), the description and the
    # 'action' parameter named none (2026-10-22 11:08 PM1 invented 'add_assignee_agent_to_task' and 'new_assignee_value')
    summary: Optional[str] = "Manages project tasks (add_task, list_tasks, modify_task, complete_task, get_dependency_graph) via Taskwarrior. Use tool_information with sub_action for per-action help."
    description = (
        "Manages the project's tasks (Taskwarrior). Actions: 'add_task' (description; optional task_id alias, depends, "
        "priority, assignee_agent_id), 'list_tasks' (optional filters), 'modify_task' (task_id plus ONLY the fields to "
        "change: assignee_agent_id, task_progress, description, priority, depends, tags), 'complete_task' (task_id), "
        "'get_dependency_graph'. To ASSIGN a task to a worker: "
        '{"action": "modify_task", "task_id": "<uuid>", "assignee_agent_id": "W1"} - the framework adds the worker\'s tag '
        "and starts the worker. Only workers (W1, W2, ...) can be assignees. Empty values ('') are ignored."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(name="action", type="str", required=True, description="One of: 'add_task', 'list_tasks', 'modify_task' (change a task, e.g. ASSIGN it with assignee_agent_id), 'complete_task', 'get_dependency_graph'."),
        ToolParameter(name="title", type="str", required=False, description="Short task title (used as description if description is omitted)."),
        ToolParameter(name="description", type="str", required=False, description="Task description."),
        ToolParameter(name="task_id", type="str", required=False, description="For add_task: an optional alias for the new task (e.g. 'task_1'). For the other actions: the task's UUID (exactly as listed), ID or alias."),
        ToolParameter(name="task_progress", type="str", required=False, description="Progress of the task: 'todo', 'doing', 'in_progress', 'waiting', 'stuck', 'decomposed', 'finished' or 'deleted'. A task that depends on unfinished tasks is never set to 'doing'/'in_progress' (its assignee starts it automatically once they are finished)."),
        ToolParameter(name="priority", type="str", required=False, description="Task priority: 'H' (high), 'M' (medium) or 'L' (low)."),
        ToolParameter(name="project_filter", type="str", required=False, description="Filter tasks by project."),
        ToolParameter(name="assignee_filter", type="str", required=False, description="For 'list_tasks': only tasks of this agent ('unassigned' for tasks without one)."),
        ToolParameter(name="task_progress_filter", type="str", required=False, description="For 'list_tasks': only tasks with this progress (e.g. 'todo')."),
        ToolParameter(name="tags_filter", type="str", required=False, description="For 'list_tasks': comma-separated tags; also the magic tags 'assigned' / 'unassigned'."),
        ToolParameter(name="tags_filter_mode", type="str", required=False, description="For 'list_tasks': 'include' (default) or 'exclude' the tasks that have tags_filter."),
        ToolParameter(name="tags", type="list", required=False, description="List of tags."),
        ToolParameter(name="depends", type="str", required=False, description="UUID (or alias) of the task this one depends on; several separated by commas."),
        ToolParameter(name="assignee_agent_id", type="str", required=False, description="Agent ID for assignment: a WORKER (e.g. 'W1'); a Project Manager, the Admin AI or the Guardian is refused."),
        ToolParameter(name="dry_run", type="boolean", required=False, description="If true, simulates the action without saving to the database. Useful to verify complex state changes."),
    ]

    def __init__(self, project_name: Optional[str] = None, session_name: Optional[str] = None):
        if not TASKLIB_AVAILABLE:
            logger.error("Tasklib library is not installed. ProjectManagementTool will not function.")

    def _heal_taskwarrior_data(self, data_path: Path) -> None:
        """Self-heals Taskwarrior data files by stripping empty or corrupted lines."""
        for fname in ("pending.data", "completed.data", "undo.data"):
            fpath = data_path / fname
            if not fpath.is_file():
                continue
            try:
                raw_text = fpath.read_text(encoding="utf-8", errors="replace")
                lines = raw_text.splitlines()
                if fname == "undo.data":
                    # undo.data is a multi-line journal: only strip empty/whitespace lines (original behaviour).
                    cleaned = [ln.strip() for ln in lines if ln.strip()]
                else:
                    # pending/completed: also re-join records split by a stray line break and clean tag values.
                    cleaned = repair_taskwarrior_records(lines)
                if len(cleaned) != len(lines) or any(ln != orig for ln, orig in zip(cleaned, lines)):
                    fpath.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")
                    logger.warning(f"ProjectManagementTool: Auto-healed {fname} ({len(lines)} -> {len(cleaned)} line(s): removed empty lines, re-joined split records, cleaned tag values).")
            except Exception as heal_err:
                logger.debug(f"ProjectManagementTool: Could not check/heal {fname}: {heal_err}")

    # Round 2w: parameters the tool reads besides its schema parameters (names models use for the same thing)
    ACCEPTED_EXTRA_KEYS = frozenset({"manager", "fields", "field", "value", "task_uuid", "status", "status_filter",
                                     "title_filter", "task_title", "task_description", "task_priority", "task_depends",
                                     "include_decomposed", "include_completed", "show_completed"} | set(ASSIGNEE_ALIAS_KEYS))

    def _unknown_parameters_note(self, kwargs: Dict[str, Any]) -> str:
        """Round 2w: ' Unknown parameter(s) ignored: ...' plus the assignment example, or ''."""
        known = {param.name for param in self.parameters} | self.ACCEPTED_EXTRA_KEYS
        unknown = sorted(key for key in kwargs if key not in known)
        if not unknown:
            return ""
        return (f" Unknown parameter(s) were ignored: {', '.join(unknown)}. To assign the task to a worker send exactly: "
                "{\"action\": \"modify_task\", \"task_id\": \"UUID\", \"assignee_agent_id\": \"W1\"}.")

    def _is_worker_agent(self, agent_id: Optional[str]) -> bool:
        """True if agent_id represents a worker (not admin, PM, framework, or system)."""
        if not agent_id:
            return False
        aid = str(agent_id).lower()
        if aid in ("admin_ai", "framework", "system", "human_user") or aid.startswith("pm"):
            return False
        return True

    def _get_taskwarrior_instance(self, project_name: str, session_name: str) -> Any:
        if not TASKLIB_AVAILABLE:
            return None
        data_path = BASE_DIR / "projects" / project_name / session_name / "task_data"
        try:
            data_path.mkdir(parents=True, exist_ok=True)
            self._heal_taskwarrior_data(data_path)
            taskrc_path = data_path / '.taskrc'
            if not taskrc_path.exists():
                with open(taskrc_path, 'w') as f:
                    f.write("uda.assignee.type=string\nuda.assignee.label=Assignee\n")
                    f.write("uda.task_progress.type=string\nuda.task_progress.label=Task Progress\n")
            else:
                # Patch existing taskrc
                with open(taskrc_path, 'r') as f:
                    content = f.read()
                if "uda.task_progress.type" not in content:
                    with open(taskrc_path, 'a') as f:
                        f.write("\nuda.task_progress.type=string\nuda.task_progress.label=Task Progress\n")
            return TaskWarrior(data_location=str(data_path), taskrc_location=str(taskrc_path))
        except Exception as e:
             logger.error(f"Failed to initialize TaskWarrior at {data_path}: {e}", exc_info=True)
             return None

    def _load_aliases(self, project_name: str, session_name: str) -> Dict[str, str]:
        import json
        alias_path = BASE_DIR / "projects" / project_name / session_name / "task_data" / "task_aliases.json"
        if alias_path.exists():
            try:
                with open(alias_path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_aliases(self, project_name: str, session_name: str, aliases: Dict[str, str]):
        import json
        alias_path = BASE_DIR / "projects" / project_name / session_name / "task_data" / "task_aliases.json"
        try:
            with open(alias_path, "w") as f:
                json.dump(aliases, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save aliases: {e}")

    def _map_task_progress(self, raw_progress: Optional[str]) -> tuple[str, str]:
        """
        Maps a varied text description to a standard 'task_progress' UDA value
        and calculates the underlying strict TaskWarrior 'status'.
        Returns: (standard_progress, tw_status)
        """
        if not raw_progress:
            return "todo", "pending"
            
        progress_norm = str(raw_progress).strip().lower()
        
        # Standard progress mapping rules to give LLMs flexibility
        if progress_norm in ["todo", "not_started", "open", "new", "pending"]:
            return "todo", "pending"
        elif progress_norm in ["doing"]:
            return "doing", "pending"
        elif progress_norm in ["in_progress", "active", "working", "started", "progressing"]:
            return "in_progress", "pending"
        elif progress_norm in ["waiting", "blocked", "paused", "on_hold"]:
            return "waiting", "waiting"
        elif progress_norm in ["stuck", "failed", "error", "issue"]:
            return "stuck", "pending"  # Still pending structurally, just stuck temporally
        elif progress_norm in ["decomposed", "broken_down", "split"]:
            return "decomposed", "pending"
        elif progress_norm in ["finished", "done", "completed", "complete", "closed"]:
            return "finished", "completed"
        elif progress_norm in ["deleted", "cancelled", "canceled", "dropped"]:
            return "deleted", "deleted"
            
        # Fallback if unknown but provided
        return progress_norm, "pending"

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        if not TASKLIB_AVAILABLE:
            return {"status": "error", "message": "Tasklib library not installed."}

        action = kwargs.get("action")
        
        if action in ["send_status_update", "status_update", "message", "send_message"]:
            return {
                "status": "error",
                "message": "Invalid action. The 'project_management' tool is ONLY for modifying the task database. If you are trying to talk to the Project Manager or report progress, please use the separate 'send_message' tool."
            }
            
        if action in ["switch_state", "change_state", "request_state"]:
            return {
                "status": "error",
                "message": "ERROR: You cannot change your state using the project_management tool. You must use the 'request_state' native JSON tool instead."
            }
            
        if action in ["read_message", "get_message", "check_messages"]:
            return {
                "status": "error",
                "message": "ERROR: There is no 'read_message' action. All messages sent to you are automatically appended to your conversation history. Just read your recent context."
            }
            
        if action in ["read_file", "read", "view_file", "cat"]:
            return {
                "status": "error",
                "message": "ERROR: You cannot read files using the project_management tool. You MUST use the separate 'file_system' tool with action='read' to read files."
            }

        # Check for common mistakes and provide helpful suggestions
        action_suggestions = {
            "assign_task": "modify_task",
            "create_task": "add_task",
            "decompose_task": "add_task",
            "decompose": "add_task",
            "new_task": "add_task", 
            "add": "add_task",
            "create": "add_task",
            "list": "list_tasks",
            "show": "list_tasks",
            "show_tasks": "list_tasks",
            "get_tasks": "list_tasks",
            "list_subtasks": "list_tasks",
            "update_project_status": "modify_task",
            "update_task_status": "modify_task",
            "update_status": "modify_task",
            "update_task": "modify_task",
            "edit_task": "modify_task",
            "change_task": "modify_task",
            "update": "modify_task",
            "edit": "modify_task",
            "update_progress": "modify_task",
            "set_progress": "modify_task",
            "progress_update": "modify_task",
            "update_task_progress": "modify_task",
            "set_task_progress": "modify_task",
            "modify_task_progress": "modify_task",  # Round 2f: PM1 2026-10-19 10:24
            "modify_task_status": "modify_task",
            "update_task_status": "modify_task",
            "set_task_status": "modify_task",
            "change_task_progress": "modify_task",
            "transition_to": "modify_task",
            "finish_task": "complete_task",
            "done": "complete_task",
            "finish": "complete_task",
            "mark_complete": "complete_task",
            "mark_completed": "complete_task",
            "mark_task_complete": "complete_task",
            "mark_task_finished": "complete_task",
            "mark_finished": "complete_task",
            "complete": "complete_task",
            "task_complete": "complete_task",
            "get_task": "list_tasks",
            "get_task_list": "list_tasks",
            "get": "list_tasks",
            "task_list": "list_tasks",
            "list_sub_tasks": "list_tasks",
            "list_subtask": "list_tasks",
            "view_tasks": "list_tasks",
            "start_task": "modify_task",
            "begin_task": "modify_task",
            "delete_task": "modify_task",
            "delete": "modify_task",
            "remove_task": "modify_task",
            "remove": "modify_task",
        }
        
        if not action:
            return {
                "status": "error", 
                "message": "Missing required 'action' parameter. Must be one of: add_task, list_tasks, modify_task, complete_task, get_dependency_graph.",
                "error_type": "missing_parameter",
                "valid_actions": ["add_task", "list_tasks", "modify_task", "complete_task", "get_dependency_graph"]
            }
        
        valid_actions = ["add_task", "list_tasks", "modify_task", "complete_task", "get_dependency_graph"]
        if action not in valid_actions:
            if action in action_suggestions:
                corrected_action = action_suggestions[action]
                logger.info(f"ProjectManagementTool: Auto-correcting action '{action}' -> '{corrected_action}'")
                
                if action in ["delete_task", "delete", "remove_task", "remove"]:
                    kwargs["task_progress"] = "deleted"
                    logger.info("ProjectManagementTool: Auto-injected task_progress='deleted' for deletion intent.")
                    
                action = corrected_action
            elif "assign" in str(action).lower():
                # Round 2w: 'add_assignee_agent_to_task', 'assign_worker', ... (PM1 2026-10-22 11:08:29) mean modify_task
                logger.info(f"ProjectManagementTool: Auto-correcting action '{action}' -> 'modify_task' (an assignment).")
                action = "modify_task"
            else:
                return {
                    "status": "error",
                    "message": f"Invalid action '{action}'. Valid actions are: {', '.join(valid_actions)}.",
                    "error_type": "invalid_action",
                    "valid_actions": valid_actions
                }
        if not project_name or not session_name:
            return {"status": "error", "message": "Missing project/session context."}
            
        # [CRITICAL GUARD] Override any user/LLM-provided project_filter with the authenticated context project_name.
        # This prevents Hallucinations/Typos (like "Snake" vs "Sake") from causing empty task list queries 
        # or creating tasks with incorrect project attributes in the dedicated taskwarrior DB.
        kwargs["project_filter"] = project_name

        tw = self._get_taskwarrior_instance(project_name, session_name)
        if not tw:
            return {"status": "error", "message": "Failed to initialize TaskWarrior backend."}

        if action in ("add_task", "modify_task"):
            alias_key = normalize_assignee_alias(kwargs)  # Round 2w
            if alias_key:
                logger.info(f"ProjectManagementTool: '{agent_id}' gave the assignee as '{alias_key}'; used as assignee_agent_id.")

        try:
            aliases = self._load_aliases(project_name, session_name)

            if action == "add_task":
                return await self._execute_add_task(tw, aliases, project_name, session_name, agent_id, kwargs)
            elif action == "list_tasks":
                return await self._execute_list_tasks(tw, agent_id, kwargs)
            elif action == "modify_task":
                return await self._execute_modify_task(tw, aliases, project_name, session_name, agent_id, kwargs)
            elif action == "complete_task":
                return await self._execute_complete_task(tw, aliases, project_name, session_name, kwargs)
            elif action == "get_dependency_graph":
                return await self._execute_get_dependency_graph(tw, kwargs)
            else:
                return {"status": "error", "message": f"Unknown action: '{action}'."}

        except Exception as e:
            logger.error(f"Error executing ProjectManagementTool action '{action}': {e}", exc_info=True)
            return {"status": "error", "message": f"An unexpected error occurred: {taskwarrior_error_summary(e)}"}  # Round 2r


    async def _execute_add_task(self, tw, aliases, project_name, session_name, agent_id, kwargs):
            # Auto-map hallucinated parameter names common with LLMs
            if "task_title" in kwargs and not kwargs.get("title"): kwargs["title"] = kwargs.get("task_title")
            if "task_description" in kwargs and not kwargs.get("description"): kwargs["description"] = kwargs.get("task_description")
            if "task_priority" in kwargs and not kwargs.get("priority"): kwargs["priority"] = kwargs.get("task_priority")
            if "task_assignee" in kwargs and not kwargs.get("assignee_agent_id"): kwargs["assignee_agent_id"] = kwargs.get("task_assignee")
            if "task_depends" in kwargs and not kwargs.get("depends"): kwargs["depends"] = kwargs.get("task_depends")

            title = kwargs.get("title")
            description = kwargs.get("description")
            
            main_desc = None
            if title and description:
                main_desc = f"{title}: {description}"
            elif title:
                main_desc = title
            elif description:
                main_desc = description
            
            if not main_desc:
                return {"status": "error", "message": "Missing 'title' or 'description' for 'add_task'."}

            # Sanitize newlines: Taskwarrior line-oriented format breaks on embedded newlines
            main_desc = " ".join(str(main_desc).splitlines()).strip()

            import difflib
            existing_tasks = tw.tasks.pending()
            if kwargs.get("project_filter"):
                existing_tasks = existing_tasks.filter(project=kwargs["project_filter"])
                
            target_desc_lower = main_desc.lower()
            for t in existing_tasks:
                existing_desc = (t['description'] or "").lower() if 'description' in t else ""
                if len(target_desc_lower) < 5 or len(existing_desc) < 5:
                    continue
                
                similarity = difflib.SequenceMatcher(None, target_desc_lower, existing_desc).ratio()
                if similarity > 0.8 or target_desc_lower in existing_desc or existing_desc in target_desc_lower:
                    logger.warning(f"ProjectManagementTool: Prevented duplicate task creation: '{main_desc}' is very similar ({similarity:.2f}) to existing task UUID {t['uuid']}")
                    # Map alias for skipped duplicate so downstream dependencies still resolve
                    user_task_id = kwargs.get("task_id")
                    if user_task_id and isinstance(user_task_id, str):
                        aliases[user_task_id.strip()] = t['uuid']
                        self._save_aliases(project_name, session_name, aliases)
                    depends_list = [dep['uuid'] for dep in t['depends']] if 'depends' in t and t['depends'] else []
                    return {
                        "status": "success",
                        "message": f"Task skipped to prevent duplication. An existing task ('{t['description'] if 'description' in t else ''}') is >80% similar.",
                        "task_uuid": t['uuid'],
                        "task_id": t['id'] if 'id' in t else 0,
                        "description": t['description'] if 'description' in t else '',
                        "assignee": t['assignee'] if 'assignee' in t and t['assignee'] else 'None',
                        "depends": depends_list,
                        "is_duplicate": True
                    }

            # Round 3c: work a FINISHED task already did is not planned again (the kick-off and update plans are exempt: the
            # framework creates them from a fresh plan)
            tag_values = kwargs.get("tags")
            tag_text = ",".join(str(t) for t in tag_values) if isinstance(tag_values, (list, tuple, set)) else str(tag_values or "")
            if agent_id not in FRAMEWORK_CALLERS and "kickoff" not in tag_text.lower():
                refusal = finished_duplicate_error(tw, title, description, kwargs.get("project_filter"))
                if refusal:
                    return refusal

            task = Task(tw, description=main_desc)
            
            # Apply unified task_progress mapping (alias handling for 'status' fallback)
            raw_prog = kwargs.get("task_progress") or kwargs.get("status")
            t_prog, t_status = self._map_task_progress(raw_prog)
            task['task_progress'] = t_prog
            task['status'] = t_status
            
            if kwargs.get("priority"):
                prio = normalize_priority(kwargs["priority"])
                if prio is None: return {"status": "error", "message": f"Invalid priority '{kwargs['priority']}'. {INVALID_PRIORITY_HINT}"}
                if prio: task['priority'] = prio
            if kwargs.get("project_filter"): task['project'] = kwargs["project_filter"]
            
            if kwargs.get("assignee_agent_id"): 
                clean_assignee = clean_agent_id(kwargs["assignee_agent_id"])
                if not clean_assignee:
                    return {"status": "error", "message": f"Invalid assignee_agent_id {kwargs['assignee_agent_id']!r}. Use the plain agent id such as 'W1' (letters, digits, '_', '.', ':' or '-'; no spaces or line breaks)."}
                refusal = non_worker_assignment_error(clean_assignee, agent_id, kwargs.get("manager"), "The task",
                                                      outcome="was NOT created")  # Round 2t
                if refusal:
                    return refusal
                kwargs["assignee_agent_id"] = clean_assignee
                task['assignee'] = clean_assignee
            elif self._is_worker_agent(agent_id):
                # Auto-assign the task to the worker who is creating it if no assignee is specified
                task['assignee'] = agent_id
                
            if kwargs.get("tags"):
                tags_arg = kwargs["tags"]
                if isinstance(tags_arg, str):
                    raw_tags = [tag.strip() for tag in tags_arg.split(',') if tag.strip()]
                elif isinstance(tags_arg, list):
                    raw_tags = [str(t).strip() for t in tags_arg if t]
                else:
                    raw_tags = [str(tags_arg).strip()]
                sanitized = []
                for t in raw_tags:
                    t = clean_tag(t.lstrip('+-'))
                    if t:
                        sanitized.append(t)
                task['tags'] = set(sanitized)
                
            if get_task_field(task, 'assignee'):
                current_tags = set(get_task_field(task, 'tags') or [])
                current_tags.add("assigned")
                current_tags.add(clean_tag(task['assignee']))
                task['tags'] = current_tags
                
            if kwargs.get("depends"): 
                dep_val_raw = str(kwargs["depends"]).strip()
                # Clean up common LLM hallucinations like list formatting '[]', '["task_1"]' or literal 'None'
                dep_val_raw = dep_val_raw.replace('[', '').replace(']', '').replace('"', '').replace("'", "").strip()
                if dep_val_raw.lower() in ['none', 'null', '']:
                    dep_items = []
                else:
                    # Support comma-separated dependency lists (e.g. "task_1,task_2,task_3")
                    dep_items = [d.strip() for d in dep_val_raw.split(',') if d.strip()]
                
                resolved_deps = set()
                for dep_item in dep_items:
                    # Resolve aliases first
                    if dep_item in aliases:
                        dep_item = aliases[dep_item]
                    
                    is_valid_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', dep_item, re.IGNORECASE))
                    is_valid_id = dep_item.isdigit()
                    
                    if is_valid_uuid or is_valid_id:
                        try:
                            dep_task = tw.tasks.get(uuid=dep_item) if is_valid_uuid else tw.tasks.get(id=int(dep_item))
                            resolved_deps.add(dep_task)
                            logger.info(f"ProjectManagementTool: Resolved dependency '{dep_item}' -> UUID '{dep_task['uuid']}'.")
                        except Exception as e:
                            logger.warning(f"ProjectManagementTool: Dependency task '{dep_item}' not found: {e}. Skipping this dependency.")
                    else:
                        logger.warning(f"ProjectManagementTool: Invalid dependency format: '{dep_item}' (from raw: '{dep_val_raw}').")
                        return {
                            "status": "error",
                            "message": f"Invalid dependency format: '{dep_item}'. Dependencies must be valid UUIDs, integer IDs, or recognized task aliases (e.g. 'T1'). If you don't know the ID, use action='list_tasks' to find it.",
                            "error_type": "invalid_dependency_format"
                        }
                if resolved_deps:
                    task['depends'] = resolved_deps
                    logger.info(f"ProjectManagementTool: Set {len(resolved_deps)} dependencies for task.")

            if get_task_field(task, 'assignee'):
                # Round 3c: one ready task at a time per worker (PM calls)
                refusal = busy_worker_assignment_error(tw, str(task['assignee']), task, agent_id, kwargs.get("manager"),
                                                       "The task", outcome="was NOT created")
                if refusal:
                    return refusal

            add_blocked_note = ""
            if task['task_progress'] in STARTED_PROGRESS:
                # Round 2x: a new task that waits for unfinished dependencies does not start as 'doing'
                add_blocked_note = blocked_start_note(task, "The new task")
                if add_blocked_note:
                    task['task_progress'] = "todo"
                    task['status'] = "pending"

            # --- DECOMPOSE STATE: Tag parent as decomposed (keep pending) ---
            manager = kwargs.get("manager")
            if manager:
                agent = manager.agents.get(agent_id)
                if agent and agent.agent_type == "worker" and agent.state == "worker_decompose" and getattr(agent, 'current_task_id', None):
                    try:
                        parent_task_id = str(agent.current_task_id).strip()
                        parent_is_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', parent_task_id, re.IGNORECASE))
                        parent_task = tw.tasks.get(uuid=parent_task_id) if parent_is_uuid else tw.tasks.get(id=int(parent_task_id))
                        
                        # Tag the sub-task with parent UUID for traceability (no dependency link)
                        existing_sub_tags = list(get_task_field(task, 'tags') or [])
                        existing_sub_tags.append(f"parent:{parent_task['uuid']}")
                        task['tags'] = list(set(existing_sub_tags))
                        
                        # Do NOT mark the parent task as completed/finished here!
                        # If the worker decides NOT to decompose the task (or assigns subtasks elsewhere),
                        # the parent task must remain assigned and in progress.
                        # Genuine decomposition completion is handled safely by workflow_manager.py during state transition.
                        
                        logger.info(f"ProjectManagementTool: Sub-task '{task['uuid']}' tagged with parent UUID '{parent_task_id}' for traceability.")
                    except Exception as e:
                        logger.warning(f"ProjectManagementTool: Failed to tag parent task '{agent.current_task_id}' as decomposed: {e}")
            # ------------------------------------------------

            try:
                is_dry_run = str(kwargs.get("dry_run")).lower() == "true"
                if not is_dry_run:
                    task.save()
                    asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": project_name, "session_name": session_name})))
                    
                    # --- DECOMPOSE DEPENDENCY TRANSFER ---
                    manager = kwargs.get("manager")
                    if manager:
                        agent = manager.agents.get(agent_id)
                        if agent and agent.agent_type == "worker" and agent.state == "worker_decompose" and getattr(agent, 'current_task_id', None):
                            try:
                                parent_task_id = str(agent.current_task_id).strip()
                                parent_is_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', parent_task_id, re.IGNORECASE))
                                parent_task = tw.tasks.get(uuid=parent_task_id) if parent_is_uuid else tw.tasks.get(id=int(parent_task_id))
                                
                                # Transfer dependencies: Any task depending on parent should now also depend on this sub-task
                                for pending_task in tw.tasks.pending():
                                    if pending_task['uuid'] == task['uuid'] or pending_task['uuid'] == parent_task['uuid']:
                                        continue
                                    if pending_task['depends']:
                                        parent_in_deps = False
                                        for dep in pending_task['depends']:
                                            if dep['uuid'] == parent_task['uuid']:
                                                parent_in_deps = True
                                                break
                                        if parent_in_deps:
                                            pending_task['depends'].add(task)
                                            pending_task.save()
                                            asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": project_name, "session_name": session_name})))
                                            logger.info(f"ProjectManagementTool: Transferred dependency to sub-task '{task['uuid']}' for dependent task '{pending_task['uuid']}'.")
                            except Exception as e:
                                logger.warning(f"ProjectManagementTool: Failed to transfer dependencies for parent task '{agent.current_task_id}': {e}")
                    # ------------------------------------------------
                else:
                    logger.info("ProjectManagementTool: 'add_task' dry_run succeeded. Database not modified.")
            except Exception as e:
                return {"status": "error", "message": f"Failed to save task: {e}"}

            user_task_id = kwargs.get("task_id")
            try:
                simulated_uuid = task['uuid'] if 'uuid' in task and task['uuid'] is not None else '00000000-0000-0000-0000-000000000000_dryrun'
            except KeyError:
                simulated_uuid = '00000000-0000-0000-0000-000000000000_dryrun'
                
            try:
                simulated_id = task['id'] if 'id' in task and task['id'] is not None else 0
            except KeyError:
                simulated_id = 0
            
            if user_task_id and isinstance(user_task_id, str) and not is_dry_run:
                user_task_id = user_task_id.strip()
                if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', user_task_id, re.IGNORECASE):
                    logger.warning(f"ProjectManagementTool: Ignored 'task_id'={user_task_id} in add_task because it is a UUID format. Aliases should be custom names like 'task_1'.")
                else:
                    aliases[user_task_id] = task['uuid']
                    self._save_aliases(project_name, session_name, aliases)
                
            depends_list = [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
            return {
                "status": "success", 
                "message": f"Task added successfully{'. (DRY RUN)' if is_dry_run else '.'}" + add_blocked_note, 
                "task_uuid": simulated_uuid if is_dry_run else task['uuid'], 
                "task_id": simulated_id if is_dry_run else task['id'], 
                "description": task['description'], 
                "assignee": kwargs.get("assignee_agent_id"), 
                "depends": depends_list,
                "dry_run": is_dry_run
            }

    async def _execute_list_tasks(self, tw, agent_id, kwargs):
            tasks_query = tw.tasks.all()
            if kwargs.get("project_filter"): tasks_query = tasks_query.filter(project=kwargs["project_filter"])
            
            # Allow new task_progress_filter or legacy status_filter
            if "task_progress_filter" in kwargs:
                tasks_query = tasks_query.filter(task_progress=kwargs["task_progress_filter"])
            elif "status_filter" in kwargs:
                if kwargs["status_filter"] != "all":
                    tasks_query = tasks_query.filter(status=kwargs["status_filter"])
                # else: "all" means no status filtering at all
            else:
                # Default: filter out completed tasks to save tokens
                tasks_query = tasks_query.pending()
            
            completed_count = 0
            try:
                all_tasks_query = tw.tasks.all()
                if kwargs.get("project_filter"): 
                    all_tasks_query = all_tasks_query.filter(project=kwargs["project_filter"])
                completed_count = len(all_tasks_query.completed())
            except Exception as e:
                logger.debug(f"Could not fetch completed tasks count: {e}")
            
            # Round 1l: callers that pass no filter get ALL pending tasks. (Before, the Admin AI and the PM silently
            # got only UNASSIGNED tasks, so "Found 0 task(s)" hid every task that was already assigned to a worker.)
            # Workers still default to their own tasks below; explicit filters behave exactly as before.

            if "assignee_filter" in kwargs: 
                a_filter = str(kwargs["assignee_filter"]).strip()
                if a_filter.lower() not in ["none", "null", "unassigned"]:
                    tasks_query = tasks_query.filter(assignee=a_filter)
            elif agent_id and self._is_worker_agent(agent_id):
                # Auto-filter for the specific worker if no filter is provided
                tasks_query = tasks_query.filter(assignee=agent_id)
            
            # Gracefully handle corrupted TaskWarrior data (e.g. malformed tags causing Invalid JSON)
            try:
                tasks = list(tasks_query)  # evaluate now: a lazy query would only fail later, outside this handler
            except Exception as tw_err:
                err_str = str(tw_err)
                if "Invalid JSON" in err_str or "JSONDecodeError" in err_str:
                    logger.error(f"ProjectManagementTool: TaskWarrior data corruption detected: {err_str[:200]}")
                    # Fallback: try fetching without filters to identify scope
                    try:
                        raw_count = len(tw.tasks.all())
                    except Exception:
                        raw_count = "unknown"
                    return {
                        "status": "error",
                        "message": f"TaskWarrior data contains corrupted entries (likely malformed tags). Total tasks in DB: {raw_count}. Consider modifying the corrupted task's tags to fix this.",
                        "error_type": "data_corruption"
                    }
                if "Unrecognized Taskwarrior file format" in err_str:
                    logger.error(f"ProjectManagementTool: TaskWarrior data file is damaged: {err_str[-300:]}")
                    return {
                        "status": "error",
                        "message": "The task database file is damaged and could not be read. The framework repairs such files automatically before each call; if this message repeats, tell the user.",
                        "error_type": "data_corruption"
                    }
                raise  # re-raise non-JSON errors
            
            if "assignee_filter" in kwargs and str(kwargs["assignee_filter"]).strip().lower() in ["none", "null", "unassigned"]:
                tasks = [t for t in tasks if t['assignee'] is None or str(t['assignee']).strip() == ""]

            # Fallback: if 'tags' is passed but not 'tags_filter', use 'tags' as filter to be tolerant of LLM errors
            if "tags_filter" not in kwargs and kwargs.get("tags"):
                tags_arg = kwargs.get("tags")
                if isinstance(tags_arg, list):
                    kwargs["tags_filter"] = ",".join(str(t) for t in tags_arg)
                elif isinstance(tags_arg, str):
                    kwargs["tags_filter"] = tags_arg

            if "tags_filter" in kwargs:
                try:
                    filter_tags = set(tag.strip().lower() for tag in kwargs["tags_filter"].split(","))
                    mode = str(kwargs.get("tags_filter_mode", "include")).lower().strip()
                    filtered_tasks = []
                    for task in tasks:
                        task_tags = set(str(tag).lower() for tag in (task['tags'] if task['tags'] is not None else []))
                        
                        # Magic tags based on assignee status to accommodate LLM behavior
                        is_assigned = task['assignee'] is not None and str(task['assignee']).strip() != ""
                        if is_assigned:
                            task_tags.add("assigned")
                        else:
                            task_tags.add("unassigned")
                            
                        if mode == "include" and filter_tags.issubset(task_tags):
                            filtered_tasks.append(task)
                        elif mode == "any" and filter_tags.intersection(task_tags):
                            filtered_tasks.append(task)
                        elif mode == "exclude" and not filter_tags.intersection(task_tags):
                            filtered_tasks.append(task)
                    tasks = filtered_tasks
                except Exception as e:
                    logger.warning(f"Error filtering tasks by tags: {e}")
            
            include_decomposed = kwargs.get("include_decomposed", False)
            if isinstance(include_decomposed, str):
                include_decomposed = include_decomposed.lower() == 'true'

            # Create a truly minimal output format replacing low-fi status with descriptive task_progress
            minimal_task_list = []
            decomposed_hidden_count = 0
            for task in tasks:
                try:
                    t_prog = task['task_progress']
                except KeyError:
                    t_prog = None
                    
                if not t_prog:
                    # Fallback for old tasks that lacked the UDA
                    try:
                        t_status = task['status']
                    except KeyError:
                        t_status = None
                    t_prog = "finished" if t_status == "completed" else "todo"
                
                # Auto-hide decomposed parent tasks from default queries.
                # These are shell tasks that have been broken into sub-tasks;
                # showing them to the PM causes confusion and reassignment loops.
                task_tags = list(task['tags']) if task['tags'] else []
                if not include_decomposed and (t_prog == 'decomposed' or 'decomposed' in task_tags):
                    decomposed_hidden_count += 1
                    continue
                    
                minimal_task_list.append({
                    "uuid": task['uuid'], 
                    "description": task['description'], 
                    "task_progress": t_prog, 
                    "assignee": task['assignee'] if task['assignee'] is not None else None,
                    "depends": [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
                })
            
            hidden_note = f" ({completed_count} completed"
            if decomposed_hidden_count > 0:
                hidden_note += f", {decomposed_hidden_count} decomposed"
            hidden_note += " tasks hidden by default)."
            return {"status": "success", "message": f"Found {len(minimal_task_list)} task(s).{hidden_note}", "tasks": minimal_task_list}

    def _completion_refusal(self, task, project_name, session_name, task_label) -> Optional[Dict[str, Any]]:
        """
        Round 2a: an error result if the task may not be marked finished yet because existing Python files named in its
        description (Round 2n: or changed for it by its worker) are empty, invalid or have defects
        (src/utils/task_files.py), else None. Never raises.
        """
        try:
            from src.config.settings import settings
            from src.tools.sandbox_runner import resolve_shared_workspace
            from src.utils.task_files import task_completion_problems, completion_refusal_message
            workspace = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_name), str(session_name))
            # Round 2n: + the Python files the assignee changed while working on this task
            from src.utils.file_authorship import files_changed_for_task
            try:
                task_uuid = task['uuid']
            except Exception:
                task_uuid = None
            changed = files_changed_for_task(str(task_uuid), workspace) if task_uuid else []
            problems = task_completion_problems(task['description'], workspace, changed)
        except Exception as gate_err:
            logger.warning(f"ProjectManagementTool: completion check failed ({gate_err}); not blocking.")
            return None
        if not problems:
            return None
        logger.warning(f"ProjectManagementTool: refused to mark task '{task_label}' finished: {problems}")
        return {"status": "error", "message": completion_refusal_message(str(task_label), problems)}

    async def _execute_modify_task(self, tw, aliases, project_name, session_name, agent_id, kwargs):
            task_id = kwargs.get("task_id") or kwargs.get("task_uuid")
            if not task_id:
                # Fallback: check if the model passed title or description instead of task_id
                title_alt = kwargs.get("title") or kwargs.get("title_filter") or kwargs.get("task_title") or kwargs.get("description")
                if title_alt:
                    task_id = str(title_alt).strip()
                    logger.info(f"ProjectManagementTool: Auto-resolved missing task_id to title/description '{task_id}'.")
                elif agent_id and self._is_worker_agent(agent_id):
                    # Fallback: if worker has exactly one pending assigned task, auto-infer it
                    try:
                        worker_pending = list(tw.tasks.pending().filter(assignee=agent_id))
                        if len(worker_pending) == 1:
                            task_id = worker_pending[0]['uuid']
                            logger.info(f"ProjectManagementTool: Auto-inferred task_id '{task_id}' for worker '{agent_id}' with exactly one pending task.")
                    except Exception as infer_err:
                        logger.debug(f"Could not auto-infer worker task: {infer_err}")

            if task_id is None or task_id == "":
                return {"status": "error", "message": "Missing 'task_id' for 'modify_task'. Provide the task's UUID, integer ID, alias, or description."}
            
            t_id_str = str(task_id).strip()
            t_id_str = re.sub(r"^[<'\"]+|[>'\"]+$", "", t_id_str).strip()
            
            if t_id_str in aliases:
                t_id_str = aliases[t_id_str]
                
            if t_id_str == "0":
                return {"status": "error", "message": "Task ID '0' indicates a completed or deleted task. You must use the task's 'uuid' to modify it, or realize it is already completed."}

            if "field" in kwargs and "value" in kwargs:
                field = str(kwargs["field"]).lower().strip()
                value = kwargs["value"]
                if field in ["status", "task_progress", "description", "priority", "tags", "depends"]:
                    kwargs[field] = value
                elif field in ["assignee", "assignee_agent_id"]:
                    kwargs["assignee_agent_id"] = value

            # Auto-extract from hallucinated 'fields' dict or string
            fields_arg = kwargs.get("fields")
            if fields_arg and isinstance(fields_arg, dict):
                for fk, fv in fields_arg.items():
                    fk_lower = str(fk).lower().strip()
                    if fk_lower and fv and (fk_lower not in kwargs or kwargs.get(fk_lower) in (None, '')):
                        kwargs[fk_lower] = fv
                logger.info(f"ProjectManagementTool: Auto-extracted fields from 'fields' dict: {list(fields_arg.keys())}")
            elif fields_arg and isinstance(fields_arg, str) and fields_arg.strip():
                # Try to parse "task_progress=finished" or "status:done" style
                for sep in ['=', ':']:
                    if sep in fields_arg:
                        parts = fields_arg.split(sep, 1)
                        if len(parts) == 2:
                            fk, fv = parts[0].strip().lower(), parts[1].strip()
                            if fk and fv and (fk not in kwargs or kwargs.get(fk) in (None, '')):
                                kwargs[fk] = fv
                                logger.info(f"ProjectManagementTool: Auto-extracted field '{fk}'='{fv}' from 'fields' string")
                            break

            blank_fields = drop_blank_fields(kwargs)  # Round 2r: '' means "not given", never "make it empty"
            if blank_fields:
                logger.debug(f"ProjectManagementTool: modify_task ignored empty value(s) of {blank_fields}.")

            try:
                if '-' in t_id_str and len(t_id_str) > 10:
                    try:
                        if not UUID_RE.match(t_id_str.lower()):
                            # Round 2v: never hand a malformed UUID to Taskwarrior ("Cannot subtract strings")
                            raise LookupError(f"'{t_id_str}' is not a complete UUID; matching task does not exist")
                        task = tw.tasks.get(uuid=t_id_str)
                    except Exception as get_err:
                        if "exist" in str(get_err):
                            matches = match_task_by_uuid_prefix(tw.tasks.all(), t_id_str)
                            if len(matches) == 1:
                                task = matches[0]
                            else:
                                raise get_err
                        else:
                            raise get_err
                elif t_id_str.isdigit():
                    task = tw.tasks.get(id=int(t_id_str))
                else:
                    matching = tw.tasks.filter(description=t_id_str)
                    if len(matching) != 1:
                        matching = tw.tasks.filter(description=t_id_str.replace('_', ' '))
                    
                    if len(matching) == 1:
                        task = matching[0]
                    else:
                        # Fuzzy matching fallback
                        search_str = t_id_str.lower().replace('_', ' ')
                        all_tasks_to_search = tw.tasks.pending()
                        fuzzy_matches = []
                        for t in all_tasks_to_search:
                            desc = (t['description'] or '').lower()
                            if search_str in desc:
                                fuzzy_matches.append(t)
                        
                        if len(fuzzy_matches) == 1:
                            task = fuzzy_matches[0]
                            logger.info(f"ProjectManagementTool: Fuzzy matched '{t_id_str}' to UUID '{task['uuid']}'")
                        elif len(fuzzy_matches) > 1:
                            raise ValueError(f"Ambiguous task id/name '{t_id_str}' matched {len(fuzzy_matches)} tasks.")
                        else:
                            raise ValueError("Task not found by description or ID.")
            except Exception as e:
                # Provide an actionable error message suggesting they list the tasks
                listing = open_tasks_listing(tw)  # Round 2h
                return {
                    "status": "error", 
                    "message": f"Task '{task_id}' not found. Note that integer IDs shift as tasks are completed, and UUIDs must be exact. Detail: {e}" + (f"\n{listing}" if listing else ""),
                    "suggestion": "IMPORTANT: You should run the project_management tool with action='list_tasks' to get the current list of tasks and their valid IDs/UUIDs."
                }



            # Validate task_progress or legacy status if provided
            previous_progress = str(task['task_progress'] or 'todo')  # Round 2x: kept if a blocked task may not start
            requested_start = False
            if "task_progress" in kwargs or "status" in kwargs:
                raw_prog = kwargs.get("task_progress") or kwargs.get("status")
                t_prog, t_status = self._map_task_progress(raw_prog)
                requested_start = t_prog in STARTED_PROGRESS and previous_progress not in STARTED_PROGRESS
                if t_prog == "finished":
                    # Round 2a: PM1 marked tasks finished whose files held only docstrings (2026-10-18 15:30, 15:50)
                    refusal = self._completion_refusal(task, project_name, session_name, task_id)
                    if refusal:
                        return refusal
                task["task_progress"] = t_prog
                task["status"] = t_status
                modified_fields = ["task_progress", "status"]
            else:
                modified_fields = []

            if "description" in kwargs: 
                task['description'] = " ".join(str(kwargs["description"]).splitlines()).strip(); modified_fields.append("description")
            if "priority" in kwargs: 
                prio = normalize_priority(kwargs["priority"])
                if prio is None: return {"status": "error", "message": f"Invalid priority '{kwargs['priority']}'. {INVALID_PRIORITY_HINT}"}
                task['priority'] = prio; modified_fields.append("priority")
            if "tags" in kwargs: 
                tags_arg = kwargs["tags"]
                if isinstance(tags_arg, str):
                    raw_tags = [tag.strip() for tag in tags_arg.split(',') if tag.strip()]
                elif isinstance(tags_arg, list):
                    raw_tags = [str(t).strip() for t in tags_arg if t]
                else:
                    raw_tags = [str(tags_arg).strip()]
                current_tags = task['tags'] if task['tags'] else set()
                if isinstance(current_tags, list):
                    current_tags = set(current_tags)
                elif isinstance(current_tags, set):
                    current_tags = current_tags.copy()
                    
                for t in raw_tags:
                    t = t.strip()
                    is_remove = t.startswith('-')
                    t_clean = clean_tag(t.lstrip('+-'))
                    if not t_clean:
                        continue
                        
                    if is_remove:
                        if t_clean in current_tags:
                            current_tags.remove(t_clean)
                    else:
                        current_tags.add(t_clean)
                task['tags'] = set(current_tags)
                modified_fields.append("tags")
            
            assignee_arg = kwargs.get("assignee_agent_id") or kwargs.get("assignee")
            if assignee_arg: 
                new_assignee = clean_agent_id(assignee_arg)
                if not new_assignee:
                    return {"status": "error", "message": f"Invalid assignee_agent_id {assignee_arg!r}. Use the plain agent id such as 'W1' (letters, digits, '_', '.', ':' or '-'; no spaces or line breaks)."}
                if task['assignee'] == new_assignee:
                    # Silently accept redundant assignment to prevent LLM retry loops
                    modified_fields.append("assignee")
                elif task['status'] == 'pending' and task['assignee'] and task['assignee'] != agent_id and not agent_id.startswith("admin") and not agent_id.startswith("PM"):
                    return {"status": "error", "message": f"Task '{task_id}' is already 'pending' and actively assigned to '{task['assignee']}'. Reassigning active tasks owned by others is blocked to prevent disruption. Only the assignee can hand it off, or its status must first be changed to 'waiting'."}
                elif (refusal := non_worker_assignment_error(new_assignee, agent_id, kwargs.get("manager"), f"Task '{task_id}'")):
                    return refusal  # Round 2t: only workers carry out tasks
                elif (refusal := busy_worker_assignment_error(tw, new_assignee, task, agent_id, kwargs.get("manager"), f"Task '{task_id}'")):
                    return refusal  # Round 3c: one ready task at a time per worker
                else:
                    previous_assignee = str(task['assignee'] or "").strip()
                    task['assignee'] = new_assignee
                    modified_fields.append("assignee")
                    
                    current_tags = task['tags'] if task['tags'] else set()
                    if isinstance(current_tags, list):
                        current_tags = set(current_tags)
                    elif isinstance(current_tags, set):
                        current_tags = current_tags.copy()
                    # Round 3c: the previous assignee's tag goes (03:40 Kick-off Task 3 of W1 still carried the tag 'W2')
                    previous_tag = clean_tag(previous_assignee) if previous_assignee else None
                    if previous_tag and previous_tag != new_assignee:
                        current_tags.discard(previous_tag)
                        
                    current_tags.add("assigned")
                    current_tags.add(new_assignee)
                    task['tags'] = set(current_tags)
                    if "tags" not in modified_fields:
                        modified_fields.append("tags")
            if "depends" in kwargs and str(kwargs["depends"] or "").strip() in ("", "[]", "None", "null"):
                kwargs.pop("depends")  # Round 2f: 'depends': '' (PM1 2026-10-19 10:14) means no dependency, not task ''
            if "depends" in kwargs:
                # Round 2w: several dependencies separated by commas (or a list), as add_task accepts them
                dep_ids = [d.strip() for d in str(kwargs["depends"]).replace('[', '').replace(']', '').replace('"', '').replace("'", "").split(',') if d.strip()]
                for dep_id in dep_ids:
                    try:
                        dep_task = resolve_task_reference(tw, aliases, dep_id)
                        if task['depends'] is None:
                            task['depends'] = set()
                        task['depends'].add(dep_task)
                    except Exception as e:
                        return {"status": "error", "message": f"Dependency task '{dep_id}' not found, so nothing was changed. Integer IDs shift when tasks are completed. Use UUID or custom alias instead. Detail: {taskwarrior_error_summary(e)}"}
                if dep_ids:
                    modified_fields.append("depends")

            blocked_note = ""
            if requested_start:
                # Round 2x: a task that waits for unfinished dependencies is not 'doing'
                blocked_note = blocked_start_note(task, f"Task '{task_id}'")
                if blocked_note:
                    task["task_progress"] = previous_progress if previous_progress not in STARTED_PROGRESS else "todo"
                    task["status"] = "pending"
                    modified_fields = [f for f in modified_fields if f not in ("task_progress", "status")]
                    logger.info(f"ProjectManagementTool: modify_task '{task_id}': task_progress not set to started - "
                                "unfinished dependencies.")
                    if not modified_fields:
                        return {"status": "error", "message": f"Task '{task_id}' was NOT changed." + blocked_note}

            if not modified_fields:
                return {"status": "error", "message": (
                    "No valid fields provided for modification. "
                    "You must provide at least one of these as a DIRECT parameter (not inside a 'fields' wrapper): "
                    "task_progress, status, description, priority, tags, depends, assignee_agent_id. "
                    "CORRECT example: {\"action\": \"modify_task\", \"task_id\": \"UUID\", \"task_progress\": \"finished\"}"
                    + (f" Empty values ('') are ignored; they were sent for: {', '.join(blank_fields)}." if blank_fields else "")
                    + self._unknown_parameters_note(kwargs)  # Round 2w
                )}

            is_dry_run = str(kwargs.get("dry_run")).lower() == "true"
            if not is_dry_run:
                try:
                    task.save()
                except Exception as save_err:  # Round 2r: say what was refused, without the internal command line
                    summary = taskwarrior_error_summary(save_err)
                    logger.warning(f"ProjectManagementTool: Taskwarrior refused modify_task of '{task_id}' ({modified_fields}): {summary}")
                    return {"status": "error", "message": (
                        f"Task '{task_id}' was NOT changed: Taskwarrior refused the change of "
                        f"{', '.join(modified_fields)}: {summary.rstrip('.')}. Send only the fields you want to change.")}
                asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": project_name, "session_name": session_name})))
            else:
                logger.info("ProjectManagementTool: 'modify_task' dry_run succeeded. Database not modified.")
                
            assignee_to_return = task['assignee'] or kwargs.get("assignee_agent_id")
            depends_list = [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
            return {
                "status": "success", 
                "message": f"Task '{task_id}' modified successfully{'. (DRY RUN - No changes saved)' if is_dry_run else '.'}" + blocked_note, 
                "task_uuid": task['uuid'], 
                "task_id": task['id'], 
                "modified_fields": modified_fields, 
                "task_progress": task['task_progress'], 
                "description": task['description'], 
                "assignee": assignee_to_return, 
                "depends": depends_list,
                "dry_run": is_dry_run
            }

    async def _execute_complete_task(self, tw, aliases, project_name, session_name, kwargs):
            task_id = kwargs.get("task_id") or kwargs.get("task_uuid")
            if not task_id:
                title_alt = kwargs.get("title") or kwargs.get("title_filter") or kwargs.get("task_title") or kwargs.get("description")
                if title_alt:
                    task_id = str(title_alt).strip()
                    logger.info(f"ProjectManagementTool: Auto-resolved complete_task task_id to title/description '{task_id}'.")
            if task_id is None or task_id == "":
                return {"status": "error", "message": "Missing 'task_id' for 'complete_task'."}
            
            t_id_str = str(task_id).strip()
            t_id_str = re.sub(r"^[<'\"]+|[>'\"]+$", "", t_id_str).strip()
            
            if t_id_str in aliases:
                t_id_str = aliases[t_id_str]
                
            if t_id_str == "0":
                return {"status": "error", "message": "Task ID '0' indicates a completed or deleted task. You must use the task's 'uuid' to modify it, or realize it is already completed."}

            try:
                if '-' in t_id_str and len(t_id_str) > 10:
                    try:
                        if not UUID_RE.match(t_id_str.lower()):
                            # Round 2v: never hand a malformed UUID to Taskwarrior ("Cannot subtract strings")
                            raise LookupError(f"'{t_id_str}' is not a complete UUID; matching task does not exist")
                        task = tw.tasks.get(uuid=t_id_str)
                    except Exception as get_err:
                        if "exist" in str(get_err):
                            matches = match_task_by_uuid_prefix(tw.tasks.all(), t_id_str)
                            if len(matches) == 1:
                                task = matches[0]
                            else:
                                raise get_err
                        else:
                            raise get_err
                elif t_id_str.isdigit():
                    task = tw.tasks.get(id=int(t_id_str))
                else:
                    matching = tw.tasks.filter(description=t_id_str)
                    if len(matching) != 1:
                        matching = tw.tasks.filter(description=t_id_str.replace('_', ' '))
                    
                    if len(matching) == 1:
                        task = matching[0]
                    else:
                        # Fuzzy matching fallback
                        search_str = t_id_str.lower().replace('_', ' ')
                        all_tasks_to_search = tw.tasks.all()
                        fuzzy_matches = []
                        for t in all_tasks_to_search:
                            desc = (t['description'] or '').lower()
                            if search_str in desc:
                                fuzzy_matches.append(t)
                        
                        if len(fuzzy_matches) == 1:
                            task = fuzzy_matches[0]
                            logger.info(f"ProjectManagementTool: Fuzzy matched '{t_id_str}' to UUID '{task['uuid']}'")
                        elif len(fuzzy_matches) > 1:
                            raise ValueError(f"Ambiguous task id/name '{t_id_str}' matched {len(fuzzy_matches)} tasks.")
                        else:
                            raise ValueError("Task not found by description or ID.")
            except Exception as e:
                # Provide an actionable error message suggesting they list the tasks
                listing = open_tasks_listing(tw)  # Round 2h
                return {
                    "status": "error", 
                    "message": f"Task '{task_id}' not found. Note that integer IDs shift as tasks are completed, and UUIDs must be exact. Detail: {e}" + (f"\n{listing}" if listing else ""),
                    "suggestion": "IMPORTANT: You should run the project_management tool with action='list_tasks' to get the current list of tasks and their valid IDs/UUIDs."
                }



            refusal = self._completion_refusal(task, project_name, session_name, task_id)  # Round 2a
            if refusal:
                return refusal

            try:
                is_dry_run = str(kwargs.get("dry_run")).lower() == "true"
                if not is_dry_run:
                    task.done() # This triggers Taskwarrior to set status=completed
                    task['task_progress'] = "finished"
                    task.save()
                    asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": project_name, "session_name": session_name})))
                    

                else:
                    logger.info("ProjectManagementTool: 'complete_task' dry_run succeeded. Database not modified.")
            except Exception as e:
                if "completed" in str(e).lower() or "Cannot complete a completed task" in str(e):
                    return {"status": "success", "message": f"Task '{task_id}' is already completed ('finished').", "task_uuid": task['uuid'], "task_id": task['id']}
                raise e
                
            return {
                "status": "success", 
                "message": f"Task '{task_id}' marked as finished{'. (DRY RUN)' if is_dry_run else '.'}", 
                "task_uuid": task['uuid'], 
                "task_id": task['id'],
                "dry_run": str(kwargs.get("dry_run")).lower() == "true"
            }

    async def _execute_get_dependency_graph(self, tw, kwargs):
        try:
            tasks_query = tw.tasks.pending()
            if kwargs.get("project_filter"): 
                tasks_query = tasks_query.filter(project=kwargs["project_filter"])
            
            tasks = tasks_query.all()
            
            if not tasks:
                return {"status": "success", "message": "No pending tasks found to graph.", "graph": "graph TD;\n  Empty[No pending tasks]"}

            # Build Mermaid Graph
            mermaid_lines = ["graph TD;"]
            
            # Create nodes
            for t in tasks:
                uuid_short = str(t['uuid'])[:8]
                desc = str(t['description'] or "Unnamed Task").replace('"', "'")
                assignee = t['assignee'] or "Unassigned"
                
                # Check for id, old tasks might not have it loaded in memory depending on tasklib 
                t_id = "?"
                try: t_id = t['id']
                except Exception: pass
                
                mermaid_lines.append(f'  {uuid_short}["[{t_id}] {desc} ({assignee})"];')

            # Create edges
            for t in tasks:
                t_uuid = str(t['uuid'])[:8]
                if t['depends']:
                    for dep in t['depends']:
                        try:
                            if isinstance(dep, str):
                                dep_uuid = dep[:8]
                            else:
                                dep_uuid = str(dep['uuid'])[:8]
                            mermaid_lines.append(f"  {dep_uuid} --> {t_uuid};")
                        except Exception as dep_err:
                            logger.warning(f"Error parsing dependency for graph: {dep_err}")

            graph_output = "\n".join(mermaid_lines)
            
            return {
                "status": "success",
                "message": "Dependency graph generated successfully.",
                "graph": graph_output
            }
        except Exception as e:
            logger.error(f"Error generating dependency graph: {e}")
            return {"status": "error", "message": f"Failed to generate dependency graph: {e}"}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the ProjectManagementTool."""
        project_name_placeholder = agent_context.get('project_name', '{project_name}') if agent_context else '{project_name}'

        common_header = f"""**Tool Name:** project_management

**Description:** Manages project tasks using a Taskwarrior backend. This tool is available to PMs (for general project management) and Workers (to decompose tasks into sub-tasks).

**Task Assignment:**
Assign a task to a WORKER with `modify_task`, `task_id` (the task's UUID) and `assignee_agent_id` (the worker's ID, e.g. `W1`).
The framework adds the worker's tag and the `assigned` tag itself and starts the worker. Only workers can be assignees.
Example: `{{"action": "modify_task", "task_id": "UUID", "assignee_agent_id": "W1"}}`

**Valid actions:** add_task, list_tasks, modify_task, complete_task, get_dependency_graph.
"""

        if sub_action == "add_task":
            return common_header + f"""
**Action: add_task**
Creates a new task.
*   `<description>` (string, required): A clear and concise description of the task.
*   `<project_filter>` (string, optional): The project name to associate the task with. Defaults to current project.
*   `<task_id>` (string, optional): An alias for the new task (e.g. `task_1`) that later calls can use instead of the UUID.
*   `<task_progress>` (string, optional): Initial progress state: 'todo' (default), 'doing', 'in_progress', 'waiting', 'stuck', 'finished'. A task with unfinished dependencies starts as 'todo'.
*   `<priority>` (string, optional): 'H' (high), 'M' (medium) or 'L' (low).
*   `<tags>` (list or comma-separated string, optional): Tags to add (e.g., `["bug", "urgent"]`).
*   `<assignee_agent_id>` (string, optional): The ID of the WORKER to assign this task to (e.g. `W1`). Only workers carry out tasks: a Project Manager, the Admin AI or the Guardian is refused and the task is NOT created.
*   `<depends>` (string, optional): UUID(s) or alias(es) of the task(s) this new task depends on, separated by commas.
*   Example:
    ```json
    {{
      "action": "add_task",
      "description": "Implement user authentication API endpoint",
      "priority": "H",
      "assignee_agent_id": "W1"
    }}
    ```
"""
        elif sub_action == "list_tasks":
            return common_header + f"""
**Action: list_tasks**
Lists existing tasks. Note that we use a granular 'task_progress' system to track exact task states.
*   `<project_filter>` (string, optional): Filter tasks by a specific project name.
*   `<task_progress_filter>` (string, optional): Filter by exact progress (e.g., 'todo', 'in_progress', 'stuck', 'finished').
*   `<assignee_filter>` (string, optional): Filter by assigned agent ID. To find unassigned tasks awaiting delegation, set this exact value to 'unassigned'.
*   `<tags_filter>` (string, optional): Filter by a comma-separated list of tags. You can also use the magic tags 'assigned' or 'unassigned' here in combination with exclude/include modes.
*   `<tags_filter_mode>` (string, optional): 'include' (default: only tasks WITH those tags) or 'exclude' (only tasks WITHOUT them).
*   Example:
    ```json
    {{
      "action": "list_tasks",
      "project_filter": "{project_name_placeholder}",
      "assignee_filter": "unassigned",
      "task_progress_filter": "todo"
    }}
    ```
"""
        elif sub_action == "modify_task":
            return common_header + f"""
**Action: modify_task**
Modifies an existing task.
*   `<task_id>` (string, required): The UUID (exactly as listed), alias or integer ID of the task to modify.
*   `<description>` (string, optional): New description for the task.
*   `<task_progress>` (string, optional): Update task progress: 'todo', 'doing', 'in_progress', 'waiting', 'stuck', 'decomposed', 'finished' or 'deleted'. (This also updates the underlying system status; 'finished' is refused while the task's files have problems; 'doing'/'in_progress' is not set while the task depends on unfinished tasks - its assignee starts it automatically once they are finished.)
*   `<priority>` (string, optional): New priority: 'H', 'M' or 'L'.
*   `<tags>` (string, optional): New comma-separated list of tags. Use `+tag` to add and `-tag` to remove.
*   `<assignee_agent_id>` (string, optional): Assign or reassign the task to a WORKER (e.g. `W1`); a Project Manager, the Admin AI or the Guardian is refused and nothing is changed. The worker's tag is added automatically.
*   `<depends>` (string, optional): Add dependencies: the UUID(s), alias(es) or ID(s) of the task(s) it depends on, separated by commas.
*   Send ONLY the fields you change. An empty value ('') means "not given" and is ignored.
*   Example (assign a task):
    ```json
    {{
      "action": "modify_task",
      "task_id": "123e4567-e89b-12d3-a456-426614174000",
      "assignee_agent_id": "W1"
    }}
    ```
*   Example (report progress):
    ```json
    {{
      "action": "modify_task",
      "task_id": "123e4567-e89b-12d3-a456-426614174000",
      "task_progress": "finished"
    }}
    ```
"""
        elif sub_action == "complete_task":
            return common_header + """
**Action: complete_task**
Marks a task as completely finished. Shortcut for `modify_task` with `task_progress='finished'`.
*   `<task_id>` (string, required): The UUID or integer ID of the task to complete.
*   Example:
    ```json
    {
      "action": "complete_task",
      "task_id": "1"
    }
    ```
"""
        elif sub_action == "get_dependency_graph":
            return common_header + """
**Action: get_dependency_graph**
Generates a Mermaid.js diagram illustrating the dependencies between all pending tasks.
*   Example:
    ```json
    {
      "action": "get_dependency_graph"
    }
    ```
"""

        return common_header + """
**Available Actions Summary:**
1.  **add_task:** Creates a new task.
2.  **list_tasks:** Lists existing tasks.
3.  **modify_task:** Modifies an existing task.
4.  **complete_task:** Marks a task as completed.
5.  **get_dependency_graph:** Returns a visual graph of task blocking relationships.

**To get detailed instructions and parameter lists for a specific action, call the 'tool_information' tool:**
    ```json
    {
      "action": "get_info",
      "tool_name": "project_management",
      "sub_action": "ACTION_NAME"
    }
    ```
"""
