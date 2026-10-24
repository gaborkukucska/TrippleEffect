# START OF FILE tests/unit/test_round3j.py
"""
Round 3j: the run of 2026-10-24 13:11-13:25 (WiFi_Snake_Game, 15 minutes).

* 13:15:53 Admin AI answered PM1's "kick-off tasks assigned" report by giving SEVEN tasks to W2 in one reply (W2 was busy;
  the busy-worker check covered only PMs).
* 13:22:48 PM1 moved "Kick-off Task 4: Integrate all components in src/main.py" from W2 to W1 without telling W2; W2 wrote
  src/main.py at 13:24:08 and W1's write of it was refused ("already exists").
* 13:24:24 and 13:25:37 PM1 was refused "An agent with the role 'Coder' already exists" right after the busy-worker refusal
  told it to create one more worker; the third failure in a row put PM1 into the error state.
* The Round 3i delivery worked: 3 queued messages reached busy agents within a step.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT, SESSION = "WiFi_Snake_Game", "startup_1792818670"

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait", role=None):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids, self.message_inbox = [], {}, []
        self.agent_config = {"config": {"role": role}} if role else {}


class Handler:
    def __init__(self):
        self.sent = []

    async def route_and_activate_agent_message(self, sender_id, target_identifier, message_content):
        self.sent.append((sender_id, target_identifier, message_content))
        return {"status": "queued", "message": "queued"}, None


def _manager(*agents, teams=None):
    teams = teams or {a.agent_id: f"team_{PROJECT}" for a in agents if a.agent_type != "admin"}
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        interaction_handler=Handler(),
        state_manager=types.SimpleNamespace(
            get_agent_team=lambda agent_id: teams.get(agent_id),
            get_agents_in_team=lambda team_id: [a for a in agents if teams.get(a.agent_id) == team_id]))


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    from src.config.settings import settings
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    (tmp_path / "projects" / PROJECT / SESSION / "shared_workspace").mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    return call


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_4 = ("Kick-off Task 4: Integrate all components in src/main.py as the entry point to start the server and handle "
          "game initialization")


# ======================================================================================== 1. the Admin AI and a PM's team
@needs_tw
def test_the_admin_ai_does_not_hand_out_the_tasks_of_a_pm_s_team(tasks):
    admin, pm1, w1, w2 = Agent("admin_ai", "admin", "admin_conversation"), Agent("PM1", "pm", "pm_manage"), Agent("W1"), \
        Agent("W2")
    manager = _manager(admin, pm1, w1, w2)
    t4 = _add(tasks, manager, TASK_4)
    refused = tasks("admin_ai", manager, action="modify_task", task_id=t4, assignee_agent_id="W2")
    assert refused["status"] == "error" and refused["error_type"] == "admin_assignment", refused
    assert refused["message"] == (
        f"Task '{t4}' was NOT changed: 'W2' works in the team of PM1, and giving tasks to its workers is PM1's job - PM1 "
        "knows which worker is free and what each one is doing. If something should change, tell PM1 with send_message "
        "(which task, and why).")
    created = tasks("admin_ai", manager, action="add_task", project_filter=PROJECT, description="Write docs/HOSTING.md",
                    assignee_agent_id="W2")
    assert created.get("error_type") == "admin_assignment", created
    # the PM may; a worker without a PM in its team may get a task from the Admin AI
    assert tasks("PM1", manager, action="modify_task", task_id=t4, assignee_agent_id="W2")["status"] == "success"
    solo = Agent("W9")
    manager2 = _manager(admin, solo, teams={"W9": "team_solo"})
    other = _add(tasks, manager2, "Write docs/NOTES.md")
    assert tasks("admin_ai", manager2, action="modify_task", task_id=other, assignee_agent_id="W9")["status"] == "success"


# ======================================================================================== 2. a reassigned task
@needs_tw
def test_the_worker_that_loses_a_task_is_told(tasks):
    pm1, w1, w2 = Agent("PM1", "pm", "pm_manage"), Agent("W1"), Agent("W2", state="worker_work")
    manager = _manager(pm1, w1, w2)
    t4 = _add(tasks, manager, TASK_4, "W2")
    w2.current_task_id = t4
    moved = tasks("PM1", manager, action="modify_task", task_id=t4, assignee_agent_id="W1")
    assert moved["status"] == "success", moved
    assert manager.interaction_handler.sent == [(
        "PM1", "W2",
        f"[Framework notice] The task '{TASK_4[:117]}...' ({t4}) was given to W1. It is no longer YOUR task: stop working on it and "
        "do not change its files - W1 does that now. Continue with your own task, or report to your PM if you have none.")]
    assert w2.current_task_id is None
    # the same assignee again, a first assignment, and the framework: nobody is told
    tasks("PM1", manager, action="modify_task", task_id=t4, assignee_agent_id="W1")
    t5 = _add(tasks, manager, "Kick-off Task 5: Write docs/README.md")
    tasks("PM1", manager, action="modify_task", task_id=t5, assignee_agent_id="W2")
    tasks("framework", manager, action="modify_task", task_id=t4, assignee_agent_id="W2")
    assert len(manager.interaction_handler.sent) == 1


# ======================================================================================== 3. one more worker of a busy role
def test_one_more_worker_of_a_role_whose_workers_are_all_busy(monkeypatch):
    import src.agents.team_scaling as ts
    w1, w2 = Agent("W1", role="Coder"), Agent("W2", role="Coder")
    busy = {"W1": ["task a"], "W2": ["task b"]}
    monkeypatch.setattr(ts, "_open_ready_tasks", lambda manager, worker_id: busy.get(worker_id, []))
    assert ts.second_worker_refusal(None, "Coder", [w1, w2], [w1, w2]) is None
    busy["W2"] = []
    refusal = ts.second_worker_refusal(None, "Coder", [w1, w2], [w1, w2])
    assert refusal == ("Agent creation failed: an agent with the role 'Coder' already exists in your team (W1, W2). W2 "
                       "has no open task: give the task to W2 instead of creating another 'Coder'.")
    team = [w1, w2, Agent("W3", role="Tester"), Agent("W4", role="Writer")]
    busy["W2"] = ["task b"]
    assert "Your team already has 4 workers" in ts.second_worker_refusal(None, "Coder", [w1, w2], team)
    monkeypatch.setattr(ts, "_open_ready_tasks", lambda manager, worker_id: None)        # tasks cannot be read
    assert ts.second_worker_refusal(None, "Coder", [w1], [w1]).endswith("Do not create duplicate roles. Use 'list_agents' "
                                                                       "to see your current team.")


@needs_tw
def test_the_busy_check_reads_the_task_list(tasks):
    from src.agents.team_scaling import second_worker_refusal
    pm1, w1 = Agent("PM1", "pm", "pm_manage"), Agent("W1", role="Coder")
    manager = _manager(pm1, w1)
    assert "W1 has no open task" in second_worker_refusal(manager, "Coder", [w1], [w1])
    _add(tasks, manager, TASK_4, "W1")
    assert second_worker_refusal(manager, "Coder", [w1], [w1]) is None


def test_create_agent_uses_the_scaling_rule():
    import inspect
    from src.agents import interaction_handler
    source = inspect.getsource(interaction_handler.AgentInteractionHandler._handle_create_agent)
    assert "second_worker_refusal(self._manager, role_requested, same_role, agents_to_check)" in source


# ======================================================================================== 4. prompts
def test_the_admin_prompts_leave_task_assignment_to_the_pm():
    import yaml
    prompts = yaml.safe_load((REPO / "prompts.yaml").read_text(encoding="utf-8"))
    assert "**CRITICAL - NO TASK ASSIGNMENT:** Never give or move tasks to workers" in prompts["admin_ai_conversation_prompt"]
    assert "Never assign or move tasks to workers yourself - that is the PM's job." in prompts["admin_work_prompt"]

# END OF FILE tests/unit/test_round3j.py
