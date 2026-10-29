# START OF FILE tests/unit/test_round2a.py
"""Round 2a tests (run 2026-10-18 07:11-16:29, Temp_Converter_CLI: all 4 tasks 'finished', src/main.py and the tests hollow).

1. PM1 marked tasks finished whose files held only docstrings (main() without code, tests without asserts).
2. W1's complete rewrites of src/main.py were refused 8 times ("already exists") although it held no code.
3. The PM's project_structure listed only 'src/': no tests/ directory, no conftest.py, no "Files To Create".
"""
import asyncio
import shutil
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.utils.task_files import (completion_refusal_message, file_paths_in_text, find_in_workspace, task_completion_problems)
from src.tools.python_static_check import is_unimplemented_python

# The run's final files (shortened)
STUB_MAIN = '#!/usr/bin/env python3\n"""CLI entry point."""\n\n\ndef main():\n    """Main CLI function.\n\n    Usage: python src/main.py 100 C\n    """\n'
STUB_TESTS = ('"""Pytest unit tests."""\n\n\ndef test_celsius_to_fahrenheit_zero():\n    """0 C is 32 F."""\n\n\n'
              'import pytest\nfrom src.temp_convert import celsius_to_fahrenheit\n\n\ndef test_zero_conversion():\n    """0 C is 32 F."""\n')
GOOD_CONVERT = 'def celsius_to_fahrenheit(c):\n    return c * 9 / 5 + 32\n'
GOOD_MAIN = ('import sys\nfrom temp_convert import celsius_to_fahrenheit\n\n\ndef main():\n    """CLI."""\n'
             '    print(celsius_to_fahrenheit(float(sys.argv[1])))\n\n\nif __name__ == "__main__":\n    main()\n')
GOOD_TESTS = 'from temp_convert import celsius_to_fahrenheit\n\n\ndef test_zero():\n    assert celsius_to_fahrenheit(0) == 32\n'


def workspace(tmp_path, files):
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


# ------------------------------------------------------------------------------------------------ task_files helpers
@pytest.mark.parametrize("text,expected", [
    ("Create CLI entry point at src/main.py accepting values", ["src/main.py"]),
    ("Write pytest unit tests in tests/test_temp_convert.py covering 0°C→32°F", ["tests/test_temp_convert.py"]),
    ("Usage (python temp_convert.py 100 C → output) and docs/README.md", ["temp_convert.py", "docs/README.md"]),
    ("Run ./src/app.py, not /etc/passwd.txt or ../x.py; e.g. version 3.10", ["src/app.py"]),
    ("src/a.py and src/a.py again", ["src/a.py"]),
])
def test_file_paths_in_text(text, expected):
    assert file_paths_in_text(text) == expected


def test_file_paths_with_directory_only():
    assert file_paths_in_text("python temp_convert.py and src/main.py", with_directory_only=True) == ["src/main.py"]


def test_find_in_workspace(tmp_path):
    ws = workspace(tmp_path, {"src/main.py": "x", "docs/README.md": "x", "a/u.py": "1", "b/u.py": "2", "__pycache__/main.py": "x"})
    assert find_in_workspace(ws, "src/main.py") == (ws / "src/main.py").resolve()
    assert find_in_workspace(ws, "README.md") == (ws / "docs/README.md").resolve()
    assert find_in_workspace(ws, "u.py") is None                  # two files: ambiguous
    assert find_in_workspace(ws, "src/other.py") is None


# ------------------------------------------------------------------------------------------------ 1. completion problems
def test_the_runs_hollow_files_are_problems(tmp_path):
    ws = workspace(tmp_path, {"src/main.py": STUB_MAIN, "tests/test_temp_convert.py": STUB_TESTS, "src/temp_convert.py": GOOD_CONVERT})
    main_problems = task_completion_problems("Create CLI entry point at src/main.py accepting values", ws)
    assert main_problems == ["src/main.py: function 'main' (line 5) contains only a docstring and no code: its body is not implemented yet."]
    test_problems = task_completion_problems("Write pytest unit tests in tests/test_temp_convert.py", ws)
    assert len(test_problems) == 2 and all("only a docstring" in p for p in test_problems)
    assert task_completion_problems("Implement conversion functions in src/temp_convert.py", ws) == []


def test_good_files_missing_files_and_bare_names_are_not_problems(tmp_path):
    ws = workspace(tmp_path, {"src/main.py": GOOD_MAIN, "tests/test_temp_convert.py": GOOD_TESTS, "src/temp_convert.py": STUB_MAIN})
    assert task_completion_problems("CLI at src/main.py, tests in tests/test_temp_convert.py", ws) == []
    assert task_completion_problems("Write src/not_yet_written.py later", ws) == []
    # a bare name is usually a usage example of ANOTHER task's file: never blocks this task
    assert task_completion_problems("README in docs/ with usage `python temp_convert.py 100 C`", ws) == []


@pytest.mark.parametrize("text,expected", [("", "is EMPTY"), ("def f(:\n", "is not valid Python"),
                                           ("def test_x():\n    x = 1\n", "can never fail")])
def test_empty_invalid_and_never_failing_files(tmp_path, text, expected):
    ws = workspace(tmp_path, {"tests/test_a.py": text})
    problems = task_completion_problems("Tests in tests/test_a.py", ws)
    assert len(problems) == 1 and expected in problems[0]


def test_conftest_and_non_python_files_are_not_checked(tmp_path):
    ws = workspace(tmp_path, {"conftest.py": "", "docs/README.md": ""})
    assert task_completion_problems("Keep tests/conftest.py and docs/README.md", ws) == []


def test_refusal_message():
    msg = completion_refusal_message("abc", ["src/main.py is EMPTY."])
    assert "'abc' was NOT marked finished" in msg and "- src/main.py is EMPTY." in msg and "send_message" in msg


# ------------------------------------------------------------------------------------------------ 1. project_management gate
needs_taskwarrior = pytest.mark.skipif(shutil.which("task") is None, reason="Taskwarrior binary not installed")


@pytest.fixture
def tw_env(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    import src.tools.project_management as pm
    from src.config.settings import settings
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)
    ws = tmp_path / "projects" / "P" / "S" / "shared_workspace"
    ws.mkdir(parents=True)

    def call(agent, **kw):
        return asyncio.run(pm.ProjectManagementTool().execute(agent_id=agent, agent_sandbox_path=tmp_path,
                                                              project_name="P", session_name="S", **kw))
    return call, ws


def add(call, description):
    return call("PM1", action="add_task", description=description, assignee_agent_id="W1")["task_uuid"]


@needs_taskwarrior
@pytest.mark.parametrize("how", [{"action": "modify_task", "task_progress": "finished"},
                                 {"action": "modify_task", "status": "done"},
                                 {"action": "complete_task"}])
def test_a_task_with_a_hollow_file_is_not_finished(tw_env, how):
    call, ws = tw_env
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text(STUB_MAIN)
    uuid = add(call, "Create CLI entry point at src/main.py accepting temperature values")
    result = call("PM1", task_id=uuid, **how)
    assert result["status"] == "error" and "NOT marked finished" in result["message"] and "only a docstring" in result["message"]
    listed = call("PM1", action="list_tasks")
    assert "finished" not in str([t for t in listed.get("tasks", []) if t.get("uuid") == uuid])


@needs_taskwarrior
def test_the_task_finishes_once_the_file_is_fixed(tw_env):
    call, ws = tw_env
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text(STUB_MAIN)
    uuid = add(call, "Create CLI entry point at src/main.py")
    assert call("W1", action="modify_task", task_id=uuid, task_progress="finished")["status"] == "error"
    (ws / "src" / "main.py").write_text(GOOD_MAIN)
    result = call("W1", action="modify_task", task_id=uuid, task_progress="finished")
    assert result["status"] == "success" and result["task_progress"] == "finished"


@needs_taskwarrior
def test_other_progress_changes_are_never_checked(tw_env):
    call, ws = tw_env
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text(STUB_MAIN)
    uuid = add(call, "Create CLI entry point at src/main.py")
    # Round 4e: 'decomposed' needs a sub-task (tag parent:<uuid>) - the stub check still does not run for it
    assert call("W1", action="add_task", description="Write the argument parser of src/main.py",
                tags=f"parent:{uuid}")["status"] == "success"
    for progress in ("in_progress", "stuck", "decomposed", "deleted"):
        assert call("W1", action="modify_task", task_id=uuid, task_progress=progress)["status"] == "success", progress


@needs_taskwarrior
def test_tasks_without_files_finish_as_before(tw_env):
    call, _ = tw_env
    uuid = add(call, "Write the README in docs/ with installation instructions")
    assert call("W1", action="complete_task", task_id=uuid)["status"] == "success"


# ------------------------------------------------------------------------------------------------ 2. rewriting a file without code
@pytest.mark.parametrize("source,expected", [
    (STUB_MAIN, True), (STUB_TESTS, True),
    ('"""Doc."""\nimport sys\n\nclass A:\n    """A."""\n    def f(self):\n        pass\n\nif __name__ == "__main__":\n    A()\n', True),
    ("def f():\n    ...\n", True),
    (GOOD_MAIN, False), (GOOD_CONVERT, False),
    ("from .core import convert\n", False),           # imports only (a package __init__): real content
    ('"""Only a docstring."""\n', False), ("X = 1\ndef f():\n    pass\n", False), ("def f(:\n", False),
])
def test_is_unimplemented_python(source, expected):
    assert is_unimplemented_python(source) is expected


def write(tmp_path, path, content):
    from src.tools.file_system import FileSystemTool
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, scope="private", action="write", path=path, content=content))


def test_a_file_without_code_can_be_rewritten(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(STUB_MAIN)
    result = write(tmp_path, "src/main.py", GOOD_MAIN)
    assert result["status"] == "success" and "held no code" in result["message"]
    assert (tmp_path / "src" / "main.py").read_text() == GOOD_MAIN


@pytest.mark.parametrize("existing", [GOOD_MAIN, "from .core import convert\n"])
def test_a_file_with_code_still_needs_force_overwrite(tmp_path, existing):
    (tmp_path / "m.py").write_text(existing)
    result = write(tmp_path, "m.py", GOOD_CONVERT)
    assert result["status"] == "error" and "already exists" in result["message"]
    assert (tmp_path / "m.py").read_text() == existing


def test_non_python_files_are_unchanged(tmp_path):
    (tmp_path / "notes.md").write_text("# Title\n")
    assert write(tmp_path, "notes.md", "# New\n")["status"] == "error"


# ------------------------------------------------------------------------------------------------ 3. kick-off planned files
class Executor:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path

    async def execute_tool(self, agent_id, agent_sandbox_path, tool_name, tool_args, project_name, session_name, manager):
        import src.tools.project_management as pm
        return await pm.ProjectManagementTool().execute(agent_id, self.tmp_path, project_name=project_name,
                                                        session_name=session_name, **tool_args)


class FakeAgent:
    def __init__(self, tmp_path):
        self.agent_id = "PM1"
        self.sandbox_path = tmp_path
        self.agent_config = {"config": {"project_name_context": "P"}}
        self.message_history = []

    def clear_history(self):
        self.message_history = []


@pytest.fixture
def kickoff(tw_env, tmp_path, monkeypatch):
    import src.workflows.pm_kickoff_workflow as pkw

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    def run(plan):
        manager = types.SimpleNamespace(current_project="P", current_session="S", tool_executor=Executor(tmp_path))
        return asyncio.run(pkw.PMKickoffWorkflow().execute(manager, FakeAgent(tmp_path), plan))
    return run, tw_env[1]


RUN_TASKS = [
    "Implement conversion functions in src/temp_convert.py with celsius_to_fahrenheit and fahrenheit_to_celsius",
    "Create CLI entry point at src/main.py accepting temperature values via argparse (python temp_convert.py 100 C)",
    "Write pytest unit tests in tests/test_temp_convert.py covering 0°C→32°F conversion",
    "Generate README.md documentation in docs/ containing installation instructions",
]


@needs_taskwarrior
def test_the_runs_kickoff_creates_tests_dir_conftest_and_files_to_create(kickoff):
    run, ws = kickoff
    result = run({"roles": ["Coder"], "tasks": [{"description": d} for d in RUN_TASKS], "project_structure": [{"dir": "src"}]})
    assert result.success, result.message
    assert (ws / "src").is_dir() and (ws / "tests").is_dir()
    assert "FRAMEWORK-OWNED conftest" in (ws / "conftest.py").read_text()
    md = (ws / "PROJECT_STRUCTURE.md").read_text()
    assert "## Files To Create" in md
    for planned in ("src/temp_convert.py", "src/main.py", "tests/test_temp_convert.py"):
        assert planned in md
    assert not (ws / "src" / "main.py").exists()                  # planned only, never created


@needs_taskwarrior
def test_kickoff_without_structure_uses_the_task_files(kickoff):
    run, ws = kickoff
    result = run({"roles": ["Coder"], "tasks": [{"description": RUN_TASKS[0]}]})
    assert result.success, result.message
    assert (ws / "src").is_dir() and (ws / "conftest.py").is_file() and "src/temp_convert.py" in (ws / "PROJECT_STRUCTURE.md").read_text()


@needs_taskwarrior
def test_kickoff_without_structure_or_files_writes_nothing(kickoff):
    run, ws = kickoff
    result = run({"roles": ["Writer"], "tasks": [{"description": "Write a short poem about autumn"}]})
    assert result.success, result.message
    assert not (ws / "PROJECT_STRUCTURE.md").exists() and not (ws / "conftest.py").exists()

# END OF FILE tests/unit/test_round2a.py
