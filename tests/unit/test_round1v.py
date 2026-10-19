"""Round 1v tests (Temp_Converter_CLI run 2026-10-16 19:25-22:58, 3.5 hours, never finished).

1. Dependencies were ignored: the tests task (depends on task 1) and the README task (depends on task 2) were started while
   task 1 was still in progress. The README was written before any test existed; the test task never finished.
2. Editing tools rejected reasonable calls or failed silently: 'chunks' JSON with raw line breaks (6x), find_replace with
   other indentation reported SUCCESS with 0 changes (2x), replace_lines with 'replace_text' instead of 'content' (3x),
   priority='normal'; insert_lines/replace_lines appended a literal backslash-n instead of a newline.
3. PROJECT_STRUCTURE.md's 'README.md  (planned)' marker was copied into real paths: a directory 'docs/README.md (force)',
   files 'docs/README.md (new)' ... ending as 'docs/README_final_v1.txt'.
4. The kickoff plan {"dir": "root/temp_convert.py", "files": ["temp_convert.py"]} created root/ and root/temp_convert.py/.
5. {"list_tasks": '{"action": "list"}'} (5x) and filenames='["tests/x.py"]' (4x) failed although the intent was clear.
6. W2 saved a test file with the same test defined twice and no assert at all; the write just said "Successfully".
"""
import asyncio
import json
import shutil
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
import src.tools.project_management as pm
from src.utils.task_utils import unfinished_dependencies

try:
    import tasklib  # noqa: F401
    HAVE_TW = shutil.which("task") is not None
except ImportError:  # pragma: no cover
    HAVE_TW = False

needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


# ======================================================================================================== 1. dependencies
class Dep(dict):
    def __getitem__(self, key):
        return self.get(key)


def task_with(*deps):
    return Dep(uuid="t", description="task", depends=list(deps))


def test_unfinished_dependencies_lists_only_open_ones():
    open_dep = Dep(uuid="a", description="Implement   core\nfunctions", status="pending", task_progress="in_progress")
    done = Dep(uuid="b", description="done", status="completed", task_progress="finished")
    finished_but_pending = Dep(uuid="c", description="x", status="pending", task_progress="finished")
    decomposed = Dep(uuid="d", description="x", status="pending", task_progress="decomposed")
    deleted = Dep(uuid="e", description="x", status="deleted", task_progress="deleted")
    assert unfinished_dependencies(task_with(open_dep, done, finished_but_pending, decomposed, deleted)) == [
        ("a", "Implement core functions")]


@pytest.mark.parametrize("task", [None, Dep(uuid="t"), task_with(), task_with("some-uuid-string"), Dep(depends=5)])
def test_unfinished_dependencies_never_blocks_on_unreadable_data(task):
    assert unfinished_dependencies(task) == []


@pytest.fixture
def tw_env(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    import src.agents.manager as manager_module   # activation writes a workspace listing under PROJECTS_BASE_DIR
    monkeypatch.setattr(manager_module.settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)
    tool = pm.ProjectManagementTool()

    def call(agent, **kw):
        return run(tool.execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P", session_name="S", **kw))
    return tool, call


def make_manager(project="P", session="S"):
    from src.agents.manager import AgentManager
    from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_STARTUP

    manager = object.__new__(AgentManager)   # no __init__: no timers, no DB
    manager.current_project, manager.current_session = project, session
    manager.current_session_db_id = None
    worker = types.SimpleNamespace(agent_id="W2", agent_type=AGENT_TYPE_WORKER, state=WORKER_STATE_STARTUP,
                                   status="idle", message_history=[], message_inbox=[])
    worker.set_status = lambda status: setattr(worker, "status", status)
    manager.agents = {"W2": worker}
    manager.scheduled = []

    async def schedule_cycle(agent, retry_count=0):
        manager.scheduled.append(agent.agent_id)
    manager.schedule_cycle = schedule_cycle
    manager.workflow_manager = types.SimpleNamespace(
        change_state=lambda agent, state: setattr(agent, "state", state),
        get_system_prompt=lambda agent, mgr: "system prompt",
    )
    return manager, worker


@needs_tw
def test_a_task_with_an_unfinished_dependency_is_not_started(tw_env):
    _, call = tw_env
    first = call("PM1", action="add_task", description="Implement the converter", task_id="task_1")
    second = call("PM1", action="add_task", description="Write the pytest tests", task_id="task_2", depends="task_1")
    manager, worker = make_manager()

    assert manager.get_unfinished_dependencies(second["task_uuid"]) == [(first["task_uuid"], "Implement the converter")]
    outcome = run(manager.activate_worker_with_task_details("W2", second["task_uuid"], "Write the pytest tests"))
    assert outcome == "blocked"
    assert manager.scheduled == [] and worker.state == "worker_startup" and worker.message_history == []

    call("W1", action="modify_task", task_id=first["task_uuid"], task_progress="finished")
    assert manager.get_unfinished_dependencies(second["task_uuid"]) == []
    outcome = run(manager.activate_worker_with_task_details("W2", second["task_uuid"], "Write the pytest tests"))
    assert outcome == "activated" and manager.scheduled == ["W2"] and worker.state == "worker_decompose"


@needs_tw
def test_tasks_without_dependencies_and_finish_notices_are_unchanged(tw_env):
    _, call = tw_env
    first = call("PM1", action="add_task", description="Implement the converter")
    second = call("PM1", action="add_task", description="Write tests", depends=first["task_uuid"])
    manager, _ = make_manager()
    assert run(manager.activate_worker_with_task_details("W2", first["task_uuid"], "Implement")) == "activated"
    manager2, _ = make_manager()
    # "the PM marked your task finished" notices are never held back by dependencies
    assert run(manager2.activate_worker_with_task_details("W2", second["task_uuid"], "Write tests", "finished")) == "activated"


def test_dependency_lookup_fails_open():
    manager, _ = make_manager(project=None)
    assert manager.get_unfinished_dependencies("whatever") == []
    manager, _ = make_manager()
    assert manager.get_unfinished_dependencies("") == []


def test_busy_worker_outcome_is_reported(monkeypatch):
    from src.agents.constants import WORKER_STATE_WORK
    manager, worker = make_manager()
    monkeypatch.setattr(type(manager), "get_unfinished_dependencies", lambda self, task_id: [])
    worker.state = WORKER_STATE_WORK
    assert run(manager.activate_worker_with_task_details("W2", "u", "desc")) == "deferred_busy"
    assert len(worker.message_inbox) == 1 and manager.scheduled == []


@needs_tw
def test_watchdog_starts_only_ready_tasks(tw_env, monkeypatch):
    """The watchdog activates an idle worker (worker_wait OR worker_startup) only for a task whose dependencies are done."""
    import src.agents.manager as manager_module
    _, call = tw_env
    first = call("PM1", action="add_task", description="Implement the converter")
    second = call("PM1", action="add_task", description="Write tests", depends=first["task_uuid"], assignee_agent_id="W2")
    manager, worker = make_manager()
    manager.state_manager = types.SimpleNamespace(get_agent_team=lambda aid: None, get_agents_in_team=lambda t: [])
    worker.state = "worker_startup"
    activated = []

    async def fake_activate(worker_agent_id, task_id_from_tool, task_description_from_tool, task_progress_from_tool="todo"):
        activated.append(task_id_from_tool)
    manager.activate_worker_with_task_details = fake_activate

    def one_pass():
        calls = {"n": 0}

        async def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] > 1:
                raise asyncio.CancelledError
        monkeypatch.setattr(manager_module.asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            run(manager._periodic_pm_manage_check())

    one_pass()
    assert activated == []                                  # task 1 still open: nothing starts
    call("W1", action="modify_task", task_id=first["task_uuid"], task_progress="finished")
    one_pass()
    assert activated == [second["task_uuid"]]               # now it starts, from worker_startup


def test_pm_is_told_what_happened_to_the_assignment():
    from src.agents.interaction_handler import AgentInteractionHandler
    handler = object.__new__(AgentInteractionHandler)
    handler._manager = types.SimpleNamespace(
        get_unfinished_dependencies=lambda task_id: [("69f65249-1463-4be6-b2cc-a6271d36d74d", "Kick-off Task 1: Implement core")])
    blocked = handler._activation_notification("W2", "70e36bf4", "blocked")
    assert "not finished yet" in blocked and "Kick-off Task 1" in blocked and "69f65249" in blocked
    assert "started automatically" in blocked and "do not assign it again" in blocked
    assert "queued" in handler._activation_notification("W2", "x", "deferred_busy")
    for outcome in ("activated", None):
        assert "automatically activated" in handler._activation_notification("W2", "x", outcome)


# ======================================================================================================== 2. priorities
@pytest.mark.parametrize("value,expected", [
    ("normal", "M"), ("Normal", "M"), ("medium", "M"), ("M", "M"), ("high", "H"), ("urgent", "H"), ("low", "L"),
    (" l ", "L"), ("", ""), ("none", ""), (None, ""), ("sky-high", None), (3, None),
])
def test_normalize_priority(value, expected):
    assert pm.normalize_priority(value) == expected


@needs_tw
def test_modify_task_accepts_normal_priority(tw_env):
    _, call = tw_env
    created = call("PM1", action="add_task", description="Implement the converter", priority="normal")
    assert created["status"] == "success", created
    result = call("W1", action="modify_task", task_id=created["task_uuid"], priority="normal", task_progress="in_progress")
    assert result["status"] == "success" and "priority" in result["modified_fields"], result
    bad = call("W1", action="modify_task", task_id=created["task_uuid"], priority="sky-high")
    assert bad["status"] == "error" and "Valid priorities are: H (high), M (medium), L (low)." in bad["message"]


# ======================================================================================================== 5. keyed action
@pytest.mark.parametrize("args,action,extra", [
    ({"list_tasks": '{"action": "list"}'}, "list_tasks", {}),
    ({"list_tasks": '{"task_id": "", ""}'}, "list_tasks", {}),                       # invalid JSON value: ignored
    ({"list_tasks": '{"action": "get_info"}'}, "list_tasks", {}),
    ({"modify_task": {"task_id": "abc", "task_progress": "finished"}}, "modify_task", {"task_id": "abc", "task_progress": "finished"}),
    ({"complete_task": "", "task_id": "t1"}, "complete_task", {"task_id": "t1"}),
])
def test_action_given_as_a_parameter_name(args, action, extra):
    from src.tools.executor import infer_action_from_keys
    assert infer_action_from_keys("project_management", args) == action
    assert args == {"action": action, **extra}


@pytest.mark.parametrize("tool,args", [
    ("project_management", {"action": "list_tasks", "add_task": "x"}),      # action present: untouched
    ("project_management", {"list_tasks": "x", "add_task": "y"}),           # ambiguous
    ("project_management", {"task_id": "x"}),                               # nothing to infer
    ("file_system", {"read": "a.py"}),                                      # other tools: untouched
])
def test_action_inference_is_conservative(tool, args):
    from src.tools.executor import infer_action_from_keys
    before = dict(args)
    assert infer_action_from_keys(tool, args) is None
    assert args == before


# ======================================================================================================== code_editor
def ce_execute(tmp_path, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    return run(CodeEditorTool().execute("W1", tmp_path, scope="private", **kwargs))


CONVERTER = '''def main():
    """Main entry point for CLI interface.

    Usage examples:
        python temp_convert.py 100 C
    """
    import sys
    print(sys.argv)
'''


def test_chunks_json_with_raw_line_breaks_is_accepted(tmp_path):
    (tmp_path / "t.py").write_text(CONVERTER)
    # what the 4B model sent: JSON whose strings contain REAL line breaks ("Invalid control character" before)
    raw = '[{"start_line": 7, "end_line": 8, "replace": "    import argparse\n    parser = argparse.ArgumentParser()\n    print(parser)"}]'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    result = ce_execute(tmp_path, action="replace_chunks", filename="t.py", chunks=raw)
    assert result["status"] == "success", result
    assert "parser = argparse.ArgumentParser()" in (tmp_path / "t.py").read_text()


def test_code_editor_syntax_rejection_shows_the_code(tmp_path):
    (tmp_path / "t.py").write_text(CONVERTER)
    chunks = [{"start_line": 1, "end_line": 6, "replace": 'def main():\n    """Main entry point.\n    Usage examples:'}]
    result = ce_execute(tmp_path, action="replace_chunks", filename="t.py", chunks=chunks)
    assert result["status"] == "error" and "No changes were saved" in result["message"]
    assert "Code around the error:" in result["message"] and "triple-quoted" in result["message"]
    assert (tmp_path / "t.py").read_text() == CONVERTER


# ======================================================================================================== 6. static hints
W2_TEST_FILE = '''"""Test cases for temperature conversion functions."""


def test_celsius_to_fahrenheit_basic():
    """Test basic Celsius to Fahrenheit conversions."""


def test_celsius_to_fahrenheit_basic():
    """Test basic Celsius to Fahrenheit conversions."""

    # Zero degrees
    import math
'''


def test_observed_test_file_gets_duplicate_and_empty_test_notes():
    from src.tools.python_static_check import python_code_notes
    notes = python_code_notes(W2_TEST_FILE, "tests/test_temp_convert.py")
    text = "\n".join(notes)
    assert "'test_celsius_to_fahrenheit_basic' is defined twice (lines 4 and 8)" in text
    assert "contains only a docstring" in text                 # the first one (Round 1o rule)
    assert "has no assert and calls nothing" in text           # the second one


@pytest.mark.parametrize("source", [
    "def test_a():\n    assert 1 + 1 == 2\n",
    "import pytest\n\n\ndef test_a():\n    with pytest.raises(ValueError):\n        int('x')\n",
    "def helper():\n    return 1\n\n\ndef test_a():\n    helper()\n",  # Round 2e: helper must exist (else NameError note)
    "class TestX:\n    def test_a(self):\n        self.assertEqual(1, 1)\n",
    "def f():\n    return 1\n\n\ndef g():\n    return 2\n",
    "class A:\n    @property\n    def x(self):\n        return 1\n\n    @x.setter\n    def x(self, v):\n        self._x = v\n",
    "from typing import overload\n\n\n@overload\ndef f(a: int) -> int: ...\n@overload\ndef f(a: str) -> str: ...\ndef f(a):\n    return a\n",
])
def test_no_false_alarms(source):
    from src.tools.python_static_check import python_code_notes
    assert python_code_notes(source, "tests/test_x.py") == []


def test_empty_test_rule_only_applies_to_test_files():
    from src.tools.python_static_check import python_code_notes
    source = "def test_helper():\n    x = 1\n"
    assert python_code_notes(source, "src/tools.py") == []
    assert len(python_code_notes(source, "tests/test_tools.py")) == 1
    assert len(python_code_notes(source)) == 1        # no file name given: checked


def test_duplicate_methods_in_a_class_are_reported():
    from src.tools.python_static_check import python_code_notes
    notes = python_code_notes("class A:\n    def m(self):\n        return 1\n\n    def m(self):\n        return 2\n", "src/a.py")
    assert len(notes) == 1 and "'m' is defined twice (lines 2 and 5)" in notes[0]


# ======================================================================================================== 2/3/5. file_system
def fs(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return run(FileSystemTool().execute("W1", tmp_path, scope="private", **kwargs))


@pytest.mark.parametrize("path,expected", [
    ("docs/README.md (new)", "docs/README.md"),
    ("docs/README.md_(new)", "docs/README.md"),
    ("docs/README.md (force_rename_fixed.txt)", "docs/README.md"),
    ("tests/test_temp_convert.py (force)", "tests/test_temp_convert.py"),
])
def test_bracket_notes_are_not_part_of_a_path(tmp_path, path, expected):
    result = fs(tmp_path, action="write", path=path, content="# Title\n")
    assert result["status"] == "success", result
    assert (tmp_path / expected).read_text() == "# Title\n"
    assert "a note in brackets is not part of a path" in result["message"]
    assert [p.name for p in (tmp_path / expected).parent.iterdir()] == [expected.split("/")[-1]]


def test_existing_names_with_brackets_are_left_alone(tmp_path):
    (tmp_path / "notes_(old).txt").write_text("keep\n")
    result = fs(tmp_path, action="read", path="notes (old).txt")      # spaces become '_' later: the real file
    assert result["status"] == "success" and "keep" in result["content"]


def test_list_with_a_bracket_note(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x")
    result = fs(tmp_path, action="list", path="docs (force)")
    assert result["status"] == "success" and "a.md" in result["message"]


@pytest.mark.parametrize("path", ["docs/README.md (force)", "docs/README.md", "src/main.py"])
def test_mkdir_refuses_file_names(tmp_path, path):
    result = fs(tmp_path, action="mkdir", path=path)
    assert result["status"] == "error" and "is a FILE name" in result["message"]
    assert not (tmp_path / "docs" / "README.md").exists() and not (tmp_path / "src" / "main.py").exists()


@pytest.mark.parametrize("path", ["docs", "config.d", "v1.0", ".github/workflows"])
def test_mkdir_still_creates_directories(tmp_path, path):
    assert fs(tmp_path, action="mkdir", path=path)["status"] == "success"
    assert (tmp_path / path).is_dir()


def test_single_element_filenames_list_is_used(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("a = 1\nb = 2\n")
    for value in ('["tests/test_a.py"]', ["tests/test_a.py"]):
        result = fs(tmp_path, action="read", filenames=value, path=".", start_line=2, end_line=2)
        assert result["status"] == "success", result
        assert "b = 2" in result["content"] and "a = 1" not in result["content"]
    assert fs(tmp_path, action="read", filenames='["a.py", "b.py"]')["status"] == "error"


def test_reading_a_directory_says_so(tmp_path):
    (tmp_path / "x.txt").write_text("1")
    result = fs(tmp_path, action="read", path=".")
    assert result["status"] == "error" and "is a directory, not a file" in result["message"]
    assert "x.txt" in result["message"] and "contains no files" not in result["message"]


def test_replace_lines_accepts_replace_text_as_content(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
    result = fs(tmp_path, action="replace_lines", path="a.py", start_line=2, end_line=2,
                find_text="y = 2", replace_text="y = 20")
    assert result["status"] == "success", result
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 20\nz = 3\n"


@pytest.mark.parametrize("action,extra", [
    ("replace_lines", {"replace_start_line": 2, "replace_end_line": 2}),
    ("insert_lines", {"insert_line": 2}),
])
def test_line_edits_add_a_real_newline(tmp_path, action, extra):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
    result = fs(tmp_path, action=action, path="a.py", content="w = 0", **extra)    # no trailing newline
    assert result["status"] == "success", result
    text = (tmp_path / "a.py").read_text()
    assert "\\n" not in text and "w = 0\n" in text
    assert text.splitlines()[1] == "w = 0"


def test_insert_at_the_end_of_a_file_without_final_newline(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    assert fs(tmp_path, action="insert_lines", path="a.txt", insert_line=2, content="two\n")["status"] == "success"
    assert (tmp_path / "a.txt").read_text() == "one\ntwo\n"


def test_line_edits_that_break_python_are_rejected(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    result = fs(tmp_path, action="replace_lines", path="a.py", replace_start_line=2, replace_end_line=2, content="return 1")
    assert result["status"] == "error" and "would not be valid Python" in result["message"]
    assert (tmp_path / "a.py").read_text() == "def f():\n    return 1\n"


def test_an_already_broken_python_file_can_still_be_edited(tmp_path):
    (tmp_path / "a.py").write_text("def f(:\n    return 1\n")
    result = fs(tmp_path, action="replace_lines", path="a.py", replace_start_line=1, replace_end_line=1, content="def f(:  # still wip")
    assert result["status"] == "success"


W1_MAIN = '''def main():
    """Main entry point for CLI interface.

    Usage examples:
        python temp_convert.py 100 C      # Convert 100C -> prints "212"

    """
    import sys
    return sys.argv
'''


def test_find_replace_exact_match_still_works(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    result = fs(tmp_path, action="find_replace", path="a.py", find_text="x = 1", replace_text="x = 2")
    assert result["status"] == "success" and result["replacements_made"] == 2
    assert (tmp_path / "a.py").read_text() == "x = 2\nx = 2\n"


def test_find_replace_ignores_indentation_differences(tmp_path):
    """W1's find_text had 'Usage examples:' without the indentation the file has (0 occurrences, reported as success)."""
    (tmp_path / "t.py").write_text(W1_MAIN)
    find_text = 'def main():\n    """Main entry point for CLI interface.\n\nUsage examples:\n    python temp_convert.py 100 C      # Convert 100C -> prints "212"'
    replace_text = 'def main():\n    """Main entry point (argparse).\n\n    Usage examples:\n        python temp_convert.py 100 C'
    result = fs(tmp_path, action="find_replace", path="t.py", find_text=find_text, replace_text=replace_text)
    assert result["status"] == "success", result
    assert "ignoring differences in indentation" in result["message"]
    text = (tmp_path / "t.py").read_text()
    assert text.startswith('def main():\n    """Main entry point (argparse).\n') and "import sys" in text
    compile(text, "t.py", "exec")


def test_find_replace_not_found_is_an_error_with_a_hint(tmp_path):
    (tmp_path / "t.py").write_text(W1_MAIN)
    result = fs(tmp_path, action="find_replace", path="t.py", find_text="    import  sys\n    return sys.argvv", replace_text="pass")
    assert result["status"] == "error" and result["replacements_made"] == 0
    assert "was NOT found" in result["message"] and "8:     import sys" in result["message"]
    assert (tmp_path / "t.py").read_text() == W1_MAIN


def test_find_replace_ambiguous_whitespace_match_is_refused(tmp_path):
    (tmp_path / "a.txt").write_text("  a\nb\n    a\nb\n")
    result = fs(tmp_path, action="find_replace", path="a.txt", find_text="a\n  b", replace_text="c")
    assert result["status"] == "error" and "several places" in result["message"]


def test_find_replace_that_breaks_python_is_rejected(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    result = fs(tmp_path, action="find_replace", path="a.py", find_text="    return 1", replace_text="return 1")
    assert result["status"] == "error" and "would not be valid Python" in result["message"]
    assert (tmp_path / "a.py").read_text() == "def f():\n    return 1\n"


def test_regex_replace_without_matches_is_an_error(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    assert fs(tmp_path, action="regex_replace", path="a.txt", regex_pattern="^bye", replace_text="x")["status"] == "error"
    result = fs(tmp_path, action="regex_replace", path="a.txt", regex_pattern="^hel", replace_text="HEL")
    assert result["status"] == "success" and (tmp_path / "a.txt").read_text() == "HELlo\n"


def test_write_hints_include_the_new_test_checks(tmp_path):
    result = fs(tmp_path, action="write", path="tests/test_temp_convert.py", content=W2_TEST_FILE)
    assert result["status"] == "success"                      # hints never block a write
    assert "is defined twice" in result["message"] and "has no assert" in result["message"]


# ======================================================================================================== 3/4. kickoff layout
OBSERVED_STRUCTURE = [
    {"dir": "root/temp_convert.py", "files": ["temp_convert.py"]},
    {"dir": "tests/test_temp_convert.py"},
    {"dir": "docs/README.md"},
]


def test_observed_kickoff_layout_becomes_usable():
    from src.workflows.pm_kickoff_workflow import normalize_planned_layout, plan_project_structure
    dirs, files, notes = plan_project_structure(OBSERVED_STRUCTURE)
    dirs, files, _ = normalize_planned_layout(dirs, files)
    assert sorted(files) == ["docs/README.md", "temp_convert.py", "tests/test_temp_convert.py"]
    assert sorted(dirs) == ["docs", "tests"]
    assert not any(d.startswith("root") or d.endswith(".py") for d in dirs)
    assert any("means the workspace root" in n for n in notes)


@pytest.mark.parametrize("structure,files,dirs", [
    ([{"dir": "root", "files": ["main.py"]}], ["main.py"], []),
    (["root/app.py"], ["app.py"], []),
    ([{"file": "root/README.md"}], ["README.md"], []),
    ([{"dir": "src", "children": [{"dir": "root", "files": ["x.py"]}]}], ["src/root/x.py"], ["src", "src/root"]),  # nested: real dir
    ([{"dir": "pkg/module.py", "children": [{"file": "helper.py"}]}], ["pkg/module.py", "pkg/helper.py"], ["pkg"]),
    ([{"dir": "config.d", "files": ["a.conf"]}], ["config.d/a.conf"], ["config.d"]),               # unchanged (Round 1c)
])
def test_root_aliases_and_file_named_dirs(structure, files, dirs):
    from src.workflows.pm_kickoff_workflow import plan_project_structure
    got_dirs, got_files, _ = plan_project_structure(structure)
    assert sorted(got_files) == sorted(files) and sorted(got_dirs) == sorted(dirs)


def test_structure_markdown_has_no_bracket_markers():
    from src.workflows.pm_kickoff_workflow import render_project_structure_md
    md = render_project_structure_md(["docs", "tests"], ["temp_convert.py", "docs/README.md"], None)
    assert "(planned)" not in md
    assert "## Files To Create" in md and "- docs/README.md" in md and "- temp_convert.py" in md
    assert "Paths never contain notes in brackets" in md
    layout = md.split("```")[1]
    assert "README.md" in layout and "docs/" in layout


def test_bloat_protection_still_reads_the_new_layout(tmp_path):
    """file_system's Workspace Bloat Protection parses the top-level entries of the layout block."""
    from src.workflows.pm_kickoff_workflow import render_project_structure_md
    from src.tools.file_system import FileSystemTool
    ws = tmp_path / "projects" / "P" / "S" / "shared_workspace"
    ws.mkdir(parents=True)
    (ws / "PROJECT_STRUCTURE.md").write_text(render_project_structure_md(["myapp"], ["myapp/x.py"], None))
    import src.tools.file_system as fsm
    original = fsm.settings.PROJECTS_BASE_DIR
    try:
        fsm.settings.PROJECTS_BASE_DIR = tmp_path / "projects"
        ok = run(FileSystemTool().execute("W1", tmp_path, project_name="P", session_name="S", action="write", path="myapp/x.py", content="x = 1\n"))
        bad = run(FileSystemTool().execute("W1", tmp_path, project_name="P", session_name="S", action="write", path="random/y.py", content="y = 1\n"))
    finally:
        fsm.settings.PROJECTS_BASE_DIR = original
    assert ok["status"] == "success", ok
    assert bad["status"] == "error" and "Workspace Bloat Protection" in bad["message"]
