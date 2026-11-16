# START OF FILE src/agents/loop_answer.py
"""
Round 5f: a worker's loop is answered where it works, not by moving it to worker_report.

2026-11-15 19:40-23:04 (qwen3.5:4b): workers were moved to worker_report 78 times - by the scheduler's loop and failed-cycle
detectors and by the cycle handler's duplicate and identical-failure breakers. In worker_report they cannot edit, and they
kept trying: 53 refused tool calls there (62 with worker_wait), each a model call; then they asked to go back to work,
made the same call and were moved again (W4: 11 times between 20:29 and 21:01). Round 5e listed this as the next step. A
4B model does not "report its blockers"; it needs to be told what is different about the call it keeps making.

* answer_loop_in_place(): for a worker in worker_work / worker_test, the notice that answers its loop (and the count is
  kept per task) - None once the task had LOOP_ANSWERS_PER_TASK answers: then the old move to worker_report applies.
* loop_notice(): what the repeated call was, what it gave, and what to do instead (by the kind of call and result).
Never raises.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LOOP_ANSWERS_PER_TASK = 2
_WORK_STATES = ("worker_work", "worker_test")
_EDIT_TOOLS = ("code_editor", "file_system")
_READ_ACTIONS = ("read", "read_file", "read_with_lines", "list", "tree", "exists")


def _last_call_and_result(history: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """The agent's last tool call ({'name', 'arguments'}) and the text of its result ('' if none)."""
    results: Dict[str, str] = {}
    for message in reversed(history or []):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" and message.get("tool_call_id"):
            results.setdefault(str(message.get("tool_call_id")), str(message.get("content") or ""))
        if message.get("role") == "assistant" and message.get("tool_calls"):
            call = message["tool_calls"][-1] if isinstance(message["tool_calls"], list) else None
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = call.get("name") or function.get("name")
            arguments = call.get("arguments", function.get("arguments"))
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"_raw": arguments}
            return {"name": str(name or "?"), "arguments": arguments if isinstance(arguments, dict) else {}}, \
                results.get(str(call.get("id")), "")
    return None, ""


def _call_text(call: Dict[str, Any]) -> str:
    args = call.get("arguments") or {}
    shown = [f"{key}={str(args[key])[:60]!r}" for key in ("action", "filename", "path", "function", "command")
             if isinstance(args.get(key), (str, int)) and str(args.get(key)).strip()]
    return f"{call.get('name')}({', '.join(shown)})"


def loop_notice(agent: Any, kind: str, number: int, result: Optional[str] = None) -> str:
    """The text that answers a worker's loop (kind: 'repeat' or 'failed_cycles'); `result`: the repeated call's result when
    it is not in the history yet."""
    call, last_result = _last_call_and_result(list(getattr(agent, "message_history", None) or []))
    result = str(result) if result else last_result
    result_line = " ".join(result.split())[:500]
    lower = result.lower()
    head = (f"[FRAMEWORK - LOOP {number}/{LOOP_ANSWERS_PER_TASK}]: "
            + ("your last tool calls failed 3 times in a row" if kind == "failed_cycles"
               else "you keep making the same tool call") + ". ")
    if call is not None:
        head += f"The call was {_call_text(call)}"
        head += f" and it gave: \"{result_line}\". " if result_line else ". "
    head += "Sending it again gives the same answer. You stay in your work state - do something DIFFERENT now:\n"
    tool = call.get("name") if call else ""
    action = str((call or {}).get("arguments", {}).get("action") or "").lower()
    if any(marker in lower for marker in ("file of the finished task", "file of the open task", "is changing it right now",
                                          "does not name it")):
        advice = ("- That file belongs to another task, so you cannot change it. Fix what YOUR task's files can fix "
                  "(e.g. make your test call the code the way the file really defines it).\n"
                  "- If the bug really is in that file: send_message to your PM once (the file, what is wrong, the error), "
                  "then go on with your own files.")
    elif tool == "command_executor":
        advice = ("- Nothing changed since the last run, so the command gives the same result. Read the FIRST error of its "
                  "output, open the line it names (file_system 'read' with 'filename'), change the code or the test, and "
                  "only then run the command again.")
    elif tool in _EDIT_TOOLS and action not in _READ_ACTIONS:
        advice = ("- Read the CURRENT text first: file_system action 'read' with 'filename' (and 'function' to read one "
                  "definition).\n"
                  "- Then make ONE code_editor 'replace_function' call whose 'code' is the COMPLETE new definition. 'code' may "
                  "hold several definitions and their import lines: each replaces the one of that name.\n"
                  "- Do not send text the file already has, and do not repeat a change that was refused.")
    elif tool in _EDIT_TOOLS or tool in ("codebase_search", "knowledge_base"):
        advice = ("- You have this content already: it is in your history above. Do not read or search it again - make the "
                  "change your task needs now (code_editor 'replace_function').")
    else:
        advice = "- Use another tool or another action for the next step of your task."
    tail = ("\n- When your task is done: project_management 'modify_task' (task_progress='finished'). "
            "If you are blocked: send_message to your PM saying exactly what blocks you.")
    if number >= LOOP_ANSWERS_PER_TASK:
        tail += "\nIf you loop once more on this task, you will be moved to worker_report."
    return head + advice + tail


def answer_loop_in_place(agent: Any, kind: str, result: Optional[str] = None) -> Optional[str]:
    """The notice for a looping worker that stays in its work state, or None (not a worker at work, or the task had its
    answers: then it goes to worker_report as before)."""
    try:
        if getattr(agent, "agent_type", None) != "worker" or getattr(agent, "state", None) not in _WORK_STATES:
            return None
        task = str(getattr(agent, "active_task_id", None) or getattr(agent, "current_task_id", None) or "-")
        counts = getattr(agent, "_loop_answers", None)
        if not isinstance(counts, dict):
            counts = {}
        number = counts.get(task, 0) + 1
        if number > LOOP_ANSWERS_PER_TASK:
            return None
        counts[task] = number
        agent._loop_answers = counts
        notice = loop_notice(agent, kind, number, result)
        logger.warning(f"loop_answer: '{getattr(agent, 'agent_id', '?')}' looped ({kind}) on task {task}; answered in "
                       f"'{agent.state}' ({number}/{LOOP_ANSWERS_PER_TASK}) instead of moving it to worker_report.")
        return notice
    except Exception as err:
        logger.debug(f"answer_loop_in_place failed: {err}")
        return None

# END OF FILE src/agents/loop_answer.py
