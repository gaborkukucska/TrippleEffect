# START OF FILE tests/unit/test_agent_lifecycle.py
"""
Tests for agent_lifecycle._select_best_available_model() and the provider-name helpers.

Round 2m: this file could not be imported (it imported PerformanceTracker, which is called ModelPerformanceTracker), so
`pytest tests` needed --ignore. Its async test methods were also never awaited by unittest, and it expected a 2-tuple
while the function returns (provider, model, base type or None, list index or None). These tests use the current API.

Rules under test:
* API-first round robin: local types in the order ollama, vllm, litellm; the instances of a type from the round-robin
  index (or the override); on an instance the largest model (then id); OCR/embedding/vision/llava and blacklisted models
  are skipped; LOCAL tier skips instances that are not local; returns (instance, model, base type, list index);
* comprehensive fallback (no local instance yields a model): size, then performance score, then id; LOCAL tier keeps
  only local providers, FREE tier keeps local and ':free' remote models; a remote provider must be configured and not
  depleted; the model must be available; nothing -> (None, None, None, None);
* the localhost Ollama ('ollama-local', no suffix) is a LOCAL provider (it was treated as remote before Round 2m).
"""
import asyncio
import logging
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents import agent_lifecycle, failover_handler
from src.agents.agent_lifecycle import _select_best_available_model
from src.config.model_registry import base_provider_type, is_local_provider_name


def _model(model_id, size=None):
    data = {"id": model_id}
    if size is not None:
        data["num_parameters"] = size
    return data


class SelectionBase(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        failover_handler._models_without_tool_support.clear()
        self.addCleanup(failover_handler._models_without_tool_support.clear)
        self.settings = patch.object(agent_lifecycle, "settings").start()
        self.addCleanup(patch.stopall)
        self.settings.MODEL_TIER = "ALL"
        self.settings.PROVIDER_API_KEYS = {}
        self.settings.is_provider_configured.return_value = True
        self.registry_models = {}
        self.metrics = {}
        registry = MagicMock()
        registry.available_models = self.registry_models
        registry.get_available_models_dict.side_effect = lambda: self.registry_models
        registry.is_model_available.side_effect = lambda provider, model_id: any(
            m["id"] == model_id for m in self.registry_models.get(provider, []))
        tracker = MagicMock()
        tracker.get_metrics.side_effect = lambda: self.metrics
        self.key_manager = MagicMock()
        self.key_manager.is_provider_depleted = AsyncMock(return_value=False)
        self.manager = types.SimpleNamespace(available_local_providers_list={}, local_api_usage_round_robin_index={},
                                             model_registry=registry, performance_tracker=tracker,
                                             key_manager=self.key_manager)

    def select(self, override=None):
        return asyncio.run(_select_best_available_model(self.manager, override))


class TestApiFirstRoundRobin(SelectionBase):

    def setUp(self):
        super().setUp()
        self.registry_models.update({
            "ollama-local": [_model("qwen3.5:4b", 4), _model("qwen3.5:9b", 9), _model("llava:13b", 13),
                             _model("nomic-embed-text", 1)],
            "ollama-local-10-0-0-5": [_model("gemma4:12b", 12)],
        })
        self.manager.available_local_providers_list = {"ollama": ["ollama-local", "ollama-local-10-0-0-5"]}

    def test_largest_usable_model_of_the_first_instance(self):
        self.assertEqual(self.select(), ("ollama-local", "qwen3.5:9b", "ollama", 0))

    def test_round_robin_index_and_override(self):
        self.manager.local_api_usage_round_robin_index = {"ollama": 1}
        self.assertEqual(self.select(), ("ollama-local-10-0-0-5", "gemma4:12b", "ollama", 1))
        self.assertEqual(self.select(override={"ollama": 2}), ("ollama-local", "qwen3.5:9b", "ollama", 0))

    def test_blacklisted_models_are_skipped(self):
        failover_handler.add_model_to_blacklist("ollama-local", "qwen3.5:9b")
        self.assertEqual(self.select(), ("ollama-local", "qwen3.5:4b", "ollama", 0))

    def test_an_instance_without_usable_models_is_skipped(self):
        self.registry_models["ollama-local"] = [_model("llava:13b", 13)]
        self.assertEqual(self.select(), ("ollama-local-10-0-0-5", "gemma4:12b", "ollama", 1))

    def test_local_tier_skips_instances_that_are_not_local(self):
        self.settings.MODEL_TIER = "LOCAL"
        self.manager.available_local_providers_list = {"ollama": ["ollama-remote-box", "ollama-local-10-0-0-5"]}
        self.registry_models["ollama-remote-box"] = [_model("big:70b", 70)]
        self.assertEqual(self.select(), ("ollama-local-10-0-0-5", "gemma4:12b", "ollama", 1))

    def test_depleted_keys_skip_the_type(self):
        self.settings.PROVIDER_API_KEYS = {"ollama": ["key"]}
        self.key_manager.is_provider_depleted = AsyncMock(side_effect=lambda base: base == "ollama")
        self.registry_models.clear()
        self.assertEqual(self.select(), (None, None, None, None))


class TestComprehensiveFallback(SelectionBase):

    def setUp(self):
        super().setUp()
        self.registry_models.update({
            "openrouter": [_model("remote-large", 100), _model("remote-medium:free", 50), _model("remote-small", 10)],
            "ollama-local-test": [_model("local-large", 110), _model("local-medium", 55)],
        })
        self.metrics.update({"openrouter": {"remote-large": {"score": 0.9}, "remote-medium:free": {"score": 0.95}},
                             "ollama": {"local-large": {"score": 0.85}}})

    def test_tier_all_largest_first(self):
        self.assertEqual(self.select(), ("ollama-local-test", "local-large", None, None))

    def test_tier_free_skips_paid_remote_models(self):
        self.settings.MODEL_TIER = "FREE"
        self.registry_models["ollama-local-test"] = []
        self.assertEqual(self.select(), ("openrouter", "remote-medium:free", None, None))

    def test_tier_local_skips_remote_providers(self):
        self.settings.MODEL_TIER = "LOCAL"
        self.registry_models["ollama-local-test"] = [_model("local-medium", 55)]
        self.assertEqual(self.select(), ("ollama-local-test", "local-medium", None, None))

    def test_depleted_or_unconfigured_remote_provider_is_skipped(self):
        self.registry_models["ollama-local-test"] = []
        self.key_manager.is_provider_depleted = AsyncMock(side_effect=lambda base: base == "openrouter")
        self.assertEqual(self.select(), (None, None, None, None))
        self.key_manager.is_provider_depleted = AsyncMock(return_value=False)
        self.settings.is_provider_configured.return_value = False
        self.assertEqual(self.select(), (None, None, None, None))

    def test_same_size_by_score_then_id(self):
        self.registry_models.clear()
        self.registry_models["openrouter"] = [_model("b-model", 10), _model("a-model", 10), _model("c-model", 10)]
        self.metrics.clear()
        self.metrics["openrouter"] = {"c-model": {"score": 0.9}}
        self.assertEqual(self.select()[:2], ("openrouter", "c-model"))
        self.metrics.clear()
        self.assertEqual(self.select()[:2], ("openrouter", "a-model"))

    def test_models_without_size(self):
        self.registry_models.clear()
        self.registry_models["openrouter"] = [_model("no-size-no-perf"), _model("no-size-has-perf"), _model("has-size", 10)]
        self.metrics.clear()
        self.metrics["openrouter"] = {"no-size-has-perf": {"score": 0.9}}
        self.assertEqual(self.select()[:2], ("openrouter", "has-size"))

    def test_no_models(self):
        self.registry_models.clear()
        self.assertEqual(self.select(), (None, None, None, None))

    def test_localhost_ollama_counts_as_local(self):
        # 'ollama-local' (the canonical localhost name) only reaches the fallback when it is not in the round-robin list
        self.settings.MODEL_TIER = "LOCAL"
        self.registry_models.clear()
        self.registry_models["ollama-local"] = [_model("qwen3.5:9b", 9)]
        self.assertEqual(self.select(), ("ollama-local", "qwen3.5:9b", None, None))


class TestProviderNames(unittest.TestCase):

    def test_base_provider_type(self):
        for name, base in [("ollama-local", "ollama"), ("ollama-local-10-0-0-5", "ollama"),
                           ("ollama-local-oli-gaborkukucska-com", "ollama"), ("ollama-local-1", "ollama"),
                           ("ollama-proxy", "ollama"), ("vllm-local-1-2-3-4", "vllm"), ("litellm-local", "litellm"),
                           ("openrouter", "openrouter"), ("openai", "openai"), ("ollama", "ollama"), ("", ""), (None, "")]:
            with self.subTest(name=name):
                self.assertEqual(base_provider_type(name), base)

    def test_is_local_provider_name(self):
        for name in ("ollama-local", "ollama-local-10-0-0-5", "ollama-proxy", "vllm-local-1-2-3-4", "litellm-local"):
            with self.subTest(name=name):
                self.assertTrue(is_local_provider_name(name))
        for name in ("openrouter", "openai", "ollama", "", None):
            with self.subTest(name=name):
                self.assertFalse(is_local_provider_name(name))


if __name__ == '__main__':
    unittest.main()
