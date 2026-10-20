"""Round 1j tests: imperfectly copied task UUIDs are resolved by their first 8 characters (unique match only).

Observed 2026-10-15 10:59: PM sent 'c273b749-4992-40d2-9a19-b58a1dc327' for 'c273b749-4992-40d2-9a19-b58a121dc327'.
"""
import asyncio
import shutil
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
import src.tools.project_management as pm

try:
    import tasklib  # noqa: F401
    HAVE_TW = shutil.which("task") is not None
except ImportError:  # pragma: no cover
    HAVE_TW = False

needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

REAL = "c273b749-4992-40d2-9a19-b58a121dc327"
MANGLED = "c273b749-4992-40d2-9a19-b58a1dc327"      # characters dropped from the middle (the 10:59 case)


def fake(*uuids):
    return [{"uuid": u} for u in uuids]


def test_unique_first_8_chars_resolves_a_mangled_uuid():
    tasks = fake(REAL, "0eb26480-f9ea-4263-9c41-38401426dee9", "e8c074f3-6af4-478d-b062-16c424cf4fd5")
    assert pm.match_task_by_uuid_prefix(tasks, MANGLED) == [tasks[0]]


def test_wrong_character_in_a_full_length_uuid_still_resolves():
    tasks = fake(REAL, "0eb26480-f9ea-4263-9c41-38401426dee9")
    assert pm.match_task_by_uuid_prefix(tasks, "c273b749-4992-40d2-9a19-b58a121dc328") == [tasks[0]]


def test_plain_prefix_still_works():
    tasks = fake(REAL, "0eb26480-f9ea-4263-9c41-38401426dee9")
    assert pm.match_task_by_uuid_prefix(tasks, "c273b749-4992") == [tasks[0]]


def test_ambiguous_prefix_returns_all_matches_so_the_caller_refuses():
    tasks = fake("aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-1111-1111-1111-000000000002")
    assert len(pm.match_task_by_uuid_prefix(tasks, "aaaaaaaa-9999")) == 2


@pytest.mark.parametrize("text", ["", "task_3", "c273", "zzzzzzzz-4992", "ffffffff-4992-40d2-9a19-b58a121dc327", None])
def test_unknown_or_too_short_ids_do_not_match(text):
    assert pm.match_task_by_uuid_prefix(fake(REAL), text) == []


def test_both_lookup_blocks_use_the_helper():
    src = Path(pm.__file__).read_text(encoding="utf-8")  # Round 2m: closes the file (ResourceWarning)
    assert src.count("match_task_by_uuid_prefix(tw.tasks.all(), t_id_str)") == 2
    assert "startswith(t_id_str)" not in src


# ---------------------------------------------------------------------------------------------
# integration with a real Taskwarrior
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)
    return pm.ProjectManagementTool()


def call(tool, tmp_path, agent, **kw):
    return asyncio.run(tool.execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P", session_name="S", **kw))


def mangle(uuid):
    return uuid[:24] + uuid[26:]      # drops two characters from the middle, keeps the first 8


@needs_tw
def test_modify_task_accepts_a_mangled_uuid(tool, tmp_path):
    other = call(tool, tmp_path, "PM1", action="add_task", description="Another unrelated task here")["task_uuid"]
    uid = call(tool, tmp_path, "PM1", action="add_task", description="Kick-off Task 3: initialise package files")["task_uuid"]
    r = call(tool, tmp_path, "PM1", action="modify_task", task_id=mangle(uid), assignee_agent_id="W1")
    assert r["status"] == "success", r
    assert r["task_uuid"] == uid and r["assignee"] == "W1"
    assert call(tool, tmp_path, "PM1", action="list_tasks", assignee_filter="W1")["tasks"][0]["uuid"] == uid
    assert other != uid


@needs_tw
def test_complete_task_accepts_a_mangled_uuid(tool, tmp_path):
    uid = call(tool, tmp_path, "PM1", action="add_task", description="Kick-off Task 4: implement the core logic")["task_uuid"]
    r = call(tool, tmp_path, "PM1", action="complete_task", task_id=mangle(uid))
    assert r["status"] == "success", r
    assert r["task_uuid"] == uid


@needs_tw
def test_unknown_uuid_still_fails_cleanly(tool, tmp_path):
    call(tool, tmp_path, "PM1", action="add_task", description="Only task in the database")
    r = call(tool, tmp_path, "PM1", action="modify_task", task_id="ffffffff-0000-0000-0000-000000000000", task_progress="finished")
    assert r["status"] == "error" and "not found" in r["message"]
