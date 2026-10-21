# START OF FILE tests/unit/test_round2s.py
"""
Round 2s: fixtures from the 2026-10-21 20:14-20:19 run (the same WiFi snake request; the project name existed already).

* 20:15:26 - the approval click reached the server WITHOUT the continue/new choice: the browser still used old cached copies
  of the page's module scripts (only main.js carried a version). The chat asked "choose 'continue' ... or 'new'".
* 20:15:59 - the user TYPED "continue". It went to Admin AI as an ordinary message: Admin went to 'work', listed an empty
  DefaultProject workspace, tried to create a Project Manager, made directories and messaged the unapproved PM1.
* 20:17:26 - that message moved PM1 (still waiting for approval) to pm_report_check and then pm_manage.
* 20:18:54 - Admin planned the project again: PM2 was created next to PM1.
"""
import asyncio
import json
import re
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
EXISTING = {"name": "WiFi_Snake_Multiplayer_Game", "latest_session": "startup_1792581286",
            "suggested_new_name": "WiFi_Snake_Multiplayer_Game_2",
            "sessions": [{"name": "startup_1792581286", "files": 7, "last_changed": "2026-10-21T19:37"}]}


def run(coro):
    return asyncio.run(coro)


# ======================================================================================== 1. the typed choice
@pytest.mark.parametrize("text, choice", [
    ("continue", "continue"), ("Continue!", "continue"), ("continue existing project", "continue"),
    ("Continue the existing project.", "continue"), ("please continue", "continue"), ("Continue, thanks", "continue"),
    ("new", "new"), ("Start new project", "new"), ("start a new project", "new"), ("new please", "new"),
    ("start it as a new project", "new"), ("new one", "new"),
    ("continue with the plan", None), ("new game please", None), ("what is new?", None), ("", None), (None, None),
])
def test_the_choice_words(text, choice):
    from src.utils.project_folders import approval_choice_from_text
    assert approval_choice_from_text(text) == choice


def _pm(agent_id="PM1", existing=EXISTING, **overrides):
    pm = types.SimpleNamespace(
        agent_id=agent_id, agent_type="pm", status="idle", state="pm_startup", _awaiting_project_approval=True,
        _existing_project_info=existing, message_history=[], persona="Project Manager (@pm_agent) (WiFi_Snake_Multiplayer_Game)",
        agent_config={"config": {"project_name_context": "WiFi_Snake_Multiplayer_Game", "session_name": "startup_1792584861",
                                 "persona": "Project Manager (@pm_agent) (WiFi_Snake_Multiplayer_Game)",
                                 "initial_plan_description": "**Objective:** a WiFi snake game"}})
    pm._approval_card = {"type": "project_pending_approval", "pm_agent_id": agent_id, "existing_project": existing}
    for key, value in overrides.items():
        setattr(pm, key, value)
    return pm


def _manager(tmp_path, monkeypatch, *pms):
    from src.agents.manager import AgentManager
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    old_ws = tmp_path / "projects" / "WiFi_Snake_Multiplayer_Game" / "startup_1792581286" / "shared_workspace"
    (old_ws / "game-server").mkdir(parents=True)
    (old_ws / "game-server" / "app.js").write_text("const express = require('express');\n")
    admin = types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", status="idle", state="admin_conversation",
                                  message_history=[{"role": "system", "content": "p"}])
    manager = object.__new__(AgentManager)
    manager.agents = {"admin_ai": admin, **{pm.agent_id: pm for pm in pms}}
    manager.current_project, manager.current_session, manager.current_session_db_id = "DefaultProject", "startup_1792584861", None
    manager.ui = []
    manager.scheduled = []

    async def set_context(project, session, loading=False):
        manager.current_project, manager.current_session = project, session

    async def send_to_ui(payload):
        manager.ui.append(payload)

    async def schedule_cycle(agent, retry_count=0):
        manager.scheduled.append(agent.agent_id)
    manager.set_project_session_context = set_context
    manager.send_to_ui = send_to_ui
    manager.schedule_cycle = schedule_cycle
    manager.push_agent_status_update = AsyncMock()
    return manager, admin


def _say(manager, text):
    async def go():
        await manager.handle_user_message(text)
        await asyncio.sleep(0)                                   # the approval schedules the PM with create_task
    run(go())


def test_typed_continue_approves_the_waiting_plan(tmp_path, monkeypatch):
    pm1 = _pm()
    manager, admin = _manager(tmp_path, monkeypatch, pm1)
    _say(manager, "continue")                                                                      # 20:15:59
    assert pm1._awaiting_project_approval is False and manager.scheduled == ["PM1"]
    assert manager.current_project == "WiFi_Snake_Multiplayer_Game"
    text = "Project 'WiFi_Snake_Multiplayer_Game' managed by 'PM1' approved and started (continuing the existing project)."
    assert manager.ui[-1] == {"type": "final_response", "agent_id": "admin_ai", "content": text}
    assert admin.message_history == [{"role": "system", "content": "p"}, {"role": "system", "content": f"[Framework Note]: {text}"}]
    assert any("game-server/app.js" in m["content"] for m in pm1.message_history)                 # the files carried over
    carried = tmp_path / "projects" / "WiFi_Snake_Multiplayer_Game" / "startup_1792584861" / "shared_workspace"
    assert (carried / "game-server" / "app.js").is_file()


def test_typed_new_starts_a_new_project(tmp_path, monkeypatch):
    pm1 = _pm()
    manager, admin = _manager(tmp_path, monkeypatch, pm1)
    _say(manager, "Start new project")
    assert pm1.agent_config["config"]["project_name_context"] == "WiFi_Snake_Multiplayer_Game_2"
    assert manager.ui[-1]["content"] == ("Project 'WiFi_Snake_Multiplayer_Game_2' managed by 'PM1' approved and started "
                                         "(as a new project).")


@pytest.mark.parametrize("case", ["no_waiting_plan", "plan_without_choice", "not_a_choice", "two_waiting_plans"])
def test_other_messages_still_go_to_admin(tmp_path, monkeypatch, case):
    pms = {"no_waiting_plan": [_pm(_awaiting_project_approval=False, state="pm_manage")],
           "plan_without_choice": [_pm(existing=None)],
           "not_a_choice": [_pm()],
           "two_waiting_plans": [_pm(), _pm("PM2")]}[case]
    manager, admin = _manager(tmp_path, monkeypatch, *pms)
    text = "continue with the leaderboard please" if case == "not_a_choice" else "continue"
    _say(manager, text)
    assert manager.scheduled == ["admin_ai"] and admin.message_history[-1] == {"role": "user", "content": text}
    assert all(pm._awaiting_project_approval == (case != "no_waiting_plan") for pm in pms)


def test_a_refused_typed_choice_is_told_in_the_chat(tmp_path, monkeypatch):
    pm1 = _pm()
    manager, admin = _manager(tmp_path, monkeypatch, pm1)
    import src.api.http_routes as routes
    from fastapi import HTTPException

    async def refuse(pm_agent_id, mode=None, manager=None):
        raise HTTPException(status_code=409, detail="Project Manager agent 'PM1' is currently busy")
    monkeypatch.setattr(routes, "approve_project_start", refuse)
    _say(manager, "new")
    assert manager.ui[-1]["content"] == "The project of PM1 was not started: Project Manager agent 'PM1' is currently busy"
    assert manager.scheduled == [] and admin.message_history == [{"role": "system", "content": "p"}]


def test_the_missing_choice_message_names_typing_and_the_reload(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from src.api.http_routes import approve_project_start
    pm1 = _pm()
    manager, _admin = _manager(tmp_path, monkeypatch, pm1)
    with pytest.raises(HTTPException):
        run(approve_project_start("PM1", None, manager))                                          # 20:15:26
    chat = manager.ui[-1]["content"]
    assert "or type continue or new here." in chat and "reload it once with Ctrl+Shift+R." in chat


def test_pending_approval_pms():
    from src.agents.manager import AgentManager
    manager = object.__new__(AgentManager)
    waiting, started, running = _pm(), _pm("PM2", _awaiting_project_approval=False), _pm("PM3", status="processing")
    other = _pm("PM4")
    other.agent_config["config"]["project_name_context"] = "Other"
    manager.agents = {pm.agent_id: pm for pm in (waiting, started, running, other)}
    assert [p.agent_id for p in manager.pending_approval_pms()] == ["PM1", "PM4"]
    assert [p.agent_id for p in manager.pending_approval_pms("WiFi_Snake_Multiplayer_Game")] == ["PM1"]


# ======================================================================================== 2. an unapproved PM does not start
def _handler(pm):
    from src.agents.interaction_handler import AgentInteractionHandler
    admin = types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", persona="Admin AI (@admin_ai)", status="idle",
                                  state="work", message_history=[])
    manager = types.SimpleNamespace(
        agents={"admin_ai": admin, "PM1": pm}, schedule_cycle=AsyncMock(), send_to_ui=AsyncMock(),
        state_manager=types.SimpleNamespace(get_agent_team=lambda agent_id: None),
        workflow_manager=types.SimpleNamespace(_get_agent_project_name=lambda agent, mgr: "WiFi_Snake_Multiplayer_Game",
                                               change_state=lambda agent, state: setattr(agent, "state", state)))
    return AgentInteractionHandler(manager), manager


QUESTION_20_17_26 = ("Hello! I see you're the Project Manager for WiFi_Snake_Multiplayer_Game project. Since this is a "
                     "continuation of an existing session, could you please help me understand ...")


def test_a_message_to_an_unapproved_pm_is_refused():
    pm1 = _pm()
    handler, manager = _handler(pm1)
    status, task = run(handler.route_and_activate_agent_message("admin_ai", "PM1", QUESTION_20_17_26))
    assert task is None and status["status"] == "error"
    assert status["message"].startswith("[Manager Feedback for SendMessage]: NOTHING was delivered: 'PM1' has not started "
                                        "yet, because its project plan is waiting for the user's approval")
    assert pm1.state == "pm_startup" and pm1.message_history == []
    manager.schedule_cycle.assert_not_awaited()


def test_a_message_to_an_approved_pm_is_delivered():
    pm1 = _pm(_awaiting_project_approval=False)
    handler, manager = _handler(pm1)

    async def go():
        result = await handler.route_and_activate_agent_message("admin_ai", "PM1", "How far is Task 2?")
        await asyncio.sleep(0)
        return result
    status, _task = run(go())
    assert status["status"] == "delivered" and "How far is Task 2?" in pm1.message_history[-1]["content"]


# ======================================================================================== 3. a new plan replaces the waiting one
def _creation_manager(tmp_path, monkeypatch):
    from src.agents.manager import AgentManager
    from src.config.settings import settings
    base = tmp_path / "projects"
    (base / "WiFi_Snake_Multiplayer_Game" / "startup_1792581286" / "shared_workspace").mkdir(parents=True)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", base)
    admin = types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", state="planning", message_history=[])
    calls, ui, deleted = [], [], []
    uuids = iter(["c283b577-7c57-46e5-bff7-1ce70a526a28", "5d1f0c2e-0d43-4a8c-9a36-2b2b8c7e1f00"])

    async def create_agent_instance(agent_id_requested, **kwargs):
        pm = types.SimpleNamespace(agent_id=agent_id_requested, agent_type="pm", status="idle", state=None,
                                   message_history=[], sandbox_path=tmp_path / "sandbox",
                                   agent_config={"config": {"project_name_context": kwargs["project_name_context"]}})
        manager.agents[agent_id_requested] = pm
        return True, "created", agent_id_requested

    async def execute_tool(**kwargs):
        calls.append(kwargs["tool_args"])
        if kwargs["tool_args"]["action"] == "add_task":
            return {"status": "success", "task_id": 1, "task_uuid": next(uuids)}
        return {"status": "success"}

    async def delete_agent_instance(agent_id):
        deleted.append(agent_id)
        manager.agents.pop(agent_id)
        return True, "deleted"

    async def send_to_ui(payload):
        ui.append(payload)
    manager = types.SimpleNamespace(
        agents={"admin_ai": admin}, current_project="DefaultProject", current_session="startup_1792584861",
        current_session_db_id=None, create_agent_instance=create_agent_instance, delete_agent_instance=delete_agent_instance,
        send_to_ui=send_to_ui, workflow_manager=types.SimpleNamespace(change_state=lambda a, s: setattr(a, "state", s)),
        tool_executor=types.SimpleNamespace(execute_tool=execute_tool))
    manager.pending_approval_pms = lambda project_name=None: AgentManager.pending_approval_pms(manager, project_name)
    return manager, admin, calls, ui, deleted


def _plan(manager, admin):
    from src.workflows.project_creation_workflow import ProjectCreationWorkflow
    return run(ProjectCreationWorkflow().execute(manager, admin, {"title": "WiFi Snake Multiplayer Game",
                                                                   "_raw_plan_body_": "**Objective:** a WiFi snake game"}))


def test_a_second_plan_replaces_the_unapproved_first(tmp_path, monkeypatch):
    manager, admin, calls, ui, deleted = _creation_manager(tmp_path, monkeypatch)
    _plan(manager, admin)                                                                          # 20:15:18 PM1
    assert manager.agents["PM1"]._kickoff_task_uuid == "c283b577-7c57-46e5-bff7-1ce70a526a28"
    result = _plan(manager, admin)                                                                 # 20:18:54 PM2
    assert result.success and deleted == ["PM1"] and "PM2" in manager.agents and "PM1" not in manager.agents
    assert {"action": "modify_task", "task_id": "c283b577-7c57-46e5-bff7-1ce70a526a28", "task_progress": "deleted"} in calls
    assert "It replaces the earlier plan of PM1 (never approved; removed)." in result.message
    assert ui[-1]["type"] == "system_event" and ui[-1]["message"] == (
        "The new plan for 'WiFi_Snake_Multiplayer_Game' replaces the earlier plan of PM1, which was never approved: use "
        "the approval card of the new plan.")


def test_a_started_project_is_not_replaced(tmp_path, monkeypatch):
    manager, admin, calls, ui, deleted = _creation_manager(tmp_path, monkeypatch)
    _plan(manager, admin)
    manager.agents["PM1"]._awaiting_project_approval = False                                      # approved and running
    manager.agents["PM1"].state = "pm_manage"
    result = _plan(manager, admin)
    assert deleted == [] and "PM1" in manager.agents and "replaces" not in result.message


def test_admin_is_told_the_choice_is_the_users(tmp_path, monkeypatch):
    manager, admin, *_ = _creation_manager(tmp_path, monkeypatch)
    _plan(manager, admin)
    note = admin.message_history[-1]["content"]
    assert note.startswith("[Framework Note]: A project named 'WiFi_Snake_Multiplayer_Game' already existed")
    assert note.endswith("The user answers with the card's buttons (or by typing continue or new); the framework then "
                         "starts the project. You do NOT start, investigate or plan it again yourself.")


# ======================================================================================== 4. no stale page scripts
def test_every_page_script_has_a_versioned_url():
    html = (REPO / "templates" / "index.html").read_text()
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1))["imports"]
    modules = sorted(f"/static/js/{p.name}" for p in (REPO / "static" / "js").glob("*.js"))
    assert sorted(imports) == modules                                                    # every module is mapped
    versions = {re.fullmatch(re.escape(key) + r"\?v=(\d+)", url).group(1) for key, url in imports.items()}
    assert len(versions) == 1                                                            # one version for all
    version = versions.pop()
    assert f'<script type="module" src="/static/js/main.js?v={version}"></script>' in html
    assert html.index('type="importmap"') < html.index('<script type="module"')        # the map comes first
    for module in (REPO / "static" / "js").glob("*.js"):                                 # imports use plain names
        for spec in re.findall(r"""(?:from\s+|import\(\s*)['"](\./[^'"]+)['"]""", module.read_text()):
            assert "?" not in spec and f"/static/js/{spec[2:]}" in imports, (module.name, spec)

# END OF FILE tests/unit/test_round2s.py
