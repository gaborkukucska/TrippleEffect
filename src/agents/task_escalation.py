# START OF FILE src/agents/task_escalation.py
"""
A worker that is forced to report again and again on the same task (Round 3d).

2026-10-23 12:43-15:12 (Multiplayer_Snake_Game, qwen3.5:4b): W1 worked on ONE task ("Fix failing unit tests in
tests/test_gameLogic.js") for two and a half hours. The framework forced it into worker_report 11 times after three failed
cycles and 7 times for a loop; each time W1 sent PM1 the same status ("14 passed, 13 failed ... the mock doesn't match"),
PM1 answered with advice and W1 went back to the same edits. Nobody saw that the task itself could not succeed as written
(the test file tested its own copy of the code). Every FORCED_REPORTS_BEFORE_ESCALATION-th forced report on the same task,
the Guardian sends the PM a message with the facts, asking for a different decision than "keep going".
"""
import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

FORCED_REPORTS_BEFORE_ESCALATION = 3
_COUNTS_ATTR = "_forced_reports_by_task"


def current_task_of(agent: Any) -> Optional[str]:
    task_id = getattr(agent, "active_task_id", None) or getattr(agent, "current_task_id", None)
    return str(task_id) if task_id else None


def record_forced_report(agent: Any) -> Optional[Tuple[str, int]]:
    """
    Counts one forced report of the worker on its current task. Returns (task_id, count) when the PM must be told (every
    FORCED_REPORTS_BEFORE_ESCALATION-th forced report on that task), else None. Never raises.
    """
    try:
        task_id = current_task_of(agent)
        if not task_id:
            return None
        counts = getattr(agent, _COUNTS_ATTR, None)
        if not isinstance(counts, dict):
            counts = {}
            setattr(agent, _COUNTS_ATTR, counts)
        counts[task_id] = counts.get(task_id, 0) + 1
        if counts[task_id] % FORCED_REPORTS_BEFORE_ESCALATION == 0:
            return task_id, counts[task_id]
    except Exception as err:
        logger.debug(f"record_forced_report failed: {err}")
    return None


def forced_report_count(agent: Any, task_id: Optional[str] = None) -> int:
    counts = getattr(agent, _COUNTS_ATTR, None)
    task_id = task_id or current_task_of(agent)
    return counts.get(task_id, 0) if isinstance(counts, dict) and task_id else 0


def supervisor_of(manager: Any, agent: Any) -> Optional[str]:
    """The PM of the worker's team, or None."""
    try:
        from src.agents.constants import AGENT_TYPE_PM
        state_manager = getattr(manager, "state_manager", None)
        team_id = state_manager.get_agent_team(agent.agent_id) if state_manager else None
        if not team_id:
            return None
        for member in state_manager.get_agents_in_team(team_id) or []:
            if getattr(member, "agent_type", None) == AGENT_TYPE_PM:
                return getattr(member, "agent_id", None)
    except Exception as err:
        logger.debug(f"supervisor_of failed: {err}")
    return None


def escalation_message(agent: Any, task_id: str, count: int, last_error: str = "") -> str:
    """The Guardian's message to the PM about a worker forced to report `count` times on one task."""
    description = " ".join(str(getattr(agent, "_injected_task_description", "") or "").split())
    if len(description) > 200:
        description = description[:197] + "..."
    task_text = f"task {task_id}" + (f" ('{description}')" if description else "")
    error_text = f"\nIts last error: {last_error}" if last_error else ""
    return (
        f"[Framework Escalation] {agent.agent_id} has been FORCED to stop and report {count} times on {task_text}, because "
        f"its tool calls kept failing or repeating.{error_text}\n"
        "Your advice so far has not changed the outcome, so do NOT just tell it to keep going. Decide ONE of these:\n"
        f"1. The task cannot succeed as written: change it (project_management modify_task with a new, smaller description "
        f"of the FIRST concrete step and the file(s)), then tell {agent.agent_id} exactly what changed.\n"
        "2. Something it depends on is missing or broken (e.g. a file that must export functions, a package missing from "
        "package.json, a test that tests a copy of the code): create a task for THAT problem first.\n"
        f"3. Send {agent.agent_id} ONE concrete different approach (a different tool or file, the exact change to make) - not "
        "the same explanation again."
    )

# END OF FILE src/agents/task_escalation.py
