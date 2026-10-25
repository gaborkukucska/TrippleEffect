# START OF FILE src/agents/context_compaction.py
"""
Round 3n: make room in an agent's history WITHOUT an LLM summary, before the summarizer is called.

2026-10-24 23:35 - 2026-10-25 03:49 (WiFi_Multiplayer_Snake_Game, Python, 32k-token window): 167 context summaries in 4
hours. W3 was summarized 75 times (median every 3 cycles), W1 44 times, W2 42 times. A worker's fixed prompt (system
prompt, tool definitions, reports) is ~9,000 tokens; the summary is triggered at ~22,500. Right after a summary the prompt
was still ~15,000 tokens, because the 6 messages it keeps held the agent's own whole-file rewrites (code_editor chunks of up
to 27,085 characters) and whole-file reads (src/server.py: 32,657 characters). One more read and the next summary was due.
W3 read tests/test_player_manager.py 55 times and src/player_manager.py 44 times: every summary removed what it had read.

compact_history() shrinks what the agent no longer needs, in messages older than the newest `keep_recent`:
* the payload of its own WRITE calls (file contents, code_editor chunks) - the result says whether it was applied and the
  file holds the current text;
* a file READ that is out of date: the same file was read again later, or changed later by a successful write;
* the output of a command / test run that was run again later (the newest output stays; the older keeps its first and
  last lines).
Messages keep their order, roles and tool-call ids, so every call still has its result. Never raises.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

COMPACTION_MARKER = "compacted by the framework"
MIN_PAYLOAD_CHARS = 400          # a write payload shorter than this is kept
MIN_RESULT_CHARS = 1500          # a read / command result shorter than this is kept
DEFAULT_KEEP_RECENT = 4
_PAYLOAD_KEYS = ("content", "chunks", "replace_lines", "replace_text", "replace", "text", "code", "new_content",
                 "find_text", "search", "insert_text", "lines", "data", "file_content")
_PATH_KEYS = ("filename", "path", "file_path", "filepath", "file", "file_name", "target_file")
_RANGE_KEYS = ("start_line", "end_line", "line_start", "line_end", "from_line", "to_line")


def _arguments(call: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """(arguments as a dict, True if they were a JSON string)."""
    args = call.get("arguments")
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return (parsed, True) if isinstance(parsed, dict) else ({}, True)
        except (ValueError, TypeError):
            return {}, True
    return (args, False) if isinstance(args, dict) else ({}, False)


def _path_of(args: Dict[str, Any]) -> Optional[str]:
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("./") or value.strip()
    return None


def _read_key(name: str, args: Dict[str, Any]) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    """(path, line range) of a call that reads a file's content, else None (lists/exists are not reads of content)."""
    from src.agents.tool_activity import is_file_read_call
    if not is_file_read_call(name, args):
        return None
    action = str(args.get("action", "")).strip().lower()
    if action in ("list", "list_directory", "list_files", "tree", "exists", "list_with_lines"):
        return None
    path = _path_of(args)
    if not path:
        return None
    return path, tuple(args.get(key) for key in _RANGE_KEYS)


def _command_key(name: str, args: Dict[str, Any]) -> Optional[str]:
    from src.agents.tool_activity import is_command_run_call
    if not is_command_run_call(name, args):
        return None
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    text = " ".join(command.split())
    for prefix in ("cd /workspace && ", "cd /workspace; ", "cd . && "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return f"{name}:{text}"


def _compact_payload(value: Any) -> Any:
    size = len(value) if isinstance(value, str) else len(json.dumps(value, default=str))
    if size < MIN_PAYLOAD_CHARS or (isinstance(value, str) and COMPACTION_MARKER in value):
        return value
    return f"<<{size:,} characters of new text were sent here and applied or refused - {COMPACTION_MARKER}; " \
           "the file holds the current text>>"


def _compacted_call(call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The call with its long write payload replaced, or None if nothing changes."""
    from src.agents.tool_activity import _written_path
    if _written_path(call) is None:
        return None
    args, was_string = _arguments(call)
    new_args = dict(args)
    changed = False
    for key in _PAYLOAD_KEYS:
        if key in new_args:
            compacted = _compact_payload(new_args[key])
            if compacted is not new_args[key]:
                new_args[key] = compacted
                changed = True
    if not changed:
        return None
    return {**call, "arguments": json.dumps(new_args) if was_string else new_args}


def _read_stub(path: str, size: int, reason: str) -> str:
    return (f"[Earlier read of '{path}' ({size:,} characters) removed - {COMPACTION_MARKER}: {reason}. Read the file again "
            "if you need its current text.]")


def _command_stub(command: str, content: str) -> str:
    lines = [line for line in content.splitlines() if line.strip()]
    head = lines[0] if lines else ""
    tail = lines[-4:] if len(lines) > 5 else lines[1:]
    shown = "\n".join([head, "..."] + tail) if len(lines) > 5 else "\n".join(lines)
    return (f"[Older output of `{command[:120]}` shortened - {COMPACTION_MARKER}: you ran it again later.]\n"
            f"{shown[:1200]}")


def compact_history(history: Optional[List[Dict[str, Any]]],
                    keep_recent: int = DEFAULT_KEEP_RECENT) -> Tuple[List[Dict[str, Any]], int]:
    """
    (the compacted copy of the history, characters saved). Only messages before the newest `keep_recent` change; the
    input list is not modified. Never raises (returns the history unchanged and 0).
    """
    messages = list(history or [])
    try:
        from src.agents.tool_activity import is_tool_result_failure
        calls: Dict[Any, Tuple[int, str, Dict[str, Any], Dict[str, Any]]] = {}   # id -> (assistant index, name, args, call)
        results: Dict[Any, int] = {}                                            # id -> tool message index
        for index, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant":
                for call in msg.get("tool_calls") or []:
                    if isinstance(call, dict) and call.get("id") is not None:
                        args, _ = _arguments(call)
                        calls[call["id"]] = (index, str(call.get("name") or ""), args, call)
            elif msg.get("role") == "tool" and msg.get("tool_call_id") is not None:
                results[msg["tool_call_id"]] = index

        # the newest read of each file (any range) / of each range, the newest change of each file, the newest run of each
        # command - by the index of the tool RESULT (a write counts when its result shows success)
        last_read_any: Dict[str, int] = {}
        last_read_full: Dict[str, int] = {}
        last_read_range: Dict[Tuple[str, Tuple[Any, ...]], int] = {}
        last_write: Dict[str, int] = {}
        last_run: Dict[str, int] = {}
        from src.agents.tool_activity import _written_path
        for call_id, (a_index, name, args, call) in calls.items():
            r_index = results.get(call_id)
            if r_index is None:
                continue
            content = messages[r_index].get("content")
            read = _read_key(name, args)
            if read:
                last_read_any[read[0]] = max(last_read_any.get(read[0], -1), r_index)
                last_read_range[read] = max(last_read_range.get(read, -1), r_index)
                if not any(read[1]):
                    last_read_full[read[0]] = max(last_read_full.get(read[0], -1), r_index)
                continue
            written = _written_path(call)
            if written and not is_tool_result_failure(name, content):
                path = written.strip().lstrip("./") or written.strip()
                last_write[path] = max(last_write.get(path, -1), r_index)
                continue
            command = _command_key(name, args)
            if command:
                last_run[command] = max(last_run.get(command, -1), r_index)

        cutoff = len(messages) - max(0, int(keep_recent))
        saved = 0
        out = list(messages)
        for index in range(max(0, cutoff)):
            msg = messages[index]
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                new_calls, changed = [], False
                for call in msg.get("tool_calls") or []:
                    compacted = _compacted_call(call) if isinstance(call, dict) else None
                    if compacted is not None:
                        saved += len(json.dumps(call, default=str)) - len(json.dumps(compacted, default=str))
                        new_calls.append(compacted)
                        changed = True
                    else:
                        new_calls.append(call)
                if changed:
                    out[index] = {**msg, "tool_calls": new_calls}
                continue
            if msg.get("role") != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or len(content) < MIN_RESULT_CHARS or COMPACTION_MARKER in content:
                continue
            entry = calls.get(msg.get("tool_call_id"))
            if entry is None:
                continue
            _a_index, name, args, _call = entry
            read = _read_key(name, args)
            if read:
                path = read[0]
                reason = None
                if last_read_range.get(read, -1) > index or last_read_full.get(path, -1) > index:
                    reason = "you read it again later"
                elif last_write.get(path, -1) > index:
                    reason = "the file was changed after this read, so this text is out of date"
                if reason:
                    stub = _read_stub(path, len(content), reason)
                    out[index] = {**msg, "content": stub}
                    saved += len(content) - len(stub)
                continue
            command = _command_key(name, args)
            if command and last_run.get(command, -1) > index:
                stub = _command_stub(command.split(":", 1)[1], content)
                if len(stub) < len(content):
                    out[index] = {**msg, "content": stub}
                    saved += len(content) - len(stub)
        return out, max(0, saved)
    except Exception as err:
        logger.debug(f"compact_history failed: {err}")
        return messages, 0


def compaction_placeholder_refusal(tool_name: Optional[str], arguments: Any) -> Optional[str]:
    """
    Round 3n: the refusal for a write whose text is the framework's placeholder (a small model may copy it from its
    compacted history instead of writing the real text), else None.
    """
    try:
        from src.agents.tool_activity import _written_path
        call = {"name": tool_name, "arguments": arguments}
        if _written_path(call) is None:
            return None
        args, _ = _arguments(call)
        for key in _PAYLOAD_KEYS:
            value = args.get(key)
            text = value if isinstance(value, str) else json.dumps(value, default=str) if value is not None else ""
            if COMPACTION_MARKER in text:
                return (f"NOTHING was written: the '{key}' you sent contains the framework's placeholder ('... "
                        f"{COMPACTION_MARKER} ...'), which only marks where an EARLIER text was removed from your history "
                        "- it is not file content. Read the file, then send the real, complete new text.")
    except Exception as err:
        logger.debug(f"compaction_placeholder_refusal failed: {err}")
    return None

# END OF FILE src/agents/context_compaction.py
