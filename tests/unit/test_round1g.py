# START OF FILE tests/unit/test_round1g.py
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
