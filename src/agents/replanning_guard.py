# START OF FILE src/agents/replanning_guard.py
"""
The Admin AI does not plan the same user request twice (Round 3l).

2026-10-24 21:25:35 (WiFi_Snake_Game): the user answered a Constitutional Guardian concern about PM1's message (sudo
apt-get). The system resumed, and Admin AI - whose newest user message was still the ORIGINAL project request from 17:04,
because the Guardian's message had been marked read and filtered out - thought "The user has just asked for a browser-based
snake game ... This is a new project request" and requested 'planning'. A whole new plan became a project update while PM1
and four workers were still building the first one.

* last_human_text(): the newest message the HUMAN wrote (framework and agent messages start with '[').
* note_planning_started(): remembers which human message a plan was started for.
* replanning_block_message(): the refusal for 'planning' when no new human message came since the last plan AND the
  project of that plan is being built (a PM of the current project exists and is not waiting for approval), else None.
Never raises.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PLANNED_FOR_ATTR = "_planned_for_human_text"


def last_human_text(history: Any) -> str:
    """Text of the newest user-role message the human wrote ('' if none): framework / agent messages start with '['."""
    for message in reversed(list(history or [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else str(content or "")
        if text.strip() and not text.lstrip().startswith("["):
            return text
    return ""


def note_planning_started(agent: Any) -> None:
    """Remember the human message the Admin AI now plans for."""
    try:
        text = last_human_text(getattr(agent, "message_history", None))
        if text:
            setattr(agent, _PLANNED_FOR_ATTR, text)
    except Exception as err:
        logger.debug(f"note_planning_started failed: {err}")


def _active_pm(manager: Any) -> Optional[Any]:
    from src.agents.constants import AGENT_TYPE_PM
    project = getattr(manager, "current_project", None)
    for agent in list((getattr(manager, "agents", None) or {}).values()):
        if getattr(agent, "agent_type", None) != AGENT_TYPE_PM or getattr(agent, "_awaiting_project_approval", False):
            continue
        config = (getattr(agent, "agent_config", None) or {}).get("config", {}) or {}
        if project and config.get("project_name_context") not in (None, "", project):
            continue
        return agent
    return None


def replanning_block_message(manager: Any, agent: Any) -> Optional[str]:
    """The refusal for the Admin AI's 'planning' request when nothing new came from the user, or None."""
    try:
        planned_for = getattr(agent, _PLANNED_FOR_ATTR, None)
        if not planned_for:
            return None
        newest = last_human_text(getattr(agent, "message_history", None))
        if not newest or newest != planned_for:
            return None
        pm = _active_pm(manager)
        if pm is None:
            return None
        shown = " ".join(newest.split())
        shown = shown if len(shown) <= 120 else shown[:117] + "..."
        project = getattr(manager, "current_project", None) or "the project"
        logger.warning(f"replanning_guard: Admin AI '{getattr(agent, 'agent_id', '?')}' asked for 'planning' without a new "
                       f"user message; '{project}' is being built by '{pm.agent_id}'. Refused.")
        return ("[Framework Note]: You were NOT moved to 'planning': the user has sent nothing new since you planned their "
                f"request ('{shown}'), and that project ({project}) is being built right now by {pm.agent_id} and its "
                "workers. Messages from agents, the Guardian or the framework are not user requests. Answer what is new "
                "(or tell the user the status), or request 'admin_standby'. Plan again only when the USER asks for "
                "something new or for changes.")
    except Exception as err:
        logger.debug(f"replanning_block_message failed: {err}")
        return None

# END OF FILE src/agents/replanning_guard.py
