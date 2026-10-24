# START OF FILE src/agents/team_scaling.py
"""
When a PM may create one more worker of a role its team already has (Round 3i).

2026-10-24 13:23-13:25 (WiFi_Snake_Game): W1 and W2 (both role 'Coder') each had an open task. PM1 was refused when it gave
them another one, and the refusal said "if the tasks can be done in parallel, create one more worker with the manage_team
tool". Its create_agent with role 'Coder' was then refused - "An agent with the role 'Coder' already exists in your team.
Do not create duplicate roles" - twice, and the third failure in a row put PM1 into the error state.

second_worker_refusal(): None when every worker of that role has an open ready task and the team has fewer than
MAX_TEAM_WORKERS workers; else the refusal text, naming the free worker of that role. When the task list cannot be read the
old rule applies (no second worker of a role). Never raises.
"""
import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

MAX_TEAM_WORKERS = 4  # the pm_manage prompt: "You can have up to 4 workers per project"


def _open_ready_tasks(manager: Any, worker_id: str) -> Optional[list]:
    try:
        from src.tools.project_management import ProjectManagementTool, open_ready_tasks_of
        project = getattr(manager, "current_project", None)
        session = getattr(manager, "current_session", None)
        if not project or not session:
            return None
        tw = ProjectManagementTool()._get_taskwarrior_instance(project, session)
        if tw is None:
            return None
        return open_ready_tasks_of(list(tw.tasks.pending()), worker_id)
    except Exception as err:
        logger.debug(f"team_scaling: task lookup for '{worker_id}' failed: {err}")
        return None


def second_worker_refusal(manager: Any, role: str, same_role: List[Any], team_members: List[Any]) -> Optional[str]:
    """The refusal text for one more worker of `role`, or None when it may be created."""
    from src.agents.constants import AGENT_TYPE_WORKER
    workers = [a for a in team_members or [] if getattr(a, "agent_type", None) == AGENT_TYPE_WORKER]
    base = (f"Agent creation failed: an agent with the role '{role}' already exists in your team "
            f"({', '.join(getattr(a, 'agent_id', '?') for a in same_role)}).")
    if len(workers) >= MAX_TEAM_WORKERS:
        return (f"{base} Your team already has {len(workers)} workers (the most a team may have). Leave the task "
                "unassigned: you are woken up when a worker is free.")
    free = []
    for agent in same_role:
        tasks = _open_ready_tasks(manager, getattr(agent, "agent_id", ""))
        if tasks is None:
            return f"{base} Do not create duplicate roles. Use 'list_agents' to see your current team."
        if not tasks:
            free.append(getattr(agent, "agent_id", "?"))
    if free:
        return (f"{base} {', '.join(free)} has no open task: give the task to {free[0]} instead of creating another "
                f"'{role}'.")
    return None

# END OF FILE src/agents/team_scaling.py
