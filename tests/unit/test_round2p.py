# START OF FILE tests/unit/test_round2p.py
"""
Round 2p: fixtures from the 2026-10-21 17:59-18:33 run (WiFi_Snake_Game_Multiplayer, session startup_1792576742,
qwen3.5:9b on one remote Ollama server). Round 2o worked (0 imitated tool calls, 7 Guardian reviews, 0 false concerns), but:

* 18:24:25-18:24:29 - the history sent for PM1 ENDED with its own assistant text ("...I should assign them immediately").
  Ollama's qwen3.5 renderer treats a final assistant message as a prefill, so the model only continued it and stopped after
  1 token. Such "empty" replies made the Guardian block PM1 three times and report it to Admin AI.
* 18:32:13 - Admin AI answered those reports with create_agent(target_agent_id='PM2', role='Project Manager (...)'), which
  made worker W3.
* 18:13-18:33 - every summary call thought for all of its 800 tokens and returned nothing (14 times); one empty chunk failed
  the whole summarization, which was retried every cycle while W1's prompt grew to 26k tokens.
* 18:15-18:33 - the watchdog wrote "You were detected as IDLE" into W1's history 5 times while its cycle was summarizing.
* 18:20-18:31 - PM1 was parked in pm_standby 4 times although Task 3 was ready and W2 idle (assigned 10 minutes late);
  18:10:09 it went to pm_audit with 6 of 7 tasks open.
* 18:01:07 - "depends_on": ["task_1..6"] was dropped, so the README task started before any code existed.
* 18:07-18:08 - W1's src/server.py had '@app.route("/health")' without its def line; the hint only said "use 4 spaces".
* 18:19:03 - code_editor on the missing src/server.py: "does not exist or is a directory".
"""
import asyncio
import json
import logging
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PM1_NARRATION = ("I have the task list and I can see two idle workers (W1 - Coder) and W2 - Technical Writer). According to "
                 "workflow Step 2.F priority is assigning new work.")                                              # 18:24:25
SERVER_WITH_MISSING_DEF = (
    '"""src/server.py - Flask WebSocket Server Skeleton for WiFi Snake Game Multiplayer"""\n'
    "from datetime import datetime\n"
    "from flask import Flask, jsonify\n\n"
    "app = Flask(__name__)\n\n\n"
    '@app.route("/health")  # Health check endpoint to verify server is running and responsive\n\n'
    '    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})\n')
TASK_1 = "992bc07e-bdcc-4d27-9500-7482ed0a5d7e"
TASK_3 = "e4c7c9f4-cfd6-4b64-8eff-6ba87f45565d"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def quiet():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


# ======================================================================================== 1. no prefill
def test_ends_with_assistant_message():
    from src.llm_providers.ollama_provider import ends_with_assistant_message
    assert ends_with_assistant_message([{"role": "user", "content": "x"}, {"role": "assistant", "content": PM1_NARRATION}])
    assert not ends_with_assistant_message([{"role": "assistant", "content": "x"}, {"role": "user", "content": "y"}])
    assert not ends_with_assistant_message([]) and not ends_with_assistant_message(None)


def _serve_chat(payloads):
    from aiohttp import web

    async def chat(request):
        payloads.append(await request.json())
        return web.json_response({"model": "m", "done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 2,
                                  "message": {"role": "assistant", "content": "ok"}})

    async def start():
        app = web.Application()
        app.router.add_post("/api/chat", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    return start


def test_a_history_ending_with_the_agents_own_text_gets_a_continue_note(monkeypatch):
    from src.config.settings import settings
    from src.llm_providers.ollama_provider import CONTINUE_AFTER_OWN_REPLY_NOTE, OllamaProvider
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    payloads = []
    registry = types.SimpleNamespace(get_model_info=lambda model: {"model_capabilities": ["completion", "tools"]})
    pm_history = [{"role": "system", "content": "pm_manage prompt"},
                  {"role": "system", "content": "[Framework System Message]: You have the current task list."},
                  {"role": "assistant", "content": PM1_NARRATION}]
    w1_history = [{"role": "system", "content": "w"}, {"role": "user", "content": "go"},
                  {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "file_system", "arguments": {}}]},
                  {"role": "tool", "tool_call_id": "c1", "name": "file_system", "content": "ok"}]

    async def go():
        runner, url = await _serve_chat(payloads)()
        try:
            provider = OllamaProvider(base_url=url, model_registry=registry)
            for history in (pm_history, w1_history):
                [e async for e in provider.stream_completion(messages=history, model="qwen3.5:9b-q4_K_M", temperature=0.7,
                                                               max_tokens=64, tools=[{"type": "function", "function": {"name": "file_system"}}])]
        finally:
            await runner.cleanup()
    run(go())
    assert payloads[0]["messages"][-2] == {"role": "assistant", "content": PM1_NARRATION}
    assert payloads[0]["messages"][-1] == {"role": "user", "content": CONTINUE_AFTER_OWN_REPLY_NOTE}
    assert payloads[1]["messages"][-1]["role"] == "user" and payloads[1]["messages"][-1]["content"].startswith("--- Tool Response")
    assert len(payloads[1]["messages"]) == 4                                      # nothing added after a tool result
    assert "real tool call" in CONTINUE_AFTER_OWN_REPLY_NOTE


# ======================================================================================== 2. the summarizer
def test_fallback_chunk_summary():
    from src.agents.cycle_components.context_summarizer import fallback_chunk_summary
    chunk = [
        {"role": "system", "content": "worker prompt"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "file_system", "arguments": {
            "action": "write", "path": "src/server.py", "content": SERVER_WITH_MISSING_DEF}}]},
        {"role": "tool", "tool_call_id": "a", "name": "file_system",
         "content": "[Tool Error: file_system] Write rejected due to Python SyntaxError in resulting code: unexpected indent at line 184."},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "request_state", "arguments": json.dumps(
            {"state": "worker_work", "task_id": TASK_1})}}]},
        {"role": "system", "content": "[EMERGENCY SYSTEM OVERRIDE]: report back to the PM."},
        {"role": "assistant", "content": "**KICK-OFF TASK 1 STATUS REPORT:** blocked on syntax errors"},
    ]
    text = fallback_chunk_summary(chunk)
    assert text.startswith("Summary of earlier steps (made by the framework):")
    assert "- called file_system(action=write, path=src/server.py)" in text and SERVER_WITH_MISSING_DEF[:40] not in text
    assert "result: [Tool Error: file_system] Write rejected due to Python SyntaxError" in text
    assert f"- called request_state(task_id={TASK_1}, state=worker_work)" in text
    assert "- said: **KICK-OFF TASK 1 STATUS REPORT:** blocked on syntax errors" in text
    assert "- last instruction: [EMERGENCY SYSTEM OVERRIDE]" in text
    long_chunk = [{"role": "assistant", "content": f"step {n}"} for n in range(80)]
    lines = fallback_chunk_summary(long_chunk).splitlines()
    assert len(lines) == 31 and "(51 steps left out)" in lines[6] and lines[-1] == "- said: step 79"
    assert fallback_chunk_summary([]).endswith("- no tool calls in this part")


def _summarizer(reply_text):
    from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID
    from src.agents.cycle_components.context_summarizer import ContextSummarizer
    from src.llm_providers.ollama_provider import OllamaProvider
    calls = []

    class Provider(OllamaProvider):
        def __init__(self):
            pass

        async def stream_completion(self, messages, model, temperature, max_tokens=None, **kwargs):
            calls.append({"max_tokens": max_tokens, **kwargs})
            if reply_text:
                yield {"type": "response_chunk", "content": reply_text}
    cg = types.SimpleNamespace(llm_provider=Provider(), model="qwen3.5:9b-q4_K_M", message_history=[])
    return ContextSummarizer(types.SimpleNamespace(agents={CONSTITUTIONAL_GUARDIAN_AGENT_ID: cg})), calls


def _w1_history():
    history = [{"role": "system", "content": "worker_work prompt"}]
    for n in range(12):
        history += [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "name": "file_system",
                                                                          "arguments": {"action": "read", "path": "src/server.py"}}]},
                    {"role": "tool", "tool_call_id": f"c{n}", "name": "file_system", "content": f"1: line {n}"}]
    return history


def test_summaries_are_made_without_thinking():
    summarizer, calls = _summarizer("W1 wrote src/server.py; the write was rejected twice.")
    ok, condensed = run(summarizer.summarize_agent_context("W1", _w1_history()))
    assert ok and calls == [{"max_tokens": 1000, "think": False}] * 2
    assert condensed[1]["content"].endswith("W1 wrote src/server.py; the write was rejected twice.")


def test_an_empty_summary_no_longer_fails_the_condensation():
    summarizer, calls = _summarizer("")                                           # 18:13:54: content_chars=0
    ok, condensed = run(summarizer.summarize_agent_context("W1", _w1_history()))
    assert ok and len(calls) == 2
    assert condensed[0]["content"] == "worker_work prompt"
    assert condensed[1]["content"].startswith("[CONTEXT SUMMARY 1/2")
    assert "called file_system(action=read, path=src/server.py)" in condensed[1]["content"]
    assert condensed[2]["content"].startswith("[CONTEXT SUMMARY 2/2") and len(condensed) == 9


# ======================================================================================== 3. the watchdog
def test_the_watchdog_leaves_an_agent_with_a_running_cycle_alone(monkeypatch):
    import src.agents.manager as manager_module
    from src.agents.manager import AgentManager
    sleeps = []

    async def fake_sleep(_seconds):
        sleeps.append(1)
        if len(sleeps) > 1:
            raise asyncio.CancelledError
    monkeypatch.setattr(manager_module.asyncio, "sleep", fake_sleep)

    def worker(agent_id):
        return types.SimpleNamespace(agent_id=agent_id, agent_type="worker", state="worker_work", status="idle",
                                     message_history=[], _awaiting_project_approval=False)
    w1, w2 = worker("W1"), worker("W2")
    manager = object.__new__(AgentManager)
    manager.agents = {"W1": w1, "W2": w2}
    scheduled = []

    async def schedule_cycle(agent, retry_count=0):
        scheduled.append(agent.agent_id)

    async def scenario():
        manager._agent_cycle_locks = {"W1": asyncio.Lock(), "W2": asyncio.Lock()}
        await manager._agent_cycle_locks["W1"].acquire()                           # W1's cycle is summarizing
        manager.schedule_cycle = schedule_cycle
        with pytest.raises(asyncio.CancelledError):
            await manager._periodic_pm_manage_check()
    run(scenario())
    assert w1.message_history == [] and scheduled == ["W2"]
    assert "detected as IDLE" in w2.message_history[-1]["content"]


# ======================================================================================== 4. the PM keeps assigning
def _listing_turn(call_id, tasks):
    return [{"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": "project_management",
                                                                  "arguments": {"action": "list_tasks", "call": call_id}}]},
            {"role": "tool", "tool_call_id": call_id, "name": "project_management",
             "content": json.dumps({"status": "success", "message": f"Found {len(tasks)} task(s).", "tasks": tasks})}]


def _schedule_pm(history, hint, monkeypatch):
    import src.agents.cycle_components.next_step_scheduler as scheduler_module
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    monkeypatch.setattr(scheduler_module, "assignment_hint_for", lambda manager, agent: hint)
    scheduled = []

    async def schedule_cycle(agent, retry):
        scheduled.append((agent.agent_id, agent.state))

    def change_state(agent, new_state):
        agent.state = new_state
        return True
    manager = types.SimpleNamespace(workflow_manager=types.SimpleNamespace(change_state=change_state), schedule_cycle=schedule_cycle)
    agent = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage", message_history=history,
                                  status="processing", set_status=lambda s: None)
    context = types.SimpleNamespace(agent=agent, all_tool_results=[], trigger_failover=False, needs_reactivation_after_cycle=False,
                                    is_retryable_error_type=False, retry_count=0, max_retries_for_cycle=3,
                                    cycle_completed_successfully=True, state_change_requested_this_cycle=False,
                                    executed_tool_successfully_this_cycle=True, action_taken_this_cycle=True,
                                    current_model_key_for_tracking="m")
    run(NextStepScheduler(manager).schedule_next_step(context))
    return agent, scheduled


def test_a_pm_with_assignable_work_is_told_instead_of_parked(monkeypatch):
    from src.agents.assignable_work import ASSIGNMENT_CHECK_TAG
    tasks = [{"uuid": TASK_1, "assignee": "W1", "task_progress": "finished"},
             {"uuid": TASK_3, "assignee": "", "task_progress": "todo"}]
    history = [{"role": "system", "content": "pm_manage prompt"}]
    for n in range(3):                                                             # 18:23:12 - 18:23:29
        history += _listing_turn(f"c{n}", tasks)
    hint = f"{ASSIGNMENT_CHECK_TAG} 1 task(s) are not assigned to anyone and worker(s) W2 is idle..."
    agent, scheduled = _schedule_pm(history, hint, monkeypatch)
    assert agent.state == "pm_manage" and scheduled == [("PM1", "pm_manage")] and agent.message_history[-1]["content"] == hint
    # the hint is given once per pm_manage visit; then the old rule parks the PM
    agent, scheduled = _schedule_pm(agent.message_history + _listing_turn("c9", tasks), hint, monkeypatch)
    assert agent.state == "pm_standby" and scheduled == []
    # without assignable work the old rule applies at once
    agent, scheduled = _schedule_pm(history[:], None, monkeypatch)
    assert agent.state == "pm_standby"


def _task_store(monkeypatch, tasks):
    from src.tools.project_management import ProjectManagementTool
    store = types.SimpleNamespace(tasks=types.SimpleNamespace(pending=lambda: list(tasks)))
    monkeypatch.setattr(ProjectManagementTool, "_get_taskwarrior_instance", lambda self, project, session: store)


RUN_TASKS = [
    {"uuid": "kick", "description": "PROJECT KICK-OFF: WiFi_Snake_Game_Multiplayer", "assignee": "PM1", "status": "pending",
     "task_progress": "decomposed", "tags": ["project_kickoff"]},
    {"uuid": TASK_1, "description": "Kick-off Task 1: Create Flask WebSocket server skeleton in src/server.py",
     "assignee": "W1", "status": "pending", "task_progress": "doing"},
    {"uuid": TASK_3, "description": "Kick-off Task 3: Build player management system in src/player_manager.py",
     "assignee": "", "status": "pending", "task_progress": "todo"},
]


def test_the_audit_is_refused_while_tasks_are_open(monkeypatch):
    import src.agents.assignable_work as work
    from src.agents.assignable_work import PM_AUDIT_GUARD_TAG, pm_audit_block_message
    _task_store(monkeypatch, RUN_TASKS)
    monkeypatch.setattr(work, "assignment_hint_for", lambda manager, agent: "[Framework Assignment Check] assign it")
    manager = types.SimpleNamespace(current_project="WiFi_Snake_Game_Multiplayer", current_session="startup_1792576742")
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm")
    text = pm_audit_block_message(manager, pm, "pm_audit")
    assert text.startswith(f"{PM_AUDIT_GUARD_TAG} You did NOT go to pm_audit: 2 project task(s) are still open")
    assert f"- {TASK_1} (doing, assigned to W1): Kick-off Task 1" in text
    assert f"- {TASK_3} (todo, assigned to nobody): Kick-off Task 3" in text and "kick" not in text.split("Open tasks:")[1].split("- ")[0]
    assert text.endswith("[Framework Assignment Check] assign it")
    assert pm_audit_block_message(manager, pm, "pm_audit") is not None
    assert pm_audit_block_message(manager, pm, "pm_audit") is None                    # refused twice, then allowed
    assert pm_audit_block_message(manager, pm, "pm_standby") is None
    assert pm_audit_block_message(manager, types.SimpleNamespace(agent_id="W1", agent_type="worker"), "pm_audit") is None


def test_the_audit_is_allowed_when_everything_is_finished(monkeypatch):
    from src.agents.assignable_work import pm_audit_block_message
    finished = [dict(t, task_progress="finished") if t["uuid"] != "kick" else t for t in RUN_TASKS]
    _task_store(monkeypatch, finished)
    manager = types.SimpleNamespace(current_project="P", current_session="S")
    assert pm_audit_block_message(manager, types.SimpleNamespace(agent_id="PM1", agent_type="pm"), "pm_audit") is None


# ======================================================================================== 5. Admin AI and stalled PMs
def test_create_agent_does_not_make_project_managers():
    from src.tools.manage_team import ManageTeamTool, PM_CREATE_REFUSAL, looks_like_project_manager_request
    observed = {"action": "create_agent", "target_agent_id": "PM2", "role": "Project Manager (WiFi_Snake_Game_Multiplayer)",
                "new_state": "work", "priority": "high"}                                          # 18:32:13
    assert looks_like_project_manager_request(observed)
    assert looks_like_project_manager_request({"role": "Coder", "persona": "Coder", "target_agent_id": "pm3"})
    assert looks_like_project_manager_request({"role": "project_manager"})
    assert not looks_like_project_manager_request({"role": "Coder", "persona": "Snake Coder"})
    assert not looks_like_project_manager_request({"role": "Technical_Writer", "persona": "Documentation Writer"})
    result = run(ManageTeamTool().execute("admin_ai", Path("."), **observed))
    assert result["status"] == "error" and result["message"].startswith(PM_CREATE_REFUSAL[:40])
    ok = run(ManageTeamTool().execute("PM1", Path("."), action="create_agent", role="Coder", persona="Snake Coder",
                                      system_prompt="You are a backend Python developer."))
    assert ok["status"] == "success_signal_to_handler"


def test_the_stall_alert_about_a_pm_asks_admin_for_guidance(monkeypatch):
    import src.agents.assignable_work as work
    from src.agents.cycle_components.agent_health_monitor import ConstitutionalGuardianHealthMonitor
    monkeypatch.setattr(work, "assignment_hint_for", lambda manager, agent: "[Framework Assignment Check] Task 3 -> W2")
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage", status="idle",
                               message_history=[{"role": "system", "content": "pm_manage prompt"}])
    manager = types.SimpleNamespace(agents={"PM1": pm}, admin_ai_id="admin_ai")
    monitor = ConstitutionalGuardianHealthMonitor(manager)
    monitor.record_agent_cycle(pm, "", has_action=False, has_thought=False, took_meaningful_action=False)
    monitor.record_agent_cycle(pm, "", has_action=False, has_thought=False, took_meaningful_action=False)
    record = monitor._health_records["PM1"]
    record.last_meaningful_action_time = 0
    needs, _description, recovery = run(monitor.analyze_agent_health(pm))
    notify = [a for a in recovery["actions"] if a.get("action") == "notify_supervisor"][0]
    assert needs and notify["supervisor_id"] == "admin_ai"
    assert "Send `PM1` ONE message with `send_message`" in notify["message_content"]
    assert "Do NOT create new agents" in notify["message_content"] and "reassign their task" not in notify["message_content"]
    assert notify["message_content"].endswith("[Framework Assignment Check] Task 3 -> W2")


# ======================================================================================== 6. a worker forced to report
def test_a_forced_report_says_how_to_go_back():
    from src.agents.cycle_components.next_step_scheduler import forced_report_details
    w1 = types.SimpleNamespace(active_task_id=TASK_1, message_history=[
        {"role": "tool", "name": "file_system", "tool_call_id": "a",
         "content": "[Tool Error: file_system] Write rejected due to Python SyntaxError in resulting code: unexpected indent at line 184."},
        {"role": "tool", "name": "file_system", "tool_call_id": "b", "content": "1: \"\"\"src/server.py\"\"\""}])
    text = forced_report_details(w1)
    assert "you can NOT change files" in text
    assert f"call the 'request_state' tool with state='worker_work' and task_id='{TASK_1}'" in text
    assert text.endswith("Your last error was: [Tool Error: file_system] Write rejected due to Python SyntaxError in resulting code: unexpected indent at line 184.")
    assert "your task_id" in forced_report_details(types.SimpleNamespace(message_history=[]))


# ======================================================================================== 7. smaller fixes
def test_dependency_ranges_are_expanded():
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow, expand_task_range
    assert expand_task_range("task_1..6") == [f"task_{n}" for n in range(1, 7)]
    assert expand_task_range("task_1-task_3") == ["task_1", "task_2", "task_3"]
    assert expand_task_range("task_2 to 4") == ["task_2", "task_3", "task_4"]
    assert expand_task_range("task_7") == ["task_7"] and expand_task_range("task_9..2") == ["task_9..2"]
    assert expand_task_range("task_1..999") == ["task_1..999"]
    created = {f"task_{n}" for n in range(1, 7)}
    assert PMKickoffWorkflow._sanitize_kickoff_dependencies(["task_1..6"], created) == (sorted(created, key=lambda t: int(t[5:])), [])
    assert PMKickoffWorkflow._sanitize_kickoff_dependencies("task_1, task_1..2", created) == (["task_1", "task_2"], [])


def test_a_decorator_without_its_def_line_is_named():
    from src.tools.python_static_check import syntax_error_report
    with pytest.raises(SyntaxError) as raised:
        compile(SERVER_WITH_MISSING_DEF, "src/server.py", "exec")
    report = syntax_error_report(SERVER_WITH_MISSING_DEF, raised.value)
    assert "Hint: line 8 is a decorator (`@app.route(\"/health\")" in report
    assert "write the def line".lower() in report.lower() and "`def health():`" in report


def test_an_indented_line_after_a_plain_statement_is_named():
    from src.tools.python_static_check import GENERIC_INDENT_HINT, syntax_error_report
    source = "x = 1\n    y = 2\n"
    with pytest.raises(SyntaxError) as raised:
        compile(source, "a.py", "exec")
    assert "line 2 is indented more than line 1, but line 1 does not end with ':'" in syntax_error_report(source, raised.value)
    source = "def f():\nreturn 1\n"                                                    # expected an indented block
    with pytest.raises(SyntaxError) as raised:
        compile(source, "b.py", "exec")
    assert GENERIC_INDENT_HINT in syntax_error_report(source, raised.value)


def test_code_editor_on_a_missing_file_says_how_to_create_it(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    (tmp_path / "src").mkdir()
    result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename="src/server.py",
                                          chunks=[{"start_line": 1, "end_line": -1, "search": "", "replace": "x = 1\n"}]))
    assert result["status"] == "error"
    assert result["message"] == ("File 'src/server.py' does not exist yet, so it cannot be edited. To CREATE it, call the "
                                 "file_system tool with action='write', filename='src/server.py' and the COMPLETE file content.")
    result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename="src",
                                          chunks=[{"start_line": 1, "end_line": 1, "search": "", "replace": "x"}]))
    assert result["message"] == "'src' is a directory, not a file: code_editor edits files."

# END OF FILE tests/unit/test_round2p.py
