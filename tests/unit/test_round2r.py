# START OF FILE tests/unit/test_round2r.py
"""
Round 2r: fixtures from the 2026-10-21 19:14-19:37 run (WiFi_Snake_Multiplayer_Game, session startup_1792581286,
qwen3.5:9b on one remote Ollama server). The approval card worked (Round 2q), but:

* 19:30:47-19:31:44 - PM1's modify_task calls carried every optional field, empty: {'priority': '', 'description': '', ...}.
  The empty description reached Taskwarrior ("Cannot add a task that is blank"), three calls failed in a row and PM1 was set
  to 'error'. Nothing ever resumed it: Task 3 and the test task were ready and unassigned, and the run stood still until the
  user stopped it at 19:37:47.
* 19:21-19:31 - W1 wrote game-server/game-engine.js with a 'default:' outside its switch block and rewrote it 8 times; every
  write said "Successfully wrote" or "identical content ... considered successful". 19:31:22 Kick-off Task 2 was marked
  finished although `node --check` failed - only Python files were checked.
* 19:17:00 - PROJECT_STRUCTURE.md told the Node.js project to run pytest with a framework conftest.py that was never written;
  "No suitable non-completed initial task found" was logged although the kick-off task had just been marked 'decomposed'.
* 19:16:04 - Admin's plan ended with "**Title:** WiFi Snake Multiplayer Game": "no title could be derived".
"""
import asyncio
import inspect
import json
import logging
import shutil
import types
from unittest.mock import AsyncMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")

# game-server/game-engine.js lines 53-58 as W1 left them (19:29:11)
ENGINE_BROKEN = (
    "const DEBUG = true;\n"
    "class GameState {\n"
    "    processMovement(socketId, direction) {\n"
    "const dxMap = {'right':1,'left':-1};\n"
    "let newX, newY; \n"
    "switch(direction){case 'right':newX=player.position.x+dxMap.right;break}default:break\n"
    "    }\n"
    "}\n"
    "module.exports = { GameState };\n")
ENGINE_FIXED = ENGINE_BROKEN.replace(
    "switch(direction){case 'right':newX=player.position.x+dxMap.right;break}default:break",
    "switch(direction){case 'right':newX=1;break;default:break}")
ENGINE_TASK = ("Kick-off Task 2: Implement WebSocket handlers in game-server/game-engine.js covering movement commands with "
               "collision detection logic for walls/self/other snake collisions, food spawning system avoiding occupied grid cells")
# 19:31:44 - PM1's third modify_task call (the 19:31:18 one also sent 'status' and 'tags_filter_mode')
PM1_CALL_19_31_44 = {"assignee_agent_id": "", "priority": "", "title": "", "description": "", "dry_run": False,
                     "task_progress": "finished"}
PM1_CALL_19_31_18 = {"assignee_agent_id": "", "tags_filter_mode": "exclude", "task_progress": "finished", "priority": "",
                     "status": "completed", "description": ""}
TW_ERROR_19_31_44 = ("Note: Modified task 91e2897b is completed. You may wish to make this task pending with: task 91e2897b "
                     "modify status:pending\nCannot add a task that is blank.\nCommand used: task rc.confirmation=no "
                     "rc.data.location=/home/tom/TrippleEffect/projects/WiFi_Snake_Multiplayer_Game/startup_1792581286/task_data "
                     "91e2897b-0a8a-4b6d-8fe3-dfb6d06bc8bd modify priority: description:''")


def run(coro):
    return asyncio.run(coro)


# ======================================================================================== 1. modify_task with empty fields
def test_blank_optional_fields_are_dropped():
    from src.tools.project_management import drop_blank_fields
    args = dict(PM1_CALL_19_31_18, task_id="x", tags=[], depends=None)
    dropped = drop_blank_fields(args)
    assert sorted(dropped) == ["assignee_agent_id", "depends", "description", "priority", "tags"]
    assert args == {"tags_filter_mode": "exclude", "task_progress": "finished", "status": "completed", "task_id": "x"}
    kept = {"description": "  New text ", "priority": "none", "tags": ["a"]}
    assert drop_blank_fields(kept) == [] and kept == {"description": "  New text ", "priority": "none", "tags": ["a"]}


def test_taskwarrior_errors_lose_the_command_line():
    from src.tools.project_management import taskwarrior_error_summary
    assert taskwarrior_error_summary(Exception(TW_ERROR_19_31_44)) == "Cannot add a task that is blank."
    assert taskwarrior_error_summary(Exception("Note: only a note\nCommand used: task x")) == "Note: only a note"
    assert taskwarrior_error_summary(ValueError("")) == "ValueError"


@pytest.fixture
def tw_env(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    import src.tools.project_management as pm
    from src.config.settings import settings
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)
    ws = tmp_path / "projects" / "P" / "S" / "shared_workspace"
    ws.mkdir(parents=True)

    def call(agent, **kw):
        return run(pm.ProjectManagementTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P",
                                                      session_name="S", **kw))
    return call, ws


def _task(call, uuid):
    listed = call("PM1", action="list_tasks", include_completed=True, show_completed=True)
    tw = __import__("src.tools.project_management", fromlist=["x"]).ProjectManagementTool()._get_taskwarrior_instance("P", "S")
    return tw.tasks.get(uuid=uuid), listed


@needs_tw
@pytest.mark.parametrize("pm1_call", [PM1_CALL_19_31_44, PM1_CALL_19_31_18])
def test_the_observed_pm1_calls_now_finish_the_task(tw_env, pm1_call):
    call, _ws = tw_env
    uuid = call("PM1", action="add_task", description="Kick-off Task 1: Initialize project structure",
                assignee_agent_id="W1", priority="H")["task_uuid"]
    result = call("PM1", action="modify_task", task_id=uuid, **pm1_call)
    assert result["status"] == "success", result
    assert result["modified_fields"] == ["task_progress", "status"]
    task, _ = _task(call, uuid)
    assert task["description"] == "Kick-off Task 1: Initialize project structure"
    assert task["priority"] == "H" and task["assignee"] == "W1" and task["status"] == "completed"


@needs_tw
def test_an_empty_progress_no_longer_reopens_a_task(tw_env):
    call, _ws = tw_env
    uuid = call("PM1", action="add_task", description="Kick-off Task 3: client", assignee_agent_id="W1")["task_uuid"]
    assert call("W1", action="modify_task", task_id=uuid, task_progress="doing")["status"] == "success"
    result = call("PM1", action="modify_task", task_id=uuid, task_progress="", status="", priority="L")
    assert result["status"] == "success" and result["modified_fields"] == ["priority"], result
    task, _ = _task(call, uuid)
    assert task["task_progress"] == "doing" and task["priority"] == "L"


@needs_tw
def test_only_empty_values_are_named_in_the_refusal(tw_env):
    call, _ws = tw_env
    uuid = call("PM1", action="add_task", description="Kick-off Task 2: engine", assignee_agent_id="W1")["task_uuid"]
    result = call("PM1", action="modify_task", task_id=uuid, assignee_agent_id="")                       # 19:30:47
    assert result["status"] == "error" and "No valid fields provided" in result["message"]
    assert "Empty values ('') are ignored; they were sent for: assignee_agent_id." in result["message"]


@needs_tw
def test_a_refused_save_is_reported_without_the_command(tw_env, monkeypatch):
    call, _ws = tw_env
    uuid = call("PM1", action="add_task", description="Kick-off Task 2: engine", assignee_agent_id="W1")["task_uuid"]
    from tasklib.task import Task

    def refuse(self):
        raise Exception(TW_ERROR_19_31_44)
    monkeypatch.setattr(Task, "save", refuse)
    result = call("PM1", action="modify_task", task_id=uuid, priority="M")
    assert result == {"status": "error", "message": (
        f"Task '{uuid}' was NOT changed: Taskwarrior refused the change of priority: Cannot add a task that is blank. "
        "Send only the fields you want to change.")}


def test_the_modify_help_says_to_send_only_changed_fields():
    from src.tools.project_management import ProjectManagementTool
    usage = ProjectManagementTool().get_detailed_usage(sub_action="modify_task")
    assert "Send ONLY the fields you change. An empty value ('') means \"not given\" and is ignored." in usage


# ======================================================================================== 2. agents in 'error' come back
class FakeAgent(types.SimpleNamespace):
    def set_status(self, status):
        self.status = status


def _pm1(history=None):
    return FakeAgent(agent_id="PM1", agent_type="pm", state="pm_report_check", status="error",
                     message_history=list(history or []), _consecutive_tool_failures=3, _awaiting_project_approval=False)


PM1_ERROR_NOTE = ("[Framework Error]: Agent 'PM1' had 3 consecutive tool execution failures. Stopped to prevent infinite loop. "
                  "Last error (project_management): An unexpected error occurred: Cannot add a task that is blank.")


def _recovery(monkeypatch, agents=None, paused=False, delay=60.0, max_per_hour=3):
    from src.agents.error_recovery import ErrorRecovery
    from src.config.settings import settings
    monkeypatch.setattr(settings, "ERROR_RECOVERY_DELAY_SECONDS", delay, raising=False)
    monkeypatch.setattr(settings, "ERROR_RECOVERY_MAX_PER_HOUR", max_per_hour, raising=False)
    manager = types.SimpleNamespace(
        agents=dict(agents or {}), schedule_cycle=AsyncMock(), send_to_ui=AsyncMock(),
        cg_escalation=types.SimpleNamespace(is_system_paused=lambda: paused),
        interaction_handler=types.SimpleNamespace(route_and_activate_agent_message=AsyncMock(return_value=({}, None))),
        state_manager=types.SimpleNamespace(get_agent_team=lambda agent_id: "team_P",
                                            get_agents_in_team=lambda team_id: list(manager.agents.values())))
    return ErrorRecovery(manager), manager


def test_the_error_reason_is_the_newest_framework_error():
    from src.agents.error_recovery import DEFAULT_REASON, error_reason
    agent = _pm1([{"role": "system", "content": "[Framework Error]: old"}, {"role": "system", "content": PM1_ERROR_NOTE},
                  {"role": "tool", "content": "x"}])
    assert error_reason(agent) == PM1_ERROR_NOTE[len("[Framework Error]: "):]
    agent.message_history.append({"role": "system", "content": "[Framework Recovery]: You were stopped ..."})
    assert error_reason(agent) == DEFAULT_REASON                         # already reported in the last recovery note
    assert error_reason(_pm1()) == DEFAULT_REASON


def test_the_stopping_message_names_the_last_error():
    from src.agents.cycle_handler import AgentCycleHandler
    source = inspect.getsource(AgentCycleHandler)
    assert 'error_message += f" Last error ({called_tool_name}): {last_failure[:400]}"' in source


def test_pm1_is_resumed_at_the_second_check(monkeypatch):
    pm1 = _pm1([{"role": "system", "content": PM1_ERROR_NOTE}])
    recovery, manager = _recovery(monkeypatch, {"PM1": pm1})
    assert run(recovery.consider(pm1, now=1000.0)) is False                # 19:31:44 + first watchdog pass: noted
    assert pm1.status == "error" and pm1._error_seen_at == 1000.0
    assert run(recovery.consider(pm1, now=1030.0)) is False                # not yet ERROR_RECOVERY_DELAY_SECONDS
    assert run(recovery.consider(pm1, now=1060.0)) is True
    assert pm1.status == "idle" and pm1._consecutive_tool_failures == 0 and pm1._error_seen_at is None
    note = pm1.message_history[-1]
    assert note["role"] == "system" and note["content"].startswith("[Framework Recovery]: You were stopped (status 'error') "
                                                                   "because: Agent 'PM1' had 3 consecutive tool execution")
    assert "Cannot add a task that is blank." in note["content"] and "(recovery 1 of at most 3 per hour)" in note["content"]
    manager.schedule_cycle.assert_awaited_once_with(pm1, 0)
    assert manager.send_to_ui.await_args.args[0]["content"] == "Resumed after an error (1/3 this hour)."


@pytest.mark.parametrize("case", ["user_stop", "guardian", "approval", "paused"])
def test_agents_that_must_stay_stopped(monkeypatch, case):
    agent = _pm1()
    if case == "user_stop":
        agent._stopped_by_user = True
    elif case == "guardian":
        agent.agent_id = "constitutional_guardian_ai"
    elif case == "approval":
        agent._awaiting_project_approval = True
    recovery, manager = _recovery(monkeypatch, {agent.agent_id: agent}, paused=(case == "paused"), delay=1.0)
    for now in (0.0, 100.0, 200.0):
        assert run(recovery.consider(agent, now=now)) is False
    assert agent.status == "error" and agent.message_history == []
    manager.schedule_cycle.assert_not_awaited()


def test_a_user_stop_ends_when_the_agent_runs_again(monkeypatch):
    agent = _pm1()
    agent._stopped_by_user = True
    recovery, _manager = _recovery(monkeypatch, {"PM1": agent})
    agent.status = "idle"                                                  # a message resumed it (interaction handler)
    run(recovery.consider(agent, now=5.0))
    assert agent._stopped_by_user is False


def test_the_user_stop_marks_the_agent(monkeypatch):
    from src.agents.manager import AgentManager
    agent = FakeAgent(agent_id="W2", agent_type="worker", state="worker_work", status="awaiting_user_review_cg",
                      cg_awaiting_user_decision=True, cg_concern_details="<CONCERN>x</CONCERN>", cg_original_text="t",
                      cg_original_event_data={}, message_history=[])
    manager = object.__new__(AgentManager)
    manager.agents = {"W2": agent}
    manager.cg_escalation = types.SimpleNamespace(finish_user_review=AsyncMock())
    ok, _ = run(manager.resolve_cg_concern_stop("W2"))
    assert ok and agent.status == "error" and agent._stopped_by_user is True


def test_admin_ai_only_runs_again_for_an_unanswered_user_message(monkeypatch):
    admin = FakeAgent(agent_id="admin_ai", agent_type="admin", state="admin_conversation", status="error",
                      message_history=[{"role": "assistant", "content": "Hi"}])
    recovery, manager = _recovery(monkeypatch, {"admin_ai": admin}, delay=1.0)
    run(recovery.consider(admin, now=0.0))
    assert run(recovery.consider(admin, now=5.0)) is True and admin.status == "idle"
    manager.schedule_cycle.assert_not_awaited()
    admin.status = "error"
    admin.message_history.append({"role": "user", "content": "Status?"})
    run(recovery.consider(admin, now=10.0))
    assert run(recovery.consider(admin, now=15.0)) is True
    manager.schedule_cycle.assert_awaited_once_with(admin, 0)


def test_after_the_hourly_limit_the_supervisor_gets_one_report(monkeypatch):
    pm1 = FakeAgent(agent_id="PM1", agent_type="pm", state="pm_manage", status="idle", message_history=[])
    w1 = FakeAgent(agent_id="W1", agent_type="worker", state="worker_work", status="error",
                   message_history=[{"role": "system", "content": "[Framework Error]: W1 stopped."}])
    recovery, manager = _recovery(monkeypatch, {"PM1": pm1, "W1": w1}, delay=1.0, max_per_hour=2)
    now = 0.0
    for _ in range(2):                                                      # two recoveries
        run(recovery.consider(w1, now=now))
        assert run(recovery.consider(w1, now=now + 2)) is True
        w1.status = "error"
        now += 10
    run(recovery.consider(w1, now=now))
    assert run(recovery.consider(w1, now=now + 2)) is False and w1.status == "error"
    route = manager.interaction_handler.route_and_activate_agent_message
    route.assert_awaited_once()
    kwargs = route.await_args.kwargs
    assert kwargs["sender_id"] == "constitutional_guardian_ai" and kwargs["target_identifier"] == "PM1"
    assert kwargs["message_content"].startswith("⚠️ Agent `W1` (state `worker_work`) stopped with an error 2 times within an "
                                                "hour and is NOT resumed automatically any more.")
    run(recovery.consider(w1, now=now + 5))                                 # no second report within the hour
    route.assert_awaited_once()
    later = now + 3700                                                      # the hour is over: resumed again
    assert run(recovery.consider(w1, now=later)) is True and w1.status == "idle"


def test_the_report_about_a_pm_names_the_open_work(monkeypatch):
    import src.agents.assignable_work as assignable_work
    monkeypatch.setattr(assignable_work, "assignment_hint_for", lambda manager, agent: "Assign Task 3 to W1.")
    admin = FakeAgent(agent_id="admin_ai", agent_type="admin", state="admin_conversation", status="idle", message_history=[])
    pm1 = _pm1()
    recovery, manager = _recovery(monkeypatch, {"admin_ai": admin, "PM1": pm1}, delay=1.0, max_per_hour=1)
    pm1._error_recoveries = [0.0]
    run(recovery.consider(pm1, now=1.0))
    run(recovery.consider(pm1, now=5.0))
    report = manager.interaction_handler.route_and_activate_agent_message.await_args.kwargs
    assert report["target_identifier"] == "admin_ai"
    assert report["message_content"].endswith("**Open work the PM should assign** (you can forward this):\nAssign Task 3 to W1.")


def test_the_watchdog_resumes_pm1(monkeypatch):
    import src.agents.manager as manager_module
    from src.agents.error_recovery import ErrorRecovery
    from src.agents.manager import AgentManager
    from src.config.settings import settings
    monkeypatch.setattr(settings, "ERROR_RECOVERY_DELAY_SECONDS", 0.001, raising=False)
    passes = []
    real_sleep = asyncio.sleep

    async def fake_sleep(_seconds):
        passes.append(1)
        if len(passes) > 2:
            raise asyncio.CancelledError
        await real_sleep(0.01)                                              # > ERROR_RECOVERY_DELAY_SECONDS
    monkeypatch.setattr(manager_module.asyncio, "sleep", fake_sleep)
    pm1 = _pm1([{"role": "system", "content": PM1_ERROR_NOTE}])
    manager = object.__new__(AgentManager)
    manager.agents = {"PM1": pm1}
    manager.send_to_ui = AsyncMock()
    manager.cg_escalation = types.SimpleNamespace(is_system_paused=lambda: False)
    manager.error_recovery = ErrorRecovery(manager)
    scheduled = []

    async def schedule_cycle(agent, retry_count=0):
        scheduled.append((agent.agent_id, agent.state))
    manager.schedule_cycle = schedule_cycle
    manager.workflow_manager = types.SimpleNamespace(change_state=lambda agent, state: setattr(agent, "state", state))
    with pytest.raises(asyncio.CancelledError):
        run(manager._periodic_pm_manage_check())
    # pass 1 notes the error, pass 2 resumes PM1 in its state; the "idle in pm_report_check" rule does not ALSO run
    assert scheduled == [("PM1", "pm_report_check")] and pm1.status == "idle"
    assert pm1.message_history[-1]["content"].startswith("[Framework Recovery]")


def test_recovery_settings_have_defaults():
    from src.config.settings import Settings
    fresh = Settings()
    assert fresh.ERROR_RECOVERY_DELAY_SECONDS == 60.0 and fresh.ERROR_RECOVERY_MAX_PER_HOUR == 3


# ======================================================================================== 3. JavaScript / JSON / shell checks
def test_check_kinds():
    from src.tools.source_check import JAVASCRIPT, JSON_KIND, SHELL, check_kind
    assert check_kind("game-server/game-engine.js") == JAVASCRIPT and check_kind("a.MJS") == JAVASCRIPT
    assert check_kind("game-server/package.json") == JSON_KIND and check_kind("scripts/start-server.sh") == SHELL
    assert check_kind("tsconfig.json") is None and check_kind("tsconfig.base.json") is None
    assert check_kind(".vscode/settings.json") is None and check_kind("public/index.html") is None
    assert check_kind("src/app.py") is None and check_kind(None) is None


@needs_node
def test_the_engine_error_is_found_with_its_line():
    from src.tools.source_check import check_source
    issue = check_source(ENGINE_BROKEN, "game-server/game-engine.js")
    assert issue.message == "SyntaxError: Unexpected token 'default'" and issue.line == 6
    assert issue.compact("game-server/game-engine.js") == (
        "game-server/game-engine.js is not valid JavaScript (SyntaxError: Unexpected token 'default' at line 6).")
    detail = issue.detailed(ENGINE_BROKEN).splitlines()
    assert detail[0] == "SyntaxError: Unexpected token 'default' (line 6)"
    assert detail[3] == "  6 | switch(direction){case 'right':newX=player.position.x+dxMap.right;break}default:break"
    assert detail[4] == " " * (6 + detail[3][6:].index("default")) + "^" * len("default")
    assert check_source(ENGINE_FIXED, "game-server/game-engine.js") is None


@needs_node
def test_es_modules_jsx_and_top_level_await():
    from src.tools.source_check import check_source
    assert check_source('import x from "y";\nexport const a = 1;\n', "a.js") is None
    broken_module = check_source('import x from "y";\nfunction(){\n', "a.js")    # node --check alone says "fine" here
    assert broken_module.line == 2 and "function name" in broken_module.message
    assert check_source("export default function App(){ return <div/>; }\n", "src/App.js") is None   # JSX: not judged
    assert check_source("function A(){ return <div/>; }\n", "src/A.js") is None
    assert check_source('const data = await fetch("a");\n', "a.js") is None
    assert check_source("module.exports = { a: 1 };\n", "a.cjs") is None


def test_json_checks():
    from src.tools.source_check import check_source
    package = {"name": "wifi-snake-multiplayer", "dependencies": {"express": "^4.21.2"}}
    assert check_source(json.dumps(package, indent=2), "game-server/package.json") is None
    assert check_source("\ufeff{}", "a.json") is None
    issue = check_source('{\n  "name": "x"\n  "version": "1.0.0"\n}\n', "package.json")       # a missing comma
    assert issue.kind == "JSON" and issue.line == 3 and issue.message == "Expecting ',' delimiter"
    assert issue.detailed('{\n  "name": "x"\n  "version": "1.0.0"\n}\n').splitlines()[-1] == " " * 8 + "^"  # under the '"' of "version"
    assert check_source("  \n", "package.json").message == "the file is empty"


@needs_bash
def test_shell_checks():
    from src.tools.source_check import check_source
    start_server = ('#!/bin/bash\necho "Starting..."\ncd "$(dirname "$0")/.." || exit 1\n\n'
                    'node game-server/app.js &>/dev/null && echo "ok" || { \n    node game-server/app.js; exit 0\n}\n')
    assert check_source(start_server, "scripts/start-server.sh") is None              # the file of the run is valid
    issue = check_source("if [ 1 ]; then\n echo hi\n}\n", "run.sh")
    assert issue.line == 3 and "syntax error near unexpected token" in issue.message
    assert check_source("#!/usr/bin/env python3\nif x:\n", "tool.sh") is None         # not a shell script
    assert check_source("#!/usr/bin/env bash\nif x\n", "tool.sh").line is not None


def fs(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return run(FileSystemTool().execute("W1", tmp_path, scope="private", **kwargs))


@needs_node
def test_write_and_identical_rewrite_name_the_syntax_error(tmp_path):
    # Round 2x: a write with a syntax error is REJECTED (it used to be saved with a note)
    first = fs(tmp_path, action="write", path="game-server/game-engine.js", content=ENGINE_BROKEN)
    assert first["status"] == "error" and not (tmp_path / "game-server/game-engine.js").exists()
    assert first["message"].startswith("Write rejected: 'game-server/game-engine.js' is not valid JavaScript (SyntaxError: "
                                       "Unexpected token 'default' at line 6). No changes were saved.")
    assert "SyntaxError: Unexpected token 'default' (line 6)\n" in first["message"]
    # a broken file that is already on disk (saved before Round 2x): an identical rewrite still names the error
    (tmp_path / "game-server").mkdir()
    (tmp_path / "game-server/game-engine.js").write_text(ENGINE_BROKEN, encoding="utf-8")
    again = fs(tmp_path, action="write", path="game-server/game-engine.js", content=ENGINE_BROKEN)
    assert again["status"] == "success" and "contains the exact same content" in again["message"]
    assert "[Framework check - the file is unchanged and STILL NOT valid JavaScript:]" in again["message"]
    assert again["message"].endswith("Sending the same text again changes nothing: fix the line named above.")
    fixed = fs(tmp_path, action="write", path="game-server/game-engine.js", content=ENGINE_FIXED, force_overwrite=True)
    assert fixed["status"] == "success" and "Framework check" not in fixed["message"]
    assert "Framework check" not in fs(tmp_path, action="write", path="notes.txt", content="default:")["message"]


@needs_node
def test_edits_name_the_syntax_error(tmp_path):
    # Round 2x: an edit that breaks a VALID file is rejected; a file that is already broken can still be edited
    from src.tools.code_editor import CodeEditorTool
    engine = tmp_path / "game-server/game-engine.js"
    fs(tmp_path, action="write", path="game-server/game-engine.js", content=ENGINE_FIXED)
    edited = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks",
                                          filename="game-server/game-engine.js",
                                          chunks=[{"search": "break;default:break}", "replace": "break}default:break"}]))
    assert edited["status"] == "error" and edited["message"].startswith(
        "Code edit rejected: the result would not be valid JavaScript (SyntaxError: Unexpected token 'default' at line 6).")
    assert engine.read_text(encoding="utf-8") == ENGINE_FIXED
    engine.write_text(ENGINE_BROKEN, encoding="utf-8")  # broken before Round 2x
    found = fs(tmp_path, action="find_replace", path="game-server/game-engine.js", find_text="const DEBUG = true;",
               replace_text="const DEBUG = false;")
    assert found["status"] == "success"
    assert "[Framework check - the edit WAS saved (the file was already invalid before it), but the file is STILL NOT " \
           "valid JavaScript:]" in found["message"]
    replaced = fs(tmp_path, action="replace_lines", path="game-server/game-engine.js", start_line=6, end_line=6,
                  content="switch(direction){case 'right':newX=1;break;default:break}")
    assert replaced["status"] == "success" and "Framework check" not in replaced["message"]
    fs(tmp_path, action="write", path="game-server/package.json", content='{"name": "x"}\n')
    appended = fs(tmp_path, action="append", path="game-server/package.json", content="{")
    assert appended["status"] == "error" and appended["message"].startswith(
        "Append rejected: 'game-server/package.json' would not be valid JSON after it")
    assert (tmp_path / "game-server/package.json").read_text(encoding="utf-8") == '{"name": "x"}\n'


def test_python_edits_keep_their_own_notes(tmp_path):
    from src.tools.python_static_check import saved_edit_notes
    assert saved_edit_notes("def f():\n    return undefined_name\n", "src/a.py").startswith(
        "\n[Framework check - the edit WAS saved, but please fix:]")
    assert saved_edit_notes("x = 1\n", "notes.md") == ""


@needs_tw
@needs_node
def test_a_task_with_broken_javascript_is_not_finished(tw_env):
    call, ws = tw_env
    (ws / "game-server").mkdir()
    (ws / "game-server" / "game-engine.js").write_text(ENGINE_BROKEN)
    uuid = call("PM1", action="add_task", description=ENGINE_TASK, assignee_agent_id="W1")["task_uuid"]
    result = call("W1", action="modify_task", task_id=uuid, task_progress="finished")                    # 19:31:22
    assert result["status"] == "error" and f"Task '{uuid}' was NOT marked finished" in result["message"]
    assert ("- game-server/game-engine.js is not valid JavaScript (SyntaxError: Unexpected token 'default' at line 6)."
            in result["message"])
    assert call("PM1", action="complete_task", task_id=uuid)["status"] == "error"
    (ws / "game-server" / "game-engine.js").write_text(ENGINE_FIXED)
    assert call("W1", action="modify_task", task_id=uuid, task_progress="finished")["status"] == "success"


@needs_tw
def test_a_changed_package_json_is_checked(tw_env):
    from src.utils import file_authorship
    call, ws = tw_env
    uuid = call("PM1", action="add_task", description="Kick-off Task 1: Initialize project structure",
                assignee_agent_id="W1")["task_uuid"]
    package = ws / "game-server" / "package.json"
    package.parent.mkdir()
    package.write_text('{\n  "name": "wifi-snake"\n  "main": "app.js"\n}\n')
    file_authorship.reset()
    try:
        file_authorship.record_file_change(package, "W1", uuid, "write")
        result = call("W1", action="modify_task", task_id=uuid, task_progress="finished")
        assert result["status"] == "error"
        assert "- game-server/package.json is not valid JSON (Expecting ',' delimiter at line 3)." in result["message"]
    finally:
        file_authorship.reset()


def test_an_empty_named_javascript_file_is_a_problem(tmp_path):
    from src.utils.task_files import task_completion_problems
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "game-client.js").write_text("  \n")
    assert task_completion_problems("Build public/game-client.js", tmp_path) == ["public/game-client.js is EMPTY."]
    assert task_completion_problems("Build public/index.html", tmp_path) == []


# ======================================================================================== 4. smaller fixes
def test_project_structure_mentions_pytest_only_for_python():
    from src.workflows.pm_kickoff_workflow import render_project_structure_md
    node = render_project_structure_md(["game-server", "public", "scripts"],
                                       ["game-server/app.js", "public/index.html", "scripts/start-server.sh"])
    assert "pytest" not in node and "conftest" not in node
    assert "Run tests and other commands from the workspace root, with the tools of the project's tech stack." in node
    python = render_project_structure_md(["src", "tests"], ["src/app.py", "tests/test_app.py"])
    assert "Run tests from the workspace root with `python -m pytest -q`." in python
    assert "python -m pytest" in render_project_structure_md(["src"], [], python_project=True)


def test_plan_title_line_is_used():
    from src.agents.tool_activity import plan_title_from_text
    plan_19_16_04 = ("**Objective:** Develop a browser-based multi-player snake game ...\n\n**Tech stack:** Node.js ...\n\n"
                     "**Tasks:**\n1. **Game Server Setup & Core API:** ...\n2. **WebSocket Game Logic:** ...\n"
                     "3. **Client-Side Game Interface:** ...\n\n**Title:** WiFi Snake Multiplayer Game\n")
    assert plan_title_from_text(plan_19_16_04) == "WiFi Snake Multiplayer Game"
    assert plan_title_from_text("- **Project Name**: Weather Bot\n") == "Weather Bot"
    assert plan_title_from_text("**Tasks:**\n1. a\n2. b") is None
    assert plan_title_from_text("The title: is not a title line") is None
    history = [{"role": "user", "content": 'Build a game called "Snake Party"'}]
    assert plan_title_from_text(plan_19_16_04, history) == "Snake Party"                 # the user's name comes first


KICKOFF_19_17_00 = """{
  "roles": ["Coder", "Technical_Writer"],
  "tasks": [
    {"id": "task_1", "depends_on": null, "description": "Initialize project structure with server/ directory containing package.json and game-server/app.js for Express setup; create public/ for frontend files (index.html, styles.css); create tests/, docs/, scripts/"},
    {"id": "task_2", "depends_on": ["task_1"], "description": "Implement WebSocket handlers in game-server/game-engine.js covering movement commands"},
    {"id": "task_3", "depends_on": ["task_2"], "description": "Build client-side interface in public/index.html (canvas rendering)"}
  ],
  "code_base_definitions": {
    "tech_stack": ["Node.js v18+", "Express"],
    "testing_framework": "Node.js built-in assert library for all test validations in tests/ directory (no external testing frameworks)",
      }"""


@pytest.fixture
def kickoff_store(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    if shutil.which("task") is None:
        pytest.skip("Taskwarrior binary not installed")
    import src.tools.project_management as pm
    import src.workflows.pm_kickoff_workflow as pkw
    from src.config.settings import settings
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class Executor:
        async def execute_tool(self, agent_id, agent_sandbox_path, tool_name, tool_args, project_name, session_name, manager):
            return await pm.ProjectManagementTool().execute(agent_id, tmp_path, project_name=project_name,
                                                            session_name=session_name, **tool_args)
    run(pm.ProjectManagementTool().execute("framework", tmp_path, project_name="Snake", session_name="S1", action="add_task",
                                           description="PROJECT KICK-OFF: Snake",
                                           tags=["project_kickoff", "auto_created_by_framework"], assignee_agent_id="PM1"))
    manager = types.SimpleNamespace(current_project="Snake", current_session="S1", tool_executor=Executor())
    agent = types.SimpleNamespace(agent_id="PM1", sandbox_path=tmp_path, message_history=[],
                                  agent_config={"config": {"project_name_context": "Snake"}})
    agent.clear_history = lambda: agent.message_history.clear()
    return manager, agent, tmp_path


def test_the_node_kickoff(kickoff_store, caplog):
    from src.agents.workflow_manager import salvage_kickoff_plan
    from src.tools.project_management import ProjectManagementTool
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
    manager, agent, tmp_path = kickoff_store
    with caplog.at_level(logging.INFO, logger="src.workflows.pm_kickoff_workflow"):
        result = run(PMKickoffWorkflow().execute(manager, agent, salvage_kickoff_plan(KICKOFF_19_17_00)))
    assert result.success is True, result.message
    assert "No suitable non-completed initial task found" not in caplog.text
    assert "are marked 'decomposed' and stay open as the parent of the kick-off tasks; nothing to complete." in caplog.text
    tw = ProjectManagementTool()._get_taskwarrior_instance("Snake", "S1")
    kickoff = [t for t in tw.tasks.pending() if "project_kickoff" in (t["tags"] or set())]
    assert len(kickoff) == 1 and kickoff[0]["task_progress"] == "decomposed"
    structure = (tmp_path / "projects" / "Snake" / "S1" / "shared_workspace" / "PROJECT_STRUCTURE.md").read_text()
    assert "pytest" not in structure and "conftest" not in structure
    assert not (tmp_path / "projects" / "Snake" / "S1" / "shared_workspace" / "conftest.py").exists()


def test_log_wording():
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.state_manager import AgentStateManager
    from src.agents.workflow_manager import AgentWorkflowManager
    assert "Duplicate count: {agent._duplicate_tool_call_count}. Skipping re-execution." not in inspect.getsource(AgentCycleHandler)
    assert "The earlier result is reused " in inspect.getsource(AgentCycleHandler)
    assert "already exists; nothing to do." in inspect.getsource(AgentStateManager.create_new_team)
    assert "project likely complete" not in inspect.getsource(AgentWorkflowManager)

# END OF FILE tests/unit/test_round2r.py
