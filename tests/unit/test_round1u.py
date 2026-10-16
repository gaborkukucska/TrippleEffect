"""Round 1u tests (run 2026-10-16 18:01-19:11): inconsistent planned layout + endless conftest/import fights; all 15 tests of
the finished project failed with ModuleNotFoundError."""
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.workflows.pm_kickoff_workflow import (
    FRAMEWORK_CONFTEST, FRAMEWORK_CONFTEST_MARKER, PMKickoffWorkflow, normalize_planned_layout,
    plan_project_structure, render_project_structure_md,
)

ROOT = Path(__file__).resolve().parents[2]

# the PM's actual structure from the run
OBSERVED = [
    {"dir": ".", "children": [{"files": ["temp_convert.py", "test_temp_convert.py"]}]},
    {"dir": "src"},
    {"dir": "docs", "children": [{"file": "README.md"}]},
    {"dir": "tests", "children": []},
]


def plan(structure):
    dirs, files, _ = plan_project_structure(structure)
    return normalize_planned_layout(dirs, files)


def test_observed_layout_becomes_consistent():
    dirs, files, notes = plan(OBSERVED)
    assert "src/temp_convert.py" in files and "tests/test_temp_convert.py" in files and "docs/README.md" in files
    assert "temp_convert.py" not in files and "test_temp_convert.py" not in files
    assert {"src", "tests", "docs"} <= set(dirs)
    assert len(notes) == 2


def test_tests_always_move_to_tests_even_without_a_declared_tests_dir():
    dirs, files, _ = plan([{"dir": "src", "files": ["a.py"]}, {"files": ["test_a.py"]}, {"dir": "pkg", "files": ["b_test.py"]}])
    assert "tests/test_a.py" in files and "tests/b_test.py" in files and "src/a.py" in files
    assert "tests" in dirs


def test_root_python_files_stay_when_the_plan_has_no_src():
    _, files, notes = plan([{"files": ["main.py", "test_main.py"]}])
    assert "main.py" in files and "tests/test_main.py" in files
    assert len(notes) == 1


def test_nothing_else_is_touched():
    dirs, files, notes = plan([{"dir": "src", "children": [{"dir": "core", "files": ["x.py"]}]},
                               {"dir": "docs", "files": ["README.md", "NOTES.md"]}, {"files": ["setup.py", "conftest.py", "README.md"]}])
    assert "src/core/x.py" in files and "setup.py" in files and "conftest.py" in files and "README.md" in files
    assert notes == []


def test_duplicates_collapse():
    _, files, _ = plan([{"dir": "src"}, {"files": ["test_a.py"]}, {"dir": "tests", "files": ["test_a.py"]}])
    assert files.count("tests/test_a.py") == 1


def test_structure_markdown_tells_workers_about_pytest():
    md = render_project_structure_md(["src", "tests"], ["src/a.py"], None)
    assert "python -m pytest -q" in md and "NEVER create or edit the root conftest.py" in md


# ---------------------------------------------------------------------------- scaffold
def workflow():
    return object.__new__(PMKickoffWorkflow)


def test_conftest_is_written_for_python_projects_only_once(tmp_path):
    workflow()._write_test_scaffold(tmp_path, ["src/a.py", "docs/README.md"])
    conftest = tmp_path / "conftest.py"
    assert conftest.exists() and FRAMEWORK_CONFTEST_MARKER in conftest.read_text()
    conftest.write_text("# user version\n")
    workflow()._write_test_scaffold(tmp_path, ["src/a.py"])
    assert conftest.read_text() == "# user version\n"


def test_no_conftest_without_python_files(tmp_path):
    workflow()._write_test_scaffold(tmp_path, ["docs/README.md", "index.html"])
    assert not (tmp_path / "conftest.py").exists()


def test_the_generated_conftest_really_fixes_the_imports(tmp_path):
    """End to end: the exact layout of the failed run, with the framework scaffold, runs green under real pytest."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "temp_convert.py").write_text("def celsius_to_fahrenheit(c):\n    return c * 9 / 5 + 32\n")
    (tmp_path / "tests" / "test_temp_convert.py").write_text(
        "def test_a():\n    from temp_convert import celsius_to_fahrenheit\n    assert celsius_to_fahrenheit(100) == 212\n"
    )
    without = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=tmp_path, capture_output=True, text=True)
    assert without.returncode != 0 and "ModuleNotFoundError" in without.stdout   # documents the bug
    workflow()._write_test_scaffold(tmp_path, ["src/temp_convert.py"])
    with_scaffold = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=tmp_path, capture_output=True, text=True)
    assert with_scaffold.returncode == 0, with_scaffold.stdout
    assert "1 passed" in with_scaffold.stdout


# ---------------------------------------------------------------------------- file_system guard
def fs_execute(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, scope="private", **kwargs))


@pytest.mark.parametrize("extra", [{}, {"force_overwrite": True}])
def test_agents_cannot_overwrite_the_framework_conftest(tmp_path, extra):
    (tmp_path / "conftest.py").write_text(FRAMEWORK_CONFTEST)
    result = fs_execute(tmp_path, action="write", path="conftest.py", content="import sys\n", **extra)
    assert result["status"] == "error" and "managed by the framework" in result["message"]
    assert (tmp_path / "conftest.py").read_text() == FRAMEWORK_CONFTEST


def test_other_conftest_files_are_unaffected(tmp_path):
    (tmp_path / "tests").mkdir()
    assert fs_execute(tmp_path, action="write", path="tests/conftest.py", content="import sys\n")["status"] == "success"
    (tmp_path / "conftest.py").write_text("# someone else's\n")
    result = fs_execute(tmp_path, action="write", path="conftest.py", content="import os\n", force_overwrite=True)
    assert result["status"] == "success"


# ---------------------------------------------------------------------------- prompts
def test_prompts():
    yaml = pytest.importorskip("yaml")
    prompts = yaml.safe_load((ROOT / "prompts.yaml").read_text(encoding="utf-8"))
    blank = type("B", (dict,), {"__missing__": lambda self, k: ""})()
    for text in prompts.values():
        if isinstance(text, str):
            text.format_map(blank)
    audit = prompts["pm_audit_prompt"]
    assert "python -m pytest -q" in audit and "NEVER pipe the output" in audit and "never change project files yourself" in audit
    assert 'do NOT use the words "Project" or "Plan"' in prompts["admin_ai_planning_prompt"]
    work = prompts["worker_work_prompt"]
    assert "NEVER create or edit the root `conftest.py`" in work and "python -m pytest -q" in work
