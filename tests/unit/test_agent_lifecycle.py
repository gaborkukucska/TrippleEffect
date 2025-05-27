import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

# Import the function to be tested and its dependencies
from src.agents.agent_lifecycle import _select_best_available_model
from src.agents.manager import AgentManager # For spec
from src.config.model_registry import ModelRegistry, ModelInfo # For spec and ModelInfo
from src.agents.performance_tracker import PerformanceTracker # For spec
from src.config.settings import Settings # For spec
from src.agents.key_manager import ProviderKeyManager # For spec

# Disable most logging output for tests unless specifically testing logging
import logging
logging.disable(logging.CRITICAL)

class TestSelectBestAvailableModel(unittest.TestCase):

    def setUp(self):
        self.manager_mock = MagicMock(spec=AgentManager)
        
        self.mock_model_registry = MagicMock(spec=ModelRegistry)
        self.manager_mock.model_registry = self.mock_model_registry
        
        self.mock_performance_tracker = MagicMock(spec=PerformanceTracker)
        self.manager_mock.performance_tracker = self.mock_performance_tracker
        
        self.mock_settings = MagicMock(spec=Settings)
        self.manager_mock.settings = self.mock_settings
        
        self.mock_key_manager = AsyncMock(spec=ProviderKeyManager)
        self.manager_mock.key_manager = self.mock_key_manager

        # Default mock behaviors
        self.mock_key_manager.is_provider_depleted = AsyncMock(return_value=False)
        self.mock_settings.is_provider_configured = MagicMock(return_value=True) # Assume configured unless specified

        # Sample model data (ModelInfo structure)
        # Provider names here are specific instance names
        self.model_L_remote_highP = ModelInfo(id="model_L_remote_highP_id", provider="openrouter", num_parameters=100, name="Large Remote HighP")
        self.model_L_remote_lowP = ModelInfo(id="model_L_remote_lowP_id", provider="openrouter", num_parameters=100, name="Large Remote LowP")
        self.model_M_remote_highP_free = ModelInfo(id="model_M_remote_highP:free", provider="openrouter", num_parameters=50, name="Medium Remote HighP Free")
        self.model_M_remote_lowP = ModelInfo(id="model_M_remote_lowP_id", provider="openrouter", num_parameters=50, name="Medium Remote LowP")
        self.model_S_remote_highP = ModelInfo(id="model_S_remote_highP_id", provider="openrouter", num_parameters=10, name="Small Remote HighP")
        
        self.model_L_local_highP = ModelInfo(id="model_L_local_highP_id", provider="ollama-local-test", num_parameters=110, name="Large Local HighP")
        self.model_M_local_lowP = ModelInfo(id="model_M_local_lowP_id", provider="ollama-local-test", num_parameters=55, name="Medium Local LowP")

        # Performance scores setup: {specific_provider_name: {model_id_suffix: {"score": ...}}}
        self.perf_metrics = {
            "openrouter": {
                "model_L_remote_highP_id": {"score": 0.9},
                "model_L_remote_lowP_id": {"score": 0.7},
                "model_M_remote_highP:free": {"score": 0.95},
                "model_M_remote_lowP_id": {"score": 0.6},
                "model_S_remote_highP_id": {"score": 0.8},
            },
            "ollama-local-test": { # Specific local provider instance name
                "model_L_local_highP_id": {"score": 0.85},
                "model_M_local_lowP_id": {"score": 0.75},
            }
        }
        # The _select_best_available_model transforms PerformanceTracker's internal format.
        # We mock get_metrics on performance_tracker.
        def get_metrics_side_effect(provider_base, model_id_suffix):
            # provider_base here is "ollama" or "openrouter"
            # model_id_suffix is the plain model ID
            # This needs to map to self.perf_metrics which is keyed by specific provider name
            for specific_provider_name, models in self.perf_metrics.items():
                if specific_provider_name.startswith(provider_base): # Simple match for test
                    if model_id_suffix in models:
                        return models[model_id_suffix]
            return {"score": 0.0, "latency": float('inf'), "calls": 0} # Default if not found

        self.mock_performance_tracker.get_metrics = MagicMock(side_effect=get_metrics_side_effect)


    async def test_scenario_1_tier_all(self):
        self.mock_settings.MODEL_TIER = "ALL"
        self.mock_model_registry.get_available_models_dict.return_value = {
            "openrouter": [self.model_L_remote_highP, self.model_M_remote_highP_free, self.model_S_remote_highP],
            "ollama-local-test": [self.model_L_local_highP, self.model_M_local_lowP]
        }
        # Expected order:
        # 1. ollama-local-test/model_L_local_highP_id (110B, 0.85)
        # 2. openrouter/model_L_remote_highP_id (100B, 0.9)
        # 3. ollama-local-test/model_M_local_lowP_id (55B, 0.75)
        # 4. openrouter/model_M_remote_highP:free (50B, 0.95)
        # 5. openrouter/model_S_remote_highP_id (10B, 0.8)
        
        provider, model_id = await _select_best_available_model(self.manager_mock)
        self.assertEqual(provider, "ollama-local-test")
        self.assertEqual(model_id, "model_L_local_highP_id")

    async def test_scenario_2_tier_free(self):
        self.mock_settings.MODEL_TIER = "FREE"
        self.mock_model_registry.get_available_models_dict.return_value = {
            "openrouter": [self.model_L_remote_highP, self.model_M_remote_highP_free, self.model_S_remote_highP],
            "ollama-local-test": [self.model_L_local_highP, self.model_M_local_lowP]
        }
        # Filtered for FREE (local always allowed, remote only if :free):
        # - openrouter/model_L_remote_highP (remote, not free) -> NO
        # - openrouter/model_M_remote_highP:free (remote, free) -> YES
        # - openrouter/model_S_remote_highP (remote, not free) -> NO
        # - ollama-local-test/model_L_local_highP (local) -> YES
        # - ollama-local-test/model_M_local_lowP (local) -> YES
        # Candidates:
        # 1. ollama-local-test/model_L_local_highP_id (110B, 0.85)
        # 2. ollama-local-test/model_M_local_lowP_id (55B, 0.75)
        # 3. openrouter/model_M_remote_highP:free (50B, 0.95)
        
        provider, model_id = await _select_best_available_model(self.manager_mock)
        self.assertEqual(provider, "ollama-local-test")
        self.assertEqual(model_id, "model_L_local_highP_id")

    async def test_scenario_3_tier_local(self):
        self.mock_settings.MODEL_TIER = "LOCAL"
        self.mock_model_registry.get_available_models_dict.return_value = {
            "openrouter": [self.model_L_remote_highP, self.model_M_remote_highP_free],
            "ollama-local-test": [self.model_L_local_highP, self.model_M_local_lowP]
        }
        # Filtered for LOCAL:
        # - ollama-local-test/model_L_local_highP_id (110B, 0.85)
        # - ollama-local-test/model_M_local_lowP_id (55B, 0.75)
        
        provider, model_id = await _select_best_available_model(self.manager_mock)
        self.assertEqual(provider, "ollama-local-test")
        self.assertEqual(model_id, "model_L_local_highP_id")

    async def test_scenario_4_provider_depleted(self):
        self.mock_settings.MODEL_TIER = "ALL"
        self.mock_model_registry.get_available_models_dict.return_value = {
            "openrouter": [self.model_L_remote_highP, self.model_M_remote_highP_free], # L_remote_highP would be first if not depleted
            "ollama-local-test": [self.model_M_local_lowP] # M_local_lowP is smaller than L_remote_highP
        }
        
        # Mock openrouter as depleted
        self.mock_key_manager.is_provider_depleted = AsyncMock(side_effect=lambda p_name: p_name == "openrouter")
        
        # Expected order without depletion: L_remote_highP (100B), M_local_lowP (55B), M_remote_highP:free (50B)
        # With openrouter depleted, L_remote_highP and M_remote_highP:free are skipped.
        # So, M_local_lowP should be chosen.
        
        provider, model_id = await _select_best_available_model(self.manager_mock)
        self.assertEqual(provider, "ollama-local-test")
        self.assertEqual(model_id, "model_M_local_lowP_id")

    async def test_scenario_5_no_models_available(self):
        self.mock_settings.MODEL_TIER = "ALL"
        self.mock_model_registry.get_available_models_dict.return_value = {} # Empty registry
        
        provider, model_id = await _select_best_available_model(self.manager_mock)
        self.assertIsNone(provider)
        self.assertIsNone(model_id)

    async def test_scenario_6_models_without_size_or_performance(self):
        self.mock_settings.MODEL_TIER = "ALL"
        
        model_no_size_no_perf = ModelInfo(id="no_size_no_perf", provider="openrouter", name="NoSizeNoPerf")
        model_no_size_has_perf = ModelInfo(id="no_size_has_perf", provider="openrouter", name="NoSizeHasPerf")
        model_has_size_no_perf = ModelInfo(id="has_size_no_perf", provider="openrouter", num_parameters=10, name="HasSizeNoPerf")
        
        self.mock_model_registry.get_available_models_dict.return_value = {
            "openrouter": [model_no_size_no_perf, model_no_size_has_perf, model_has_size_no_perf]
        }
        
        # Performance for one of them
        def get_metrics_side_effect(provider_base, model_id_suffix):
            if model_id_suffix == "no_size_has_perf": return {"score": 0.9}
            return {"score": 0.0} # Default for others
        self.mock_performance_tracker.get_metrics = MagicMock(side_effect=get_metrics_side_effect)

        # Expected order:
        # 1. has_size_no_perf (10B, score 0.0) - size is primary
        # 2. no_size_has_perf (0B default, score 0.9) - then performance
        # 3. no_size_no_perf (0B default, score 0.0) - then ID (no_size_no_perf < no_size_has_perf if scores were same)
        # Actually, sort_models_by_size_performance_id sorts by ID if size and score are equal.
        # has_size_no_perf: num_params=10, score=0.0
        # no_size_has_perf: num_params=0 (default), score=0.9
        # no_size_no_perf: num_params=0 (default), score=0.0
        # Order: has_size_no_perf, no_size_has_perf, no_size_no_perf
        
        provider, model_id = await _select_best_available_model(self.manager_mock)
        self.assertEqual(provider, "openrouter")
        self.assertEqual(model_id, "has_size_no_perf")

if __name__ == '__main__':
    unittest.main()
