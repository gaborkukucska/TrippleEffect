# START OF FILE tests/unit/test_failover_handler.py
"""
Tests for src/agents/failover_handler.py.

Round 2m: the old tests could not run - they called _select_alternate_models() without its 'agent' argument, used
fixtures they never created (model1_free, local_model_r2, ...), referenced AgentManager without importing it, and their
async test methods were never awaited by unittest. These tests use the current signatures and run the coroutines.

Rules under test:
* _select_alternate_models: skips the original model, tried models, blacklisted models, RAW-template models and (for PM
  and worker agents) vision/OCR/embedding models; in the FREE tier only ':free' OpenRouter models; sorts by size
  (closest to the preferred model's size when known, else largest first), then performance, then id.
* handle_agent_model_failover (local part): pass 1 tries the SAME model on another healthy local API; pass 2 tries the
  other models of every healthy local API, largest first; a provider-level error skips the failed API; errors that are
  only retryable (connection) do not start a failover; when everything fails the agent is set to ERROR.
"""
import asyncio
import logging
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from src.agents import failover_handler
from src.agents.constants import AGENT_STATUS_ERROR
from src.agents.failover_handler import _select_alternate_models, handle_agent_model_failover

B = 1_000_000_000


def _model(model_id, size=None, **extra):
    data = {"id": model_id, **extra}
    if size is not None:
        data["num_parameters"] = size
    return data


class TestSelectAlternateModels(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        failover_handler._models_without_tool_support.clear()
        self.addCleanup(failover_handler._models_without_tool_support.clear)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.original = "openrouter/original:free"
        self.models = [
            _model("openrouter/model1:free", 7 * B),
            _model("openrouter/model2", 3 * B),
            _model("openrouter/model3:free", 1 * B),
            _model("openrouter/model4:free", 3 * B),
            _model(self.original, 3 * B),
        ]
        self.scores = {"openrouter/model1:free": 0.8, "openrouter/model2": 0.9, "openrouter/model3:free": 0.85,
                       "openrouter/model4:free": 0.8}
        tracker = MagicMock()
        tracker.get_metrics.side_effect = lambda base, model_id: (
            {"score": self.scores[model_id]} if model_id in self.scores else None)
        self.manager = types.SimpleNamespace(performance_tracker=tracker)
        self.agent = types.SimpleNamespace(agent_type="worker", agent_config={"config": {}})
        self.registry = patch.object(failover_handler, "model_registry").start()
        self.settings = patch.object(failover_handler, "settings").start()
        self.addCleanup(patch.stopall)
        self.registry.get_available_models_dict.return_value = {"openrouter": self.models}
        self.registry.get_model_info.return_value = None
        self.settings.MODEL_TIER = "ALL"

    def select(self, tried=(), max_alternates=5, agent=None):
        return asyncio.run(_select_alternate_models(self.manager, agent or self.agent, "openrouter", self.original,
                                                    set(tried), max_alternates=max_alternates))

    def test_largest_first_then_performance_then_id(self):
        self.assertEqual(self.select(), ["openrouter/model1:free", "openrouter/model2", "openrouter/model4:free",
                                         "openrouter/model3:free"])
        self.assertEqual(self.select(max_alternates=1), ["openrouter/model1:free"])

    def test_closest_to_the_preferred_model_size_first(self):
        self.registry.get_model_info.side_effect = lambda model_id: {"num_parameters": 3 * B} if model_id == self.original else None
        self.assertEqual(self.select(), ["openrouter/model2", "openrouter/model4:free", "openrouter/model3:free",
                                         "openrouter/model1:free"])

    def test_free_tier_keeps_only_free_openrouter_models(self):
        self.settings.MODEL_TIER = "FREE"
        self.assertEqual(self.select(), ["openrouter/model1:free", "openrouter/model4:free", "openrouter/model3:free"])

    def test_original_tried_and_blacklisted_models_are_skipped(self):
        failover_handler.add_model_to_blacklist("openrouter", "openrouter/model4:free")
        self.assertEqual(self.select(tried={"openrouter/model1:free"}), ["openrouter/model2", "openrouter/model3:free"])

    def test_raw_template_models_are_skipped_and_blacklisted(self):
        self.registry.get_model_info.side_effect = (
            lambda model_id: {"model_template": "{{ .Prompt }}"} if model_id == "openrouter/model1:free" else None)
        self.assertNotIn("openrouter/model1:free", self.select())
        self.assertTrue(failover_handler.is_model_blacklisted("openrouter", "openrouter/model1:free"))

    def test_vision_models_are_skipped_for_workers_but_not_for_admin(self):
        self.models.append(_model("openrouter/llava-vision:free", 13 * B))
        self.assertNotIn("openrouter/llava-vision:free", self.select())
        admin = types.SimpleNamespace(agent_type="admin", agent_config={"config": {}})
        self.assertEqual(self.select(agent=admin)[0], "openrouter/llava-vision:free")

    def test_no_candidates(self):
        tried = {"openrouter/model1:free", "openrouter/model2", "openrouter/model3:free", "openrouter/model4:free"}
        self.assertEqual(self.select(tried=tried), [])


class TestHandleAgentModelFailoverLocal(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        failover_handler._models_without_tool_support.clear()
        self.addCleanup(failover_handler._models_without_tool_support.clear)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.agent = types.SimpleNamespace(
            agent_id="W1", agent_type="worker", provider_name="ollama-local-a", model="qwen3.5:9b",
            agent_config={"config": {}}, _failover_state={}, set_status=MagicMock())
        tracker = MagicMock()
        tracker.get_metrics.return_value = None
        self.manager = types.SimpleNamespace(agents={"W1": self.agent}, send_to_ui=AsyncMock(),
                                             performance_tracker=tracker)
        self.registry = patch.object(failover_handler, "model_registry").start()
        self.addCleanup(patch.stopall)
        self.registry.get_available_models_dict.return_value = {
            "ollama-local-a": [_model("qwen3.5:9b", 9 * B), _model("qwen3.5:4b", 4 * B)],
            "ollama-local-b": [_model("qwen3.5:9b", 9 * B), _model("gemma4:12b", 12 * B)],
        }
        self.registry.get_reachable_provider_url.side_effect = lambda provider: f"http://{provider}:11434"
        self.registry.get_model_info.return_value = None
        self.health = patch.object(failover_handler, "_check_provider_health", new_callable=AsyncMock).start()
        self.health.return_value = True
        self.switch = patch.object(failover_handler, "_try_switch_agent", new_callable=AsyncMock).start()

    def run_failover(self, error):
        return asyncio.run(handle_agent_model_failover(self.manager, "W1", error))

    def tried(self):
        return [(call.args[2], call.args[3]) for call in self.switch.call_args_list]

    def test_the_same_model_on_another_api_comes_first(self):
        self.switch.return_value = True
        self.assertTrue(self.run_failover(ValueError("model crashed")))
        self.assertEqual(self.tried(), [("ollama-local-b", "qwen3.5:9b")])

    def test_other_models_largest_first_then_error_state(self):
        self.switch.return_value = False
        self.assertFalse(self.run_failover(ValueError("model crashed")))
        self.assertEqual(self.tried(), [("ollama-local-b", "qwen3.5:9b"),     # pass 1
                                        ("ollama-local-a", "qwen3.5:4b"),     # pass 2, the failed model is skipped
                                        ("ollama-local-b", "gemma4:12b")])
        self.agent.set_status.assert_called_with(AGENT_STATUS_ERROR)
        self.assertFalse(hasattr(self.agent, "_failover_state"))

    def test_a_provider_level_error_skips_the_failed_api(self):
        self.switch.return_value = False
        self.run_failover(asyncio.TimeoutError())
        self.assertEqual(self.tried(), [("ollama-local-b", "qwen3.5:9b"), ("ollama-local-b", "gemma4:12b")])

    def test_an_unhealthy_api_is_skipped(self):
        self.switch.return_value = False
        self.health.side_effect = lambda url: "ollama-local-b" not in url
        self.run_failover(ValueError("model crashed"))
        self.assertEqual(self.tried(), [("ollama-local-a", "qwen3.5:4b")])

    def test_the_localhost_ollama_is_a_local_api(self):
        # Round 2m: 'ollama-local' (no suffix) was not seen as local, so it was never tried and a failure on it went the
        # remote way
        models = self.registry.get_available_models_dict.return_value
        models["ollama-local"] = models.pop("ollama-local-b")
        self.switch.return_value = True
        self.assertTrue(self.run_failover(ValueError("model crashed")))
        self.assertEqual(self.tried(), [("ollama-local", "qwen3.5:9b")])
        self.agent.provider_name = "ollama-local"
        self.agent._failover_state = {}  # a new failure on the new API
        self.switch.reset_mock()
        self.switch.return_value = False
        self.run_failover(ValueError("model crashed"))
        self.assertEqual(self.tried()[0], ("ollama-local-a", "qwen3.5:9b"))
        self.assertNotIn(("ollama-local", "qwen3.5:9b"), self.tried())

    def test_a_retryable_connection_error_does_not_start_a_failover(self):
        error = aiohttp.ClientPayloadError("stream cut")
        self.assertFalse(self.run_failover(error))
        self.switch.assert_not_called()
        self.agent.set_status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
