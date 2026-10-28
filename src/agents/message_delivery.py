# START OF FILE src/agents/message_delivery.py
"""
Messages between agents reach a BUSY agent at its next step, and are shown at the END of its prompt (Round 3i).

2026-10-24 10:00-11:43 (WiFi_Snake_Multiplayer_Game): PM1 sent 8 messages to its workers. A message to a worker in
worker_work or worker_report was put in its inbox, and the inbox was only emptied when the worker went to worker_wait or
itself sent a message - so 6 of the 8 NEVER reached the worker:
* 11:08:32 and 11:10:42 PM1's answers to W2's report (W2 had gone back to worker_work 2 seconds earlier), 11:42:37;
* 11:16:53 PM1's detailed answer to W1's report, 11:28:44, and 11:38:37 PM1's answer to the Guardian's escalation
  ("the task description is too broad and the tests ...").
The "priority recheck" a message set only restarted the busy agent's thinking - without the message. And a message that DID
reach the history sat in the middle of the prompt: after it came the workspace tree, the task list and, last, "[TASK FOCUS
REMINDER] ... take the next concrete step", so a small model read the message as old context.

* deliver_pending_messages(): at the start of every step of an agent, the messages waiting in its inbox move to the end of
  its history. A deferred task ACTIVATION (it carries _deferred_task_description / _deferred_task_id) stays queued until the
  worker is in worker_wait, as before: a new task must not take over the current one.
* new_messages_block(): the text appended LAST to the prompt while a message is unread (in full for its first
  FULL_SHOWS steps, then as a one-line reminder), so it is the most recent thing the model reads.
* unread_messages_missing_from(): unread messages a context summary would drop (they are put back after the summary).
Never raises.
"""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Round 4b: a PM's kick-off status report to the Admin AI asks nothing. 2026-10-28 18:24:44 (granite4.2:8b) admin_ai read
# "act on it first (answer with send_message ...)" under PM1's "kick-off phase complete" report, sent PM1 the whole project
# again as a "new request" (with a WebSocket server the plan did not have), then read the plan files for 14 minutes and
# tried to write src/client/style.css - while PM1 and W1 waited for the same Ollama server.
_STATUS_REPORT_RE = re.compile(
    r"\bkick-?off(?: phase)? (?:is )?complete\b|\bkick-?off tasks\b[^.]{0,80}?\bassigned\b", re.IGNORECASE)
_FROM_RE = re.compile(r"^\[MESSAGE_ID: [^\]]+\] \[From @([^\s\]]+)")


def is_status_report_for_admin(agent: Any, message: Any) -> bool:
    """True for a PM's kick-off status report in the Admin AI's history (Round 4b)."""
    if getattr(agent, "agent_type", None) != "admin" or not isinstance(message, dict):
        return False
    content = str(message.get("content") or "")
    sender = _FROM_RE.match(content)
    if not sender or not re.match(r"^PM\d*$", sender.group(1)):
        return False
    return bool(_STATUS_REPORT_RE.search(content))

DEFERRED_KEYS = ("_deferred_task_description", "_deferred_task_id")
FULL_SHOWS = 3
MAX_BLOCK_MESSAGES = 3
MAX_MESSAGE_CHARS = 1500
_SHOWN_ATTR = "_message_block_shows"


def is_deferred_activation(message: Any) -> bool:
    """True for a queued task activation that waits for worker_wait."""
    return isinstance(message, dict) and any(key in message for key in DEFERRED_KEYS)


def deliver_pending_messages(agent: Any) -> int:
    """
    Moves the queued messages of the agent's inbox (except deferred task activations) to the end of its history.
    Returns how many were delivered.
    """
    try:
        inbox = getattr(agent, "message_inbox", None)
        if not inbox:
            return 0
        deliver = [m for m in inbox if isinstance(m, dict) and not is_deferred_activation(m)]
        if not deliver:
            return 0
        agent.message_inbox = [m for m in inbox if not (isinstance(m, dict) and not is_deferred_activation(m))]
        agent.message_history.extend(deliver)
        logger.info(f"message_delivery: delivered {len(deliver)} queued message(s) to busy agent "
                    f"'{getattr(agent, 'agent_id', '?')}' (state {getattr(agent, 'state', '?')}): "
                    f"{[m.get('message_id') for m in deliver]}")
        return len(deliver)
    except Exception as err:
        logger.debug(f"deliver_pending_messages failed: {err}")
        return 0


def _read_ids(agent: Any) -> Any:
    read = getattr(agent, "read_message_ids", None)
    return read if isinstance(read, (dict, set, list, tuple)) else ()


def unread_messages(agent: Any, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The messages (dicts with a message_id) in history that the agent has not marked read, oldest first."""
    read = _read_ids(agent)
    found: List[Dict[str, Any]] = []
    seen = set()
    for message in history or []:
        if not isinstance(message, dict):
            continue
        message_id = message.get("message_id")
        if message_id and message_id not in read and message_id not in seen:
            seen.add(message_id)
            found.append(message)
    return found


def unread_messages_missing_from(agent: Any, before: List[Dict[str, Any]],
                                 after: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The unread messages of `before` that are not in `after` (a context summary dropped them)."""
    kept = {m.get("message_id") for m in after or [] if isinstance(m, dict) and m.get("message_id")}
    return [m for m in unread_messages(agent, before) if m.get("message_id") not in kept]


def new_messages_block(agent: Any, history: List[Dict[str, Any]]) -> Optional[str]:
    """
    The text appended LAST to the prompt while the agent has unread messages, or None. A message is shown in full for its
    first FULL_SHOWS steps (counted per message), then only named, so an ignored message keeps a short reminder.
    """
    try:
        unread = unread_messages(agent, history)
        if not unread:
            return None
        shows = getattr(agent, _SHOWN_ATTR, None)
        if not isinstance(shows, dict):
            shows = {}
            setattr(agent, _SHOWN_ATTR, shows)
        live_ids = {m.get("message_id") for m in unread}
        for stale in [key for key in shows if key not in live_ids]:
            shows.pop(stale, None)
        full = [m for m in unread if shows.get(m.get("message_id"), 0) < FULL_SHOWS][-MAX_BLOCK_MESSAGES:]
        short = [m for m in unread if m not in full]
        lines = ["[NEW MESSAGE(S) FOR YOU - read this before your next step]"]
        for message in full:
            content = str(message.get("content") or "").strip()
            if len(content) > MAX_MESSAGE_CHARS:
                content = content[:MAX_MESSAGE_CHARS - 3] + "..."
            lines.append(content)
            shows[message.get("message_id")] = shows.get(message.get("message_id"), 0) + 1
        if short:
            lines.append("Still unread (shown above earlier): " + ", ".join(str(m.get("message_id")) for m in short[-5:]))
        ids = ", ".join(f"'{m.get('message_id')}'" for m in (full + short)[-5:])
        if all(is_status_report_for_admin(agent, m) for m in unread):
            lines.append("These are STATUS REPORTS of a Project Manager: they ask you nothing. Tell the user in one or two "
                         "sentences that the project's work has started, acknowledge them with mark_message_read "
                         f"(message_id {ids}), then request 'admin_standby'. Do NOT message the PM about them, and do not "
                         "plan, read or build anything for them: the PM and its workers do the project.")
            return "\n".join(lines)
        lines.append("A message from your PM, a worker, the Admin AI or the Guardian can change what you should do NEXT: "
                     "act on it first (answer with send_message if it asks you something), then acknowledge it with "
                     f"mark_message_read (message_id {ids}).")
        return "\n".join(lines)
    except Exception as err:
        logger.debug(f"new_messages_block failed: {err}")
        return None

# END OF FILE src/agents/message_delivery.py
