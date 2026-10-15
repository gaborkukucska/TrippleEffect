#!/usr/bin/env python3
"""
Round 1n test-harness fix (tests/unit/test_round1n.py only; no production code changes).

The 6 kickoff tests failed with a pydantic ValidationError because the real WorkflowResult only accepts a real Agent in
tasks_to_schedule, and the tests use a lightweight fake agent. The tests now swap WorkflowResult for a plain object.
Usage:  python3 fix.py --dry-run   then   python3 fix.py
"""
import sys
from pathlib import Path

path = Path("tests/unit/test_round1n.py")
old = '    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)\n'
new = old + '''    import src.workflows.pm_kickoff_workflow as pkw

    class PlainResult:
        """The real WorkflowResult is a pydantic model that insists on a real Agent in tasks_to_schedule."""

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)
'''
text = path.read_text(encoding="utf-8")
if text.count(old) != 1:
    sys.exit(f"ERROR: expected exactly 1 match in {path}, found {text.count(old)}; nothing written.")
if "PlainResult" in text:
    sys.exit("Already applied; nothing written.")
if "--dry-run" in sys.argv:
    print("[dry-run] would patch", path)
else:
    path.write_text(text.replace(old, new), encoding="utf-8")
    print("PATCHED ", path)
