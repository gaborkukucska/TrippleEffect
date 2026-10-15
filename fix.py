#!/usr/bin/env python3
"""
TrippleEffect: Round 1i Surgical Fix  (src/tools/project_management.py)

Based on the 2026-10-15 09:52 session (Word_Stats_CLI). Rounds 1g and 1h are assumed applied.

OBSERVED: at 10:22 worker W1 called modify_task with assignee_agent_id="\nW1\n". The value was stored
as-is and also added as a TAG. Taskwarrior writes each tag as an attribute key (tags_<tag>:"x") without
escaping, so the record in completed.data was split over three lines. From then on every
`task ... export` failed with "Unrecognized Taskwarrior file format or blank line in data ... line 3",
so list_tasks (and with it the PM and every worker) was blind for the rest of the run.
(Reproduced here against a real Taskwarrior 2.6.2 with the original tool.)

 1. Input hygiene (add_task, modify_task):
      * assignee_agent_id is cleaned (whitespace/quotes stripped) and must look like an agent id
        (letters, digits, _ . : -). Otherwise the call is rejected with a clear message and nothing is saved.
      * every tag (user supplied, the auto "assigned" tag and the assignee tag) goes through clean_tag():
        no quotes/brackets/commas, whitespace becomes "_".
      * modify_task descriptions get the same newline flattening add_task already had.
 2. Self-healing: _heal_taskwarrior_data (runs before every tool call) now also
      * re-joins records that a line break split in two,
      * removes line-break escapes from tag and assignee values and drops duplicate tag flags
        (a task whose assignee was stored as "\\nW1\\n" would otherwise never match assignee=W1).
    pending.data / completed.data only; undo.data keeps the old behaviour. Well-formed files are untouched.
    Tested on your real corrupted completed.data: export works again and a later save does not re-corrupt it.
 3. list_tasks: the query was lazy, so the existing "data corruption" handler never ran (the error surfaced
    later, in the loop). It is now evaluated inside the try, and a damaged data file returns a clear
    data_corruption error instead of a raw traceback.
 4. tasklib Task has no `in` support ('field' in task is ALWAYS False). Two places in add_task relied on it:
      * the "+assigned / +<agent>" tags were never added when an assignee was given at creation;
      * for sub-tasks created while decomposing, the parent tag REPLACED the sub-task's tags.
    Both now use get_task_field(). NOTE: this switches the first behaviour ON (it is the documented design).
    The duplicate-task check in add_task has the same `in` problem and is therefore dormant; it is
    deliberately NOT touched here (enabling it could treat a sub-task as a duplicate of its own parent).
 5. tests/unit/test_round1i.py (new; uses a real Taskwarrior when installed, otherwise those tests skip).

Usage:
    python3 fix.py --dry-run
    python3 fix.py

All-or-nothing: if ANY find-string is missing (or matches more than once) nothing is written.
"""
import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path.cwd()
PM = "src/tools/project_management.py"

HELPERS = r'''from typing import List
from src.utils.task_utils import get_task_field

logger = logging.getLogger(__name__)


# --- Input hygiene for values that end up inside Taskwarrior's line-oriented data files -------------
# Taskwarrior stores one task per line and writes each tag as an attribute KEY (tags_<tag>:"x") without
# escaping. A tag that contains a line break (e.g. the model passing assignee_agent_id="\nW1\n") therefore
# splits a record across lines and every later `task export` fails with "Unrecognized Taskwarrior file
# format ... at line N" (observed 2026-10-15 10:22). These helpers keep such values out of the database
# and repair files that were already damaged.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")
_WS_RE = re.compile(r"\s+")
_LITERAL_ESCAPE_RE = re.compile(r"\\[nrt]")
_TAGS_ATTR_RE = re.compile(r'(?:(?<=\s)|(?<=\[))tags:"([^"]*)"')
_ASSIGNEE_ATTR_RE = re.compile(r'(?:(?<=\s)|(?<=\[))assignee:"([^"]*)"')
_TAG_FLAG_RE = re.compile(r'(\s)(tags_[^\s:"]+:"x")')


def clean_agent_id(value: Any) -> Optional[str]:
    """Returns the agent id without surrounding whitespace/quotes, or None if it is not a plausible id."""
    if value is None:
        return None
    text = str(value).strip().strip("'\"").strip()
    return text if _AGENT_ID_RE.match(text) else None


def clean_tag(value: Any) -> str:
    """Makes a value safe to use as a Taskwarrior tag: no quotes/brackets/commas, whitespace becomes '_'."""
    text = str(value).replace('"', "").replace("'", "").replace("[", "").replace("]", "").replace(",", "")
    return _WS_RE.sub("_", text.strip())


def _clean_tags_attr(match: "re.Match") -> str:
    parts: List[str] = []
    for element in match.group(1).split(","):
        element = _WS_RE.sub("_", _LITERAL_ESCAPE_RE.sub("", element).strip())
        if element and element not in parts:
            parts.append(element)
    return 'tags:"' + ",".join(parts) + '"'


def _clean_assignee_attr(match: "re.Match") -> str:
    cleaned = _LITERAL_ESCAPE_RE.sub("", match.group(1)).strip()
    return 'assignee:"' + (cleaned or match.group(1)) + '"'


def _clean_record(record: str) -> str:
    record = _TAGS_ATTR_RE.sub(_clean_tags_attr, record)
    record = _ASSIGNEE_ATTR_RE.sub(_clean_assignee_attr, record)
    seen: set = set()

    def _drop_duplicate_flag(m: "re.Match") -> str:
        if m.group(2) in seen:
            return ""
        seen.add(m.group(2))
        return m.group(1) + m.group(2)

    return _TAG_FLAG_RE.sub(_drop_duplicate_flag, record)


def repair_taskwarrior_records(lines: List[str]) -> List[str]:
    """
    Best-effort repair of Taskwarrior pending.data / completed.data lines:
      * drops empty lines and surrounding whitespace;
      * re-joins a record that a stray line break split over several lines (a record is "[...]");
      * removes line-break escapes/whitespace from tag values and duplicate tag flags.
    Well-formed records are returned unchanged.
    """
    records: List[str] = []
    partial: Optional[str] = None
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if partial is not None:
            if s.startswith("["):
                records.append(partial)  # unterminated fragment that cannot be completed: keep as is
                partial = None
            else:
                partial += s
                if s.endswith("]"):
                    records.append(partial)
                    partial = None
                continue
        if s.startswith("[") and not s.endswith("]"):
            partial = s
            continue
        records.append(s)
    if partial is not None:
        records.append(partial)
    return [_clean_record(r) if r.startswith("[") else r for r in records]
'''

PATCHES = [
    # ---- 1. helpers ----------------------------------------------------------------------
    {
        "file": PM,
        "find": r'''from typing import List

logger = logging.getLogger(__name__)
''',
        "replace": HELPERS,
    },
    # ---- 2. self-healing -----------------------------------------------------------------
    {
        "file": PM,
        "find": r'''                lines = raw_text.splitlines()
                cleaned = [ln.strip() for ln in lines if ln.strip()]
                if len(cleaned) != len(lines) or any(ln != orig for ln, orig in zip(cleaned, lines)):
                    fpath.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")
                    logger.warning(f"ProjectManagementTool: Auto-healed {fname} by removing {len(lines) - len(cleaned)} empty/whitespace line(s).")
''',
        "replace": r'''                lines = raw_text.splitlines()
                if fname == "undo.data":
                    # undo.data is a multi-line journal: only strip empty/whitespace lines (original behaviour).
                    cleaned = [ln.strip() for ln in lines if ln.strip()]
                else:
                    # pending/completed: also re-join records split by a stray line break and clean tag values.
                    cleaned = repair_taskwarrior_records(lines)
                if len(cleaned) != len(lines) or any(ln != orig for ln, orig in zip(cleaned, lines)):
                    fpath.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")
                    logger.warning(f"ProjectManagementTool: Auto-healed {fname} ({len(lines)} -> {len(cleaned)} line(s): removed empty lines, re-joined split records, cleaned tag values).")
''',
    },
    # ---- 3a. add_task: assignee -----------------------------------------------------------
    {
        "file": PM,
        "find": r'''                task['assignee'] = kwargs["assignee_agent_id"]
            elif self._is_worker_agent(agent_id):''',
        "replace": r'''                clean_assignee = clean_agent_id(kwargs["assignee_agent_id"])
                if not clean_assignee:
                    return {"status": "error", "message": f"Invalid assignee_agent_id {kwargs['assignee_agent_id']!r}. Use the plain agent id such as 'W1' (letters, digits, '_', '.', ':' or '-'; no spaces or line breaks)."}
                kwargs["assignee_agent_id"] = clean_assignee
                task['assignee'] = clean_assignee
            elif self._is_worker_agent(agent_id):''',
    },
    # ---- 3b. add_task: tags ---------------------------------------------------------------
    {
        "file": PM,
        "find": r'''                    t = t.lstrip('+-').strip()
                    t = t.replace('"', '').replace("'", "").replace("[", "").replace("]", "").strip()
                    if t:
                        sanitized.append(t)''',
        "replace": r'''                    t = clean_tag(t.lstrip('+-'))
                    if t:
                        sanitized.append(t)''',
    },
    # ---- 4a. add_task: assigned/assignee tags (tasklib Task has no `in`) ---------------------
    {
        "file": PM,
        "find": r'''            if 'assignee' in task and task['assignee']:
                current_tags = task['tags'] if 'tags' in task and task['tags'] else set()
                current_tags.add("assigned")
                current_tags.add(task['assignee'])
                task['tags'] = current_tags''',
        "replace": r'''            if get_task_field(task, 'assignee'):
                current_tags = set(get_task_field(task, 'tags') or [])
                current_tags.add("assigned")
                current_tags.add(clean_tag(task['assignee']))
                task['tags'] = current_tags''',
    },
    # ---- 4b. add_task: decompose parent tag must not replace the sub-task's tags ------------
    {
        "file": PM,
        "find": r'''                        existing_sub_tags = list(task['tags']) if 'tags' in task and task['tags'] else []''',
        "replace": r'''                        existing_sub_tags = list(get_task_field(task, 'tags') or [])''',
    },
    # ---- 5. list_tasks: evaluate the lazy query inside the try ------------------------------
    {
        "file": PM,
        "find": r'''            try:
                tasks = tasks_query.all()
            except Exception as tw_err:
                err_str = str(tw_err)''',
        "replace": r'''            try:
                tasks = list(tasks_query)  # evaluate now: a lazy query would only fail later, outside this handler
            except Exception as tw_err:
                err_str = str(tw_err)''',
    },
    {
        "file": PM,
        "find": r'''                raise  # re-raise non-JSON errors''',
        "replace": r'''                if "Unrecognized Taskwarrior file format" in err_str:
                    logger.error(f"ProjectManagementTool: TaskWarrior data file is damaged: {err_str[-300:]}")
                    return {
                        "status": "error",
                        "message": "The task database file is damaged and could not be read. The framework repairs such files automatically before each call; if this message repeats, tell the user.",
                        "error_type": "data_corruption"
                    }
                raise  # re-raise non-JSON errors''',
    },
    # ---- 6a. modify_task: description newlines ----------------------------------------------
    {
        "file": PM,
        "find": r'''                task['description'] = kwargs["description"]; modified_fields.append("description")''',
        "replace": r'''                task['description'] = " ".join(str(kwargs["description"]).splitlines()).strip(); modified_fields.append("description")''',
    },
    # ---- 6b. modify_task: tags --------------------------------------------------------------
    {
        "file": PM,
        "find": r'''                    t_clean = t.lstrip('+-').strip()
                    t_clean = t_clean.replace('"', '').replace("'", "").replace("[", "").replace("]", "").strip()''',
        "replace": r'''                    t_clean = clean_tag(t.lstrip('+-'))''',
    },
    # ---- 6c. modify_task: assignee ----------------------------------------------------------
    {
        "file": PM,
        "find": r'''                new_assignee = assignee_arg
                if task['assignee'] == new_assignee:''',
        "replace": r'''                new_assignee = clean_agent_id(assignee_arg)
                if not new_assignee:
                    return {"status": "error", "message": f"Invalid assignee_agent_id {assignee_arg!r}. Use the plain agent id such as 'W1' (letters, digits, '_', '.', ':' or '-'; no spaces or line breaks)."}
                if task['assignee'] == new_assignee:''',
    },
    # ---- 6d. modify_task: report the cleaned assignee ---------------------------------------
    {
        "file": PM,
        "find": r'''            assignee_to_return = kwargs.get("assignee_agent_id") or task['assignee']''',
        "replace": r'''            assignee_to_return = task['assignee'] or kwargs.get("assignee_agent_id")''',
    },
]

NEW_FILES = {
    "tests/unit/test_round1i.py": r'''"""Round 1i tests: Taskwarrior data-file hygiene in ProjectManagementTool.

Background: assignee_agent_id="\\nW1\\n" was stored as a tag; Taskwarrior writes tags as attribute KEYS without
escaping, which split a record over several lines and broke every later `task export`.
Integration tests use a real Taskwarrior (skipped when `task` or tasklib is not installed).
"""
import asyncio
import shutil
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
    src = open(pm.__file__, encoding="utf-8").read()
    assert "'assignee' in task" not in src
    assert "'tags' in task" not in src
''',
}


def apply_patches(dry_run: bool) -> int:
    originals = {}
    updated = {}
    errors = []

    for idx, p in enumerate(PATCHES, 1):
        rel = p["file"]
        path = ROOT / rel
        if rel not in updated:
            if not path.is_file():
                errors.append(f"[patch {idx}] File not found: {rel}")
                continue
            originals[rel] = path.read_text(encoding="utf-8")
            updated[rel] = originals[rel]
        count = updated[rel].count(p["find"])
        if count == 0:
            errors.append(f"[patch {idx}] NO MATCH in {rel} for find-string starting: {p['find'][:80]!r}")
        elif count > 1:
            errors.append(f"[patch {idx}] {count} MATCHES (expected exactly 1) in {rel}: {p['find'][:80]!r}")
        else:
            updated[rel] = updated[rel].replace(p["find"], p["replace"], 1)

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
    ap = argparse.ArgumentParser(description="Apply TrippleEffect Round 1i fixes.")
    ap.add_argument("--dry-run", action="store_true", help="Show diffs only; do not write any files.")
    args = ap.parse_args()
    if not (ROOT / "src").is_dir():
        print("ERROR: run this from the TrippleEffect repository root.", file=sys.stderr)
        return 1
    return apply_patches(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
