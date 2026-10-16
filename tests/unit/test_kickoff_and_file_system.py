# START OF FILE tests/unit/test_kickoff_and_file_system.py
"""
Round 1c regression tests.

* PMKickoffWorkflow project_structure parsing: file names are never created as
  directories; every observed JSON shape is accepted; PROJECT_STRUCTURE.md renders
  dict/list code_base_definitions instead of crashing.
* FileSystemTool: 'write' replaces an EMPTY directory of the same name, refuses a
  non-empty one; a leading 'shared_workspace/' is stripped in shared scope.

Run from the repo root inside the venv:
    python -m pytest tests/unit/test_kickoff_and_file_system.py -q
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import settings  # noqa: E402
# Import order matters: src.workflows <-> src.agents import each other and only resolve
# when src.agents is loaded first, exactly as the application does at startup.
import src.agents  # noqa: E402,F401
from src.workflows.pm_kickoff_workflow import (  # noqa: E402
    PMKickoffWorkflow, looks_like_file, plan_project_structure, render_project_structure_md,
)
from src.tools.file_system import FileSystemTool  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# The exact project_structure the PM produced in the 2026-10-14 test session.
REAL_SESSION_STRUCTURE = [
    {"dir": ".", "children": []},
    {"dir": "src", "children": [{"dir": "__init__.py"}, {"file": "main.py"}]},
    {"dir": "tests", "files": ["test_main.py"]},
    {"dir": "docs", "files": ["README.md"]},
    {"dir": "research"},
    {"dir": "config"},
]
REAL_SESSION_DEFINITIONS = {
    "language": "Python 3.x",
    "coding_conventions": [{"type": "style_guide", "details": "PEP8 compliant code formatting"}],
    "project_constraints": ["Minimal scope - single useful command feature"],
}


# --- looks_like_file ---------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("__init__.py", True), ("main.py", True), ("README.md", True), ("src/app.test.js", True),
    ("Makefile", True), ("Dockerfile", True), (".gitignore", True), (".env", True),
    ("src", False), ("tests/", False), (".github", False), (".vscode", False), ("v1.2", False),
])
def test_looks_like_file(name, expected):
    assert looks_like_file(name) is expected


# --- plan_project_structure -------------------------------------------------------------------

def test_real_session_structure_records_init_py_as_file():
    dirs, files, notes = plan_project_structure(REAL_SESSION_STRUCTURE)
    assert "src/__init__.py" not in dirs
    assert "src/__init__.py" in files
    assert set(dirs) == {"src", "tests", "docs", "research", "config"}
    assert set(files) == {"src/__init__.py", "src/main.py", "tests/test_main.py", "docs/README.md"}
    assert any("looks like a file" in n for n in notes)


def test_documented_schema_shape():
    dirs, files, _ = plan_project_structure([
        {"dir": "src", "files": ["__init__.py", "main.py"]},
        {"dir": "tests", "files": ["test_main.py"]},
        {"dir": "docs"},
        {"file": "README.md"},
    ])
    assert dirs == ["src", "tests", "docs"]
    assert files == ["src/__init__.py", "src/main.py", "tests/test_main.py", "README.md"]


def test_nested_paths_strings_and_parent_dirs():
    dirs, files, _ = plan_project_structure([
        {"dir": "src/core", "children": ["utils/", "helpers.py", {"dir": "models", "files": [{"file": "user.py"}]}]},
        "scripts/run.sh",
        "assets",
    ])
    assert {"src/core", "src/core/utils", "src/core/models", "scripts", "assets"} <= set(dirs)
    assert {"src/core/helpers.py", "src/core/models/user.py", "scripts/run.sh"} == set(files)


def test_unsafe_entries_are_skipped():
    dirs, files, notes = plan_project_structure([
        {"dir": "../escape"}, {"dir": "/etc"}, {"file": "../../x.py"}, {"dir": "ok"},
        {"dir": 42}, None, 7,
    ])
    assert dirs == ["ok"] and files == []
    assert len([n for n in notes if "unsafe or invalid" in n]) == 4  # ../escape, /etc, ../../x.py, 42


def test_dir_named_like_file_with_children_stays_dir():
    dirs, files, _ = plan_project_structure([{"dir": "config.d", "files": ["a.conf"]}])
    assert dirs == ["config.d"] and files == ["config.d/a.conf"]


# --- PROJECT_STRUCTURE.md -----------------------------------------------------------------

def test_render_handles_dict_definitions_and_marks_planned_files():
    dirs, files, _ = plan_project_structure(REAL_SESSION_STRUCTURE)
    md = render_project_structure_md(dirs, files, REAL_SESSION_DEFINITIONS)
    assert "- **language**: Python 3.x" in md
    assert "type: style_guide; details: PEP8 compliant code formatting" in md
    assert "## Files To Create" in md and "(planned)" not in md   # Round 1v: no bracket notes in the layout
    assert any(line.startswith("- ") and line.endswith("__init__.py") for line in md.splitlines())
    assert "  src/" in md


def test_render_accepts_string_and_none_definitions():
    assert "Use pytest" in render_project_structure_md(["src"], [], "Use pytest\nPEP8")
    assert "Code Base Definitions" not in render_project_structure_md(["src"], [], None)


def test_workflow_helpers_create_only_directories(tmp_path):
    wf = PMKickoffWorkflow()
    ws = tmp_path / "shared_workspace"
    dirs, files, _ = plan_project_structure(REAL_SESSION_STRUCTURE)
    created = wf._create_directory_structure(dirs, ws)
    assert set(created) == set(dirs)
    assert (ws / "src").is_dir()
    assert not (ws / "src" / "__init__.py").exists()  # planned file: NOT created as a directory
    wf._write_project_structure_md(ws, created, REAL_SESSION_DEFINITIONS, files)
    assert (ws / "PROJECT_STRUCTURE.md").read_text().startswith("# Project Directory Structure")


def test_workflow_never_creates_dir_over_existing_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes").write_text("a file")
    created = PMKickoffWorkflow()._create_directory_structure(["notes", "src"], ws)
    assert created == ["src"] and (ws / "notes").is_file()


def test_workflow_continues_when_a_parent_is_a_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "lib").write_text("a file, not a dir")
    created = PMKickoffWorkflow()._create_directory_structure(["lib/sub", "docs"], ws)
    assert created == ["docs"] and (ws / "docs").is_dir()


# --- FileSystemTool --------------------------------------------------------------------------

@pytest.fixture
def fs_env(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    ws = projects / "Proj" / "sess" / "shared_workspace"
    sb = tmp_path / "sandbox"
    ws.mkdir(parents=True)
    sb.mkdir()
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", projects, raising=False)
    return {"ws": ws, "sb": sb}


def fs(env, **kwargs):
    return run(FileSystemTool().execute(agent_id="W1", agent_sandbox_path=env["sb"],
                                        project_name="Proj", session_name="sess", **kwargs))


def test_write_replaces_empty_directory(fs_env):
    (fs_env["ws"] / "src" / "__init__.py").mkdir(parents=True)
    r = fs(fs_env, action="write", path="src/__init__.py", content='"""pkg"""\n')
    assert r["status"] == "success", r
    assert "empty directory" in r["message"]
    assert (fs_env["ws"] / "src" / "__init__.py").is_file()


def test_write_refuses_non_empty_directory(fs_env):
    d = fs_env["ws"] / "pkg"
    d.mkdir()
    (d / "keep.txt").write_text("x")
    r = fs(fs_env, action="write", path="pkg", content="y")
    assert r["status"] == "error" and "not empty" in r["message"]
    assert (d / "keep.txt").read_text() == "x"


def test_write_existing_file_still_protected(fs_env):
    (fs_env["ws"] / "a.txt").write_text("old")
    r = fs(fs_env, action="write", path="a.txt", content="new")
    assert r["status"] == "error" and "already exists" in r["message"]
    assert (fs_env["ws"] / "a.txt").read_text() == "old"


def test_shared_workspace_prefix_is_stripped(fs_env):
    (fs_env["ws"] / "PROJECT_PLAN.md").write_text("# plan\n")
    r = fs(fs_env, action="read", path="shared_workspace/PROJECT_PLAN.md")
    assert r["status"] == "success" and "# plan" in r["content"]
    r = fs(fs_env, action="list", path="shared_workspace")
    assert r["status"] == "success" and "PROJECT_PLAN.md" in str(r)
# END OF FILE tests/unit/test_kickoff_and_file_system.py
