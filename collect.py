#!/usr/bin/env python3
"""
TrippleEffect: Session & Log Collector  (v2: smart log reduction; v2.2: the session of the log;
v2.3: configuration and environment, with secrets redacted; v2.4: every project session of the log;
v2.5: keys that .env sets more than once; v2.6: full tool calls, system prompts and injected notices from the
request payloads of the DEBUG log; v2.7: tool results sent as user messages are found, Guardian calls are named)

Run from the TrippleEffect repository root:   python3 collect.py
Output directory: ~/Desktop/te_session_data     (change with --dest)

What it collects (same names as before, plus the new zz_ files):
  workspace__*          shared workspace files of the latest session (each capped at 200 KB)
  task_data__*          Taskwarrior data files of the latest session
  agent_session_data.json.txt
  zz_session_summary.txt        what was collected, sizes, and how much the log was reduced
  zz_events.txt                 NEW. Every INFO/WARNING/ERROR/CRITICAL line of the WHOLE run, one line each
                                (max 300 chars), without database noise and routine per-call boilerplate
                                (headers, scheduling, status flips). Read this first: it is the timeline.
  zz_llm_raw_outputs.txt        NEW. What each agent actually said ("Raw response before post-processing").
  zz_latest_session_log.txt     The log with the bulk removed (see below). Replaces the old head+tail copy.
  zz_log_error_scan.txt         WARNING/ERROR/CRITICAL entries, GROUPED: each distinct problem once with
                                its count, first/last time and (for errors) the traceback.
  zz_tool_errors_scan.txt       Tool execution failures, grouped the same way.
  zz_config_yaml.txt            NEW (v2.3). config.yaml (secret values redacted).
  zz_env_redacted.txt           NEW (v2.3). .env line by line; values of keys, tokens, passwords and anything that
                                looks like a secret are replaced by [REDACTED: set, N chars].
  zz_environment.txt            NEW (v2.3). Every setting the framework reads (os.getenv in src/) with the value it
                                really gets: .env first (src/main.py loads it with override=True), else this shell's
                                environment, else the code default. Plus Python / node / bash / Taskwarrior versions.
  zz_git_state.txt              NEW (v2.3). Branch, last commits, changed files and the local diff (redacted).
  zz_tool_calls_full.txt        NEW (v2.6). From the request payloads of the DEBUG log (each LLM call is logged with its
                                whole conversation): every tool call counted once per tool/action with its failures;
                                every distinct FAILED call with its full arguments and the tool result; code_editor
                                chunks whose 'replace' repeats 'search' (they change nothing).
  zz_prompts_and_injections.txt NEW (v2.6). The system prompt of each agent type / state (each variant once, later
                                variants as a diff), the tool names offered per state, and every framework / Guardian
                                message injected into a conversation, with how often and to whom.
  other__<Project>__*           NEW (v2.4). The workspace and task data of every OTHER project session the log worked
                                in (e.g. a second project the run created), named other__<Project>__workspace__... and
                                other__<Project>__task_data__...; --primary-only leaves them out.

How the log is reduced (nothing is cut by position, so the middle of a long run is never lost):
  * database/Taskwarrior debug chatter (aiosqlite, tasklib.backends, database_manager DEBUG) is dropped;
  * prompt dumps are de-duplicated: every agent call re-prints its whole conversation, so a continuation
    line that was already printed earlier is not printed again (new messages still show, with their role);
  * identical repeated one-line entries are shown a few times, then counted in a footer;
  * very long lines and long continuation blocks are capped.
Only if the result is still > 1 MB is it trimmed (first 250 KB + last 750 KB, with a visible marker).

Which session (v2.2, Round 2n): the project session the log itself worked in (its last "Setting context. Project: X,
Session: Y" line that is not DefaultProject); --session picks one by hand. Without either, the session with the most
recently changed file. (v2 took the newest shared_workspace FOLDER date, which only changes when a file is created
directly in it: on 2026-10-21 it exported Temp_Converter_CLI instead of the WiFi_Snake_Game run of the log.)

Secrets (v2.3): a value is redacted when its NAME is a key/token/secret/password (e.g. OPENROUTER_API_KEY_1,
GITHUB_ACCESS_TOKEN, SECRET_KEY - not WORKER_WORK_STATE_MAX_TOKENS) or when the VALUE looks like one (sk-..., ghp_...,
github_pat_..., xox?-..., AIza..., JWTs, long random strings, the password in user:password@host URLs). Check the files
before you share them if your .env holds secrets under unusual names.

The script only writes files it generates into the destination folder (earlier exports of the same
names are replaced). It never touches your repository.
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
GENERATED_PREFIXES = ("workspace__", "task_data__", "zz_", "other__")
GENERATED_NAMES = {"agent_session_data.json.txt"}

HEADER_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ - ([\w.]+) - (\w+) - (.*)$")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CALL_ID_RE = re.compile(r"call_[a-z0-9]+")
HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
BIGNUM_RE = re.compile(r"\d{4,}")
INDEX_RE = re.compile(r"^\s*\[\d+\]\s*")

NOISE_MODULES = {"aiosqlite", "tasklib.backends", "asyncio", "urllib3", "httpx", "httpcore", "multipart", "PIL"}
LEVELS_INFO_PLUS = {"INFO", "WARNING", "ERROR", "CRITICAL"}
LEVELS_WARN_PLUS = {"WARNING", "ERROR", "CRITICAL"}

MAX_FILE_COPY = 200_000
MAX_FINAL_LOG = 1_000_000
HEAD_KEEP, TAIL_KEEP = 250_000, 750_000

TOOL_ERR_RE = re.compile(
    r"TOOL_EXEC_|Tool Execution Failed|Tool Execution Blocked|tool_execution_complete_failure|ToolError|"
    r"Invalid action|State transition rejected|Auto-correct|Action .* is not supported|Missing .* parameter",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------------------
# log parsing
# --------------------------------------------------------------------------------------
class Entry:
    __slots__ = ("module", "level", "msg", "header", "cont")

    def __init__(self, module: str, level: str, msg: str, header: str):
        self.module, self.level, self.msg, self.header = module, level, msg, header
        self.cont: List[str] = []

    @property
    def stamp(self) -> str:
        return self.header[:23]


def parse_entries(text: str) -> List[Entry]:
    entries: List[Entry] = []
    current: Optional[Entry] = None
    for line in text.splitlines():
        m = HEADER_RE.match(line)
        if m:
            current = Entry(m.group(1), m.group(2), m.group(3), line)
            entries.append(current)
        elif current is not None:
            current.cont.append(line)
        else:  # preamble before the first timestamp
            current = Entry("?", "INFO", line, line)
            entries.append(current)
    return entries


def is_noise(e: Entry) -> bool:
    if e.module in NOISE_MODULES or e.module.split(".")[0] in NOISE_MODULES:
        return e.level not in LEVELS_WARN_PLUS
    if e.module.endswith("database_manager") and e.level == "DEBUG":
        return True
    return False


def norm_key(s: str) -> str:
    """Identity of a line for de-duplication. Only ids that differ on every call are normalised; numbers are NOT,
    so lines carrying measurements (durations, counts, sizes) are never mistaken for repeats."""
    s = UUID_RE.sub("U", s)
    s = CALL_ID_RE.sub("call_X", s)
    return HEX_RE.sub("0xH", s)


def clip(line: str, limit: int) -> str:
    return line if len(line) <= limit else f"{line[:limit]} ...[+{len(line) - limit} chars]"


# --------------------------------------------------------------------------------------
# reduced log
# --------------------------------------------------------------------------------------
def reduce_continuation(e: Entry, seen: set) -> List[str]:
    """Continuation lines to print for an entry (global de-duplication for DEBUG prompt dumps)."""
    if not e.cont:
        return []
    if e.level in ("ERROR", "CRITICAL"):
        return [clip(l, 400) for l in e.cont[:60]] + ([f"    ... [{len(e.cont) - 60} more lines]"] if len(e.cont) > 60 else [])
    if e.level == "WARNING":
        return [clip(l, 400) for l in e.cont[:25]] + ([f"    ... [{len(e.cont) - 25} more lines]"] if len(e.cont) > 25 else [])
    if e.level == "INFO":
        return [clip(l, 400) for l in e.cont[:8]] + ([f"    ... [{len(e.cont) - 8} more lines]"] if len(e.cont) > 8 else [])

    # DEBUG: print only continuation lines that were not printed before.
    out: List[str] = []
    short_buf: List[str] = []   # short structural lines (braces, "role": ...) kept only next to new content
    omitted = 0
    for line in e.cont:
        stripped = line.strip()
        if len(stripped) < 40:
            short_buf.append(line)
            if len(short_buf) > 3:
                short_buf.pop(0)
            continue
        key = norm_key(stripped)
        if key in seen:
            omitted += 1
            short_buf = []
            continue
        seen.add(key)
        out.extend(clip(l, 600) for l in short_buf)
        short_buf = []
        out.append(clip(line, 600))
        if len(out) >= 120:
            out.append("    ... [output capped]")
            break
    if omitted:
        out.append(f"    ... [{omitted} line(s) already shown earlier omitted]")
    return out


def build_reduced_log(entries: List[Entry]) -> Tuple[str, Dict[str, int]]:
    out: List[str] = []
    seen_lines: set = set()
    counts: Dict[Tuple[str, str, str], int] = {}
    suppressed: "OrderedDict[Tuple[str, str, str], List]" = OrderedDict()
    stats = {"entries": len(entries), "noise": 0, "suppressed": 0}
    for e in entries:
        if is_noise(e):
            stats["noise"] += 1
            continue
        key = (e.module, e.level, norm_key(e.msg)[:600])
        counts[key] = counts.get(key, 0) + 1
        limit = 3 if e.level == "DEBUG" else 6
        # suppress repeats of identical SINGLE-line entries (entries with a traceback/body are judged by header too,
        # but only after `limit` full copies were shown)
        if counts[key] > limit:
            stats["suppressed"] += 1
            info = suppressed.setdefault(key, [0, e.stamp, e.stamp, e.header])
            info[0] += 1
            info[2] = e.stamp
            continue
        out.append(clip(e.header, 700 if e.level != "DEBUG" else 300))
        out.extend(reduce_continuation(e, seen_lines))
    if suppressed:
        out.append("")
        out.append("=" * 100)
        out.append("SUPPRESSED REPEATS (identical entries shown several times above, then only counted)")
        out.append("=" * 100)
        for (module, level, _), (n, first, last, sample) in suppressed.items():
            out.append(f"x{n:<5} {level:<8} {module}  [{first[11:]} .. {last[11:]}]")
            out.append(f"        {clip(sample[26:], 260)}")
    return "\n".join(out) + "\n", stats


def trim_if_large(text: str) -> str:
    if len(text) <= MAX_FINAL_LOG:
        return text
    cut = len(text) - HEAD_KEEP - TAIL_KEEP
    return (
        text[:HEAD_KEEP]
        + f"\n\n... [TRIMMED {cut:,} chars from the middle of the reduced log; zz_events.txt has the complete timeline] ...\n\n"
        + text[-TAIL_KEEP:]
    )


# --------------------------------------------------------------------------------------
# other log views
# --------------------------------------------------------------------------------------
# Routine per-call boilerplate: carries no information beyond "a call happened" and would bury the timeline.
# (zz_events.txt only; the call start/finish lines "making API call" and "Response Status" are kept for latency.)
CHATTY_EVENT_RE = re.compile(
    r"^(Request Headers Sent|Response Headers Received|API call headers OK|Sending agent_raw_response event|"
    r"Sending tool_result event to UI|Successfully sent .* to UI via broadcast)|"
    r"stream_completion finished cleanly|Using model-native num_ctx|has a RAW template|Native tool calling enabled|"
    r"preparing request|schedule_cycle ASYNC called|Successfully created asyncio task|_schedule_new_cycle|"
    r"Closing agent_generator|Finished processing cycle attempt|Finished cycle logic|Status changed from|"
    r"WORKER_ACTIVATION_DEBUG|Executor: Executing tool"
)


def build_events(entries: List[Entry]) -> str:
    return "\n".join(
        clip(e.header, 300)
        for e in entries
        if e.level in LEVELS_INFO_PLUS and not is_noise(e) and not (e.level == "INFO" and CHATTY_EVENT_RE.search(e.msg))
    ) + "\n"


def build_raw_outputs(entries: List[Entry]) -> str:
    blocks = []
    for e in entries:
        if "Raw response before post-processing" in e.msg:
            body = [clip(l, 600) for l in e.cont[:60]]
            if len(e.cont) > 60:
                body.append(f"    ... [{len(e.cont) - 60} more lines]")
            blocks.append("\n".join([clip(e.header, 300)] + body))
    return "\n\n".join(blocks) + "\n" if blocks else "No raw LLM response blocks found in the log.\n"


def grouped_scan(entries: List[Entry], selector, title: str, max_chars: int = 150_000) -> str:
    groups: "OrderedDict[Tuple[str, str, str], List]" = OrderedDict()
    for e in entries:
        if is_noise(e) or not selector(e):
            continue
        key = (e.module, e.level, BIGNUM_RE.sub("N", norm_key(e.msg.splitlines()[0] if e.msg else ""))[:160])
        g = groups.get(key)
        if g is None:
            groups[key] = [1, e.stamp, e.stamp, e]
        else:
            g[0] += 1
            g[2] = e.stamp
    if not groups:
        return f"{title}: nothing matched.\n"
    out = [f"{title}: {sum(g[0] for g in groups.values())} entries, {len(groups)} distinct problem(s)", "=" * 100]
    for (module, level, _), (n, first, last, e) in groups.items():
        out.append(f"x{n}  {level}  {module}  first {first[5:]}  last {last[5:]}")
        out.append("  " + clip(e.msg, 700))
        cap = 40 if e.level in ("ERROR", "CRITICAL") else 8
        out.extend("  | " + clip(l, 400) for l in e.cont[:cap])
        if len(e.cont) > cap:
            out.append(f"  | ... [{len(e.cont) - cap} more lines]")
        out.append("-" * 100)
        if sum(len(x) + 1 for x in out) > max_chars:
            out.append("... [scan truncated at size limit; remaining groups omitted; see zz_events.txt]")
            break
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------
# v2.6: what the models were really sent (the DEBUG request payloads)
# --------------------------------------------------------------------------------------
# Every LLM call is logged at DEBUG level with its complete request: the system prompt, the whole conversation (with
# every tool call's full arguments and every tool result) and the tool schemas offered. From these:
#   zz_tool_calls_full.txt         - tool calls per tool/action and how many failed; every distinct FAILED call with its
#                                    full arguments and result; code_editor chunks whose 'replace' repeats 'search'
#   zz_prompts_and_injections.txt  - the system prompt per agent type and state (each variant once), the tools offered
#                                    per state, and every framework / Guardian message injected into a conversation
PAYLOAD_MARKERS = ("EXACT JSON payload being sent to", "FULL JSON equivalent of api_params being sent")
PAYLOAD_MODEL_RE = re.compile(r"Provider '([^']+)'")
AGENT_ID_RE = re.compile(r"Your Agent ID: `([^`]+)`")
AGENT_TYPE_RE = re.compile(r"Your Agent Type: `([^`]+)`")
STATE_NAME_RE = re.compile(r"(?:Tools you can call in this state|No tools are offered in this state) \('([\w-]+)'\)")
TOOL_RESPONSE_RE = re.compile(r"^--- Tool Response \(([^)]*)\) ---\n(.*?)(?:\n-{10,})?\s*$", re.DOTALL)
STATE_LABEL_RE = re.compile(r"---\s*(?:Current State|Worker Agent State|State):\s*([^\n]+?)\s*---")
VOLATILE_PROMPT_RE = re.compile(r"(Current Time \(UTC\): `)[^`]*`")
FAILURE_PREFIXES = ("[tool execution failed]", "[toolexec error", "[tool execution error", "[tool error", "error:")
CONTENT_TOOLS = {"file_system", "web_search", "zim_search", "knowledge_base", "codebase_search", "tool_information",
                 "asset_search", "github_tool", "system_help"}
EXECUTOR_REFUSAL_RE = re.compile(r"^error from tool '[\w.-]+': ", re.MULTILINE)
SEARCH_KEYS = ("search", "search_block", "search_text", "find", "old", "old_code")
REPLACE_KEYS = ("replace", "replace_block", "replace_text", "replacement", "new", "new_code", "new_text", "new_content",
                "content", "line_content", "text", "code", "replace_with", "replacement_text")
MAX_PAYLOAD_FILE = 1_500_000


def iter_payloads(entries: List[Entry]):
    """(timestamp, model, payload dict) for every logged request payload that parses as JSON."""
    for e in entries:
        if e.level != "DEBUG" or not any(marker in e.msg for marker in PAYLOAD_MARKERS):
            continue
        try:
            payload = json.loads("\n".join(e.cont))
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            model = PAYLOAD_MODEL_RE.search(e.msg)
            yield e.stamp, (model.group(1) if model else "?"), payload


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):  # content parts
        return "\n".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in value)
    return json.dumps(value, ensure_ascii=False)


def _arguments(raw) -> Dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {"_raw": raw}
        except ValueError:
            return {"_raw": raw}
    return {}


def result_failed(tool: str, content: str) -> bool:
    """The framework's rule (src/agents/tool_activity.is_tool_result_failure), copied so the script needs no imports."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        pass
    else:
        return isinstance(data, dict) and data.get("status") == "error"
    lowered = content.lower()
    if lowered.lstrip().startswith(FAILURE_PREFIXES) or EXECUTOR_REFUSAL_RE.search(lowered[:2000]):
        return True
    if tool in CONTENT_TOOLS:
        return False
    return "error" in lowered or "[tool execution failed]" in lowered


def _first_key(chunk: Dict, keys) -> Optional[str]:
    return next((key for key in keys if key in chunk), None)


def _chunks(arguments: Dict) -> List[Dict]:
    chunks = arguments.get("chunks") or arguments.get("replacements") or arguments.get("edits")
    if isinstance(chunks, str):
        try:
            chunks = json.loads(chunks, strict=False)
        except ValueError:
            return []
    if isinstance(chunks, dict):
        chunks = [chunks]
    return [c for c in chunks or [] if isinstance(c, dict)] if isinstance(chunks, list) else []


def _shown(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n...[+{len(text) - limit} chars]"


def _who(messages: List[Dict]) -> Tuple[str, str, str]:
    """(agent id, agent type, state) from the system prompt of a payload."""
    system = _text(messages[0].get("content")) if messages and messages[0].get("role") == "system" else ""
    agent = AGENT_ID_RE.search(system)
    agent_type = AGENT_TYPE_RE.search(system)
    state = STATE_NAME_RE.search(system) or STATE_LABEL_RE.search(system)
    if not agent:
        # v2.7: the Constitutional Guardian's own calls (reviews of messages, context summaries)
        if "summarization" in system:
            return "guardian", "guardian", "summary"
        if "Constitutional Guardian" in system:
            return "guardian", "guardian", "review"
    return (agent.group(1) if agent else "?", agent_type.group(1) if agent_type else "?",
            state.group(1).strip() if state else "?")


def _tool_result(message: Dict) -> Optional[Tuple[str, str]]:
    """
    v2.7: (tool name, result text) of a tool result in a payload, else None. The Ollama provider sends a result as a 'tool'
    message (with 'tool_name' since Round 3w) or - for a result whose call is not in the payload, and before Round 3w for
    qwen, llama and other models - as a USER message "--- Tool Response (name) ---".
    """
    role = message.get("role")
    content = _text(message.get("content"))
    if role == "tool":
        return str(message.get("name") or message.get("tool_name") or "?"), content
    if role == "user":
        match = TOOL_RESPONSE_RE.match(content)
        if match:
            return match.group(1), match.group(2)
    return None


def _injection_key(content: str) -> str:
    return BIGNUM_RE.sub("N", norm_key(content))[:3000]


def analyse_payloads(entries: List[Entry]) -> Dict:
    data = {"payloads": 0, "calls": OrderedDict(), "counts": {}, "prompts": OrderedDict(), "tools": OrderedDict(),
            "injections": OrderedDict(), "noop": []}
    seen_messages: set = set()
    for stamp, model, payload in iter_payloads(entries):
        data["payloads"] += 1
        messages = [m for m in payload["messages"] if isinstance(m, dict)]
        agent, agent_type, state = _who(messages)
        where = (agent_type, state)
        # the system prompt: each distinct text once per (agent type, state)
        if messages and messages[0].get("role") == "system":
            prompt = _text(messages[0].get("content"))
            normal = VOLATILE_PROMPT_RE.sub(r"\1...`", prompt).replace(f"`{agent}`", "`<agent>`")
            group = data["prompts"].setdefault(where, {"agents": set(), "first": stamp, "variants": OrderedDict()})
            group["agents"].add(agent)
            variant = group["variants"].setdefault(normal, [stamp, 0, prompt])
            variant[1] += 1
        # the tools offered in this call
        names = tuple(sorted(str((t.get("function") or {}).get("name") or t.get("name") or "?")
                             for t in payload.get("tools") or [] if isinstance(t, dict)))
        offered = data["tools"].setdefault(where, OrderedDict())
        offered[names] = offered.get(names, 0) + 1
        for index, message in enumerate(messages):
            role = message.get("role")
            content = _text(message.get("content"))
            key = (agent, role, norm_key(content)[:4000], json.dumps(message.get("tool_calls"), sort_keys=True, default=str)[:4000])
            if key in seen_messages:
                continue
            seen_messages.add(key)
            if index > 0 and _tool_result(message) is None and (
                    role == "system" or (role == "user" and content.lstrip().startswith("["))):
                injected = data["injections"].setdefault(_injection_key(content), {
                    "first": stamp, "count": 0, "agents": set(), "states": set(), "role": role, "text": content})
                injected["count"] += 1
                injected["agents"].add(agent)
                injected["states"].add(state)
            if role != "assistant" or not message.get("tool_calls"):
                continue
            later = messages[index + 1:]
            next_assistant = next((i for i, m in enumerate(later) if m.get("role") == "assistant"), len(later))
            results = [m for m in later[:next_assistant] if _tool_result(m) is not None]
            for position, call in enumerate(message["tool_calls"]):
                function = call.get("function") or {}
                tool = str(function.get("name") or "?")
                arguments = _arguments(function.get("arguments"))
                call_id = call.get("id")
                result = next((m for m in results if call_id and m.get("tool_call_id") == call_id), None)
                if result is None and position < len(results):
                    result = results[position]
                result_text = _tool_result(result)[1] if result else ""
                failed = bool(result) and result_failed(tool, result_text)
                action = str(arguments.get("action") or "-")
                count = data["counts"].setdefault((tool, action), [0, 0])
                count[0] += 1
                count[1] += 1 if failed else 0
                if failed:
                    signature = (tool, json.dumps(arguments, sort_keys=True, default=str)[:20000],
                                 BIGNUM_RE.sub("N", norm_key(result_text.splitlines()[0] if result_text else ""))[:300])
                    entry = data["calls"].setdefault(signature, {"first": stamp, "count": 0, "agents": set(),
                                                                "state": state, "model": model, "tool": tool,
                                                                "arguments": arguments, "result": result_text})
                    entry["count"] += 1
                    entry["agents"].add(agent)
                if tool == "code_editor":
                    noop = [c for c in _chunks(arguments)
                            if _first_key(c, SEARCH_KEYS) and _first_key(c, REPLACE_KEYS)
                            and c[_first_key(c, SEARCH_KEYS)] == c[_first_key(c, REPLACE_KEYS)]]
                    if noop:
                        filename = str(arguments.get("filename") or arguments.get("filepath") or arguments.get("path") or "")
                        before = next((_tool_result(m)[1] for m in reversed(messages[:index])
                                       if _tool_result(m) is not None and filename and filename in _text(m.get("content"))), "")
                        data["noop"].append((stamp, agent, state, arguments, result_text, before))
    return data


def build_tool_calls_report(data: Dict) -> str:
    out = [f"# Tool calls ({data['payloads']:,} request payloads read; each call counted once)", "",
           "## Calls per tool and action (failed / total)"]
    for (tool, action), (total, failed) in sorted(data["counts"].items(), key=lambda kv: (-kv[1][1], -kv[1][0])):
        out.append(f"  {failed:>5} / {total:<6} {tool}  action={action}")
    out += ["", f"## Failed calls: {len(data['calls'])} distinct (same tool, arguments and first result line = one)", ""]
    for number, entry in enumerate(sorted(data["calls"].values(), key=lambda v: v["first"]), 1):
        out.append("=" * 110)
        out.append(f"#{number}  x{entry['count']}  {entry['tool']}  first {entry['first']}  agents {', '.join(sorted(entry['agents']))}"
                   f"  state {entry['state']}  model {entry['model']}")
        out.append("--- arguments:")
        out.append(_shown(json.dumps(entry["arguments"], indent=1, ensure_ascii=False, default=str), 6000))
        out.append("--- result:")
        out.append(_shown(entry["result"], 4000))
        if sum(len(x) + 1 for x in out) > MAX_PAYLOAD_FILE:
            out.append("... [file size limit reached; later failed calls omitted]")
            break
    out += ["", f"## code_editor chunks whose 'replace' repeats 'search' (they change nothing): {len(data['noop'])}", ""]
    for number, (stamp, agent, state, arguments, result, before) in enumerate(data["noop"][:40], 1):
        out.append("=" * 110)
        out.append(f"#{number}  {stamp}  {agent} ({state})  file {arguments.get('filename') or arguments.get('filepath')}")
        out.append("--- arguments:")
        out.append(_shown(json.dumps(arguments, indent=1, ensure_ascii=False, default=str), 8000))
        out.append("--- result:")
        out.append(_shown(result or "(no result in the payload)", 2500))
        out.append("--- the last earlier tool result that names the file:")
        out.append(_shown(before or "(none in the payload)", 4000))
    if len(data["noop"]) > 40:
        out.append(f"... [{len(data['noop']) - 40} more]")
    return "\n".join(out) + "\n"


def build_prompts_report(data: Dict) -> str:
    import difflib
    out = [f"# System prompts, offered tools and injected messages ({data['payloads']:,} request payloads read)", "",
           "## Tools offered per agent type and state (tool names sent with the call: count)"]
    for (agent_type, state), offered in data["tools"].items():
        for names, count in offered.items():
            out.append(f"  {agent_type:<7} {state:<22} x{count:<5} {', '.join(names) or '(no tools)'}")
    out += ["", f"## Injected framework / Guardian messages: {len(data['injections'])} distinct "
                "(count = conversations it was added to; numbers and ids normalised)", ""]
    for entry in sorted(data["injections"].values(), key=lambda v: v["first"]):
        out.append("-" * 110)
        out.append(f"x{entry['count']}  first {entry['first']}  role {entry['role']}  agents {', '.join(sorted(entry['agents']))}"
                   f"  states {', '.join(sorted(entry['states']))}")
        out.append(_shown(entry["text"], 3000))
    out += ["", f"## System prompts: {len(data['prompts'])} agent type / state combination(s)", ""]
    for (agent_type, state), group in data["prompts"].items():
        variants = list(group["variants"].values())
        out.append("=" * 110)
        out.append(f"### {agent_type} / {state}  agents {', '.join(sorted(group['agents']))}  first {group['first']}  "
                   f"{len(variants)} variant(s)")
        first_stamp, count, prompt = variants[0]
        out.append(f"--- variant 1 (x{count}, first {first_stamp}):")
        out.append(_shown(prompt, 30000))
        for number, (stamp, count, text) in enumerate(variants[1:6], 2):
            diff = [line for line in difflib.unified_diff(prompt.splitlines(), text.splitlines(), lineterm="", n=0)
                    if not line.startswith(("---", "+++", "@@"))]
            out.append(f"--- variant {number} (x{count}, first {stamp}): {len(diff)} changed line(s) against variant 1")
            out.extend("    " + clip(line, 300) for line in diff[:40])
            if len(diff) > 40:
                out.append(f"    ... [{len(diff) - 40} more]")
        if len(variants) > 6:
            out.append(f"--- ... {len(variants) - 6} more variant(s)")
        if sum(len(x) + 1 for x in out) > MAX_PAYLOAD_FILE:
            out.append("... [file size limit reached; later prompts omitted]")
            break
    return "\n".join(out) + "\n"


def redact_report(text: str) -> Tuple[str, int]:
    """v2.6: the payload reports hold prompts and tool results - redacted like the git diff."""
    hidden = 0
    lines = []
    for line in text.splitlines():
        shown, n = redact_text_line(line)
        hidden += n
        lines.append(shown)
    return "\n".join(lines) + "\n", hidden


# --------------------------------------------------------------------------------------
# file helpers
# --------------------------------------------------------------------------------------
def flat_name(prefix: str, rel_path: Path) -> str:
    return f"{prefix}__" + "__".join(rel_path.parts) + ".txt"


def copy_capped(src: Path, dest: Path) -> int:
    data = src.read_bytes()
    if len(data) > MAX_FILE_COPY:
        half = MAX_FILE_COPY // 2
        marker = f"\n\n... [TRIMMED {len(data) - MAX_FILE_COPY:,} bytes from the middle] ...\n\n".encode()
        data = data[:half] + marker + data[-half:]
    dest.write_bytes(data)
    return src.stat().st_size


CONTEXT_RE = re.compile(r"Setting context\. Project: (?P<project>[^,\n]+), Session: (?P<session>[^\s,]+)")


def session_dirs_from_log(root: Path, text: str) -> List[Path]:
    """
    v2.4: every project session folder the log worked in ('Setting context' lines that are not DefaultProject), each once,
    ordered by its LAST context line - the last one is the session the run ended in.
    """
    projects_dir = root / "projects"
    found: List[Path] = []
    for match in CONTEXT_RE.finditer(text or ""):
        project, session = match.group("project").strip(), match.group("session").strip().rstrip(".")
        if not project or project == "DefaultProject":
            continue
        for name in (project, project.replace(" ", "_").strip()[:50].rstrip("_")):
            candidate = projects_dir / name / session
            if candidate.is_dir():
                if candidate in found:
                    found.remove(candidate)
                found.append(candidate)
                break
    return found


def session_dir_from_log(root: Path, text: str) -> Optional[Path]:
    """The project session folder the log worked in: its LAST 'Setting context' line that is not DefaultProject."""
    sessions = session_dirs_from_log(root, text)
    return sessions[-1] if sessions else None


def copy_session_files(session_dir: Path, dest: Path, prefix: str, summary: List[str]) -> int:
    """v2.4: the workspace files and task data of one more session, as <prefix>workspace__... / <prefix>task_data__..."""
    copied = 0
    ws_dir = session_dir / "shared_workspace"
    if ws_dir.is_dir():
        for walk_root, dirs, files in os.walk(ws_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in sorted(files):
                src_f = Path(walk_root) / f
                rel = src_f.relative_to(ws_dir)
                try:
                    size = copy_capped(src_f, dest / flat_name(f"{prefix}workspace", rel))
                    summary.append(f"  - {rel} ({size} bytes)")
                    copied += 1
                except Exception as exc:
                    summary.append(f"  - {rel} (copy failed: {exc})")
    task_dir = session_dir / "task_data"
    if task_dir.is_dir():
        for item in sorted(task_dir.iterdir()):
            if item.is_file():
                try:
                    size = copy_capped(item, dest / f"{prefix}task_data__{item.name}.txt")
                    summary.append(f"  - task_data/{item.name} ({size} bytes)")
                    copied += 1
                except Exception as exc:
                    summary.append(f"  - task_data/{item.name}: copy failed ({exc})")
    if not copied:
        summary.append("  (no files)")
    return copied


def _newest_file_mtime(folder: Path) -> float:
    newest = 0.0
    for walk_root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in files:
            try:
                newest = max(newest, (Path(walk_root) / name).stat().st_mtime)
            except OSError:
                continue
    return newest


def get_latest_session_dir(root: Path) -> Optional[Path]:
    """The session folder with the most recently changed FILE (a folder's own date changes only for direct entries)."""
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return None
    sessions = []
    for ws in projects_dir.rglob("shared_workspace"):
        try:
            if ws.is_dir():
                session = ws.parent
                sessions.append((max(_newest_file_mtime(session), session.stat().st_mtime), str(session), session))
        except OSError:
            continue
    sessions.sort()
    return sessions[-1][2] if sessions else None


def get_latest_log_file(root: Path) -> Optional[Path]:
    logs_dir = root / "logs"
    if not logs_dir.is_dir():
        return None
    log_files = []
    for f in logs_dir.rglob("*.log"):
        try:
            if f.is_file():
                log_files.append((f.stat().st_mtime, f))
        except OSError:
            continue
    log_files.sort()
    return log_files[-1][1] if log_files else None


def clear_previous_export(dest: Path) -> None:
    """Remove only files this script generates (earlier exports); anything else in the folder is kept."""
    if not dest.is_dir():
        return
    for item in dest.iterdir():
        if item.is_file() and (item.name.startswith(GENERATED_PREFIXES) or item.name in GENERATED_NAMES):
            item.unlink()


def kb(n: int) -> str:
    return f"{n / 1024:,.0f} KB"


# --------------------------------------------------------------------------------------
# configuration and environment (v2.3)
# --------------------------------------------------------------------------------------
SECRET_NAME_RE = re.compile(
    r"(?:^|[_.-])(?:API_?KEY|KEY|TOKEN|ACCESS_?KEY|SECRET(?:_?KEY)?|PASSWORD|PASSWD|PASS|PWD|AUTH|CREDENTIALS?|"
    r"PRIVATE_?KEY|COOKIE|SALT|BEARER)(?:_?\d+)?$|PASSWORD|SECRET", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(
    r"^(?:sk-|sk_|rk_|pk_live|ghp_|gho_|ghs_|ghu_|github_pat_|glpat-|xox[abprs]-|AIza|ya29\.|hf_|r8_|eyJ[\w-]{10,}\.)")
URL_PASSWORD_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^/@:\s]*):(?P<password>[^/@\s]+)@", re.IGNORECASE)
ENV_LINE_RE = re.compile(r"^(?P<lead>\s*(?:export\s+)?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<eq>\s*=\s*)(?P<rest>.*)$")
YAML_LINE_RE = re.compile(r"^(?P<lead>\s*-?\s*)(?P<name>[A-Za-z_][\w.-]*)(?P<eq>\s*:\s+)(?P<rest>.+)$")
# settings the framework finds by prefix (src/config/settings.py: provider keys and URLs such as OPENROUTER_API_KEY_1)
DYNAMIC_SETTING_PREFIXES = ("OPENAI", "OPENROUTER", "LITELLM", "ANTHROPIC", "GOOGLE", "DEEPSEEK", "VLLM", "OLLAMA")
GETENV_RE = re.compile(r"os\.(?:getenv|environ\.get)\(\s*[\"'](?P<name>[A-Z][A-Z0-9_]*)[\"']|"
                       r"os\.environ\[\s*[\"'](?P<name2>[A-Z][A-Z0-9_]*)[\"']\s*\]")


def looks_secret_value(value: str) -> bool:
    """A value that is a secret by its shape: a known key prefix, or a long random string without spaces or paths."""
    v = value.strip().strip("'\"")
    if not v:
        return False
    if SECRET_VALUE_RE.match(v):
        return True
    if len(v) >= 32 and re.fullmatch(r"[A-Za-z0-9_\-+=/.]+", v) and not re.search(r"[/\\]", v.strip("=")):
        classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
        return classes >= 2
    return False


def split_value(rest: str) -> Tuple[str, str]:
    """(value, trailing comment) of an env/yaml value: quotes respected; ' # ...' after the value is a comment."""
    text = rest.rstrip("\n")
    stripped = text.lstrip()
    if stripped[:1] in ("'", '"'):
        quote = stripped[0]
        end = stripped.find(quote, 1)
        if end > 0:
            return stripped[:end + 1], stripped[end + 1:]
    match = re.search(r"\s+#", text)
    if match:
        return text[:match.start()], text[match.start():]
    if text.lstrip().startswith("#"):
        return "", text
    return text, ""


def with_comment(shown: str, comment: str) -> str:
    """The shown value and its trailing comment, separated by a space."""
    if comment and not comment[:1].isspace():
        return f"{shown} {comment}"
    return f"{shown}{comment}"


def redact_value(name: str, value: str) -> Tuple[str, bool]:
    """(shown value, redacted?) for one setting."""
    bare = value.strip()
    unquoted = bare.strip("'\"")
    if SECRET_NAME_RE.search(name) or looks_secret_value(unquoted):
        if not unquoted:
            return "[empty]", False
        return f"[REDACTED: set, {len(unquoted)} chars]", True
    cleaned, count = URL_PASSWORD_RE.subn(r"\g<scheme>\g<user>:[REDACTED]@", value)
    return cleaned, bool(count)


def redact_text_line(line: str) -> Tuple[str, int]:
    """Redacts secret-looking tokens and URL passwords anywhere in a free text line (diffs). Returns (line, count)."""
    count = 0
    line, n = URL_PASSWORD_RE.subn(r"\g<scheme>\g<user>:[REDACTED]@", line)
    count += n

    def token(match: "re.Match") -> str:
        nonlocal count
        if looks_secret_value(match.group(0)):
            count += 1
            return f"[REDACTED: {len(match.group(0))} chars]"
        return match.group(0)
    line = re.sub(r"[A-Za-z0-9_\-+=/.]{20,}", token, line)
    assignment = ENV_LINE_RE.match(line.lstrip("+- ")) or YAML_LINE_RE.match(line.lstrip("+- "))
    if assignment and SECRET_NAME_RE.search(assignment.group("name")):
        value, comment = split_value(assignment.group("rest"))
        shown, hidden = redact_value(assignment.group("name"), value)
        if hidden:
            count += 1
            prefix = line[:len(line) - len(line.lstrip("+- "))]
            line = f"{prefix}{assignment.group('lead')}{assignment.group('name')}{assignment.group('eq')}{with_comment(shown, comment)}"
    return line, count


def redact_env_file(text: str) -> Tuple[str, Dict[str, str], int]:
    """(.env with secrets redacted, {name: shown value}, number of redactions). v2.5: + the keys set more than once."""
    out, values, hidden_count = [], {}, 0
    assignments: Dict[str, List[Tuple[int, str]]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = ENV_LINE_RE.match(line)
        if not match or line.lstrip().startswith("#"):
            shown_line, n = redact_text_line(line) if not line.lstrip().startswith("#") else (line, 0)
            out.append(shown_line)
            hidden_count += n
            continue
        value, comment = split_value(match.group("rest"))
        shown, hidden = redact_value(match.group("name"), value)
        hidden_count += int(hidden)
        values[match.group("name")] = shown
        assignments.setdefault(match.group("name"), []).append((number, shown.strip()))
        out.append(f"{match.group('lead')}{match.group('name')}{match.group('eq')}{with_comment(shown, comment)}")
    duplicates = env_duplicates(assignments)
    if duplicates:
        out += ["", "# " + "=" * 98, "# collect.py v2.5: KEYS SET MORE THAN ONCE - python-dotenv uses the LAST value:"]
        out += [f"#   {line}" for line in duplicates]
    return "\n".join(out) + "\n", values, hidden_count


def env_duplicates(assignments: Dict[str, List[Tuple[int, str]]]) -> List[str]:
    """v2.5: one line per key assigned more than once with different values (2026-10-25: OLLAMA_MAX_CTX_CAP 24576 / 32768)."""
    lines = []
    for name, items in assignments.items():
        if len(items) > 1 and len({value for _n, value in items}) > 1:
            shown = ", ".join(f"line {number}: {value}" for number, value in items)
            lines.append(f"{name} ({shown}) -> line {items[-1][0]} applies")
    return lines


def redact_yaml(text: str) -> Tuple[str, int]:
    """config.yaml with secret values redacted (line by line: 'name: value' pairs and free text)."""
    out, hidden_count = [], 0
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            out.append(line)
            continue
        match = YAML_LINE_RE.match(line)
        if match:
            value, comment = split_value(match.group("rest"))
            if value.strip() in ("|", ">", "|-", ">-"):
                out.append(line)
                continue
            shown, hidden = redact_value(match.group("name"), value)
            if hidden:
                hidden_count += 1
                out.append(f"{match.group('lead')}{match.group('name')}{match.group('eq')}{with_comment(shown, comment)}")
                continue
        shown_line, n = redact_text_line(line)
        hidden_count += n
        out.append(shown_line)
    return "\n".join(out) + "\n", hidden_count


def framework_setting_names(root: Path) -> List[str]:
    """Names of environment variables the framework reads (os.getenv / os.environ.get / os.environ[...] in src/)."""
    names = set()
    for path in (root / "src").rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        try:
            for match in GETENV_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
                names.add(match.group("name") or match.group("name2"))
        except OSError:
            continue
    return sorted(names)


def env_example_values(root: Path) -> Dict[str, str]:
    """{name: value} of the uncommented settings in .env.example (the documented suggestions)."""
    path = root / ".env.example"
    values: Dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = ENV_LINE_RE.match(line)
            if match and not line.lstrip().startswith("#"):
                values[match.group("name")] = split_value(match.group("rest"))[0].strip()
    return values


def command_output(command: List[str], cwd: Optional[Path] = None, limit: int = 4000) -> str:
    try:
        result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as err:
        return f"(not available: {type(err).__name__})"
    text = (result.stdout or "") + (("\n" + result.stderr) if result.returncode and result.stderr else "")
    return clip(text.strip(), limit) if text.strip() else f"(no output, exit {result.returncode})"


def build_environment_report(root: Path, env_values: Dict[str, str], env_found: bool) -> Tuple[str, int]:
    """zz_environment.txt: the effective value of every setting the framework reads, plus tool versions."""
    names = framework_setting_names(root)
    example = env_example_values(root)
    for name in list(env_values) + [n for n in os.environ if n.startswith(DYNAMIC_SETTING_PREFIXES)]:
        if name not in names:
            names.append(name)
    rows, hidden = [], 0
    width = max((len(n) for n in names), default=10)
    for name in sorted(names):
        if name in env_values:
            shown, source = env_values[name], ".env"
        elif name in os.environ:
            shown, was_hidden = redact_value(name, os.environ[name])
            hidden += int(was_hidden)
            source = "shell"
        else:
            suggestion = example.get(name)
            shown = "(not set - the code default applies" + (f"; .env.example suggests {suggestion!r})" if suggestion is not None else ")")
            source = "-"
        rows.append(f"  {name:<{width}}  [{source:<5}]  {shown}")
    tools = [
        ("python", sys.version.split()[0] + f" ({platform.platform()})"),
        ("venv python", command_output([str(root / ".venv" / "bin" / "python"), "--version"]) if (root / ".venv" / "bin" / "python").exists() else "(no .venv)"),
        ("node", command_output(["node", "--version"]) if shutil.which("node") else "NOT INSTALLED (JavaScript files are not syntax-checked)"),
        ("bash", command_output(["bash", "--version"]).splitlines()[0] if shutil.which("bash") else "not installed"),
        ("task (Taskwarrior)", command_output(["task", "--version"]) if shutil.which("task") else "NOT INSTALLED"),
        ("git", command_output(["git", "--version"]) if shutil.which("git") else "not installed"),
    ]
    text = ["# TrippleEffect environment", "",
            "Effective value of every setting the framework reads. Source: [.env] = your .env (src/main.py loads it with",
            "override=True, so it wins), [shell] = this shell's environment (only used when .env does not set it; the",
            "shell that starts the framework may differ), [-] = not set. Secret values are redacted.", ""]
    if not env_found:
        text.append("NOTE: no .env file found in the repository root.\n")
    text += [f"## Settings ({len(names)})"] + rows + ["", "## Tools"] + [f"  {k:<20} {v}" for k, v in tools]
    return "\n".join(text) + "\n", hidden


def build_git_state(root: Path) -> Tuple[str, int]:
    """zz_git_state.txt: branch, last commits, status and the local diff (redacted, capped)."""
    if not shutil.which("git") or not (root / ".git").exists():
        return "No git repository (or git not installed).\n", 0
    parts = ["# git state", "", "## Branch", command_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], root),
             "", "## Last commits", command_output(["git", "log", "--oneline", "-12"], root),
             "", "## Status (changed / untracked files)", command_output(["git", "status", "--short"], root, 20000),
             "", "## Diff of tracked files (git diff HEAD)"]
    diff = command_output(["git", "diff", "HEAD", "--", ".", ":(exclude)*.db", ":(exclude)*.log"], root, 150_000)
    hidden = 0
    lines = []
    for line in diff.splitlines():
        shown, n = redact_text_line(line)
        hidden += n
        lines.append(shown)
    return "\n".join(parts + lines) + "\n", hidden


def collect_configuration(root: Path, dest: Path, summary: List[str]) -> None:
    """v2.3: config.yaml, .env (redacted), the effective environment and the git state."""
    summary.append("\n## Configuration & environment (secrets redacted)")
    config = root / "config.yaml"
    if config.is_file():
        text, hidden = redact_yaml(config.read_text(encoding="utf-8", errors="replace"))
        (dest / "zz_config_yaml.txt").write_text(text, encoding="utf-8")
        summary.append(f"Generated: zz_config_yaml.txt ({kb(len(text.encode()))}, {hidden} value(s) redacted)")
    else:
        summary.append("config.yaml: not found in the repository root")
    env_path = root / ".env"
    env_values: Dict[str, str] = {}
    if env_path.is_file():
        text, env_values, hidden = redact_env_file(env_path.read_text(encoding="utf-8", errors="replace"))
        (dest / "zz_env_redacted.txt").write_text(text, encoding="utf-8")
        summary.append(f"Generated: zz_env_redacted.txt ({len(env_values)} setting(s), {hidden} value(s) redacted)")
        if "KEYS SET MORE THAN ONCE" in text:  # v2.5
            summary.append("WARNING: .env sets some keys more than once (the LAST value applies) - see the end of "
                           "zz_env_redacted.txt")
    else:
        summary.append(".env: not found in the repository root")
    report, hidden = build_environment_report(root, env_values, env_path.is_file())
    (dest / "zz_environment.txt").write_text(report, encoding="utf-8")
    summary.append(f"Generated: zz_environment.txt ({hidden} shell value(s) redacted)")
    git_text, hidden = build_git_state(root)
    (dest / "zz_git_state.txt").write_text(git_text, encoding="utf-8")
    summary.append(f"Generated: zz_git_state.txt ({kb(len(git_text.encode()))}, {hidden} value(s) redacted)")


# --------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Collect TrippleEffect session data with a reduced log.")
    ap.add_argument("--root", default=".", help="TrippleEffect repository root (default: current directory)")
    ap.add_argument("--dest", default=str(Path.home() / "Desktop" / "te_session_data"))
    ap.add_argument("--log", default=None, help="Specific log file (default: newest *.log under logs/)")
    ap.add_argument("--session", default=None, help="Specific session folder, e.g. projects/My_Project/startup_123 "
                                                     "(default: the session the log worked in)")
    ap.add_argument("--raw", action="store_true", help="Also write zz_raw_log_head_tail.txt (old behaviour: 400 KB head + 600 KB tail)")
    ap.add_argument("--no-config", action="store_true", help="Do not collect config.yaml, .env, environment and git state")
    ap.add_argument("--primary-only", action="store_true",
                    help="Only the session the run ended in, not the other project sessions of the log (v2.4)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    dest = Path(args.dest).expanduser()
    if not (root / "src").is_dir():
        sys.exit("ERROR: Run this script from the TrippleEffect repository root (or pass --root).")

    dest.mkdir(parents=True, exist_ok=True)
    clear_previous_export(dest)
    summary = ["# TrippleEffect Latest Session Summary\n"]

    # 1. Session files --------------------------------------------------------------
    log_file = Path(args.log) if args.log else get_latest_log_file(root)
    raw_text: Optional[str] = None
    if log_file and log_file.is_file():
        raw_text = log_file.read_text(encoding="utf-8", errors="replace")
    if args.session:
        session_dir = Path(args.session)
        if not session_dir.is_absolute():
            session_dir = root / session_dir
        if not session_dir.is_dir():
            sys.exit(f"ERROR: --session folder not found: {session_dir}")
        how = "given with --session"
    else:
        session_dir = session_dir_from_log(root, raw_text or "")
        how = "the session the log worked in"
        if session_dir is None:
            session_dir = get_latest_session_dir(root)
            how = "most recently changed session (the log names no project session)"
    if not session_dir:
        summary.append("No project sessions found under projects/.")
        print("WARNING: No project sessions found under projects/.")
    else:
        try:
            rel_sess = session_dir.relative_to(root)
        except ValueError:
            rel_sess = session_dir
        summary.append(f"Latest Session: {rel_sess}  ({how})\n")
        print(f"Collecting from session: {rel_sess} ({how})")

        ws_dir = session_dir / "shared_workspace"
        summary.append("## Shared Workspace Files:")
        count = 0
        if ws_dir.is_dir():
            for walk_root, dirs, files in os.walk(ws_dir):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for f in sorted(files):
                    src_f = Path(walk_root) / f
                    rel = src_f.relative_to(ws_dir)
                    try:
                        size = copy_capped(src_f, dest / flat_name("workspace", rel))
                        summary.append(f"  - {rel} ({size} bytes)")
                        count += 1
                    except Exception as exc:
                        summary.append(f"  - {rel} (copy failed: {exc})")
            if count == 0:
                summary.append("  (shared_workspace is empty)")
        else:
            summary.append("  (shared_workspace folder does not exist)")

        task_dir = session_dir / "task_data"
        if task_dir.is_dir():
            summary.append("\n## Task Data:")
            for item in sorted(task_dir.iterdir()):
                if item.is_file():
                    try:
                        size = copy_capped(item, dest / f"task_data__{item.name}.txt")
                        summary.append(f"  - {item.name} ({size} bytes)")
                    except Exception as exc:
                        summary.append(f"  - {item.name}: copy failed ({exc})")

        sess_json = session_dir / "agent_session_data.json"
        if sess_json.is_file():
            size = copy_capped(sess_json, dest / "agent_session_data.json.txt")
            summary.append(f"\nCopied agent_session_data.json ({size} bytes)")

        # v2.4: the other project sessions the log worked in (2026-10-22 the run ended in the copy
        # 'Multiplayer_Snake_Game_Enhancements' and the export missed the game itself)
        if not args.session and not args.primary_only:
            others = [other for other in session_dirs_from_log(root, raw_text or "") if other != session_dir]
            for other in others:
                try:
                    rel_other = other.relative_to(root)
                except ValueError:
                    rel_other = other
                label = other.parent.name
                if sum(1 for o in others if o.parent.name == label) > 1:
                    label = f"{label}_{other.name}"  # two sessions of one project: the session tells them apart
                project_label = re.sub(r"[^\w.-]", "_", label)
                summary.append(f"\n## Other session of the log: {rel_other}  (files: other__{project_label}__*)")
                print(f"Also collecting the other session of the log: {rel_other}")
                copy_session_files(other, dest, f"other__{project_label}__", summary)

    # 2. Log ------------------------------------------------------------------------
    if log_file and log_file.is_file() and raw_text is not None:
        try:
            rel_log = log_file.relative_to(root)
        except ValueError:
            rel_log = log_file
        raw_size = len(raw_text.encode("utf-8", errors="replace"))
        print(f"Reading log: {log_file.name} ({kb(raw_size)})")
        entries = parse_entries(raw_text)

        events = build_events(entries)
        raw_out = build_raw_outputs(entries)
        reduced, stats = build_reduced_log(entries)
        reduced = trim_if_large(reduced)
        tool_scan = grouped_scan(
            entries, lambda e: e.level in LEVELS_WARN_PLUS and bool(TOOL_ERR_RE.search(e.msg)), "Tool execution failures")
        err_scan = grouped_scan(entries, lambda e: e.level in LEVELS_WARN_PLUS, "Warnings / errors / critical")

        files = OrderedDict([
            ("zz_events.txt", events),
            ("zz_llm_raw_outputs.txt", raw_out),
            ("zz_latest_session_log.txt", reduced),
            ("zz_log_error_scan.txt", err_scan),
            ("zz_tool_errors_scan.txt", tool_scan),
        ])
        # v2.6: the request payloads (DEBUG log): full tool calls, prompts per state, offered tools, injected notices
        payload_note = ""
        try:
            payload_data = analyse_payloads(entries)
            if payload_data["payloads"]:
                for name, builder in (("zz_tool_calls_full.txt", build_tool_calls_report),
                                      ("zz_prompts_and_injections.txt", build_prompts_report)):
                    files[name], _hidden = redact_report(builder(payload_data))
            else:
                payload_note = ("No request payloads in the log (they are logged at DEBUG level): zz_tool_calls_full.txt "
                                "and zz_prompts_and_injections.txt were not written.")
        except Exception as exc:  # the other files are still written
            payload_note = f"Request payload analysis failed: {type(exc).__name__}: {exc}"
        for name, content in files.items():
            (dest / name).write_text(content, encoding="utf-8")

        first = next((e.stamp for e in entries if HEADER_RE.match(e.header)), "?")
        last = next((e.stamp for e in reversed(entries) if HEADER_RE.match(e.header)), "?")
        summary.append(f"\n## Latest Log: {rel_log}")
        summary.append(f"Source: {kb(raw_size)}, {stats['entries']:,} entries, covering {first} .. {last}")
        summary.append(f"Reduced log: {kb(len(reduced.encode('utf-8', errors='replace')))} "
                       f"({stats['noise']:,} noise entries dropped, {stats['suppressed']:,} repeated entries counted in the footer)")
        for name in files:
            summary.append(f"Generated: {name} ({kb(len(files[name].encode('utf-8', errors='replace')))})")
        if payload_note:
            summary.append(payload_note)
        summary.append("Read zz_events.txt first: it is the complete, untruncated timeline of the run.")

        if args.raw:
            half_head, half_tail = 400_000, 600_000
            body = raw_text
            if len(body) > half_head + half_tail:
                body = body[:half_head] + f"\n\n... [TRUNCATED {len(body) - half_head - half_tail:,} chars] ...\n\n" + body[-half_tail:]
            (dest / "zz_raw_log_head_tail.txt").write_text(body, encoding="utf-8")
            summary.append("Generated: zz_raw_log_head_tail.txt (--raw)")
    else:
        summary.append("\nNo log file found under logs/ (and no --log given).")
        print("WARNING: no log file found.")

    # 3. Configuration & environment (v2.3) -------------------------------------------
    if not args.no_config:
        try:
            collect_configuration(root, dest, summary)
        except Exception as exc:  # the session data above is still exported
            summary.append(f"\nConfiguration collection failed: {type(exc).__name__}: {exc}")
            print(f"WARNING: configuration collection failed: {exc}")

    (dest / "zz_session_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"\n[DONE] All session data exported to: {dest}")
    total = 0
    print("\nFiles ready:")
    for f in sorted(dest.iterdir()):
        if f.is_file():
            total += f.stat().st_size
            print(f"  - {f.name} ({f.stat().st_size:,} bytes)")
    print(f"\nTotal: {kb(total)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
