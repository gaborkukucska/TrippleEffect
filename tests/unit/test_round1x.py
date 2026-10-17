"""Round 1x tests (run 2026-10-17 11:30-14:56, Python_Temp_Convert_CLI: only the code task finished in 3.5 hours).

1. W1 wrote and edited tests/test_temp_convert.py for 66 minutes while still in worker_decompose, whose replies are capped
   at WORKER_DECOMPOSE_STATE_MAX_TOKENS (1024): an incomplete file, empty replies and failed edits.
2. code_editor got chunks such as {'start_line': 31, 'search': '', 'replace': ''} and answered "Search text is ambiguous
   (found 987 times)"; chunks without 'replace' got no hint how to delete lines.
3. replace_lines was sent 'replacement_content' instead of 'content'.
4. Ollama replies never logged why they ended, so the many empty replies could not be explained.
"""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK
from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
from src.agents.tool_activity import files_written_in_last_turn


# ------------------------------------------------------------------------------------------------ helpers
def call(call_id, name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": args}]}


def result(call_id, name, content):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


WROTE_TESTS = [
    call("c1", "file_system", action="read", path="src/temp_convert.py"), result("c1", "file_system", "1: x"),
    call("c2", "file_system", action="write", path="tests/test_temp_convert.py", content="def test_a():\n    assert 1\n"),
    result("c2", "file_system", "Successfully wrote content to 'tests/test_temp_convert.py'"),
]


# ------------------------------------------------------------------------------------------------ 1. files_written_in_last_turn
def test_the_last_write_is_found():
    assert files_written_in_last_turn(WROTE_TESTS) == ["tests/test_temp_convert.py"]


@pytest.mark.parametrize("history", [
    [],
    None,
    WROTE_TESTS + [call("c3", "file_system", action="read", path="a.py"), result("c3", "file_system", "1: x")],  # last turn only read
    [call("c1", "file_system", action="write", path="a.py", content="x"),
     result("c1", "file_system", "[Tool Execution Failed] Write rejected due to Python SyntaxError")],              # failed write
    [call("c1", "file_system", action="write", path="a.py", content="x")],                                           # no result yet
    [call("c1", "project_management", action="add_task", description="sub-task"), result("c1", "project_management", '{"status": "success"}')],
])
def test_nothing_is_reported_otherwise(history):
    assert files_written_in_last_turn(history) == []


def test_several_writes_in_one_turn_and_code_editor_edits():
    history = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "name": "file_system", "arguments": {"action": "write", "path": "README.md", "content": "x"}},
            {"id": "b", "name": "code_editor", "arguments": {"action": "replace_chunks", "filename": "src/m.py", "chunks": []}},
        ]},
        result("a", "file_system", "Successfully wrote content to 'README.md'"),
        result("b", "code_editor", "Successfully applied 1 edits to 'src/m.py'."),
    ]
    assert files_written_in_last_turn(history) == ["README.md", "src/m.py"]


# ------------------------------------------------------------------------------------------------ 1. promotion to worker_work
def make(history, tracking_error=None, change_ok=True):
    manager = MagicMock()
    manager.schedule_cycle = AsyncMock(return_value=MagicMock())
    manager.send_to_ui = AsyncMock()
    manager.current_session_db_id = None
    tracked = []

    def track(agent, state, task_id=None):
        tracked.append((state, task_id))
        if tracking_error:
            raise ValueError(tracking_error)
    manager.cycle_handler._handle_worker_task_tracking = track

    def change_state(agent, state):
        if change_ok:
            agent.state = state
            agent.message_history = agent.message_history[:1]    # the real change_state condenses the history
        return change_ok
    manager.workflow_manager.change_state = change_state
    statuses = []
    agent = SimpleNamespace(agent_id="W1", agent_type=AGENT_TYPE_WORKER, state=WORKER_STATE_DECOMPOSE, status="processing",
                            current_task_id="780cd441", message_history=[{"role": "system", "content": "sys"}] + list(history),
                            set_status=lambda s, **k: statuses.append(s))
    return NextStepScheduler(manager), manager, agent, tracked


def ctx(agent, tool_ok=True):
    return SimpleNamespace(
        agent=agent, all_tool_results=[], trigger_failover=False, needs_reactivation_after_cycle=False,
        is_retryable_error_type=False, retry_count=0, max_retries_for_cycle=3, cycle_completed_successfully=True,
        executed_tool_successfully_this_cycle=tool_ok, action_taken_this_cycle=tool_ok,
        state_change_requested_this_cycle=False, current_model_key_for_tracking="m",
        last_error_content="", last_error_obj=None, plan_submitted_this_cycle=False, thought_produced_this_cycle=False,
    )


def test_a_decompose_worker_that_writes_files_moves_to_worker_work():
    sched, manager, agent, tracked = make(WROTE_TESTS)
    asyncio.run(sched.schedule_next_step(ctx(agent)))
    assert agent.state == WORKER_STATE_WORK
    assert tracked == [(WORKER_STATE_WORK, "780cd441")]          # same task bookkeeping as request_state
    notice = agent.message_history[-1]["content"]
    assert "tests/test_temp_convert.py" in notice and WORKER_STATE_WORK in notice and "worker_report" in notice
    manager.schedule_cycle.assert_awaited_once()


def test_reading_or_planning_keeps_the_worker_in_decompose():
    history = [call("c1", "file_system", action="read", path="PROJECT_PLAN.md"), result("c1", "file_system", "1: # Plan")]
    sched, manager, agent, tracked = make(history)
    asyncio.run(sched.schedule_next_step(ctx(agent)))
    assert agent.state == WORKER_STATE_DECOMPOSE and tracked == []
    manager.schedule_cycle.assert_awaited_once()                 # unchanged behaviour: continue decomposing


def test_a_task_that_cannot_be_worked_on_blocks_the_promotion():
    sched, manager, agent, tracked = make(WROTE_TESTS, tracking_error="Task '780cd441' is already marked as 'finished'.")
    asyncio.run(sched.schedule_next_step(ctx(agent)))
    assert agent.state == WORKER_STATE_DECOMPOSE
    manager.schedule_cycle.assert_awaited_once()


def test_a_rejected_state_change_keeps_the_worker_in_decompose():
    sched, manager, agent, _ = make(WROTE_TESTS, change_ok=False)
    asyncio.run(sched.schedule_next_step(ctx(agent)))
    assert agent.state == WORKER_STATE_DECOMPOSE
    assert not any("[Framework Notice]: You already started" in m.get("content", "") for m in agent.message_history)
    manager.schedule_cycle.assert_awaited_once()


def test_unexpected_errors_never_break_scheduling():
    sched, manager, agent, _ = make(WROTE_TESTS)

    def boom(agent, state, task_id=None):
        raise RuntimeError("db locked")
    manager.cycle_handler._handle_worker_task_tracking = boom
    asyncio.run(sched.schedule_next_step(ctx(agent)))
    assert agent.state == WORKER_STATE_DECOMPOSE
    manager.schedule_cycle.assert_awaited_once()


# ------------------------------------------------------------------------------------------------ 2. code_editor
def ce(tmp_path, chunks):
    from src.tools.code_editor import CodeEditorTool
    return asyncio.run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks",
                                                filename="t.py", chunks=chunks))


SOURCE = "a = 1\nb = 2\nc = 3\n"


def test_empty_search_is_not_ambiguous_it_is_absent(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result_ = ce(tmp_path, [{"start_line": 2, "end_line": None, "search": "", "replace": ""}])   # the observed chunk
    assert result_["status"] == "success", result_
    assert (tmp_path / "t.py").read_text() == "a = 1\nc = 3\n"


def test_empty_search_without_lines_is_still_an_error(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result_ = ce(tmp_path, [{"search": "  ", "replace": "x = 0"}])
    assert result_["status"] == "error" and "found" not in result_["message"]
    assert "Provide either 'start_line'/'end_line' OR 'search'" in result_["message"]
    assert (tmp_path / "t.py").read_text() == SOURCE


def test_missing_replace_explains_how_to_delete(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result_ = ce(tmp_path, [{"end_line": 3, "start_line": None}])
    assert result_["status"] == "error" and '"replace": ""' in result_["message"] and "DELETE" in result_["message"]


def test_normal_search_and_line_edits_are_unchanged(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    assert ce(tmp_path, [{"search": "b = 2", "replace": "b = 20"}])["status"] == "success"
    assert ce(tmp_path, [{"start_line": 3, "end_line": 3, "replace": "c = 30"}])["status"] == "success"
    assert (tmp_path / "t.py").read_text() == "a = 1\nb = 20\nc = 30\n"


# ------------------------------------------------------------------------------------------------ 3. replace_lines aliases
@pytest.mark.parametrize("key", ["replacement_content", "new_content", "new_text", "new_lines"])
def test_replace_lines_content_aliases(tmp_path, key):
    from src.tools.file_system import FileSystemTool
    (tmp_path / "a.py").write_text(SOURCE)
    result_ = asyncio.run(FileSystemTool().execute("W1", tmp_path, scope="private", action="replace_lines", path="a.py",
                                                   start_line=2, end_line=2, **{key: "b = 22"}))
    assert result_["status"] == "success", result_
    assert (tmp_path / "a.py").read_text() == "a = 1\nb = 22\nc = 3\n"


# ------------------------------------------------------------------------------------------------ 4. Ollama finish statistics
def test_finish_statistics_are_logged(caplog):
    from src.llm_providers.ollama_provider import log_ollama_finish
    caplog.set_level(logging.INFO, logger="src.llm_providers.ollama_provider")
    data = {"done": True, "done_reason": "stop", "eval_count": 412, "prompt_eval_count": 9001,
            "message": {"content": "", "thinking": "x" * 1500, "tool_calls": [{"function": {}}]}}
    assert log_ollama_finish(data) == "stop"
    line = caplog.records[-1]
    assert line.levelno == logging.INFO
    for part in ("done_reason=stop", "prompt_tokens=9001", "output_tokens=412", "thinking_chars=1500", "tool_calls=1"):
        assert part in line.getMessage()


def test_a_cut_off_reply_is_a_warning(caplog):
    from src.llm_providers.ollama_provider import log_ollama_finish
    caplog.set_level(logging.INFO, logger="src.llm_providers.ollama_provider")
    assert log_ollama_finish({"done": True, "done_reason": "length", "eval_count": 1024, "message": {}}, thinking_chars=3900) == "length"
    assert caplog.records[-1].levelno == logging.WARNING and "CUT OFF" in caplog.records[-1].getMessage()
    assert "thinking_chars=3900" in caplog.records[-1].getMessage()


@pytest.mark.parametrize("data", [None, "x", {}, {"done": False, "done_reason": "length"}, {"done": True, "message": "oops"}])
def test_finish_logging_never_raises(data):
    from src.llm_providers.ollama_provider import log_ollama_finish
    out = log_ollama_finish(data)
    assert out in (None, "?")
