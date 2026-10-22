# START OF FILE src/llm_providers/tool_call_format.py
"""
Native tool calls in the OpenAI chat format (Round 2z) - used by the OpenRouter and OpenAI providers.

2026-10-22 16:23 (OpenRouter, nvidia/nemotron-3.5-lightning:free): the OpenRouter provider never sent the tool schemas
("OpenRouterProvider received tools/tool_choice arguments, but they will be ignored") although every prompt tells the
agents to use native tool calls. Admin AI answered the user's project request with the text
'{"name": "request_state", "arguments": {"state": "planning"}}'; it was shown to the user as a reply and nothing happened.

* openai_chat_messages(messages): the framework's history in the OpenAI format. The framework keeps an assistant's calls
  as {"id", "name", "arguments": {...}}; the API wants {"id", "type": "function", "function": {"name", "arguments":
  "<JSON text>"}}. Every call must be answered by the 'tool' messages right after it, and a 'tool' message must answer
  one of them - a condensed history can break both, so unanswered calls are left out and an orphaned result becomes a
  user message. A history that ends with the assistant's own reply gets a short user note (some models would continue
  that reply instead of starting a new one).
* add_tool_call_delta / finished_tool_calls: the streamed pieces of tool calls joined into complete calls. Arguments that
  are not valid JSON are passed on as text, so the agent hears what was wrong instead of the call vanishing.
* json_text_tool_calls(text, names): a reply that is nothing but a tool call written as JSON text (the observed reply
  above), as native calls - only for known tool names.
"""
import json
import logging
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

CONTINUE_NOTE = ("[Framework]: Your previous message is complete and in the history above. Continue with your next step "
                 "now: a tool call or your reply.")
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def _call_id(call: Dict[str, Any]) -> str:
    value = call.get("id")
    return str(value) if value else f"call_{uuid.uuid4().hex[:12]}"


def openai_tool_call(call: Any) -> Optional[Dict[str, Any]]:
    """One call of the framework's history ({"id", "name", "arguments"} or already OpenAI-shaped) in the OpenAI format."""
    if not isinstance(call, dict):
        return None
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = function.get("name") or call.get("name")
    if not name:
        return None
    arguments = function.get("arguments", call.get("arguments", {}))
    if not isinstance(arguments, str):
        try:
            arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
        except (TypeError, ValueError):
            arguments = json.dumps({"value": str(arguments)})
    return {"id": _call_id(call), "type": "function", "function": {"name": str(name), "arguments": arguments}}


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def openai_chat_messages(messages: Iterable[Any]) -> List[Dict[str, Any]]:
    """The framework's message history as valid OpenAI chat messages (see the module docstring)."""
    source = [m for m in (messages or []) if isinstance(m, dict) and m.get("role")]
    result: List[Dict[str, Any]] = []
    index = 0
    while index < len(source):
        message = source[index]
        role = str(message.get("role"))
        if role == "assistant":
            calls = [c for c in (openai_tool_call(c) for c in (message.get("tool_calls") or [])) if c]
            answered = set()
            follow = index + 1
            while follow < len(source) and source[follow].get("role") == "tool":
                answered.add(str(source[follow].get("tool_call_id") or ""))
                follow += 1
            kept = [c for c in calls if c["id"] in answered]
            if len(kept) < len(calls):
                logger.debug(f"openai_chat_messages: left out {len(calls) - len(kept)} tool call(s) without a result.")
            text = _text(message.get("content"))
            if kept:
                result.append({"role": "assistant", "content": text or None, "tool_calls": kept})
                kept_ids = {c["id"] for c in kept}
                for tool_message in source[index + 1:follow]:
                    call_id = str(tool_message.get("tool_call_id") or "")
                    if call_id in kept_ids:
                        result.append({"role": "tool", "tool_call_id": call_id, "content": _text(tool_message.get("content"))})
                        kept_ids.discard(call_id)  # a second result for the same call is not allowed
                    else:
                        result.append(_tool_result_as_user(tool_message))
                index = follow
                continue
            if text.strip():
                result.append({"role": "assistant", "content": text})
            index += 1
            continue
        if role == "tool":
            result.append(_tool_result_as_user(message))  # no assistant call right before it
        elif role in ("system", "user"):
            result.append({"role": role, "content": _text(message.get("content"))})
        else:
            result.append({"role": "user", "content": _text(message.get("content"))})
        index += 1
    if result and result[-1].get("role") == "assistant":
        result.append({"role": "user", "content": CONTINUE_NOTE})
    return result


def _tool_result_as_user(message: Dict[str, Any]) -> Dict[str, Any]:
    name = message.get("name") or "tool"
    return {"role": "user", "content": f"--- Tool Response ({name}) ---\n{_text(message.get('content'))}\n-----------------------"}


def add_tool_call_delta(accumulator: Dict[int, Dict[str, Any]], delta_call: Any) -> None:
    """Adds one streamed tool-call piece (an OpenAI SDK object or a dict) to accumulator {index: call}."""
    def field(obj: Any, name: str) -> Any:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    position = field(delta_call, "index")
    position = int(position) if isinstance(position, int) else len(accumulator)
    function = field(delta_call, "function")
    entry = accumulator.setdefault(position, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
    if field(delta_call, "id"):
        entry["id"] = field(delta_call, "id")
    if function is not None:
        if field(function, "name"):
            entry["function"]["name"] += str(field(function, "name"))
        if field(function, "arguments"):
            entry["function"]["arguments"] += str(field(function, "arguments"))


def finished_tool_calls(accumulator: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The complete calls in order: arguments parsed from JSON when possible, else passed on as the text received."""
    calls: List[Dict[str, Any]] = []
    for position in sorted(accumulator):
        entry = accumulator[position]
        name = entry["function"].get("name")
        if not name:
            continue
        text = entry["function"].get("arguments") or ""
        try:
            arguments: Any = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            logger.warning(f"Tool call '{name}': arguments are not valid JSON; passing them on as text: {text[:200]!r}")
            arguments = text
        calls.append({"id": entry.get("id") or f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                      "function": {"name": name, "arguments": arguments}})
    return calls


def json_text_tool_calls(text: Any, known_names: Iterable[str]) -> List[Dict[str, Any]]:
    """
    Native calls for a reply that is ONLY tool calls written as JSON text - {"name": ..., "arguments": {...}} (or
    "parameters"), a list of them, optionally in a ```json fence - and only for known tool names; else [].
    """
    if not isinstance(text, str) or not text.strip():
        return []
    body = text.strip()
    fenced = _FENCE_RE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    if not body.startswith(("{", "[")):
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    names = set(known_names)
    calls: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"name", "arguments", "parameters", "id", "type"}:
            return []
        name = item.get("name")
        arguments = item.get("arguments", item.get("parameters", {}))
        if not isinstance(name, str) or name not in names or not isinstance(arguments, (dict, str)):
            return []
        calls.append({"id": f"text_call_{uuid.uuid4().hex[:12]}", "function": {"name": name, "arguments": arguments}})
    return calls

# END OF FILE src/llm_providers/tool_call_format.py
