# START OF FILE tests/unit/test_round3u.py
"""
Round 3u: the run of 2026-10-26 18:41-21:48 (WiFi_Snake_Multiplayer, qwen3.5:4b on a remote Ollama 0.40.8 behind a
Cloudflare proxy; PM1, W1 Senior Python Developer, W2 Technical Documentation Specialist).

* 19:53-21:47 76 requests ended with "Status 524" after 47-120 s (requests with tools were sent with stream=false), others
  with the proxy's "Temporarily Offline" page. After the retries the error counted as the agent's EMPTY reply: the Guardian
  blocked W1 and W2 ("2 consecutive empty responses"), and failover moved W1, W2 and PM1 to gemma4:12b / qwen3.5:9b on the
  SAME server (one load took 56.9 s).
* 19:21:06 PM1 gave "Kick-off Task 7: Write pytest tests ..." from W2 to W1; at 19:21:08 W2 marked it finished - accepted,
  with 74 failing tests that called Game.update(), Game.pause(), Server.test_client() ... which do not exist. W1's
  "Kick-off Task 9: Run pytest tests and deploy ..." then spent 2 h 20 min changing src/game_logic.py to fit them.
* 19:26 W2 wrote requirements.txt as a Markdown document (``` fences) - `pip install -r` stops at the first fence.
* 19:24 / 19:49 PM1 added "Create deployment scripts and deployment documentation" (Dockerfile, docker-compose.yml,
  deploy.sh) and "Create comprehensive user documentation and FAQ" for the idle W2 - neither in the plan.
* 19:01 W2's `python -m pytest -q` timed out ("the test run did not END") and W2 edited public/game.js, a browser file
  pytest never loads.
"""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer", "startup_1793011298"
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

# the proxy's page of the run (Cloudflare 524 and the 500 "Temporarily Offline" answers both carried it)
OFFLINE_PAGE = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" '
                'content="width=device-width, initial-scale=1.0">\n<title>NoSlop — Temporarily Offline</title>\n<style>\n'
                '  * { box-sizing: border-box; }\n</style></head><body>offline</body></html>')

# W2's requirements.txt of 19:26 (first lines)
MARKDOWN_REQUIREMENTS = """# WiFi Snake Multiplayer - Python Dependencies

## Core Dependencies

```
Flask==3.0.0
Flask-SocketIO==5.3.6
python-socketio==5.11.0
eventlet==0.34.2
```
"""


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def response_error(status, body=None):
    from src.llm_providers.server_errors import mark_response_error
    request_info = types.SimpleNamespace(real_url="https://oli.example.com/api/chat", url="https://oli.example.com/api/chat",
                                         method="POST", headers={})
    error = aiohttp.ClientResponseError(request_info, (), status=status, message=f"Status {status}")
    return mark_response_error(error, body) if body is not None else error


@pytest.fixture
def python_on_path(monkeypatch):
    """The sandbox runs `python -m pytest` from PATH: put this interpreter's directory first (a venv run need not be active)."""
    bin_dir = str(Path(sys.executable).parent)
    if not (Path(bin_dir) / "python").exists():
        pytest.skip("no 'python' next to this interpreter")
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))


# ======================================================================================== 1. gateway / proxy errors
def test_gateway_statuses_and_html_pages_mean_the_server_did_not_answer():
    from src.llm_providers.server_errors import is_server_unavailable_error, server_unavailable_status, looks_like_html
    assert looks_like_html(OFFLINE_PAGE) and not looks_like_html('{"error":"model requires more system memory"}')
    for status in (502, 503, 504, 520, 522, 524, 530):
        assert server_unavailable_status(status), status
        assert is_server_unavailable_error(response_error(status, OFFLINE_PAGE))
    assert is_server_unavailable_error(response_error(524))                   # no body known: the status says it
    assert is_server_unavailable_error(response_error(500, OFFLINE_PAGE))     # the proxy's page
    # Ollama's own error is a model error: failover may still try another model
    assert not is_server_unavailable_error(response_error(500, '{"error":"model requires more system memory (12 GiB)"}'))
    assert not is_server_unavailable_error(response_error(500))
    assert not is_server_unavailable_error(response_error(404, OFFLINE_PAGE))
    assert not is_server_unavailable_error(ValueError("Status 524"))
    assert not is_server_unavailable_error(None)
    # the OpenAI client's errors (OpenRouter, vLLM) keep their handling: an OpenRouter 502 is often one upstream model
    import httpx
    import openai
    upstream = openai.APIStatusError("Bad gateway", response=httpx.Response(502, request=httpx.Request(
        "POST", "https://openrouter.ai/api/v1/chat/completions")), body=None)
    assert not is_server_unavailable_error(upstream)


def test_a_gateway_error_is_a_provider_level_error_for_the_outcome():
    from src.agents.cycle_components.outcome_determiner import CycleOutcomeDeterminer
    from src.agents.failover_handler import is_provider_level_error
    assert is_provider_level_error(response_error(524, OFFLINE_PAGE))
    assert is_provider_level_error(asyncio.TimeoutError())
    assert not is_provider_level_error(response_error(500, '{"error":"llama runner process has terminated"}'))
    context = types.SimpleNamespace(agent=types.SimpleNamespace(agent_id="W2"), last_error_obj=response_error(524, OFFLINE_PAGE),
                                    last_error_content="[Ollama Error]: Max retries. Last: Status 524 - <!DOCTYPE html>",
                                    is_retryable_error_type=None, is_key_related_error=None, is_provider_level_error=False,
                                    trigger_failover=False, cycle_completed_successfully=False)
    with quiet():
        CycleOutcomeDeterminer().determine_cycle_outcome(context)
    # not counted as the agent's (empty) reply by the Guardian; failover skips the server
    assert context.is_provider_level_error is True and context.trigger_failover is True
    context.last_error_obj, context.is_provider_level_error = response_error(500, '{"error":"out of memory"}'), False
    with quiet():
        CycleOutcomeDeterminer().determine_cycle_outcome(context)
    assert context.is_provider_level_error is False and context.trigger_failover is True


class TestFailoverAfterAGatewayError:
    def setup_method(self):
        from src.agents import failover_handler
        self.fh = failover_handler
        failover_handler._models_without_tool_support.clear()
        self.agent = types.SimpleNamespace(
            agent_id="W2", agent_type="worker", provider_name="ollama-local-oli-gaborkukucska-com",
            model="qwen3.5:4b-q4_K_M", agent_config={"config": {}}, _failover_state={}, set_status=MagicMock())
        tracker = MagicMock()
        tracker.get_metrics.return_value = None
        self.manager = types.SimpleNamespace(agents={"W2": self.agent}, send_to_ui=AsyncMock(), performance_tracker=tracker)
        self.patches = [patch.object(failover_handler, "model_registry"),
                        patch.object(failover_handler, "_check_provider_health", new_callable=AsyncMock),
                        patch.object(failover_handler, "_try_switch_agent", new_callable=AsyncMock)]
        self.registry, self.health, self.switch = (p.start() for p in self.patches)
        model = lambda mid, size: {"id": mid, "num_parameters": size, "provider": "x"}  # noqa: E731
        self.registry.get_available_models_dict.return_value = {"ollama-local-oli-gaborkukucska-com": [
            model("qwen3.5:4b-q4_K_M", 4e9), model("qwen3.5:9b-q4_K_M", 9e9), model("gemma4:12b", 12e9)]}
        self.registry.get_reachable_provider_url.side_effect = lambda provider: "https://oli.gaborkukucska.com"
        self.registry.get_model_info.return_value = None
        self.health.return_value = True   # 20:21:16 the health check of the server still passed
        self.switch.return_value = True

    def teardown_method(self):
        for p in self.patches:
            p.stop()
        self.fh._models_without_tool_support.clear()

    def tried(self):
        return [(c.args[2], c.args[3]) for c in self.switch.call_args_list]

    def test_no_other_model_on_the_same_server(self):
        with quiet():
            switched = run(self.fh.handle_agent_model_failover(self.manager, "W2", response_error(524, OFFLINE_PAGE)))
        # 20:21:17 W2 was switched to gemma4:12b on the same server; now: nothing to switch to - retry its own model later
        assert switched is False and self.tried() == []

    def test_another_server_with_the_same_model_is_still_used(self):
        models = self.registry.get_available_models_dict.return_value
        models["ollama-local-192-168-1-5"] = [{"id": "qwen3.5:4b-q4_K_M", "num_parameters": 4e9}]
        self.registry.get_reachable_provider_url.side_effect = lambda provider: f"http://{provider}"
        with quiet():
            switched = run(self.fh.handle_agent_model_failover(self.manager, "W2", response_error(524, OFFLINE_PAGE)))
        assert switched is True and self.tried() == [("ollama-local-192-168-1-5", "qwen3.5:4b-q4_K_M")]

    def test_a_model_error_of_the_server_may_still_try_another_model(self):
        with quiet():
            run(self.fh.handle_agent_model_failover(self.manager, "W2",
                                                    response_error(500, '{"error":"model requires more system memory"}')))
        assert self.tried() and self.tried()[0][0] == "ollama-local-oli-gaborkukucska-com"


def test_a_successful_cycle_resets_the_outage_backoff():
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    agent = types.SimpleNamespace(agent_id="W1", _failover_exhaustion_retry_count=4, message_history=[], state="worker_work")
    context = types.SimpleNamespace(agent=agent, all_tool_results=[], last_error_obj=None, trigger_failover=False)
    scheduler = NextStepScheduler(types.SimpleNamespace())
    with quiet():
        try:
            run(scheduler.schedule_next_step(context))
        except Exception:
            pass  # the rest of the scheduling needs a real manager; only the reset is checked here
    assert agent._failover_exhaustion_retry_count == 0


# ======================================================================================== 2. streamed tool-call replies
async def _serve(handler):
    from aiohttp import web
    app = web.Application()
    app.router.add_route("POST", "/api/chat", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


TOOLS = [{"type": "function", "function": {"name": "file_system", "description": "files",
                                           "parameters": {"type": "object", "properties": {}}}}]
REGISTRY = types.SimpleNamespace(get_model_info=lambda model: {"model_capabilities": ["completion", "tools"]})


def _stream_lines(lines, gap=0.0):
    async def chat(request):
        from aiohttp import web
        chat.calls.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        for line in lines:
            await response.write((json.dumps(line) + "\n").encode())
            if gap:
                await asyncio.sleep(gap)
        await response.write_eof()
        return response
    chat.calls = []
    return chat


def _events(handler, monkeypatch, **settings_values):
    from src.llm_providers import ollama_provider as op
    from src.llm_providers.ollama_provider import OllamaProvider
    from src.config.settings import settings
    monkeypatch.setattr(op, "MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    for name, value in settings_values.items():
        monkeypatch.setattr(settings, name, value, raising=False)

    async def go():
        runner, url = await _serve(handler)
        try:
            provider = OllamaProvider(base_url=url, model_registry=REGISTRY)
            return [e async for e in provider.stream_completion(
                messages=[{"role": "user", "content": "fix src/game_logic.py"}], model="qwen3.5:4b-q4_K_M",
                temperature=0.6, max_tokens=256, tools=TOOLS)]
        finally:
            await runner.cleanup()
    with quiet():
        return run(go())


def test_a_tool_call_reply_is_streamed_and_collected_into_one_call(monkeypatch):
    call = {"function": {"name": "file_system", "arguments": {"action": "read", "filename": "src/game_logic.py"}}}
    chat = _stream_lines([
        {"message": {"role": "assistant", "content": "", "thinking": "I need to read "}, "done": False},
        {"message": {"role": "assistant", "content": "", "thinking": "the file first."}, "done": False},
        {"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "eval_count": 42,
         "prompt_eval_count": 6223, "eval_duration": 5e8, "prompt_eval_duration": 8e8},
    ], gap=0.01)
    events = _events(chat, monkeypatch)
    assert chat.calls[0]["stream"] is True and chat.calls[0]["tools"]
    kinds = [e["type"] for e in events if e["type"] != "status"]
    assert kinds == ["finish_info", "native_tool_calls"], events
    assert next(e for e in events if e["type"] == "native_tool_calls")["tool_calls"] == [call]
    finish = next(e for e in events if e["type"] == "finish_info")
    assert finish["done_reason"] == "stop" and finish["output_tokens"] == 42 and finish["prompt_tokens"] == 6223
    assert finish["thinking_chars"] == len("I need to read the file first.")


def test_a_streamed_text_reply_comes_as_one_text(monkeypatch):
    chat = _stream_lines([{"message": {"role": "assistant", "content": part}, "done": False}
                          for part in ("The task ", "is done.")]
                         + [{"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                             "eval_count": 5}])
    events = _events(chat, monkeypatch)
    assert [e["content"] for e in events if e["type"] == "response_chunk"] == ["The task is done."]
    # a text that is only a tool call written as JSON becomes that call, as with a non-streamed reply
    as_text = json.dumps({"name": "file_system", "arguments": {"action": "list"}})
    chat = _stream_lines([{"message": {"role": "assistant", "content": as_text}, "done": False},
                          {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}])
    events = _events(chat, monkeypatch)
    assert [e["tool_calls"] for e in events if e["type"] == "native_tool_calls"] == [
        [{"function": {"name": "file_system", "arguments": {"action": "list"}}}]]


def test_an_error_line_in_the_stream_is_an_error_event(monkeypatch):
    chat = _stream_lines([{"error": "model requires more system memory"}])
    events = _events(chat, monkeypatch)
    errors = [e for e in events if e["type"] == "error"]
    assert errors and "model requires more system memory" in errors[0]["content"]


def test_the_switch_sends_the_old_non_streamed_request(monkeypatch):
    calls = []

    async def chat(request):
        from aiohttp import web
        calls.append(await request.json())
        return web.json_response({"message": {"role": "assistant", "content": "ok"}, "done": True, "done_reason": "stop"})
    events = _events(chat, monkeypatch, OLLAMA_STREAM_TOOL_CALLS=False)
    assert calls[0]["stream"] is False
    assert [e["content"] for e in events if e["type"] == "response_chunk"] == ["ok"]


def test_a_streamed_request_that_never_answers_is_given_up(monkeypatch):
    from src.llm_providers import ollama_provider as op
    monkeypatch.setattr(op, "NON_STREAM_MIN_SECONDS", 0.5)
    monkeypatch.setattr(op, "NON_STREAM_MARGIN_SECONDS", 0.0)
    op.note_output_rate  # noqa: B018 - rates are needed for a short first-line timeout
    calls = []

    async def chat(request):
        calls.append(await request.json())
        await asyncio.sleep(5)
        from aiohttp import web
        return web.json_response({"done": True})

    from src.llm_providers.ollama_provider import OllamaProvider
    from src.config.settings import settings
    monkeypatch.setattr(op, "MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)

    async def go():
        runner, url = await _serve(chat)
        try:
            provider = OllamaProvider(base_url=url, model_registry=REGISTRY)
            op.note_output_rate(f"{url}|m", {"eval_count": 1000, "eval_duration": 1e9, "prompt_eval_count": 10000,
                                            "prompt_eval_duration": 1e9})
            started = time.monotonic()
            events = [e async for e in provider.stream_completion(
                messages=[{"role": "user", "content": "x"}], model="m", temperature=0.6, max_tokens=64, tools=TOOLS)]
            return events, time.monotonic() - started
        finally:
            await runner.cleanup()
    with quiet():
        events, seconds = run(go())
    assert calls and calls[0]["stream"] is True
    assert any(e.get("type") == "error" for e in events) and seconds < 4, (events, seconds)


def test_a_proxy_page_after_the_retries_carries_its_body(monkeypatch):
    async def chat(request):
        from aiohttp import web
        return web.Response(status=524, text=OFFLINE_PAGE, content_type="text/html")
    events = _events(chat, monkeypatch)
    error = next(e for e in events if e["type"] == "error")
    from src.agents.failover_handler import is_provider_level_error
    assert is_provider_level_error(error["_exception_obj"])

    async def ollama_error(request):
        from aiohttp import web
        return web.json_response({"error": "model requires more system memory (12.1 GiB) than is available"}, status=500)
    events = _events(ollama_error, monkeypatch)
    assert not is_provider_level_error(next(e for e in events if e["type"] == "error")["_exception_obj"])


# ======================================================================================== 3. another worker's task
class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids, self.message_inbox = [], {}, []
        self.agent_config = {}


class Handler:
    def __init__(self):
        self.sent = []

    async def route_and_activate_agent_message(self, sender_id, target_identifier, message_content):
        self.sent.append((sender_id, target_identifier, message_content))
        return {"status": "queued", "message": "queued"}, None


def _manager(*agents):
    teams = {a.agent_id: f"team_{PROJECT}" for a in agents if a.agent_type != "admin"}
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
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    call.workspace = workspace
    return call


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_7 = "Kick-off Task 7: Write pytest tests for game logic, server, player management, scoring, and leaderboard in tests/"
TASK_8 = "Kick-off Task 8: Write README.md with setup instructions, hosting guide, and usage examples"


def test_the_ownership_check_on_its_own():
    from src.tools.project_management import foreign_task_change_error
    pm1, w1, w2 = Agent("PM1", "pm", "pm_report_check"), Agent("W1", state="worker_work"), Agent("W2", state="worker_report")
    manager = _manager(pm1, w1, w2)
    task = {"uuid": "cf11ea4a-251d-4d7b-b6e5-6c2436ef8c04", "description": TASK_7, "assignee": "W1"}
    refused = foreign_task_change_error(task, task["uuid"], "W2", manager, finishing=True)
    assert refused["error_type"] == "foreign_task"
    assert refused["message"].startswith(f"Task '{task['uuid']}' ('{TASK_7[:97]}...') was NOT marked finished: it is "
                                         "assigned to W1, not to you (W2)")
    assert foreign_task_change_error(task, task["uuid"], "W1", manager) is None            # its own task
    assert foreign_task_change_error(task, task["uuid"], "PM1", manager) is None           # the PM
    assert foreign_task_change_error(task, task["uuid"], "framework", manager) is None
    assert foreign_task_change_error(dict(task, assignee="PM1"), "t", "W2", manager) is None  # a decomposed parent
    assert foreign_task_change_error(dict(task, assignee=""), "t", "W2", manager) is None
    assert foreign_task_change_error(task, "t", "W2", None) is None                         # unknown caller: not judged


@needs_tw
def test_a_worker_cannot_finish_a_task_that_was_given_to_another_worker(tasks):
    pm1, w1, w2 = Agent("PM1", "pm", "pm_report_check"), Agent("W1", state="worker_wait"), Agent("W2", state="worker_report")
    manager = _manager(pm1, w1, w2)
    t7 = _add(tasks, manager, TASK_7, "W2")
    assert tasks("PM1", manager, action="modify_task", task_id=t7, assignee_agent_id="W1")["status"] == "success"
    refused = tasks("W2", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "foreign_task", refused
    assert "it is assigned to W1, not to you (W2)" in refused["message"]
    assert tasks("W2", manager, action="complete_task", task_id=t7)["error_type"] == "foreign_task"
    assert tasks("W2", manager, action="modify_task", task_id=t7, description=TASK_7 + " Also more.")["error_type"] \
        == "foreign_task"
    listed = tasks("PM1", manager, action="list_tasks", assignee_filter="W1")
    assert t7 in json.dumps(listed)
    # its own task is unaffected
    t8 = _add(tasks, manager, TASK_8, "W2")
    (tasks.workspace / "README.md").write_text("# WiFi Snake\n\nRun `python src/server.py`.\n")
    assert tasks("W2", manager, action="modify_task", task_id=t8, task_progress="finished")["status"] == "success"


# ======================================================================================== 4. tests that call missing code
GAME = '''"""Game logic."""


class Snake:
    def __init__(self, body):
        self.body = list(body)

    def eat_food(self, food_position):
        return self.body[0] == food_position


class Game:
    def __init__(self, width=20, height=20):
        self.snake = Snake([(5, 5)])
        self.score = 0

    def step(self):
        self.score += 1
        return self.score
'''
WRONG_TESTS = '''from game import Game, Snake


def test_update_moves():
    game = Game()
    game.update()
    assert game.score == 1


def test_eat_food():
    snake = Snake([(1, 1)])
    assert snake.eat_food()


def test_step_scores():
    assert Game().step() == 1
'''
RIGHT_TESTS_WITH_A_BUG_FOUND = '''from game import Game, Snake


def test_step_scores_ten():
    assert Game().step() == 10   # the code computes 1: a bug the test found, not a call of missing code


def test_eat_food():
    assert Snake([(1, 1)]).eat_food((1, 1))
'''


def _framework_conftest(root):
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST
    (Path(root) / "conftest.py").write_text(FRAMEWORK_CONFTEST)  # the kick-off writes it for a Python project


def _workspace(tmp_path, tests_text, conftest=True):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "tests" / "test_game").mkdir(parents=True)
    (root / "src" / "game.py").write_text(GAME)
    (root / "tests" / "test_game" / "test_game.py").write_text(tests_text)
    if conftest:
        _framework_conftest(root)
    return root


def test_test_writing_tasks_and_their_files(tmp_path):
    from src.utils.test_task_check import is_test_writing_task, named_directories, task_python_test_files
    assert is_test_writing_task(TASK_7)
    assert is_test_writing_task("Create unit tests for src/score_manager.py")
    assert not is_test_writing_task(TASK_8)
    assert not is_test_writing_task("Implement player registration in src/player_manager.py")
    assert named_directories(TASK_7) == ["tests"]
    assert named_directories("Write tests in tests/unit/ and docs in docs/") == ["tests/unit", "docs"]
    root = _workspace(tmp_path, WRONG_TESTS)
    (root / "tests" / "conftest.py").write_text("")
    (root / "tests" / "helpers.py").write_text("")
    assert task_python_test_files(root, TASK_7) == ["tests/test_game/test_game.py"]
    # a code task: only the test files it names or changed
    assert task_python_test_files(root, "Implement src/game.py") == []
    assert task_python_test_files(root, "Implement src/game.py",
                                  [root / "tests" / "test_game" / "test_game.py"]) == ["tests/test_game/test_game.py"]


def test_the_run_s_test_errors_that_call_missing_code():
    from src.utils.test_diagnosis import calls_missing_code
    output = """____________________________ TestGame.test_update ____________________________
tests/test_game_logic/test_game_logic.py:219: in test_update
    game.update()
    ^^^^^^^^^^^
E   AttributeError: 'Game' object has no attribute 'update'
____________________________ TestSnake.test_eat ____________________________
tests/test_game_logic/test_game_logic.py:106: in test_eat_food
    assert snake.eat_food()
E   TypeError: Snake.eat_food() missing 1 required positional argument: 'food_position'
____________________________ TestRooms.test_join ____________________________
tests/test_player_manager/test_player_manager.py:60: in test_join_room
    manager.join_room('p1', 'room')
src/player_manager.py:84: in join_room
    color = COLORS[name]
E   KeyError: 'red'
____________________________ TestScores.test_total ____________________________
tests/test_score_manager/test_score_manager.py:64: in test_total
    assert manager.total('p1') == 80
E   assert 0 == 80
____________________________ TestApi.test_none ____________________________
tests/test_server/test_server.py:90: in test_none
    assert response.json.name
E   AttributeError: 'NoneType' object has no attribute 'name'
____________________________ TestApi.test_client ____________________________
tests/test_server/test_server.py:36: in setup_method
    self.client = socketio.server.test_client(app)
E   AttributeError: 'Server' object has no attribute 'test_client'
"""
    assert calls_missing_code(None, output) == [
        ("AttributeError", "'Game' object has no attribute 'update'", ("tests/test_game_logic/test_game_logic.py", 219)),
        ("TypeError", "Snake.eat_food() missing 1 required positional argument: 'food_position'",
         ("tests/test_game_logic/test_game_logic.py", 106)),
        ("AttributeError", "'Server' object has no attribute 'test_client'", ("tests/test_server/test_server.py", 36)),
    ]  # not: a KeyError in the code, an assertion, an attribute of None


def test_a_test_task_with_tests_of_missing_code_is_refused(tmp_path, python_on_path):
    from src.utils.test_task_check import test_task_problem
    root = _workspace(tmp_path, WRONG_TESTS)
    with quiet():
        problem = run(test_task_problem(root, TASK_7))
    assert problem and problem.startswith("its tests call code the project does NOT have"), problem
    assert ("- tests/test_game/test_game.py line 6: AttributeError: 'Game' object has no attribute 'update'" in problem)
    assert ("- tests/test_game/test_game.py line 12: TypeError: Snake.eat_food() missing 1 required positional argument: "
            "'food_position'") in problem
    assert "src/game.py line 8 defines `def eat_food(self, food_position)`" in problem
    assert "Do not change working code to fit a test." in problem
    # tests that call the real code and find a wrong result are fine
    (root / "tests" / "test_game" / "test_game.py").write_text(RIGHT_TESTS_WITH_A_BUG_FOUND)
    with quiet():
        assert run(test_task_problem(root, TASK_7)) is None
    # no Python test files: nothing to run
    assert run(test_task_problem(root, TASK_8)) is None
    # a CODE task whose worker changed the test: the code and the test must agree
    (root / "tests" / "test_game" / "test_game.py").write_text(WRONG_TESTS)
    with quiet():
        problem = run(test_task_problem(root, "Kick-off Task 1: Implement the game logic in src/game.py",
                                        [root / "tests" / "test_game" / "test_game.py"]))
    assert "Make the code and its tests agree" in problem and "Do not change working code" not in problem


@needs_tw
def test_the_test_task_is_not_marked_finished_with_tests_of_missing_code(tasks, python_on_path):
    pm1, w2 = Agent("PM1", "pm", "pm_manage"), Agent("W2", state="worker_work")
    manager = _manager(pm1, w2)
    (tasks.workspace / "src").mkdir()
    (tasks.workspace / "src" / "game.py").write_text(GAME)
    (tasks.workspace / "tests" / "test_game").mkdir(parents=True)
    (tasks.workspace / "tests" / "test_game" / "test_game.py").write_text(WRONG_TESTS)
    _framework_conftest(tasks.workspace)
    t7 = _add(tasks, manager, TASK_7, "W2")
    refused = tasks("W2", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "tests_call_missing_code", refused
    assert refused["message"].startswith(f"Task '{t7}' was NOT marked finished: its tests call code the project does NOT")
    assert tasks("PM1", manager, action="complete_task", task_id=t7)["error_type"] == "tests_call_missing_code"
    (tasks.workspace / "tests" / "test_game" / "test_game.py").write_text(RIGHT_TESTS_WITH_A_BUG_FOUND)
    assert tasks("W2", manager, action="modify_task", task_id=t7, task_progress="finished")["status"] == "success"


# ======================================================================================== 5. requirements.txt
def test_the_markdown_requirements_file_is_not_a_requirements_file(tmp_path):
    from src.tools.source_check import check_kind, requirements_issue, file_problem, REQUIREMENTS
    for name in ("requirements.txt", "requirements-dev.txt", "dev-requirements.txt", "requirements/base.txt",
                 "requirements.in"):
        assert check_kind(name) == REQUIREMENTS, name
    for name in ("notes.txt", "docs/requirements.md", "reqs.txt", "src/requirements.py"):
        assert check_kind(name) != REQUIREMENTS, name
    issue = requirements_issue(MARKDOWN_REQUIREMENTS)
    assert (issue.line, issue.message) == (5, "'```' is not a package requirement")
    assert "NOT Markdown" in issue.hint()
    path = tmp_path / "requirements.txt"
    path.write_text(MARKDOWN_REQUIREMENTS)
    assert file_problem(path, "requirements.txt") == (
        "requirements.txt is not valid pip requirements ('```' is not a package requirement at line 5).")
    # pip ignores a '- Flask' bullet without a word: Flask would never be installed
    assert requirements_issue("Flask==3.0.0\n- flask-socketio\n").line == 2
    assert requirements_issue("Flask = 3.0").line == 1
    valid = ("# core\nFlask==3.0.0\nflask-socketio>=5.3,<6  # realtime\npython-socketio[client]==5.11.0\n"
             "eventlet ; python_version < \"3.13\"\n-r requirements-dev.txt\n--index-url https://pypi.org/simple\n-e .\n"
             "./local/pkg\ngit+https://github.com/x/y.git#egg=y\nmypkg @ https://example.com/mypkg-1.0.tar.gz\n"
             "requests==2.31.0 --hash=sha256:abcdef \\\n    --hash=sha256:012345\nlegacy (>=1.0)\npkg==${VERSION}\n")
    assert requirements_issue(valid) is None


def test_writing_the_markdown_requirements_file_is_rejected(tmp_path):
    from src.tools.file_system import FileSystemTool
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    with quiet():
        written = run(FileSystemTool().execute(agent_id="W2", agent_sandbox_path=sandbox, action="write",
                                               filename="requirements.txt", content=MARKDOWN_REQUIREMENTS, scope="private"))
    assert written["status"] == "error", written
    assert "is not valid pip requirements" in written["message"] and "```" in written["message"]
    assert not (sandbox / "requirements.txt").exists()
    with quiet():
        written = run(FileSystemTool().execute(agent_id="W2", agent_sandbox_path=sandbox, action="write",
                                               filename="requirements.txt", content="Flask==3.0.0\nFlask-SocketIO==5.3.6\n",
                                               scope="private"))
    assert written["status"] == "success", written


# ======================================================================================== 6. unplanned PM work
@needs_tw
def test_the_pm_s_unplanned_deployment_and_faq_tasks_are_refused(tasks):
    pm1, w1, w2 = Agent("PM1", "pm", "pm_manage"), Agent("W1"), Agent("W2")
    manager = _manager(pm1, w1, w2)
    kickoff = ("PROJECT KICK-OFF: WiFi_Snake_Multiplayer  Full Plan Overview: **Objective:** Create a browser-based "
               "multiplayer snake game with WiFi local network support, player naming, scoring, and leaderboard. "
               "8. **Documentation:** Write `README.md` with setup instructions, hosting guide, and usage examples. "
               "9. **Verification:** Run tests and deploy the game locally to verify all features work correctly.")
    added = tasks("framework", manager, action="add_task", description=kickoff, project_filter=PROJECT,
                  tags="project_kickoff", assignee_agent_id="W1")
    assert added["status"] == "success", added
    for title, description in (
            ("Create deployment scripts and deployment documentation",
             "Create deployment scripts (docker-compose.yml, requirements.txt, or similar) and documentation for hosting "
             "the game on a local network."),
            ("Create comprehensive user documentation and FAQ",
             "Create comprehensive user documentation and FAQ: Create detailed user documentation including installation "
             "guide, game rules, controls, and a FAQ section.")):
        refused = tasks("PM1", manager, action="add_task", title=title, description=description, project_filter=PROJECT)
        assert refused.get("error_type") == "unplanned_improvement", (title, refused)
    # a problem that was found, and planned work, are still fine
    fix = tasks("PM1", manager, action="add_task", title="Fix: the docker build fails",
                description="Fix: the docker build fails because requirements.txt is not valid", project_filter=PROJECT)
    assert fix["status"] == "success", fix


# ======================================================================================== 7. a pytest run that does not end
def test_pytest_commands_get_the_hang_report():
    from src.tools.sandbox_runner import with_pytest_hang_report, pytest_hang_report_seconds
    assert pytest_hang_report_seconds(30) == 15 and pytest_hang_report_seconds(6) == 5 and pytest_hang_report_seconds(120) == 60
    env = {"PATH": "/usr/bin"}
    assert with_pytest_hang_report(env, "python -m pytest -q", 30)["PYTEST_ADDOPTS"] == "-o faulthandler_timeout=15"
    assert with_pytest_hang_report({"PYTEST_ADDOPTS": "-x"}, "pytest", 60)["PYTEST_ADDOPTS"] == \
        "-o faulthandler_timeout=30 -x"
    assert with_pytest_hang_report(env, "npm test", 30) is env
    own = {"PYTEST_ADDOPTS": "-o faulthandler_timeout=3"}
    assert with_pytest_hang_report(own, "pytest", 30) is own


DUMP = """..Timeout (0:00:15)!
Thread 0x00007f35ad9ac740 (most recent call first):
  File "{root}/src/game_logic.py", line 79 in run_game_loop
  File "{root}/src/game_logic.py", line 150 in run
  File "{root}/tests/test_game_logic/test_game_logic.py", line 330 in test_run_game
  File "/home/tom/TrippleEffect/.venv/lib/python3.14/site-packages/_pytest/python.py", line 167 in pytest_pyfunc_call
  File "<frozen runpy>", line 88 in _run_code
"""


def test_the_hang_report_names_the_test_and_the_line(tmp_path):
    from src.utils.test_diagnosis import hung_test_note
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "tests" / "test_game_logic").mkdir(parents=True)
    (root / "src" / "game_logic.py").write_text("\n" * 78 + "        while True:\n" + "\n" * 70 + "        x\n")
    (root / "tests" / "test_game_logic" / "test_game_logic.py").write_text("")
    note = hung_test_note(root, DUMP.format(root=root))
    assert note == (
        "[Framework check - where the test run was stuck:]\n"
        "The test `test_run_game` (tests/test_game_logic/test_game_logic.py line 330) did not end: after 0:00:15 it was "
        "still running run_game_loop() at src/game_logic.py line 79: `while True:`.\n"
        "Called from: src/game_logic.py line 150 in run().\n"
        "Fix THAT code path: a loop that never ends (a game loop, `while True`, a wait for a flag nothing sets), or a wait "
        "for something that never comes (a server, a socket, input, a thread) - a test must call code that returns. "
        "Running the tests again or with a longer timeout will not help.")
    assert hung_test_note(root, "....\nTest command timed out") is None
    assert hung_test_note(root, DUMP.format(root="/elsewhere")) is None   # no project frame


def test_a_looping_test_is_reported_by_the_test_runner(tmp_path, python_on_path):
    from src.tools.test_runner import TestRunnerTool
    from src.utils.test_runs import clear_test_runs
    root = tmp_path / "private"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "game.py").write_text("class Game:\n    def run(self):\n        while True: pass\n")
    (root / "tests" / "test_game.py").write_text("from game import Game\n\n\ndef test_run():\n    Game().run()\n")
    _framework_conftest(root)
    clear_test_runs()
    with quiet():
        result = run(TestRunnerTool().execute(agent_id="W2", agent_sandbox_path=root, command="python -m pytest -q",
                                              timeout=8, scope="private"))
    clear_test_runs()
    assert result["status"] == "error", result
    assert "the test run did not END" in result["message"]
    assert ("The test `test_run` (tests/test_game.py line 5) did not end: after 0:00:05 it was still running run() at "
            "src/game.py line 3: `while True: pass`.") in result["message"], result["message"]
    assert "while a module is loaded" not in result["message"]  # the general guess is left out when the place is known

# END OF FILE tests/unit/test_round3u.py
