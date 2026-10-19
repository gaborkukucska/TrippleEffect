# START OF FILE tests/unit/test_round2f.py
"""Round 2f tests (run 2026-10-19 07:52-10:28, Temp_Converter_CLI, qwen3.5:4b).

The project was built and all tasks were finished, but:
1. 10:26-10:28: PM1 listed the (empty) task list four times; the Round 2e polling rule then put it in pm_standby, so the
   finished project was never audited or reported (the run idled for 2.5 hours).
2. W1 sent 'run_command>' (stray tag character) six times: command_executor answered "Invalid action. Must be
   'run_command'", so the tests and the CLI were not run until 10:22.
3. 'list_with_lines' (4x), 'modify_task_progress', a task UUID with one wrong character, "depends": "".
"""
import asyncio
import json
import shutil
import sys
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.tools.executor import ToolExecutor

try:
    import tasklib  # noqa: F401
    HAVE_TW = shutil.which("task") is not None
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


def executor(tmp_path, tool, args, agent="W1"):
    manager = types.SimpleNamespace(agents={agent: types.SimpleNamespace(agent_type="worker", state="worker_work")})
    return run(ToolExecutor().execute_tool(agent, tmp_path, tool, dict(args, scope="private"), manager=manager))


# ============================================================================================ 1. finished project -> audit
def _listing_turn(call_id, name, args, tasks=None):
    turn = [{"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": args}]}]
    content = (json.dumps({"status": "success", "message": f"Found {len(tasks)} task(s).", "tasks": tasks})
               if tasks is not None else '{"status": "success_signal_to_handler", "action_to_perform": "list_agents"}')
    turn.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": content})
    return turn


# PM1's pm_manage visit of 2026-10-19 10:26:26-10:28:09 (history cleared on entering pm_manage)
OBSERVED_TURNS = [
    ("c1", "project_management", {"action": "list_tasks", "project_filter": "Temp_Converter_CLI"}, []),
    ("c2", "manage_team", {"action": "list_agents", "team_id": "team_Temp_Converter_CLI"}, None),
    ("c3", "project_management", {"action": "list_tasks", "task_progress_filter": "completed",
                                  "project_filter": "Temp_Converter_CLI"}, []),
    ("c4", "project_management", {"action": "list_tasks"}, []),
]


def _schedule(history, state="pm_manage"):
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    scheduled = []

    async def schedule_cycle(agent, retry):
        scheduled.append((agent.agent_id, agent.state))
        return None

    def change_state(agent, new_state):
        agent.state = new_state
        return True

    manager = types.SimpleNamespace(workflow_manager=types.SimpleNamespace(change_state=change_state),
                                    schedule_cycle=schedule_cycle)
    agent = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state=state, message_history=history,
                                  status="processing", set_status=lambda s: None)
    context = types.SimpleNamespace(agent=agent, all_tool_results=[], trigger_failover=False,
                                    needs_reactivation_after_cycle=False, is_retryable_error_type=False, retry_count=0,
                                    max_retries_for_cycle=3, cycle_completed_successfully=True,
                                    state_change_requested_this_cycle=False, executed_tool_successfully_this_cycle=True,
                                    action_taken_this_cycle=True, current_model_key_for_tracking="m")
    run(NextStepScheduler(manager).schedule_next_step(context))
    return agent, scheduled


def test_the_observed_finished_project_goes_to_audit_not_standby():
    history = [{"role": "system", "content": "pm_manage prompt"}]
    for call_id, name, args, tasks in OBSERVED_TURNS[:3]:
        history += _listing_turn(call_id, name, args, tasks)
    agent, scheduled = _schedule(history)                       # 10:27:57 - last list was filtered: ask for a full list
    assert agent.state == "pm_manage" and scheduled == [("PM1", "pm_manage")]
    assert "NO filters" in agent.message_history[-1]["content"]
    history = agent.message_history + _listing_turn(*OBSERVED_TURNS[3])
    agent, scheduled = _schedule(history)                       # 10:28:09 - full list, nothing open
    assert agent.state == "pm_audit" and scheduled == [("PM1", "pm_audit")]
    assert "moved you to 'pm_audit'" in agent.message_history[-1]["content"]


def test_the_list_all_tasks_message_is_given_once():
    history = [{"role": "system", "content": "p"}]
    for call_id, name, args, tasks in OBSERVED_TURNS[:3]:
        history += _listing_turn(call_id, name, args, tasks)
    agent, _ = _schedule(history)
    history = agent.message_history + _listing_turn("c5", "manage_team", {"action": "list_agents", "team_id": "t"})
    agent, scheduled = _schedule(history)
    texts = [m["content"] for m in agent.message_history if m.get("role") == "system"]
    assert sum("NO filters" in t for t in texts) == 1 and agent.state == "pm_manage"


def test_open_work_still_means_standby():
    tasks = [{"uuid": "t2", "assignee": "W2", "task_progress": "doing"}]
    history = [{"role": "system", "content": "p"}]
    for call_id, name, args, _ in OBSERVED_TURNS[:3]:
        history += _listing_turn(call_id, name, args, tasks if name == "project_management" else None)
    agent, scheduled = _schedule(history)
    assert agent.state == "pm_standby" and scheduled == []


def test_a_pm_that_never_lists_unfiltered_goes_to_standby_after_six_turns():
    history = [{"role": "system", "content": "p"}]
    for number in range(6):
        history += _listing_turn(f"c{number}", "project_management", {"action": "list_tasks", "assignee_filter": f"W{number}"}, [])
    agent, scheduled = _schedule(history)
    assert agent.state == "pm_standby" and scheduled == []


def test_the_pm_s_own_kickoff_task_does_not_count_as_open():
    from src.agents.tool_activity import pm_listing_status
    history = _listing_turn("c1", "project_management", {"action": "list_tasks"},
                            [{"uuid": "k", "assignee": "PM1", "task_progress": "decomposed"},
                             {"uuid": "x", "assignee": "PM1", "task_progress": "todo"}])
    assert pm_listing_status(history, "PM1") == "done"
    assert pm_listing_status([], "PM1") == "none"


def test_a_failed_read_is_never_answered_from_the_history():
    from src.agents.cycle_handler import AgentCycleHandler
    handler = object.__new__(AgentCycleHandler)
    read = {"action": "read_with_lines>", "path": "src/tests/test_converter.py"}
    agent = types.SimpleNamespace(message_history=[
        {"role": "system", "content": "p"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "r1", "name": "file_system", "arguments": read}]},
        {"role": "tool", "tool_call_id": "r1", "name": "file_system",
         "content": "[Tool Execution Failed] File not found: 'src/tests/test_converter.py'"},
    ])   # W2 created the file at 09:52; W1's history cannot show that
    assert handler._detect_cross_cycle_identical_failure(agent, "file_system", dict(read)) is None


# ============================================================================================ 2. 'run_command>'
def test_run_command_with_a_stray_tag_character_runs(tmp_path):
    command = f'{sys.executable} -c "print(21 * 2)"'
    result = executor(tmp_path, "command_executor", {"action": "run_command>", "command": command, "timeout": 30})
    text = result if isinstance(result, str) else json.dumps(result)
    assert "Invalid action" not in text and "42" in text, text


@pytest.mark.parametrize("args", [{"action": "execute"}, {"action": "run"}, {}])
def test_run_command_aliases_and_a_missing_action(tmp_path, args):
    command = f'{sys.executable} -c "print(6 * 7)"'
    result = executor(tmp_path, "command_executor", dict(args, command=command, timeout=30))
    text = result if isinstance(result, str) else json.dumps(result)
    assert "42" in text, text


def test_an_unknown_command_action_is_still_refused(tmp_path):
    result = executor(tmp_path, "command_executor", {"action": "deploy", "command": "echo x"})
    text = result if isinstance(result, str) else json.dumps(result)
    assert "Invalid action" in text


def test_the_executor_cleans_the_action_of_every_tool(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    result = executor(tmp_path, "file_system", {"action": "read>", "path": "a.txt"})
    assert "hello" in (result if isinstance(result, str) else json.dumps(result))


# ============================================================================================ 3. small gaps
def test_list_with_lines_lists_a_directory_and_reads_a_file(tmp_path):
    (tmp_path / "src" / "tests").mkdir(parents=True)
    (tmp_path / "src" / "core.py").write_text("x = 1\n")
    listed = executor(tmp_path, "file_system", {"action": "list_with_lines>", "path": "src/"})
    read = executor(tmp_path, "file_system", {"action": "list_with_lines", "path": "src/core.py"})
    listed, read = (r if isinstance(r, str) else json.dumps(r) for r in (listed, read))
    assert "Invalid action" not in listed and "tests" in listed
    assert "1: x = 1" in read


def test_uuid_typo_helper():
    from src.tools.project_management import _uuid_typo_match, match_task_by_uuid_prefix
    tasks = [{"uuid": "2b7e308d-3493-4baa-8c50-9a2b16784dff"}, {"uuid": "6aaaeeb6-f656-4b29-b2ab-ea6f77d5c8bc"}]
    assert _uuid_typo_match(tasks, "3b7e308d-3493-4baa-8c50-9a2b16784dff") is tasks[0]          # observed 10:13
    assert match_task_by_uuid_prefix(tasks, "3b7e308d-3493-4baa-8c50-9a2b16784dff") == [tasks[0]]
    assert _uuid_typo_match(tasks, "3b7e308d-3493-4baa-8c50-9a2b16784aaa") is None             # 4 characters differ
    assert _uuid_typo_match(tasks, "3b7e308d") is None                                          # not a full UUID
    twins = [{"uuid": "2b7e308d-3493-4baa-8c50-9a2b16784dff"}, {"uuid": "2b7e308d-3493-4baa-8c50-9a2b16784dfe"}]
    assert _uuid_typo_match(twins, "2b7e308d-3493-4baa-8c50-9a2b16784dfa") is None             # ambiguous


@pytest.fixture
def tw_env(tmp_path, monkeypatch):
    import src.tools.project_management as pm
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)
    tool = pm.ProjectManagementTool()

    def call(agent, **kw):
        return run(tool.execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P", session_name="S", **kw))
    return call


@needs_tw
def test_task_actions_seen_in_the_run(tw_env):
    task = tw_env("PM1", action="add_task", description="Kick-off Task 4: verify")
    uuid = task["task_uuid"]
    typo = ("3" if uuid[0] != "3" else "4") + uuid[1:]
    result = tw_env("PM1", action="modify_task_progress", task_id=typo, task_progress="in_progress")
    assert result["status"] == "success" and result["task_uuid"] == uuid, result
    result = tw_env("PM1", action="modify_task", task_id=uuid, depends="", priority="M")
    assert result["status"] == "success" and "depends" not in result["modified_fields"], result

# END OF FILE tests/unit/test_round2f.py
