# START OF FILE tests/unit/test_round2m.py
"""
Round 2m: a clean test suite (no false failures, no tests that silently never run, no warnings) and the real defects the
repaired tests found. The repaired test files carry their own notes; this file covers what has no other home:

* agents' Python code is parsed without printing Python's SyntaxWarnings to the server's stderr, and the warnings
  (an invalid escape such as '\\T') are reported to the agent as a code note instead;
* ast.literal_eval of a 'chunks' string with an invalid escape is quiet as well;
* Pydantic v2: tool schemas use model_dump() (.dict() is deprecated; the workflow result event is tested in
  test_agent_core);
* SQLAlchemy 2: declarative_base comes from sqlalchemy.orm (sqlalchemy.ext.declarative is deprecated).
"""
import asyncio
import inspect
import types
import warnings

import src.agents  # noqa: F401 - resolves the pre-existing circular import order
from src.tools.code_editor import CodeEditorTool, _literal_eval_chunks
from src.tools.python_static_check import parse_python, python_code_notes, python_source_warnings

BAD_ESCAPE_SOURCE = 'PATH = "C:\\Temp"\n\n\ndef main():\n    return PATH\n'


def _caught(callable_, *args):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = callable_(*args)
    return result, [str(w.message) for w in caught]


# --- quiet parsing ---------------------------------------------------------------------------------------------------------
def test_the_source_really_has_an_invalid_escape():
    import ast
    _, messages = _caught(ast.parse, BAD_ESCAPE_SOURCE)
    assert any("escape sequence" in message for message in messages)


def test_parse_python_is_quiet_and_returns_the_tree():
    tree, messages = _caught(parse_python, BAD_ESCAPE_SOURCE)
    assert messages == []
    assert [type(node).__name__ for node in tree.body] == ["Assign", "FunctionDef"]


def test_parse_python_still_raises_syntax_errors():
    try:
        parse_python("def (")
    except SyntaxError:
        return
    raise AssertionError("SyntaxError expected")


def test_python_source_warnings():
    # the wording differs: "invalid escape sequence '\\T'" (3.12/3.13), '"\\T" is an invalid escape sequence. ...' (3.14)
    (line, message), = python_source_warnings(BAD_ESCAPE_SOURCE)
    assert line == 1 and "invalid escape sequence" in message and "\\T" in message
    assert python_source_warnings('PATH = r"C:\\Temp"\n') == []
    assert python_source_warnings("def (") == []


def test_the_warning_becomes_a_code_note():
    notes, messages = _caught(python_code_notes, BAD_ESCAPE_SOURCE, "src/paths.py")
    assert messages == []
    assert notes == ["line 1: invalid escape sequence '\\T' in a string (Python warns; it will be an error in a future "
                     "version) - write the backslash as '\\\\' or use a raw string r'...'."]
    assert python_code_notes('PATH = "C:\\\\Temp"\n', "src/paths.py") == []


def test_code_editor_write_is_quiet_and_reports_the_escape(tmp_path):
    (tmp_path / "src").mkdir()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = asyncio.run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="write",
                                                      filename="src/paths.py", content=BAD_ESCAPE_SOURCE))
    assert result["status"] == "success", result
    assert not [w for w in caught if issubclass(w.category, (SyntaxWarning, DeprecationWarning))]
    assert "invalid escape sequence" in result["message"]
    assert (tmp_path / "src" / "paths.py").read_text() == BAD_ESCAPE_SOURCE


def test_literal_eval_of_chunks_is_quiet():
    text = "[{'search': 'x', 'replace': 'C:\\Temp'}]"
    value, messages = _caught(_literal_eval_chunks, text)
    assert messages == []
    assert value == [{"search": "x", "replace": "C:\\Temp"}]


# --- library deprecations ------------------------------------------------------------------------------------------------
def test_tool_schema_uses_model_dump():
    from src.tools.base import ToolParameter
    tool = CodeEditorTool()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = tool.get_schema()
    assert schema["name"] == "code_editor"
    assert all(isinstance(p, dict) and "name" in p for p in schema["parameters"])
    assert schema["parameters"][0] == ToolParameter(**schema["parameters"][0]).model_dump()


def test_declarative_base_from_sqlalchemy_orm():
    from src.core import database_manager
    source = inspect.getsource(database_manager)
    assert "from sqlalchemy.ext.declarative import" not in source
    assert "from sqlalchemy.orm import declarative_base" in source
    assert hasattr(database_manager.Base, "metadata")
