"""Round 1n tests: false failure signals and kickoff leftovers seen in the 2026-10-15 Temp_Convert_CLI run (15:14-16:29).

1. cycle handler: a successful read of PROJECT_STRUCTURE.md ("... error handling ...") and a successful but empty
   web_search ("Check for spelling errors") were counted as FAILED cycles; three in a row forced worker W1 into the report
   state before it had researched anything (15:33:19 and 15:45:34).
2. workflow manager: W1 had written nothing, but merely reading PROJECT_STRUCTURE.md (which lists temp_convert.py as
   planned) made the framework tell it that it "appears to have created/modified code files".
3. file_system: W1 wrote its 3 KB temp_convert.py under the parameter name `message_content` and was rejected twice
   ("'content' parameter is required for 'write'").
4. PM kickoff: the PM invented the dependency id `temp_structure_init`; the task tool refused it, the whole kickoff failed
   after two tasks already existed, and the retry (different wording) created two more.
"""
import asyncio
import json
import shutil
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents import tool_activity as ta

# ---------------------------------------------------------------------------------------------------------------------
# 1. is_tool_result_failure
# ---------------------------------------------------------------------------------------------------------------------

STRUCTURE_MD_READ = (
    "1: # Project Directory Structure\n2: \n3: ## Code Base Definitions & Standards\n4: \n5: {\n"
    "6: \"coding_conventions\": [\"Implement error handling with try/except blocks\"],\n"
    "7: }\n8: \n9: Entries marked (planned) are files that do NOT exist yet\n"
)
EMPTY_SEARCH = (
    "No search results found for query: 'argparse best practices'\n[source]: DuckDuckGo Scraping\n[results]: []\n"
    "[suggestions]: [\"Try using different keywords\", \"Make your query more specific or more general\", "
    "\"Check for spelling errors in your query\"]"
)


def legacy_failure(content: str) -> bool:
    """The rule the cycle handler used before Round 1n."""
    try:
        data = json.loads(content)
        return isinstance(data, dict) and data.get("status") == "error"
    except (json.JSONDecodeError, TypeError):
        low = content.lower()
        return "error" in low or "[tool execution failed]" in low


def test_successful_file_read_mentioning_error_is_not_a_failure():
    assert legacy_failure(STRUCTURE_MD_READ) is True  # documents the bug
    assert ta.is_tool_result_failure("file_system", STRUCTURE_MD_READ) is False


def test_successful_empty_search_is_not_a_failure():
    assert legacy_failure(EMPTY_SEARCH) is True  # documents the bug
    assert ta.is_tool_result_failure("web_search", EMPTY_SEARCH) is False


@pytest.mark.parametrize("tool", ["file_system", "web_search", "zim_search", "knowledge_base", "codebase_search",
                                  "tool_information", "asset_search", "github_tool", "system_help",
                                  "command_executor", "test_runner", "project_management", "send_message"])
@pytest.mark.parametrize("content", [
    "[Tool Execution Failed] 'content' parameter is required for 'write'. [ID:8_1] - File operation failed.",
    "[Tool Execution Failed] File not found: 'shared/src/core/temp_convert.py' in shared workspace",
    "[ToolExec Error: ToolExecutor unavailable]",
    "[Tool Execution Error: Unknown]",
    '{"status": "error", "message": "boom"}',
])
def test_real_failures_are_failures_for_every_tool(tool, content):
    assert ta.is_tool_result_failure(tool, content) is True


def test_framework_failure_marker_inside_file_text_is_not_a_failure():
    text = "1: log line\n2: [Tool Execution Failed] copied from an old log\n"
    assert ta.is_tool_result_failure("file_system", text) is False


@pytest.mark.parametrize("content", [
    "Traceback (most recent call last):\n  File x.py\nValueError: bad",
    "FAILED tests/test_x.py::test_a - AssertionError\n1 failed, 2 passed",
    "bash: foo: command not found\nError: exit status 127",
    "Some output without the keyword",
    "",
])
def test_other_tools_keep_the_legacy_rule_exactly(content):
    for tool in ("command_executor", "test_runner", "code_editor", "manage_team", "some_new_tool", None):
        assert ta.is_tool_result_failure(tool, content) == legacy_failure(content)


@pytest.mark.parametrize("content", ['{"status": "success", "message": "no error occurred"}', "[1, 2, 3]", "42", "null"])
def test_structured_results_are_judged_by_status_only(content):
    for tool in ("file_system", "command_executor"):
        assert ta.is_tool_result_failure(tool, content) is False


def test_none_content_is_not_a_failure():
    assert ta.is_tool_result_failure("file_system", None) is False


# ---------------------------------------------------------------------------------------------------------------------
# 2. written_code_files
# ---------------------------------------------------------------------------------------------------------------------

def call(call_id, name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": args}]}


def result(call_id, name, content):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def test_reading_the_structure_file_is_not_writing_code():
    history = [
        {"role": "system", "content": "sys"},
        call("c1", "file_system", action="read", path="PROJECT_STRUCTURE.md", scope="shared"),
        result("c1", "file_system", "1: temp_convert.py (planned)\n2: test_temp_convert.py"),
        call("c2", "web_search", query="python temp_convert.py argparse"),
        result("c2", "web_search", EMPTY_SEARCH),
    ]
    assert ta.written_code_files(history) == []


def test_successful_write_counts():
    history = [call("c1", "file_system", action="write", path="src/core/temp_convert.py", content="x=1"),
               result("c1", "file_system", "File 'src/core/temp_convert.py' written successfully.")]
    assert ta.written_code_files(history) == ["src/core/temp_convert.py"]


def test_failed_write_does_not_count():
    history = [call("c1", "file_system", action="write", path="src/core/temp_convert.py", message_content="x=1"),
               result("c1", "file_system", "[Tool Execution Failed] 'content' parameter is required for 'write'.")]
    assert ta.written_code_files(history) == []


def test_write_without_a_result_yet_does_not_count():
    assert ta.written_code_files([call("c1", "file_system", action="write", path="a.py", content="x")]) == []


@pytest.mark.parametrize("action", ["append", "insert_lines", "replace_lines", "find_replace", "regex_replace",
                                    "search_replace_block"])
def test_edit_actions_count(action):
    history = [call("c1", "file_system", action=action, filename="app.js"), result("c1", "file_system", "ok")]
    assert ta.written_code_files(history) == ["app.js"]


def test_non_code_files_and_read_only_actions_do_not_count():
    history = [
        call("c1", "file_system", action="write", path="README.md", content="# hi"), result("c1", "file_system", "ok"),
        call("c2", "file_system", action="list", path="src"), result("c2", "file_system", "main.py"),
        call("c3", "file_system", action="mkdir", path="src/core.py"), result("c3", "file_system", "ok"),
        call("c4", "code_editor", action="view", path="main.py"), result("c4", "code_editor", "print(1)"),
    ]
    assert ta.written_code_files(history) == []


def test_code_editor_edit_counts():
    history = [call("c1", "code_editor", action="apply_patch", path="main.py"), result("c1", "code_editor", "patched")]
    assert ta.written_code_files(history) == ["main.py"]


def test_dedupes_keeps_order_and_limits():
    history = []
    for i, name in enumerate(["b.py", "a.py", "b.py", "c.js", "d.ts", "e.go", "f.rs"]):
        history += [call(f"c{i}", "file_system", action="write", path=name, content="x"), result(f"c{i}", "file_system", "ok")]
    assert ta.written_code_files(history) == ["b.py", "a.py", "c.js", "d.ts", "e.go"]
    assert ta.written_code_files(history, limit=2) == ["b.py", "a.py"]


def test_arguments_given_as_json_string_are_understood():
    msg = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "name": "file_system", "arguments": json.dumps({"action": "write", "path": "x.py", "content": "1"})}]}
    assert ta.written_code_files([msg, result("c1", "file_system", "ok")]) == ["x.py"]


def test_malformed_history_entries_are_ignored():
    history = ["junk", None, {"role": "assistant", "tool_calls": ["bad", {"name": "file_system", "arguments": None}]},
               {"role": "tool", "content": "orphan"}]
    assert ta.written_code_files(history) == []


# ---------------------------------------------------------------------------------------------------------------------
# 2b. The real state-change code path (workflow_manager.change_state work -> report)
# ---------------------------------------------------------------------------------------------------------------------

def worker(history):
    from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_WORK
    return types.SimpleNamespace(agent_id="W1", agent_type=AGENT_TYPE_WORKER, state=WORKER_STATE_WORK,
                                 message_history=history, _last_system_prompt_state="x")


def reminders(agent):
    return [m for m in agent.message_history if "[Framework Testing Reminder]" in str(m.get("content", ""))]


def test_change_state_does_not_nudge_a_worker_that_only_read_the_plan_files():
    from src.agents.workflow_manager import AgentWorkflowManager
    from src.agents.constants import WORKER_STATE_REPORT
    history = [
        {"role": "system", "content": "sys"},
        call("c1", "file_system", action="read", path="PROJECT_STRUCTURE.md", scope="shared"),
        result("c1", "file_system", "1: temp_convert.py (planned)\n2: test_temp_convert.py (planned)"),
    ]
    agent = worker(history)
    assert AgentWorkflowManager().change_state(agent, WORKER_STATE_REPORT) is True
    assert agent.state == WORKER_STATE_REPORT
    assert reminders(agent) == []


def test_change_state_still_nudges_a_worker_that_wrote_code_without_testing():
    from src.agents.workflow_manager import AgentWorkflowManager
    from src.agents.constants import WORKER_STATE_REPORT
    history = [
        {"role": "system", "content": "sys"},
        call("c1", "file_system", action="write", path="src/core/temp_convert.py", content="x"),
        result("c1", "file_system", "File written successfully."),
    ]
    agent = worker(history)
    assert AgentWorkflowManager().change_state(agent, WORKER_STATE_REPORT) is True
    found = reminders(agent)
    assert len(found) == 1 and "src/core/temp_convert.py" in found[0]["content"]


def test_change_state_does_not_nudge_after_a_failed_write():
    from src.agents.workflow_manager import AgentWorkflowManager
    from src.agents.constants import WORKER_STATE_REPORT
    history = [
        {"role": "system", "content": "sys"},
        call("c1", "file_system", action="write", path="src/core/temp_convert.py", message_content="x"),
        result("c1", "file_system", "[Tool Execution Failed] 'content' parameter is required for 'write'."),
    ]
    agent = worker(history)
    AgentWorkflowManager().change_state(agent, WORKER_STATE_REPORT)
    assert reminders(agent) == []


# ---------------------------------------------------------------------------------------------------------------------
# 2c. The cycle handler really uses the classifier
# ---------------------------------------------------------------------------------------------------------------------

def test_cycle_handler_uses_the_classifier_and_no_longer_scans_for_the_word_error():
    src = (Path(__file__).resolve().parents[2] / "src/agents/cycle_handler.py").read_text(encoding="utf-8")
    assert "from src.agents.tool_activity import is_tool_result_failure" in src
    assert "tool_was_successful = not is_tool_result_failure(tool_name, result_content_str)" in src
    assert 'if "error" in result_lower or "[tool execution failed]" in result_lower:' not in src


# ---------------------------------------------------------------------------------------------------------------------
# 3. file_system: content under another parameter name
# ---------------------------------------------------------------------------------------------------------------------

def fs_execute(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, **kwargs))


@pytest.mark.parametrize("alias", ["message_content", "file_content", "contents", "content", "text", "code"])
def test_write_accepts_the_content_under_common_wrong_names(tmp_path, alias):
    r = fs_execute(tmp_path, action="write", path="src/core/temp_convert.py", **{alias: "def f():\n    return 1\n"})
    assert r["status"] == "success", r
    assert (tmp_path / "src/core/temp_convert.py").read_text() == "def f():\n    return 1\n"


def test_content_wins_over_an_alias(tmp_path):
    r = fs_execute(tmp_path, action="write", path="a.txt", content="right", message_content="wrong")
    assert r["status"] == "success"
    assert (tmp_path / "a.txt").read_text() == "right"


@pytest.mark.parametrize("action", ["write", "append", "insert_lines", "replace_lines"])
def test_missing_content_error_names_the_received_parameters(tmp_path, action):
    (tmp_path / "a.txt").write_text("1\n2\n3\n")
    r = fs_execute(tmp_path, action=action, path="a.txt", msg_body="x", insert_line=1, replace_start_line=1, replace_end_line=1)
    assert r["status"] == "error"
    assert f"'content' parameter is required for '{action}'" in r["message"]
    assert "Received parameters:" in r["message"] and "msg_body" in r["message"] and "path" in r["message"]


def test_append_with_alias_works(tmp_path):
    (tmp_path / "a.txt").write_text("1\n")
    r = fs_execute(tmp_path, action="append", path="a.txt", message_content="2\n")
    assert r["status"] == "success", r
    assert (tmp_path / "a.txt").read_text() == "1\n2\n"


# ---------------------------------------------------------------------------------------------------------------------
# 4. PM kickoff: unknown dependencies and rollback (real Taskwarrior)
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,created,kept,dropped", [
    ("task_1", {"task_1"}, ["task_1"], []),
    ("temp_structure_init", {"task_1"}, [], ["temp_structure_init"]),
    (["task_1", "bogus"], {"task_1"}, ["task_1"], ["bogus"]),
    ('["task_1", "task_2"]', {"task_1", "task_2"}, ["task_1", "task_2"], []),
    ("task_1, task_2", {"task_1"}, ["task_1"], ["task_2"]),
    (1, {"1"}, ["1"], []),
    (1, set(), [], ["1"]),
    ("none", {"task_1"}, [], []),
    ("", set(), [], []),
])
def test_sanitize_kickoff_dependencies(raw, created, kept, dropped):
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
    assert PMKickoffWorkflow._sanitize_kickoff_dependencies(raw, created) == (kept, dropped)


needs_taskwarrior = pytest.mark.skipif(shutil.which("task") is None, reason="Taskwarrior binary not installed")


class Executor:
    """Plays the ToolExecutor: runs the REAL project_management tool; optionally raises for one task description."""

    def __init__(self, tmp_path, fail_on=None):
        self.tmp_path, self.fail_on, self.calls = tmp_path, fail_on, []

    async def execute_tool(self, agent_id, agent_sandbox_path, tool_name, tool_args, project_name, session_name, manager):
        import src.tools.project_management as pm
        self.calls.append((tool_name, dict(tool_args)))
        if self.fail_on and tool_args.get("action") == "add_task" and self.fail_on in tool_args.get("description", ""):
            raise RuntimeError("simulated tool crash")
        return await pm.ProjectManagementTool().execute(agent_id, self.tmp_path, project_name=project_name,
                                                        session_name=session_name, **tool_args)


class FakeAgent:
    def __init__(self, tmp_path):
        self.agent_id = "PM1"
        self.sandbox_path = tmp_path
        self.agent_config = {"config": {"project_name_context": "P"}}
        self.message_history = []

    def clear_history(self):
        self.message_history = []


@pytest.fixture
def kickoff(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    if shutil.which("task") is None:
        pytest.skip("Taskwarrior binary not installed")
    import src.tools.project_management as pm
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    import src.workflows.pm_kickoff_workflow as pkw

    class PlainResult:
        """The real WorkflowResult is a pydantic model that insists on a real Agent in tasks_to_schedule."""

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class Env:
        pass

    env = Env()
    env.tmp_path = tmp_path
    env.pm = pm

    def run(plan, fail_on=None):
        from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
        executor = Executor(tmp_path, fail_on=fail_on)
        manager = types.SimpleNamespace(current_project="P", current_session="S", tool_executor=executor)
        env.executor = executor
        return asyncio.run(PMKickoffWorkflow().execute(manager, FakeAgent(tmp_path), plan))

    def tw():
        return pm.ProjectManagementTool()._get_taskwarrior_instance("P", "S")

    def kickoff_tasks():
        return [t for t in tw().tasks.pending() if str(t["description"]).startswith("Kick-off Task")]

    def aliases():
        return pm.ProjectManagementTool()._load_aliases("P", "S")

    env.run, env.tw, env.kickoff_tasks, env.aliases = run, tw, kickoff_tasks, aliases
    # the framework's own initial task, as created by the project-creation workflow
    asyncio.run(pm.ProjectManagementTool().execute(
        "framework", tmp_path, project_name="P", session_name="S", action="add_task",
        description="PROJECT KICK-OFF: P", tags=["project_kickoff", "auto_created_by_framework"], assignee_agent_id="PM1"))
    return env


def plan(*tasks, roles=("Coder",)):
    return {"roles": list(roles), "tasks": list(tasks)}


@needs_taskwarrior
def test_unknown_dependency_no_longer_fails_the_whole_kickoff(kickoff):
    # the PM output of 15:17:54, reduced: a bogus "task" without description plus a task depending on its invented id
    r = kickoff.run(plan(
        {"id": "task_1", "description": "Research temperature conversion formulas"},
        {"id": "task_2", "depends_on": "task_1", "description": "Setup project structure"},
        {"id": "temp_structure_init", "dir_name": ".venv", "children": []},
        {"id": "task_3", "depends_on": "temp_structure_init", "description": "Create core module directory hierarchy"},
    ))
    assert r.success is True, r.message
    tasks = {t["description"]: t for t in kickoff.kickoff_tasks()}
    assert len(tasks) == 3
    third = tasks["Kick-off Task 3: Create core module directory hierarchy"]
    assert not third["depends"]
    second = tasks["Kick-off Task 2: Setup project structure"]
    first = tasks["Kick-off Task 1: Research temperature conversion formulas"]
    assert [d["uuid"] for d in second["depends"]] == [first["uuid"]]


@needs_taskwarrior
def test_valid_list_dependencies_are_kept_and_unknown_ones_dropped(kickoff):
    r = kickoff.run(plan(
        {"id": "a", "description": "Alpha task"},
        {"id": "b", "description": "Bravo task"},
        {"id": "c", "depends_on": ["a", "b", "ghost"], "description": "Charlie task"},
    ))
    assert r.success is True, r.message
    tasks = {t["description"]: t for t in kickoff.kickoff_tasks()}
    deps = {d["description"] for d in tasks["Kick-off Task 3: Charlie task"]["depends"]}
    assert deps == {"Kick-off Task 1: Alpha task", "Kick-off Task 2: Bravo task"}


@needs_taskwarrior
def test_numeric_plan_ids_resolve_to_the_right_tasks(kickoff):
    r = kickoff.run(plan({"id": 1, "description": "First task"}, {"id": 2, "depends_on": 1, "description": "Second task"}))
    assert r.success is True, r.message
    tasks = {t["description"]: t for t in kickoff.kickoff_tasks()}
    second = tasks["Kick-off Task 2: Second task"]
    assert [d["description"] for d in second["depends"]] == ["Kick-off Task 1: First task"]


@needs_taskwarrior
def test_partial_failure_rolls_back_what_this_attempt_created(kickoff):
    r = kickoff.run(plan(
        {"id": "t1", "description": "First task"},
        {"id": "t2", "depends_on": "t1", "description": "Second task"},
        {"id": "t3", "description": "THIRD task"},
    ), fail_on="THIRD")
    assert r.success is False
    assert "removed again" in r.message and "COMPLETE kickoff plan" in r.message
    assert kickoff.kickoff_tasks() == []
    assert "t1" not in kickoff.aliases() and "t2" not in kickoff.aliases()
    # the retry with different wording starts from a clean slate
    r2 = kickoff.run(plan({"id": "task_1", "description": "Research formulas"}, {"id": "task_2", "description": "Write code"}))
    assert r2.success is True, r2.message
    assert len(kickoff.kickoff_tasks()) == 2


@needs_taskwarrior
def test_rollback_never_deletes_a_pre_existing_task_that_was_skipped_as_duplicate(kickoff):
    import src.tools.project_management as pm
    pre = asyncio.run(pm.ProjectManagementTool().execute(
        "PM1", kickoff.tmp_path, project_name="P", session_name="S", action="add_task",
        description="Kick-off Task 1: Research conversion formulas"))
    assert pre["status"] == "success"
    r = kickoff.run(plan(
        {"id": "t1", "description": "Research conversion formulas"},   # skipped as near-duplicate of the existing task
        {"id": "t2", "description": "Second task"},
        {"id": "t3", "description": "THIRD task"},
    ), fail_on="THIRD")
    assert r.success is False
    remaining = [t["description"] for t in kickoff.kickoff_tasks()]
    assert remaining == ["Kick-off Task 1: Research conversion formulas"]


@needs_taskwarrior
def test_success_path_is_unchanged(kickoff):
    r = kickoff.run(plan({"id": "task_1", "description": "Research"}, {"id": "task_2", "depends_on": "task_1", "description": "Build"}))
    assert r.success is True
    assert "All 2 kick-off tasks created successfully" in r.message
    assert len(kickoff.kickoff_tasks()) == 2
