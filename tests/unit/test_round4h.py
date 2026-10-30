# START OF FILE tests/unit/test_round4h.py
"""
Round 4h: the run of 2026-10-30 03:27-07:50 (qwen3.5:4b-q4_K_M, WiFi_Snake_Game: Python / Flask).

* From 04:00:09 every request to https://oli.gaborkukucska.com answered "Status 530" (Cloudflare: the server behind it is
  unreachable). W3's failover found nothing else and the framework retried 43 times until 07:50; the user only had a status
  line in the internal-comms panel, and the run looked stuck.
* 03:42:04 PM1 tried to swap the tasks of W1 and W2; the busy-worker refusal said "leave this task unassigned ... or create
  one more worker" although both tasks had their worker. 03:42:50 PM1 created W3, and 03:43:22 gave it "Kick-off Task 6: Write
  pytest tests ..." while W2 was writing tests/test_game_logic.py for it (in_progress since 03:41:06): W2's notice was only
  queued, and W3 wrote the tests again.
* 03:48:13 and 03:55:54 a pytest run of tests/test_api.py that did not end was told about `require.main === module`,
  after()/afterAll() hooks and mocha's `--exit`.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1793302038"
PROVIDER = "ollama-local-oli-gaborkukucska-com"
ERROR_530 = (f"[{PROVIDER} Error] [Ollama Error]: Max retries. Last: Status 530 - <!DOCTYPE html>")
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


class Manager:
    def __init__(self):
        self.sent = []

    async def send_to_ui(self, message):
        self.sent.append(message)


def _worker(agent_id="W3"):
    return types.SimpleNamespace(agent_id=agent_id, provider_name=PROVIDER,
                                 llm_provider=types.SimpleNamespace(base_url="https://oli.gaborkukucska.com"))


# ======================================================================================== 1. the model server is down
def test_the_user_is_told_once_when_the_model_server_stays_down_and_when_it_is_back(monkeypatch):
    import time as _time
    from src.agents import provider_outage
    monkeypatch.setattr(provider_outage, "_clock", lambda seconds: _time.strftime("%H:%M", _time.gmtime(seconds)))
    manager, w3 = Manager(), _worker()
    start = 1793304009.0                                                                                        # 04:00:09
    with quiet():
        assert not run(provider_outage.note_provider_outage(manager, w3, ERROR_530, now=start))
        assert not run(provider_outage.note_provider_outage(manager, w3, ERROR_530, now=start + 60))
        assert run(provider_outage.note_provider_outage(manager, w3, ERROR_530, now=start + 150))
        assert not run(provider_outage.note_provider_outage(manager, w3, ERROR_530, now=start + 450))      # once
    assert manager.sent == [{
        "type": "provider_outage", "agent_id": "system", "provider": PROVIDER,
        "content": "⚠️ The model server https://oli.gaborkukucska.com has not answered since 20:00: Cloudflare cannot reach the "
                   "server behind it (HTTP 530: the server or its tunnel is not running). No other model server is available, "
                   "so the agents' work is paused (W3 waiting). They retry every few minutes and continue by themselves when "
                   "the server answers again - nothing in the project is lost. If the server is yours, check that it (and its "
                   "tunnel) is running."}]
    with quiet():
        assert run(provider_outage.note_provider_answered(manager, w3, now=start + 13800))                  # 07:50:09
        assert not run(provider_outage.note_provider_answered(manager, w3, now=start + 13900))
    assert manager.sent[-1] == {
        "type": "provider_recovered", "agent_id": "system", "provider": PROVIDER,
        "content": "✅ The model server https://oli.gaborkukucska.com answers again (it was unreachable from 20:00 for 3 h 50 "
                   "min). The agents continue their work."}
    # a short blip: nobody is told
    blip = Manager()
    with quiet():
        run(provider_outage.note_provider_outage(blip, w3, ERROR_530, now=start))
        assert not run(provider_outage.note_provider_answered(blip, w3, now=start + 40))
    assert blip.sent == []
    assert provider_outage.error_meaning("Cannot connect to host oli.gaborkukucska.com:443") == \
        "no connection to it can be made"


def test_the_scheduler_reports_the_outage_and_the_recovery(monkeypatch):
    import src.agents.cycle_components.next_step_scheduler as nss
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    manager = Manager()
    manager._provider_outages = {PROVIDER: {"since": __import__("time").time() - 300, "notified": False, "agents": []}}

    async def no_failover(agent_id, error):
        return False
    manager.handle_agent_model_failover = no_failover
    w3 = _worker()
    w3.message_history, w3.state, w3.status = [], "worker_work", "processing"
    w3.set_status = lambda status: setattr(w3, "status", status)
    scheduler = NextStepScheduler(manager)

    async def no_cycle(agent, retry):
        return None
    monkeypatch.setattr(scheduler, "_schedule_new_cycle", no_cycle)
    real_sleep = asyncio.sleep

    async def no_wait(seconds, *args, **kwargs):
        await real_sleep(0)
    monkeypatch.setattr(nss.asyncio, "sleep", no_wait)
    failed = types.SimpleNamespace(agent=w3, all_tool_results=[], last_error_obj=RuntimeError("530"), trigger_failover=True,
                                   last_error_content=ERROR_530, current_model_key_for_tracking=f"{PROVIDER}/qwen3.5")
    with quiet():
        run(scheduler.schedule_next_step(failed))
    assert [m["type"] for m in manager.sent] == ["status", "provider_outage"]
    answered = types.SimpleNamespace(agent=w3, all_tool_results=[], last_error_obj=None, trigger_failover=False)
    with quiet():
        try:
            run(scheduler.schedule_next_step(answered))
        except Exception:
            pass  # the rest of the scheduling needs a real manager; only the notice is checked here
    assert manager.sent[-1]["type"] == "provider_recovered" and manager._provider_outages == {}


def test_the_main_chat_shows_the_notices():
    handlers = (Path(__file__).resolve().parents[2] / "static" / "js" / "handlers.js").read_text(encoding="utf-8")
    for kind in ("provider_outage", "provider_recovered"):
        block = handlers.split(f"case '{kind}':", 1)[1].split("break;", 1)[0]
        assert "targetAreaId = 'conversation-area';" in block


# ======================================================================================== 2. a task in work keeps its worker
class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history = []


def _manager(*agents):
    handler = types.SimpleNamespace(sent=[])

    async def route(sender_id, target_identifier, message_content):
        handler.sent.append((sender_id, target_identifier, message_content))
    handler.route_and_activate_agent_message = route
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        interaction_handler=handler,
        state_manager=types.SimpleNamespace(
            get_agent_team=lambda agent_id: f"team_{PROJECT}",
            get_agents_in_team=lambda team_id: [a for a in agents]))


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
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert result["status"] == "success", result
    return result["task_uuid"]


TASK_6 = "Kick-off Task 6: Write pytest tests for game logic, player system, and API endpoints in tests/"
TASK_7 = ("Kick-off Task 7: Write README.md with setup instructions, hosting guide for local WiFi access, and usage examples "
          "in docs/")


@needs_tw
def test_a_task_its_worker_works_on_is_not_given_to_another(tasks):
    pm1, w1, w2, w3 = Agent("PM1", "pm", "pm_report_check"), Agent("W1"), Agent("W2", state="worker_work"), Agent("W3")
    manager = _manager(pm1, w1, w2, w3)
    task6 = _add(tasks, manager, TASK_6, "W2")
    w2.active_task_id = task6
    assert tasks("W2", manager, action="modify_task", task_id=task6, task_progress="in_progress")["status"] == "success"
    refused = tasks("PM1", manager, action="modify_task", task_id=task6, assignee_agent_id="W3")            # 03:43:22
    assert refused == {"status": "error", "error_type": "task_in_work", "message": (
        f"Task '{task6}' ('{TASK_6[:87]}...') "
        "was NOT given to W3: W2 is working on it RIGHT NOW (worker_work, in_progress). A second worker on it would write the same "
        "files again. It stays W2's task: wait for W2's report (you are woken up when it comes). Give W3 a task that has no "
        "worker yet, or let it wait.")}
    assert manager.interaction_handler.sent == []
    # a worker that waits (or reports) may lose its task - and is told
    w2.state = "worker_wait"
    assert tasks("PM1", manager, action="modify_task", task_id=task6, assignee_agent_id="W3")["status"] == "success"
    assert manager.interaction_handler.sent and manager.interaction_handler.sent[0][1] == "W2"


@needs_tw
def test_a_task_that_has_its_worker_is_not_left_unassigned(tasks):
    pm1, w1, w2 = Agent("PM1", "pm", "pm_report_check"), Agent("W1"), Agent("W2", state="worker_work")
    manager = _manager(pm1, w1, w2)
    task6 = _add(tasks, manager, TASK_6, "W2")
    task7 = _add(tasks, manager, TASK_7, "W1")
    refused = tasks("PM1", manager, action="modify_task", task_id=task7, assignee_agent_id="W2")            # 03:42:04
    assert refused["error_type"] == "busy_worker"
    assert refused["message"] == (
        f"Task '{task7}' was NOT changed: 'W2' already has an open task ('{TASK_6[:87]}...', todo, {task6}). A worker "
        "works on ONE task at a time, so a second ready task would only wait in its queue. This task already has its "
        "worker: W1 (todo). Leave it with W1 - nothing needs to change.")
    assert "create one more worker" not in refused["message"]


# ======================================================================================== 3. a pytest run that does not end
def test_a_python_test_run_that_does_not_end_gets_python_advice(tmp_path):
    from src.tools.project_links_check import test_hang_hint
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_api.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    hint = test_hang_hint(tmp_path, "python -m pytest tests/test_auth.py tests/test_api.py -v --tb=short", cpu_busy=0.0,
                          output="tests/test_api.py::TestAPIEndpoints::test_update_score_success")
    assert hint.startswith("\n[Framework hint - the TEST process was IDLE (0% CPU) when it was stopped, so it waited for "
                           "something:] usually a server a test (or a module it imports) starts and never stops")
    assert "`if __name__ == '__main__':`" in hint and "app.test_client()" in hint and "the code after `yield`" in hint
    assert "require.main" not in hint and "mocha" not in hint
    assert hint.endswith("It printed last: 'tests/test_api.py::TestAPIEndpoints::test_update_score_success'. Running the "
                         "same command again with a longer timeout will not help.")
    js = test_hang_hint(tmp_path, "node --test tests/game.test.js", cpu_busy=0.0, output="ok 1")
    assert "require.main === module" in js

# END OF FILE tests/unit/test_round4h.py
