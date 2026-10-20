# START OF FILE tests/unit/test_model_registry.py
"""
Tests for ModelRegistry: parameter-size parsing and model discovery from Ollama and OpenRouter.

Round 2m: the two discovery tests never ran - they were 'async def' methods of a plain unittest.TestCase ("coroutine
... was never awaited"), awaited the synchronous _apply_filters() and mocked aiohttp in a way the code does not use
(session.post() as an awaitable instead of an async context manager). They now run against a real local HTTP server
(aiohttp.web on 127.0.0.1) that answers like Ollama (/api/tags, /api/show) and OpenRouter (/models).

Rules under test:
* '7B', '13.5b', '70G', '180K', '3M', '0.5B' -> parameter counts; anything else -> None;
* the parameters block of /api/show gives stop tokens and num_ctx;
* Ollama: every model of /api/tags is kept; /api/show adds num_parameters, family, template, stop tokens and num_ctx
  (from the parameters block, else from model_info '<arch>.context_length'); a failed /api/show keeps the model without
  details; the provider is 'ollama-local-127-0-0-1';
* OpenRouter: architecture.n_parameters is used only when it is a number; the tier filter keeps ':free' models in FREE.
"""
import logging
import unittest
from unittest.mock import MagicMock

from aiohttp import web

from src.config.model_registry import ModelRegistry
from src.config.settings import Settings

OLLAMA_TAGS = {"models": [{"name": "llama3:8b"}, {"name": "codegemma:7b"}, {"name": "phi3:3.8b"},
                          {"name": "no_details:latest"}, {"name": "broken:latest"}]}
OLLAMA_SHOW = {
    "llama3:8b": {"details": {"parameter_size": "8B", "family": "llama"}, "template": "{{ .System }} {{ .Prompt }}",
                  "parameters": 'num_ctx 8192\nstop "<|eot_id|>"\nstop "<|end|>"\ntemperature 0.7'},
    "codegemma:7b": {"details": {"parameter_size": "7B", "family": "gemma"},
                     "model_info": {"gemma.context_length": 8192}},
    "phi3:3.8b": {"details": {"parameter_size": "3.8B"}},
    "no_details:latest": {"details": {}},
}
OPENROUTER_MODELS = {"data": [
    {"id": "vendor/model1", "name": "Model One", "architecture": {"n_parameters": 7000000000}},
    {"id": "vendor/model2:free", "name": "Model Two", "architecture": {"n_parameters": "13B"}},
    {"id": "vendor/model3", "name": "Model Three", "architecture": {}},
    {"id": "vendor/model4", "name": "Model Four"},
    {"id": "vendor/model5", "name": "Model Five", "architecture": {"n_parameters": None}},
]}


def make_settings(tier="ALL"):
    settings = MagicMock(spec=Settings)
    settings.MODEL_TIER = tier
    settings.PROVIDER_API_KEYS = {"openrouter": ["fake_or_key_1234"]}
    settings.is_provider_configured = MagicMock(return_value=True)
    return settings


class TestParameterParsing(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.registry = ModelRegistry(settings_obj=make_settings())

    def test_parse_parameter_size_valid(self):
        parse = self.registry._parse_ollama_parameter_string_to_int
        self.assertEqual(parse("7B"), 7_000_000_000)
        self.assertEqual(parse("13.5B"), 13_500_000_000)
        self.assertEqual(parse("13.5b"), 13_500_000_000)
        self.assertEqual(parse("70G"), 70_000_000_000)
        self.assertEqual(parse("180K"), 180_000)
        self.assertEqual(parse("3M"), 3_000_000)
        self.assertEqual(parse("0.5B"), 500_000_000)
        self.assertEqual(parse("  7B  "), 7_000_000_000)

    def test_parse_parameter_size_invalid(self):
        parse = self.registry._parse_ollama_parameter_string_to_int
        for value in ("unknown", "7X", "B7", "", None, "1.2.3B"):
            with self.subTest(value=value):
                self.assertIsNone(parse(value))

    def test_parse_parameters_block(self):
        parse = self.registry._parse_ollama_parameters_string
        self.assertEqual(parse('num_ctx 65536\nstop "<end_of_turn>"\nstop \'<eos>\'\ntemperature 0.7'),
                         (["<end_of_turn>", "<eos>"], 65536))
        self.assertEqual(parse("temperature 0.7"), (None, None))
        self.assertEqual(parse(""), (None, None))
        self.assertEqual(parse(None), (None, None))


class TestDiscovery(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.requests = []
        app = web.Application()
        app.router.add_get("/api/tags", self.tags)
        app.router.add_post("/api/show", self.show)
        app.router.add_get("/models", self.openrouter_models)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def tags(self, request):
        self.requests.append(("GET", request.path, None))
        return web.json_response(OLLAMA_TAGS)

    async def show(self, request):
        name = (await request.json()).get("name")
        self.requests.append(("POST", request.path, name))
        if name not in OLLAMA_SHOW:
            return web.json_response({"error": "model not found"}, status=404)
        return web.json_response(OLLAMA_SHOW[name])

    async def openrouter_models(self, request):
        self.requests.append(("GET", request.path, request.headers.get("Authorization")))
        return web.json_response(OPENROUTER_MODELS)

    async def test_ollama_models_with_details(self):
        registry = ModelRegistry(settings_obj=make_settings())
        provider, url, models = await registry._verify_and_fetch_models(self.base_url)
        self.assertEqual(provider, "ollama-local-127-0-0-1")
        self.assertEqual(url, self.base_url)
        by_id = {m["id"]: m for m in models}
        self.assertEqual(list(by_id), ["llama3:8b", "codegemma:7b", "phi3:3.8b", "no_details:latest", "broken:latest"])
        self.assertEqual(by_id["llama3:8b"], {
            "id": "llama3:8b", "provider": provider, "name": "llama3:8b", "num_parameters": 8_000_000_000,
            "family": "llama", "model_template": "{{ .System }} {{ .Prompt }}",
            "model_stop_tokens": ["<|eot_id|>", "<|end|>"], "model_num_ctx": 8192})
        self.assertEqual(by_id["codegemma:7b"]["num_parameters"], 7_000_000_000)
        self.assertEqual(by_id["codegemma:7b"]["model_num_ctx"], 8192)  # from model_info
        self.assertEqual(by_id["phi3:3.8b"]["num_parameters"], 3_800_000_000)
        self.assertNotIn("num_parameters", by_id["no_details:latest"])
        self.assertEqual(by_id["broken:latest"], {"id": "broken:latest", "provider": provider, "name": "broken:latest"})
        self.assertEqual(sum(1 for r in self.requests if r[1] == "/api/show"), 5)

    async def test_a_url_that_is_not_an_api_is_not_verified(self):
        registry = ModelRegistry(settings_obj=make_settings())
        self.assertIsNone(await registry._verify_and_fetch_models(f"{self.base_url}/nothing-here"))

    async def test_openrouter_models_with_parameters(self):
        registry = ModelRegistry(settings_obj=make_settings())
        registry._reachable_providers["openrouter"] = self.base_url
        await registry._discover_openrouter_models()
        registry._apply_filters()
        params = {m["id"]: m.get("num_parameters") for m in registry.available_models["openrouter"]}
        self.assertEqual(params, {"vendor/model1": 7_000_000_000, "vendor/model2:free": None, "vendor/model3": None,
                                  "vendor/model4": None, "vendor/model5": None})
        self.assertIn(("GET", "/models", "Bearer fake_or_key_1234"), self.requests)

    async def test_free_tier_keeps_only_free_openrouter_models(self):
        registry = ModelRegistry(settings_obj=make_settings(tier="FREE"))
        registry._reachable_providers["openrouter"] = self.base_url
        await registry._discover_openrouter_models()
        registry._apply_filters()
        self.assertEqual([m["id"] for m in registry.available_models["openrouter"]], ["vendor/model2:free"])


if __name__ == '__main__':
    unittest.main()
