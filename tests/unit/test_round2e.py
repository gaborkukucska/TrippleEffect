# START OF FILE tests/unit/test_round2e.py
"""Round 2e tests (run 2026-10-19 01:48-05:40, Temp_Converter_CLI, qwen3.5:4b).

1. 02:49-03:35: W1's read of src/temp_convert.py had failed at 02:30 (file not written yet). After every write the read was
   answered with that OLD "File not found" (identical-failure detector), so W1 wrote the file 8 times.
2. 04:03: PM1 marked task 1 finished although src/temp_convert.py used Decimal and 'f' without defining them, the function
   declared '-> float' returned nothing and main() held only 'import sys'. W2's tests never imported the functions.
3. 04:08-05:05: PM1 polled list_tasks/list_agents for an hour while W2 waited for the single Ollama slot.
4. 05:21: the Guardian (an LLM call) allowed W2 to re-apply 'replace_lines 48-50'; the model then got two results.
5. Edits: a top-level 'text' (5x "Missing required parameters: chunks"), {'read': 'true'} without an action, imports +
   the following 'def' line replacing the module docstring (2x duplicate def), an already-applied chunk sent again with
   stale line numbers, a line edit that deleted the only 'return', a search text whose first line only was in the file,
   a 40-line file read as lines 39-100 six times, failed tool results deleted from the history.
"""
import asyncio
import inspect
import json
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.tools.code_editor import (CodeEditorTool, boundary_overlap, removed_statement_lines, repair_boundary_overlap,
                                   replacement_already_in_place, whole_line_span)
from src.tools.executor import ToolExecutor, infer_action_from_keys
from src.tools.file_system import FileSystemTool
from src.tools.python_static_check import (python_code_notes, python_code_notes_for, syntax_error_report, undefined_names,
                                           workspace_import_hints)

# ---- file states W1 and W2 really saw (from the 03:58 and 04:36 history dumps) -------------------------------------------
# S0: src/temp_convert.py as read at 03:42 (before W1's edits); S1: after the 03:47 edit; S2: after the 03:52 edit.
# T0: tests/test_temp_convert.py as W2 read it at 04:26 (40 lines; the last test has only a docstring).
S0 = (
    '"""Temperature conversion utilities for Celsius <-> Fahrenheit."""\n'
    '\n'
    '\n'
    'def celsius_to_fahrenheit(celsius: float) -> float:\n'
    '    """Convert temperature from Celsius to Fahrenheit.\n'
    '\n'
    '    Args:\n'
    '        celsius (float): Temperature in degrees Celsius.\n'
    '\n'
    '    Returns:\n'
    '        float: Equivalent temperature in degrees Fahrenheit.\n'
    '\n'
    '    Formula used: F = C * 9/5 + 32 or F = C * 1.8 + 32\n'
    '    \n'
    '    Example:\n'
    '        >>> celsius_to_fahrenheit(0)\n'
    '        32.0\n'
    '        >>> round(celsius_to_fahrenheit(100), 2)\n'
    '        212.0\n'
    '\n'
    '    Raises:\n'
    '        TypeError: If input is not a number or if it\'s None."""\n'
    '    \n'
    '    # Input validation - ensure we have a valid numeric type\n'
    '    \n'
    "    return c * Decimal('1.8') + 32\n"
    '\n'
    '\n'
    'def fahrenheit_to_celsius(fahrenheit: float) -> float:\n'
    '    """Convert temperature from Fahrenheit to Celsius.\n'
    '\n'
    '    Args:\n'
    '        fahrenheit (float): Temperature in degrees Fahrenheit.\n'
    '\n'
    '    Returns:\n'
    '        float: Equivalent temperature in degrees Celsius.\n'
    '\n'
    '    Formula used: C = F - 32 then divide by 1.8, or equivalently C = (F-32) * 5/9\n'
    '    \n'
    '    Example:\n'
    '        >>> fahrenheit_to_celsius(0)\n'
    '        -17.78...\n'
    '        >>> round(fahrenheit_to_celsius(212), 2)\n'
    '        100.0\n'
    '\n'
    '    Raises:\n'
    '        TypeError: If input is not a number or if it\'s None."""\n'
    '    \n'
    '    # Input validation and conversion to Decimal for precision\n'
    '    \n'
    "    return (f - 32) * Decimal('5') / 9\n"
    '\n'
    '\n'
    'def main():\n'
    '    """CLI entry point using argparse to handle temperature conversions.\n'
    '\n'
    '    This function provides a command-line interface that accepts user input,\n'
    '    performs the appropriate conversion based on flags or arguments provided,\n'
    '    and outputs the result in various formats (JSON/text).\n'
    '\n'
    '    Example usage:\n'
    '        python -m src.temp_convert --from-celsius 25   # Convert Celsius to Fahrenheit\n'
    '        \n'
    '        python -m src.temp_convert --to-fahrenheit 80.6 # Convert Fahrenheit to Celsius\n'
    '    \n'
    '    Args:\n'
    '        None - Uses argparse for command-line argument parsing"""\n'
    '    \n'
    '    import sys\n'
)
S1 = (
    '"""Temperature conversion utilities for Celsius <-> Fahrenheit."""\n'
    '\n'
    '\n'
    'def celsius_to_fahrenheit(celsius: float) -> float:\n'
    '    """Convert temperature from Celsius to Fahrenheit.\n'
    '\n'
    '    Args:\n'
    '        celsius (float): Temperature in degrees Celsius.\n'
    '\n'
    '    Returns:\n'
    '        float: Equivalent temperature in degrees Fahrenheit.\n'
    '\n'
    '    Formula used: F = C * 9/5 + 32 or F = C * 1.8 + 32\n'
    '    \n'
    '    Example:\n'
    '        >>> celsius_to_fahrenheit(0)\n'
    '        32.0\n'
    '        >>> round(celsius_to_fahrenheit(100), 2)\n'
    '        212.0\n'
    '\n'
    '    Raises:\n'
    '        TypeError: If input is not a number or if it\'s None."""\n'
    '    \n'
    '# Input validation - ensure we have a valid numeric type\n'
    '    if not isinstance(celsius, (int, float)):\n'
    '        raise TypeError(f"Input must be an int or float, got {type(celsius).__name__}")\n'
    '    \n'
    '    # Convert to Decimal for precision calculation\n'
    '\n'
    '\n'
    'def fahrenheit_to_celsius(fahrenheit: float) -> float:\n'
    '    """Convert temperature from Fahrenheit to Celsius.\n'
    '\n'
    '    Args:\n'
    '        fahrenheit (float): Temperature in degrees Fahrenheit.\n'
    '\n'
    '    Returns:\n'
    '        float: Equivalent temperature in degrees Celsius.\n'
    '\n'
    '    Formula used: C = F - 32 then divide by 1.8, or equivalently C = (F-32) * 5/9\n'
    '    \n'
    '    Example:\n'
    '        >>> fahrenheit_to_celsius(0)\n'
    '        -17.78...\n'
    '        >>> round(fahrenheit_to_celsius(212), 2)\n'
    '        100.0\n'
    '\n'
    '    Raises:\n'
    '        TypeError: If input is not a number or if it\'s None."""\n'
    '    \n'
    '    # Input validation and conversion to Decimal for precision\n'
    '    \n'
    "    return (f - 32) * Decimal('5') / 9\n"
    '\n'
    '\n'
    'def main():\n'
    '    """CLI entry point using argparse to handle temperature conversions.\n'
    '\n'
    '    This function provides a command-line interface that accepts user input,\n'
    '    performs the appropriate conversion based on flags or arguments provided,\n'
    '    and outputs the result in various formats (JSON/text).\n'
    '\n'
    '    Example usage:\n'
    '        python -m src.temp_convert --from-celsius 25   # Convert Celsius to Fahrenheit\n'
    '        \n'
    '        python -m src.temp_convert --to-fahrenheit 80.6 # Convert Fahrenheit to Celsius\n'
    '    \n'
    '    Args:\n'
    '        None - Uses argparse for command-line argument parsing"""\n'
    '    \n'
    '    import sys\n'
)
S2 = (
    '"""Temperature conversion utilities for Celsius <-> Fahrenheit."""\n'
    '\n'
    '\n'
    'def celsius_to_fahrenheit(celsius: float) -> float:\n'
    '    """Convert temperature from Celsius to Fahrenheit.\n'
    '\n'
    '    Args:\n'
    '        celsius (float): Temperature in degrees Celsius.\n'
    '\n'
    '    Returns:\n'
    '        float: Equivalent temperature in degrees Fahrenheit.\n'
    '\n'
    '    Formula used: F = C * 9/5 + 32 or F = C * 1.8 + 32\n'
    '    \n'
    '    Example:\n'
    '        >>> celsius_to_fahrenheit(0)\n'
    '        32.0\n'
    '        >>> round(celsius_to_fahrenheit(100), 2)\n'
    '        212.0\n'
    '\n'
    '    Raises:\n'
    '        TypeError: If input is not a number or if it\'s None."""\n'
    '    \n'
    '    if not isinstance(celsius, (int, float)):\n'
    '        raise TypeError(f"Input must be an int or float, got {type(celsius).__name__}")\n'
    '\n'
    '    # Convert to Decimal for precision calculation\n'
    '    c = Decimal(str(celsius))\n'
    '\n'
    'def fahrenheit_to_celsius(fahrenheit: float) -> float:\n'
    '    """Convert temperature from Fahrenheit to Celsius.\n'
    '\n'
    '    Args:\n'
    '        fahrenheit (float): Temperature in degrees Fahrenheit.\n'
    '\n'
    '    Returns:\n'
    '        float: Equivalent temperature in degrees Celsius.\n'
    '\n'
    '    Formula used: C = F - 32 then divide by 1.8, or equivalently C = (F-32) * 5/9\n'
    '    \n'
    '    Example:\n'
    '        >>> fahrenheit_to_celsius(0)\n'
    '        -17.78...\n'
    '        >>> round(fahrenheit_to_celsius(212), 2)\n'
    '        100.0\n'
    '\n'
    '    Raises:\n'
    '        TypeError: If input is not a number or if it\'s None."""\n'
    '    \n'
    '    # Input validation and conversion to Decimal for precision\n'
    '    \n'
    "    return (f - 32) * Decimal('5') / 9\n"
    '\n'
    '\n'
    'def main():\n'
    '    """CLI entry point using argparse to handle temperature conversions.\n'
    '\n'
    '    This function provides a command-line interface that accepts user input,\n'
    '    performs the appropriate conversion based on flags or arguments provided,\n'
    '    and outputs the result in various formats (JSON/text).\n'
    '\n'
    '    Example usage:\n'
    '        python -m src.temp_convert --from-celsius 25   # Convert Celsius to Fahrenheit\n'
    '        \n'
    '        python -m src.temp_convert --to-fahrenheit 80.6 # Convert Fahrenheit to Celsius\n'
    '    \n'
    '    Args:\n'
    '        None - Uses argparse for command-line argument parsing"""\n'
    '    \n'
    '    import sys\n'
)
T0 = (
    '"""Pytest tests for temperature conversion utilities."""\n'
    '\n'
    '\n'
    'def test_celsius_to_fahrenheit_zero():\n'
    '    """Test 0°C = 32°F equivalence.\n'
    '\n'
    '    Key verification: Zero degrees Celsius should equal exactly 32 degrees Fahrenheit.\n'
    '    This is a fundamental reference point in the C-F scale relationship.\n'
    '    \n'
    '    Expected result after conversion from celsius to fahrenheit at input=0: output must be approximately 32 (within floating-point tolerance)."""\n'
    '    \n'
    '    assert abs(1 * round(celsius_to_fahrenheit(0), 5) - 32, 6) < 1e-9\n'
    '\n'
    '\n'
    'def test_celsius_to_fahrenheit_hundred():\n'
    '    """Test 100°C = 212°F equivalence.\n'
    '\n'
    '    Key verification: One hundred degrees Celsius should equal exactly 212 degrees Fahrenheit.\n'
    '    This is the boiling point of water at standard atmospheric pressure, a critical reference in thermodynamics."""\n'
    '\n'
    '\n'
    'def test_fahrenheit_to_celsius_two_hundred_twelve():\n'
    '    """Test 212°F = 100°C equivalence (reverse direction).\n'
    '\n'
    '    Key verification: Two hundred twelve degrees Fahrenheit should equal exactly one hundred degrees Celsius.\n'
    '    This tests the inverse conversion function and confirms bidirectional consistency."""\n'
    '\n'
    '\n'
    'def test_bidirectional_equivalence_zero_celsius_fahrenheit():\n'
    '    """Test -40° bidirectional equivalence point.\n'
    '\n'
    '    Critical mathematical property: At -40°, both scales intersect where C = F = -40°.\n'
    '    \n'
    '    Verification steps:\n'
    '        1. Convert -40°C to Fahrenheit -> should equal approximately -40°F\n'
    '        2. Verify the result is within floating-point tolerance of original input"""\n'
    '\n'
    '\n'
    'def test_celsius_to_fahrenheit_negative_四十():\n'
    '    """Test conversion from Celsius at -40°."""\n'
)
CALL_0347 = {'action': 'replace_chunks', 'filename': 'src/temp_convert.py', 'chunks': [{'end_line': 26, 'replace': '# Input validation - ensure we have a valid numeric type\n    if not isinstance(celsius, (int, float)):\n        raise TypeError(f"Input must be an int or float, got {type(celsius).__name__}")\n    \n    # Convert to Decimal for precision calculation', 'start_line': 24}], 'scope': 'shared'}
CALL_0348 = {'action': 'replace_chunks', 'filename': 'src/temp_convert.py', 'chunks': [{'end_line': 26, 'replace': '# Input validation - ensure we have a valid numeric type\n    if not isinstance(celsius, (int, float)):\n        raise TypeError(f"Input must be an int or float, got {type(celsius).__name__}")\n    \n    # Convert to Decimal for precision calculation', 'start_line': 24}, {'end_line': 51, 'replace': '# Input validation and conversion to Decimal for precision\n    if not isinstance(fahrenheit, (int, float)):\n        raise TypeError(f"Input must be an int or float, got {type(fahrenheit).__name__}")\n    \n    # Convert input to Decimal for precise calculation', 'start_line': 49}], 'scope': 'shared'}
CALL_0354 = {'action': 'replace_chunks', 'filename': 'src/temp_convert.py', 'chunks': [{'end_line': 53, 'replace': '# Input validation and conversion to Decimal for precision\n    if not isinstance(fahrenheit, (int, float)):\n        raise TypeError(f"Input must be an int or float, got {type(fahrenheit).__name__}")\n\n    # Convert input to Decimal for precise calculation\n    f = Decimal(str(fahrenheit))', 'search': '# Input validation and conversion to Decimal for precision\n    if not isinstance(fahrenheit, (int, float)):\n        raise TypeError(f"Input must be an int or float, got {type(fahrenheit).__name__}")\n\n    # Convert input to Decimal for precise calculation', 'start_line': 49}], 'scope': 'shared'}
CALL_0356 = {'action': 'replace_chunks', 'filename': 'src/temp_convert.py', 'chunks': [{'end_line': 1, 'replace': 'from decimal import Decimal\nimport argparse\n\n\ndef celsius_to_fahrenheit(celsius: float) -> float:', 'search': '"""Temperature conversion utilities for Celsius <-> Fahrenheit."""', 'start_line': 0}], 'scope': 'shared'}
CALL_0358 = {'action': 'replace_chunks', 'filename': 'src/temp_convert.py', 'chunks': [{'end_line': 3, 'replace': 'from decimal import Decimal\nimport argparse\n\n\ndef celsius_to_fahrenheit(celsius: float) -> float:', 'search': '"""Temperature conversion utilities for Celsius <-> Fahrenheit."""\n\n', 'start_line': 0}], 'scope': 'shared'}
W2_CALL_0500 = {'path': 'tests/test_temp_convert.py', 'start_line': '39', 'end_line': '50', 'text': 'def test_celsius_to_fahrenheit_negative_四十():\n    """Test conversion from Celsius at -40°."""\n\n    \ndef test_bidirectional_equivalence_zero_celsius_fahrenheit_complete():\n    """Complete bidirectional equivalence point for -40°."""\n    \n    # Step 1: Convert -40°C to Fahrenheit -> should equal approximately -40°F\n    c_to_f = round(celsius_to_fahrenheit(-40), 5)\n    assert abs(c_to_f + 40, 6) < 1e-9\n    \n    # Verify the result is within floating-point tolerance of original input\n    f_to_c = round(fahrenheit_to_celsius(c_to_f), 5)\n    assert abs(f_to_c - (-40), 6) < 1e-9\n', 'action': 'replace_lines'}
W2_CALL_0520 = {'action': 'replace_lines', 'end_line': '50', 'path': 'tests/test_temp_convert.py', 'start_line': '48', 'content': '\ndef test_bidirectional_equivalence_zero_celsius_fahrenheit():\n    """Test -40° bidirectional equivalence point.\n\n    Critical mathematical property: At -40°, both scales intersect where C = F = -40°.\n    \n    Verification steps:\n        1. Convert -40°C to Fahrenheit -> should equal approximately -40°F\n        2. Verify the result is within floating-point tolerance of original input"""\n', 'filename': 'tests/test_temp_convert.py'}

FINAL_TESTS = (  # tests/test_temp_convert.py at the end of the run (first 31 lines are enough for the checks)
    '"""Pytest tests for temperature conversion utilities."""\n\n\n'
    'def test_celsius_to_fahrenheit_zero():\n    """Test 0C."""\n    assert abs(celsius_to_fahrenheit(0) - 32) < 1e-9\n\n\n'
    'def test_fahrenheit_to_celsius_two_hundred_twelve():\n    """Test 212F."""\n'
    '    assert abs(fahrenheit_to_celsius(212) - 100) < 1e-9\n'
)
CORRECT = (
    '"""Temperature conversion."""\nfrom decimal import Decimal\n\n\n'
    'def celsius_to_fahrenheit(celsius: float) -> float:\n    """C to F."""\n    c = Decimal(str(celsius))\n'
    '    return float(c * Decimal("1.8") + 32)\n\n\n'
    'def fahrenheit_to_celsius(fahrenheit: float) -> float:\n    """F to C."""\n    return (fahrenheit - 32) * 5 / 9\n\n\n'
    'def main():\n    """CLI."""\n    import argparse\n    parser = argparse.ArgumentParser()\n    parser.parse_args()\n'
)


def run(coro):
    return asyncio.run(coro)


def src_file(tmp_path, text, name="src/temp_convert.py"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ce(tmp_path, args):
    return run(CodeEditorTool().execute("W1", tmp_path, **dict(args, scope="private")))


def fs(tmp_path, **args):
    return run(FileSystemTool().execute("W2", tmp_path, scope="private", **args))


def executor(tmp_path, tool, args, agent="W2"):
    manager = types.SimpleNamespace(agents={agent: types.SimpleNamespace(agent_type="worker", state="worker_work")})
    return run(ToolExecutor().execute_tool(agent, tmp_path, tool, dict(args, scope="private"), manager=manager))


def compiles(text):
    try:
        compile(text, "x", "exec")
        return True
    except SyntaxError:
        return False


def test_the_fixtures_are_the_observed_files():
    assert len(S0.splitlines()) == 69 and S0.splitlines()[25] == "    return c * Decimal('1.8') + 32"
    assert len(S1.splitlines()) == 71 and S1.splitlines()[23] == "# Input validation - ensure we have a valid numeric type"
    assert len(S2.splitlines()) == 70 and S2.splitlines()[27] == "    c = Decimal(str(celsius))"
    assert len(T0.splitlines()) == 40 and T0.splitlines()[39].endswith('at -40°."""')
    assert all(compiles(text) for text in (S0, S1, S2, T0))


# ============================================================================================ 1. stale identical failures
def _history_read_failed_then(write_result, read=None):
    read = read or {"action": "read", "path": "src/temp_convert.py"}
    write = {"action": "write", "path": "src/temp_convert.py", "content": "x = 1\n"}
    history = [
        {"role": "system", "content": "prompt"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "r1", "name": "file_system", "arguments": read}]},
        {"role": "tool", "tool_call_id": "r1", "name": "file_system",
         "content": "[Tool Execution Failed] File not found: 'src/temp_convert.py' in shared workspace."},
    ]
    if write_result is not None:
        history += [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "w1", "name": "file_system", "arguments": write}]},
            {"role": "tool", "tool_call_id": "w1", "name": "file_system", "content": write_result},
        ]
    return types.SimpleNamespace(message_history=history), read


def _handler():
    from src.agents.cycle_handler import AgentCycleHandler
    return object.__new__(AgentCycleHandler)


def test_a_read_that_failed_before_the_file_was_written_runs_again():
    agent, read = _history_read_failed_then("Successfully wrote content to 'src/temp_convert.py' in shared workspace.")
    assert _handler()._detect_cross_cycle_identical_failure(agent, "file_system", dict(read)) is None


# Round 2f: failed READS are never replayed any more (another agent may have created the file), so these two use a
# file_system call that is not a read.
DELETE = {"action": "delete", "path": "src/temp_convert.py"}


def test_the_same_failure_without_a_file_change_is_still_caught():
    agent, call = _history_read_failed_then(None, DELETE)
    assert "File not found" in _handler()._detect_cross_cycle_identical_failure(agent, "file_system", dict(call))


def test_a_failed_write_is_not_a_file_change():
    agent, call = _history_read_failed_then("[Tool Execution Failed] Write rejected due to Python SyntaxError", DELETE)
    assert _handler()._detect_cross_cycle_identical_failure(agent, "file_system", dict(call)) is not None


# ============================================================================================ 2. static checks / task gate
FINAL_SRC_NOTES = [
    "'Decimal' is used on line 28", "'f' is used on line 52", "did you mean 'fahrenheit'?",
    "'celsius_to_fahrenheit' (line 4) is declared to return 'float'", "function 'main' (line 55) contains only a docstring and imports",
]


def test_the_final_source_file_gets_all_four_defects(tmp_path):
    notes = "\n".join(python_code_notes(S2, "src/temp_convert.py"))
    for text in FINAL_SRC_NOTES:
        assert text in notes, text
    assert "missing 'from decimal import Decimal'?" in notes


def test_a_correct_implementation_gets_no_note():
    assert python_code_notes(CORRECT, "src/temp_convert.py") == []


def test_tests_get_the_import_of_the_function_under_test(tmp_path):
    src_file(tmp_path, CORRECT)
    src_file(tmp_path, "", "src/__init__.py")
    notes = python_code_notes_for(FINAL_TESTS, "tests/test_temp_convert.py", tmp_path)
    assert any("missing 'from temp_convert import celsius_to_fahrenheit'?" in n for n in notes)
    assert any("missing 'from temp_convert import fahrenheit_to_celsius'?" in n for n in notes)
    fixed = "from temp_convert import celsius_to_fahrenheit, fahrenheit_to_celsius\n" + FINAL_TESTS
    assert python_code_notes_for(fixed, "tests/test_temp_convert.py", tmp_path) == []


def test_workspace_import_hints(tmp_path):
    src_file(tmp_path, CORRECT)
    src_file(tmp_path, "def helper():\n    return 1\n\n\ndef _private():\n    return 2\n", "src/pkg/util.py")
    src_file(tmp_path, "def test_x():\n    assert 1\n", "tests/test_x.py")
    src_file(tmp_path, "def main():\n    return 0\n", "tool.py")      # 'main' now exists in two modules
    hints = workspace_import_hints(tmp_path)
    assert hints["celsius_to_fahrenheit"] == "temp_convert" and hints["helper"] == "pkg.util"
    assert "_private" not in hints and "test_x" not in hints and "main" not in hints


def test_task_completion_is_refused_for_the_observed_file(tmp_path):
    from src.utils.task_files import task_completion_problems
    src_file(tmp_path, S2)
    task = ("Kick-off Task 1: Implement conversion functions (celsius_to_fahrenheit, fahrenheit_to_celsius) and CLI main() "
            "with argparse in src/temp_convert.py")
    problems = task_completion_problems(task, tmp_path)
    assert len(problems) == 4 and all(p.startswith("src/temp_convert.py: ") for p in problems)
    src_file(tmp_path, CORRECT)
    assert task_completion_problems(task, tmp_path) == []


@pytest.mark.parametrize("source", [
    "def f() -> Optional[int]:\n    x = 1\n",                       # may return None
    "def f() -> int:\n    x = 1\n    raise ValueError(x)\n",        # always raises
    "def f() -> Iterator[int]:\n    yield 1\n",
    "def f() -> int:\n    while True:\n        pass\n",
    "def f() -> None:\n    print(1)\n",
    "from abc import abstractmethod\n\n\n@abstractmethod\ndef f() -> int:\n    import os\n",
    "def f() -> int:\n    if True:\n        return 1\n",
    "def g():\n    try:\n        import yaml\n    except ImportError:\n        yaml = None\n    return yaml\n",
    "for i in range(3):\n    pass\nprint(i)\n",
    "x = [y for y in range(3)]\nlambda a: a + 1\n",
    "def f[T](a: T) -> T:\n    return a\n" if __import__("sys").version_info >= (3, 12) else "x = 1\n",
    "from os import *\nprint(getcwd())\n",                         # star import: names unknown
    "globals()['v'] = 1\nprint(v)\n",                              # dynamic name
    "print(__file__, __name__)\n",
])
def test_no_false_alarms(source):
    names = ("Optional", "Iterator")
    prefix = "".join(f"from typing import {n}\n" for n in names if n in source)
    assert python_code_notes(prefix + source, "src/m.py") == [], source


def test_a_nested_function_return_does_not_count():
    source = "def f() -> int:\n    def g():\n        return 1\n    g()\n"
    assert any("declared to return 'int'" in n for n in python_code_notes(source, "src/m.py"))


def test_undefined_names_helper():
    import ast
    source = "def f(a):\n    return a + b + len(c)\n\n\nc = 1\n"
    assert undefined_names(ast.parse(source), source) == [("b", 2)]


# ============================================================================================ 3. repeated edits / Guardian
def test_duplicate_file_change_verdicts():
    from src.agents.tool_activity import duplicate_file_change_verdict
    assert duplicate_file_change_verdict("file_system", W2_CALL_0520)[0] == "BLOCK_AND_GUIDE"
    assert "NOT applied a second time" in duplicate_file_change_verdict("file_system", W2_CALL_0520)[1]
    assert duplicate_file_change_verdict("code_editor", CALL_0347)[0] == "BLOCK_AND_GUIDE"
    assert duplicate_file_change_verdict("file_system", {"action": "write", "path": "a.py", "content": "x"}) == ("ALLOW", "")
    assert duplicate_file_change_verdict("code_editor", {"action": "write_file", "filename": "a.py"}) == ("ALLOW", "")
    assert duplicate_file_change_verdict("file_system", {"action": "read", "path": "a.py"}) is None
    assert duplicate_file_change_verdict("project_management", {"action": "list_tasks"}) is None


def test_the_guardian_needs_no_llm_for_a_repeated_line_edit():
    from src.agents.cycle_components.agent_health_monitor import ConstitutionalGuardianHealthMonitor
    monitor = object.__new__(ConstitutionalGuardianHealthMonitor)   # no manager: an LLM call would fail
    agent = types.SimpleNamespace(agent_id="W2", state="worker_report")
    verdict, feedback = run(monitor.evaluate_duplicate_tool_call(agent, "file_system", dict(W2_CALL_0520), 1))
    assert verdict == "BLOCK_AND_GUIDE" and "tests/test_temp_convert.py" in feedback


def test_an_allowed_duplicate_drops_the_cached_result():
    from src.agents import cycle_handler
    source = inspect.getsource(cycle_handler)
    allowed = source.index("allowed to run duplicate tool")
    assert source.index("all_tool_results_for_history.remove(history_item)", allowed) - allowed < 900


# ============================================================================================ 4. PM polling
def _pm_history(last_call=None, progress="doing", last_filter=None):
    tasks = [{"uuid": "t2", "assignee": "W2", "task_progress": progress}]
    calls = [("c1", "project_management", {"action": "list_tasks", "project_filter": "P"}),
             ("c2", "manage_team", {"action": "list_agents", "team_id": "team_P"}),
             ("c3", "project_management", dict({"action": "list_tasks"},
                                               **({"task_progress_filter": "todo"} if last_filter is None else last_filter)))]
    if last_call:
        calls.append(last_call)
    history = [{"role": "system", "content": "prompt"}]
    for call_id, name, args in calls:
        history.append({"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": args}]})
        content = json.dumps({"status": "success", "tasks": tasks}) if args.get("action") == "list_tasks" else "ok"
        history.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": content})
    return history


def test_trailing_monitoring_turns():
    from src.agents.tool_activity import trailing_monitoring_turns, is_monitoring_call
    assert trailing_monitoring_turns(_pm_history()) == 3
    assigned = ("c4", "project_management", {"action": "modify_task", "task_id": "t3", "assignee_agent_id": "W1"})
    assert trailing_monitoring_turns(_pm_history(assigned)) == 0
    assert is_monitoring_call({"name": "file_system", "arguments": {"action": "read", "path": "PROJECT_PLAN.md"}})
    assert not is_monitoring_call({"name": "send_message", "arguments": {"target_agent_id": "W2"}})


def _schedule(history):
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    scheduled = []

    async def schedule_cycle(agent, retry):
        scheduled.append(agent.agent_id)
        return None

    def change_state(agent, state):
        agent.state = state
        return True

    manager = types.SimpleNamespace(workflow_manager=types.SimpleNamespace(change_state=change_state),
                                    schedule_cycle=schedule_cycle)
    agent = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage", message_history=history,
                                  status="processing", set_status=lambda s: None)
    context = types.SimpleNamespace(agent=agent, all_tool_results=[], trigger_failover=False,
                                    needs_reactivation_after_cycle=False, is_retryable_error_type=False, retry_count=0,
                                    max_retries_for_cycle=3, cycle_completed_successfully=True,
                                    state_change_requested_this_cycle=False, executed_tool_successfully_this_cycle=True,
                                    action_taken_this_cycle=True, current_model_key_for_tracking="m")
    run(NextStepScheduler(manager).schedule_next_step(context))
    return agent, scheduled


def test_a_polling_pm_goes_to_standby():
    agent, scheduled = _schedule(_pm_history())
    assert agent.state == "pm_standby" and scheduled == []
    assert "pm_standby" in agent.message_history[-1]["content"] and "3 times" in agent.message_history[-1]["content"]


def test_a_pm_that_acted_is_reactivated():
    assigned = ("c4", "project_management", {"action": "modify_task", "task_id": "t3", "assignee_agent_id": "W1"})
    agent, scheduled = _schedule(_pm_history(assigned))
    assert agent.state == "pm_manage" and scheduled == ["PM1"]


def test_a_pm_whose_listing_shows_no_open_task_is_told_to_audit():
    agent, scheduled = _schedule(_pm_history(progress="finished", last_filter={}))   # unfiltered listing, all finished
    # Round 2f: the framework moves the PM to pm_audit itself (it used to only say so, once, and then put it in standby)
    assert agent.state == "pm_audit" and scheduled == ["PM1"]
    assert "pm_audit" in agent.message_history[-1]["content"]


# ============================================================================================ 5. edits
def test_0347_warns_that_the_return_line_was_removed(tmp_path):
    path = src_file(tmp_path, S0)
    result = ce(tmp_path, CALL_0347)
    assert result["status"] == "success" and path.read_text() == S1
    assert "removed line 26 'return c * Decimal('1.8') + 32'" in result["message"]
    assert "declared to return 'float'" in result["message"]           # the code notes say it, too


def test_0348_resent_chunk_is_not_applied_twice(tmp_path):
    path = src_file(tmp_path, S1)
    first_chunk_only = dict(CALL_0348, chunks=[CALL_0348["chunks"][0]])
    result = ce(tmp_path, first_chunk_only)
    assert result["status"] == "success" and path.read_text() == S1
    assert "already hold exactly this text" in result["message"]


def test_0348_stale_line_numbers_get_the_out_of_date_hint(tmp_path):
    path = src_file(tmp_path, S1)
    result = ce(tmp_path, CALL_0348)
    assert result["status"] == "error" and path.read_text() == S1
    assert "OUT OF DATE" in result["message"]


def test_0354_says_that_the_search_text_is_not_in_the_file(tmp_path):
    src_file(tmp_path, S2)
    result = ce(tmp_path, CALL_0354)
    assert result["status"] == "error" and "Only the FIRST line of your search text is in the file (line(s) 50)" in result["message"]


@pytest.mark.parametrize("call_name", ["CALL_0356", "CALL_0358"])
def test_imports_plus_the_following_def_line_are_saved_once(tmp_path, call_name):
    path = src_file(tmp_path, S2)
    result = ce(tmp_path, globals()[call_name])
    assert result["status"] == "success", result
    text = path.read_text()
    assert compiles(text) and text.count("def celsius_to_fahrenheit") == 1
    assert text.splitlines()[:5] == ["from decimal import Decimal", "import argparse", "", "",
                                     "def celsius_to_fahrenheit(celsius: float) -> float:"]
    assert text.splitlines()[5:] == S2.splitlines()[4:]
    assert "were not inserted a second time" in result["message"]


def test_boundary_helpers():
    lines = ["a = 1\n", "\n", "def f():\n", "    return a\n"]
    assert boundary_overlap(lines, 0, 1, "import os\n\ndef f():\n") == (0, 2)
    assert boundary_overlap(lines, 2, 3, "\ndef f():\n    pass\n") == (0, 0)
    assert boundary_overlap(lines, 3, 4, "def f():\n    return 2\n") == (1, 0)
    assert whole_line_span("ab\ncd\nef", 3, 5) == (1, 2) and whole_line_span("ab\ncd\nef", 3, 6) == (1, 2)
    assert whole_line_span("ab\ncd\nef", 4, 5) is None and whole_line_span("ab\ncd\nef", 6, 8) == (2, 3)


def test_a_valid_edit_is_never_repaired():
    current = "x = 1\n\n\ndef f():\n    return x\n"
    lines = current.splitlines(True)
    replacement = "x = 2\n\n\ndef f():\n"          # valid result: an extra (empty) def is NOT a syntax error here? check
    naive = "".join(lines[:0]) + replacement + "".join(lines[1:])
    if compiles(naive):
        assert repair_boundary_overlap(current, lines, 0, 1, replacement, naive) is None
    broken_current = "def f(:\n"
    assert repair_boundary_overlap(broken_current, broken_current.splitlines(True), 0, 1, "x\n", "def f(:\nx\n") is None


def test_already_in_place_and_removed_statement_helpers():
    lines = ["a\n", "b\n", "c\n", "d\n"]
    assert replacement_already_in_place(lines, 1, 2, "b\nc\n") and not replacement_already_in_place(lines, 1, 3, "b\nc\n")
    assert not replacement_already_in_place(lines, 1, 2, "\n\n")
    code = ["def f():\n", "    x = 1\n", "    return x\n"]
    assert removed_statement_lines(code, 1, 3, "    x = 2\n") == ["line 3 'return x'"]
    assert removed_statement_lines(code, 1, 3, "    x = 2\n    return x\n") == []


@pytest.mark.parametrize("with_action", [False, True])
def test_w2_top_level_text_edit_is_applied(tmp_path, with_action):
    path = src_file(tmp_path, T0, "tests/test_temp_convert.py")
    args = dict(W2_CALL_0500)
    if with_action:            # how the call looked in W2's history
        args.update(action="replace_lines", filename=args.pop("path"))
    result = executor(tmp_path, "code_editor", args)
    assert "Successfully applied 1 edits" in str(result), result
    assert "did you mean 'test_" not in str(result)
    text = path.read_text()
    assert compiles(text) and "test_bidirectional_equivalence_zero_celsius_fahrenheit_complete" in text
    assert text.splitlines()[:38] == T0.splitlines()[:38]


def test_replace_lines_warns_about_a_removed_assert(tmp_path):
    src_file(tmp_path, T0, "tests/test_temp_convert.py")
    result = fs(tmp_path, action="replace_lines", path="tests/test_temp_convert.py", start_line=12, end_line=12, content="    pass")
    assert result["status"] == "success" and "removed line 12 'assert abs(1 * round" in result["message"]


# ============================================================================================ 6. file_system
def test_read_given_as_a_flag_is_the_action(tmp_path):
    src_file(tmp_path, T0, "tests/test_temp_convert.py")
    args = {"end_line": 50, "path": "tests/test_temp_convert.py", "read": "true", "start_line": 39}
    result = executor(tmp_path, "file_system", args)
    text = result if isinstance(result, str) else json.dumps(result)
    assert "39:" in text or "test_celsius_to_fahrenheit_negative" in text, text


def test_infer_action_from_keys_keeps_data_values():
    args = {"path": "a.py", "write": "print(1)\n"}
    assert infer_action_from_keys("file_system", args) is None and args["write"] == "print(1)\n"
    args = {"path": "a.py", "read": True}
    assert infer_action_from_keys("file_system", args) == "read" and "read" not in args
    args = {"list_tasks": '{"action": "list"}'}
    assert infer_action_from_keys("project_management", args) == "list_tasks"     # Round 1v behaviour unchanged


def test_a_ranged_read_says_where_the_file_ends(tmp_path):
    src_file(tmp_path, T0, "tests/test_temp_convert.py")
    result = fs(tmp_path, action="read", path="tests/test_temp_convert.py", start_line=39, end_line=100)
    assert result["status"] == "success"
    assert result["content"].endswith("[End of file: 'tests/test_temp_convert.py' has 40 lines; there is nothing after line 40.]")
    middle = fs(tmp_path, action="read", path="tests/test_temp_convert.py", start_line=1, end_line=10)
    assert "End of file" not in middle["content"]
    whole = fs(tmp_path, action="read", path="tests/test_temp_convert.py")
    assert "End of file" not in whole["content"]


def test_invalid_character_hint():
    source = 'def f():\n    """Doc."""\n\n    At -40°, both scales meet.\n'
    try:
        compile(source, "x", "exec")
    except SyntaxError as err:
        assert "OUTSIDE a string" in syntax_error_report(source, err)


# ============================================================================================ 7. failed results in history
def test_older_failed_results_are_shortened_not_deleted():
    from src.agents.cycle_components.prompt_assembler import shorten_failed_tool_result, SHORTENED_FAILURE_TAG
    long_error = ("[Tool Execution Failed] Code edit rejected due to Python SyntaxError in resulting code: unterminated "
                  "triple-quoted string literal (detected at line 75) at line 73. No changes were saved.\nCode around the "
                  "error:\n     70 |  ...")
    short = shorten_failed_tool_result(long_error)
    assert short.startswith("[Tool Execution Failed] Code edit rejected") and SHORTENED_FAILURE_TAG in short
    assert "Code around the error" not in short and shorten_failed_tool_result(short) == short
    chunks_error = ("[Tool Execution Failed] Code edit failed: 0 chunks succeeded, but 1 failed issues occurred.\nChunk 0: "
                    "start_line/end_line (49-53) and the 'search' text point to DIFFERENT code\nNo changes were saved.")
    short = shorten_failed_tool_result(chunks_error)
    assert "DIFFERENT code" in short and "No changes were saved." in short
    assert shorten_failed_tool_result("") is None


def test_the_assembler_keeps_one_line_per_older_failure():
    from src.agents.cycle_components import prompt_assembler
    source = inspect.getsource(prompt_assembler.PromptAssembler.prepare_llm_call_data)
    assert "shorten_failed_tool_result(msg.get(\"content\", \"\"))" in source and "indices_to_remove.discard(idx)" in source

# END OF FILE tests/unit/test_round2e.py
