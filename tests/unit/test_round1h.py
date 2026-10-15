# START OF FILE tests/unit/test_round1h.py
"""
Round 1h regression tests:
- request_state pseudo-tool: not offered to a PM in pm_startup; PM gets PM states; admin/worker unchanged.
- Python-style JSON (single quotes, trailing commas) from small models is repaired without touching strings,
  and the real 08:52:02 kickoff plan from the 2026-10-15 session is accepted.
- Manager-type roles are dropped from kickoff plans; roles are de-duplicated.
- pm_startup prompt tells the PM to list worker roles only and still formats cleanly.
- tasklib Task objects have no .get() and `in` is always False: the shared helpers and both call sites
  (prompt_assembler reminder pin, cycle_handler._infer_worker_task) must use item access.
"""
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents import constants as C

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- request_state schema
ORIGINAL_STATE_DESC = "The destination state to transition to (e.g., worker_work, worker_test, worker_report, worker_wait)."
ORIGINAL_TASK_DESC = ("REQUIRED when transitioning to 'worker_work' or 'worker_test' state. "
                      "The UUID of the sub-task you are choosing to work on.")


def test_request_state_not_offered_to_pm_in_startup():
    from src.agents.core import build_request_state_schema
    assert build_request_state_schema(C.AGENT_TYPE_PM, C.PM_STATE_STARTUP, [C.PM_STATE_STARTUP]) is None


def test_request_state_pm_gets_pm_states_only():
    from src.agents.core import build_request_state_schema
    pm_states = [C.PM_STATE_STARTUP, C.PM_STATE_BUILD_TEAM_TASKS, C.PM_STATE_MANAGE, C.PM_STATE_STANDBY, "default"]
    schema = build_request_state_schema(C.AGENT_TYPE_PM, C.PM_STATE_BUILD_TEAM_TASKS, pm_states)
    props = schema["function"]["parameters"]["properties"]
    desc = props["state"]["description"]
    assert C.PM_STATE_MANAGE in desc and C.PM_STATE_STANDBY in desc
    for hidden in ("worker_work", "default", C.PM_STATE_STARTUP, C.PM_STATE_BUILD_TEAM_TASKS):
        assert hidden not in desc
    assert "REQUIRED" not in props["task_id"]["description"]
    assert schema["function"]["name"] == "request_state"
    assert schema["function"]["parameters"]["required"] == ["state"]


def test_request_state_pm_without_valid_states_still_valid_schema():
    from src.agents.core import build_request_state_schema
    schema = build_request_state_schema(C.AGENT_TYPE_PM, C.PM_STATE_MANAGE, None)
    assert schema["function"]["parameters"]["properties"]["state"]["description"] == "The destination state to transition to."


@pytest.mark.parametrize("agent_type,state", [
    (C.AGENT_TYPE_ADMIN, C.ADMIN_STATE_CONVERSATION),
    (C.AGENT_TYPE_ADMIN, C.ADMIN_STATE_PLANNING),
    (C.AGENT_TYPE_WORKER, C.WORKER_STATE_WORK),
    (C.AGENT_TYPE_WORKER, C.WORKER_STATE_DECOMPOSE),
])
def test_request_state_admin_and_worker_schema_unchanged(agent_type, state):
    from src.agents.core import build_request_state_schema
    schema = build_request_state_schema(agent_type, state, ["whatever", "default"])
    assert schema == {
        "type": "function",
        "function": {
            "name": "request_state",
            "description": "Request a state transition for the agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "description": ORIGINAL_STATE_DESC},
                    "task_id": {"type": ["string", "null"], "description": ORIGINAL_TASK_DESC},
                    "task_description": {"type": "string", "description": "Optional description of the task."},
                },
                "required": ["state"],
            },
        },
    }


# ------------------------------------------------------------- JSON syntax repair
def test_repair_json_syntax_single_quotes_and_trailing_commas():
    from src.agents.workflow_manager import _repair_json_syntax
    broken = "{\"a\": [{'file': 'sample.txt'}, ], \"b\": 1, }"
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)
    assert json.loads(_repair_json_syntax(broken)) == {"a": [{"file": "sample.txt"}], "b": 1}


def test_repair_json_syntax_leaves_double_quoted_strings_alone():
    from src.agents.workflow_manager import _repair_json_syntax
    valid = '{"a": "it\'s fine, ] really", "b": "x,}", "c": "say \\"hi\\", ok"}'
    assert _repair_json_syntax(valid) == valid
    assert json.loads(_repair_json_syntax(valid)) == json.loads(valid)


def test_repair_json_syntax_apostrophes_and_double_quotes_in_single_quoted():
    from src.agents.workflow_manager import _repair_json_syntax
    fixed = json.loads(_repair_json_syntax("{'a': 'He said \"hi\"', 'b': 'it\\'s'}"))
    assert fixed == {"a": 'He said "hi"', "b": "it's"}


def test_combined_bare_word_single_quote_and_trailing_comma():
    from src.agents.workflow_manager import _repair_bare_json_values, _repair_json_syntax
    broken = "{'id': 't2', 'depends_on': t1, 'tags': ['a', 'b',],}"
    assert json.loads(_repair_bare_json_values(_repair_json_syntax(broken))) == {
        "id": "t2", "depends_on": "t1", "tags": ["a", "b"]}


SESSION_PLAN_0852 = """```json
{
  "roles": ["Researcher", "Coder"],
  "tasks": [
    {"id": "task_1", "description": "Review existing PROJECT_PLAN.md"},
    {"id": "task_2", "depends_on": "task_1", "description": "Research text analysis"}
  ],
  "code_base_definitions": {
    "tech_stack": ["Python standard library (argparse, collections.Counter)", "pytest>=7.0"],
    "conventions": "Case-insensitive word counting"
  },
  "project_structure": [
    {"dir": "src", "children": [{"dir": "", "files": ["__init__.py"]}, {"dir": "", "files": ["wordstats.py"]}]},
    {"dir": "tests", "children": []},
    {"dir": "samples", "children": [{'file': 'sample.txt'}], },
    {"dir": "docs"},
    {"dir": "config"}
  ]
}
```"""


def test_session_plan_with_single_quotes_and_trailing_comma_is_accepted():
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
        manager=None, agent=agent, content=SESSION_PLAN_0852,
        matching_triggers=[("kickoff_plan", FakeWorkflow())],
    ))
    assert result == "executed"
    assert captured["data"]["project_structure"][2]["children"] == [{"file": "sample.txt"}]


# ------------------------------------------------------------- role sanitiser
def test_sanitize_worker_roles_drops_manager_roles():
    from src.workflows.pm_kickoff_workflow import sanitize_worker_roles
    kept, dropped = sanitize_worker_roles(
        ["Project_Manager", "Researcher", "PM1_Project_Manager", "Coder", "project manager", "PM", "pm2"])
    assert kept == ["Researcher", "Coder"]
    assert dropped == ["Project_Manager", "PM1_Project_Manager", "project manager", "PM", "pm2"]


def test_sanitize_worker_roles_dedupes_and_keeps_similar_names():
    from src.workflows.pm_kickoff_workflow import sanitize_worker_roles
    kept, dropped = sanitize_worker_roles(["Coder", "coder", " Coder ", "Product_Manager", "Spmthing", "Technical_Writer", ""])
    assert kept == ["Coder", "Product_Manager", "Spmthing", "Technical_Writer"]
    assert dropped == []


def test_sanitize_worker_roles_passes_through_non_strings():
    from src.workflows.pm_kickoff_workflow import sanitize_worker_roles
    item = {"role": "Coder"}
    kept, dropped = sanitize_worker_roles([item, "Coder"])
    assert kept == [item, "Coder"] and dropped == []


def test_kickoff_rejects_manager_only_roles_with_explanation():
    from unittest.mock import MagicMock
    from src.agents.core import Agent
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
    agent = MagicMock(spec=Agent)
    agent.agent_id = "PM1"
    plan = {"roles": ["PM1_Project_Manager", "Project_Manager"], "tasks": [{"id": "t1", "description": "x"}]}
    result = run(PMKickoffWorkflow().execute(None, agent, plan))
    assert result.success is False
    assert "Manager roles (PM1_Project_Manager, Project_Manager)" in result.message
    assert result.next_agent_state == C.PM_STATE_STARTUP
    assert result.tasks_to_schedule == [(agent, 0)]


# ------------------------------------------------------------- prompt text
class _Blank(dict):
    def __missing__(self, key):
        return ""


def test_pm_startup_prompt_guides_roles_and_still_formats():
    yaml = pytest.importorskip("yaml")
    prompts = yaml.safe_load((REPO_ROOT / "prompts.yaml").read_text(encoding="utf-8"))
    text = prompts["pm_startup_prompt"]
    assert "NEVER list 'Project_Manager'" in text
    assert "MUST include a developer role" in text
    rendered = text.format_map(_Blank())  # raises on stray/unbalanced braces
    assert '"roles": [' in rendered and "project_structure" in rendered


# ------------------------------------------------------------- tasklib helpers
class FakeTaskLibTask:
    """Mimics tasklib.Task: item access only, no .get(), no __contains__, None for unset fields."""

    def __init__(self, **fields):
        self._data = dict(fields)

    def __getitem__(self, key):
        try:
            int(key)
            raise StopIteration
        except ValueError:
            pass
        return self._data.get(key)


def test_fake_task_really_behaves_like_tasklib():
    t = FakeTaskLibTask(assignee="W1")
    assert not hasattr(t, "get")
    assert "assignee" not in t  # the trap: membership is False even though the field is set
    assert t["assignee"] == "W1" and t["missing"] is None


def test_get_task_field_item_access_and_defaults():
    from src.utils.task_utils import get_task_field
    t = FakeTaskLibTask(task_progress="todo", status="pending")
    assert get_task_field(t, "task_progress") == "todo"
    assert get_task_field(t, "nope") is None
    assert get_task_field(t, "nope", "dflt") == "dflt"
    assert get_task_field(None, "x", 5) == 5
    assert get_task_field({"a": 1}, "a") == 1 and get_task_field({"a": 1}, "b", 2) == 2


def test_task_is_assigned_to_variants():
    from src.utils.task_utils import task_is_assigned_to
    assert task_is_assigned_to(FakeTaskLibTask(assignee="W1"), "W1")
    assert not task_is_assigned_to(FakeTaskLibTask(assignee="W2"), "W1")
    assert not task_is_assigned_to(FakeTaskLibTask(), "W1")
    assert task_is_assigned_to(FakeTaskLibTask(tags={"assigned", "W1"}), "W1")
    assert task_is_assigned_to(FakeTaskLibTask(tags={"assigned", "+W1"}), "W1")
    assert not task_is_assigned_to(FakeTaskLibTask(tags={"W1"}), "W1")
    assert task_is_assigned_to({"assignee": "W1"}, "W1")


def test_real_tasklib_task_matches_the_assumptions():
    tasklib_task = pytest.importorskip("tasklib.task")

    class DummyBackend:
        config = {}

        def __getattr__(self, name):
            raise AttributeError(name)

    try:
        t = tasklib_task.Task(DummyBackend(), description="demo", assignee="W1", task_progress="todo")
    except Exception as exc:  # tasklib internals differ across versions; the fake above covers the contract
        pytest.skip(f"cannot build an in-memory tasklib Task here: {exc}")
    from src.utils.task_utils import get_task_field, task_is_assigned_to
    assert not hasattr(t, "get")
    assert "assignee" not in t
    assert get_task_field(t, "task_progress") == "todo"
    assert task_is_assigned_to(t, "W1")


# ------------------------------------------------------------- call sites
def test_infer_worker_task_returns_assigned_task(monkeypatch):
    from src.agents.cycle_handler import AgentCycleHandler
    import src.tools.project_management as pm_module

    tasks = [
        FakeTaskLibTask(uuid="u-other", assignee="W2", task_progress="todo"),
        FakeTaskLibTask(uuid="u-done", assignee="W1", task_progress="finished"),
        FakeTaskLibTask(uuid="u-parent", assignee="W1", task_progress="decomposed"),
        FakeTaskLibTask(uuid="u-mine", assignee="W1", task_progress="todo"),
    ]
    tw = SimpleNamespace(tasks=SimpleNamespace(filter=lambda **kw: tasks))
    monkeypatch.setattr(pm_module, "TASKLIB_AVAILABLE", True)
    monkeypatch.setattr(pm_module.ProjectManagementTool, "_get_taskwarrior_instance", lambda self, p, s: tw)

    handler = AgentCycleHandler.__new__(AgentCycleHandler)
    handler._manager = SimpleNamespace(current_project="P", current_session="S")
    assert handler._infer_worker_task("W1") == "u-mine"
    assert handler._infer_worker_task("W9") is None


def _function_source(path: Path, qualified_start: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(qualified_start)
    nxt = re.search(r"\n    def |\nclass |\ndef ", text[start + len(qualified_start):])
    return text[start:start + len(qualified_start) + (nxt.start() if nxt else len(text))]


def test_no_task_dot_get_left_in_tasklib_call_sites():
    pa = (REPO_ROOT / "src/agents/cycle_components/prompt_assembler.py").read_text(encoding="utf-8")
    assert "task.get(" not in pa
    body = _function_source(REPO_ROOT / "src/agents/cycle_handler.py", "def _infer_worker_task(")
    assert "task.get(" not in body
    assert "' in task" not in body and '" in task' not in body
