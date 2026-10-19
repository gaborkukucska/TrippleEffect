"""Round 1o tests (Temp_Convert_CLI run 2026-10-15 18:42-20:24).

1. Admin's final_response gate blocked "Kick-off complete! ... PROJECT_PLAN.md" as a false completion claim and sent Admin
   into 8 wasted LLM cycles.
2. W1 wrote src/core/temp_convert.py that used `logging` without importing it and whose main() was only a docstring; the
   write succeeded silently and W1 reported the task as done.
"""
import asyncio
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.tool_activity import claims_project_complete
from src.tools.python_static_check import python_code_notes

ROOT = Path(__file__).resolve().parents[2]

# ------------------------------------------------------------------------------------------------ completion claims

ADMIN_MESSAGE_18_54 = (
    "Great news: Kick-off complete! Your team has already started their work - W2 and W1 are now handling Tasks 1 & 2 "
    "respectively!\n\nProceed autonomously with Phase 1 from PROJECT_PLAN.md. Create the implementation and test tasks, "
    "assign them to your workers (Coder for code/tests + Technical Writer for README).\n\nI'll keep you updated as things progress"
)


def legacy_gate(text: str) -> bool:
    lower = text.lower()
    return ("complete" in lower or "finished" in lower or "done" in lower) and "project" in lower


def test_the_observed_admin_message_is_not_a_completion_claim():
    assert legacy_gate(ADMIN_MESSAGE_18_54) is True  # documents the bug
    assert claims_project_complete(ADMIN_MESSAGE_18_54) is False


@pytest.mark.parametrize("text", [
    "The project is complete.", "Project is finished and all deliverables are ready.", "All tasks are done.",
    "all tasks complete", "Project completed successfully!", "I have finished the project.", "PROJECT IS DONE",
])
def test_real_completion_claims_are_still_detected(text):
    assert claims_project_complete(text) is True


@pytest.mark.parametrize("text", [
    "Kick-off complete.", "Planning is done, the project plan is ready.", "Task 2 is complete; project continues.",
    "", None, "The project will be finished when the tests pass.",
])
def test_status_updates_are_not_completion_claims(text):
    assert claims_project_complete(text) is False


def test_cycle_handler_final_response_gate_uses_the_strict_detector():
    src = (ROOT / "src/agents/cycle_handler.py").read_text(encoding="utf-8")
    assert "claims_project_complete" in src
    assert 'if ("complete" in lower_fc or "finished" in lower_fc or "done" in lower_fc) and "project" in lower_fc:' not in src
    assert "if claims_project_complete(lower_fc):" in src

# ------------------------------------------------------------------------------------------------ static notes

W1_FILE = '"""Temperature conversion module."""\n\nimport sys\nfrom typing import Union\n\n\ndef celsius_to_fahrenheit(c: float) -> float:\n    """Convert."""\n    if not isinstance(c, (int, float)):\n        raise TypeError("bad")\n    try:\n        return round((c * 9 / 5) + 32, 2)\n    except Exception as e:\n        logging.error("Error converting Celsius to Fahrenheit", exc_info=True)\n        raise ValueError("Failed") from e\n\n\ndef main():\n    """Main entry point that parses command-line arguments and performs temperature conversion.\n\n    Example Usage from Command Line:\n        python temp_convert.py 100 C\n    """\n'


def test_the_real_w1_defects_are_reported():
    notes = python_code_notes(W1_FILE)
    assert len(notes) == 2
    assert "'logging'" in notes[0] and "line 14" in notes[0] and "import logging" in notes[0]
    assert "function 'main'" in notes[1] and "only a docstring" in notes[1]


def test_clean_file_has_no_notes():
    src = 'import argparse\nimport logging\nimport sys\nfrom pathlib import Path\n\nlog = logging.getLogger(__name__)\n\n\ndef main(argv=None):\n    """Entry."""\n    parser = argparse.ArgumentParser()\n    parser.add_argument("value")\n    args = parser.parse_args(argv)\n    log.info("%s", args.value)\n    return Path(sys.argv[0]).name and 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
    assert python_code_notes(src) == []


@pytest.mark.parametrize("src", [
    "import os.path\nprint(os.path.join('a'))\n",                  # dotted import binds 'os'
    "import logging as lg\nlg.info(1)\n",                          # alias
    "from os import path\npath.join('a')\n",                       # from-import
    "def f(logging):\n    logging.info(1)\n",                       # parameter
    "class A:\n    def m(self):\n        json = 1\n        return json.real\n",   # local assignment
    "try:\n    pass\nexcept Exception as sys:\n    sys.args\n",     # except-as
    "for time in [1]:\n    time.real\n",                           # loop target
    "with open('x') as string:\n    string.read()\n",              # with-as
    "def f():\n    global re\n    re = 1\n",                       # global
    "x = 'abc'.upper()\nprint(len(x).bit_length())\n",              # builtins / non-modules
    "import typing\nx: typing.List[int] = []\n",
])
def test_no_false_alarms_when_the_name_is_bound(src):
    assert python_code_notes(src) == []


def test_only_standard_library_module_names_are_flagged():
    # Round 2e: a name that is never imported is a NameError whatever it is; only a stdlib name gets "missing 'import X'"
    notes = python_code_notes("requests.get('x')\n")
    assert len(notes) == 1 and "NameError" in notes[0] and "missing 'import requests'" not in notes[0]
    assert python_code_notes("import requests\nrequests.get('x')\n") == []
    assert python_code_notes("self.x = 1\n") == []
    assert len(python_code_notes("math.sqrt(4)\n")) == 1


def test_each_missing_module_is_reported_once():
    notes = python_code_notes("os.getcwd()\nos.listdir('.')\nsys.exit(1)\n")
    assert len(notes) == 2 and "'os'" in notes[0] and "'sys'" in notes[1]


def test_stub_detection_is_narrow():
    assert python_code_notes('def a():\n    """doc"""\n') != []
    assert python_code_notes('async def a():\n    """doc"""\n') != []
    assert python_code_notes('def a():\n    """doc"""\n    pass\n') == []
    assert python_code_notes('def a():\n    """doc"""\n    return 1\n') == []
    assert python_code_notes('def a():\n    pass\n') == []
    assert python_code_notes('class P:\n    def m(self):\n        """interface"""\n') == []
    assert python_code_notes('import abc\n@abc.abstractmethod\ndef a():\n    """doc"""\n') == []
    assert python_code_notes('from typing import overload\n@overload\ndef a():\n    """doc"""\n') == []


def test_unparseable_and_odd_input_gives_no_notes():
    assert python_code_notes("def f(:\n") == []
    assert python_code_notes("") == []
    assert python_code_notes("x = 1\0") == []


def test_notes_are_capped():
    src = "\n".join(f"def f{i}():\n    \"\"\"d\"\"\"\n" for i in range(10))
    from src.tools.python_static_check import MAX_NOTES
    assert MAX_NOTES == 6 and len(python_code_notes(src)) == MAX_NOTES  # Round 2e: was 4

# ------------------------------------------------------------------------------------------------ file_system integration


def fs_execute(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, **kwargs))


def test_write_succeeds_and_appends_the_hints(tmp_path):
    r = fs_execute(tmp_path, action="write", path="src/core/temp_convert.py", content=W1_FILE)
    assert r["status"] == "success", r
    assert (tmp_path / "src/core/temp_convert.py").read_text() == W1_FILE          # saved unchanged
    assert "Successfully wrote content to 'src/core/temp_convert.py'" in r["message"]
    assert "[Framework check - the file WAS saved, but please fix:]" in r["message"]
    assert "'logging'" in r["message"] and "function 'main'" in r["message"]
    assert "do not resend the whole file" in r["message"]


def test_clean_write_message_is_unchanged(tmp_path):
    r = fs_execute(tmp_path, action="write", path="ok.py", content="import sys\n\n\ndef main():\n    return sys.argv\n")
    assert r["status"] == "success"
    assert "Framework check" not in r["message"]


def test_non_python_files_get_no_hints(tmp_path):
    r = fs_execute(tmp_path, action="write", path="notes.md", content="os.getcwd()\n")
    assert r["status"] == "success" and "Framework check" not in r["message"]


def test_syntax_errors_are_still_rejected_without_saving(tmp_path):
    r = fs_execute(tmp_path, action="write", path="bad.py", content="def f(:\n    pass\n")
    assert r["status"] == "error" and "SyntaxError" in r["message"]
    assert not (tmp_path / "bad.py").exists()


def test_a_failing_checker_never_breaks_the_write(tmp_path, monkeypatch):
    import src.tools.python_static_check as psc

    def boom(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(psc, "python_code_notes", boom)
    r = fs_execute(tmp_path, action="write", path="x.py", content="os.getcwd()\n")
    assert r["status"] == "success" and "Framework check" not in r["message"]
    assert (tmp_path / "x.py").exists()
