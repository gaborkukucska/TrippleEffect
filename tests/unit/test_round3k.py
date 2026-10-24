# START OF FILE tests/unit/test_round3k.py
"""
Round 3k: the run of 2026-10-24 13:39-16:38 (WiFi_Snake_Game), with two internet outages and a browser reload.

* No task was lost: Taskwarrior still holds all 12 tasks (2 pending, 10 finished). After the reload (15:55:54) the task
  panel showed only its "Active" group open - the other groups (10 finished tasks) were closed again.
* 14:50:38 W1's request (tools attached, so NOT streamed: Ollama answers only when the reply is complete) was sent as the
  laptop lost its connection; it waited until 15:20:38 - 30 minutes in which the whole team stood still.
* 14:41-16:34 every one of W1's 49 test runs stopped before the first test ("Test suite failed to run": Cannot find module
  '@babel/preset-env'), so the "same tests keep failing" check never counted them.
"""
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(autouse=True)
def _clean():
    from src.llm_providers.ollama_provider import reset_output_rates
    from src.utils.test_diagnosis import clear_cache
    reset_output_rates()
    clear_cache()
    yield
    reset_output_rates()
    clear_cache()


# ======================================================================================== 1. a non-streamed request
def test_non_stream_read_timeout(monkeypatch):
    from src.llm_providers import ollama_provider as op
    from src.config.settings import settings
    key = "http://ollama|qwen3.5:4b-q4_K_M"
    assert op.non_stream_read_timeout(key, 60000, 8192) == op.DEFAULT_READ_TIMEOUT          # nothing measured yet
    # the run's speeds: prompt 3,000 tok/s (uncached), output 80 tok/s
    op.note_output_rate(key, {"eval_count": 800, "eval_duration": 10e9, "prompt_eval_count": 9000,
                              "prompt_eval_duration": 3e9})
    monkeypatch.setattr(settings, "OLLAMA_CONCURRENCY_LIMIT", 2, raising=False)
    # 60,000 chars ~ 20,000 tokens: (20000/3000 + 8192/80) x 2.5 + 120 = 392.7 s - not 1800
    assert op.non_stream_read_timeout(key, 60000, 8192) == pytest.approx(392.7, abs=0.2)
    assert op.non_stream_read_timeout(key, 3000, 256) == op.NON_STREAM_MIN_SECONDS           # at least 300 s
    monkeypatch.setattr(settings, "OLLAMA_CONCURRENCY_LIMIT", 1, raising=False)
    assert op.non_stream_read_timeout(key, 60000, 8192) == op.NON_STREAM_MIN_SECONDS         # 283.6 -> at least 300
    assert op.non_stream_read_timeout(key, 60000, 16384) == pytest.approx(
        (20000 / 3000 + 16384 / 80) * 1.5 + 120, abs=0.2)
    op.note_output_rate(key, {"eval_count": 100, "eval_duration": 50e9})                     # 2 tok/s: slow machine
    assert op.non_stream_read_timeout(key, 60000, 8192) == op.DEFAULT_READ_TIMEOUT           # capped at 1800 s
    op.note_output_rate("other|m", {"eval_count": 100, "eval_duration": 1e9})               # no prompt rate yet
    assert op.non_stream_read_timeout("other|m", 1000, 100) == op.DEFAULT_READ_TIMEOUT


def test_a_non_streamed_request_that_never_answers_is_given_up(monkeypatch):
    from aiohttp import web
    from src.llm_providers import ollama_provider as op
    from src.llm_providers.ollama_provider import OllamaProvider
    from src.config.settings import settings
    monkeypatch.setattr(op, "NON_STREAM_MIN_SECONDS", 0.5)
    monkeypatch.setattr(op, "NON_STREAM_MARGIN_SECONDS", 0.0)
    monkeypatch.setattr(op, "MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    calls = []

    async def chat(request):
        calls.append(await request.json())
        await asyncio.sleep(5)       # the connection is gone: no answer comes
        return web.json_response({"done": True})

    registry = types.SimpleNamespace(get_model_info=lambda model: {"model_capabilities": ["completion", "tools"]})
    tools = [{"type": "function", "function": {"name": "file_system", "description": "files",
                                               "parameters": {"type": "object", "properties": {}}}}]

    async def go():
        app = web.Application()
        app.router.add_route("POST", "/api/chat", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            provider = OllamaProvider(base_url=url, model_registry=registry)
            op.note_output_rate(f"{url}|m", {"eval_count": 1000, "eval_duration": 1e9, "prompt_eval_count": 10000,
                                            "prompt_eval_duration": 1e9})
            started = time.monotonic()
            events = [e async for e in provider.stream_completion(
                messages=[{"role": "user", "content": "fix server.js"}], model="m", temperature=0.6, max_tokens=64,
                tools=tools)]
            return events, time.monotonic() - started
        finally:
            await runner.cleanup()

    with quiet():
        events, seconds = run(go())
    assert calls and calls[0]["stream"] is False
    assert any(e.get("type") == "error" for e in events), events
    assert seconds < 4, seconds


# ======================================================================================== 2. test files that never run
JEST_NOT_RUN = """FAIL tests/server.test.js
  ● Test suite failed to run

    Cannot find module '@babel/preset-env'
    Require stack:
    - /ws/node_modules/@babel/core/lib/config/files/plugins.js

FAIL tests/client.test.js
  ● Test suite failed to run

    Cannot find module '@babel/preset-env'

Test Suites: 2 failed, 2 total
Tests:       0 total
"""


def test_suite_failures_and_the_missing_package_hint(tmp_path):
    from src.utils.test_diagnosis import suite_failures, output_hints
    assert suite_failures(JEST_NOT_RUN) == ["tests/server.test.js", "tests/client.test.js"]
    assert suite_failures("ERROR tests/test_game.py - ModuleNotFoundError: No module named 'game'\n1 error in 0.1s") == [
        "tests/test_game.py"]
    assert suite_failures("FAIL tests/a.test.js\n  ● A › b\n") == []
    hints = output_hints(tmp_path, JEST_NOT_RUN)
    assert hints == ["The npm package(s) @babel/preset-env are not installed, so the test file cannot even load. Either "
                     "install them (`npm install --save-dev @babel/preset-env`, which also adds them to package.json) or "
                     "remove what needs them (e.g. a babel `transform` in jest.config.js that plain Node.js code does not "
                     "need)."]
    assert output_hints(tmp_path, "Cannot find module '../src/game' from 'tests/game.test.js'") == []   # a project file


def test_test_files_that_never_run_are_counted(tmp_path):
    from src.utils.test_diagnosis import stuck_note, STUCK_RUNS
    ws = tmp_path
    (ws / "server").mkdir()
    server = ws / "server" / "server.js"
    server.write_text("module.exports = {};\n")
    notes = []
    for number in range(STUCK_RUNS):
        server.write_text(server.read_text() + f"// edit {number}\n")
        notes.append(stuck_note(ws, JEST_NOT_RUN))
    assert notes[:-1] == [None] * (STUCK_RUNS - 1)
    assert notes[-1].startswith("[Framework check - the test file(s) tests/client.test.js, tests/server.test.js have not "
                                "RUN at all in the last 4 runs, although server/server.js changed in between:]")
    assert "Read the FIRST error under it and fix exactly that" in notes[-1]


def test_a_piped_run_whose_suites_did_not_run_is_diagnosed(tmp_path, monkeypatch):
    import src.tools.command_executor as ce
    ws = tmp_path / "sandbox"
    ws.mkdir()
    (ws / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None, **kwargs):
        return types.SimpleNamespace(status="completed", returncode=0, stdout="", stderr=JEST_NOT_RUN, message="",
                                     background_log=None, stdout_truncated=False, stderr_truncated=False, cpu_busy=None)
    monkeypatch.setattr(ce, "run_sandboxed", fake_run)
    with quiet():
        answer = run(ce.CommandExecutionTool().execute(agent_id="W1", agent_sandbox_path=ws, scope="private",
                                                       action="run_command", command="npm test 2>&1 | tail -40"))
    assert "The npm package(s) @babel/preset-env are not installed" in answer["content"]


# ======================================================================================== 3. the task panel
def test_the_task_panel_keeps_its_groups_open_across_a_reload_and_shows_all_counts(tmp_path):
    source = (REPO / "static" / "js" / "ui.js").read_text(encoding="utf-8")
    assert "const OPEN_STATES_KEY = 'te_task_group_open_states';" in source
    assert "localStorage.getItem(OPEN_STATES_KEY)" in source and "localStorage.setItem(OPEN_STATES_KEY" in source
    assert "details.addEventListener('toggle'" in source
    assert "${tasksData.length} task(s): ${activeTasks.length} active, ${assignedTasks.length} assigned, " in source
    if shutil.which("node"):
        copy = tmp_path / "ui.mjs"
        copy.write_text(source, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

# END OF FILE tests/unit/test_round3k.py
