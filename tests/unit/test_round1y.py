# START OF FILE tests/unit/test_round1y.py
"""Round 1y tests (run 2026-10-17 15:16 - 2026-10-18 05:48, Temp_Converter_CLI: src/temp_convert.py ended EMPTY).

1. 18:09:13 W1 sent code_editor chunks with a line range (.. end_line 86) AND a 'search_block' plus 'replace_text': ''. The
   line numbers silently won, the search text was ignored and the whole file was deleted ("Successfully applied 2 edits").
2. Nothing stopped an edit from leaving a file that had content empty.
3. The empty file then could not be repaired for 11.5 hours: 'write' was refused 38 times ("already exists"), reading it
   answered "Tool execution was successful.", ranged reads/edits failed with "range for file with 0 lines".
4. code_editor 'replace_chunks>' / 'read_file_with_lines', a 'chunks' string with trailing junk ("Extra data").
5. file_system action 'code_editor', 'write_with_content', 'write_with_lines', 'read_with_content', 'list_with_tree'.
6. Duplicate reads: a read after an edit returned the stale cached result; each duplicate read cost a Guardian LLM call
   (59 calls, all cut off at 150 tokens).
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.tools.code_editor import CodeEditorTool, _line_range_conflict, _loads_chunks, emptied_file_error
from src.tools.file_system import FileSystemTool
from src.agents.tool_activity import files_changed_after, is_file_read_call, read_call_target

SOURCE = (
    "# Import core functions from the same file\n"
    "import sys\n"
    "\n"
    "def c_to_f(c):\n"
    "    return c * 9 / 5 + 32\n"
    "\n"
    "def f_to_c(f):\n"
    "    return (f - 32) * 5 / 9\n"
)


def ce(tmp_path, chunks, filename="t.py", **extra):
    return asyncio.run(CodeEditorTool().execute("W1", tmp_path, scope="private", action=extra.pop("action", "replace_chunks"),
                                                filename=filename, chunks=chunks, **extra))


def fs(tmp_path, **kwargs):
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, scope="private", **kwargs))


# ------------------------------------------------------------------------------------------------ 1. lines vs search
def test_the_observed_chunk_no_longer_deletes_the_file(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    # search text sits at line 1 but the range starts at line 4: the two point to different code
    result = ce(tmp_path, [{"start_line": 4, "end_line": 86, "replace_text": "",
                            "search_block": "# Import core functions from the same file"}])
    assert result["status"] == "error"
    assert "DIFFERENT code" in result["message"] and "line(s) 1" in result["message"] and "4: def c_to_f(c):" in result["message"]
    assert (tmp_path / "t.py").read_text() == SOURCE


def test_search_text_that_is_not_in_the_file_blocks_the_line_edit(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result = ce(tmp_path, [{"start_line": 4, "end_line": 5, "search": "def nothing_like_this():", "replace": "x = 1"}])
    assert result["status"] == "error" and "not in the file" in result["message"]
    assert (tmp_path / "t.py").read_text() == SOURCE


def test_lines_that_start_with_the_search_text_are_replaced(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    # the 18:36 pattern: the range of a function plus its 'def' line as search anchor
    result = ce(tmp_path, [{"start_line": 4, "end_line": 5, "search": "def c_to_f(c):",
                            "replace": "def c_to_f(c):\n    return c * 1.8 + 32"}])
    assert result["status"] == "success", result
    assert "return c * 1.8 + 32\n\ndef f_to_c" in (tmp_path / "t.py").read_text()


def test_whole_range_as_search_with_other_indentation_is_accepted(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result = ce(tmp_path, [{"start_line": 7, "end_line": 8, "search": "def f_to_c(f):\n  return (f - 32) * 5 / 9   ",
                            "replace": "def f_to_c(f):\n    return (f - 32) / 1.8"}])
    assert result["status"] == "success", result
    assert (tmp_path / "t.py").read_text().endswith("return (f - 32) / 1.8\n")


@pytest.mark.parametrize("search", ["", "   ", None])
def test_line_edits_without_search_are_unchanged(tmp_path, search):
    (tmp_path / "t.py").write_text(SOURCE)
    chunk = {"start_line": 2, "end_line": 2, "replace": "import os"}
    if search is not None:
        chunk["search"] = search
    assert ce(tmp_path, [chunk])["status"] == "success"
    assert (tmp_path / "t.py").read_text() == SOURCE.replace("import sys", "import os")


def test_line_range_conflict_helper():
    lines = SOURCE.splitlines(True)
    assert _line_range_conflict(lines, 3, 5, "def c_to_f(c):") is None
    assert _line_range_conflict(lines, 3, 5, "\n  def c_to_f(c):  \n    return c * 9 / 5 + 32\n") is None
    assert "DIFFERENT" in _line_range_conflict(lines, 3, 5, "def f_to_c(f):")
    assert "DIFFERENT" in _line_range_conflict(lines, 3, 4, "def c_to_f(c):\n    return c * 9 / 5 + 32")  # longer than range


# ------------------------------------------------------------------------------------------------ 2. never empty a file
def test_code_editor_refuses_to_empty_a_file(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result = ce(tmp_path, [{"start_line": 1, "end_line": 86, "replace": ""}])
    assert result["status"] == "error" and "delete ALL content" in result["message"] and "force_overwrite" in result["message"]
    assert (tmp_path / "t.py").read_text() == SOURCE


def test_code_editor_still_deletes_some_lines(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    assert ce(tmp_path, [{"start_line": 1, "end_line": 3, "replace": ""}])["status"] == "success"
    assert (tmp_path / "t.py").read_text() == SOURCE.split("\n", 3)[3]


def test_replace_lines_refuses_to_empty_a_file(tmp_path):
    (tmp_path / "notes.md").write_text("# Notes\nline\n")
    result = fs(tmp_path, action="replace_lines", path="notes.md", start_line=1, end_line=2, content="")
    assert result["status"] == "error" and "delete ALL content" in result["message"]
    assert (tmp_path / "notes.md").read_text() == "# Notes\nline\n"


def test_find_replace_refuses_to_empty_a_file(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    result = fs(tmp_path, action="find_replace", path="a.py", find_text="x = 1\n", replace_text="")
    assert result["status"] == "error" and "delete ALL content" in result["message"]
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_search_replace_block_refuses_to_empty_a_file(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    result = fs(tmp_path, action="search_replace_block", path="a.py", search_block="x = 1\ny = 2\n", replace_block="")
    assert result["status"] == "error" and "delete ALL content" in result["message"]
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 2\n"


def test_search_replace_block_normal_edit_is_unchanged(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    result = fs(tmp_path, action="search_replace_block", path="a.py", search_block="y = 2", replace_block="y = 3")
    assert result["status"] == "success", result
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 3\n"


def test_emptied_file_error_helper():
    assert emptied_file_error("a.py", "x = 1\n", "") and emptied_file_error("a.py", "x = 1\n", " \n\n")
    assert emptied_file_error("a.py", "", "") is None            # was already empty
    assert emptied_file_error("a.py", "x = 1\n", "y = 2\n") is None


def test_write_with_force_overwrite_can_still_empty_a_file(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert fs(tmp_path, action="write", path="a.py", content="", force_overwrite=True)["status"] == "success"
    assert (tmp_path / "a.py").read_text() == ""


# ------------------------------------------------------------------------------------------------ 3. repairing an empty file
def test_an_empty_file_can_be_written_without_force_overwrite(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("")
    result = fs(tmp_path, action="write", path="src/m.py", content="x = 1\n")
    assert result["status"] == "success" and "was empty" in result["message"]
    assert (tmp_path / "src" / "m.py").read_text() == "x = 1\n"


def test_a_file_with_content_still_needs_force_overwrite(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    result = fs(tmp_path, action="write", path="m.py", content="x = 2\n")
    assert result["status"] == "error" and "already exists" in result["message"]
    assert (tmp_path / "m.py").read_text() == "x = 1\n"


@pytest.mark.parametrize("extra", [{}, {"start_line": 1, "end_line": 30}])
def test_reading_an_empty_file_says_it_is_empty(tmp_path, extra):
    (tmp_path / "m.py").write_text("\n")
    result = fs(tmp_path, action="read", path="m.py", **extra)
    assert result["status"] == "success" and "is EMPTY" in result["message"] and "'write'" in result["message"]


def test_reading_a_normal_file_is_unchanged(tmp_path):
    (tmp_path / "m.py").write_text("a\nb\n")
    assert fs(tmp_path, action="read", path="m.py") == {"status": "success", "content": "1: a\n2: b\n"}


@pytest.mark.parametrize("end_line", [0, 1, 5])
def test_code_editor_inserts_into_an_empty_file(tmp_path, end_line):
    (tmp_path / "t.py").write_text("")
    result = ce(tmp_path, [{"start_line": 1, "end_line": end_line, "replace": "x = 1\n"}])
    assert result["status"] == "success", result
    assert (tmp_path / "t.py").read_text() == "x = 1\n"


def test_bad_ranges_get_a_clear_message(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result = ce(tmp_path, [{"start_line": 3, "end_line": -1, "replace": "x"}])
    assert result["status"] == "error" and "numbered 1 to 8" in result["message"]
    (tmp_path / "e.py").write_text("")
    result = ce(tmp_path, [{"start_line": 4, "end_line": 6, "replace": "x"}], filename="e.py")
    assert result["status"] == "error" and "EMPTY" in result["message"]


# ------------------------------------------------------------------------------------------------ 4. code_editor input
def test_stray_tag_character_in_action(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    assert ce(tmp_path, [{"start_line": 2, "end_line": 2, "replace": "import os"}], action="replace_chunks>")["status"] == "success"


@pytest.mark.parametrize("action", ["read_file_with_lines", "read", "view"])
def test_code_editor_read_actions_are_answered(tmp_path, action):
    (tmp_path / "t.py").write_text("a\n")
    result = asyncio.run(CodeEditorTool().execute("W1", tmp_path, scope="private", action=action, path="t.py"))
    assert result["status"] == "success" and result["content"] == "1: a\n" and "only edits" in result["message"]


def test_chunks_string_with_trailing_junk(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    chunks = json.dumps([{"start_line": 2, "end_line": 2, "replace": "import os"}]) + "]}"
    assert ce(tmp_path, chunks)["status"] == "success"
    assert "import os" in (tmp_path / "t.py").read_text()


def test_loads_chunks_only_ignores_brackets():
    assert _loads_chunks('[{"a": 1}]\n]`') == [{"a": 1}]
    with pytest.raises(json.JSONDecodeError):
        _loads_chunks('[{"a": 1}] , {"b": 2}]')
    with pytest.raises(json.JSONDecodeError):
        _loads_chunks('[{"a": 1')


# ------------------------------------------------------------------------------------------------ 5. file_system actions
def test_file_system_action_code_editor_with_chunks_is_run(tmp_path):
    (tmp_path / "t.py").write_text(SOURCE)
    result = fs(tmp_path, action="code_editor", path="t.py", chunks=[{"start_line": 2, "end_line": 2, "replace": "import os"}])
    assert result["status"] == "success" and "code_editor" in result["message"]
    assert "import os" in (tmp_path / "t.py").read_text()


@pytest.mark.parametrize("action", ["code_editor", "replace_chunks"])
def test_file_system_action_code_editor_without_chunks_explains(tmp_path, action):
    (tmp_path / "t.py").write_text(SOURCE)
    result = fs(tmp_path, action=action, path="t.py")
    assert result["status"] == "error" and "separate TOOL" in result["message"]


@pytest.mark.parametrize("action,expected", [("write_with_content", "write"), ("write_with_lines", "write"),
                                             ("read_with_content", "read"), ("list_with_tree", "tree")])
def test_new_action_aliases(tmp_path, action, expected):
    (tmp_path / "t.txt").write_text("a\n")
    kwargs = {"path": "new.txt", "content": "b\n"} if expected == "write" else {"path": "t.txt" if expected == "read" else "."}
    result = fs(tmp_path, action=action, **kwargs)
    assert result["status"] == "success", result
    if expected == "write":
        assert (tmp_path / "new.txt").read_text() == "b\n"


# ------------------------------------------------------------------------------------------------ 6. duplicate reads
def call(call_id, name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": args}]}


def result_(call_id, name, content):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


READ = call("r1", "file_system", action="read", path="src/m.py")
READ_RESULT = result_("r1", "file_system", "1: x = 1")
EDIT = call("e1", "code_editor", action="replace_chunks", filename="src/m.py", chunks=[])
EDIT_OK = result_("e1", "code_editor", "Successfully applied 1 edits to 'src/m.py'.")
EDIT_FAILED = result_("e1", "code_editor", "[Tool Execution Failed] Code edit failed: 0 chunks succeeded")


@pytest.mark.parametrize("name,args,expected", [
    ("file_system", {"action": "read", "path": "a"}, True),
    ("file_system", {"action": "read_with_lines>", "path": "a"}, True),
    ("file_system", {"action": "list", "path": "."}, True),
    ("code_editor", {"action": "read_file_with_lines", "path": "a"}, True),
    ("file_system", {"action": "write", "path": "a"}, False),
    ("code_editor", {"action": "replace_chunks"}, False),
    ("project_management", {"action": "list_tasks"}, False),
])
def test_is_file_read_call(name, args, expected):
    assert is_file_read_call(name, args) is expected


def test_read_call_target():
    assert read_call_target({"action": "read", "path": "src/m.py"}) == "src/m.py"
    assert read_call_target({"action": "list"}) == "."


def test_files_changed_after():
    assert files_changed_after([READ, READ_RESULT, EDIT, EDIT_OK], 0) is True
    assert files_changed_after([READ, READ_RESULT, EDIT, EDIT_FAILED], 0) is False
    assert files_changed_after([EDIT, EDIT_OK, READ, READ_RESULT], 2) is False   # the edit came before the read
    assert files_changed_after([READ, READ_RESULT], 0) is False


def make_cycle_handler():
    from src.agents.cycle_handler import AgentCycleHandler
    return AgentCycleHandler.__new__(AgentCycleHandler)   # the duplicate check uses no instance state


def test_a_read_after_an_edit_is_not_a_duplicate():
    handler = make_cycle_handler()
    agent = SimpleNamespace(message_history=[{"role": "system", "content": "s"}, READ, READ_RESULT, EDIT, EDIT_OK])
    assert handler._detect_cross_cycle_duplicate_tool_call(agent, "file_system", {"action": "read", "path": "src/m.py"}) is None


def test_a_repeated_read_without_changes_is_still_a_duplicate():
    handler = make_cycle_handler()
    agent = SimpleNamespace(message_history=[{"role": "system", "content": "s"}, READ, READ_RESULT, EDIT, EDIT_FAILED])
    assert handler._detect_cross_cycle_duplicate_tool_call(agent, "file_system", {"action": "read", "path": "src/m.py"}) == "1: x = 1"


def test_other_duplicates_are_unchanged():
    handler = make_cycle_handler()
    listing = call("p1", "project_management", action="list_tasks")
    agent = SimpleNamespace(message_history=[{"role": "system", "content": "s"}, listing,
                                             result_("p1", "project_management", '{"status": "success", "tasks": []}'),
                                             EDIT, EDIT_OK])
    assert handler._detect_cross_cycle_duplicate_tool_call(agent, "project_management", {"action": "list_tasks"}) is not None


def make_guardian(llm_text="ALLOW"):
    from src.agents.cycle_components.agent_health_monitor import ConstitutionalGuardianHealthMonitor
    monitor = ConstitutionalGuardianHealthMonitor.__new__(ConstitutionalGuardianHealthMonitor)

    async def stream(**kwargs):
        yield {"type": "response_chunk", "content": llm_text}
    provider = MagicMock()
    provider.stream_completion = MagicMock(side_effect=lambda **kw: stream(**kw))
    monitor._manager = SimpleNamespace(agents={"constitutional_guardian_ai": SimpleNamespace(llm_provider=provider, model="m")})
    monitor._analyze_agent_history = AsyncMock(return_value={"history_length": 5})
    return monitor, provider


def test_duplicate_read_is_answered_without_the_guardian_llm():
    monitor, provider = make_guardian()
    agent = SimpleNamespace(agent_id="W1", state="worker_work")
    verdict, feedback = asyncio.run(monitor.evaluate_duplicate_tool_call(agent, "file_system", {"action": "read", "path": "src/m.py"}, 1))
    assert verdict == "BLOCK_AND_GUIDE" and "'src/m.py'" in feedback and "request_state" in feedback
    provider.stream_completion.assert_not_called()


def test_other_duplicates_still_ask_the_guardian_llm():
    monitor, provider = make_guardian("ALLOW")
    agent = SimpleNamespace(agent_id="PM1", state="pm_manage")
    verdict, _ = asyncio.run(monitor.evaluate_duplicate_tool_call(agent, "project_management", {"action": "add_task"}, 1))
    assert verdict == "ALLOW"
    provider.stream_completion.assert_called_once()

# END OF FILE tests/unit/test_round1y.py
