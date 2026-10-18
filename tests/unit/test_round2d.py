# START OF FILE tests/unit/test_round2d.py
"""Round 2d tests (run 2026-10-18 22:17-23:59, Temp_Converter_CLI, qwen3.5:4b).

W1 needed 70 minutes and about 30 failed tool calls to replace ONE broken main() in src/temp_convert.py. Most failures were
the framework's, not the model's:
1. The watchdog re-activated W1 for its own task (progress 'doing') into worker_decompose, where replies are capped at 1024
   tokens: 29 cycles there, 4 complete-file replies cut off - and each cut-off was reported as "Your last response was empty".
2. code_editor: one edit as top-level search_block/replace_block (7x "Missing required parameters: chunks"), 'chunks' with an
   invalid JSON escape and a missing closing bracket (2x), line numbers one line off from a search text that matched exactly
   once (6x "DIFFERENT code"), a search fragment inside the given lines (4x).
3. file_system: the complete, corrected file was refused 6x ("already exists") although it kept every function; start_line
   '1, 60' (2x); 'line_content' instead of 'content'; replace_lines with find/replace text but no line numbers (3x).
"""
import asyncio
import json
import shutil
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.tools.code_editor import (CodeEditorTool, _close_open_brackets, _repair_json_escapes, parse_line_numbers,
                                   single_chunk_from_args, unique_search_near_lines)
from src.tools.executor import ToolExecutor, split_line_range
from src.tools.file_system import FileSystemTool
from src.tools.python_static_check import python_rewrite_problems

try:
    import tasklib  # noqa: F401
    HAVE_TW = shutil.which("task") is not None
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

# src/temp_convert.py exactly as W1 wrote it at 22:28:54 (53 lines; main() at line 44 is a fake-argparse stub)
DOC_C = ('    """Convert temperature from Celsius to Fahrenheit.\n\n    Args:\n        c (float): Temperature in degrees Celsius.\n\n'
         '    Returns:\n        float: Temperature in degrees Fahrenheit.\n    \n    Formula: F = C * 9/5 + 32\n    \n'
         '    Examples:\n        >>> round(celsius_to_fahrenheit(0), 1)\n        32.0\n        >>> round(celsius_to_fahrenheit(100), 1)\n'
         '        212.0\n    """\n')
DOC_F = ('    """Convert temperature from Fahrenheit to Celsius.\n\n    Args:\n        f (float): Temperature in degrees Fahrenheit.\n\n'
         '    Returns:\n        float: Temperature in degrees Celsius.\n    \n    Formula: C = (F - 32) * 5/9\n    \n'
         '    Examples:\n        >>> round(fahrenheit_to_celsius(100), 1)\n        37.8\n'
         '        >>> fahrenheit_to_celsius(-40) == celsius_to_fahrenheit(-40)\n        True\n    """\n')
HEAD = ('"""Temperature Converter CLI - Core Conversion Functions."""\n\n\ndef celsius_to_fahrenheit(c: float) -> float:\n' + DOC_C
        + '    return c * 9 / 5 + 32\n\n\ndef fahrenheit_to_celsius(f: float) -> float:\n' + DOC_F + '    return (f - 32) * 5 / 9\n\n\n')
STUB_MAIN = ('def main():\n    """CLI entry point using argparse for temperature conversion."""\n    \n    import sys\n    \n'
             "    parser = type('argparse', (), {\n"
             "        'add_argument': lambda self, name=None, dest='value', help='', required=False: None,\n"
             "        'format_help': lambda self: '',\n        'print_usage': lambda self: ''\n    })()\n")
STUB = HEAD + STUB_MAIN
NEW_MAIN = ('def main():\n    """CLI entry point using argparse for temperature conversion."""\n\n    import sys\n    \n'
            "    # Parse command line arguments manually since we're not importing full argparse\n"
            '    if len(sys.argv) < 3:\n        print("Usage: python temp_convert.py [temperature] [unit]")\n        sys.exit(1)\n\n'
            '    try:\n        temperature = float(sys.argv[1])\n    except ValueError:\n'
            "        print(f\"Error: Invalid temperature value '{sys.argv[1]}')\")\n        sys.exit(1)\n\n"
            "    unit = sys.argv[2].upper()\n    if unit == 'C':\n        result = celsius_to_fahrenheit(temperature)\n"
            '        print(f"{temperature}°C = {result:.0f}°F")\n    elif unit == \'F\':\n'
            '        result = fahrenheit_to_celsius(temperature)\n        print(f"{temperature}°F = {result:.1f}°C")\n'
            "    else:\n        print(\"Error: Invalid unit. Use 'C' for Celsius or 'F' for Fahrenheit.\")\n        sys.exit(1)\n")
FIXED = HEAD + NEW_MAIN + '\n\nif __name__ == "__main__":\n    main()\n'   # the file W1 finally wrote at 23:40:37


def run(coro):
    return asyncio.run(coro)


def workspace(tmp_path, text):
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "temp_convert.py").write_text(text)
    return tmp_path / "src" / "temp_convert.py"


def ce(tmp_path, **args):
    return run(CodeEditorTool().execute("W1", tmp_path, scope="private", **args))


def fs(tmp_path, **args):
    return run(FileSystemTool().execute("W1", tmp_path, scope="private", **args))


def executor(tmp_path, tool, args):
    manager = types.SimpleNamespace(agents={"W1": types.SimpleNamespace(agent_type="worker", state="worker_work")})
    return run(ToolExecutor().execute_tool("W1", tmp_path, tool, dict(args, scope="private"), manager=manager))


def test_the_fixture_is_the_observed_file():
    assert len(STUB.splitlines()) == 53 and STUB.splitlines()[43] == "def main():" and STUB.endswith("    })()\n")
    assert len(FIXED.splitlines()) == 73 and FIXED.splitlines()[55] == "    except ValueError:"   # as written at 23:40:37
    compile(STUB, "s", "exec")
    compile(FIXED, "f", "exec")


# ============================================================================================ 1. code_editor: line hints
def test_off_by_one_line_numbers_with_an_exact_search_text_are_applied(tmp_path):
    """23:19:41 (and 23:25:54, 23:26:49, 23:34:16): search starts at line 44, the model said 45-60."""
    path = workspace(tmp_path, STUB)
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"search": STUB_MAIN.rstrip("\n"), "replace": NEW_MAIN.rstrip("\n"), "start_line": 45, "end_line": 60}])
    assert result["status"] == "success", result
    assert "found at line 44" in result["message"] and "read the file again" in result["message"]
    assert path.read_text() == HEAD + NEW_MAIN


def test_whitespace_different_search_text_is_applied_once_near_the_lines(tmp_path):
    """23:21:06: the search text had empty lines where the file has '    ' lines."""
    path = workspace(tmp_path, STUB)
    search = STUB_MAIN.replace("\n    \n", "\n\n").rstrip("\n")
    assert search not in STUB and NEW_MAIN.count("\n    \n") == 1
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"search": search, "replace": NEW_MAIN.rstrip("\n"), "start_line": 45, "end_line": 60}])
    assert result["status"] == "success", result
    assert path.read_text() == HEAD + NEW_MAIN


def test_a_search_fragment_inside_the_given_lines_is_applied(tmp_path):
    """23:43:14: fix the stray ')' inside line 57 of the finished file, lines given as 56-58."""
    path = workspace(tmp_path, FIXED)
    line_57 = FIXED.splitlines()[56]
    assert "'{sys.argv[1]}')\"" in line_57
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"end_line": 58, "replace": "'{sys.argv[1]}'\"", "search": "'{sys.argv[1]}')\"", "start_line": 56}])
    assert result["status"] == "success", result
    assert path.read_text().splitlines()[56] == "        print(f\"Error: Invalid temperature value '{sys.argv[1]}'\")"


@pytest.mark.parametrize("search,start,end", [("def main():", 46, 60), ('"""Temperature Converter CLI', 3, 50)])
def test_search_text_before_the_lines_is_still_refused(tmp_path, search, start, end):
    """Round 1y stays: line numbers that point two or more lines after the search text are a real conflict."""
    path = workspace(tmp_path, STUB)
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"search": search, "replace": "x = 1", "start_line": start, "end_line": end}])
    assert result["status"] == "error" and "DIFFERENT code" in result["message"]
    assert path.read_text() == STUB


def test_one_line_inside_the_range_replaced_by_a_whole_block_is_refused(tmp_path):
    """Lines 44-53 + search for ONE line in the middle + a new 25-line main(): replacing only that line would duplicate code."""
    path = workspace(tmp_path, STUB)
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"search": "    import sys", "replace": NEW_MAIN, "start_line": 44, "end_line": 53}])
    assert result["status"] == "error" and "DIFFERENT code" in result["message"]
    assert path.read_text() == STUB


def test_a_small_fix_of_one_line_inside_the_range_is_applied(tmp_path):
    path = workspace(tmp_path, STUB)
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"search": "    import sys", "replace": "    import sys\n    import os", "start_line": 44, "end_line": 53}])
    assert result["status"] == "success" and "found at line 47" in result["message"]
    assert "    import sys\n    import os\n" in path.read_text()


def test_search_text_that_occurs_twice_near_the_lines_is_refused(tmp_path):
    path = workspace(tmp_path, "a = 1\nx = 0\nb = 2\nx = 0\n")
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"search": "x = 0", "replace": "x = 9", "start_line": 1, "end_line": 4}])
    assert result["status"] == "error"
    assert path.read_text() == "a = 1\nx = 0\nb = 2\nx = 0\n"


def test_search_text_that_is_not_in_the_file_is_still_refused(tmp_path):
    """23:57:32 and 23:59:36: the search text lacked a '{' and is not in the file."""
    path = workspace(tmp_path, FIXED)
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"end_line": 57, "replace": "'sys.argv[1]}'\"", "search": "'sys.argv[1]}')\"", "start_line": 56}])
    assert result["status"] == "error" and "not in the file" in result["message"]
    assert path.read_text() == FIXED


def test_unique_search_near_lines_helper():
    content = "a\nb\nc\nd\n"
    assert unique_search_near_lines(content, "c", 3, 3)[2:] == (3, True)
    assert unique_search_near_lines(content, "c", 4, 4)[2:] == (3, True)       # one line before start_line
    assert unique_search_near_lines(content, "b", 4, 4) is None                 # two lines before
    assert unique_search_near_lines(content, "a\n  b", 1, 2)[2:] == (1, False)  # whitespace-insensitive
    assert unique_search_near_lines(content, "   ", 1, 4) is None
    from src.tools.code_editor import line_hint_edit_allowed
    assert line_hint_edit_allowed(content, 2, 2, 3, "b", "x\ny\nz\nw")            # whole line at start_line - 1: any size
    assert not line_hint_edit_allowed(content, 4, 3, 1, "c", "x\ny\nz\nw")         # inside the range: local fixes only
    assert line_hint_edit_allowed(content, 4, 3, 1, "c", "x\ny\nz")


# ======================================================================== 2. code_editor: arguments the model really sent
def test_top_level_search_and_replace_blocks_pass_the_executor(tmp_path):
    """23:04:07: path, start_line '43', end_line '50', search_block, replace_block, action replace_chunks - no 'chunks'."""
    path = workspace(tmp_path, STUB)
    result = executor(tmp_path, "code_editor", {
        "path": "src/temp_convert.py", "start_line": "43", "end_line": "50", "search_block": STUB_MAIN,
        "replace_block": NEW_MAIN, "action": "replace_chunks"})
    assert "Missing required parameters" not in str(result) and "Successfully applied 1 edits" in str(result), result
    assert "applied as one chunk" in str(result)
    assert path.read_text() == HEAD + NEW_MAIN


def test_top_level_edit_without_an_action_passes_the_executor(tmp_path):
    """23:14:38 and 23:16:42: no 'action' at all."""
    path = workspace(tmp_path, STUB)
    result = executor(tmp_path, "code_editor", {
        "filename": "src/temp_convert.py", "start_line": "44", "end_line": "53", "search_block": STUB_MAIN,
        "replace_block": NEW_MAIN})
    assert "Successfully applied 1 edits" in str(result), result
    assert path.read_text() == HEAD + NEW_MAIN


def test_replace_lines_with_search_text_and_a_range_string_is_an_edit(tmp_path):
    """22:38:18: action 'replace_lines', search_text/replace_text, start_line '43, 52', end_line '52'."""
    path = workspace(tmp_path, STUB)
    result = executor(tmp_path, "code_editor", {
        "action": "replace_lines", "end_line": "52", "file_path": "src/temp_convert.py", "search_text": STUB_MAIN,
        "replace_text": NEW_MAIN, "start_line": "43, 52"})
    assert "Successfully applied 1 edits" in str(result), result
    assert path.read_text() == HEAD + NEW_MAIN


def test_the_observed_broken_replacement_now_gets_the_real_error(tmp_path):
    """22:38:18 as sent: the replacement lost its indentation. The answer is now the syntax error, not 'Missing chunks'."""
    path = workspace(tmp_path, STUB)
    broken = ('def main():\n    """CLI entry point using argparse for temperature conversion."""\n\nimport sys\n    \n'
              'if len(sys.argv) < 3:\n    print("Usage")\nelse:\ntry:\nvalue = float(sys.argv[1])\n')
    result = executor(tmp_path, "code_editor", {
        "action": "replace_lines", "end_line": "52", "filename": "src/temp_convert.py", "search_text": STUB_MAIN,
        "replace_text": broken, "start_line": "43, 52"})
    assert "SyntaxError" in str(result) and "Code around the error" in str(result)
    assert "Missing required parameters" not in str(result)
    assert path.read_text() == STUB


def test_chunks_with_invalid_escape_and_missing_bracket_are_parsed(tmp_path):
    """22:33:15 and 22:35:30: '\\"\\"\\"\\CLI' (\\C is no JSON escape) and the outer ']' missing."""
    path = workspace(tmp_path, "x = 1\nNOTE = 'old'\n")
    chunks = '[{"search": "NOTE = \'old\'", "replace": "NOTE = \'C:\\\\Temp \\\\CLI\'"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(chunks)
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py", chunks=chunks)
    assert result["status"] == "success", result
    assert path.read_text() == "x = 1\nNOTE = 'C:\\Temp \\CLI'\n"


def test_a_broken_chunks_string_is_still_an_error(tmp_path):
    path = workspace(tmp_path, "x = 1\n")
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py", chunks='[{"search": "x = 1", "replace": "x = }')
    assert result["status"] == "error" and "not valid JSON" in result["message"]
    assert path.read_text() == "x = 1\n"


def test_one_chunk_object_without_a_list_is_accepted(tmp_path):
    path = workspace(tmp_path, "x = 1\n")
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py", chunks={"search": "x = 1", "replace": "x = 2"})
    assert result["status"] == "success" and path.read_text() == "x = 2\n"


def test_saved_python_edits_report_code_notes(tmp_path):
    path = workspace(tmp_path, "def main():\n    return 1\n")
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py",
                chunks=[{"search": "    return 1", "replace": '    """Main."""'}])
    assert result["status"] == "success" and "WAS saved, but please fix" in result["message"]
    assert "only a docstring" in result["message"] and path.read_text() == 'def main():\n    """Main."""\n'


def test_repair_helpers():
    assert _repair_json_escapes(r'"\C \\ \n \u0041 \users \"') == r'"\\C \\ \n \u0041 \\users \"'
    assert _close_open_brackets('[{"a": "]}"}') == '[{"a": "]}"}]'
    assert _close_open_brackets('[{"a": 1}]') == '[{"a": 1}]'
    assert _close_open_brackets('[{"a": "open') == '[{"a": "open'      # an open string is never "repaired"
    assert _close_open_brackets('[{"a": 1]') == '[{"a": 1]'            # mismatched brackets are left alone


@pytest.mark.parametrize("value,expected", [(43, (43, None)), ("43", (43, None)), ("43, 52", (43, 52)), ("43-52", (43, 52)),
                                            ("43 to 52", (43, 52)), ("x", (None, None)), (True, (None, None)),
                                            (None, (None, None))])
def test_parse_line_numbers(value, expected):
    assert parse_line_numbers(value) == expected


def test_single_chunk_and_required_parameters():
    assert single_chunk_from_args({"search_block": "a", "replace_block": "b", "start_line": "43, 52"}) == {
        "replace": "b", "search": "a", "start_line": 43, "end_line": 52}
    assert single_chunk_from_args({"replace": "b"}) is None                   # nothing says WHERE
    assert single_chunk_from_args({"action": "replace_chunks"}) is None
    tool = CodeEditorTool()
    assert tool.params_not_required_for({"action": "replace_chunks", "search_block": "a", "replace_block": "b"}) == {"chunks"}
    assert tool.params_not_required_for({"search_block": "a", "replace_block": "b"}) == {"chunks", "action"}
    assert tool.params_not_required_for({"chunks": []}) == {"action"}
    assert tool.params_not_required_for({"action": "replace_chunks"}) == set()


def test_a_chunk_without_replace_is_still_malformed(tmp_path):
    """23:44:15: {'end_line': 57, 'start_line': 56} (no 'replace')."""
    workspace(tmp_path, FIXED)
    result = ce(tmp_path, action="replace_chunks", filename="src/temp_convert.py", chunks=[{"end_line": 57, "start_line": 56}])
    assert result["status"] == "error" and "missing 'replace' key" in result["message"]


# ================================================================================================ 3. file_system / executor
def test_start_line_range_string_reads_the_range(tmp_path):
    """22:34:07: read with start_line '1, 60' was rejected by the executor."""
    workspace(tmp_path, STUB)
    result = executor(tmp_path, "file_system", {"action": "read_with_lines>", "path": "src/temp_convert.py", "start_line": "43, 52"})
    assert "expects an integer" not in str(result)
    assert "43: \n44: def main():" in str(result) and "53:" not in str(result) and "52:" in str(result)


def test_split_line_range_helper():
    schema = {"parameters": [{"name": "start_line"}, {"name": "end_line"}]}
    assert split_line_range("start_line", "1, 60", schema, {}) == {"start_line": 1, "end_line": 60}
    assert split_line_range("start_line", "1, 60", schema, {"end_line": 30}) == {"start_line": 1}
    assert split_line_range("insert_line", "1, 60", schema, {}) is None
    assert split_line_range("start_line", "abc", schema, {}) is None


def test_the_complete_corrected_file_replaces_the_old_version(tmp_path):
    """23:24:30 and 23:30:54: the full file (both functions kept, main() fixed) was refused 'already exists' 6 times."""
    path = workspace(tmp_path, STUB)
    result = executor(tmp_path, "file_system", {"action": "write", "content": FIXED, "filename": "src/temp_convert.py"})
    assert "Successfully wrote" in str(result) and "keeps all of its functions" in str(result), result
    assert path.read_text() == FIXED


def test_a_rewrite_that_drops_a_function_still_needs_force_overwrite(tmp_path):
    path = workspace(tmp_path, FIXED)
    result = fs(tmp_path, action="write", path="src/temp_convert.py", content=HEAD.split("def fahrenheit_to_celsius")[0])
    assert result["status"] == "error" and "already exists" in result["message"]
    assert "function 'fahrenheit_to_celsius'" in result["message"] and "function 'main'" in result["message"]
    assert path.read_text() == FIXED


def test_a_rewrite_that_empties_a_function_still_needs_force_overwrite(tmp_path):
    path = workspace(tmp_path, FIXED)
    hollow = FIXED.replace("    return c * 9 / 5 + 32\n", "    pass\n")
    result = fs(tmp_path, action="write", path="src/temp_convert.py", content=hollow)
    assert result["status"] == "error" and "'celsius_to_fahrenheit' has code now but would hold no code" in result["message"]
    assert path.read_text() == FIXED


def test_a_rewrite_that_drops_a_constant_still_needs_force_overwrite(tmp_path):
    path = workspace(tmp_path, "RATE = 1.8\n\ndef f(c):\n    return c * RATE\n")
    result = fs(tmp_path, action="write", path="src/temp_convert.py", content="def f(c):\n    return c * 1.8\n")
    assert result["status"] == "error" and "top-level name 'RATE'" in result["message"]
    assert "RATE = 1.8" in path.read_text()


def test_a_rewrite_with_a_syntax_error_reports_the_syntax_error(tmp_path):
    """22:41:06: the complete file with lost indentation got 'already exists'; the real problem is the syntax."""
    path = workspace(tmp_path, STUB)
    broken = HEAD + 'def main():\n    """CLI."""\n\nimport sys\nif len(sys.argv) < 3:\n    print("Usage")\nelse:\ntry:\nvalue = 1\n'
    result = fs(tmp_path, action="write", path="src/temp_convert.py", content=broken)
    assert result["status"] == "error" and "SyntaxError" in result["message"] and "already exists" not in result["message"]
    assert path.read_text() == STUB


def test_line_content_is_content(tmp_path):
    """22:37:30: 'write_with_lines>' with 'line_content' -> "'content' parameter is required"."""
    result = fs(tmp_path, action="write_with_lines>", path="src/new.py", line_content="x = 1\n")
    assert result["status"] == "success" and (tmp_path / "src" / "new.py").read_text() == "x = 1\n"


def test_replace_lines_without_line_numbers_is_a_search_replace(tmp_path):
    """23:38:22: replace_lines with end_line 60, find_text and replace_text but no start line."""
    path = workspace(tmp_path, STUB)
    result = executor(tmp_path, "file_system", {"action": "replace_lines", "end_line": 60, "find_text": STUB_MAIN,
                                                "replace_text": NEW_MAIN, "filename": "src/temp_convert.py"})
    assert "Successfully replaced block" in str(result) and "applied as 'search_replace_block'" in str(result), result
    assert path.read_text() == HEAD + NEW_MAIN


def test_replace_lines_without_line_numbers_or_texts_is_still_an_error(tmp_path):
    workspace(tmp_path, STUB)
    result = fs(tmp_path, action="replace_lines", path="src/temp_convert.py", content="x = 1\n")
    assert result["status"] == "error" and "replace_start_line" in result["message"]


def test_search_replace_block_no_longer_saves_invalid_python(tmp_path):
    path = workspace(tmp_path, STUB)
    result = fs(tmp_path, action="search_replace_block", path="src/temp_convert.py",
                search_block="    import sys\n", replace_block="    import sys\n    x = (\n")
    assert result["status"] == "error" and "not be valid Python" in result["message"]
    assert path.read_text() == STUB


def test_python_rewrite_problems_helper():
    assert python_rewrite_problems(STUB, FIXED) == []
    assert python_rewrite_problems("x = 1\n", "x = 2\n") == [
        "the existing file defines no function or class, so the framework cannot check what a new version would lose"]
    old = "import os\n\nclass A:\n    def m(self):\n        return 1\n    def n(self):\n        return 2\n"
    assert python_rewrite_problems(old, "import os\n\nclass A:\n    def m(self):\n        return 3\n") == ["method 'A.n' would be removed"]
    assert python_rewrite_problems(old, "class A:\n    def m(self):\n        return 1\n    def n(self):\n        return 2\n") == [
        "top-level name 'os' (line 1) would be removed"]


# ======================================================================================== 4. replies cut off at the limit
def test_boosted_max_tokens():
    from src.agents.core import boosted_max_tokens
    assert boosted_max_tokens(1024, 0) == 1024
    assert boosted_max_tokens(1024, 1) == 2048
    assert boosted_max_tokens(1024, 2) == 4096
    assert boosted_max_tokens(1024, 5) == 4096
    assert boosted_max_tokens(None, 3) is None


def test_finish_info_event():
    from src.llm_providers.ollama_provider import finish_info_event
    assert finish_info_event({"eval_count": 1024}, "length") == {"type": "finish_info", "done_reason": "length", "output_tokens": 1024}
    assert finish_info_event({}, None) is None


class _FakeProvider:
    def __init__(self, events):
        self.events, self.max_tokens_seen = events, []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.max_tokens_seen.append(max_tokens)
        for event in self.events:
            yield event


def _worker(provider, monkeypatch, state="worker_decompose"):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)
    monkeypatch.setattr(core_module.settings, "WORKER_DECOMPOSE_STATE_MAX_TOKENS", 1024)

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools={}),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow),
        cycle_handler=None)
    agent = Agent({"agent_id": "W1", "config": {"agent_type": "worker", "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = state
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return run(go())


def test_a_cut_off_reply_is_recorded_and_the_next_call_gets_more_tokens(monkeypatch):
    provider = _FakeProvider([{"type": "finish_info", "done_reason": "length", "output_tokens": 1024}])
    agent = _worker(provider, monkeypatch)
    _consume(agent)
    assert agent._last_reply_cut_off is True and agent._consecutive_cut_off_replies == 1
    _consume(agent)
    assert provider.max_tokens_seen == [1024, 2048] and agent._consecutive_cut_off_replies == 2
    provider.events = [{"type": "response_chunk", "content": "done"}, {"type": "finish_info", "done_reason": "stop"}]
    _consume(agent)
    assert provider.max_tokens_seen[-1] == 4096
    assert agent._last_reply_cut_off is False and agent._consecutive_cut_off_replies == 0
    _consume(agent)
    assert provider.max_tokens_seen[-1] == 1024


def test_a_reply_without_finish_info_changes_nothing(monkeypatch):
    agent = _worker(_FakeProvider([{"type": "response_chunk", "content": "hi"}]), monkeypatch)
    _consume(agent)
    assert agent._last_reply_cut_off is False and agent._consecutive_cut_off_replies == 0


def test_cut_off_notice_text():
    from src.agents.cycle_handler import cut_off_reply_notice
    agent = types.SimpleNamespace(_last_cut_off_max_tokens=1024, _last_base_max_tokens=1024, _consecutive_cut_off_replies=1)
    text = cut_off_reply_notice(agent)
    assert "CUT OFF at 1024 tokens" in text and "NOTHING was executed or saved" in text and "up to 2048 tokens" in text
    agent = types.SimpleNamespace(_last_cut_off_max_tokens=4096, _last_base_max_tokens=1024, _consecutive_cut_off_replies=2)
    assert "same limit" in cut_off_reply_notice(agent)


def test_cycle_handler_uses_the_cut_off_notice():
    import inspect
    from src.agents import cycle_handler
    source = inspect.getsource(cycle_handler)
    assert "if getattr(agent, '_last_reply_cut_off', False):\n" in source
    assert "nudge_msg = cut_off_reply_notice(agent)" in source


# ================================================================================ 5. a started task is resumed in worker_work
@pytest.fixture
def tw_env(tmp_path, monkeypatch):
    import src.tools.project_management as pm
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    import src.agents.manager as manager_module
    monkeypatch.setattr(manager_module.settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)
    tool = pm.ProjectManagementTool()

    def call(agent, **kw):
        return run(tool.execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P", session_name="S", **kw))
    return call


def make_manager(state="worker_wait"):
    from src.agents.manager import AgentManager
    from src.agents.cycle_handler import AgentCycleHandler

    manager = object.__new__(AgentManager)   # no __init__: no timers, no DB
    manager.current_project, manager.current_session = "P", "S"
    manager.current_session_db_id = None
    worker = types.SimpleNamespace(agent_id="W1", agent_type="worker", state=state, status="idle",
                                   message_history=[], message_inbox=[], active_task_id=None)
    worker.set_status = lambda status: setattr(worker, "status", status)
    manager.agents = {"W1": worker}
    manager.scheduled = []

    async def schedule_cycle(agent, retry_count=0):
        manager.scheduled.append((agent.agent_id, agent.state))
    manager.schedule_cycle = schedule_cycle
    manager.workflow_manager = types.SimpleNamespace(
        change_state=lambda agent, new_state: setattr(agent, "state", new_state),
        get_system_prompt=lambda agent, mgr: "system prompt",
        resolve_state_alias=lambda agent_type, new_state: new_state,
    )
    handler = object.__new__(AgentCycleHandler)
    handler._manager = manager
    manager.cycle_handler = handler
    return manager, worker


@needs_tw
def test_a_task_the_worker_already_started_is_resumed_in_worker_work(tw_env):
    first = tw_env("PM1", action="add_task", description="Implement src/temp_convert.py")
    tw_env("PM1", action="modify_task", task_id=first["task_uuid"], assignee_agent_id="W1", task_progress="doing")
    manager, worker = make_manager()
    assert manager.get_task_progress(first["task_uuid"]) == "doing"

    outcome = run(manager.activate_worker_with_task_details("W1", first["task_uuid"], "Implement src/temp_convert.py"))
    assert outcome == "activated" and worker.state == "worker_work" and manager.scheduled == [("W1", "worker_work")]
    assert worker.active_task_id == first["task_uuid"]
    directive = worker.message_history[-1]["content"]
    assert "Continue your task" in directive and "already started" in directive and "nothing to decompose" in directive


@needs_tw
def test_a_new_task_still_starts_in_worker_decompose(tw_env):
    first = tw_env("PM1", action="add_task", description="Implement src/temp_convert.py")
    manager, worker = make_manager()
    assert run(manager.activate_worker_with_task_details("W1", first["task_uuid"], "Implement")) == "activated"
    assert worker.state == "worker_decompose" and "You have been assigned a new task" in worker.message_history[-1]["content"]


@needs_tw
def test_resume_falls_back_to_decompose_when_the_task_cannot_be_tracked(tw_env, monkeypatch):
    first = tw_env("PM1", action="add_task", description="Implement")
    tw_env("PM1", action="modify_task", task_id=first["task_uuid"], assignee_agent_id="W1", task_progress="doing")
    manager, worker = make_manager()

    def refuse(agent, state, task_id=None):
        raise ValueError("Task cannot be worked on")
    monkeypatch.setattr(manager.cycle_handler, "_handle_worker_task_tracking", refuse)
    assert run(manager.activate_worker_with_task_details("W1", first["task_uuid"], "Implement")) == "activated"
    assert worker.state == "worker_decompose"


def test_task_progress_lookup_fails_open():
    manager, _ = make_manager()
    manager.current_project = None
    assert manager.get_task_progress("whatever") is None
    manager.current_project = "P"
    assert manager.get_task_progress("") is None


def test_finished_notices_never_resume(monkeypatch):
    manager, worker = make_manager()
    monkeypatch.setattr(type(manager), "get_task_progress", lambda self, task_id: "doing")
    assert run(manager.activate_worker_with_task_details("W1", "u", "desc", "finished")) == "activated"
    assert worker.state == "worker_decompose" and "FINISHED" in worker.message_history[-1]["content"]


# ============================================================================================ 6. duplicate detection
def test_a_previous_tool_error_is_not_a_successful_duplicate():
    from src.agents.cycle_handler import AgentCycleHandler
    handler = object.__new__(AgentCycleHandler)
    args = {"action": "replace_lines", "filename": "src/temp_convert.py"}
    agent = types.SimpleNamespace(message_history=[
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "code_editor", "arguments": args}]},
        {"role": "tool", "tool_call_id": "c1", "name": "code_editor",
         "content": "[Tool Error: code_editor] Tool 'code_editor' missing required parameters: chunks"},
    ])
    assert handler._detect_cross_cycle_duplicate_tool_call(agent, "code_editor", dict(args)) is None
    agent.message_history[1]["content"] = "Successfully applied 1 edits to 'src/temp_convert.py'."
    assert handler._detect_cross_cycle_duplicate_tool_call(agent, "code_editor", dict(args)) is not None

# END OF FILE tests/unit/test_round2d.py
