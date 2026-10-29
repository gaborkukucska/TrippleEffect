# START OF FILE src/utils/test_failure_routing.py
"""
The failures of a verification run go to the workers whose tests fail (Round 4e).

2026-10-29 11:08-13:06 (WiFi_Multiplayer_Snake_Game, qwen3.5:4b) "Kick-off Task 8: Run all tests and the game server to
verify the complete system works end-to-end" went to W2, the Technical Writer. The full pytest run had 30-40 failures and
60+ errors in FOUR test files, written by W1 and W3 for their own tasks. The task could not be finished before the whole
suite passed (Round 3e), and a "run the tests" task could change any file (Round 3o) - so W2 spent two hours editing other
workers' tests and src/websocket_server.py, was forced to report 9 times, and every time the watchdog sent it back to work;
PM1 told it "STOP trying file operations. Task 8 is about RUNNING TESTS" while the framework kept refusing the finish.

Now, when a task that only says to RUN / VERIFY the tests (it does not say to fix them) cannot be finished because the full
test run fails, the failing test files that ANOTHER worker made get a fix task for that worker ("Fix the failing tests in
tests/x.py: ..."), the verification task waits for those tasks (it is started again when they are finished), and its worker
is told to wait. A failing file the verification worker made itself, or whose owner is unknown, stays its own work. A file
gets at most MAX_FIX_ROUNDS such tasks per verification task; after that the verification worker handles it.
Only pytest output names the failing file per test (see src/utils/test_runs.failing_tests); other runners are not routed.
"""
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_FIX_ROUNDS = 2
MAX_LISTED_TESTS = 3
FIX_TAG = "test_fix"
_FIX_WORD_RE = re.compile(r"\b(?:fix|fixes|fixing|fixed|repair|repairs|repairing|resolve|resolves|resolving|debug|debugs|"
                          r"correct|corrects|make\s+(?:\w+\s+){0,3}pass)\b", re.IGNORECASE)
_NAMED_TEST_FILE_RE = re.compile(r"\S+\.(?:m?js|cjs|ts|py)\b")


def verifies_tag(task_uuid: Any) -> str:
    return f"verifies:{str(task_uuid)[:8]}"


def says_to_fix(description: Optional[str]) -> bool:
    """True for a task text that says to fix / repair / resolve (failures, tests, bugs)."""
    return bool(description) and bool(_FIX_WORD_RE.search(str(description)))


def is_verification_only_task(description: Optional[str]) -> bool:
    """A task that says to run / verify the WHOLE test suite and does not say to fix anything (nor names one test file)."""
    from src.utils.test_runs import is_test_run_task
    text = str(description or "")
    return is_test_run_task(text) and not says_to_fix(text) and not _NAMED_TEST_FILE_RE.search(text)


def _names_score(text: str, rel: str) -> int:
    """How specifically a task text names the test file: 3 its path, 2 its name, 1 its folder ('tests/x/'), else 0."""
    lower, rel_lower = text.lower(), rel.lower()
    if rel_lower in lower:
        return 3
    if re.search(r"(?<![\w./-])" + re.escape(PurePosixPath(rel_lower).name) + r"(?![\w-])", lower):
        return 2
    folder = PurePosixPath(rel_lower).parent.as_posix()
    if folder not in ("", ".") and re.search(re.escape(folder) + r"/?(?![\w-])", lower):
        return 1
    return 0


def test_file_owner(tasks: List[Any], workspace: Any, rel: str, verification_uuid: str,
                    verifier: Optional[str], worker_ids: List[str]) -> Optional[Tuple[str, Any]]:
    """
    (worker id, task) that owns a test file: the worker that first changed it in this server run (with that task), else
    the worker of the task that names it most specifically (its path, its name, its folder; the earliest task first).
    Fix tasks of a routing count as the original's follow-up, not as owners. None if no other worker owns it.
    """
    from src.utils.task_utils import get_task_field
    from src.utils.file_authorship import first_change
    def worker_task(task: Any) -> bool:
        assignee = str(get_task_field(task, "assignee", "") or "").strip()
        tags = {str(tag) for tag in (get_task_field(task, "tags") or [])}
        return (assignee in worker_ids and str(get_task_field(task, "uuid", "")) != verification_uuid
                and FIX_TAG not in tags and "project_kickoff" not in tags)
    first = first_change(Path(workspace) / rel)
    if first and first.get("agent_id") in worker_ids:
        if first["agent_id"] == verifier:
            return None
        for task in tasks:
            if worker_task(task) and str(get_task_field(task, "uuid", "")).startswith(str(first.get("task_id") or "-")[:8]):
                return first["agent_id"], task
    scored = []
    for task in tasks:
        if not worker_task(task):
            continue
        score = _names_score(str(get_task_field(task, "description", "") or ""), rel)
        if score:
            entry = get_task_field(task, "entry", None)
            scored.append((-score, str(entry or ""), task))
    if not scored:
        return None
    _score, _entry, task = sorted(scored, key=lambda item: (item[0], item[1]))[0]
    owner = str(get_task_field(task, "assignee", "") or "").strip()
    return (owner, task) if owner != verifier else None


def fix_task_text(rel: str, items: List[str], verification_text: str) -> str:
    shown = "; ".join(items[:MAX_LISTED_TESTS]) + (f"; and {len(items) - MAX_LISTED_TESTS} more" if len(items) > MAX_LISTED_TESTS
                                                  else "")
    label = " ".join(str(verification_text).split())[:70]
    return (f"Fix the failing tests in {rel} ({len(items)} failing): {shown}. The full test run of '{label}' fails there. "
            f"Run `python -m pytest -q {rel}`, make each test call the code exactly as it is defined (read the code file "
            "first) and fix real bugs in the code; tell your PM about a bug in another worker's code.")


async def route_test_failures(tool: Any, tw: Any, project_name: str, session_name: str, task: Any, caller_id: Optional[str],
                              manager: Any) -> Optional[Dict[str, Any]]:
    """
    For a verification-only task whose full test run fails (the latest run of the current files): fix tasks for the other
    workers whose test files fail, the task made to wait for them - and the refusal that says so. None when nothing was
    routed (no such run, pytest gave no per-file summary, or every failing file is the verification worker's own).
    Never raises.
    """
    try:
        from src.utils.task_utils import get_task_field
        description = str(get_task_field(task, "description", "") or "")
        if not is_verification_only_task(description):
            return None
        from src.config.settings import settings
        from src.tools.sandbox_runner import resolve_shared_workspace
        from src.agents.tool_activity import workspace_fingerprint
        from src.utils.test_runs import FAILED, latest_test_run
        workspace = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_name), str(session_name))
        run = latest_test_run(workspace)
        current = workspace_fingerprint(workspace)
        if not run or run.get("outcome") != FAILED or current is None or run.get("fingerprint") != current:
            return None
        failing: Dict[str, List[str]] = run.get("failing") or {}
        if not failing:
            return None
        from src.tools.project_management import _all_tasks
        from src.agents.constants import AGENT_TYPE_WORKER
        agents = getattr(manager, "agents", None) or {}
        worker_ids = [agent_id for agent_id, agent in agents.items() if getattr(agent, "agent_type", None) == AGENT_TYPE_WORKER]
        task_uuid = str(get_task_field(task, "uuid", ""))
        verifier = str(get_task_field(task, "assignee", "") or "").strip() or None
        all_tasks = [t for t in _all_tasks(tw) if str(get_task_field(t, "status", "") or "").strip().lower() != "deleted"]
        tag = verifies_tag(task_uuid)
        routed: List[Tuple[str, str, str, bool]] = []  # (file, owner, fix task uuid, new)
        aliases = tool._load_aliases(project_name, session_name)
        for rel, items in sorted(failing.items()):
            owner = test_file_owner(all_tasks, workspace, rel, task_uuid, verifier, worker_ids)
            if owner is None:
                continue
            owner_id, _owner_task = owner
            earlier = [t for t in all_tasks if tag in {str(x) for x in (get_task_field(t, "tags") or [])}
                       and rel in str(get_task_field(t, "description", "") or "")]
            still_open = [t for t in earlier if str(get_task_field(t, "status", "pending")).strip().lower() == "pending"]
            if still_open:
                routed.append((rel, owner_id, str(get_task_field(still_open[0], "uuid", "")), False))
                continue
            if len(earlier) >= MAX_FIX_ROUNDS:
                continue  # fixed twice and failing again: the verification worker's work now
            result = await tool._execute_add_task(tw, aliases, project_name, session_name, "framework", {
                "description": fix_task_text(rel, items, description), "project_filter": project_name,
                "assignee_agent_id": owner_id, "tags": f"{FIX_TAG},{tag}", "priority": "H"})
            if not isinstance(result, dict) or result.get("status") != "success" or not result.get("task_uuid"):
                logger.warning(f"test_failure_routing: the fix task for {rel} ({owner_id}) was not created: {result}")
                continue
            routed.append((rel, owner_id, str(result["task_uuid"]), not result.get("is_duplicate")))
        if not routed:
            return None
        fresh = tw.tasks.get(uuid=task_uuid)
        if fresh["depends"] is None:
            fresh["depends"] = set()
        for _rel, _owner, fix_uuid, _new in routed:
            fresh["depends"].add(tw.tasks.get(uuid=fix_uuid))
        fresh["task_progress"] = "waiting"
        fresh.save()
        lines = "\n".join(f"- {rel} -> {owner} (task {fix_uuid}{', new' if new else ''})" for rel, owner, fix_uuid, new in routed)
        mine = [rel for rel in failing if rel not in {r[0] for r in routed}]
        logger.warning(f"test_failure_routing: '{description[:60]}' - failures routed to their owners: "
                       f"{[(r[0], r[1]) for r in routed]}; left: {mine}.")
        message = (f"Task '{task_uuid}' was NOT marked finished: the full test run (`{run.get('command')}`) fails, and the "
                   f"failures are in test files OTHER workers made. The framework gave each its fix:\n{lines}\n"
                   "This task now WAITS for those tasks and is started again when they are finished - then run the whole "
                   "test suite once more and mark the task finished when it passes.")
        if mine:
            message += f" These failing files are yours to fix first: {', '.join(mine)}."
        else:
            message += (" Do NOT change their files: request 'worker_wait' now (a worker), or let the task wait (a PM).")
        return {"status": "error", "error_type": "tests_routed", "message": message,
                "routed": [{"file": rel, "assignee": owner, "task_uuid": fix_uuid} for rel, owner, fix_uuid, _ in routed]}
    except Exception as err:
        logger.warning(f"test_failure_routing: routing failed ({err}); not blocking more than before.")
        return None

# END OF FILE src/utils/test_failure_routing.py
