# START OF FILE tests/unit/test_round4k.py
"""
Round 4k: the run of 2026-10-30 22:30 - 2026-10-31 00:55 (qwen3.5:4b-q4_K_M, WiFi_Snake_Multiplayer: Python http.server +
vanilla JavaScript, 3 workers).

* All 26 request_state calls made in the same reply as another tool call were lost: core.py yielded the state change as a
  second event after the tool requests, and the cycle handler stops reading events once it has run a reply's tools. W1 and
  W3 asked again and again to go back from worker_report to worker_work (each time with a command_executor or file_system
  call that worker_report refuses) and looped there until the framework moved them to worker_wait (20 loop interventions).
* Workers were forced into worker_report after "3 consecutive failed cycles" that included a pytest run which RAN (exit code
  4): Round 3w treats such a run as a result for PMs only.
* WorkflowManager.change_state set agent.state directly, so the Round 2x reset of the cycles-in-one-state watchdog never
  ran: PM1's first cycle after a wake from pm_standby was flagged as "10 cycles without changing from state 'pm_manage'".
* PM1 created "Task 8: Write API documentation in docs/API.md" a second time because "... and error handling" counted as a
  named problem; "Create CONTRIBUTING.md documentation" passed the unplanned-work check.
* The kick-off plan left out plan items 5 (src/auth.py) and 6 (src/leaderboard.py): no task owned those files.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer", "startup_1793370615"
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


# ======================================================================== 1. a state change requested with tool calls
def _cycle_setup(state):
    from src.agents.constants import AGENT_STATUS_IDLE, AGENT_TYPE_WORKER
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    manager = MagicMock(spec=AgentManager)
    manager.db_manager, manager.performance_tracker, manager.send_to_ui = AsyncMock(), AsyncMock(), AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value="task")
    manager.current_project, manager.current_session, manager.current_session_db_id = None, "s", None
    manager._agent_cycle_locks, manager.agents = {}, {}
    manager.state_manager = MagicMock()
    manager.state_manager.get_agent_team.return_value = None
    manager.workflow_manager = MagicMock()
    manager.workflow_manager.get_system_prompt.return_value = "worker prompt"
    manager.workflow_manager.resolve_state_alias.side_effect = lambda _type, wanted: wanted
    manager.workflow_manager.change_state.side_effect = (
        lambda agent, wanted, task_description=None: setattr(agent, "state", wanted) or True)
    agent = MagicMock(spec=Agent)
    agent.agent_id, agent.agent_type, agent.state, agent.status = "W1", AGENT_TYPE_WORKER, state, AGENT_STATUS_IDLE
    agent.llm_provider, agent.provider_kwargs, agent.max_tokens = None, {}, None
    agent.provider_name, agent.model, agent.persona = "ollama-local", "qwen3.5:4b-q4_K_M", "Coder"
    agent.message_history = [{"role": "system", "content": "worker prompt"}]
    agent._failover_state, agent._failed_models_this_cycle = {}, set()
    agent._last_reply_cut_off = agent._think_off_next_call = agent._context_window_full = False
    agent._last_reply_output_lost = False
    agent._empty_response_retry_count = 0
    agent.text_buffer = ""
    agent.needs_priority_recheck = False
    agent.active_task_id = None
    interaction_handler = MagicMock()
    handler = AgentCycleHandler(manager, interaction_handler)
    handler._handle_worker_task_tracking = MagicMock(return_value=None)
    return handler, agent, manager, interaction_handler


def _reply(agent, calls, requested_state, task_id=None):
    async def events(*_args, **_kwargs):
        yield {"type": "tool_requests", "content_for_history": "", "raw_assistant_response": "", "agent_id": agent.agent_id,
               "calls": calls,
               "state_change": {"type": "agent_state_change_requested", "requested_state": requested_state,
                                "agent_id": agent.agent_id, "task_description": None, "task_id": task_id}}
    agent.process_message = MagicMock(side_effect=events)


def _results(*contents):
    """execute_single_tool answers with these result texts in turn"""
    results = iter(contents)

    async def execute(agent, call_id, name, args, *_rest):
        return {"status": "success", "content": next(results), "call_id": call_id, "name": name}
    return AsyncMock(side_effect=execute)


SEND = {"id": "c1", "name": "send_message", "arguments": {"target_agent_id": "PM1", "message_content": "Task 1 is done."}}
RUN = {"id": "c2", "name": "command_executor",
       "arguments": {"action": "run_command", "command": "python -m pytest tests/test_game.py -v", "timeout": 60}}
WRITE = {"id": "c3", "name": "file_system", "arguments": {"action": "write", "filename": "src/game.py", "content": "x ="}}


def test_core_attaches_the_state_change_to_the_tool_requests():
    # the protocol itself is pinned in tests/unit/test_agent_core.py (test_request_state_rides_with_the_tool_requests)
    from src.agents.user_stop import discarded_reply_note
    note = discarded_reply_note({"type": "tool_requests", "calls": [{"name": "command_executor"}],
                                 "state_change": {"requested_state": "worker_work"}})
    assert note == ("[Framework Note]: The user STOPPED the project while your last reply was being written, so its tool "
                    "call(s) (command_executor) were NOT run and its request to change to the state 'worker_work' was NOT "
                    "applied. Nothing changed. When the project is resumed, check the current state before you act again.")


def test_a_report_and_the_way_back_to_work_in_one_reply():
    handler, agent, manager, interaction_handler = _cycle_setup("worker_report")
    interaction_handler.execute_single_tool = _results("Message sent to PM1.")
    _reply(agent, [SEND], "worker_work", task_id="be676c26")
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "worker_work"
    manager.workflow_manager.change_state.assert_called_once_with(agent, "worker_work", task_description=None)
    handler._handle_worker_task_tracking.assert_called_once_with(agent, "worker_work", "be676c26")
    roles = [(m.get("role"), str(m.get("content"))[:40]) for m in agent.message_history]
    # the tool result directly after the reply, then the record and the state change - and the result only once
    assert roles[1][0] == "assistant" and roles[2] == ("tool", "Message sent to PM1.")
    assert roles[3][0] == "system" and roles[3][1].startswith("[Framework Record]: request_state")
    assert roles[4][1].startswith("[System State Change]: State changed to")
    assert sum(1 for m in agent.message_history if m.get("role") == "tool") == 1
    assert manager.schedule_cycle.call_count >= 1   # worker_work is no idle state: the agent goes on


def test_back_to_work_although_the_call_was_refused_in_worker_report():
    # 23:38:58-23:39:54: three times [request_state worker_work + command_executor] from worker_report
    handler, agent, manager, interaction_handler = _cycle_setup("worker_report")
    interaction_handler.execute_single_tool = _results(
        "[Tool Error: command_executor] 'command_executor' cannot be used in the 'worker_report' state, so NOTHING was done.")
    _reply(agent, [RUN], "worker_work")
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "worker_work"


def test_a_failed_call_keeps_the_worker_at_work():
    handler, agent, manager, interaction_handler = _cycle_setup("worker_work")
    interaction_handler.execute_single_tool = _results(
        "[Tool Execution Failed] Edit rejected: the result would not be valid Python (unexpected indent at line 2).")
    _reply(agent, [WRITE], "worker_report")
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "worker_work"
    manager.workflow_manager.change_state.assert_not_called()
    notes = [m["content"] for m in agent.message_history if "was NOT applied" in str(m.get("content"))]
    assert notes == ["[Framework Note]: Your request_state to 'worker_report' was NOT applied: your file_system call in "
                     "the same reply FAILED (see the result above), so you are still in your current state. Deal with that "
                     "result first (fix and repeat the call, or report the problem with send_message). When the work really "
                     "is done, call request_state again - on its own, in a reply without other tool calls."]
    assert agent.message_history.index(next(m for m in agent.message_history if m.get("role") == "tool")) < \
        agent.message_history.index(next(m for m in agent.message_history if "was NOT applied" in str(m.get("content"))))
    assert manager.schedule_cycle.call_count >= 1   # it reads the note


def test_a_test_run_that_ran_does_not_stop_the_report():
    handler, agent, manager, interaction_handler = _cycle_setup("worker_work")
    interaction_handler.execute_single_tool = _results(
        "Command execution completed with return code 1.\n--- STDOUT ---\nFAILED tests/test_game.py::test_move - assert 1 == 2")
    _reply(agent, [RUN], "worker_report")
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "worker_report"


def test_the_request_gets_the_guards_of_a_request_made_alone(monkeypatch):
    import src.agents.cycle_handler as ch
    handler, agent, manager, interaction_handler = _cycle_setup("worker_report")
    interaction_handler.execute_single_tool = _results("Message sent to PM1.")
    monkeypatch.setattr(ch, "worker_wait_block_message", lambda _m, _a, wanted: (
        "[Framework Notice]: your task is still open." if wanted == "worker_wait" else None))
    _reply(agent, [SEND], "worker_wait")
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "worker_report"
    manager.workflow_manager.change_state.assert_not_called()
    assert agent.message_history[-1] == {"role": "system", "content": "[Framework Notice]: your task is still open."}


def test_a_blocked_send_message_still_blocks_the_state_change():
    handler, agent, manager, interaction_handler = _cycle_setup("worker_work")
    interaction_handler.execute_single_tool = _results("Wrote src/game.py.")
    _reply(agent, [WRITE, SEND], "worker_report")
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "worker_work"
    assert any("was BLOCKED because your 'send_message' tool call failed" in str(m.get("content"))
               for m in agent.message_history)


def test_the_admin_does_not_stand_by_without_telling_the_user():
    # 22:34:59 admin_ai: mark_message_read + request_state admin_standby, no text for the user
    from src.agents.constants import AGENT_TYPE_ADMIN
    from src.agents.tool_activity import ADMIN_SILENT_STANDBY_NOTE
    handler, agent, manager, interaction_handler = _cycle_setup("admin_conversation")
    agent.agent_id, agent.agent_type = "admin_ai", AGENT_TYPE_ADMIN
    interaction_handler.execute_single_tool = _results("Message msg_1 marked as read.")
    read = {"id": "c9", "name": "mark_message_read", "arguments": {"message_id": "msg_1793370859695_PM1"}}
    _reply(agent, [read], "admin_standby")
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "admin_conversation"
    assert agent.message_history[-1] == {"role": "system", "content": ADMIN_SILENT_STANDBY_NOTE}
    # with a sentence for the user the request is applied
    handler, agent, manager, interaction_handler = _cycle_setup("admin_conversation")
    agent.agent_id, agent.agent_type = "admin_ai", AGENT_TYPE_ADMIN
    interaction_handler.execute_single_tool = _results("Message msg_1 marked as read.")

    async def events(*_args, **_kwargs):
        yield {"type": "tool_requests", "raw_assistant_response": "", "agent_id": "admin_ai", "calls": [read],
               "content_for_history": "The project has started - PM1 and its team are building it. I stand by.",
               "state_change": {"type": "agent_state_change_requested", "requested_state": "admin_standby",
                                "agent_id": "admin_ai", "task_description": None, "task_id": None}}
    agent.process_message = MagicMock(side_effect=events)
    with quiet():
        run(handler.run_cycle(agent, 0))
    assert agent.state == "admin_standby"


def test_the_refusal_rule():
    from src.agents.tool_activity import combined_state_change_refusal
    failed = [{"role": "tool", "name": "file_system", "content": "[Tool Execution Failed] File not found: 'x.py'"}]
    ran = [{"role": "tool", "name": "command_executor", "content": "Command execution completed with return code 4."}]
    fine = [{"role": "tool", "name": "send_message", "content": "Message sent to PM1."}]
    assert combined_state_change_refusal("worker_report", failed).startswith(
        "[Framework Note]: Your request_state to 'worker_report' was NOT applied: your file_system call")
    assert combined_state_change_refusal("worker_work", failed) is None
    assert combined_state_change_refusal("worker_test", failed) is None
    assert combined_state_change_refusal("worker_report", ran) is None
    assert combined_state_change_refusal("worker_wait", fine) is None
    two = failed + [{"role": "tool", "name": "code_editor", "content": "[Tool Execution Failed] Code edit failed"}]
    assert "your file_system, code_editor calls in the same reply FAILED" in combined_state_change_refusal("pm_standby", two)


# ======================================================================== 2. a command that ran is no failed cycle
def test_a_command_that_ran_is_no_failed_worker_cycle():
    from src.agents.cycle_components.next_step_scheduler import cycle_ran_a_command, failed_cycle_excuse
    agent = types.SimpleNamespace(agent_id="W1", message_history=[
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "command_executor"}}]},
        {"role": "tool", "name": "command_executor", "content":
         "Command execution completed with return code 4.\n--- STDERR ---\nERROR collecting tests/test_server.py"},
    ])
    assert cycle_ran_a_command(agent)
    assert failed_cycle_excuse(agent) == "a command it ran ended with an exit code (e.g. failing tests): a result, not a failed call"
    # only the LAST reply counts
    agent.message_history += [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "file_system"}}]},
                              {"role": "tool", "name": "file_system", "content": "[Tool Execution Failed] File not found"}]
    assert not cycle_ran_a_command(agent) and failed_cycle_excuse(agent) is None
    timed_out = types.SimpleNamespace(agent_id="W3", message_history=[
        {"role": "assistant", "content": ""},
        {"role": "tool", "name": "command_executor", "content":
         "[Tool Execution Failed] The TEST command timed out after 120 seconds and its process group was terminated"}])
    assert not cycle_ran_a_command(timed_out)   # a run that never ends is still a failure


# ======================================================================== 3. change_state restarts the watchdog
def test_a_workflow_state_change_restarts_the_cycles_in_state_watchdog():
    from src.agents.workflow_manager import AgentWorkflowManager
    agent = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_report", message_history=[],
                                  _cycles_without_transition=9, _last_state_for_watchdog="worker_report")
    with quiet():
        assert AgentWorkflowManager().change_state(agent, "worker_wait")
    assert agent.state == "worker_wait"
    assert agent._cycles_without_transition == 0 and agent._last_state_for_watchdog is None
    agent._cycles_without_transition, agent._last_state_for_watchdog = 4, "worker_wait"
    with quiet():
        AgentWorkflowManager().change_state(agent, "worker_wait")   # no change: nothing is reset
    assert agent._cycles_without_transition == 4


# ======================================================================== 4. features are no problems; contributing docs
def test_error_handling_is_no_named_problem():
    from src.tools.project_management import names_problem
    assert not names_problem("Task 8: Write API documentation in docs/API.md: ... Include request/response formats, "
                             "authentication flow, and error handling.")
    assert not names_problem("Write pytest tests for auth.py. Test both successful and failed authentication scenarios")
    assert not names_problem("Add error messages and error codes to the API responses")
    assert names_problem("Fix the error handling in src/api.py")
    assert names_problem("The server hangs on start in src/server.py")
    assert names_problem("Tests fail in tests/test_api.py")
    assert names_problem("error: the leaderboard is empty after a restart")


def test_contributing_docs_are_improvement_work():
    from src.tools.project_management import _IMPROVEMENT_RE, _improvement_stem
    text = ("Create CONTRIBUTING.md documentation: Write CONTRIBUTING.md in docs/ with contribution guidelines, development "
            "workflow, and code style recommendations")
    assert {_improvement_stem(m.group(1)) for m in _IMPROVEMENT_RE.finditer(text)} == {"contribut"}
    assert [m.group(1) for m in _IMPROVEMENT_RE.finditer(text)] == ["Create CONTRIBUTING.md", "Write CONTRIBUTING.md",
                                                                  "contribution guidelines"]
    assert not list(_IMPROVEMENT_RE.finditer("This complements the existing CONTRIBUTING.md and README.md."))
    assert _improvement_stem("code of conduct") == "code of conduct" and _improvement_stem("CHANGELOG") == "changelog"
    assert _improvement_stem("user guide") == "guide"   # unchanged


class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_work"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history = []


def _manager(*agents):
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        state_manager=types.SimpleNamespace(
            get_agent_team=lambda agent_id: f"team_{PROJECT}",
            get_agents_in_team=lambda team_id: [a for a in agents]))


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    from src.config.settings import settings
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    (tmp_path / "projects" / PROJECT / SESSION / "shared_workspace").mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    return call


KICKOFF_DOCS = ("PROJECT KICK-OFF: WiFi_Snake_Multiplayer Full Plan Overview: 8. Documentation: Write README.md with "
                "installation instructions, usage examples, mobile hosting guide, and API documentation.")
API_DOCS_1 = ("Write comprehensive API documentation in docs/API.md covering all REST endpoints, request/response formats, "
              "authentication flow, and usage examples. This complements the existing CONTRIBUTING.md and README.md.")
API_DOCS_2 = ("Write comprehensive API documentation in docs/API.md covering all endpoints (player registration, "
              "authentication, game join, score retrieval, leaderboard queries). Include request/response formats, "
              "authentication flow, and error handling.")


@needs_tw
def test_the_api_docs_are_not_written_twice_and_no_contributing_guide(tasks):
    pm1 = Agent("PM1", "pm", "pm_manage")
    manager = _manager(pm1, Agent("W2"))
    added = tasks("framework", manager, action="add_task", description=KICKOFF_DOCS, project_filter=PROJECT,
                  tags=["project_kickoff"])
    assert added["status"] == "success", added
    contributing = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",   # 23:02:53
                         title="Create CONTRIBUTING.md documentation",
                         description="Write CONTRIBUTING.md in docs/ with contribution guidelines, development workflow, "
                                     "and code style recommendations for the WiFi Snake Multiplayer project")
    assert contributing["status"] == "error" and contributing.get("error_type") == "unplanned_improvement", contributing
    first = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",
                  title="Task 8: Write API documentation in docs/API.md", description=API_DOCS_1)          # 23:40:48
    assert first["status"] == "success", first      # the plan asks for API documentation; naming CONTRIBUTING.md is fine
    assert tasks("framework", manager, action="complete_task", task_id=first["task_uuid"])["status"] == "success"
    second = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",
                   title="Task 8: Write API documentation in docs/API.md", description=API_DOCS_2)         # 00:13:16
    assert second["status"] == "error" and second.get("error_type") == "finished_duplicate", second
    # a problem that was found may still be fixed
    fix = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",
                description="Fix: docs/API.md documents /api/login, but src/api.py serves /api/auth/login")
    assert fix["status"] == "success", fix


# ======================================================================== 5. plan items the kick-off left out
PLAN = """# Project Plan: WiFi_Snake_Multiplayer

**Objective:** Browser-based multiplayer Snake game with player naming, scoring, and leaderboard.

**Tasks:**
1. **Backend server:** Implement a Flask-like HTTP server in `src/server.py` using standard library's http.server module.
2. **Game logic:** Create `src/game.py` with core snake mechanics: movement, food spawning, collision detection.
3. **API endpoints:** Build RESTful endpoints in `src/api.py` for player registration, login and leaderboard queries.
4. **Frontend:** Develop `public/index.html` with responsive HTML5 canvas game and leaderboard UI.
5. **Player management:** Implement `src/auth.py` for player naming, authentication, and session management.
6. **Leaderboard:** Create `src/leaderboard.py` to store and retrieve player scores with ranking logic.
7. **Tests:** Write pytest tests for game logic, authentication, API endpoints, and leaderboard functionality in `tests/`.
8. **Documentation:** Write `README.md` with installation instructions, usage examples and API documentation.
9. **Verification:** Run all tests, start the server, and verify the game works for local multi-device play.
"""
KICKOFF = [
    "Implement HTTP server in src/server.py using http.server module with long polling",
    "Implement game mechanics in src/game.py including movement, food spawning, collision detection",
    "Implement RESTful API in src/api.py for player registration, authentication, game join and leaderboard queries",
    "Develop responsive HTML5 canvas game UI in public/index.html with player controls and leaderboard",
    "Write pytest tests in tests/ for game logic, authentication, API endpoints, and leaderboard functionality",
    "Write README.md with installation instructions, usage examples, mobile hosting guide, and API documentation",
    "Run all tests and start server to verify the game works for local multi-device play",
]


def test_the_plan_items_no_kickoff_task_covers():
    from src.workflows.pm_kickoff_workflow import plan_item_text, plan_items_without_task
    assert plan_item_text("**Player management:** Implement `src/auth.py` for x.") == \
        "Player management: Implement src/auth.py for x."
    assert plan_items_without_task(PLAN, KICKOFF) == [
        ("5", "Player management: Implement src/auth.py for player naming, authentication, and session management."),
        ("6", "Leaderboard: Create src/leaderboard.py to store and retrieve player scores with ranking logic."),
    ]
    # a task that names the file - with another folder, or without one - covers the item
    assert plan_items_without_task(PLAN, KICKOFF + ["Implement src/auth/auth.py with password hashing",
                                                    "Create leaderboard.py with ranking"]) == []
    assert plan_items_without_task("", KICKOFF) == [] and plan_items_without_task(None, KICKOFF) == []
    # items about tests, docs or the final run are the PM's to merge
    assert plan_items_without_task("1. Write tests/test_auth.py for src/auth.py\n2. Write docs/guide.md\n"
                                   "3. Run src/main.py and verify it starts", []) == []


def test_missing_plan_items_become_kickoff_tasks_before_the_tests():
    from src.workflows.pm_kickoff_workflow import add_missing_plan_tasks, infer_missing_dependencies
    tasks = [{"id": f"task_{n}", "depends_on": None, "description": text} for n, text in enumerate(KICKOFF, 1)]
    tasks[6]["depends_on"] = ["task_1..6"]          # a dependency the PM gave
    notes = add_missing_plan_tasks(tasks, PLAN)
    assert len(notes) == 2 and notes[0].startswith("approved plan item 5 names files no kick-off task names")
    assert [t["id"] for t in tasks] == ["task_1", "task_2", "task_3", "task_4", "plan_item_5", "plan_item_6",
                                        "task_5", "task_6", "task_7"]
    assert tasks[4]["description"].startswith("Player management: Implement src/auth.py")
    assert tasks[8]["depends_on"] == ["task_1..6", "plan_item_5", "plan_item_6"]
    infer_missing_dependencies(tasks)
    assert tasks[6]["depends_on"] == ["task_1", "task_2", "task_3", "task_4", "plan_item_5", "plan_item_6"]  # the tests
    assert add_missing_plan_tasks(tasks, PLAN) == []        # covered now


def test_the_kickoff_adds_the_plan_items_and_their_files(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    import src.workflows.pm_kickoff_workflow as pkw
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    monkeypatch.setattr(pmt, "TASKLIB_AVAILABLE", False)
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    ws.mkdir(parents=True)
    (ws / "PROJECT_PLAN.md").write_text(PLAN, encoding="utf-8")

    class Executor:
        def __init__(self):
            self.calls = []

        async def execute_tool(self, **kwargs):
            self.calls.append(kwargs["tool_args"])
            n = len(self.calls)
            return {"status": "success", "task_id": n, "task_uuid": f"00000000-0000-0000-0000-{n:012d}"}

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class PM:
        agent_id, sandbox_path, message_history = "PM1", tmp_path, []
        agent_config = {"config": {"project_name_context": PROJECT}}

        def clear_history(self):
            self.message_history = []
    plan = {"roles": ["Coder", "Technical_Writer"],
            "tasks": [{"id": f"task_{n}", "description": text} for n, text in enumerate(KICKOFF, 1)],
            "project_structure": [{"dir": "src", "children": [
                {"dir": "server", "files": ["server.py"]}, {"dir": "game", "files": ["game.py"]},
                {"dir": "api", "files": ["api.py"]}, {"dir": "auth", "files": ["auth.py"]},
                {"dir": "leaderboard", "files": ["leaderboard.py"]}]},
                {"dir": "public", "files": ["index.html"]}, {"dir": "tests"}, {"dir": "docs", "files": ["README.md"]}]}
    executor = Executor()
    manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, tool_executor=executor)
    pm = PM()
    with quiet():
        result = run(pkw.PMKickoffWorkflow().execute(manager, pm, plan))
    assert result.success
    added = [call for call in executor.calls if call.get("action") == "add_task"]
    descriptions = [call["description"] for call in added]
    assert descriptions[4] == ("Kick-off Task 5: Player management: Implement src/auth.py for player naming, "
                               "authentication, and session management.")
    assert descriptions[5].startswith("Kick-off Task 6: Leaderboard: Create src/leaderboard.py")
    assert descriptions[6].startswith("Kick-off Task 7: Write pytest tests in tests/")
    assert added[4]["task_id"] == "plan_item_5"
    assert set(added[6]["depends"].split(",")) == {"task_1", "task_2", "task_3", "task_4", "plan_item_5",
                                                            "plan_item_6"}
    assert len(pm.kick_off_tasks) == 9
    md = (ws / "PROJECT_STRUCTURE.md").read_text(encoding="utf-8")
    assert "- src/auth.py" in md and "- src/leaderboard.py" in md and "src/auth/auth.py" not in md
    assert not (ws / "src" / "auth").exists()


def test_an_update_kickoff_is_not_extended(tmp_path, monkeypatch):
    import src.workflows.pm_kickoff_workflow as pkw
    calls = []
    monkeypatch.setattr(pkw, "add_missing_plan_tasks", lambda *args: calls.append(args) or [])
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    ws.mkdir(parents=True)
    (ws / "PROJECT_PLAN.md").write_text(PLAN, encoding="utf-8")

    class Executor:
        async def execute_tool(self, **kwargs):
            return {"status": "success", "task_id": 1, "task_uuid": "00000000-0000-0000-0000-000000000001"}

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)
    import src.workflows.project_update as pu
    monkeypatch.setattr(pu, "team_workers", lambda *_a: [types.SimpleNamespace(agent_id="W1", persona="Coder")])

    class PM:
        agent_id, sandbox_path, message_history = "PM1", tmp_path, []
        agent_config = {"config": {"project_name_context": PROJECT}}
        _project_update = {"number": 1}

        def clear_history(self):
            self.message_history = []
    manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, tool_executor=Executor())
    with quiet():
        run(pkw.PMKickoffWorkflow().execute(manager, PM(), {"roles": [], "tasks": [
            {"id": "u1", "description": "Add a pause button to public/index.html"}]}))
    assert calls == []

# END OF FILE tests/unit/test_round4k.py
