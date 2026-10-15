#!/usr/bin/env python3
"""
TrippleEffect: Round 1j Surgical Fix  (src/tools/project_management.py)

Based on the 2026-10-15 10:46 session (Word_Stats_CLI). Rounds 1g, 1h and 1i are assumed applied
(this patch only needs the original project_management.py structure, not 1i's helpers).

OBSERVED: at 10:59:20 the PM called modify_task with task_id 'c273b749-4992-40d2-9a19-b58a1dc327'. The real
UUID is 'c273b749-4992-40d2-9a19-b58a121dc327': the 4B model dropped characters from the MIDDLE of a UUID it
had copied. The tool only tried an exact match and a plain prefix match, so the call failed ("Task ... not
found"), the PM burned another ~1 minute LLM cycle on the retry, and the assignment was delayed.

 1. modify_task and complete_task: when the exact UUID is not found, the task is now also looked up by the
    FIRST 8 HEX CHARACTERS (the part models copy reliably). Only a UNIQUE match is accepted, so an ambiguous or
    unknown id still fails exactly as before. The substitution is logged at INFO.
    (Before, the prefix fallback only ran for ids shorter than 36 characters and used the full text as prefix.)
 2. tests/unit/test_round1j.py (new; the integration tests use a real Taskwarrior when installed).

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
    # ---- 1a. helper ------------------------------------------------------------------------
    {
        "file": PM,
        "find": r'''class ProjectManagementTool(BaseTool):
    name = "project_management"''',
        "replace": r'''def match_task_by_uuid_prefix(tasks, text: str) -> list:
    """
    Resolves a UUID the model has copied imperfectly. Small models often drop or alter characters in the MIDDLE
    of a UUID but copy the start correctly, so the lookup tries the text as a prefix and then its first 8 hex
    characters. Returns the matching tasks: one task = success, several = ambiguous (the caller refuses),
    none = not found.
    """
    needle_full = str(text or "").strip().lower()
    all_tasks = list(tasks)
    seen = []
    for needle in (needle_full, needle_full[:8]):
        if len(needle) < 8 or needle in seen or not re.match(r"^[0-9a-f-]+$", needle):
            continue
        seen.append(needle)
        matches = [t for t in all_tasks if str(t['uuid']).lower().startswith(needle)]
        if len(matches) == 1:
            if needle != needle_full:
                logger.info(f"ProjectManagementTool: Resolved imperfect task id '{text}' to UUID '{matches[0]['uuid']}' by its first 8 characters.")
            return matches
        if len(matches) > 1:
            return matches
    return []


class ProjectManagementTool(BaseTool):
    name = "project_management"''',
    },
    # ---- 1b. modify_task + complete_task (identical block in both) ---------------------------
    {
        "file": PM,
        "count": 2,
        "find": r'''                        if "exist" in str(get_err) and len(t_id_str) < 36:
                            matches = [t for t in tw.tasks.all() if t['uuid'].startswith(t_id_str)]''',
        "replace": r'''                        if "exist" in str(get_err):
                            matches = match_task_by_uuid_prefix(tw.tasks.all(), t_id_str)''',
    },
]

NEW_FILES = {
    "tests/unit/test_round1j.py": r'''"""Round 1j tests: imperfectly copied task UUIDs are resolved by their first 8 characters (unique match only).

Observed 2026-10-15 10:59: PM sent 'c273b749-4992-40d2-9a19-b58a1dc327' for 'c273b749-4992-40d2-9a19-b58a121dc327'.
"""
import asyncio
import shutil

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
    src = open(pm.__file__, encoding="utf-8").read()
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
    ap = argparse.ArgumentParser(description="Apply TrippleEffect Round 1j fixes.")
    ap.add_argument("--dry-run", action="store_true", help="Show diffs only; do not write any files.")
    args = ap.parse_args()
    if not (ROOT / "src").is_dir():
        print("ERROR: run this from the TrippleEffect repository root.", file=sys.stderr)
        return 1
    return apply_patches(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
