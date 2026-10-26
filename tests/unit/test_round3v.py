# START OF FILE tests/unit/test_round3v.py
"""
Round 3v: what an agent is told about its tools is ONE consistent set of rules.

The system prompt of every (agent type, state), the native tools that state offers, the tools the executor allows, the
tools' own help and every framework / Guardian notice must agree:
* the [TOOL USE] line "Tools you can call in this state" names exactly the tools the state offers (src/agents/tool_guidance.py);
* a prompt names only tools, actions, parameters and states that exist (and only tools offered in that state, unless the line
  says they are not available or belong to another state);
* the offered tools never exceed what the executor allows (tool_activity.state_tool_allowlist), a PM is never offered
  code_editor and request_state lists only the agent type's own states;
* notices name native calls in words: no bare `{"action": ...}` JSON text, no XML tool tags, no state of another agent type;
* a Guardian intervention that moves the agent to another state says which state and what to do there, and its guidance is
  not removed by its own context clean-up.
"""
import ast
import asyncio
import json
import logging
import re
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def executor():
    logging.disable(logging.CRITICAL)
    import src.agents  # noqa: F401 - the tools import src.agents; importing the executor first is circular
    from src.tools.executor import ToolExecutor
    yield ToolExecutor()
    logging.disable(logging.NOTSET)


@pytest.fixture(scope="module")
def wm():
    from src.agents.workflow_manager import AgentWorkflowManager
    return AgentWorkflowManager()


def _agent(agent_type, state):
    return types.SimpleNamespace(
        agent_id="X1", agent_type=agent_type, persona="P", state=state, _config_system_prompt="",
        agent_config={"config": {}}, message_history=[], initial_plan_description="Build the thing.",
        current_task_description="Build the thing.", provider_name="ollama-local-test", model="m", role="Developer")


def _manager(agent, executor):
    state_manager = MagicMock()
    state_manager.get_agent_team.return_value = "team_P"
    state_manager.get_agents_in_team.return_value = [agent]
    return types.SimpleNamespace(state_manager=state_manager, current_project="P", current_session="S",
                                 agents={agent.agent_id: agent}, bootstrap_agents=[], tool_executor=executor)


@pytest.fixture(scope="module")
def rendered(executor, wm):
    """{(agent type, state): system prompt} for every mapped state, with the real prompts.yaml and the real tools."""
    from src.config.settings import settings
    assert settings.NATIVE_TOOL_CALLING_ENABLED, "the audit checks the default native tool calling"
    logging.disable(logging.CRITICAL)
    prompts = {}
    for (agent_type, state), key in wm._prompt_map.items():
        if key == "default_system_prompt":
            continue
        agent = _agent(agent_type, state)
        prompts[(agent_type, state)] = wm.get_system_prompt(agent, _manager(agent, executor))
    logging.disable(logging.NOTSET)
    return prompts


def _offered(agent_type, state, executor):
    from src.agents.tool_guidance import offered_tool_names, offers_request_state
    names = set(offered_tool_names(agent_type, state, executor.tools, skip=["resolve_concern"]))
    return names | ({"request_state"} if offers_request_state(agent_type, state) else set())


def _actions(executor):
    """tool -> the actions its schema names."""
    out = {}
    for name, tool in executor.tools.items():
        props = tool.get_json_schema()["function"]["parameters"].get("properties", {})
        action = props.get("action", {})
        text = " ".join([action.get("description") or ""] + list(action.get("enum") or []))
        out[name] = (set(re.findall(r"'(\w+)'", text)) | set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text))
                     | set(action.get("enum") or []))
    return out


def _params(executor):
    out = {name: set(tool.get_json_schema()["function"]["parameters"].get("properties", {}))
           for name, tool in executor.tools.items()}
    out["request_state"] = {"state", "task_id", "task_description"}
    return out


NEGATION = re.compile(r"\b(?:not|never|NOT|NEVER|cannot|can't|don't|Do not|DO NOT|refused|refuses|no )\b|"
                      r"other states?|another state|states that offer|When you create a worker|only in '|in 'worker_work'")
TOOL_MENTION = re.compile(r"`(\w+)`|'(\w+)' tool|\bthe (\w+) tool\b")
STATE_ARG = re.compile(r"\bstate\s*=\s*['\"](\w+)['\"]")
ACTION_ARG = re.compile(r"\baction\s*[=:]\s*[`'\"]?(\w+)|\baction '(\w+)'|`(\w+)` action\b|action `(\w+)`")
PARAM_ARG = re.compile(r"`(\w+)=['\"]")


# =========================================================================================== 1. the [TOOL USE] block
def test_every_prompt_names_exactly_the_offered_tools(rendered, executor):
    for (agent_type, state), prompt in rendered.items():
        offered = _offered(agent_type, state, executor)
        line = next((l for l in prompt.splitlines() if f"Tools you can call in this state ('{state}'):" in l), None)
        if not offered:
            assert line is None and "No tools are offered in this state" in prompt, (agent_type, state)
            continue
        assert line is not None, (agent_type, state)
        listed = line.split(":**", 1)[1].split(".", 1)[0].strip().split(", ")
        assert set(listed) == offered, (agent_type, state, set(listed) ^ offered)
        assert prompt.splitlines().count("[TOOL USE]") == 1, (agent_type, state)   # admin_work had the block twice


def test_the_file_rule_fits_the_agent_type(rendered):
    from src.agents.tool_guidance import ADMIN_FILE_RULE, FILE_CHANGE_RULE, PM_FILE_RULE
    for (agent_type, state), prompt in rendered.items():
        assert "ALWAYS use `code_editor` for modifying existing code" not in prompt   # the old rule, also told to the PM
        if agent_type == "pm":
            assert FILE_CHANGE_RULE not in prompt and "code_editor" not in prompt.split("[TOOL USE]")[-1].split("[IMPORTANT]")[0]
            if state != "pm_build_team_tasks" and state != "pm_startup":
                assert PM_FILE_RULE in prompt
        if agent_type == "admin":
            assert ADMIN_FILE_RULE in prompt
        if agent_type == "worker" and state in ("worker_work", "worker_test"):
            assert FILE_CHANGE_RULE in prompt
        if agent_type == "worker" and state == "worker_decompose":
            assert FILE_CHANGE_RULE not in prompt and "you create and change files in 'worker_work'" in prompt


# =========================================================================================== 2. names in the prompts
def test_prompts_name_only_offered_tools(rendered, executor):
    tools = set(executor.tools) | {"request_state"}
    problems = []
    for (agent_type, state), prompt in rendered.items():
        offered = _offered(agent_type, state, executor)
        for line in prompt.splitlines():
            if NEGATION.search(line) or "Tools you can call" in line:
                continue
            for match in TOOL_MENTION.finditer(line):
                name = next(group for group in match.groups() if group)
                if name in tools and name not in offered:
                    problems.append(f"{agent_type}/{state}: `{name}` is not offered here :: {line.strip()[:140]}")
    assert not problems, "\n".join(problems)


def test_prompts_name_only_real_states_actions_and_parameters(rendered, executor, wm):
    actions, params = _actions(executor), _params(executor)
    all_actions = set().union(*actions.values())
    problems = []
    for (agent_type, state), prompt in rendered.items():
        for line in prompt.splitlines():
            for requested in STATE_ARG.findall(line):
                if not wm.is_valid_state(agent_type, requested):
                    problems.append(f"{agent_type}/{state}: state '{requested}' is no {agent_type} state :: {line.strip()[:140]}")
            named = {next(g for g in m.groups() if g) for m in TOOL_MENTION.finditer(line)} & set(actions)
            pool = set().union(*(actions[t] for t in named)) if named else all_actions
            for match in ACTION_ARG.finditer(line):
                action = next(g for g in match.groups() if g)
                if action not in pool and action not in actions:   # "`file_system` action 'write'": a tool, then its action
                    problems.append(f"{agent_type}/{state}: action '{action}' :: {line.strip()[:140]}")
            param_pool = set().union(*(params[t] for t in named | {"request_state"}))
            for name in PARAM_ARG.findall(line):
                if name not in param_pool:
                    problems.append(f"{agent_type}/{state}: parameter '{name}' :: {line.strip()[:140]}")
    assert not problems, "\n".join(problems)


def test_no_invented_tool_or_action_names_anywhere(executor):
    """Names that never existed: manage_team 'add_agent', a 'browser_subagent' tool, file_system 'read_with_lines'."""
    texts = [(ROOT / "prompts.yaml").read_text(encoding="utf-8")]
    texts += [tool.get_detailed_usage() + json.dumps(tool.get_json_schema()) for tool in executor.tools.values()]
    texts += [text for path in NOTICE_MODULES for _line, text in _strings(path)
              # a string that IS the wrong name is data that recognises it (manage_team maps 'add_agent' to 'create_agent')
              if text.strip() not in ("add_agent", "read_with_lines", "browser_subagent")]
    joined = "\n".join(texts)
    for name in (r"\badd_agent\b", r"\bbrowser_subagent\b", r"\bread_with_lines\b"):
        assert not re.search(name, joined), name


# =========================================================================================== 3. offered vs allowed
def test_offered_tools_never_exceed_the_executor(executor, wm):
    from src.agents.core import build_request_state_schema
    from src.agents.tool_activity import state_tool_allowlist
    from src.agents.tool_guidance import offered_tool_names
    for agent_type, states in wm._valid_states.items():
        for state in states:
            offered = set(offered_tool_names(agent_type, state, executor.tools))
            allowed = state_tool_allowlist(agent_type, state)
            if allowed is not None:
                assert offered <= set(allowed), (agent_type, state, offered - set(allowed))
            if agent_type == "pm":
                assert "code_editor" not in offered
            schema = build_request_state_schema(agent_type, state, states)
            if schema is None:
                assert (agent_type, state) == ("pm", "pm_startup")
                continue
            listed = schema["function"]["parameters"]["properties"]["state"]["description"]
            listed = listed.split("Valid states for you: ")[1].rstrip(".").split(", ")
            assert set(listed) <= set(states) and state not in listed, (agent_type, state, listed)


def test_tool_information_lists_the_tools_of_the_current_state(executor):
    from src.tools.tool_information import ToolInformationTool
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_build_team_tasks")
    manager = types.SimpleNamespace(agents={"PM1": pm}, tool_executor=executor, current_project="P",
                                    state_manager=MagicMock(), cg_escalation=None)
    tool = ToolInformationTool()
    listing = asyncio.run(tool.execute(agent_id="PM1", agent_sandbox_path=Path("."), manager=manager, action="list_tools"))
    message = listing["message"]
    assert "in state 'pm_build_team_tasks'" in message and "**manage_team**" in message
    assert "**project_management**" not in message and "**code_editor**" not in message
    assert "Not available in this state" in message and "project_management" in message.split("Not available in this state")[1]
    info = asyncio.run(tool.execute(agent_id="PM1", agent_sandbox_path=Path("."), manager=manager, action="get_info",
                                    tool_name="project_management"))
    assert info["message"].startswith("Note: `project_management` is not available in your current state 'pm_build_team_tasks'")
    wrong = asyncio.run(tool.execute(agent_id="PM1", agent_sandbox_path=Path("."), manager=manager, action="get_info",
                                     tool_name="list_tools"))
    assert "call the `tool_information` tool with action='list_tools'" in wrong["message"] and '{"action"' not in wrong["message"]


# =========================================================================================== 4. notices
NOTICE_MODULES = [
    "src/agents/cycle_handler.py", "src/agents/cycle_components/agent_health_monitor.py",
    "src/agents/cycle_components/next_step_scheduler.py", "src/agents/tool_guidance.py", "src/tools/error_handler.py",
    "src/tools/tool_information.py", "src/agents/assignable_work.py", "src/agents/tool_activity.py",
    "src/workflows/pm_kickoff_workflow.py", "src/tools/manage_team.py", "src/tools/github_tool.py",
]
BARE_JSON_CALL = re.compile(r'\{\s*\\?"action\\?"\s*:')


def _strings(path):
    """(line, text) of every string literal (f-strings with '{}' for their fields), docstrings left out."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.JoinedStr):
            yield node.lineno, "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def test_notices_never_show_a_call_as_bare_json_or_xml():
    problems = []
    for path in NOTICE_MODULES:
        for line, text in _strings(path):
            if re.search(r"\*\*(?:Tool Name|Sub-Action|Action|Available Sub-Actions)", text):
                continue   # a tool's own help: its examples are the arguments of that tool
            for match in BARE_JSON_CALL.finditer(text):
                # a JSON object must say whose arguments it shows (a tool's own help and examples do)
                before = text[max(0, match.start() - 120):match.start()]
                if not re.search(r"tool|Example|example|[Aa]rguments", before):
                    problems.append(f"{path}:{line}: {text.strip()[:120]}")
            if re.search(r"<(tool_call|send_message|project_management|manage_team|file_system|code_editor)>", text) \
                    and path != "src/agents/cycle_handler.py":   # cycle_handler only DETECTS such text
                problems.append(f"{path}:{line}: XML tool tag :: {text.strip()[:120]}")
            if re.search(r"XML (?:format|tags must|block)|<tool_call> tags", text):
                problems.append(f"{path}:{line}: XML wording :: {text.strip()[:120]}")
    assert not problems, "\n".join(problems)


def test_notices_request_only_real_states(wm):
    real = set().union(*map(set, wm._valid_states.values()))
    problems = []
    for path in NOTICE_MODULES:
        for line, text in _strings(path):
            for state in STATE_ARG.findall(text):
                if state not in real and state not in ("x", "X"):
                    problems.append(f"{path}:{line}: state='{state}' :: {text.strip()[:120]}")
    assert not problems, "\n".join(problems)


def test_removed_admin_generators_are_gone():
    from src.agents.cycle_handler import AgentCycleHandler
    assert not hasattr(AgentCycleHandler, "_generate_admin_work_completion_message")
    assert not hasattr(AgentCycleHandler, "_generate_empty_response_guidance")


def test_cycle_handler_notices():
    from src.agents.cycle_handler import (admin_false_completion_text, pm_completion_directive, watchdog_intervention_text,
                                          worker_breaker_next_step)
    gate = admin_false_completion_text(3)
    assert "3 PENDING tasks" in gate and "the `project_management` tool with action='list_tasks'" in gate
    assert "admin_work" not in gate and "run_command" not in gate
    manage = pm_completion_directive("pm_manage")
    assert "state='pm_audit'" in manage and "pm_standby" not in manage and '"action"' not in manage
    audit = pm_completion_directive("pm_audit")
    assert "state='pm_standby'" in audit and "state='pm_manage'" in audit and "send_message" in audit
    worker = types.SimpleNamespace(agent_type="worker", state="worker_work", active_task_id="t-1", current_task_id=None)
    step = worker_breaker_next_step(worker)
    assert "state='worker_work' and task_id='t-1'" in step and "force_overwrite" not in step
    text = watchdog_intervention_text(types.SimpleNamespace(agent_type="worker", state="worker_work",
                                                            _cycles_without_transition=12))
    assert "worker_wait" not in text and "'worker_report'" in text


def test_error_handler_names_its_help_call():
    from src.tools.error_handler import ErrorType, tool_error_handler
    for error_type in ErrorType:
        response = tool_error_handler.generate_enhanced_error_response(
            error_type, "file_system", attempted_action="lst", tool_args={"action": "lst"},
            agent_context={"agent_id": "W1", "agent_type": "admin", "agent_state": "work"},
            available_actions=["list", "read"], tool_schema={"parameters": [{"name": "filename", "required": True}]})
        text = tool_error_handler.format_error_for_agent(response)
        assert "XML formatting" not in text and "list_tools', 'search_knowledge" not in text
        for match in BARE_JSON_CALL.finditer(text):
            assert "Arguments of the `" in text[max(0, match.start() - 60):match.start()], text


# =========================================================================================== 5. the Guardian
def _monitor(executor):
    from src.agents.cycle_components.agent_health_monitor import ConstitutionalGuardianHealthMonitor
    manager = MagicMock()
    manager.tool_executor = executor
    manager.cg_escalation = None
    manager.send_to_ui = AsyncMock()
    manager.current_session_db_id = None
    return ConstitutionalGuardianHealthMonitor(manager)


@pytest.mark.parametrize("agent_type,state,expected", [
    ("worker", "worker_work", "worker_wait"), ("pm", "pm_activate_workers", "pm_manage"), ("admin", "work", "admin_standby")])
def test_a_reset_names_the_new_state_and_offers_its_tools(executor, agent_type, state, expected):
    from src.agents.tool_guidance import guardian_reset_state
    monitor = _monitor(executor)
    agent = types.SimpleNamespace(agent_id="A1", agent_type=agent_type, state=state, current_task_id="t-9",
                                  active_task_id=None, message_history=[{"role": "system", "content": "prompt"}])
    recovery = {"type": "empty_response_violation", "actions": [
        {"action": "inject_guidance", "message": monitor._generate_empty_response_guidance(agent, {})},
        {"action": "clear_problematic_context", "keep_last_n_messages": 3},
        {"action": "provide_available_tools", "include_examples": True}]}
    monitor._add_reset_note(agent, recovery)
    assert recovery["reset_state"] == expected == guardian_reset_state(agent_type, "empty_response_violation")
    guidance = recovery["actions"][0]["message"]
    assert f"[Framework Override]: Your state is now '{expected}'." in guidance
    assert '{"action"' not in guidance and "state='conversation'" not in guidance
    if agent_type == "worker":
        assert "state='worker_decompose'" in guidance and "t-9" in guidance
    # the clean-up runs first, so the guidance survives (it used to be removed by the clean-up right after injection)
    agent.set_status = lambda status: None
    agent.set_state = lambda new_state: setattr(agent, "state", new_state)
    assert asyncio.run(monitor.execute_recovery_plan(agent, recovery))
    contents = [m["content"] for m in agent.message_history]
    assert guidance in contents
    tools_message = next(c for c in contents if c.startswith("[Constitutional Guardian]: The tools you can call"))
    assert f"in state '{expected}'" in tools_message and "XML format" not in tools_message
    if agent_type == "worker":
        assert "file_system" not in tools_message and "project_management" in tools_message


def test_guardian_guidance_texts_are_native_and_typed(executor):
    monitor = _monitor(executor)
    worker = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_work")
    progression = monitor._generate_state_progression_guidance(worker, {})
    assert "worker_wait" not in progression and "state='worker_report'" in progression
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage")
    pm_text = monitor._generate_state_progression_guidance(pm, {})
    assert "<send_message>" not in pm_text and "state='pm_audit'" in pm_text and '{"action"' not in pm_text
    loop = monitor._generate_tool_information_loop_guidance(worker, {})
    assert "AVAILABLE TOOLS:" not in loop and "state='conversation'" not in loop and "\\n" not in loop
    assert "<tool_call>" not in monitor._generate_action_encouragement_guidance(worker, {})
    assert "XML tags" not in monitor._generate_tool_execution_loop_guidance(worker, {})
    decompose = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_decompose")
    assert "`tool_information`" not in monitor._generate_tool_execution_loop_guidance(decompose, {})


def test_state_change_notes():
    from src.agents.tool_guidance import state_change_note
    assert "request_state' tool with state='worker_decompose'" in state_change_note("worker", "worker_wait")
    assert "action='list_tasks'" in state_change_note("pm", "pm_manage")
    assert state_change_note("admin", "admin_conversation").startswith("[Framework Override]: Your state is now 'admin_conversation'.")
    report = state_change_note("worker", "worker_report", "t-2")
    assert "state='worker_work' and task_id='t-2'" in report

# =========================================================================================== 6. collect.py v2.6
def _collect_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("te_collect", ROOT / "collect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_reads_the_request_payloads():
    collect = _collect_module()
    prompt = ("--- Current State: WORK ---\n- Your Agent ID: `W1`\n- Your Agent Type: `worker`\n"
              "- **Tools you can call in this state ('worker_work'):** code_editor, request_state.")
    messages = [
        {"role": "system", "content": prompt}, {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "code_editor", "arguments": {
            "action": "replace_chunks", "filename": "a.py", "chunks": [{"search": "x = 1", "replace": "x = 1"}]}}}]},
        {"role": "tool", "content": "Error: Chunk 1 changes NOTHING"},
        {"role": "system", "content": "[Framework Override]: Your state is now 'worker_wait'."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {
            "name": "project_management", "arguments": json.dumps({"action": "list_tasks"})}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"status": "success", "tasks": []})},
    ]
    header = "2026-10-26 10:0{}:00,001 - src.llm_providers.ollama_provider - DEBUG - OllamaProvider 'qwen': EXACT JSON payload being sent to http://h/api/chat (before request):"
    log = "\n".join([header.format(1)] + json.dumps({"messages": messages[:4], "tools": [
        {"function": {"name": "code_editor"}}]}, indent=2).splitlines() + [header.format(2)] + json.dumps(
        {"messages": messages, "tools": [{"function": {"name": "code_editor"}}]}, indent=2).splitlines()
        + ["2026-10-26 10:03:00,001 - src.agents.core - INFO - end"])
    data = collect.analyse_payloads(collect.parse_entries(log))
    assert data["payloads"] == 2
    assert data["counts"][("code_editor", "replace_chunks")] == [1, 1]         # counted once, failed
    assert data["counts"][("project_management", "list_tasks")] == [1, 0]
    calls = collect.build_tool_calls_report(data)
    assert "changes NOTHING" in calls and "'replace' repeats 'search' (they change nothing): 1" in calls
    report = collect.build_prompts_report(data)
    assert "worker  worker_work" in report and "[Framework Override]: Your state is now 'worker_wait'." in report
    assert "### worker / worker_work  agents W1" in report


# END OF FILE tests/unit/test_round3v.py
