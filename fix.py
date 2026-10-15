#!/usr/bin/env python3
"""
TrippleEffect: Round 1l Surgical Fix  (src/tools/project_management.py)

Rounds 1g-1k are assumed applied (this patch needs the Round 1i/1j state of project_management.py).

OBSERVED (2026-10-15 15:02:47, Temp_Convert_CLI run): the PM, now in pm_manage, called list_tasks with only
project_filter, exactly as its prompt instructs ("list all active tasks"). The tool answered
"Found 0 task(s). (0 completed tasks hidden by default)" although 4 pending tasks were assigned to W1. Cause: for any
caller that is not a worker (Admin AI, PM) and passes no filter at all, list_tasks silently added
assignee_filter="unassigned". The prompts also tell the Admin AI to use list_tasks to VERIFY that every task is
finished before reporting completion, so it could be misled the same way. A PM or Admin that believes there are 0 tasks
may advance the project phase early or declare the project complete.

 1. list_tasks: no implicit "unassigned" filter. With no filter the Admin AI and the PM now see all pending tasks.
    Unchanged: explicit assignee_filter / task_progress_filter / tags_filter, completed tasks hidden by default,
    decomposed parents hidden by default, workers defaulting to their own tasks, and the pm_activate_workers call
    (it passes explicit filters, and the cycle handler already keeps only unassigned tasks from the result).
 2. tests/unit/test_round1l.py (new, 7 tests, no Taskwarrior needed). Two of them fail without this fix.

Usage:
    python3 fix.py --dry-run
    python3 fix.py

All-or-nothing: if ANY find-string is missing (or matches the wrong number of times) nothing is written.
"""
import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path.cwd()
PM = "src/tools/project_management.py"

PATCHES = [
    {
        "file": PM,
        "find": r'''            has_filters = any([
                kwargs.get("task_progress_filter"),
                kwargs.get("status_filter"),
                kwargs.get("assignee_filter"),
                kwargs.get("tags_filter")
            ])
            if not has_filters and agent_id and not self._is_worker_agent(agent_id):
                # PM must always specify filtering. List yet unassigned tasks by default when no filter is provided.
                kwargs["assignee_filter"] = "unassigned"
''',
        "replace": r'''            # Round 1l: callers that pass no filter get ALL pending tasks. (Before, the Admin AI and the PM silently
            # got only UNASSIGNED tasks, so "Found 0 task(s)" hid every task that was already assigned to a worker.)
            # Workers still default to their own tasks below; explicit filters behave exactly as before.
''',
    },
]

NEW_FILES = {
    "tests/unit/test_round1l.py": r'''"""Round 1l tests: list_tasks must not silently hide ASSIGNED tasks from the Admin AI / Project Manager.

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
''',
}

def apply_patches(dry_run: bool) -> int:
    originals = {}
    updated = {}
    errors = []

    for idx, p in enumerate(PATCHES, 1):
        rel = p["file"]
        path = ROOT / rel
        expected = p.get("count", 1)
        if rel not in updated:
            if not path.is_file():
                errors.append(f"[patch {idx}] File not found: {rel}")
                continue
            originals[rel] = path.read_text(encoding="utf-8")
            updated[rel] = originals[rel]
        count = updated[rel].count(p["find"])
        if count == 0:
            errors.append(f"[patch {idx}] NO MATCH in {rel} for find-string starting: {p['find'][:80]!r}")
        elif count != expected:
            errors.append(f"[patch {idx}] {count} MATCHES (expected exactly {expected}) in {rel}: {p['find'][:80]!r}")
        else:
            updated[rel] = updated[rel].replace(p["find"], p["replace"])

    for rel in NEW_FILES:
        if (ROOT / rel).exists():
            errors.append(f"New file already exists, refusing to overwrite: {rel}")

    if errors:
        print("ERROR: patch aborted, NOTHING was written:\n", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    for rel, new_text in updated.items():
        if new_text == originals[rel]:
            continue
        if dry_run:
            diff = difflib.unified_diff(
                originals[rel].splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2,
            )
            sys.stdout.writelines(diff)
            print()
        else:
            (ROOT / rel).write_text(new_text, encoding="utf-8")
            print(f"PATCHED  {rel}")

    for rel, content in NEW_FILES.items():
        if dry_run:
            print(f"[dry-run] would create {rel} ({len(content.splitlines())} lines)")
        else:
            target = ROOT / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"CREATED  {rel}")

    print("\nDry run complete: no files changed." if dry_run else "\nAll patches applied successfully.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply TrippleEffect Round 1l fixes.")
    ap.add_argument("--dry-run", action="store_true", help="Show diffs only; do not write any files.")
    args = ap.parse_args()
    if not (ROOT / "src").is_dir():
        print("ERROR: run this from the TrippleEffect repository root.", file=sys.stderr)
        return 1
    return apply_patches(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
