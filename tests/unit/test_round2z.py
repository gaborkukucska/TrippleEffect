# START OF FILE tests/unit/test_round2z.py
"""
Round 2z: the 2026-10-22 15:26-15:36 run, with the Ollama server's own debug log (Ollama 0.40.8, qwen3.5:9b-q4_K_M).

15:29:22-15:29:59 PM1's modify_task (assign Kick-off Task 1 to W1) was dropped six times in a row: 199, 204, 185, 166, 146
and 141 output tokens, no text, no tool call (Round 2y recognised every one as lost output). The server logged no
error - only the sampler of every request:

    repeat_last_n = 64, repeat_penalty = 1.150, frequency_penalty = 0.000, presence_penalty = 1.500

repeat_penalty 1.15 was the framework's own default for every request; presence_penalty 1.5 is the model's default.
Both push the model away from tokens of the last 64 - and a tool call repeats '<parameter=...>' and '</parameter>' for
every argument (one-argument request_state calls always arrived). Qwen3.5 is meant to run coding and tool calling
with presence penalty 0 and repetition penalty 1.0. The penalties are now settings (OLLAMA_REPEAT_PENALTY,
OLLAMA_PRESENCE_PENALTY) with those defaults, and the options sent are logged once per model.
"""
import asyncio
import logging
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order


def run(coro):
    return asyncio.run(coro)


# ======================================================================================== the settings
@pytest.mark.parametrize("raw, expected", [
    (None, 1.0), ("1.0", 1.0), ("1.05", 1.05), (" 0 ", 0.0), ("", None), ("none", None), ("Default", None), ("abc", 1.0),
])
def test_optional_float_setting(monkeypatch, raw, expected):
    from src.config.settings import optional_float_setting
    if raw is None:
        monkeypatch.delenv("TE_TEST_PENALTY", raising=False)
    else:
        monkeypatch.setenv("TE_TEST_PENALTY", raw)
    logging.disable(logging.CRITICAL)
    try:
        assert optional_float_setting("TE_TEST_PENALTY", "1.0") == expected
    finally:
        logging.disable(logging.NOTSET)


def test_the_defaults_turn_the_penalties_off():
    from src.config.settings import settings
    assert settings.OLLAMA_REPEAT_PENALTY == 1.0 and settings.OLLAMA_PRESENCE_PENALTY == 0.0
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert "OLLAMA_REPEAT_PENALTY=1.0\nOLLAMA_PRESENCE_PENALTY=0.0\n" in env_example


# ======================================================================================== the options sent
def test_the_penalty_options(monkeypatch):
    from src.config.settings import settings
    from src.llm_providers.ollama_provider import sampling_penalty_options
    assert sampling_penalty_options({"temperature": 0.6}) == {"repeat_penalty": 1.0, "presence_penalty": 0.0}
    # a value the call or the agent config gives is kept
    assert sampling_penalty_options({"repeat_penalty": 1.1}) == {"presence_penalty": 0.0}
    assert sampling_penalty_options({}, {"presence_penalty": 0.5}) == {"repeat_penalty": 1.0}
    # an empty setting sends nothing: the model's own default applies
    monkeypatch.setattr(settings, "OLLAMA_PRESENCE_PENALTY", None)
    monkeypatch.setattr(settings, "OLLAMA_REPEAT_PENALTY", 1.05)
    assert sampling_penalty_options({}) == {"repeat_penalty": 1.05}


def _serve(routes):
    from aiohttp import web

    async def start():
        app = web.Application()
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    return start


def _requests(provider_kwargs_list, monkeypatch):
    from aiohttp import web
    from src.llm_providers import ollama_provider
    from src.llm_providers.ollama_provider import OllamaProvider
    monkeypatch.setattr(ollama_provider, "_sampling_logged", set())
    payloads = []

    async def chat(request):
        payloads.append(await request.json())
        return web.json_response({"model": "m", "done": True, "done_reason": "stop", "prompt_eval_count": 10,
                                  "eval_count": 2, "message": {"role": "assistant", "content": "ok"}})
    registry = types.SimpleNamespace(get_model_info=lambda model: {"model_capabilities": ["completion", "tools"]})
    history = [{"role": "system", "content": "You are PM1."}, {"role": "user", "content": "assign Kick-off Task 1"}]

    async def go():
        runner, url = await _serve([("POST", "/api/chat", chat)])()
        try:
            provider = OllamaProvider(base_url=url, model_registry=registry)
            for extra in provider_kwargs_list:
                [e async for e in provider.stream_completion(messages=history, model="qwen3.5:9b-q4_K_M", temperature=0.6,
                                                               max_tokens=64, **extra)]
        finally:
            await runner.cleanup()
    run(go())
    return payloads


def test_every_request_sends_the_penalties(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="src.llm_providers.ollama_provider"):
        payloads = _requests([{}, {}, {"repeat_penalty": 1.1}], monkeypatch)
    options = [p["options"] for p in payloads]
    assert options[0]["repeat_penalty"] == 1.0 and options[0]["presence_penalty"] == 0.0     # was 1.15 / (model) 1.5
    assert options[1]["repeat_penalty"] == 1.0
    assert options[2]["repeat_penalty"] == 1.1 and options[2]["presence_penalty"] == 0.0
    logged = [r.getMessage() for r in caplog.records if "sampling options sent" in r.getMessage()]
    assert logged == ["OllamaProvider: sampling options sent for 'qwen3.5:9b-q4_K_M': {'temperature': 0.6, "
                      "'repeat_penalty': 1.0, 'presence_penalty': 0.0} (options not listed use the model's own defaults)."]


def test_an_empty_setting_leaves_the_models_default(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "OLLAMA_PRESENCE_PENALTY", None)
    monkeypatch.setattr(settings, "OLLAMA_REPEAT_PENALTY", None)
    options = _requests([{}], monkeypatch)[0]["options"]
    assert "presence_penalty" not in options and "repeat_penalty" not in options


def test_the_old_default_is_gone():
    source = Path("src/llm_providers/ollama_provider.py").read_text(encoding="utf-8")
    assert 'valid_options["repeat_penalty"] = 1.15' not in source

# END OF FILE tests/unit/test_round2z.py
