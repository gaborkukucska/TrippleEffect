"""Round 1i tests: Taskwarrior data-file hygiene in ProjectManagementTool.

Background: assignee_agent_id="\\nW1\\n" was stored as a tag; Taskwarrior writes tags as attribute KEYS without
escaping, which split a record over several lines and broke every later `task export`.
Integration tests use a real Taskwarrior (skipped when `task` or tasklib is not installed).
"""
import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
import src.tools.project_management as pm

try:
    import tasklib  # noqa: F401
    HAVE_TW = shutil.which("task") is not None
except ImportError:  # pragma: no cover
    HAVE_TW = False

needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


GOOD = ('[assignee:"W2" description:"Task two" entry:"1792029457" modified:"1792029458" project:"P" status:"completed" '
        'tags:"W2,assigned" tags_W2:"x" tags_assigned:"x" task_progress:"finished" uuid:"u-2"]')
BROKEN = [
    '[assignee:"\\nW1\\n" description:"Task three" entry:"1792029457" modified:"1792029458" project:"P" status:"completed" '
    'tags:"\\nW1\\n,W1,assigned" tags_',
    'W1',
    ':"x" tags_W1:"x" tags_assigned:"x" task_progress:"finished" uuid:"u-3"]',
]


# ---------------------------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("W1", "W1"), ("\nW1\n", "W1"), ("  'W2' ", "W2"), ('"PM1"', "PM1"), ("Coder_1.a:b-c", "Coder_1.a:b-c"),
    ("W 1", None), ("W1\nW2", None), ("", None), (None, None), ("a,b", None), ("W1]", None),
])
def test_clean_agent_id(raw, expected):
    assert pm.clean_agent_id(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("W1", "W1"), ("\nW1\n", "W1"), ("a b", "a_b"), ('"x"', "x"), ("[t]", "t"), ("a,b", "ab"),
    ("task_order_3", "task_order_3"), ("parent:abc-123", "parent:abc-123"),
])
def test_clean_tag(raw, expected):
    assert pm.clean_tag(raw) == expected


def test_repair_leaves_good_records_alone_and_is_idempotent():
    assert pm.repair_taskwarrior_records([GOOD]) == [GOOD]
    fixed = pm.repair_taskwarrior_records([GOOD] + BROKEN)
    assert pm.repair_taskwarrior_records(fixed) == fixed


def test_repair_rejoins_split_record_and_cleans_tags():
    fixed = pm.repair_taskwarrior_records([GOOD] + BROKEN)
    assert len(fixed) == 2
    assert all(r.startswith("[") and r.endswith("]") for r in fixed)
    rec = fixed[1]
    assert 'tags:"W1,assigned"' in rec
    assert "\\n" not in rec.split('tags:"', 1)[1].split('"', 1)[0]
    assert rec.count('tags_W1:"x"') == 1          # duplicate flag created by re-joining is dropped
    assert rec.count('tags_assigned:"x"') == 1
    assert 'assignee:"W1"' in rec
    assert 'uuid:"u-3"' in rec


def test_repair_drops_empty_lines_and_keeps_unrecoverable_fragment():
    out = pm.repair_taskwarrior_records(["", GOOD, "   ", '[description:"half" tags_', GOOD])
    assert out[0] == GOOD and out[-1] == GOOD and len(out) == 3
    assert out[1].startswith('[description:"half"')


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
    return run(tool.execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P", session_name="S", **kw))


def data_dir(tmp_path):
    return tmp_path / "projects" / "P" / "S" / "task_data"


def assert_files_well_formed(tmp_path):
    for fname in ("pending.data", "completed.data"):
        p = data_dir(tmp_path) / fname
        if p.exists():
            for line in p.read_text().splitlines():
                assert line.startswith("[") and line.endswith("]"), f"{fname} has a malformed line: {line[:80]!r}"


def tags_of(tool, uuid):
    tw = tool._get_taskwarrior_instance("P", "S")
    return set(tw.tasks.get(uuid=uuid)["tags"] or [])


@needs_tw
def test_modify_task_with_newline_wrapped_assignee_does_not_corrupt_database(tool, tmp_path):
    r = call(tool, tmp_path, "PM1", action="add_task", description="Kick-off Task 3: define structure")
    assert r["status"] == "success"
    uid = r["task_uuid"]
    r = call(tool, tmp_path, "PM1", action="modify_task", task_id=uid, assignee_agent_id="\nW1\n")
    assert r["status"] == "success" and r["assignee"] == "W1"
    r = call(tool, tmp_path, "W1", action="modify_task", task_id=uid, task_progress="finished")
    assert r["status"] == "success"
    assert_files_well_formed(tmp_path)
    r = call(tool, tmp_path, "PM1", action="list_tasks", task_progress_filter="finished")
    assert r["status"] == "success"
    assert [t["assignee"] for t in r["tasks"]] == ["W1"]
    assert tags_of(tool, uid) == {"W1", "assigned"}


@needs_tw
def test_add_task_cleans_assignee_and_tags(tool, tmp_path):
    r = call(tool, tmp_path, "PM1", action="add_task", description="Build the thing properly",
             assignee_agent_id="\nW1\n", tags=["\nW1\n", "needs review", "[x]"])
    assert r["status"] == "success" and r["assignee"] == "W1"
    assert_files_well_formed(tmp_path)
    assert tags_of(tool, r["task_uuid"]) == {"W1", "assigned", "needs_review", "x"}


@needs_tw
def test_invalid_assignee_is_rejected_and_nothing_is_saved(tool, tmp_path):
    r = call(tool, tmp_path, "PM1", action="add_task", description="Build the thing properly", assignee_agent_id="W 1")
    assert r["status"] == "error" and "assignee_agent_id" in r["message"]
    r = call(tool, tmp_path, "PM1", action="add_task", description="Another task entirely")
    assert r["status"] == "success"
    uid = r["task_uuid"]
    r = call(tool, tmp_path, "PM1", action="modify_task", task_id=uid, assignee_agent_id="W1\nW2")
    assert r["status"] == "error" and "assignee_agent_id" in r["message"]
    assert_files_well_formed(tmp_path)
    assert tags_of(tool, uid) == set()


@needs_tw
def test_add_task_with_assignee_gets_assigned_tags(tool, tmp_path):
    r = call(tool, tmp_path, "PM1", action="add_task", description="Implement the parser", assignee_agent_id="W1")
    assert tags_of(tool, r["task_uuid"]) == {"W1", "assigned"}


@needs_tw
def test_decompose_subtask_keeps_its_tags_and_gets_parent_tag(tool, tmp_path):
    parent = call(tool, tmp_path, "PM1", action="add_task", description="Parent task for decomposition",
                  assignee_agent_id="W1")["task_uuid"]
    manager = SimpleNamespace(agents={"W1": SimpleNamespace(agent_type="worker", state="worker_decompose",
                                                            current_task_id=parent)})
    r = call(tool, tmp_path, "W1", action="add_task", description="Sub task number one here", tags=["x"],
             manager=manager)
    assert r["status"] == "success"
    assert tags_of(tool, r["task_uuid"]) == {"x", "assigned", "W1", f"parent:{parent}"}
    assert_files_well_formed(tmp_path)


@needs_tw
def test_damaged_files_are_healed_before_the_next_call(tool, tmp_path):
    # create the database, then write the exact damage seen in the 10:22 run
    call(tool, tmp_path, "PM1", action="add_task", description="Seed task so the files exist")
    d = data_dir(tmp_path)
    (d / "completed.data").write_text("\n".join([GOOD.replace('uuid:"u-2"', 'uuid:"00000000-0000-0000-0000-000000000002"')]
                                                 + BROKEN[:2]
                                                 + [BROKEN[2].replace('u-3', '00000000-0000-0000-0000-000000000003')]) + "\n")
    r = call(tool, tmp_path, "PM1", action="list_tasks", task_progress_filter="finished")
    assert r["status"] == "success", r
    assert {t["assignee"] for t in r["tasks"]} == {"W1", "W2"}
    assert_files_well_formed(tmp_path)
    # a later save must not re-introduce the damage
    r = call(tool, tmp_path, "PM1", action="add_task", description="Another task after healing")
    assert r["status"] == "success"
    assert_files_well_formed(tmp_path)
    assert call(tool, tmp_path, "PM1", action="list_tasks", assignee_filter="unassigned")["status"] == "success"


@needs_tw
def test_unreadable_file_returns_clear_data_corruption_error(tool, tmp_path, monkeypatch):
    call(tool, tmp_path, "PM1", action="add_task", description="Seed task so the files exist")
    monkeypatch.setattr(pm.ProjectManagementTool, "_heal_taskwarrior_data", lambda self, p: None)
    (data_dir(tmp_path) / "completed.data").write_text("\n".join(BROKEN) + "\n")
    r = call(tool, tmp_path, "PM1", action="list_tasks")
    assert r["status"] == "error" and r.get("error_type") == "data_corruption"


def test_no_task_in_membership_left_in_assignee_and_tag_code():
    src = Path(pm.__file__).read_text(encoding="utf-8")  # Round 2m: closes the file (ResourceWarning)
    assert "'assignee' in task" not in src
    assert "'tags' in task" not in src
