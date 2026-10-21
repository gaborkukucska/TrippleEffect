# START OF FILE src/agents/error_recovery.py
"""
Agents in the 'error' status come back (Round 2r).

2026-10-21 19:31:44 (WiFi_Snake_Multiplayer_Game, qwen3.5:9b): PM1's modify_task call failed three times in a row (the empty
description it sent reached Taskwarrior), so CycleHandler set PM1 to 'error' "to prevent an infinite loop". Only an incoming
message ever clears that status, and nobody messaged PM1: Task 3 and the test task were ready and unassigned, W1 waited in
worker_wait, and the run stood still until the user stopped it at 19:37:47.

The periodic watchdog (AgentManager._periodic_pm_manage_check) calls ErrorRecovery.consider() for every agent:
* an agent first seen in 'error' is noted; ERROR_RECOVERY_DELAY_SECONDS later it is set back to 'idle' with a
  '[Framework Recovery]' note that repeats why it was stopped, and its cycle is scheduled (Admin AI only when a user
  message is still unanswered);
* at most ERROR_RECOVERY_MAX_PER_HOUR recoveries per agent and hour. Then its supervisor (worker -> PM, PM -> Admin AI) gets
  ONE report from the Constitutional Guardian (a message also resumes an agent in 'error') and the user sees it in the UI;
* never resumed: an agent the user stopped (Constitutional Guardian 'Stop agent'), the Guardian agent itself, an agent
  awaiting project approval, and no agent while the system is paused for a user decision.
"""
import logging
import time
from typing import Any, Optional

from src.agents.constants import (
    AGENT_STATUS_ERROR, AGENT_STATUS_IDLE, AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER, BOOTSTRAP_AGENT_ID,
    CONSTITUTIONAL_GUARDIAN_AGENT_ID,
)

logger = logging.getLogger(__name__)

RECOVERY_TAG = "[Framework Recovery]"
ERROR_TAG = "[Framework Error]"
RECOVERY_WINDOW_SECONDS = 3600.0
MAX_REASON_CHARS = 600
DEFAULT_REASON = "its last cycle ended with an error."


def error_reason(agent: Any) -> str:
    """The text of the newest '[Framework Error]' note in the agent's history (since its last recovery), else a default."""
    for msg in reversed(list(getattr(agent, "message_history", None) or [])):
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str):
            continue
        if content.startswith(ERROR_TAG):
            text = content[len(ERROR_TAG):].strip(" :\n")
            return (text[:MAX_REASON_CHARS] + (" ..." if len(text) > MAX_REASON_CHARS else "")) or DEFAULT_REASON
        if content.startswith(RECOVERY_TAG):
            break  # older errors were already reported in that note
    return DEFAULT_REASON


def recovery_note(reason: str, attempt: int, max_attempts: int) -> str:
    return (f"{RECOVERY_TAG}: You were stopped (status 'error') because: {reason} "
            f"The framework has resumed you (recovery {attempt} of at most {max_attempts} per hour). "
            "Do NOT repeat a call that failed in the same way: read its error, change what the error names (send only "
            "the arguments that are needed, without empty values), or take another next step of your current state.")


def exhausted_report(agent: Any, reason: str, attempts: int) -> str:
    agent_id = getattr(agent, "agent_id", "?")
    return (f"⚠️ Agent `{agent_id}` (state `{getattr(agent, 'state', None)}`) stopped with an error {attempts} times within "
            f"an hour and is NOT resumed automatically any more.\n**Last reason:** {reason}\n"
            f"**ACTION REQUIRED:** send `{agent_id}` ONE message with `send_message` that names its concrete next step "
            "(a message resumes it), or give its task to another agent.")


def has_unanswered_user_message(agent: Any) -> bool:
    """True if the newest user/assistant message in the agent's history is from the user."""
    for msg in reversed(list(getattr(agent, "message_history", None) or [])):
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
            return msg.get("role") == "user"
    return False


def supervisor_of(manager: Any, agent: Any) -> Optional[str]:
    """The PM of a worker's team, Admin AI for a PM, None otherwise."""
    agent_type = getattr(agent, "agent_type", None)
    if agent_type == AGENT_TYPE_PM:
        return BOOTSTRAP_AGENT_ID
    if agent_type != AGENT_TYPE_WORKER:
        return None
    state_manager = getattr(manager, "state_manager", None)
    if state_manager is None:
        return None
    try:
        team_id = state_manager.get_agent_team(agent.agent_id)
        for member in (state_manager.get_agents_in_team(team_id) if team_id else []):
            if getattr(member, "agent_type", None) == AGENT_TYPE_PM:
                return getattr(member, "agent_id", None)
    except Exception as err:  # the lookup must never break the watchdog
        logger.debug(f"ErrorRecovery: no supervisor for '{agent.agent_id}': {err}")
    return None


class ErrorRecovery:
    """Resumes agents left in the 'error' status (see the module docstring)."""

    def __init__(self, manager: Any):
        self._manager = manager

    @staticmethod
    def _limits():
        from src.config.settings import settings
        delay = float(getattr(settings, "ERROR_RECOVERY_DELAY_SECONDS", 60.0))
        max_attempts = int(getattr(settings, "ERROR_RECOVERY_MAX_PER_HOUR", 3))
        return delay, max(1, max_attempts)

    def skip_reason(self, agent: Any) -> Optional[str]:
        """Why an agent in 'error' must stay there for now (None if it may be resumed)."""
        if getattr(agent, "agent_id", None) == CONSTITUTIONAL_GUARDIAN_AGENT_ID:
            return "it is the Constitutional Guardian"
        if getattr(agent, "_stopped_by_user", False):
            return "the user stopped it"
        if getattr(agent, "_awaiting_project_approval", False):
            return "its project awaits approval"
        escalation = getattr(self._manager, "cg_escalation", None)
        if escalation is not None:
            try:
                if escalation.is_system_paused():
                    return "the system is paused for a user decision"
            except Exception:
                pass
        return None

    async def consider(self, agent: Any, now: Optional[float] = None) -> bool:
        """Called by the watchdog for every agent. Returns True if the agent was resumed (and its cycle scheduled)."""
        now = time.time() if now is None else now
        if getattr(agent, "status", None) != AGENT_STATUS_ERROR:
            # back in work (e.g. a message resumed it): forget the old sighting and a user stop that is over
            if getattr(agent, "_error_seen_at", None) is not None:
                agent._error_seen_at = None
            if getattr(agent, "_stopped_by_user", False):
                agent._stopped_by_user = False
            return False
        skip = self.skip_reason(agent)
        if skip:
            logger.debug(f"ErrorRecovery: '{agent.agent_id}' stays in 'error': {skip}.")
            return False
        seen = getattr(agent, "_error_seen_at", None)
        if seen is None:
            agent._error_seen_at = now
            logger.info(f"ErrorRecovery: '{agent.agent_id}' is in 'error' (state '{getattr(agent, 'state', None)}'); "
                        "it will be resumed if it is still there at the next check.")
            return False
        delay, max_attempts = self._limits()
        if now - seen < delay:
            return False
        attempts = [t for t in (getattr(agent, "_error_recoveries", None) or []) if now - t < RECOVERY_WINDOW_SECONDS]
        agent._error_recoveries = attempts
        reason = error_reason(agent)
        if len(attempts) >= max_attempts:
            await self._report_exhausted(agent, reason, len(attempts), now)
            return False
        attempts.append(now)
        agent._error_seen_at = None
        agent._consecutive_tool_failures = 0
        if hasattr(agent, "_empty_response_retry_count"):
            agent._empty_response_retry_count = 0
        agent.message_history.append({"role": "system", "content": recovery_note(reason, len(attempts), max_attempts)})
        agent.set_status(AGENT_STATUS_IDLE)
        logger.warning(f"ErrorRecovery: resumed '{agent.agent_id}' (state '{getattr(agent, 'state', None)}') after an error "
                       f"({len(attempts)}/{max_attempts} this hour). Reason: {reason[:200]}")
        await self._ui({"type": "status", "agent_id": agent.agent_id,
                        "content": f"Resumed after an error ({len(attempts)}/{max_attempts} this hour)."})
        if getattr(agent, "agent_type", None) == AGENT_TYPE_ADMIN and not has_unanswered_user_message(agent):
            return True  # Admin AI waits for the user; nothing to answer now
        await self._manager.schedule_cycle(agent, 0)
        return True

    async def _report_exhausted(self, agent: Any, reason: str, attempts: int, now: float) -> None:
        reported_at = getattr(agent, "_error_recovery_reported_at", None)
        if reported_at is not None and now - float(reported_at) < RECOVERY_WINDOW_SECONDS:
            return  # one report per hour
        agent._error_recovery_reported_at = now
        report = exhausted_report(agent, reason, attempts)
        supervisor_id = supervisor_of(self._manager, agent)
        if supervisor_id == BOOTSTRAP_AGENT_ID and getattr(agent, "agent_type", None) == AGENT_TYPE_PM:
            try:
                from src.agents.assignable_work import assignment_hint_for
                open_work = assignment_hint_for(self._manager, agent)
            except Exception:  # the report must never fail
                open_work = None
            if open_work:
                report += f"\n\n**Open work the PM should assign** (you can forward this):\n{open_work}"
        logger.error(f"ErrorRecovery: '{agent.agent_id}' stopped with an error {attempts} times within an hour; not resumed "
                     f"again. Reporting to {supervisor_id or 'the user'}.")
        await self._ui({"type": "error", "agent_id": agent.agent_id, "content": report})
        agents = getattr(self._manager, "agents", {}) or {}
        handler = getattr(self._manager, "interaction_handler", None)
        if supervisor_id and supervisor_id != agent.agent_id and supervisor_id in agents and handler is not None:
            try:
                await handler.route_and_activate_agent_message(
                    sender_id=CONSTITUTIONAL_GUARDIAN_AGENT_ID, target_identifier=supervisor_id, message_content=report)
            except Exception as err:
                logger.error(f"ErrorRecovery: could not report '{agent.agent_id}' to '{supervisor_id}': {err}")

    async def _ui(self, payload: dict) -> None:
        sender = getattr(self._manager, "send_to_ui", None)
        if sender is None:
            return
        try:
            await sender(payload)
        except Exception as err:
            logger.debug(f"ErrorRecovery: UI update failed: {err}")

# END OF FILE src/agents/error_recovery.py
