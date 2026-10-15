"""Round 1l tests: list_tasks must not silently hide ASSIGNED tasks from the Admin AI / Project Manager.

Observed 2026-10-15 15:02:47: the PM (pm_manage) called list_tasks with only project_filter, as its prompt tells it to,
and got "Found 0 task(s)" although 4 pending tasks were assigned to W1. The tool had added an invisible
assignee_filter="unassigned" for non-worker callers. A PM that believes there are 0 tasks may advance the project
phase early or declare it complete.
"""
import asyncio

import src.agents  # noqa: F401 - resolves pre-existing circular import order
import src.tools.project_management as pm


class FakeTask:
    def __init__(self, uuid, assignee=None, progress="todo", status="pending", tags=(), depends=()):
        self._d = {"uuid": uuid, "assignee": assignee, "task_progress": progress, "status": status,
                   "tags": list(tags), "depends": list(depends), "description": f"task {uuid}", "project": "P"}

    def __getitem__(self, key):
        return self._d[key]


class FakeQuery:
    def __init__(self, tasks):
        self._tasks = list(tasks)

    def filter(self, **kw):
        out = self._tasks
        for k, v in kw.items():
            out = [t for t in out if t[k] == v]
        return FakeQuery(out)

    def pending(self):
        return FakeQuery([t for t in self._tasks if t["status"] == "pending"])

    def completed(self):
        return FakeQuery([t for t in self._tasks if t["status"] == "completed"])

    def all(self):
        return FakeQuery(self._tasks)

    def __iter__(self):
        return iter(self._tasks)

    def __len__(self):
        return len(self._tasks)


class FakeTW:
    def __init__(self, tasks):
        self.tasks = FakeQuery(tasks)


def run(agent_id, tasks, **kwargs):
    tool = pm.ProjectManagementTool()
    return asyncio.run(tool._execute_list_tasks(FakeTW(tasks), agent_id, dict(kwargs)))


def sample():
    return [
        FakeTask("u1", assignee="W1", progress="in_progress"),
        FakeTask("u2", assignee="W1"),
        FakeTask("u3", assignee=None),
        FakeTask("u4", assignee="W2", status="completed", progress="finished"),
    ]


def uuids(result):
    return sorted(t["uuid"] for t in result["tasks"])


def test_pm_without_filters_sees_assigned_pending_tasks():
    r = run("PM1", sample(), project_filter="P")
    assert uuids(r) == ["u1", "u2", "u3"]


def test_admin_without_filters_sees_assigned_pending_tasks():
    r = run("admin_ai", sample(), project_filter="P")
    assert uuids(r) == ["u1", "u2", "u3"]


def test_explicit_unassigned_filter_still_works():
    r = run("PM1", sample(), project_filter="P", assignee_filter="unassigned")
    assert uuids(r) == ["u3"]


def test_explicit_assignee_filter_still_works():
    r = run("PM1", sample(), project_filter="P", assignee_filter="W1")
    assert uuids(r) == ["u1", "u2"]


def test_activate_workers_style_call_is_unchanged():
    r = run("PM1", sample(), project_filter="P", task_progress_filter="todo", tags_filter_mode="exclude", tags_filter="assigned")
    assert uuids(r) == ["u3"]


def test_worker_still_defaults_to_its_own_tasks():
    r = run("W1", sample(), project_filter="P")
    assert uuids(r) == ["u1", "u2"]


def test_completed_tasks_stay_hidden_by_default():
    r = run("PM1", sample(), project_filter="P")
    assert "u4" not in uuids(r)
    assert "1 completed" in r["message"]
