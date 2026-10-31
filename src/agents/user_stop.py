# START OF FILE src/agents/user_stop.py
"""
Replies that arrive after the user STOPPED the project are not acted on (Round 3o).

2026-10-25 13:40:47 the user pressed Stop: PM1 was moved to pm_standby and W2 to worker_wait. Both were waiting for an LLM
reply at that moment; the replies arrived at 13:40:57 and 13:41:05 and their tool calls still ran (PM1's manage_team
list_agents, W2's code_editor edit of src/snake_game.py). A stopped project must not change files.

* mark_stopped() / mark_resumed(): the stop and start routes (src/api/http_routes.py) stamp each agent.
* stopped_during_cycle(): True when the agent was stopped after its current cycle began.
* The cycle handler drops the tool calls and the state change of such a reply, leaves a note in the agent's history (so
  it knows on resume what did not run) and does not reschedule the agent - unless the project was resumed meanwhile, then
  the agent simply runs again with the resume note.
"""
import time
from typing import Any, Dict, Iterable, List, Optional

STOPPED_AT_ATTR = "_stopped_by_user_at"
RESUMED_AT_ATTR = "_resumed_by_user_at"
# events whose actions must not happen after a stop (text and thoughts are harmless; a workflow has already run)
DISCARDED_EVENT_TYPES = frozenset({"tool_requests", "agent_state_change_requested"})


def mark_stopped(agent: Any, when: Optional[float] = None) -> None:
    setattr(agent, STOPPED_AT_ATTR, time.time() if when is None else when)


def mark_resumed(agent: Any, when: Optional[float] = None) -> None:
    setattr(agent, RESUMED_AT_ATTR, time.time() if when is None else when)


def _stamp(agent: Any, attr: str) -> Optional[float]:
    value = getattr(agent, attr, None)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def stopped_during_cycle(agent: Any, cycle_started_at: Optional[float]) -> bool:
    """True if the user stopped the project after this agent's current cycle began."""
    stopped = _stamp(agent, STOPPED_AT_ATTR)
    return stopped is not None and cycle_started_at is not None and stopped >= cycle_started_at


def resumed_since_stop(agent: Any) -> bool:
    """True if the project was resumed after the agent's last stop."""
    stopped, resumed = _stamp(agent, STOPPED_AT_ATTR), _stamp(agent, RESUMED_AT_ATTR)
    return stopped is not None and resumed is not None and resumed > stopped


def _call_names(calls: Iterable[Any]) -> List[str]:
    names: List[str] = []
    for call in calls or []:
        if isinstance(call, dict):
            name = call.get("name") or (call.get("function") or {}).get("name")
            if name:
                names.append(str(name))
    return names


def discarded_reply_note(event: Dict[str, Any]) -> str:
    """The note kept in the agent's history for a reply whose actions were dropped because of the stop."""
    if event.get("type") == "tool_requests":
        names = _call_names(event.get("calls") or [])
        what = f"its tool call(s) ({', '.join(names)}) were NOT run" if names else "its tool calls were NOT run"
        state_change = event.get("state_change")  # Round 4k: a request_state made in the same reply
        if isinstance(state_change, dict) and state_change.get("requested_state"):
            what += f" and its request to change to the state '{state_change['requested_state']}' was NOT applied"
    else:
        what = f"its request to change to the state '{event.get('requested_state', '?')}' was NOT applied"
    return (f"[Framework Note]: The user STOPPED the project while your last reply was being written, so {what}. "
            "Nothing changed. When the project is resumed, check the current state before you act again.")

# END OF FILE src/agents/user_stop.py
