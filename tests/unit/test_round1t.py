"""Round 1t tests: W1 resent a file with the same SyntaxError 7 times (run of 2026-10-16 10:05-12:16) because the rejection
only named the error and a line number."""
import asyncio

import pytest

from src.tools.python_static_check import syntax_error_report

TRY_NO_EXCEPT = """import sys


def main():
    try:
        value = float(sys.argv[1])
        print(value)

    unit = sys.argv[2]
    print(unit)


main()
"""


def error_of(source):
    with pytest.raises(SyntaxError) as info:
        compile(source, "x.py", "exec")
    return info.value


def test_try_without_except_points_at_the_try_line():
    err = error_of(TRY_NO_EXCEPT)
    report = syntax_error_report(TRY_NO_EXCEPT, err)
    assert "Code around the error:" in report
    assert ">>" in report and "unit = sys.argv[2]" in report
    assert "The `try:` on line 5" in report
    assert "except Exception as exc:" in report and "COMPLETE corrected file" in report


def test_a_try_that_already_has_a_handler_is_not_blamed():
    source = "def a():\n    try:\n        x = 1\n    except Exception:\n        pass\n\n\ndef b():\n    try:\n        y = 2\n    z = 3\n"
    report = syntax_error_report(source, error_of(source))
    assert "The `try:` on line 9" in report


def test_fstring_brace_hint():
    source = 'name = "x"\nprint(f"{name} is }")\n'
    assert "`{{` and `}}`" in syntax_error_report(source, error_of(source))


def test_unterminated_triple_quote_hint():
    source = 'def f():\n    """doc\n    return 1\n'
    assert "triple-quoted" in syntax_error_report(source, error_of(source))


def test_indentation_hint():
    source = "def f():\nreturn 1\n"
    assert "4 spaces" in syntax_error_report(source, error_of(source))


def test_unknown_errors_still_show_the_code_and_never_raise():
    source = "x = = 1\n"
    report = syntax_error_report(source, error_of(source))
    assert "x = = 1" in report
    fake = SyntaxError("boom")
    fake.lineno = 999
    assert syntax_error_report("a\n", fake) == ""
    assert syntax_error_report(None, fake) == ""   # must not raise


def test_long_lines_are_clipped():
    source = "x = " + "1 + " * 200 + "\n"
    report = syntax_error_report(source, error_of(source))
    assert max(len(line) for line in report.splitlines()) < 140


def fs_execute(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, **kwargs))


def test_file_system_write_rejection_carries_the_report(tmp_path):
    result = fs_execute(tmp_path, action="write", path="src/temp_convert.py", content=TRY_NO_EXCEPT, scope="private")
    assert result["status"] == "error"
    message = result["message"]
    assert message.startswith("Write rejected due to Python SyntaxError in resulting code: expected 'except' or 'finally' block")
    assert "No changes were saved" in message
    assert "The `try:` on line 5" in message
    assert not (tmp_path / "src" / "temp_convert.py").exists()


def test_valid_python_is_still_written(tmp_path):
    result = fs_execute(tmp_path, action="write", path="ok.py", content="def f():\n    return 1\n", scope="private")
    assert result["status"] == "success"
    assert (tmp_path / "ok.py").exists()
