# START OF FILE tests/unit/test_round2j.py
"""
Round 2j: fixtures from the 2026-10-20 07:05-07:37 run with qwen3.5:9b (project Temp_Converter_CLI, session
startup_1792451114).

* 07:19:37-07:26:08 - PM1 spent 6.5 minutes on a kick-off plan whose roles and tasks were fine, but whose
  "project_structure" closed one entry too early (']}],' then '{"file": "README.md"}' then ']}'). The plan was rejected as a
  whole and PM1 had to write it again. It also wrote roles as objects and tasks with "title" instead of "description".
"""
import asyncio
import types

import pytest

from src.agents.constants import PM_STATE_STARTUP

PROJECT, SESSION = "Temp_Converter_CLI", "startup_1792451114"


# --- a kick-off plan with one broken part (2026-10-20 07:26:08) -----------------------------------------------------------
KICKOFF_07_26 = '''```json
{
  "roles": [
    {
      "name": "Coder",
      "description": "Implements the core temperature conversion logic using argparse for CLI input, writes automated pytest tests to verify correct conversions"
    },
    {
      "name": "Technical_Writer", 
      "description": "Creates comprehensive README.md documentation explaining usage examples and conversion formulas"}
  ],
  "tasks": [
    {"id": "task_1", "depends_on": null, "title": "Implement core temperature converter functions in src/temp_convert.py"},
    {"id": "task_2", "depends_on": ["task_1"], "title": "Write pytest test cases for conversion accuracy and edge case verification (0°C=32°F, 100°C=212°F, -40° bidirectional)"}, 
    {"id": "task_3", "depends_on": null, "title": "Create README.md with usage examples like `python temp_convert.py 100 C`, conversion formulas reference, and test instructions"},
    {"id": "task_4", "depends_on": ["task_2"], "title": "Execute all unit tests via pytest and run sample conversions against known values to verify end-to-end functionality"}
  ],
  "code_base_definitions": {
    "tech_stack": "Python 3 standard library only (argparse for CLI parsing, no external dependencies)", 
    "testing_framework": "pytest", 
    "coding_conventions": "PEP8 compliance with clear function naming conventions and docstrings in all functions. Use type hints where appropriate but not required since this is a simple script.",
    "file_naming": ".py files for source code, .md/.txt documentation"
  },
  "project_structure": [
    {"dir": "src", "children": [{"files": ["temp_convert.py"]}]}, 
    {"dir": "tests", "children": [{"files": ["test_temp_convert.py"]}]}],
    {"file": "README.md"}
]}```'''


def test_the_observed_plan_is_not_valid_json():
    import json
    body = KICKOFF_07_26.strip("`")[len("json\n"):]
    with pytest.raises(json.JSONDecodeError):
        json.loads(body)


def test_salvage_keeps_the_valid_parts_of_the_observed_plan():
    from src.agents.workflow_manager import salvage_kickoff_plan
    plan = salvage_kickoff_plan(KICKOFF_07_26)
    assert [r["name"] for r in plan["roles"]] == ["Coder", "Technical_Writer"]
    assert [t["id"] for t in plan["tasks"]] == ["task_1", "task_2", "task_3", "task_4"]
    assert plan["code_base_definitions"]["testing_framework"] == "pytest"
    assert [entry["dir"] for entry in plan["project_structure"]] == ["src", "tests"]


def test_salvage_needs_roles_and_tasks():
    from src.agents.workflow_manager import salvage_kickoff_plan
    assert salvage_kickoff_plan('{"roles": ["Coder"], "tasks": [}') is None
    assert salvage_kickoff_plan('{"roles": [], "tasks": [{"description": "x"}]}') is None
    assert salvage_kickoff_plan('{"title": "Temp Converter CLI"}') is None
    assert salvage_kickoff_plan(None) is None
    # single-quoted / bare-word slips inside a part are repaired as for a whole plan
    plan = salvage_kickoff_plan("{'roles': ['Coder'], \"tasks\": [{\"id\": \"task_2\", \"depends_on\": task_1, \"description\": \"x\"}], ]")
    assert plan["roles"] == ["Coder"] and plan["tasks"][0]["depends_on"] == "task_1"


def test_the_workflow_trigger_uses_the_salvaged_plan():
    from src.agents.workflow_manager import AgentWorkflowManager
    seen = {}

    class KickoffWorkflow:
        name = "pm_project_kickoff"

        async def execute(self, manager, agent, data):
            seen.update(data)
            return "executed"

    wm = AgentWorkflowManager()
    agent = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state=PM_STATE_STARTUP)
    result = asyncio.run(wm._try_json_workflow_trigger(None, agent, KICKOFF_07_26, [("kickoff_plan", KickoffWorkflow())]))
    assert result == "executed"
    assert len(seen["tasks"]) == 4 and len(seen["roles"]) == 2


def test_role_objects_and_task_titles_are_read():
    from src.workflows.pm_kickoff_workflow import role_name_of, task_text_of
    assert role_name_of({"name": "Coder", "description": "..."}) == "Coder"
    assert role_name_of({"role": " Tester "}) == "Tester"
    assert role_name_of("Coder") == "Coder"
    odd = {"description": 3}
    assert role_name_of(odd) is odd
    assert task_text_of({"title": "Implement x"}) == "Implement x"
    assert task_text_of({"description": "D", "title": "T"}) == "D"
    assert task_text_of({"id": "task_1"}) == "" and task_text_of("text") == ""


@pytest.fixture
def kickoff_store(tmp_path, monkeypatch):
    import shutil
    pytest.importorskip("tasklib")
    if shutil.which("task") is None:
        pytest.skip("Taskwarrior binary not installed")
    import src.tools.project_management as pm
    import src.workflows.pm_kickoff_workflow as pkw
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class Executor:
        async def execute_tool(self, agent_id, agent_sandbox_path, tool_name, tool_args, project_name, session_name, manager):
            return await pm.ProjectManagementTool().execute(agent_id, tmp_path, project_name=project_name,
                                                            session_name=session_name, **tool_args)
    asyncio.run(pm.ProjectManagementTool().execute(
        "framework", tmp_path, project_name=PROJECT, session_name=SESSION, action="add_task",
        description=f"PROJECT KICK-OFF: {PROJECT}", tags=["project_kickoff", "auto_created_by_framework"], assignee_agent_id="PM1"))
    manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, tool_executor=Executor())
    agent = types.SimpleNamespace(agent_id="PM1", sandbox_path=tmp_path, message_history=[],
                                  agent_config={"config": {"project_name_context": PROJECT}})
    agent.clear_history = lambda: agent.message_history.clear()
    return manager, agent, pm.ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)


def test_the_observed_plan_creates_roles_and_tasks(kickoff_store):
    from src.agents.workflow_manager import salvage_kickoff_plan
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
    manager, agent, tw = kickoff_store
    result = asyncio.run(PMKickoffWorkflow().execute(manager, agent, salvage_kickoff_plan(KICKOFF_07_26)))
    assert result.success is True, result.message
    assert agent.kick_off_roles == ["Coder", "Technical_Writer"]
    descriptions = sorted(str(t["description"]) for t in tw.tasks.pending() if str(t["description"]).startswith("Kick-off Task"))
    assert descriptions[0] == "Kick-off Task 1: Implement core temperature converter functions in src/temp_convert.py"
    assert len(descriptions) == 4
