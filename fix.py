#!/usr/bin/env python3
"""
TrippleEffect: Round 1g Surgical Fix

Fixes found in the 2026-10-15 07:21 session (Word_Stats_CLI_Development):

 1. src/agents/manager.py
      Watchdog crashed every 60s for any PM:
      "name 'PM_STATE_REPORT_CHECK' is not defined" (constant was used but never imported).

 2. src/tools/manage_team.py
      The create_agent usage text contained a literal example `"provider": "ollama-local",
      "model": "qwen3:14b"`. The 4B model copied it, the model does not exist locally, and
      PM1 hit 3 consecutive failures -> forced into `error` -> project dead.
      The example no longer contains provider/model and tells the PM to leave them out.

 3. src/agents/agent_lifecycle.py
      Defense in depth: when a dynamic agent (not bootstrap, not session reload) requests a
      provider/model that is not actually available, fall back to the framework's automatic
      model selection instead of failing the whole create_agent call.

 4. src/agents/workflow_manager.py
      First PM kickoff plan was rejected only because the model wrote an unquoted bare word
      (`"depends_on": task_0`). Added a conservative repair for bare-word JSON values that is
      only attempted AFTER strict parsing fails, and never touches text inside strings.

 5. src/agents/core.py
      The PM retry feedback now states the JSON strictness rules so the retry succeeds.

 6. tests/unit/test_round1g.py  (new file)

Usage:
    python3 patch_round1g.py --dry-run     # show diffs, change nothing
    python3 patch_round1g.py               # apply

All-or-nothing: if ANY find-string is missing (or matches more than once) nothing is written.
"""
import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path.cwd()

# --------------------------------------------------------------------------------------
# Patches: (file, find, replace). Each `find` must match EXACTLY ONCE.
# Raw strings are used so backslashes are literal.
# --------------------------------------------------------------------------------------
PATCHES = [
    # ---- 1. manager.py: missing import ------------------------------------------------
    {
        "file": "src/agents/manager.py",
        "find": r'''    PM_STATE_STANDBY, WORKER_STATE_WORK, WORKER_STATE_WAIT,
''',
        "replace": r'''    PM_STATE_STANDBY, WORKER_STATE_WORK, WORKER_STATE_WAIT, PM_STATE_REPORT_CHECK,
''',
    },

    # ---- 2a. manage_team.py: remove literal provider/model hint -----------------------
    {
        "file": "src/tools/manage_team.py",
        "find": r'''*   `<provider>` (string, optional): Specify an LLM provider (e.g., 'ollama-local').
*   `<model>` (string, optional): Specify an LLM model (e.g., 'qwen3:14b').
''',
        "replace": r'''*   `<provider>` and `<model>` (string, optional): LEAVE THESE OUT. The framework automatically picks a valid, available model for every new agent. Only set them if you were explicitly given an exact provider and model name; invented names are not available and are ignored.
''',
    },

    # ---- 2b. manage_team.py: remove provider/model from the example -------------------
    {
        "file": "src/tools/manage_team.py",
        "find": r'''      "system_prompt": "You are a UI designer. Your goal is to design a clean, responsive web interface.",
      "provider": "ollama-local",
      "model": "qwen3:14b"
''',
        "replace": r'''      "system_prompt": "You are a UI designer. Your goal is to design a clean, responsive web interface."
''',
    },

    # ---- 3a. agent_lifecycle.py: availability helper ----------------------------------
    {
        "file": "src/agents/agent_lifecycle.py",
        "find": r'''# --- _create_agent_internal (with corrected validation) ---
''',
        "replace": r'''def _requested_model_is_available(provider_name: Optional[str], model_id_canonical: Optional[str]) -> bool:
    """
    True if the explicitly requested provider/model pair exists in the ModelRegistry.
    Mirrors the suffix handling used by the final validation in _create_agent_internal:
    local provider types (ollama/vllm/litellm) are checked by suffix, others by canonical ID.
    """
    if not provider_name or not model_id_canonical:
        return False
    base_type = _get_base_provider_type_for_class_lookup(provider_name)
    if base_type in ("ollama", "vllm", "litellm") and model_id_canonical.startswith(f"{base_type}/"):
        model_to_check = model_id_canonical[len(base_type) + 1:]
    else:
        model_to_check = model_id_canonical
    return bool(model_registry.is_model_available(provider_name, model_to_check))


# --- _create_agent_internal (with corrected validation) ---
''',
    },

    # ---- 3b. agent_lifecycle.py: fallback to auto-selection ---------------------------
    {
        "file": "src/agents/agent_lifecycle.py",
        "find": r'''    # Auto-selection only for non-bootstrap agents if needed
''',
        "replace": r'''    # --- Fallback: an explicitly requested model/provider that does not exist (dynamic agents only) ---
    # Small local models often invent model names (e.g. copy an example from tool docs). Instead of failing
    # the whole create_agent call, discard the unusable request and let automatic selection choose.
    if (not is_bootstrap and not loading_from_session and provider_name and model_id_canonical
            and not _requested_model_is_available(provider_name, model_id_canonical)):
        logger.warning(
            f"Lifecycle: Requested model '{model_id_canonical}' (provider '{provider_name}') is not available for "
            f"dynamic agent '{agent_id}'. Falling back to automatic model selection."
        )
        provider_name = None
        model_id_canonical = None
        agent_config_data.pop("provider", None)
        agent_config_data.pop("model", None)

    # Auto-selection only for non-bootstrap agents if needed
''',
    },

    # ---- 4a. workflow_manager.py: module-level JSON repair helper ---------------------
    {
        "file": "src/agents/workflow_manager.py",
        "find": r'''class AgentWorkflowManager:
    def __init__(self):
''',
        "replace": r'''# Matches either a complete JSON string literal (group 1, left untouched) or a bare word used as a
# value, i.e. directly after ':' '[' or ',' and directly before ',' '}' or ']' (groups 2 and 3).
_BARE_JSON_VALUE_RE = re.compile(
    r'("(?:\\.|[^"\\])*")'
    r'|([:\[,]\s*)([A-Za-z_][A-Za-z0-9_.\-]*)(?=\s*[,}\]])'
)


def _repair_bare_json_values(text: str) -> str:
    """
    Conservative repair for a very common small-model slip: unquoted identifiers as JSON values,
    e.g. {"id": "task_2", "depends_on": task_1}. Bare words are quoted; Python-style True/False/None
    become true/false/null; valid JSON literals and everything inside strings are left untouched.
    Only intended to be used after strict json.loads has already failed.
    """
    def _sub(match):
        if match.group(1) is not None:
            return match.group(1)
        prefix, word = match.group(2), match.group(3)
        if word in ("true", "false", "null"):
            return match.group(0)
        python_literals = {"True": "true", "False": "false", "None": "null"}
        if word in python_literals:
            return prefix + python_literals[word]
        return prefix + '"' + word + '"'
    return _BARE_JSON_VALUE_RE.sub(_sub, text)


class AgentWorkflowManager:
    def __init__(self):
''',
    },

    # ---- 4b. workflow_manager.py: use repair inside _parse_robust ---------------------
    {
        "file": "src/agents/workflow_manager.py",
        "find": r'''                            text = text[:char_idx].strip()
                            continue
                    break
            return None, last_err
''',
        "replace": r'''                            text = text[:char_idx].strip()
                            continue
                    # Last resort: quote bare-word values (e.g. "depends_on": task_0) and retry once.
                    repaired = _repair_bare_json_values(text)
                    if repaired != text:
                        try:
                            data = json.loads(repaired, strict=False)
                            logger.info("WorkflowManager: Parsed JSON after repairing unquoted bare-word values.")
                            return data, None
                        except json.JSONDecodeError as repair_err:
                            last_err = repair_err
                    break
            return None, last_err
''',
    },

    # ---- 5. core.py: clearer PM retry feedback ----------------------------------------
    {
        "file": "src/agents/core.py",
        "find": r'''f"Do not include conversational text outside of your `<think>` tags."''',
        "replace": r'''f"Do not include conversational text outside of your `<think>` tags.\n"
                            f"The JSON must be strictly valid: every string value (including references such as \"depends_on\": \"task_1\") must be in double quotes; no comments, no trailing commas, no bare words."''',
    },
]

NEW_FILES = {
    "tests/unit/test_round1g.py": r'''# START OF FILE tests/unit/test_round1g.py
"""
Round 1g regression tests:
- manager.py imports PM_STATE_REPORT_CHECK (watchdog NameError every 60s).
- manage_team create_agent usage no longer hard-codes a provider/model that small models copy.
- Lifecycle helper detects unavailable requested models (-> automatic selection fallback).
- Bare-word JSON values (e.g. "depends_on": task_0) are repaired without touching strings,
  and the kickoff plan from the 2026-10-15 session now parses through the workflow trigger path.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents import constants as C


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 1. watchdog constant
def test_manager_imports_pm_state_report_check():
    import src.agents.manager as manager_module
    assert getattr(manager_module, "PM_STATE_REPORT_CHECK", None) == C.PM_STATE_REPORT_CHECK


# ---------------------------------------------------------------- 2. create_agent docs
def test_create_agent_usage_has_no_hardcoded_model():
    from src.tools.manage_team import ManageTeamTool
    text = ManageTeamTool().get_detailed_usage(agent_context=None, sub_action="create_agent")
    assert "qwen3:14b" not in text
    assert '"model"' not in text
    assert '"provider"' not in text
    assert "LEAVE THESE OUT" in text


# ---------------------------------------------------------------- 3. lifecycle helper
def test_requested_model_availability_helper(monkeypatch):
    from src.agents import agent_lifecycle as lc

    available = {("ollama-local", "qwen3.5:4b-q4_K_M"), ("openrouter", "vendor/model:free")}
    monkeypatch.setattr(lc.model_registry, "is_model_available", lambda p, m: (p, m) in available)

    assert lc._requested_model_is_available("ollama-local", "qwen3.5:4b-q4_K_M")
    assert lc._requested_model_is_available("ollama-local", "ollama/qwen3.5:4b-q4_K_M")
    assert not lc._requested_model_is_available("ollama-local", "qwen3:14b")
    assert not lc._requested_model_is_available("ollama-local", "qwen3:14b-q4_K_M")
    assert lc._requested_model_is_available("openrouter", "vendor/model:free")
    assert not lc._requested_model_is_available("openrouter", "vendor/other")
    assert not lc._requested_model_is_available(None, "x")
    assert not lc._requested_model_is_available("ollama-local", None)


# ---------------------------------------------------------------- 4. JSON repair
def test_repair_quotes_bare_words():
    from src.agents.workflow_manager import _repair_bare_json_values
    broken = '{"id": "task_2", "depends_on": task_0, "after": [task_1, task_2]}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)
    fixed = json.loads(_repair_bare_json_values(broken))
    assert fixed == {"id": "task_2", "depends_on": "task_0", "after": ["task_1", "task_2"]}


def test_repair_does_not_touch_strings_or_valid_literals():
    from src.agents.workflow_manager import _repair_bare_json_values
    valid = '{"a": "note: foo, bar", "b": null, "c": true, "d": false, "e": 12, "f": "x [y, z]"}'
    assert _repair_bare_json_values(valid) == valid


def test_repair_maps_python_literals():
    from src.agents.workflow_manager import _repair_bare_json_values
    fixed = json.loads(_repair_bare_json_values('{"a": None, "b": True, "c": False}'))
    assert fixed == {"a": None, "b": True, "c": False}


SESSION_PLAN = """```json
{
  "roles": ["PM1_Project_Manager"],
  "tasks": [
    {"id": "task_0", "description": "Review existing PROJECT_PLAN.md"},
    {"id": "task_1", "depends_on": null, "description": "Research argparse + collections.Counter"},
    {"id": "task_2", "depends_on": task_0, "description": "Design directory structure"},
    {"id": "task_3", "depends_on": task_1, "description": "Define tech stack: stdlib only"}
  ],
  "code_base_definitions": "Tech Stack: Python 3.x. Test framework: pytest.",
  "project_structure": [{"dir": "src", "children": [{"dir": "", "files": ["__init__.py"]}]}]
}
```"""


def test_session_kickoff_plan_parses_via_workflow_trigger():
    from src.agents.workflow_manager import AgentWorkflowManager

    captured = {}

    class FakeWorkflow:
        name = "pm_project_kickoff"

        async def execute(self, manager, agent, data):
            captured["data"] = data
            return "executed"

    wm = AgentWorkflowManager()
    agent = SimpleNamespace(agent_id="PM1", agent_type=C.AGENT_TYPE_PM, state=C.PM_STATE_STARTUP)
    result = run(wm._try_json_workflow_trigger(
        manager=None, agent=agent, content=SESSION_PLAN,
        matching_triggers=[("kickoff_plan", FakeWorkflow())],
    ))
    assert result == "executed"
    assert captured["data"]["tasks"][2]["depends_on"] == "task_0"
    assert len(captured["data"]["tasks"]) == 4


def test_unrepairable_json_still_returns_none():
    from src.agents.workflow_manager import AgentWorkflowManager

    class FakeWorkflow:
        name = "pm_project_kickoff"

        async def execute(self, manager, agent, data):  # pragma: no cover - must not run
            raise AssertionError("workflow must not execute on invalid JSON")

    wm = AgentWorkflowManager()
    agent = SimpleNamespace(agent_id="PM1", agent_type=C.AGENT_TYPE_PM, state=C.PM_STATE_STARTUP)
    result = run(wm._try_json_workflow_trigger(
        manager=None, agent=agent, content='```json\n{"roles": ["a"], "tasks": [ {oops ]}\n```',
        matching_triggers=[("kickoff_plan", FakeWorkflow())],
    ))
    assert result is None
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
    ap = argparse.ArgumentParser(description="Apply TrippleEffect Round 1g fixes.")
    ap.add_argument("--dry-run", action="store_true", help="Show diffs only; do not write any files.")
    args = ap.parse_args()
    if not (ROOT / "src").is_dir():
        print("ERROR: run this from the TrippleEffect repository root.", file=sys.stderr)
        return 1
    return apply_patches(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
