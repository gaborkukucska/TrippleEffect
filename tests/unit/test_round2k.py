# START OF FILE tests/unit/test_round2k.py
"""
Round 2k: fixtures from the 2026-10-20 07:49-12:02 run with qwen3.5:9b (project Temp_Converter_CLI, session
startup_1792453746).

* 10:19-11:03 - W1 was in worker_report, where only send_message, mark_message_read and project_management are offered,
  and still ran code_editor / file_system edits for 17 cycles.
* 10:23-10:58 - PM1 (pm_report_check) edited src/temp_convert.py itself (code_editor replace_chunks, file_system
  find_replace) while W1 edited the same lines; both kept failing on line numbers the other had just shifted.
* 10:31 and 11:12 - W1 sent file_system {'action': 'write_with_lines', 'filename': ..., 'lines': '<whole file>'} and was
  told "'content' parameter is required".
"""
import asyncio
import logging
import types

import pytest

from src.agents.tool_activity import (
    FRAMEWORK_INTERNAL_ARG, is_file_change_call, state_tool_allowlist, state_tool_block_message,
)

PM_CHUNK_CALL = {"action": "replace_chunks", "filename": "src/temp_convert.py",
                 "chunks": [{"start_line": 68, "end_line": 80, "replace": "    # Same units - return value unchanged\n"}]}
PM_FIND_REPLACE = {"action": "find_replace", "filename": "src/temp_convert.py",
                   "find_text": "    else:  # source is F", "replace_text": "    elif source_unit == 'F':"}


# --- the rules ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["worker_report", "worker_wait"])
def test_a_worker_reporting_or_waiting_cannot_edit(state):
    message = state_tool_block_message("worker", state, "code_editor", dict(PM_CHUNK_CALL))
    assert message.startswith(f"'code_editor' cannot be used in the '{state}' state, so NOTHING was done.")
    assert "mark_message_read, project_management, send_message" in message
    assert "state='worker_work'" in message
    assert state_tool_block_message("worker", state, "file_system", {"action": "read", "filename": "src/temp_convert.py"})
    for tool in ("send_message", "mark_message_read", "project_management"):
        assert state_tool_block_message("worker", state, tool, {}) is None


@pytest.mark.parametrize("state", ["worker_work", "worker_test", "worker_decompose"])
def test_a_working_worker_still_edits(state):
    assert state_tool_block_message("worker", state, "code_editor", dict(PM_CHUNK_CALL)) is None
    assert state_tool_block_message("worker", state, "file_system", dict(PM_FIND_REPLACE)) is None


@pytest.mark.parametrize("state", ["pm_report_check", "pm_manage", "pm_audit", "pm_activate_workers"])
def test_a_pm_does_not_change_project_files(state):
    for tool, args in (("code_editor", PM_CHUNK_CALL), ("file_system", PM_FIND_REPLACE),
                       ("file_system", {"action": "write_with_lines", "filename": "README.md", "lines": "x"}),
                       ("file_system", {"action": "delete", "path": "src/temp_convert.py"})):
        message = state_tool_block_message("pm", state, tool, dict(args))
        assert message and message.startswith("A Project Manager does not change project files")
        assert "send_message" in message
    assert "on 'src/temp_convert.py' was NOT done" in state_tool_block_message("pm", state, "code_editor", dict(PM_CHUNK_CALL))
    # reading, listing and other tools stay allowed
    for tool, args in (("file_system", {"action": "read", "filename": "src/temp_convert.py", "start_line": 62}),
                       ("file_system", {"action": "list", "path": "src"}),
                       ("code_editor", {"action": "read_file_content", "filename": "src/temp_convert.py"}),
                       ("project_management", {"action": "list_tasks"}), ("send_message", {"target_agent_id": "W1"})):
        assert state_tool_block_message("pm", state, tool, dict(args)) is None


def test_admin_and_unknown_states_are_not_restricted():
    assert state_tool_block_message("admin", "work", "code_editor", dict(PM_CHUNK_CALL)) is None
    assert state_tool_allowlist("worker", "worker_work") is None and state_tool_allowlist("pm", "pm_manage") is None
    assert state_tool_allowlist("worker", "worker_report") == {"send_message", "mark_message_read", "project_management"}


def test_is_file_change_call():
    assert is_file_change_call("file_system", {"action": "write>"})
    assert is_file_change_call("code_editor", '{"action": "replace_chunks"}')
    assert not is_file_change_call("file_system", {"action": "read_with_lines"})
    assert not is_file_change_call("code_editor", {"action": "view"})
    assert not is_file_change_call("command_executor", {"action": "run_command"})


# --- the executor enforces them -------------------------------------------------------------------------------------------
@pytest.fixture
def executor():
    from src.tools.executor import ToolExecutor
    logging.disable(logging.CRITICAL)
    yield ToolExecutor()
    logging.disable(logging.NOTSET)


def _manager(**agents):
    return types.SimpleNamespace(agents={agent_id: types.SimpleNamespace(agent_id=agent_id, agent_type=agent_type, state=state)
                                         for agent_id, (agent_type, state) in agents.items()})


def test_the_executor_refuses_a_pm_edit(executor, tmp_path):
    result = asyncio.run(executor.execute_tool("PM1", tmp_path, "code_editor", dict(PM_CHUNK_CALL),
                                               manager=_manager(PM1=("pm", "pm_report_check"))))
    assert isinstance(result, str) and result.startswith("[Tool Error: code_editor] A Project Manager does not change project files")


def test_the_executor_refuses_edits_in_worker_report(executor, tmp_path):
    args = {"action": "write", "filename": "notes.txt", "content": "x", "scope": "private"}
    result = asyncio.run(executor.execute_tool("W1", tmp_path, "file_system", dict(args),
                                               manager=_manager(W1=("worker", "worker_report"))))
    assert result.startswith("[Tool Error: file_system] 'file_system' cannot be used in the 'worker_report' state")
    assert not (tmp_path / "notes.txt").exists()


def test_a_framework_call_on_behalf_of_the_agent_still_runs(executor, tmp_path):
    args = {"action": "write", "filename": "notes.txt", "content": "x", "scope": "private", FRAMEWORK_INTERNAL_ARG: True}
    result = asyncio.run(executor.execute_tool("W1", tmp_path, "file_system", args,
                                               manager=_manager(W1=("worker", "worker_report"))))
    assert "Tool Error" not in str(result)
    assert (tmp_path / "notes.txt").read_text() == "x"


def test_a_working_worker_writes(executor, tmp_path):
    args = {"action": "write", "filename": "notes.txt", "content": "x", "scope": "private"}
    asyncio.run(executor.execute_tool("W1", tmp_path, "file_system", args, manager=_manager(W1=("worker", "worker_work"))))
    assert (tmp_path / "notes.txt").read_text() == "x"


# --- 'lines' is the content of a write --------------------------------------------------------------------------------------
TEST_FILE_11_12 = '"""Tests for temperature conversion utilities."""\n\n\ndef test_celsius_to_fahrenheit():\n    assert True\n'


def test_write_with_lines_takes_lines_as_content(tmp_path):
    from src.tools.file_system import FileSystemTool
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(FileSystemTool().execute(
            "W1", tmp_path, action="write_with_lines", filename="tests/test_temp_convert.py", lines=TEST_FILE_11_12,
            scope="private"))
        assert result["status"] == "success", result
        assert (tmp_path / "tests" / "test_temp_convert.py").read_text() == TEST_FILE_11_12
        result = asyncio.run(FileSystemTool().execute(
            "W1", tmp_path, action="write", filename="b.txt", lines=["first", "second"], scope="private"))
        assert result["status"] == "success" and (tmp_path / "b.txt").read_text() == "first\nsecond\n"
        result = asyncio.run(FileSystemTool().execute(
            "W1", tmp_path, action="write", filename="c.txt", lines=[1, 2], scope="private"))
        assert result["status"] == "error" and "'content' parameter is required" in result["message"]
    finally:
        logging.disable(logging.NOTSET)
