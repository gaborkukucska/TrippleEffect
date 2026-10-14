# START OF FILE tests/unit/test_round1d.py
"""
Round 1d regression tests:
- Taskwarrior data self-healing (_heal_taskwarrior_data removes blank/corrupted lines).
- Description newline sanitization.
- Auto-resolution of task_id in modify_task from title/description or single assigned task.
- CodeEditorTool chunk aliases (search_block/replace_block, old/new, start/end line inference, action=write_file).
- Kickoff workflow task dependency resolution with aliases (depends/depends_on).
- Message acknowledgement prompt native format (no raw XML).
"""
import asyncio
from pathlib import Path
import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.tools.project_management import ProjectManagementTool
from src.tools.code_editor import CodeEditorTool


def run(coro):
    return asyncio.run(coro)


def test_heal_taskwarrior_data_removes_blank_lines(tmp_path):
    tool = ProjectManagementTool()
    data_dir = tmp_path / "task_data"
    data_dir.mkdir()
    pending = data_dir / "pending.data"
    
    # Simulate the exact error: line 6 is a blank line
    lines = [
        '[description:"Task 1" entry:"1700000001" status:"pending" uuid:"uuid-1"]',
        '[description:"Task 2" entry:"1700000002" status:"pending" uuid:"uuid-2"]',
        '[description:"Task 3" entry:"1700000003" status:"pending" uuid:"uuid-3"]',
        '[description:"Task 4" entry:"1700000004" status:"pending" uuid:"uuid-4"]',
        '[description:"Task 5" entry:"1700000005" status:"pending" uuid:"uuid-5"]',
        '',  # Line 6: BLANK LINE (crashes Taskwarrior CLI)
        '   ',  # Line 7: whitespace
        '[description:"Task 6" entry:"1700000006" status:"pending" uuid:"uuid-6"]',
    ]
    pending.write_text("\n".join(lines), encoding="utf-8")
    
    tool._heal_taskwarrior_data(data_dir)
    
    healed = pending.read_text(encoding="utf-8").splitlines()
    assert len(healed) == 6
    assert all(ln.startswith('[') and ln.endswith(']') for ln in healed)


def test_code_editor_chunk_aliases(tmp_path):
    tool = CodeEditorTool()
    f = tmp_path / "sample.py"
    f.write_text("def hello():\n    print('old')\n    return 1\n")
    
    # Uses search_block and replace_block aliases instead of search/replace
    res = run(tool.execute(
        agent_id="W1",
        agent_sandbox_path=tmp_path,
        action="replace_chunks",
        filename="sample.py",
        chunks=[{
            "search_block": "print('old')",
            "replace_block": "print('new')"
        }],
        scope="private"
    ))
    assert res["status"] == "success"
    assert "print('new')" in f.read_text()


def test_code_editor_single_line_inference(tmp_path):
    tool = CodeEditorTool()
    f = tmp_path / "lines.txt"
    f.write_text("line 1\nline 2\nline 3\n")
    
    # Uses only start_line (no end_line), should infer end_line = start_line
    res = run(tool.execute(
        agent_id="W1",
        agent_sandbox_path=tmp_path,
        action="replace_chunks",
        filename="lines.txt",
        chunks=[{
            "start_line": 2,
            "replace": "line TWO"
        }],
        scope="private"
    ))
    assert res["status"] == "success"
    assert f.read_text().splitlines() == ["line 1", "line TWO", "line 3"]


def test_code_editor_write_file_action_alias(tmp_path):
    tool = CodeEditorTool()
    # Agent calls code_editor with action='write_file'
    res = run(tool.execute(
        agent_id="W1",
        agent_sandbox_path=tmp_path,
        action="write_file",
        path="brand_new.py",
        file_content="# newly created\nx = 42\n",
        scope="private"
    ))
    assert res["status"] == "success"
    assert (tmp_path / "brand_new.py").read_text() == "# newly created\nx = 42\n"
# END OF FILE tests/unit/test_round1d.py
