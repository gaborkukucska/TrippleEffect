# START OF FILE tests/unit/test_round2c.py
"""Round 2c tests (run 2026-10-18 21:16-22:06, Temp_Converter_CLI_Tool).

1. 'chunks' strings mixing JSON and Python ("end_line": null with 'single-quoted' search texts) were rejected 7 times.
2. code_editor action 'write_file_content' with 'file_contents' was rejected: "Missing required parameters: chunks".
3. PM1's standby was blocked as "project finished" after a FILTERED task list (todo, not assigned) came back empty.
4. Normal progress messages were logged at CRITICAL and filled the error scan.
"""
import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.tools.code_editor import CodeEditorTool, _literal_eval_chunks, is_write_action
from src.agents.tool_activity import pm_standby_block_message

SOURCE = '"""Temperature conversion module for CLI tool."""\n\n\ndef c_to_f(c):\n    return c * 9 / 5 + 32\n\n\nif __name__ == "__main__":\n    print(c_to_f(100))\n'


def ce(tmp_path, chunks, **extra):
    return asyncio.run(CodeEditorTool().execute("W1", tmp_path, scope="private", action=extra.pop("action", "replace_chunks"),
                                                filename="t.py", chunks=chunks, **extra))


# ------------------------------------------------------------------------------------------------ 1. mixed JSON/Python chunks
def test_the_observed_mixed_chunk_is_applied(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    chunks = ('[{"start_line": 8, "end_line": null, "search": \'if __name__ == "__main__":\', '
              '"replace": "if __name__ == \\"__main__\\":  # CLI"}]')
    with pytest.raises(json.JSONDecodeError):
        json.loads(chunks)                                       # what the 4B model sends is not JSON
    result = ce(tmp_path, chunks)
    assert result["status"] == "success", result
    assert 'if __name__ == "__main__":  # CLI\n' in (tmp_path / "t.py").read_text()


def test_null_true_false_inside_strings_are_kept():
    text = '[{"search": \'x = null or true\', "replace": "flag = false", "end_line": null, "dry": true, "x": false}]'
    assert _literal_eval_chunks(text) == [{"search": "x = null or true", "replace": "flag = false", "end_line": None,
                                           "dry": True, "x": False}]


@pytest.mark.parametrize("text", ['[{"search": \'a\', "replace": nul}]', '[{"search": \'a\'', "__import__('os')"])
def test_still_invalid_text_raises(text):
    with pytest.raises((ValueError, SyntaxError)):
        _literal_eval_chunks(text)


def test_a_broken_chunk_string_is_still_an_error(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    # the 21:39:22 call: the "replace" string ended early (...Fahrenheit.\"", then code outside the string)
    chunks = '[{"start_line": 87, "end_line": null, "search": \'if x:\', "replace": "a = \\"b\\"",\n        c = d}]'
    result = ce(tmp_path, chunks)
    assert result["status"] == "error" and "not valid JSON" in result["message"]
    assert (tmp_path / "t.py").read_text() == SOURCE


# ------------------------------------------------------------------------------------------------ 2. write actions
@pytest.mark.parametrize("action,expected", [("write", True), ("write_file_content", True), ("create_file", True),
                                             ("save_file", True), ("overwrite_file", True), ("replace_chunks", False),
                                             ("read", False), (None, False)])
def test_is_write_action(action, expected):
    assert is_write_action(action) is expected


@pytest.mark.parametrize("key", ["file_contents", "contents", "code", "content"])
def test_write_file_content_with_file_contents_creates_the_file(tmp_path, key):
    result = asyncio.run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="write_file_content",
                                                  filename="new.py", **{key: "x = 1\n"}))
    assert result["status"] == "success", result
    assert (tmp_path / "new.py").read_text() == "x = 1\n"


def test_write_action_passes_the_executor(tmp_path):
    from src.tools.executor import ToolExecutor
    manager = SimpleNamespace(agents={"W1": SimpleNamespace(agent_type="worker", state="worker_work")})
    result = asyncio.run(ToolExecutor().execute_tool("W1", tmp_path, "code_editor", {
        "action": "write_file_content", "filename": "new.py", "file_contents": "x = 1\n"}, manager=manager))
    assert "Successfully wrote" in str(result) and (tmp_path / "new.py").read_text() == "x = 1\n"


def test_write_action_on_an_existing_file_still_needs_force_overwrite(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result = asyncio.run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="write_file_content",
                                                  filename="t.py", file_contents="x = 1\n"))
    assert result["status"] == "error" and "already exists" in result["message"]
    assert (tmp_path / "t.py").read_text() == SOURCE


# ------------------------------------------------------------------------------------------------ 3. filtered task lists
def listing(call_id, args, tasks):
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": "project_management",
                                                             "arguments": dict(args, action="list_tasks")}]},
        {"role": "tool", "tool_call_id": call_id, "name": "project_management",
         "content": json.dumps({"status": "success", "message": f"Found {len(tasks)} task(s).", "tasks": tasks})},
    ]


OBSERVED_FILTERS = {"task_progress_filter": "todo", "tags_filter_mode": "exclude", "tags_filter": "assigned",
                    "project_filter": "Temp_Converter_CLI_Tool"}


def block(history):
    return pm_standby_block_message("pm", "pm_manage", "pm_standby", history, "PM1")


def test_an_empty_filtered_listing_is_not_proof_that_the_project_is_finished():
    message = block(listing("c1", OBSERVED_FILTERS, []))
    assert message and "FILTERED" in message and "task_progress_filter='todo'" in message and "tags_filter='assigned'" in message
    assert "project_filter" not in message and "work is finished" not in message


def test_an_empty_unfiltered_listing_still_means_finished():
    message = block(listing("c1", {"project_filter": "Temp_Converter_CLI_Tool"}, []))
    assert "work is finished" in message


def test_a_listing_with_open_tasks_allows_standby_filtered_or_not():
    open_task = [{"uuid": "a", "assignee": "W1", "task_progress": "in_progress"}]
    assert block(listing("c1", OBSERVED_FILTERS, open_task)) is None
    assert block(listing("c1", {}, open_task)) is None


def test_listing_without_a_matching_call_is_treated_as_unfiltered():
    history = listing("c1", OBSERVED_FILTERS, [])
    history[0]["tool_calls"][0]["id"] = "other"
    assert "work is finished" in block(history)


# ------------------------------------------------------------------------------------------------ 4. log levels
def test_cycle_progress_messages_are_not_critical():
    import inspect
    from src.agents import cycle_handler
    source = inspect.getsource(cycle_handler)
    for text in ("run_cycle TASK STARTED", "PERSISTENT HISTORY UPDATED", "workflow_executed event data",
                 "CLEANED JSON error spam", "allowing natural processing"):
        line = next(l for l in source.splitlines() if text in l)
        assert "logger.info(" in line and "logger.critical(" not in line, line
    assert "logger.critical(error_message)" in source               # real problems stay CRITICAL

# END OF FILE tests/unit/test_round2c.py
