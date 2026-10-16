"""Round 1s tests (Temp_Convert_CLI run 2026-10-16 08:47-09:45).

1. Overlapping Constitutional Guardian reviews left the Guardian showing 'processing' forever.
2. PM1 went to pm_standby without listing the tasks although every task was finished: no audit, no report to Admin.
3. W1 called file_system with action 'read_with_lines>' (stray '>'), which was rejected three times.
4. The Admin plan contained a wrong formula ('(f - 32) / 187') that was copied into the code.
"""
import asyncio
import json
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.constants import AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING
from src.agents.tool_activity import PM_STANDBY_GUARD_TAG, pm_standby_block_message

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------------------------------------ CG status race
class CGProvider:
    """stream_completion that blocks until the test releases it, so reviews can overlap."""

    def __init__(self):
        self.gates = []

    async def stream_completion(self, **kwargs):
        gate = asyncio.Event()
        self.gates.append(gate)
        await gate.wait()
        yield {"type": "response_chunk", "content": "<OK/>"}


class CGAgent:
    def __init__(self):
        self.status = AGENT_STATUS_IDLE
        self.llm_provider = CGProvider()
        self.model = "m"
        self.temperature = 0.1

    def set_status(self, status):
        self.status = status


def make_handler(monkeypatch, cg):
    from src.agents import cycle_handler as ch

    monkeypatch.setattr(ch.settings, "GOVERNANCE_PRINCIPLES", [], raising=False)
    monkeypatch.setattr(ch.settings, "PROMPTS", {"cg_system_prompt": "{governance_principles_text}{team_wip_updates}"}, raising=False)
    manager = types.SimpleNamespace(
        agents={ch.CONSTITUTIONAL_GUARDIAN_AGENT_ID: cg},
        workflow_manager=types.SimpleNamespace(_build_team_wip_updates=lambda agent, manager: ""),
    )

    async def push(agent_id):
        return None

    manager.push_agent_status_update = push
    handler = object.__new__(ch.AgentCycleHandler)
    handler._manager = manager
    return handler


def test_overlapping_cg_reviews_leave_the_guardian_idle(monkeypatch):
    cg = CGAgent()
    handler = make_handler(monkeypatch, cg)
    agent = types.SimpleNamespace(agent_id="admin_ai")

    async def scenario():
        review_a = asyncio.create_task(handler._get_cg_verdict(agent, "text A"))
        await asyncio.sleep(0.01)
        assert cg.status == AGENT_STATUS_PROCESSING
        review_b = asyncio.create_task(handler._get_cg_verdict(agent, "text B"))   # starts while A still runs
        await asyncio.sleep(0.01)
        cg.llm_provider.gates[0].set()                                             # A finishes first
        assert await review_a == "<OK/>"
        await asyncio.sleep(0.01)
        assert cg.status == AGENT_STATUS_PROCESSING                                # B is still running
        cg.llm_provider.gates[1].set()
        assert await review_b == "<OK/>"

    asyncio.run(scenario())
    assert cg.status == AGENT_STATUS_IDLE


def test_a_single_cg_review_still_restores_the_previous_status(monkeypatch):
    cg = CGAgent()
    handler = make_handler(monkeypatch, cg)

    async def scenario():
        task = asyncio.create_task(handler._get_cg_verdict(types.SimpleNamespace(agent_id="a"), "hello"))
        await asyncio.sleep(0.01)
        cg.llm_provider.gates[0].set()
        await task

    asyncio.run(scenario())
    assert cg.status == AGENT_STATUS_IDLE and cg._cg_active_reviews == 0


# ------------------------------------------------------------------------------------------------ PM standby guard
def listing(*tasks):
    return {"role": "tool", "content": json.dumps({"status": "success", "message": f"Found {len(tasks)} task(s).", "tasks": list(tasks)})}


def task(progress, assignee="W1"):
    return {"uuid": "u", "description": "d", "task_progress": progress, "assignee": assignee}


def guard(history, requested="pm_standby", state="pm_manage", agent_type="pm"):
    return pm_standby_block_message(agent_type, state, requested, history, "PM1")


def test_standby_without_a_task_listing_is_blocked_once():
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    message = guard(history)
    assert message and message.startswith(PM_STANDBY_GUARD_TAG) and "list_tasks" in message and "pm_audit" in message
    history.append({"role": "system", "content": message})
    assert guard(history) is None                      # second request is let through (no endless loop)


def test_standby_when_nothing_is_open_is_redirected_to_audit():
    for empty in ([], [task("finished"), task("decomposed")]):
        message = guard([listing(*empty)])
        assert message and "pm_audit" in message and "NO open tasks" in message


def test_standby_is_allowed_while_work_is_open():
    for progress in ("todo", "in_progress", "doing"):
        assert guard([listing(task("finished"), task(progress))]) is None


def test_tasks_assigned_to_the_pm_itself_are_ignored():
    assert guard([listing(task("todo", assignee="PM1"))]) is not None


def test_the_most_recent_listing_wins():
    history = [listing(task("todo")), {"role": "assistant", "content": "ok"}, listing(task("finished"))]
    assert guard(history) is not None
    assert guard(list(reversed(history))) is None


def test_garbage_tool_results_do_not_crash_the_guard():
    history = [{"role": "tool", "content": '"tasks" {not json'}, {"role": "tool", "content": None}, "junk", {"role": "tool"}]
    assert guard(history) is not None                   # no usable listing -> must look at the tasks first


@pytest.mark.parametrize("args", [
    dict(requested="pm_manage"), dict(requested="pm_audit"), dict(state="pm_standby"), dict(state="pm_audit"),
    dict(agent_type="worker"), dict(agent_type="admin"),
])
def test_other_transitions_are_never_touched(args):
    assert guard([], **args) is None


# ------------------------------------------------------------------------------------------------ file_system action
def fs_execute(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, **kwargs))


@pytest.mark.parametrize("action", ["read_with_lines>", "<read_with_lines>", " Read_With_Lines ", "read>", '"read"', "READ"])
def test_malformed_read_actions_are_cleaned_up(tmp_path, action):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = fs_execute(tmp_path, action=action, path="a.py", scope="private")
    assert result["status"] == "success", result
    assert "x = 1" in str(result)


def test_unknown_and_missing_actions_still_fail_cleanly(tmp_path):
    assert "Invalid action 'frobnicate'" in fs_execute(tmp_path, action="frobnicate>", path="a.py", scope="private")["message"]
    assert "Missing required 'action'" in fs_execute(tmp_path, action="<>", path="a.py", scope="private")["message"]


# ------------------------------------------------------------------------------------------------ prompts
def test_prompts_ban_formulas_and_separate_standby_from_audit():
    yaml = pytest.importorskip("yaml")
    prompts = yaml.safe_load((ROOT / "prompts.yaml").read_text(encoding="utf-8"))
    for key, text in prompts.items():
        if isinstance(text, str):
            text.format_map(type("B", (dict,), {"__missing__": lambda self, k: ""})())
    assert "do NOT write formulas, equations, constants or code" in prompts["admin_ai_planning_prompt"]
    assert "Do NOT write formulas, algorithms or code" in prompts["pm_startup_prompt"]
    manage = prompts["pm_manage_prompt"]
    assert "only while work is still open" in manage and "request pm_audit" in manage
