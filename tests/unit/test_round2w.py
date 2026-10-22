# START OF FILE tests/unit/test_round2w.py
"""
Round 2w: the tools' own information (schema text sent with every call, and tool_information help) audited for all tools.

2026-10-22 11:08 (WiFi_Snake_Game, qwen3.5:9b): PM1 had to assign Kick-off Task 1 to W1. The project_management schema said
only "Manages project tasks using Tasklib." and action "The action to perform."; its summary named actions that do not exist
(get_tasks, get_project_state); its help told PMs to put "{project}_worker_1" as assignee and to add the tag themselves.
PM1 sent modify_task with 'status_change_type' / 'new_assignee_value', then the action 'add_assignee_agent_to_task', then
'add_assignee_agent_id': three failures, and PM1 was set to 'error'.

The audit tests below check every registered tool, so a later change of a tool that leaves its information behind fails.
"""
import asyncio
import importlib
import inspect
import json
import re
import shutil
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

PM_ACTIONS = ["add_task", "list_tasks", "modify_task", "complete_task", "get_dependency_graph"]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def tools():
    from src.tools.executor import ToolExecutor
    return ToolExecutor().tools


def _schema_text(tool) -> str:
    schema = tool.get_json_schema()["function"]
    return schema["description"] + "\n" + "\n".join(
        f"{name}: {value['description']}" for name, value in schema["parameters"]["properties"].items())


def _all_help(tool) -> str:
    text = inspect.getsource(importlib.import_module(type(tool).__module__))
    parts = []
    for sub_action in [None] + sorted(set(re.findall(r'sub_action\s*==\s*["\'](\w+)["\']', text))):
        try:
            parts.append(tool.get_detailed_usage(sub_action=sub_action))
        except TypeError:
            parts.append(tool.get_detailed_usage())
    return "\n".join(parts)


# ======================================================================================== 1. audit of every tool
# actions a tool accepts only as another name for a listed action
ACTION_ALIASES = {"file_system": {"list_with_lines", "write_file"}}
# names in help texts that are not parameters of that tool (tool_information calls, chunk keys, file contents shown)
HELP_NAMES_OF_OTHER_THINGS = {"sub_action", "tool_name", "search", "replace", "dependencies", "devDependencies", "jest", "action"}


def test_every_action_a_tool_handles_is_named_in_its_schema(tools):
    missing = {}
    for name, tool in tools.items():
        source = inspect.getsource(importlib.import_module(type(tool).__module__))
        handled = set(re.findall(r'action\s*==\s*["\'](\w+)["\']', source)) - ACTION_ALIASES.get(name, set())
        text = _schema_text(tool)
        absent = sorted(action for action in handled if action not in text)
        if absent:
            missing[name] = absent
    assert missing == {}


def test_every_parameter_the_help_names_is_accepted(tools):
    unknown = {}
    for name, tool in tools.items():
        source = inspect.getsource(importlib.import_module(type(tool).__module__))
        accepted = {p.name for p in tool.parameters} | {alias for p in tool.parameters for alias in (p.aliases or [])}
        accepted |= set(re.findall(r"kwargs\.get\(\s*[\"'](\w+)[\"']", source))
        accepted |= set(re.findall(r"[\"'](\w+)[\"']\s+in\s+kwargs", source))
        help_text = _all_help(tool)
        named = set(re.findall(r"`<(\w+)>`", help_text)) | set(re.findall(r'"(\w+)":\s', help_text))
        bad = sorted(n for n in named if n not in accepted and n not in HELP_NAMES_OF_OTHER_THINGS)
        if bad:
            unknown[name] = bad
    assert unknown == {}


@pytest.mark.parametrize("stale", [
    "PM_1",                      # the PM is 'PM1'
    "_worker_1",                 # workers are 'W1', 'W2', ...
    "16384 characters",          # code_editor allows 32768 per chunk
    "get_project_state", "get_tasks,", "get_team_status",   # actions that do not exist
    "'coder', 'analyst'",        # agent ids are W1, PM1, admin_ai
    "Default: 'private'.",       # the default scope on a project is 'shared'
])
def test_no_stale_information_is_left(tools, stale):
    for name, tool in tools.items():
        blob = _schema_text(tool) + "\n" + (tool.summary or "") + "\n" + _all_help(tool)
        assert stale not in blob, (name, stale)


# ======================================================================================== 2. project_management information
def test_the_project_management_schema_says_how_to_assign(tools):
    tool = tools["project_management"]
    schema = tool.get_json_schema()["function"]
    props = schema["parameters"]["properties"]
    for action in PM_ACTIONS:
        assert f"'{action}'" in props["action"]["description"] and action in tool.summary
    assert '{"action": "modify_task", "task_id": "<uuid>", "assignee_agent_id": "W1"}' in schema["description"]
    assert "Only workers (W1, W2, ...) can be assignees." in schema["description"]
    assert props["priority"]["description"] == "Task priority: 'H' (high), 'M' (medium) or 'L' (low)."
    for progress in ("todo", "doing", "in_progress", "waiting", "stuck", "decomposed", "finished", "deleted"):
        assert f"'{progress}'" in props["task_progress"]["description"]
    assert "tags_filter" in props and "tags_filter_mode" in props            # the filters the PM prompts use


def test_the_project_management_help_matches_the_tool(tools):
    tool = tools["project_management"]
    header = tool.get_detailed_usage()
    assert "The framework adds the worker's tag and the `assigned` tag itself and starts the worker." in header
    assert "MUST use BOTH the `assignee_agent_id` parameter AND add the agent's ID as a tag" not in header
    modify = tool.get_detailed_usage(sub_action="modify_task")
    assert '"assignee_agent_id": "W1"' in modify and "separated by commas" in modify
    assert "`<tags_filter_mode>`" in tool.get_detailed_usage(sub_action="list_tasks")


# ======================================================================================== 3. the 11:08 calls now work
@pytest.fixture
def tw_env(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    import src.tools.project_management as pm_module
    from src.config.settings import settings
    monkeypatch.setattr(pm_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm_module, "broadcast", no_broadcast)
    manager = types.SimpleNamespace(agents={
        "PM1": types.SimpleNamespace(agent_id="PM1", agent_type="pm"),
        "W1": types.SimpleNamespace(agent_id="W1", agent_type="worker"),
        "W2": types.SimpleNamespace(agent_id="W2", agent_type="worker")}, state_manager=None)

    def call(agent, **kw):
        return run(pm_module.ProjectManagementTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P",
                                                             session_name="S", manager=manager, **kw))
    return call


@needs_tw
@pytest.mark.parametrize("arguments", [
    {"action": "modify_task", "status_change_type": "assignee_agent_addition", "new_assignee_value": "W1"},   # 11:08:20
    {"action": "add_assignee_agent_to_task", "new_assignee_value": "W1"},                                     # 11:08:29
    {"action": "modify_task", "add_assignee_agent_id": "W1", "status_change_type": "assignee_agent_addition"},  # 11:08:42
    {"action": "assign_task", "assigned_to": "W1"},
])
def test_pm1s_assignment_calls_assign_the_task(tw_env, arguments):
    task = tw_env("PM1", action="add_task", description="Kick-off Task 1: Implement core game engine")
    result = tw_env("PM1", task_id=task["task_uuid"], **arguments)
    assert result["status"] == "success" and result["assignee"] == "W1"


@needs_tw
def test_unknown_parameters_are_named(tw_env):
    task = tw_env("PM1", action="add_task", description="Kick-off Task 1")
    result = tw_env("PM1", action="modify_task", task_id=task["task_uuid"], status_change_type="assignee_agent_addition")
    assert result["status"] == "error"
    assert result["message"].endswith(
        "Unknown parameter(s) were ignored: status_change_type. To assign the task to a worker send exactly: "
        '{"action": "modify_task", "task_id": "UUID", "assignee_agent_id": "W1"}.')
    refused = tw_env("PM1", action="modify_task", task_id=task["task_uuid"], new_assignee_value="PM1")
    assert refused["status"] == "error" and refused["error_type"] == "non_worker_assignee"   # an alias is checked too


@needs_tw
def test_modify_task_accepts_several_dependencies(tw_env):
    first = tw_env("PM1", action="add_task", description="Task 1", task_id="task_1")
    second = tw_env("PM1", action="add_task", description="Task 2", task_id="task_2")
    third = tw_env("PM1", action="add_task", description="Task 3")
    result = tw_env("PM1", action="modify_task", task_id=third["task_uuid"], depends=f"task_1, {second['task_uuid']}")
    assert result["status"] == "success"
    listed = tw_env("PM1", action="list_tasks")
    deps = next(t for t in listed["tasks"] if t["uuid"] == third["task_uuid"])["depends"]
    assert sorted(deps) == sorted([first["task_uuid"], second["task_uuid"]])
    missing = tw_env("PM1", action="modify_task", task_id=third["task_uuid"], depends="task_9")
    assert missing["status"] == "error" and missing["message"].startswith("Dependency task 'task_9' not found, so nothing was changed.")


def test_assignee_aliases_and_task_references():
    from src.tools.project_management import normalize_assignee_alias
    args = {"assignee_agent_id": "", "new_assignee_value": "W2"}
    assert normalize_assignee_alias(args) == "new_assignee_value" and args["assignee_agent_id"] == "W2"
    kept = {"assignee_agent_id": "W1", "worker": "W2"}
    assert normalize_assignee_alias(kept) is None and kept["assignee_agent_id"] == "W1"
    assert normalize_assignee_alias({"task_progress": "doing"}) is None


# ======================================================================================== 4. the other tools
def test_manage_team_information(tools):
    tool = tools["manage_team"]
    assert "send_message" not in tool.summary and "get_team_status" not in tool.summary
    states_help = tool.get_detailed_usage(sub_action="set_agent_state")
    for state in ("pm_build_team_tasks", "pm_activate_workers", "pm_report_check", "pm_audit", "pm_standby",
                  "worker_decompose", "worker_test", "worker_report"):
        assert state in states_help
    assert "do NOT send an `agent_id`" in tool.get_detailed_usage(sub_action="create_agent")


def test_send_message_code_editor_test_runner_and_tool_information(tools):
    assert "(e.g., 'W1', 'PM1', 'admin_ai')" in _schema_text(tools["send_message"])
    assert '"target_agent_id": "PM1"' in tools["send_message"].get_detailed_usage()
    code_editor_text = _schema_text(tools["code_editor"])
    assert "32768 characters" in code_editor_text and "create new files with file_system 'write'" in code_editor_text
    assert "MAX_CHUNK_SIZE_CHARS = 32768" in inspect.getsource(importlib.import_module("src.tools.code_editor"))
    assert "(default 30, at most 120)" in tools["test_runner"].get_detailed_usage()
    assert "'project_management'" in _schema_text(tools["tool_information"])
    file_system_text = _schema_text(tools["file_system"])
    for action in ("git_log", "git_branch", "git_checkout", "git_add", "git_pull", "git_push"):
        assert f"'{action}'" in file_system_text
    assert "'append', 'insert_lines', 'replace_lines', 'find_replace'" in file_system_text

# END OF FILE tests/unit/test_round2w.py
