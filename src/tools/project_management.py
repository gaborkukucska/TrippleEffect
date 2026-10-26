# START OF FILE src/tools/project_management.py
import logging
from pathlib import Path, PurePosixPath
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


def admin_assignment_error(assignee: str, caller_id: Optional[str], manager: Any, task_ref: str,
                           outcome: str = "was NOT changed") -> Optional[Dict[str, Any]]:
    """
    Round 3i: the refusal for the ADMIN AI giving a task to a worker of a team that has a PM, or None. Never raises.
    2026-10-24 13:15:53 (WiFi_Snake_Game) Admin AI answered PM1's "kick-off tasks assigned" report by giving SEVEN tasks to
    W2 in one reply - W2 was busy, the busy-worker check covers only PMs - and W2 then wrote src/main.py, the file of the task
    PM1 gave W1 seven minutes later.
    """
    try:
        agents = getattr(manager, "agents", None)
        caller = agents.get(caller_id) if isinstance(agents, dict) and caller_id else None
        from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM
        if caller is None or getattr(caller, "agent_type", None) != AGENT_TYPE_ADMIN:
            return None
        state_manager = getattr(manager, "state_manager", None)
        team_id = state_manager.get_agent_team(assignee) if state_manager is not None else None
        if not team_id:
            return None
        pms = [getattr(a, "agent_id", None) for a in (state_manager.get_agents_in_team(team_id) or [])
               if getattr(a, "agent_type", None) == AGENT_TYPE_PM]
        pms = [pm for pm in pms if pm]
        if not pms:
            return None
        logger.warning(f"ProjectManagementTool: Admin AI '{caller_id}' tried to give {task_ref} to '{assignee}' of the team "
                       f"of {pms[0]}. Refused.")
        return {"status": "error", "error_type": "admin_assignment", "message": (
            f"{task_ref} {outcome}: '{assignee}' works in the team of {pms[0]}, and giving tasks to its workers is "
            f"{pms[0]}'s job - {pms[0]} knows which worker is free and what each one is doing. If something should change, "
            f"tell {pms[0]} with send_message (which task, and why).")}
    except Exception as err:
        logger.debug(f"admin_assignment_error failed: {err}")
        return None


async def notify_previous_assignee(manager: Any, caller_id: Optional[str], previous: str, new: str, task: Any) -> bool:
    """
    Round 3i: tells the worker that LOST a task (it was given to another worker) to stop working on it, and clears the
    task from that worker's current task. Returns True if a message was sent. Never raises.
    2026-10-24 13:22:48 PM1 moved "Kick-off Task 4: Integrate all components in src/main.py" from W2 to W1 without a word;
    W2 wrote src/main.py at 13:24:08, and W1's own write of src/main.py was then refused ("already exists").
    """
    try:
        agents = getattr(manager, "agents", None)
        if not isinstance(agents, dict) or caller_id not in agents or previous not in agents or previous == caller_id:
            return False
        from src.agents.constants import AGENT_TYPE_WORKER
        worker = agents[previous]
        if getattr(worker, "agent_type", None) != AGENT_TYPE_WORKER:
            return False
        task_uuid = str(get_task_field(task, "uuid", "") or "")
        for attr in ("current_task_id", "active_task_id"):
            if str(getattr(worker, attr, "") or "") == task_uuid and task_uuid:
                setattr(worker, attr, None)
        description = " ".join(str(get_task_field(task, "description", "") or "").split())
        if len(description) > 120:
            description = description[:117] + "..."
        handler = getattr(manager, "interaction_handler", None)
        if handler is None:
            return False
        await handler.route_and_activate_agent_message(
            sender_id=caller_id, target_identifier=previous,
            message_content=(f"[Framework notice] The task '{description}' ({task_uuid}) was given to {new}. It is no longer "
                             f"YOUR task: stop working on it and do not change its files - {new} does that now. Continue "
                             "with your own task, or report to your PM if you have none."))
        logger.info(f"ProjectManagementTool: told '{previous}' that task {task_uuid} now belongs to '{new}'.")
        return True
    except Exception as err:
        logger.debug(f"notify_previous_assignee failed: {err}")
        return False


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
        # Round 3d: also without the new task's title, and against a finished task's description without ITS title.
        # 2026-10-23 12:17 PM1 re-created Kick-off Task 1 as "Backend server setup with Socket.io: Implement Express server
        # ..." (0.79 with the title, 0.86 without) and Task 2 as "Frontend game UI implementation: Build HTML/CSS/..." (0.79/0.89).
        wanted_variants = [wanted]
        description_only = task_core_text(None, description).lower()
        if len(description_only) >= 12 and description_only not in wanted_variants:
            wanted_variants.append(description_only)
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
            done_variants = [done]
            head, separator, tail = done.partition(": ")
            if separator and len(head) <= 60 and len(tail.strip()) >= 12 and "." not in head:
                done_variants.append(tail.strip())   # 'Title: description' -> 'description'
            ratio = max(difflib.SequenceMatcher(None, w, d).ratio() for w in wanted_variants for d in done_variants)
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


def open_duplicate_error(tw: Any, agent_id: Optional[str], manager: Any, title: Any, description: Any,
                         project: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Round 3g: the refusal for a PM's new task that repeats an OPEN task a worker already has (the same text, or the open
    task's whole text inside the new one), or None. Never raises.
    2026-10-24 07:02:50 (WiFi_Snake_Game) PM1 created "Fix failing tests for game.js module: " + the full text of W1's open
    task "Fix failing tests: 18 tests failed across game.js, network.js, and sync.js ..." and gave it to the new worker W4, so
    W1 and W4 edited src/game.js at the same time for the same failures. (The older check of pending tasks never ran: it
    tested `'description' in task`, which is always False for a tasklib task.)
    """
    try:
        if not agent_id or agent_id in FRAMEWORK_CALLERS or not _is_pm_caller(agent_id, manager):
            return None
        import difflib
        wanted_variants = [v for v in (task_core_text(title, description).lower(),
                                       task_core_text(None, description).lower()) if len(v) >= 12]
        if not wanted_variants:
            return None
        pending = tw.tasks.pending()
        if project:
            pending = pending.filter(project=project)
        for task in pending:
            assignee = str(get_task_field(task, "assignee", "") or "").strip()
            if not assignee or assignee == agent_id or "project_kickoff" in _task_tags(task):
                continue
            if str(get_task_field(task, "task_progress", "todo")).strip().lower() in _OPEN_EXCLUDED_PROGRESS:
                continue
            open_text = str(get_task_field(task, "description", "") or "")
            core = task_core_text(None, open_text).lower()
            if len(core) < 12:
                continue
            core_variants = [core]
            head, separator, tail = core.partition(": ")
            if separator and len(head) <= 60 and len(tail.strip()) >= 12 and "." not in head:
                core_variants.append(tail.strip())
            ratio = max(difflib.SequenceMatcher(None, w, c).ratio() for w in wanted_variants for c in core_variants)
            contained = any(len(c) >= 40 and c in w for w in wanted_variants for c in core_variants)
            if ratio < FINISHED_DUPLICATE_RATIO and not contained:
                continue
            uuid = get_task_field(task, "uuid", "?")
            progress = str(get_task_field(task, "task_progress", "todo") or "todo")
            logger.warning(f"ProjectManagementTool: new task '{str(description)[:80]}' repeats the OPEN task {uuid} of "
                           f"'{assignee}' ({ratio:.2f}, contained: {contained}). Refused.")
            return {"status": "error", "error_type": "open_duplicate", "message": (
                f"The task was NOT created: the same work is already OPEN in task {uuid} "
                f"('{' '.join(open_text.split())[:120]}'), assigned to {assignee} (task_progress: {progress}). Two workers "
                f"on the same work edit the same files against each other. Instead, send {assignee} what to do differently "
                "(send_message), or change that task (modify_task). Create a new task only for DIFFERENT work, naming its "
                "own files.")}
        return None
    except Exception as err:
        logger.debug(f"open_duplicate_error failed: {err}")
        return None


# --- Round 3g: improvement work nobody asked for ---------------------------------------------------------------------------
# 2026-10-23 22:37-23:47 (WiFi_Snake_Game) with one kick-off task still open, PM1 gave the idle W1 and W3 "Polish UI components
# and add animations", "Code review and optimization for game logic modules" (twice), "Review game.js/player.js/network.js
# for performance issues and best practices" and "Add CSS animations for player movement" - none of it in the plan, none of
# it about a problem. The prompts forbid inventing work for idle workers; the model did it anyway.
# Round 3m: + documentation beyond the plan. 2026-10-24 22:08 PM1 added "Kick-off Task 7: Create comprehensive documentation"
# (the plan's README.md was finished at 22:02) and 23:01 "Create User Guide and API Documentation" for the idle W2, who
# rewrote docs/API.md (13 KB) while W1's failing test was the only open work.
_IMPROVEMENT_RE = re.compile(
    r"\b(polish\w*|enhanc\w*|optimi[sz]\w*|refactor\w*|beautif\w*|animation\w*|code review|best practices|"
    r"performance (?:review|report|recommendation|issue)s?|clean[ -]?up|"
    r"improve (?:the )?(?:ui|ux|code|performance|look|design|quality|readability)|"
    r"(?:user|developer|player|installation|deployment) guides?|api (?:documentation|docs|reference)|tutorials?|"
    r"(?:comprehensive|additional|extended|detailed|more) (?:documentation|docs))\b", re.IGNORECASE)
_PROBLEM_RE = re.compile(
    r"\b(fix\w*|fail\w*|error\w*|bugs?|broken|crash\w*|missing|does not|doesn't|cannot|can't|not work\w*|"
    r"regression|issues? (?:found|reported)|slow\w*|lag\w*|freez\w*|hang\w*)\b", re.IGNORECASE)


def _improvement_stem(word: str) -> str:
    word = word.lower()
    for stem in ("polish", "enhanc", "optimi", "refactor", "beautif", "animation", "review", "best practice",
                 "performance", "clean", "improve", "guide", "api doc", "api ref", "tutorial"):  # Round 3m: + documentation
        if stem in word:
            return stem
    if word.endswith(("documentation", "docs")):
        return word.split()[0]  # 'comprehensive documentation' -> 'comprehensive'
    return word


def unplanned_improvement_error(tw: Any, agent_id: Optional[str], manager: Any, title: Any, description: Any,
                                project: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Round 3g: the refusal for a PM's new task of improvement work (polish, optimize, animations, code review, ...) that the
    project plan does not ask for and that names no problem, or None. The plan is every kick-off/update task of the project
    (an update task carries the user's feedback, so "add animations" asked for by the user is allowed). Never raises.
    """
    try:
        if not agent_id or agent_id in FRAMEWORK_CALLERS or not _is_pm_caller(agent_id, manager):
            return None
        text = " ".join(str(part) for part in (title, description) if part)
        match = _IMPROVEMENT_RE.search(text)
        if not match or _PROBLEM_RE.search(text):
            return None
        plan_parts = []
        for task in list(tw.tasks.pending()) + list(tw.tasks.completed()):
            if project and str(get_task_field(task, "project", "") or "") not in ("", project):
                continue
            if "project_kickoff" in _task_tags(task):
                plan_parts.append(str(get_task_field(task, "description", "") or ""))
        plan = " ".join(plan_parts).lower()
        if not plan:
            return None  # no plan to compare with
        stem = _improvement_stem(match.group(1))
        if stem in plan:
            return None
        logger.warning(f"ProjectManagementTool: '{agent_id}' tried to add unplanned improvement work "
                       f"('{match.group(1)}'): '{text[:80]}'. Refused.")
        return {"status": "error", "error_type": "unplanned_improvement", "message": (
            f"The task was NOT created: '{match.group(1)}' work is not part of the project plan and the task names no "
            "problem. An idle worker with nothing to do is NORMAL while the remaining tasks are finished - do not invent "
            "work for it (it changes working files and costs hours). Create a task only for planned work that is missing, "
            "or for a problem that was FOUND, naming the problem and the file(s) to fix (e.g. 'Fix: the score does not "
            "update in src/ui.js'). If nothing needs a decision, request 'pm_standby'.")}
    except Exception as err:
        logger.debug(f"unplanned_improvement_error failed: {err}")
        return None


FINISHED_PROGRESS = frozenset({"finished", "completed", "complete", "done", "closed"})


def task_is_finished(task: Any) -> bool:
    """Round 3d: True for a task that is completed (status) or finished (task_progress)."""
    status = str(get_task_field(task, "status", "") or "").strip().lower()
    progress = str(get_task_field(task, "task_progress", "") or "").strip().lower()
    return status == "completed" or progress in FINISHED_PROGRESS


def finished_task_change_error(task: Any, task_ref: Any, kwargs: Dict[str, Any], caller_id: Optional[str],
                               map_progress) -> Optional[Dict[str, Any]]:
    """
    Round 3d: the refusal of a modify_task that would give a FINISHED task to a worker again without reopening it, or
    rewrite its description into other work; None otherwise (also for framework calls). 2026-10-23 12:46:36 PM1 gave the
    finished Kick-off Task 1 (the Express server) to W2, which was activated on a finished task; at 13:11:24 it rewrote that
    task's description into "Create project documentation: ..." and activated W2 on it again - the record of the finished
    server work was gone.
    """
    try:
        if caller_id in FRAMEWORK_CALLERS or not task_is_finished(task):
            return None
        raw_progress = kwargs.get("task_progress") or kwargs.get("status")
        reopening = bool(raw_progress) and map_progress(raw_progress)[0] not in FINISHED_PROGRESS | {"deleted"}
        current = " ".join(str(get_task_field(task, "description", "") or "").split())
        new_description = kwargs.get("description")
        rewrites = (new_description is not None and str(new_description).strip()
                    and " ".join(str(new_description).split()) != current)
        assigns = bool(kwargs.get("assignee_agent_id") or kwargs.get("assignee"))
        if not rewrites and not (assigns and not reopening):
            return None
        shown = current if len(current) <= 100 else current[:97] + "..."
        what = "rewritten into other work" if rewrites else "given to a worker again"
        logger.warning(f"ProjectManagementTool: '{caller_id}' tried to change the FINISHED task {task_ref} ({what}). Refused.")
        return {"status": "error", "error_type": "finished_task", "message": (
            f"Task '{task_ref}' is FINISHED ('{shown}') and was NOT changed: a finished task is not {what}. For new or "
            "remaining work create a NEW task with action 'add_task' (a title and a description that names the problem and "
            "the file(s)), then assign that new task. To have exactly this work done again, reopen it: modify_task with "
            "task_progress='todo' and the assignee_agent_id.")}
    except Exception as err:
        logger.debug(f"finished_task_change_error failed: {err}")
        return None


_PLANNED_TASK_PREFIX_RE = re.compile(r"^\s*(?:Kick-off Task|Update \d+ Task)\s*\d+\s*:\s*", re.IGNORECASE)


def _is_planned_task(task: Any) -> bool:
    tags = _task_tags(task)
    description = str(get_task_field(task, "description", "") or "")
    return "kickoff" in tags or any(tag.startswith("update_") for tag in tags) or bool(
        _PLANNED_TASK_PREFIX_RE.match(description))


def planned_task_rewrite_error(task: Any, task_ref: Any, kwargs: Dict[str, Any], caller_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Round 3t: the refusal of a modify_task that REPLACES the text of an open planned task (a kick-off or update task of
    the approved plan) with other work, or None. Adding to the text (the planned text stays in it) is allowed; framework
    calls pass. 2026-10-26 13:16-14:51 (WiFi_Snake_Game) PM1 rewrote "Kick-off Task 7: Write pytest tests for game logic,
    player management, and leaderboard in tests/" four times - into "Fix src/game_logic.py to export GRID_WIDTH ...", "Fix
    test failure: Run `pytest ...`" and finally "Examine src/game_logic.py to verify GRID_WIDTH and GRID_HEIGHT are in
    __all__ ... No test runs required." - which W2 then marked finished: the planned tests task was gone, and the test-run
    check with it.
    """
    try:
        if caller_id in FRAMEWORK_CALLERS or not _is_planned_task(task):
            return None
        new_description = kwargs.get("description")
        if new_description is None or not str(new_description).strip():
            return None
        current = " ".join(str(get_task_field(task, "description", "") or "").split())
        new_text = " ".join(str(new_description).split())
        core = _PLANNED_TASK_PREFIX_RE.sub("", current).strip()
        if not core or new_text == current or core.lower() in new_text.lower():
            return None  # unchanged, or the planned text is kept and only added to
        shown = current if len(current) <= 100 else current[:97] + "..."
        logger.warning(f"ProjectManagementTool: '{caller_id}' tried to replace the text of the planned task {task_ref}. "
                       "Refused.")
        return {"status": "error", "error_type": "planned_task", "message": (
            f"Task '{task_ref}' is a planned task of the approved plan ('{shown}') and its text was NOT replaced: a new "
            "description would erase this planned work (and the checks that belong to it, such as the passing test run a "
            "test task needs). For other work - for example a fix in a file of another worker - create a NEW task with "
            "action 'add_task' (a description that names the problem and the file) and assign it to the worker whose task "
            "made that file. To give this task's worker more details, use send_message, or add them after the planned text "
            "(keep the planned text in the description).")}
    except Exception as err:
        logger.debug(f"planned_task_rewrite_error failed: {err}")
        return None


def file_owner_note(manager: Any, project_name: Optional[str], session_name: Optional[str], rel_path: Optional[str]) -> str:
    """
    Round 3t: for a Project Manager whose change of a project file was refused, who owns that file and what to do:
    ' src/leaderboard.py belongs to W4 (...): ...' - '' if no worker's task names it. Never raises. 2026-10-26 13:30-14:12
    PM1 tried to fix src/leaderboard.py and src/game_logic.py itself 9 times, then rewrote W2's tests task into a fix of
    W1's src/game_logic.py - which W2 was not allowed to change.
    """
    try:
        if not rel_path or not project_name or not session_name:
            return ""
        tw = ProjectManagementTool()._get_taskwarrior_instance(project_name, session_name)
        if tw is None:
            return ""
        rel = _clean_rel_path(rel_path)
        from src.utils.task_files import file_paths_in_text
        owners = []
        for task in _all_tasks(tw):
            assignee = str(get_task_field(task, "assignee", "") or "").strip()
            if (not assignee or non_worker_role(assignee, manager) or "project_kickoff" in _task_tags(task)
                    or str(get_task_field(task, "status", "") or "").strip().lower() == "deleted"):
                continue
            if _task_names_path(file_paths_in_text(str(get_task_field(task, "description", "") or "")), rel):
                owners.append(task)
        if not owners:
            return ""
        open_owners = [t for t in owners if not task_is_finished(t)]
        task = open_owners[0] if open_owners else owners[-1]
        owner = str(get_task_field(task, "assignee", "") or "").strip()
        label = f"'{_shown_text(get_task_field(task, 'description', ''), 70)}'"
        if open_owners:
            return (f" '{rel}' belongs to {owner}, whose open task {label} ({get_task_field(task, 'uuid', '?')}) names it: "
                    f"tell {owner} exactly what must change with send_message.")
        return (f" '{rel}' belongs to {owner} (its FINISHED task {label}): create a NEW task for the fix - action "
                f"'add_task' with a description that names '{rel}' and the change, and assignee_agent_id='{owner}'. Do not "
                "give the fix to a worker whose task does not name this file: it may not change it.")
    except Exception as err:
        logger.debug(f"file_owner_note failed: {err}")
        return ""


def _worker_parent_task(tw: Any, agent_id: str, manager: Any) -> Optional[Any]:
    """The task a worker is working on: its current/active task, else its only open task. None if unknown."""
    worker = getattr(manager, "agents", {}).get(agent_id) if isinstance(getattr(manager, "agents", None), dict) else None
    pending = list(tw.tasks.pending())
    for attr in ("current_task_id", "active_task_id"):
        wanted = str(getattr(worker, attr, "") or "").strip() if worker is not None else ""
        if wanted:
            for task in pending:
                if str(get_task_field(task, "uuid", "")) == wanted:
                    return task
    own = [t for t in pending if str(get_task_field(t, "assignee", "") or "").strip() == agent_id
           and str(get_task_field(t, "task_progress", "todo")).strip().lower() not in _OPEN_EXCLUDED_PROGRESS]
    return own[0] if len(own) == 1 else None


def foreign_files_subtask_error(tw: Any, agent_id: Optional[str], manager: Any, description: str) -> Optional[Dict[str, Any]]:
    """
    Round 3e: the refusal of a task a WORKER creates (a sub-task of its decomposition) that names a file which its own task
    does not name but another open task does - the work of that other task - or None. Never raises.
    2026-10-23 16:59:13 W1 decomposed "Kick-off Task 1: Set up Express server ... in src/server.js and src/routes/" into eight
    sub-tasks that created src/game.js (Task 5), src/routes/auth.js (Task 3), public/index.html (Task 6), tests/... (Task 7)
    and docs/README.md (Task 8), wrote all of them itself in 12 minutes while W2 waited, and the other kick-off tasks were then
    "finished" without being worked on.
    """
    try:
        if not agent_id or agent_id in FRAMEWORK_CALLERS or _is_pm_caller(agent_id, manager):
            return None
        worker = getattr(manager, "agents", {}).get(agent_id) if isinstance(getattr(manager, "agents", None), dict) else None
        from src.agents.constants import AGENT_TYPE_WORKER
        if worker is None or getattr(worker, "agent_type", None) != AGENT_TYPE_WORKER:
            return None
        from src.utils.task_files import file_paths_in_text
        new_files = file_paths_in_text(description)
        if not new_files:
            return None
        parent = _worker_parent_task(tw, agent_id, manager)
        if parent is None:
            return None
        parent_uuid = str(get_task_field(parent, "uuid", ""))
        parent_files = set(file_paths_in_text(str(get_task_field(parent, "description", "") or "")))
        for other in tw.tasks.pending():
            if str(get_task_field(other, "uuid", "")) == parent_uuid or "project_kickoff" in _task_tags(other):
                continue
            if str(get_task_field(other, "task_progress", "todo")).strip().lower() in _OPEN_EXCLUDED_PROGRESS:
                continue
            if str(get_task_field(other, "assignee", "") or "").strip() == agent_id:
                continue  # Round 3f: its own next task (2026-10-23 20:12:24 W2 was refused index.html of its own Task 4)
            other_text = str(get_task_field(other, "description", "") or "")
            taken = [f for f in new_files if f in set(file_paths_in_text(other_text)) and f not in parent_files]
            if not taken:
                continue
            other_shown = " ".join(other_text.split())
            other_shown = other_shown if len(other_shown) <= 90 else other_shown[:87] + "..."
            parent_shown = " ".join(str(get_task_field(parent, "description", "") or "").split())
            parent_shown = parent_shown if len(parent_shown) <= 90 else parent_shown[:87] + "..."
            logger.warning(f"ProjectManagementTool: '{agent_id}' tried to create a task for {taken} of task "
                           f"{get_task_field(other, 'uuid', '?')}. Refused.")
            return {"status": "error", "error_type": "foreign_files", "message": (
                f"The task was NOT created: {', '.join(taken)} belongs to another open task ('{other_shown}', "
                f"{get_task_field(other, 'uuid', '?')}), not to YOUR task ('{parent_shown}'). A sub-task covers only a part "
                "of your own task - leave the other tasks' files to the workers who get those tasks. Create sub-tasks only "
                "for the files and steps your task names, or work on your task directly (request worker_work).")}
        return None
    except Exception as err:
        logger.debug(f"foreign_files_subtask_error failed: {err}")
        return None


def _clean_rel_path(rel_path: Any) -> str:
    rel = str(rel_path).replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _task_names_path(names: List[str], rel_path: str) -> bool:
    """True if one of the paths a task names is rel_path, or is a bare file name equal to rel_path's name."""
    rel = _clean_rel_path(rel_path)
    base = rel.rsplit("/", 1)[-1]
    return any(name == rel or ("/" not in name and name == base) for name in names)


# Round 3g+: a test folder a task names ('Write pytest tests ... in tests/'). 2026-10-24 10:07:57 W1 (Kick-off Task 1,
# src/game.py) wrote tests/test_game/test_game.py although "Kick-off Task 7: Write pytest tests for game mechanics,
# networking, player management, and leaderboard in tests/" was W2's; W1's tests were wrong and W1 spent 1.5 hours bending
# src/game.py to them.
_TEST_DIR_CLAIM_RE = re.compile(r"(?<![\w./-])(tests?|__tests__|spec)/(?![\w.-])")
_TEST_WORD_RE = re.compile(r"\b(?:tests?|testing|pytest|jest|mocha|unit tests?)\b", re.IGNORECASE)


def _claims_test_file(task_text: str, rel_path: str) -> bool:
    """True if the task names a test folder ('in tests/') that holds rel_path, and rel_path is a test file."""
    from src.tools.project_links_check import is_test_file
    rel = _clean_rel_path(rel_path)
    if not is_test_file(rel):
        return False
    top = rel.split("/", 1)[0]
    return any(match.group(1) == top for match in _TEST_DIR_CLAIM_RE.finditer(task_text))


def foreign_file_creation_error(manager: Any, agent_id: Optional[str], project_name: Optional[str],
                                session_name: Optional[str], rel_path: Optional[str]) -> Optional[str]:
    """
    Round 3f: the refusal (text) for a WORKER that creates a NEW file which its own task does not name but an open task
    ASSIGNED TO ANOTHER WORKER does - that worker's work - or None. Never raises.
    2026-10-23 20:01:55 (WiFi_Snake_Multiplayer_Game) W1, working on "Kick-off Task 2: ... core game logic ... in script.js",
    wrote test/game.test.js, the file of "Kick-off Task 6: Write unit tests ... in test/game.test.js", assigned to W2 40 s
    earlier. W1 spent 10 minutes on that test file; W2 later inherited it and spent an hour on it.
    """
    try:
        if not agent_id or not rel_path or agent_id in FRAMEWORK_CALLERS or not project_name or not session_name:
            return None
        rel_path = _clean_rel_path(rel_path)
        agents = getattr(manager, "agents", None)
        worker = agents.get(agent_id) if isinstance(agents, dict) else None
        from src.agents.constants import AGENT_TYPE_WORKER
        if worker is None or getattr(worker, "agent_type", None) != AGENT_TYPE_WORKER:
            return None
        tw = ProjectManagementTool()._get_taskwarrior_instance(project_name, session_name)
        if tw is None:
            return None
        own = _worker_parent_task(tw, agent_id, manager)
        if own is None:
            return None
        from src.utils.task_files import file_paths_in_text
        own_text = str(get_task_field(own, "description", "") or "")
        if _task_names_path(file_paths_in_text(own_text), rel_path):
            return None
        own_mentions_tests = bool(_TEST_WORD_RE.search(own_text)) or _claims_test_file(own_text, rel_path)
        own_uuid = str(get_task_field(own, "uuid", ""))
        for other in tw.tasks.pending():
            if str(get_task_field(other, "uuid", "")) == own_uuid or "project_kickoff" in _task_tags(other):
                continue
            if str(get_task_field(other, "task_progress", "todo")).strip().lower() in _OPEN_EXCLUDED_PROGRESS:
                continue
            assignee = str(get_task_field(other, "assignee", "") or "").strip()
            if not assignee or assignee == agent_id:
                continue
            other_text = str(get_task_field(other, "description", "") or "")
            if not _task_names_path(file_paths_in_text(other_text), rel_path) and not (
                    not own_mentions_tests and _claims_test_file(other_text, rel_path)):  # Round 3g+: 'tests in tests/'
                continue
            def shown(text: str) -> str:
                text = " ".join(text.split())
                return text if len(text) <= 90 else text[:87] + "..."
            logger.warning(f"ProjectManagementTool: '{agent_id}' tried to create '{rel_path}', the file of task "
                           f"{get_task_field(other, 'uuid', '?')} assigned to '{assignee}'. Refused.")
            return (f"The file was NOT created: '{rel_path}' is the file of the task '{shown(other_text)}' "
                    f"({get_task_field(other, 'uuid', '?')}), which is assigned to {assignee}. Your task is "
                    f"'{shown(own_text)}' and does not name it. Leave other workers' files to them and finish the files of "
                    f"YOUR task. If your task cannot be done without '{rel_path}', tell your PM with send_message.")
        return None
    except Exception as err:
        logger.debug(f"foreign_file_creation_error failed: {err}")
        return None


# --- Round 3o: a worker does not change the files of ANOTHER worker's task ------------------------------------------------
# 2026-10-25 13:35-13:40 (Laptop_Snake_Multiplayer, nemotron-3.5-lightning): W2's task was "Kick-off Task 3: Write pytest tests
# ... in tests/test_snake.py". Its tests called GameState.spawn_food(position=...) (no such argument), used HTTPServer without
# importing it and expected a snake to reverse from UP to DOWN. Instead of fixing its tests, W2 edited src/snake_game.py - the
# file of W1's FINISHED Kick-off Task 1 - four times ('length: int = 1' -> 3 -> 1, the body set-up), and PM1 then asked W1 to
# change the code to match the tests.
def _all_tasks(tw: Any) -> List[Any]:
    """The pending and the completed tasks (finished tasks still own their files)."""
    tasks: List[Any] = []
    for getter in ("pending", "completed"):
        try:
            tasks.extend(list(getattr(tw.tasks, getter)()))
        except Exception:
            continue
    return tasks


def _shown_text(text: str, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _task_time(task: Any, field: str) -> Optional[float]:
    """A task's 'entry' / 'end' time as a timestamp, or None."""
    try:
        value = get_task_field(task, field)
        if value is None or value == "":
            return None
        if hasattr(value, "timestamp"):
            return float(value.timestamp())
        text = str(value).strip()
        return float(text) if re.fullmatch(r"\d+(?:\.\d+)?", text) else None
    except Exception:
        return None


def foreign_file_change_error(manager: Any, agent_id: Optional[str], project_name: Optional[str],
                              session_name: Optional[str], rel_path: Optional[str]) -> Optional[str]:
    """
    Round 3o: the refusal (text) for a WORKER that changes an EXISTING file named by a task (open or finished) assigned to
    ANOTHER worker, or None. Allowed: a task of its own names the file (any of its tasks, finished ones too); its own
    current task says to run / verify / fix the tests (src/utils/test_runs.is_test_run_task - such a task fixes whatever
    fails); or its current task was created AFTER the other task was finished (follow-up work such as "Fix the score bug" or
    an update task may change finished work - the kick-off tasks of one plan are created together, so a test task never
    counts as follow-up of the code it tests). PMs, the framework, unassigned and kick-off tasks own nothing here.
    Never raises.
    """
    try:
        if not agent_id or not rel_path or agent_id in FRAMEWORK_CALLERS or not project_name or not session_name:
            return None
        rel_path = _clean_rel_path(rel_path)
        agents = getattr(manager, "agents", None)
        worker = agents.get(agent_id) if isinstance(agents, dict) else None
        from src.agents.constants import AGENT_TYPE_WORKER
        if worker is None or getattr(worker, "agent_type", None) != AGENT_TYPE_WORKER:
            return None
        tw = ProjectManagementTool()._get_taskwarrior_instance(project_name, session_name)
        if tw is None:
            return None
        from src.utils.task_files import file_paths_in_text
        from src.utils.test_runs import is_test_run_task
        tasks = [t for t in _all_tasks(tw) if "project_kickoff" not in _task_tags(t)
                 and str(get_task_field(t, "status", "") or "").strip().lower() != "deleted"]
        own_tasks = [t for t in tasks if str(get_task_field(t, "assignee", "") or "").strip() == agent_id]
        for own_task in own_tasks:
            if _task_names_path(file_paths_in_text(str(get_task_field(own_task, "description", "") or "")), rel_path):
                return None
        current = _worker_parent_task(tw, agent_id, manager)
        open_own = [t for t in own_tasks if not task_is_finished(t)
                    and str(get_task_field(t, "task_progress", "todo")).strip().lower() not in _OPEN_EXCLUDED_PROGRESS]
        deciding = [current] if current is not None else open_own
        if any(is_test_run_task(str(get_task_field(t, "description", "") or "")) for t in deciding):
            return None  # 'Run the tests ... and fix any failures' may change any file
        for other in tasks:
            assignee = str(get_task_field(other, "assignee", "") or "").strip()
            if not assignee or assignee == agent_id or non_worker_role(assignee, manager):
                continue
            other_text = str(get_task_field(other, "description", "") or "")
            if not _task_names_path(file_paths_in_text(other_text), rel_path):
                continue
            if current is not None and task_is_finished(other):
                created, finished_at = _task_time(current, "entry"), _task_time(other, "end")
                if created is not None and finished_at is not None and created > finished_at:
                    continue  # follow-up work, planned after that task was done
            state = "FINISHED" if task_is_finished(other) else "open"
            own_shown = (f"Your task is '{_shown_text(get_task_field(current, 'description', ''))}' and does not name it."
                         if current is not None else "None of your tasks names it.")
            logger.warning(f"ProjectManagementTool: '{agent_id}' tried to change '{rel_path}', the file of the {state} task "
                           f"{get_task_field(other, 'uuid', '?')} of '{assignee}'. Refused.")
            return (f"'{rel_path}' was NOT changed: it is the file of the {state} task '{_shown_text(other_text)}' "
                    f"({get_task_field(other, 'uuid', '?')}) of {assignee}. {own_shown} Do not change another worker's "
                    f"files. If one of YOUR tests fails because of '{rel_path}', first check the TEST: does it call the code "
                    f"exactly the way '{rel_path}' defines it (names, arguments, return values, what is allowed)? Read "
                    f"'{rel_path}' and fix your test to match the real code. Only if the code really has a bug: tell your PM "
                    "with send_message (the test, what it expects, what the code does) - the PM gives the fix to "
                    f"{assignee}.")
        return None
    except Exception as err:
        logger.debug(f"foreign_file_change_error failed: {err}")
        return None


def blocked_worker_change_error(manager: Any, agent_id: Optional[str], project_name: Optional[str],
                                session_name: Optional[str], rel_path: Optional[str]) -> Optional[str]:
    """
    Round 3r: the refusal (text) for a WORKER that changes a file while EVERY open task it has waits for an unfinished task
    (its dependencies), or None - also when it has no open task (not judged here) or one it can work on. Never raises.
    2026-10-26 07:28:56 (WiFi_Snake_Game, qwen3.5:4b) W1 finished Kick-off Task 7; its other task, "Kick-off Task 8: Run the
    tests and start the server", waited for "Kick-off Task 6: Write pytest tests" (W2). W1 was told "It stays waiting; its
    assignee is started automatically", stayed in worker_work and changed src/snake_game.py 12 times until 07:51 to fit W2's
    half-written tests, while W2 changed those tests to fit W1's changing code. W2 then spent until 09:05 on tests that
    failed against the code W1 had bent out of shape (Snake.move kept only two segments).
    """
    try:
        if not agent_id or not rel_path or agent_id in FRAMEWORK_CALLERS or not project_name or not session_name:
            return None
        agents = getattr(manager, "agents", None)
        worker = agents.get(agent_id) if isinstance(agents, dict) else None
        from src.agents.constants import AGENT_TYPE_WORKER
        if worker is None or getattr(worker, "agent_type", None) != AGENT_TYPE_WORKER:
            return None
        tw = ProjectManagementTool()._get_taskwarrior_instance(project_name, session_name)
        if tw is None:
            return None
        from src.utils.task_utils import unfinished_dependencies
        own_open = [t for t in tw.tasks.pending()
                    if str(get_task_field(t, "assignee", "") or "").strip() == agent_id
                    and "project_kickoff" not in _task_tags(t)
                    and str(get_task_field(t, "task_progress", "todo") or "todo").strip().lower() not in _OPEN_EXCLUDED_PROGRESS]
        if not own_open:
            return None
        waiting = []
        for task in own_open:
            blocking = unfinished_dependencies(task)
            if not blocking:
                return None  # a task it can work on
            waiting.append((task, blocking))
        task, blocking = waiting[0]
        dep_uuid, dep_text = blocking[0]
        dep_assignee = ""
        for dep in (get_task_field(task, "depends") or []):
            if not isinstance(dep, (str, bytes)) and str(get_task_field(dep, "uuid", "")) == dep_uuid:
                dep_assignee = str(get_task_field(dep, "assignee", "") or "").strip()
        who = f", which {dep_assignee} is working on," if dep_assignee and dep_assignee != agent_id else ""
        logger.warning(f"ProjectManagementTool: '{agent_id}' tried to change '{_clean_rel_path(rel_path)}' while its task "
                       f"{get_task_field(task, 'uuid', '?')} waits for {dep_uuid}. Refused.")
        return (f"'{_clean_rel_path(rel_path)}' was NOT changed: you have NO task you can work on now. Your task "
                f"'{_shown_text(get_task_field(task, 'description', ''))}' ({get_task_field(task, 'uuid', '?')}) waits for "
                f"'{_shown_text(dep_text)}' ({dep_uuid}){who} to be finished - until then its files are still changing. Do "
                "not change files meanwhile: call the 'request_state' tool with state='worker_wait'. You are started "
                "automatically as soon as that task is finished.")
    except Exception as err:
        logger.debug(f"blocked_worker_change_error failed: {err}")
        return None


def project_pm_of(manager: Any, project_name: Optional[str]) -> Optional[str]:
    """Round 3s: the id of a Project Manager working on this project (its project_name_context; any PM if none has one)."""
    agents = getattr(manager, "agents", None)
    if not isinstance(agents, dict) or not project_name:
        return None
    from src.agents.constants import AGENT_TYPE_PM
    pms = [agent for agent in agents.values() if getattr(agent, "agent_type", None) == AGENT_TYPE_PM]
    for pm in pms:
        config = (getattr(pm, "agent_config", None) or {}).get("config", {}) if isinstance(
            getattr(pm, "agent_config", None), dict) else {}
        if config.get("project_name_context") == project_name:
            return str(getattr(pm, "agent_id", "") or "") or None
    if pms and not any(isinstance(getattr(pm, "agent_config", None), dict)
                       and (pm.agent_config.get("config") or {}).get("project_name_context") for pm in pms):
        return str(getattr(pms[0], "agent_id", "") or "") or None
    return None


def admin_project_change_error(manager: Any, agent_id: Optional[str], project_name: Optional[str],
                               rel_path: Optional[str]) -> Optional[str]:
    """
    Round 3s: the refusal (text) for the ADMIN AI changing a file in the shared workspace of a project that a Project
    Manager builds, or None. Never raises. 2026-10-26 09:33:18 (WiFi_Snake_Game) the Admin AI had handed the project to PM1
    at 09:28:38, then requested its 'work' state to "Initialize WiFi Snake Game project: ... create HTML/CSS/JS files" and
    wrote game.js and server.py into the team's workspace and edited index.html - beside src/core/snake_game.js and
    src/server/server.py of PM1's plan. The project kept both versions.
    """
    try:
        agents = getattr(manager, "agents", None)
        agent = agents.get(agent_id) if isinstance(agents, dict) and agent_id else None
        from src.agents.constants import AGENT_TYPE_ADMIN
        if agent is None or getattr(agent, "agent_type", None) != AGENT_TYPE_ADMIN or not rel_path:
            return None
        pm_id = project_pm_of(manager, project_name)
        if not pm_id:
            return None
        logger.warning(f"ProjectManagementTool: the Admin AI '{agent_id}' tried to change '{rel_path}' of project "
                       f"'{project_name}', which {pm_id} builds. Refused.")
        return (f"'{_clean_rel_path(rel_path)}' was NOT changed: the project '{project_name}' is built by {pm_id} and its "
                "team, so the Admin AI does not change its files - a second version beside the team's files breaks the "
                f"project. Tell {pm_id} what should change with send_message (the PM makes it a task for a worker), or tell "
                "the user what you found.")
    except Exception as err:
        logger.debug(f"admin_project_change_error failed: {err}")
        return None


_JAVASCRIPT_TWIN_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx")


def javascript_twin_creation_error(manager: Any, agent_id: Optional[str], project_name: Optional[str],
                                   session_name: Optional[str], rel_path: Optional[str], workspace: Any) -> Optional[str]:
    """
    Round 3s: the refusal (text) for creating dir/x.py when dir/x.js (or .ts, ...) exists and no task names dir/x.py - a
    Python copy of a JavaScript module - or None. Test files and the framework are not judged. Never raises.
    2026-10-26 10:16:32 (WiFi_Snake_Game) W3, told to "Write pytest tests for game logic", wrote src/core/snake_game.py - a
    "Python bridge module for JavaScript game logic" - beside src/core/snake_game.js, then src/utils/score_manager.py beside
    score_manager.js, and spent an hour making its tests pass against those copies. The game never runs them.
    """
    try:
        if not rel_path or not workspace or agent_id in FRAMEWORK_CALLERS:
            return None
        rel = _clean_rel_path(rel_path)
        path = PurePosixPath(rel)
        if path.suffix.lower() != ".py" or path.name == "conftest.py":
            return None
        from src.tools.project_links_check import is_test_file, _javascript_test_advice
        if is_test_file(rel):
            return None
        root = Path(workspace)
        twin = next((path.with_suffix(suffix).as_posix() for suffix in _JAVASCRIPT_TWIN_SUFFIXES
                     if (root / path.with_suffix(suffix).as_posix()).is_file()), None)
        if twin is None:
            return None
        if project_name and session_name:
            tw = ProjectManagementTool()._get_taskwarrior_instance(project_name, session_name)
            if tw is not None:
                from src.utils.task_files import file_paths_in_text
                for task in _all_tasks(tw):
                    if _task_names_path(file_paths_in_text(str(get_task_field(task, "description", "") or "")), rel):
                        return None  # planned Python work of that name
        logger.warning(f"ProjectManagementTool: '{agent_id}' tried to create '{rel}', a Python copy of '{twin}'. Refused.")
        return (f"'{rel}' was NOT created: {twin} already holds this module in JavaScript. A Python copy is a second "
                "implementation that the project never runs - testing it proves nothing about the real code, and the two "
                f"copies drift apart. {_javascript_test_advice(root, twin)}; pytest tests only the project's own Python "
                "files. If a task really needs this Python file, the PM names it in a task.")
    except Exception as err:
        logger.debug(f"javascript_twin_creation_error failed: {err}")
        return None


def idle_worker_message_note(manager: Any, sender_id: Optional[str], target_id: Optional[str]) -> str:
    """
    Round 3o: the note for a PM whose message reaches a worker that waits (worker_wait) WITHOUT an open task: a message
    gives it no work. '' otherwise. 2026-10-25 13:39:24 PM1 sent W1 (waiting, no task) a list of fixes for
    src/snake_game.py; W1 marked the message read and nothing happened. Never raises.
    """
    try:
        agents = getattr(manager, "agents", None)
        if not isinstance(agents, dict) or not sender_id or not target_id:
            return ""
        sender, target = agents.get(sender_id), agents.get(target_id)
        from src.agents.constants import AGENT_TYPE_PM, AGENT_TYPE_WORKER, WORKER_STATE_WAIT
        if sender is None or target is None or getattr(sender, "agent_type", None) != AGENT_TYPE_PM:
            return ""
        if getattr(target, "agent_type", None) != AGENT_TYPE_WORKER or getattr(target, "state", None) != WORKER_STATE_WAIT:
            return ""
        project_name = getattr(manager, "current_project", None)
        session_name = getattr(manager, "current_session", None)
        if not project_name or not session_name:
            return ""
        tw = ProjectManagementTool()._get_taskwarrior_instance(project_name, session_name)
        if tw is None:
            return ""
        for task in tw.tasks.pending():
            if str(get_task_field(task, "assignee", "") or "").strip() != target_id:
                continue
            if str(get_task_field(task, "task_progress", "todo")).strip().lower() not in _OPEN_EXCLUDED_PROGRESS:
                return ""  # it has an open task (also one that waits for others): it will work on it
        return (f" NOTE: {target_id} is waiting and has NO open task, so this message gives it no work - it can only read "
                f"it. If {target_id} must change files, create a task for it: project_management action 'add_task' with "
                f"a title, a description that names the file(s) and the exact change, and assignee_agent_id='{target_id}'.")
    except Exception as err:
        logger.debug(f"idle_worker_message_note failed: {err}")
        return ""


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
                return await self._execute_complete_task(tw, aliases, project_name, session_name, kwargs, agent_id=agent_id)
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
                # Round 3g: nor work a worker already HAS
                refusal = open_duplicate_error(tw, agent_id, kwargs.get("manager"), title, description,
                                               kwargs.get("project_filter"))
                if refusal:
                    return refusal
                # Round 3g: nor improvement work the plan does not ask for
                refusal = unplanned_improvement_error(tw, agent_id, kwargs.get("manager"), title, description,
                                                      kwargs.get("project_filter"))
                if refusal:
                    return refusal

            # Round 3e: a worker's (sub-)task does not take the files of ANOTHER open task
            refusal = foreign_files_subtask_error(tw, agent_id, kwargs.get("manager"), main_desc)
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
                # Round 3i: the Admin AI does not hand out the tasks of a PM's team
                refusal = admin_assignment_error(str(task['assignee']), agent_id, kwargs.get("manager"), "The task",
                                                 outcome="was NOT created")
                if refusal:
                    return refusal
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

    def _completion_refusal(self, task, project_name, session_name, task_label, caller_id=None) -> Optional[Dict[str, Any]]:
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
            # Round 3e: a task that says to run / verify / fix the tests needs a passing full test run of the current files
            from src.utils.test_runs import verification_problem
            unverified = None
            if caller_id not in FRAMEWORK_CALLERS and "project_kickoff" not in _task_tags(task):
                unverified = verification_problem(workspace, task['description'])
        except Exception as gate_err:
            logger.warning(f"ProjectManagementTool: completion check failed ({gate_err}); not blocking.")
            return None
        if not problems and not unverified:
            return None
        logger.warning(f"ProjectManagementTool: refused to mark task '{task_label}' finished: {problems or unverified}")
        if not problems:
            return {"status": "error", "error_type": "tests_not_passing", "message": (
                f"Task '{task_label}' was NOT marked finished: {unverified}\nA PM should send the task back to its worker "
                "with send_message (name what fails) instead of marking it finished.")}
        message = completion_refusal_message(str(task_label), problems)
        if unverified:
            message += f"\nAlso: {unverified}"
        return {"status": "error", "message": message}

    async def _execute_modify_task(self, tw, aliases, project_name, session_name, agent_id, kwargs):
            reassigned_from = None  # Round 3i: the worker that loses the task (it is told)
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



            # Round 3d: a FINISHED task is not given out again without being reopened, and never rewritten into other work
            refusal = finished_task_change_error(task, task_id, kwargs, agent_id, self._map_task_progress)
            if refusal:
                return refusal
            # Round 3t: an open task of the approved plan keeps its text (new work is a new task)
            refusal = planned_task_rewrite_error(task, task_id, kwargs, agent_id)
            if refusal:
                return refusal

            # Validate task_progress or legacy status if provided
            previous_progress = str(task['task_progress'] or 'todo')  # Round 2x: kept if a blocked task may not start
            requested_start = False
            if "task_progress" in kwargs or "status" in kwargs:
                raw_prog = kwargs.get("task_progress") or kwargs.get("status")
                t_prog, t_status = self._map_task_progress(raw_prog)
                requested_start = t_prog in STARTED_PROGRESS and previous_progress not in STARTED_PROGRESS
                if t_prog == "finished":
                    # Round 2a: PM1 marked tasks finished whose files held only docstrings (2026-10-18 15:30, 15:50)
                    refusal = self._completion_refusal(task, project_name, session_name, task_id, caller_id=agent_id)
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
                elif (refusal := admin_assignment_error(new_assignee, agent_id, kwargs.get("manager"), f"Task '{task_id}'")):
                    return refusal  # Round 3i: the Admin AI does not hand out the tasks of a PM's team
                elif (refusal := busy_worker_assignment_error(tw, new_assignee, task, agent_id, kwargs.get("manager"), f"Task '{task_id}'")):
                    return refusal  # Round 3c: one ready task at a time per worker
                else:
                    previous_assignee = str(task['assignee'] or "").strip()
                    task['assignee'] = new_assignee
                    modified_fields.append("assignee")
                    if previous_assignee and previous_assignee != new_assignee:
                        reassigned_from = previous_assignee
                    
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
                if reassigned_from:
                    await notify_previous_assignee(kwargs.get("manager"), agent_id, reassigned_from,
                                                   str(task['assignee'] or ""), task)
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

    async def _execute_complete_task(self, tw, aliases, project_name, session_name, kwargs, agent_id=None):
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



            refusal = self._completion_refusal(task, project_name, session_name, task_id, caller_id=agent_id)  # Round 2a
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
