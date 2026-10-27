"""
Round 3z: the run of 2026-10-27 14:57-15:19 (WiFi_Snake_Game, qwen3.5:4b-q4_K_M on a remote Ollama behind a proxy).

* 15:01:31-15:02:53 the server timed out 3x and reset the connection (aiohttp.ClientOSError, "[Errno 104] Connection reset
  by peer"). That was an "unknown error": failover switched W1 to gemma4:12b on the SAME server (a 12B model loaded next to
  the 4B model of the other agents; the stream then stalled for 180 s twice) and W1 stayed on it for the rest of the run.
* The CODE MAP showed `getGameState() -> {` and `gameState = {` for objects written over several lines.
* 15:13:14 W1 ("Kick-off Task 1: ... core snake game logic in src/game.js") wrote tests/game/game.test.js for the
  unassigned "Kick-off Task 8: Write automated tests ... in tests/ ...".
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

SERVER = "ollama-local-oli-gaborkukucska-com"
PROJECT, SESSION = "WiFi_Snake_Game", "startup_1793084277"
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


def connection_reset():
    return aiohttp.ClientOSError(104, "Connection reset by peer")


# ======================================================================================== 1. connection errors
def test_connection_errors_are_provider_level():
    from src.agents.failover_handler import is_provider_level_error
    assert is_provider_level_error(connection_reset())
    assert is_provider_level_error(aiohttp.ServerDisconnectedError())
    assert is_provider_level_error(aiohttp.ConnectionTimeoutError())
    assert is_provider_level_error(ConnectionResetError(104, "Connection reset by peer"))
    assert not is_provider_level_error(ValueError("[Ollama Error]: XML syntax error on line 11"))


def test_the_outcome_of_a_connection_reset_is_a_provider_level_failover():
    from src.agents.cycle_components.outcome_determiner import CycleOutcomeDeterminer
    context = types.SimpleNamespace(
        agent=types.SimpleNamespace(agent_id="W1"), last_error_obj=connection_reset(),
        last_error_content="[Ollama Error]: Max retries after connection/timeout. Last: [Errno 104] Connection reset by peer",
        is_retryable_error_type=None, is_key_related_error=None, is_provider_level_error=False, trigger_failover=False,
        cycle_completed_successfully=False)
    with quiet():
        CycleOutcomeDeterminer().determine_cycle_outcome(context)
    assert context.is_provider_level_error is True and context.trigger_failover is True


class TestFailover:
    def setup_method(self):
        from src.agents import failover_handler
        self.fh = failover_handler
        failover_handler._models_without_tool_support.clear()
        self.agent = types.SimpleNamespace(
            agent_id="W1", agent_type="worker", provider_name=SERVER, model="qwen3.5:4b-q4_K_M",
            agent_config={"config": {}}, _failover_state={}, set_status=MagicMock())
        tracker = MagicMock()
        tracker.get_metrics.return_value = None
        self.manager = types.SimpleNamespace(agents={"W1": self.agent}, send_to_ui=AsyncMock(), performance_tracker=tracker,
                                             key_manager=types.SimpleNamespace(get_active_key_config=AsyncMock()))
        self.patches = [patch.object(failover_handler, "model_registry"),
                        patch.object(failover_handler, "_check_provider_health", new_callable=AsyncMock),
                        patch.object(failover_handler, "_try_switch_agent", new_callable=AsyncMock)]
        self.registry, self.health, self.switch = (p.start() for p in self.patches)
        model = lambda mid, size: {"id": mid, "num_parameters": size, "provider": "x"}  # noqa: E731
        self.registry.get_available_models_dict.return_value = {SERVER: [
            model("qwen3.5:4b-q4_K_M", 4e9), model("gemma4:12b", 12e9)]}
        self.registry.get_reachable_provider_url.side_effect = lambda provider: "https://oli.gaborkukucska.com"
        self.registry.get_model_info.return_value = None
        self.health.return_value = True

        async def fake_switch(manager, agent, provider, model_id, key_config):
            agent.provider_name, agent.model = provider, model_id
            return True
        self.switch.side_effect = fake_switch

    def teardown_method(self):
        for p in self.patches:
            p.stop()
        self.fh._models_without_tool_support.clear()

    def tried(self):
        return [(c.args[2], c.args[3]) for c in self.switch.call_args_list]

    def test_a_connection_reset_does_not_switch_to_another_model_on_the_same_server(self):
        with quiet():
            switched = run(self.fh.handle_agent_model_failover(self.manager, "W1", connection_reset()))
        # 15:02:56 W1 was switched to gemma4:12b; now: nothing to switch to - it retries its own model after a cooldown
        assert switched is False and self.tried() == []

    def test_after_a_model_error_switch_the_worker_returns_for_a_new_task(self):
        with quiet():
            assert run(self.fh.handle_agent_model_failover(self.manager, "W1", ValueError("model runner crashed"))) is True
        assert self.agent.model == "gemma4:12b"
        assert self.agent._configured_model["model"] == "qwen3.5:4b-q4_K_M"
        # too early for the time-based return
        with quiet():
            assert run(self.fh.return_to_configured_model(self.manager, self.agent, "test",
                                                          min_age=self.fh.FAILOVER_RETURN_AFTER_SECONDS)) is False
        assert self.agent.model == "gemma4:12b"
        # a new task: back to the configured model
        with quiet():
            assert run(self.fh.return_to_configured_model(self.manager, self.agent, "a new task")) is True
        assert (self.agent.provider_name, self.agent.model) == (SERVER, "qwen3.5:4b-q4_K_M")
        assert self.agent._configured_model is None and not self.agent._failover_state

    def test_the_time_based_return(self):
        with quiet():
            run(self.fh.handle_agent_model_failover(self.manager, "W1", ValueError("model runner crashed")))
        self.agent._configured_model["since"] -= self.fh.FAILOVER_RETURN_AFTER_SECONDS + 1
        with quiet():
            assert run(self.fh.return_to_configured_model(self.manager, self.agent, "test",
                                                          min_age=self.fh.FAILOVER_RETURN_AFTER_SECONDS)) is True
        assert self.agent.model == "qwen3.5:4b-q4_K_M"

    def test_no_return_while_the_model_is_gone_or_the_server_does_not_answer(self):
        with quiet():
            run(self.fh.handle_agent_model_failover(self.manager, "W1", ValueError("model runner crashed")))
        self.health.return_value = False
        with quiet():
            assert run(self.fh.return_to_configured_model(self.manager, self.agent, "a new task")) is False
        assert self.agent.model == "gemma4:12b" and self.agent._configured_model                # kept for later
        self.health.return_value = True
        self.registry.get_available_models_dict.return_value = {SERVER: [{"id": "gemma4:12b"}]}
        with quiet():
            assert run(self.fh.return_to_configured_model(self.manager, self.agent, "a new task")) is False
        assert self.agent.model == "gemma4:12b"

    def test_nothing_to_do_without_a_failover(self):
        agent = types.SimpleNamespace(agent_id="W2", provider_name=SERVER, model="qwen3.5:4b-q4_K_M")
        assert run(self.fh.return_to_configured_model(self.manager, agent, "a new task")) is False
        agent._configured_model = {"provider": SERVER, "model": "qwen3.5:4b-q4_K_M", "since": 0}
        assert run(self.fh.return_to_configured_model(self.manager, agent, "a new task")) is False
        assert agent._configured_model is None and self.tried() == []


def test_the_scheduler_tries_the_return_after_a_cycle_without_errors():
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    agent = types.SimpleNamespace(agent_id="W1", message_history=[], state="worker_work", status="idle",
                                  _configured_model={"provider": SERVER, "model": "qwen3.5:4b-q4_K_M", "since": 0})
    context = types.SimpleNamespace(agent=agent, all_tool_results=[], last_error_obj=None, trigger_failover=False)
    with patch("src.agents.failover_handler.return_to_configured_model", new_callable=AsyncMock) as back, quiet():
        try:
            run(NextStepScheduler(types.SimpleNamespace()).schedule_next_step(context))
        except Exception:
            pass  # the rest of the scheduling needs a real manager; the return is tried first
    from src.agents.failover_handler import FAILOVER_RETURN_AFTER_SECONDS
    assert back.await_count == 1 and back.await_args.kwargs["min_age"] == FAILOVER_RETURN_AFTER_SECONDS
    context.last_error_obj = connection_reset()
    with patch("src.agents.failover_handler.return_to_configured_model", new_callable=AsyncMock) as back, quiet():
        try:
            run(NextStepScheduler(types.SimpleNamespace()).schedule_next_step(context))
        except Exception:
            pass
    assert back.await_count == 0                                                 # not after a failed cycle


# ======================================================================================== 2. object shapes in the map
GAME_JS = """const CONSTANTS = {
  GRID_SIZE: 20,
  INITIAL_DIRECTION: { x: 1, y: 0 },
  COLORS: { HEAD: '#4CAF50' }
};
let gameState = {
  snake: [],
  food: null,
  score: 0
};
function getGameState() {
  return {
    snake: gameState.snake,
    food: gameState.food,
    score: gameState.score,
    over: (() => { return false; })()
  };
}
function find(x) {
  if (x) {
    return { ...gameState, x };
  }
  return null;
}
module.exports = { CONSTANTS, getGameState, find };
"""


def test_objects_written_over_several_lines_show_their_keys():
    from src.utils.code_map import javascript_outline
    lines = "\n".join(javascript_outline(GAME_JS))
    assert "getGameState() -> {snake, food, score, over}" in lines
    assert "find(x) -> {...gameState, x} | null" in lines
    assert "gameState = {snake, food, score}" in lines
    assert "CONSTANTS = {GRID_SIZE, INITIAL_DIRECTION, COLORS}" in lines
    assert "-> {\n" not in lines and "= {," not in lines


# ======================================================================================== 3. a planned task's tests folder
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


def _manager(*agents):
    return types.SimpleNamespace(agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION)


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


@needs_tw
def test_a_code_worker_does_not_write_tests_into_the_tests_folder_of_an_unassigned_planned_task(tasks):
    from src.tools.project_management import foreign_file_creation_error
    w1 = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_work", current_task_id=None,
                               active_task_id=None)
    manager = _manager(w1)
    w1.current_task_id = _add(tasks, manager, "Kick-off Task 1: Implement the core snake game logic in src/game.js", "W1")
    _add(tasks, manager, "Kick-off Task 8: Write automated tests for game logic, server communication, and authentication "
                         "in tests/ and run the server to verify functionality")
    refusal = foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "tests/game/game.test.js")
    assert refusal and "is the file of the planned task 'Kick-off Task 8: Write automated tests" in refusal
    assert "not assigned yet" in refusal
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "src/game.js") is None
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "docs/notes.md") is None   # not a test file


@needs_tw
def test_a_worker_whose_own_task_is_about_tests_may_write_there(tasks):
    from src.tools.project_management import foreign_file_creation_error
    w2 = types.SimpleNamespace(agent_id="W2", agent_type="worker", state="worker_work", current_task_id=None,
                               active_task_id=None)
    manager = _manager(w2)
    w2.current_task_id = _add(tasks, manager, "Kick-off Task 9: Write JavaScript tests for the server in tests/server/", "W2")
    _add(tasks, manager, "Kick-off Task 8: Write automated tests for game logic in tests/")
    assert foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "tests/server/server.test.js") is None
