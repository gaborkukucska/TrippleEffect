# START OF FILE tests/unit/test_round3b.py
"""
Round 3b: more work on an EXISTING project, from the 2026-10-22 16:41-20:14 run (OpenRouter nvidia/nemotron-3-ultra-550b).

* 17:24 PM1 and its team (W1, W2) had built a working network snake game, 'Multiplayer_Snake_Game'.
* 20:12:21 the user tested it and asked for changes ("Let's now make it better ..."). Admin AI read the game's files and
  wrote a good plan, titled "Multiplayer Snake Game Enhancements", that began "Now I have a thorough understanding of the
  codebase. Let me create the project plan.".
* 20:13:34 the plan workflow only knew NEW projects: it created PM2; the approval (20:14:15) switched to the new, EMPTY
  project 'Multiplayer_Snake_Game_Enhancements' - a copy without the game's files, while PM1 and its team sat idle.
* 20:14:31 a daily rate limit (429) hit PM2; every local model failed with "Provider class not found for 'ollama-local'".

Now a plan for a running project is an update of it: its PM gets the update, the user approves it, the PM plans the
update's tasks with the project's files and gives them to its existing team. Plus: the preamble is cut from plans, Admin
AI's address book says which projects run, the failover reaches 'ollama-local' (after the same model's other keys), and
collect.py exports every project session of the log.
"""
import asyncio
import json
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT = "Multiplayer_Snake_Game"
SESSION = "startup_1792658476"

PLAN_20_13_34 = """Now I have a thorough understanding of the codebase. Let me create the project plan.

**Objective:** Fix mobile touch controls visibility, add settings screen with "walled edges" toggle, implement multiple food types with varied effects, and sync configuration between client and server.

**Tech stack:** Node.js, Express, Socket.io, Vanilla JavaScript (ES Modules), HTML5 Canvas, CSS3, Jest for testing.

**Tasks:**
1. **Fix mobile touch controls visibility:** Debug and fix the CSS/JS issue preventing touch controls from appearing on mobile devices.
2. **Add settings screen UI:** Create a new settings screen accessible from the name-entry screen with a "Walled edges" toggle.
3. **Write tests and verify:** Add unit tests for new food types, ghost mode, walled edges collision logic, and config handling."""


def run(coro):
    return asyncio.run(coro)


# ======================================================================================== 1. the plan's preamble
def test_the_preamble_of_20_13_34_is_removed():
    from src.workflows.project_update import strip_plan_preamble
    plan, removed = strip_plan_preamble(PLAN_20_13_34)
    assert plan.startswith("**Objective:** Fix mobile touch controls visibility")
    assert removed == "Now I have a thorough understanding of the codebase. Let me create the project plan."


@pytest.mark.parametrize("text", [
    "**Objective:** A word counter.\n\n**Tasks:**\n1. Count.",                          # starts with the plan
    "- a list item before\n\n**Objective:** X",                                            # a list is never cut
    "Objective first? No:\n\n" + "x" * 500 + "\n\n**Objective:** X",                       # too long to be a preamble
])
def test_a_plan_without_a_short_preamble_stays(text):
    from src.workflows.project_update import strip_plan_preamble
    plan, removed = strip_plan_preamble(text)
    assert removed == "" and plan == text.strip()


def test_an_announcement_without_an_objective_line_is_removed():
    from src.workflows.project_update import strip_plan_preamble
    plan, removed = strip_plan_preamble("Here is the plan.\n\n1. **Core:** do it.\n2. **Tests:** test it.")
    assert removed == "Here is the plan." and plan.startswith("1. **Core:**")
    assert strip_plan_preamble("The game needs settings.\n\n1. Do it.") == ("The game needs settings.\n\n1. Do it.", "")


# ======================================================================================== 2. which project a plan updates
class FakeAgent:
    def __init__(self, agent_id, agent_type="pm", project=PROJECT, state="pm_standby", status="idle", awaiting=False,
                 role=None, persona=None, sandbox=None):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, status
        self._awaiting_project_approval = awaiting
        self.persona = persona or f"Project Manager (@pm_agent) ({project})"
        self.role = role
        self.sandbox_path = sandbox or Path("/tmp")
        self.message_history = [{"role": "user", "content": "old work"}]
        self.agent_config = {"config": {"project_name_context": project, "session_name": SESSION,
                                        "initial_plan_description": "**Objective:** a network snake game"}}
        self.initial_plan_description = "**Objective:** a network snake game"
        self._last_system_prompt_state = state

    def clear_history(self):
        self.message_history = []

    def set_status(self, status):
        self.status = status


def _agents(*agents):
    return {agent.agent_id: agent for agent in agents}


def test_the_plan_names_the_project():
    from src.workflows.project_update import find_update_target
    pm1 = FakeAgent("PM1")
    manager = types.SimpleNamespace(agents=_agents(pm1))
    project, pm, why = find_update_target(manager, {"title": "Settings", "project": "Multiplayer Snake Game"}, "Settings")
    assert (project, pm) == (PROJECT, pm1) and why == "the plan names it ('project': 'Multiplayer Snake Game')"


def test_the_title_of_20_13_34_starts_with_the_project_name():
    from src.workflows.project_update import find_update_target
    pm1 = FakeAgent("PM1")
    manager = types.SimpleNamespace(agents=_agents(pm1))
    project, pm, why = find_update_target(manager, {"title": "x"}, "Multiplayer_Snake_Game_Enhancements")
    assert (project, pm) == (PROJECT, pm1) and "starts with the project's name" in why
    assert find_update_target(manager, {}, "multiplayer_snake_game")[0] == PROJECT        # the same name


def test_no_update_without_a_running_project():
    from src.workflows.project_update import find_update_target
    waiting = FakeAgent("PM1", awaiting=True, state="pm_startup")                        # its plan was never approved
    other = FakeAgent("PM2", project="Word_Stats_CLI")
    manager = types.SimpleNamespace(agents=_agents(waiting, other))
    assert find_update_target(manager, {"project": PROJECT}, "Multiplayer_Snake_Game_Enhancements") is None
    assert find_update_target(manager, {}, "Snake_Game") is None
    assert find_update_target(manager, {}, "Word_Stats_CLIX") is None                      # not a '_' word boundary


def test_the_longest_project_name_wins():
    from src.workflows.project_update import find_update_target
    short, long_ = FakeAgent("PM1", project="Snake"), FakeAgent("PM2", project="Snake_Game")
    manager = types.SimpleNamespace(agents=_agents(short, long_))
    assert find_update_target(manager, {}, "Snake_Game_Settings")[1] is long_


# ======================================================================================== 3. the plan becomes an update
class Executor:
    def __init__(self):
        self.calls = []
        self.uuids = iter(["55920601-4c8b-4072-89cc-f03d55d60bd3", "6a1d4a5e-1111-4c8b-9a36-2b2b8c7e1f00",
                           "7b2e5b6f-2222-4c8b-9a36-2b2b8c7e1f00"])

    async def execute_tool(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["tool_args"]["action"] == "add_task":
            return {"status": "success", "task_id": len(self.calls), "task_uuid": next(self.uuids)}
        return {"status": "success"}


def _workspace(tmp_path):
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    for name, text in {"server.js": "import express from 'express';\n", "gameLogic.js": "export const x = 1;\n",
                       "public/client.js": "// client\n", "tests/gameLogic.test.js": "test('x', () => {});\n",
                       "PROJECT_PLAN.md": "# Project Plan: Multiplayer_Snake_Game\n\n**Objective:** a network snake game\n",
                       "PROJECT_STRUCTURE.md": "# Project Directory Structure\n", "node_modules/x/index.js": "x",
                       ".te_background/bg_1.log": "log", "coverage/lcov.info": "TN:", "src/coverage/report.js": "//"}.items():
        (ws / name).parent.mkdir(parents=True, exist_ok=True)
        (ws / name).write_text(text)
    return ws


def _manager(tmp_path, monkeypatch, *agents):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    admin = FakeAgent("admin_ai", agent_type="admin", project=None, state="planning")
    admin.message_history = []
    admin.agent_config = {"config": {}}
    workers = [a for a in agents if a.agent_type == "worker"]
    ui, scheduled, contexts, created = [], [], [], []

    async def send_to_ui(payload):
        ui.append(payload)

    async def schedule_cycle(agent, retry_count=0):
        scheduled.append(agent.agent_id)

    async def set_context(project, session, loading=False):
        contexts.append((project, session))
        manager.current_project, manager.current_session = project, session

    async def create_agent_instance(agent_id_requested, **kwargs):
        pm = FakeAgent(agent_id_requested, project=kwargs["project_name_context"], state=None, sandbox=tmp_path)
        pm.agent_config["config"]["initial_plan_description"] = kwargs.get("initial_plan_description")
        manager.agents[agent_id_requested] = pm
        created.append(agent_id_requested)
        return True, "created", agent_id_requested

    def change_state(agent, state):
        agent.state = state
        return True

    def pending_approval_pms(project_name=None):
        from src.agents.manager import AgentManager
        return AgentManager.pending_approval_pms(manager, project_name)

    manager = types.SimpleNamespace(
        agents={"admin_ai": admin, **_agents(*agents)}, current_project=PROJECT, current_session=SESSION,
        current_session_db_id=None, tool_executor=Executor(), send_to_ui=send_to_ui, schedule_cycle=schedule_cycle,
        set_project_session_context=set_context, create_agent_instance=create_agent_instance,
        pending_approval_pms=pending_approval_pms, workflow_manager=types.SimpleNamespace(change_state=change_state),
        state_manager=types.SimpleNamespace(
            get_agent_team=lambda agent_id: f"team_{PROJECT}" if agent_id in ("PM1", "W1", "W2") else None,
            get_agents_in_team=lambda team_id: [manager.agents["PM1"], *workers] if team_id == f"team_{PROJECT}" else []))
    manager.ui, manager.scheduled, manager.contexts, manager.created = ui, scheduled, contexts, created
    return manager, admin


def _team(tmp_path):
    pm1 = FakeAgent("PM1", sandbox=tmp_path)
    w1 = FakeAgent("W1", agent_type="worker", state="worker_wait", role="Coder", persona="Coder")
    w2 = FakeAgent("W2", agent_type="worker", state="worker_wait", role="QA_Tester", persona="QA_Tester")
    return pm1, w1, w2


def _plan(manager, admin, data):
    from src.workflows.project_creation_workflow import ProjectCreationWorkflow
    return run(ProjectCreationWorkflow().execute(manager, admin, data))


def test_the_plan_of_20_13_34_is_an_update_of_the_game(tmp_path, monkeypatch):
    pm1, w1, w2 = _team(tmp_path)
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    result = _plan(manager, admin, {"title": "Multiplayer Snake Game Enhancements", "_raw_plan_body_": PLAN_20_13_34})
    assert result.success and result.workflow_name == "project_creation"
    assert result.next_agent_state == "admin_conversation"
    assert manager.created == [] and set(manager.agents) == {"admin_ai", "PM1", "W1", "W2"}       # no PM2, no copy
    task = manager.tool_executor.calls[0]
    assert task["project_name"] == PROJECT and task["session_name"] == SESSION
    assert task["tool_args"]["project_filter"] == PROJECT and task["tool_args"]["assignee_agent_id"] == "PM1"
    assert task["tool_args"]["description"].startswith(
        "PROJECT UPDATE 1: Multiplayer Snake Game Enhancements\n\nUpdate Plan:\n**Objective:** Fix mobile touch controls")
    assert {"project_kickoff", "project_update", "update_1"} <= set(task["tool_args"]["tags"])
    update = pm1._pending_update
    assert update["number"] == 1 and update["project"] == PROJECT and update["session"] == SESSION
    assert update["plan"].startswith("**Objective:**") and "Let me create" not in update["plan"]
    card = result.ui_message_data
    assert card["type"] == "project_pending_approval" and card["pm_agent_id"] == "PM1" and card["project_title"] == PROJECT
    assert card["update"] == {"number": 1, "title": "Multiplayer Snake Game Enhancements", "project": PROJECT,
                              "workers": ["W1 (Coder)", "W2 (QA_Tester)"]}
    assert pm1.state == "pm_standby" and pm1.message_history == [{"role": "user", "content": "old work"}]  # nothing started
    note = admin.message_history[-1]["content"]
    assert note.startswith("[Framework Note]: Your plan 'Multiplayer Snake Game Enhancements' is UPDATE 1 of the existing "
                           "project 'Multiplayer_Snake_Game'. The user approves it on the approval card (or by typing "
                           "continue; new starts it as a separate project); then PM1 and its team (W1 (Coder), W2 (QA_Tester)) "
                           "make the changes in the project's own workspace.")
    assert "You do NOT plan it again, create a new project or start the work yourself." in note


def test_a_new_project_is_still_a_new_project(tmp_path, monkeypatch):
    pm1, w1, w2 = _team(tmp_path)
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    result = _plan(manager, admin, {"title": "Word Stats CLI", "_raw_plan_body_": "**Objective:** count words"})
    assert result.success and manager.created == ["PM2"] and not hasattr(pm1, "_pending_update")
    assert manager.agents["PM2"].agent_config["config"]["project_name_context"] == "Word_Stats_CLI"


def test_a_second_update_plan_replaces_the_waiting_one(tmp_path, monkeypatch):
    pm1, w1, w2 = _team(tmp_path)
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    _plan(manager, admin, {"title": "Settings", "project": PROJECT, "_raw_plan_body_": "**Objective:** settings"})
    first_task = pm1._pending_update["task_uuid"]
    result = _plan(manager, admin, {"title": "Settings And Food", "project": PROJECT, "_raw_plan_body_": "**Objective:** more"})
    assert {"action": "modify_task", "task_id": first_task, "task_progress": "deleted"} in [
        c["tool_args"] for c in manager.tool_executor.calls]
    assert pm1._pending_update["title"] == "Settings And Food" and pm1._pending_update["number"] == 1
    assert "It replaces the earlier update plan 'Settings', which was never approved." in result.message


# ======================================================================================== 4. the user approves the update
def _approve(manager, pm_id, mode=None):
    from src.api.http_routes import approve_project_start

    async def go():
        response = await approve_project_start(pm_id, mode, manager)
        await asyncio.sleep(0)
        return response
    return run(go())


def test_the_approved_update_goes_to_pm1_and_its_team(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    pm1, w1, w2 = _team(tmp_path)
    pm1._project_completed, pm1._pm_audit_attempt_count, pm1._audit_refusals = True, 2, {3: 1}
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    _plan(manager, admin, {"title": "Multiplayer Snake Game Enhancements", "_raw_plan_body_": PLAN_20_13_34})
    response = _approve(manager, "PM1")
    text = ("Update 1 of project 'Multiplayer_Snake_Game' approved: PM1 and its team (W1 (Coder), W2 (QA_Tester)) now plan "
            "and make the changes in the project's own workspace.")
    assert response.success and response.message == text
    assert manager.ui[-2] == {"type": "project_approved", "pm_agent_id": "PM1", "message": text}
    assert manager.ui[-1] == {"type": "final_response", "agent_id": "admin_ai", "content": text}
    assert manager.contexts == [] and manager.current_project == PROJECT                            # the same project
    assert pm1.state == "pm_startup" and pm1.message_history == [] and manager.scheduled == ["PM1"]
    assert pm1._pending_update is None and pm1._project_update_count == 1
    assert pm1._project_update["number"] == 1 and pm1._project_update["task_uuid"]
    assert (pm1._project_completed, pm1._pm_audit_attempt_count, pm1._audit_refusals) == (False, 0, {})
    overview = pm1.initial_plan_description
    assert overview.startswith("PROJECT UPDATE 1 of the EXISTING project 'Multiplayer_Snake_Game': "
                               "Multiplayer Snake Game Enhancements")
    assert "your team already exists (W1 (Coder), W2 (QA_Tester))" in overview
    assert "Update plan:\n**Objective:** Fix mobile touch controls" in overview
    existing = overview.split("Existing project files:\n", 1)[1].splitlines()
    assert existing == ["- gameLogic.js", "- public/client.js", "- server.js", "- src/coverage/report.js",
                        "- tests/gameLogic.test.js"]                     # not node_modules/, .te_background/, coverage/
    plan_md = (ws / "PROJECT_PLAN.md").read_text()
    assert plan_md.startswith("# Project Plan: Multiplayer_Snake_Game\n\n**Objective:** a network snake game\n")
    assert "\n\n## Update 1: Multiplayer Snake Game Enhancements\n\n**Objective:** Fix mobile touch controls" in plan_md
    assert sorted(p.name for p in (tmp_path / "projects").iterdir()) == [PROJECT]                   # no copy


def test_the_update_switches_to_its_project(tmp_path, monkeypatch):
    _workspace(tmp_path)
    pm1, w1, w2 = _team(tmp_path)
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    _plan(manager, admin, {"title": "Settings", "project": PROJECT, "_raw_plan_body_": "**Objective:** settings"})
    manager.current_project = "Word_Stats_CLI"                                                     # another project ran last
    assert _approve(manager, "PM1", "update").success
    assert manager.contexts == [(PROJECT, SESSION)]


def test_a_busy_pm_does_not_start_the_update(tmp_path, monkeypatch):
    import src.workflows.project_update as project_update
    from fastapi import HTTPException
    monkeypatch.setattr(project_update, "UPDATE_START_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(project_update, "UPDATE_POLL_SECONDS", 0.01)
    pm1, w1, w2 = _team(tmp_path)
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    _plan(manager, admin, {"title": "Settings", "project": PROJECT, "_raw_plan_body_": "**Objective:** settings"})
    pm1.status = "processing"
    with pytest.raises(HTTPException) as err:
        _approve(manager, "PM1", "update")
    assert err.value.status_code == 409 and "PM1 is still busy" in err.value.detail
    assert pm1._pending_update is not None and pm1.state == "pm_standby"                           # nothing changed
    assert "Approve it again in a minute." in manager.ui[-1]["content"]


def test_the_update_starts_when_the_pm_finishes_its_step(tmp_path, monkeypatch):
    import src.workflows.project_update as project_update
    monkeypatch.setattr(project_update, "UPDATE_POLL_SECONDS", 0.01)
    _workspace(tmp_path)
    pm1, w1, w2 = _team(tmp_path)
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    _plan(manager, admin, {"title": "Settings", "project": PROJECT, "_raw_plan_body_": "**Objective:** settings"})
    pm1.status = "processing"

    async def go():
        from src.api.http_routes import approve_project_start

        async def finish():
            await asyncio.sleep(0.05)
            pm1.status = "idle"
        asyncio.create_task(finish())
        return await approve_project_start("PM1", "update", manager)
    assert run(go()).success and pm1.state == "pm_startup"


def test_the_update_can_start_as_a_new_project(tmp_path, monkeypatch):
    _workspace(tmp_path)
    pm1, w1, w2 = _team(tmp_path)
    manager, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    _plan(manager, admin, {"title": "Multiplayer Snake Game Enhancements", "_raw_plan_body_": PLAN_20_13_34})
    update_task = pm1._pending_update["task_uuid"]
    response = _approve(manager, "PM1", "new")
    assert response.success and manager.created == ["PM2"]
    pm2 = manager.agents["PM2"]
    assert pm2.agent_config["config"]["project_name_context"] == "Multiplayer_Snake_Game_Enhancements"
    assert pm2._awaiting_project_approval is False and manager.scheduled == ["PM2"]               # approved right away
    assert manager.current_project == "Multiplayer_Snake_Game_Enhancements"
    assert pm1._pending_update is None and pm1.state == "pm_standby"
    assert {"action": "modify_task", "task_id": update_task, "task_progress": "deleted"} in [
        c["tool_args"] for c in manager.tool_executor.calls]
    chat = [m["content"] for m in manager.ui if m.get("type") == "final_response"]
    assert chat == ["The plan starts as the new project 'Multiplayer_Snake_Game_Enhancements' (PM2).",
                    "Project 'Multiplayer_Snake_Game_Enhancements' managed by 'PM2' approved and started."]


def test_the_typed_approval_echo_does_not_reach_admin(tmp_path, monkeypatch):
    """The card's click also sends 'approve project PM1' to the chat: it must not reach Admin AI for an update either."""
    from src.agents.manager import AgentManager
    pm1, w1, w2 = _team(tmp_path)
    manager = object.__new__(AgentManager)
    admin = FakeAgent("admin_ai", agent_type="admin", project=None, state="admin_conversation")
    admin.message_history = []
    manager.agents = _agents(admin, pm1, w1, w2)
    manager.current_project, manager.current_session, manager.current_session_db_id = PROJECT, SESSION, None

    async def set_context(project, session, loading=False):
        manager.current_project, manager.current_session = project, session
    manager.set_project_session_context = set_context
    pm1._pending_update = {"number": 1}
    run(manager.handle_user_message("approve project PM1"))
    assert admin.message_history == []


def test_typed_continue_approves_the_waiting_update(tmp_path, monkeypatch):
    from src.agents.manager import AgentManager
    _workspace(tmp_path)
    pm1, w1, w2 = _team(tmp_path)
    helper, admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    _plan(helper, admin, {"title": "Settings", "project": PROJECT, "_raw_plan_body_": "**Objective:** settings"})
    manager = object.__new__(AgentManager)
    for name in ("agents", "current_project", "current_session", "current_session_db_id", "tool_executor", "send_to_ui",
                 "schedule_cycle", "set_project_session_context", "workflow_manager", "state_manager"):
        setattr(manager, name, getattr(helper, name))

    async def go():
        await manager.handle_user_message("continue")
        await asyncio.sleep(0)
    run(go())
    assert pm1.state == "pm_startup" and helper.scheduled == ["PM1"] and pm1._pending_update is None
    assert helper.ui[-1]["content"].startswith("Update 1 of project 'Multiplayer_Snake_Game' approved: PM1")
    assert all(m.get("content") != "continue" for m in admin.message_history)                    # not sent to Admin AI


# ======================================================================================== 5. the PM plans the update
def _kickoff(tmp_path, monkeypatch, pm, manager, plan):
    import src.tools.project_management as pmt
    import src.workflows.pm_kickoff_workflow as pkw
    monkeypatch.setattr(pmt, "TASKLIB_AVAILABLE", False)

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)
    return run(pkw.PMKickoffWorkflow().execute(manager, pm, plan))


UPDATE_KICKOFF = {"roles": ["Coder"], "tasks": [
    {"id": "task_1", "description": "Fix the touch controls in public/style.css and public/client.js"},
    {"id": "task_2", "depends_on": "task_1", "description": "Add the settings screen to public/index.html and public/settings.js"},
    {"id": "task_3", "depends_on": ["task_1", "task_2"], "description": "Run all tests and the server to verify everything works"}],
    "code_base_definitions": "Node.js, Express, Socket.io, Jest", "project_structure": [{"dir": "public", "files": ["settings.js"]}]}


def test_the_update_tasks_go_to_the_existing_team(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    pm1, w1, w2 = _team(tmp_path)
    manager, _admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    pm1._project_update = {"number": 1, "title": "Enhancements", "task_uuid": "55920601-4c8b-4072-89cc-f03d55d60bd3"}
    pm1.state = "pm_startup"
    result = _kickoff(tmp_path, monkeypatch, pm1, manager, UPDATE_KICKOFF)
    assert result.success, result.message
    assert result.next_agent_state == "pm_activate_workers" and result.tasks_to_schedule == [(pm1, 0)]
    assert result.message.startswith("Update 1: all 3 tasks created by PM 'PM1'; its team (W1 (Coder), W2 (QA_Tester)) "
                                     "carries them out.")
    added = [c["tool_args"] for c in manager.tool_executor.calls if c["tool_args"]["action"] == "add_task"]
    assert [a["description"] for a in added] == [
        "Update 1 Task 1: Fix the touch controls in public/style.css and public/client.js",
        "Update 1 Task 2: Add the settings screen to public/index.html and public/settings.js",
        "Update 1 Task 3: Run all tests and the server to verify everything works"]
    assert all("update_1" in a["tags"] and "kickoff" in a["tags"] for a in added)
    assert added[2]["depends"] == "task_1,task_2"
    assert pm1._project_update is None and pm1._last_project_update["number"] == 1
    md = (ws / "PROJECT_STRUCTURE.md").read_text()
    to_create = md.split("## Files To Create", 1)[1].split("## Existing Files", 1)[0]
    existing = md.split("## Existing Files", 1)[1]
    assert "- public/settings.js\n" in to_create and "- public/index.html\n" in to_create
    assert existing.startswith("\n\nThese files ALREADY EXIST (the project's current files): read a file before you change "
                               "it; never create one of them again or make a copy of it:")
    for name in ("server.js", "gameLogic.js", "public/client.js", "tests/gameLogic.test.js"):
        assert f"- {name}\n" in existing
    assert "node_modules" not in md and "PROJECT_PLAN.md" not in existing


def test_an_update_needs_no_roles_when_the_team_exists(tmp_path, monkeypatch):
    _workspace(tmp_path)
    pm1, w1, w2 = _team(tmp_path)
    manager, _admin = _manager(tmp_path, monkeypatch, pm1, w1, w2)
    pm1._project_update = {"number": 2, "title": "Food Types"}
    plan = dict(UPDATE_KICKOFF, roles=[])
    result = _kickoff(tmp_path, monkeypatch, pm1, manager, plan)
    assert result.success and result.next_agent_state == "pm_activate_workers"


def test_an_update_without_workers_builds_the_team_first(tmp_path, monkeypatch):
    _workspace(tmp_path)
    pm1 = FakeAgent("PM1", sandbox=tmp_path)
    manager, _admin = _manager(tmp_path, monkeypatch, pm1)
    pm1._project_update = {"number": 1, "title": "Enhancements"}
    result = _kickoff(tmp_path, monkeypatch, pm1, manager, UPDATE_KICKOFF)
    assert result.success and result.next_agent_state == "pm_build_team_tasks"
    assert pm1.kick_off_roles == ["Coder"] and pm1._project_update is None
    no_roles = _kickoff(tmp_path, monkeypatch, pm1, manager, dict(UPDATE_KICKOFF, roles=[]))
    assert no_roles.success is False                                                             # nobody would work


def test_a_new_project_kickoff_is_unchanged(tmp_path, monkeypatch):
    pm1 = FakeAgent("PM1", sandbox=tmp_path, state="pm_startup")
    manager, _admin = _manager(tmp_path, monkeypatch, pm1)
    result = _kickoff(tmp_path, monkeypatch, pm1, manager, UPDATE_KICKOFF)
    assert result.success and result.next_agent_state == "pm_build_team_tasks"
    added = [c["tool_args"] for c in manager.tool_executor.calls if c["tool_args"]["action"] == "add_task"]
    assert added[0]["description"].startswith("Kick-off Task 1: ") and added[0]["tags"] == ["kickoff", "pm_decomposed", "task_order_1"]


# ======================================================================================== 6. Admin AI knows the projects
def test_admin_address_book_says_which_projects_run():
    from src.agents.workflow_manager import admin_pm_project_note
    pm1 = FakeAgent("PM1")
    assert admin_pm_project_note(pm1) == (" | project started (PM state: pm_standby); plan changes or new work for this "
                                          "project as an UPDATE of it")
    pm1._pending_update = {"number": 2}
    assert admin_pm_project_note(pm1) == " | update 2 of this project waits for the user's approval"
    assert admin_pm_project_note(FakeAgent("PM2", awaiting=True)) == " | its plan waits for the user's approval"
    bootstrap = FakeAgent("project_manager_agent")
    bootstrap.agent_config = {"config": {}}
    assert admin_pm_project_note(bootstrap) == ""


def test_the_prompts_explain_updates():
    import yaml
    prompts = yaml.safe_load((REPO / "prompts.yaml").read_text())
    planning = prompts["admin_ai_planning_prompt"].format(admin_standard_framework_instructions="")
    assert "[CHANGES TO AN EXISTING PROJECT]" in planning
    example = planning.split("[CHANGES TO AN EXISTING PROJECT]", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    assert json.loads(example) == {"title": "Settings And Food Types", "project": "Multiplayer_Snake_Game"}
    assert "NEVER plan a copy or a new version of an existing project as a new project." in planning
    assert "Feedback that asks to fix, change or extend an existing project" in prompts["admin_ai_conversation_prompt"]
    assert "feedback that asks to fix, change or extend an existing project" in prompts["admin_ai_standby_prompt"]
    assert "Please ask the user to test the latest version." in prompts["pm_audit_prompt"]


def test_the_update_card():
    handlers = (REPO / "static" / "js" / "handlers.js").read_text()
    assert 'data-mode="update" data-label="Approve update">Approve update</button>' in handlers
    assert 'data-mode="new" data-label="Start as a new project instead">Start as a new project instead</button>' in handlers
    assert "choice.dataset.label ||" in (REPO / "static" / "js" / "main.js").read_text()
    assert '<script type="module" src="/static/js/main.js?v=6"></script>' in (REPO / "templates" / "index.html").read_text()


# ======================================================================================== 7. failover
def test_failover_reaches_the_plain_ollama_local_provider(monkeypatch):
    """20:14:31 'Provider class not found for 'ollama-local' in PROVIDER_CLASS_MAP' for every local model."""
    import src.agents.failover_handler as fh
    created = []

    class DummyProvider:
        def __init__(self, **kwargs):
            created.append(kwargs)
    monkeypatch.setitem(fh.PROVIDER_CLASS_MAP, "ollama", DummyProvider)
    monkeypatch.setattr(fh.model_registry, "get_reachable_provider_url", lambda name: "http://localhost:11434")

    class Closer:
        async def _close_provider_safe(self, provider):
            return None

        async def send_to_ui(self, payload):
            return None
    agent = FakeAgent("PM2")
    agent.provider_name, agent.model, agent.llm_provider = "openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free", None
    assert run(fh._try_switch_agent(Closer(), agent, "ollama-local", "qwen3.5:9b-q4_K_M", None)) is True
    assert agent.provider_name == "ollama-local" and agent.model == "qwen3.5:9b-q4_K_M"
    assert created[0]["base_url"] == "http://localhost:11434"


def test_a_rate_limited_key_tries_the_same_model_on_the_next_key(monkeypatch):
    import src.agents.failover_handler as fh
    from src.config.settings import settings
    keys = ["sk-or-key-0000", "sk-or-key-1111", "sk-or-key-2222"]
    monkeypatch.setitem(settings.PROVIDER_API_KEYS, "openrouter", keys)
    switches = []

    async def fake_switch(manager, agent, provider, model, key_config):
        switches.append((provider, model, (key_config or {}).get("api_key")))
        agent._last_api_key_used = (key_config or {}).get("api_key")
        return True
    monkeypatch.setattr(fh, "_try_switch_agent", fake_switch)

    class KeyManager:
        index = 2

        async def get_active_key_config(self, provider):
            key = keys[self.index % len(keys)]
            self.index += 1
            return {"api_key": key}
    manager = types.SimpleNamespace(key_manager=KeyManager())
    agent = FakeAgent("PM2")
    agent.provider_name, agent.model, agent._last_api_key_used = "openrouter", "nvidia/nemotron:free", keys[2]
    state = {"tried_keys_per_external_provider": {}}
    assert run(fh._try_same_model_next_key(manager, agent, state, "openrouter", agent.model)) is True
    assert switches == [("openrouter", "nvidia/nemotron:free", "sk-or-key-0000")]               # key 2 failed: key 0
    assert run(fh._try_same_model_next_key(manager, agent, state, "openrouter", agent.model)) is True
    assert switches[-1][2] == "sk-or-key-1111"
    assert run(fh._try_same_model_next_key(manager, agent, state, "openrouter", agent.model)) is False  # all keys tried
    assert len(switches) == 2


# ======================================================================================== 8. collect.py
def _collect_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("collect_r3b", REPO / "collect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOG_20_14 = (
    "2026-10-22 16:41:20,000 - src.agents.manager - INFO - Setting context. Project: DefaultProject, Session: startup_1792658476, Loading: False\n"
    "2026-10-22 16:43:02,000 - src.agents.manager - INFO - Setting context. Project: Multiplayer_Snake_Game, Session: startup_1792658476, Loading: False\n"
    "2026-10-22 20:14:15,469 - src.agents.manager - INFO - Setting context. Project: Multiplayer_Snake_Game_Enhancements, Session: startup_1792658476, Loading: False\n")


def test_collect_finds_every_session_of_the_log(tmp_path):
    collect = _collect_module()
    for project in (PROJECT, "Multiplayer_Snake_Game_Enhancements"):
        (tmp_path / "projects" / project / SESSION / "shared_workspace").mkdir(parents=True)
    sessions = collect.session_dirs_from_log(tmp_path, LOG_20_14)
    assert [s.parent.name for s in sessions] == [PROJECT, "Multiplayer_Snake_Game_Enhancements"]
    assert collect.session_dir_from_log(tmp_path, LOG_20_14).parent.name == "Multiplayer_Snake_Game_Enhancements"
    again = LOG_20_14 + LOG_20_14.splitlines()[1] + "\n"                                        # the game again, last
    assert [s.parent.name for s in collect.session_dirs_from_log(tmp_path, again)] == [
        "Multiplayer_Snake_Game_Enhancements", PROJECT]


def test_collect_exports_the_other_session(tmp_path, monkeypatch):
    collect = _collect_module()
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    game = root / "projects" / PROJECT / SESSION
    (game / "shared_workspace" / "public").mkdir(parents=True)
    (game / "shared_workspace" / "server.js").write_text("import express from 'express';\n")
    (game / "shared_workspace" / "public" / "client.js").write_text("// client\n")
    (game / "task_data").mkdir()
    (game / "task_data" / "pending.data").write_text("[task]\n")
    copy = root / "projects" / "Multiplayer_Snake_Game_Enhancements" / SESSION
    (copy / "shared_workspace").mkdir(parents=True)
    (copy / "shared_workspace" / "PROJECT_PLAN.md").write_text("# plan\n")
    (root / "logs").mkdir()
    (root / "logs" / "app.log").write_text(LOG_20_14)
    dest = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["collect.py", "--root", str(root), "--dest", str(dest), "--no-config"])
    assert collect.main() == 0
    names = sorted(p.name for p in dest.iterdir())
    assert "workspace__PROJECT_PLAN.md.txt" in names                                          # the session it ended in
    assert "other__Multiplayer_Snake_Game__workspace__server.js.txt" in names
    assert "other__Multiplayer_Snake_Game__workspace__public__client.js.txt" in names
    assert "other__Multiplayer_Snake_Game__task_data__pending.data.txt" in names
    summary = (dest / "zz_session_summary.txt").read_text()
    assert f"## Other session of the log: projects/{PROJECT}/{SESSION}  (files: other__{PROJECT}__*)" in summary
    monkeypatch.setattr("sys.argv", ["collect.py", "--root", str(root), "--dest", str(dest), "--no-config", "--primary-only"])
    assert collect.main() == 0
    assert not [p for p in dest.iterdir() if p.name.startswith("other__")]                    # earlier export removed

# END OF FILE tests/unit/test_round3b.py
