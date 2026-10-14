import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

from src.agents.failover_handler import _select_alternate_models, handle_agent_model_failover
# Make sure Settings can be imported and potentially mocked if its attributes are accessed globally
from src.config.settings import Settings 
from src.agents.core import Agent


class TestSelectAlternateModels(unittest.TestCase):

    def setUp(self):
        self.manager_mock = MagicMock()
        self.manager_mock.performance_tracker = AsyncMock()
        # Mock settings object directly on the manager, as _select_alternate_models accesses it via manager.settings
        # However, the code also imports settings directly: from src.config.settings import settings
        # So, we need to patch 'src.agents.failover_handler.settings' for module-level access.
        
        # Sample models - now including num_parameters
        self.model1_free_large = {"id": "openrouter/model1:free", "name": "Model 1 Free Large", "num_parameters": 7_000_000_000}
        self.model2_paid_medium = {"id": "openrouter/model2", "name": "Model 2 Paid Medium", "num_parameters": 3_000_000_000}
        self.model3_free_small = {"id": "openrouter/model3:free", "name": "Model 3 Free Small", "num_parameters": 1_000_000_000}
        self.model4_free_medium = {"id": "openrouter/model4:free", "name": "Model 4 Free Medium", "num_parameters": 3_000_000_000}
        self.original_model_id = "openrouter/original:free" # Assume this one is also medium for some tests
        self.original_model_info = {"id": self.original_model_id, "name": "Original Model", "num_parameters": 3_000_000_000}


    @patch('src.agents.failover_handler.model_registry')
    @patch('src.agents.failover_handler.settings')
    def test_select_alternates_prioritizes_size_then_performance(self, mock_settings_module, mock_model_registry_module):
        mock_settings_module.MODEL_TIER = "ALL" # Allow all models for this test
        
        # Models available from registry for the provider "openrouter"
        # model_data_copy["provider"] = provider is added inside _select_alternate_models
        mock_model_registry_module.get_available_models_dict.return_value = {
            "openrouter": [
                self.model1_free_large, # 7B
                self.model2_paid_medium, # 3B
                self.model3_free_small,  # 1B
                self.model4_free_medium, # 3B (same size as model2)
                self.original_model_info 
            ]
        }

        # Mock performance metrics (fetched via manager.performance_tracker.get_metrics)
        # The sorter expects: {provider_name: {model_id: {"score": ...}}}
        # _select_alternate_models prepares this structure.
        # We need to mock get_metrics to return scores for individual models.
        def get_metrics_side_effect(provider_base, model_id_suffix):
            if provider_base == "openrouter":
                if model_id_suffix == self.model1_free_large["id"]: return {"score": 0.8}
                if model_id_suffix == self.model2_paid_medium["id"]: return {"score": 0.9} # Higher score
                if model_id_suffix == self.model3_free_small["id"]: return {"score": 0.85}
                if model_id_suffix == self.model4_free_medium["id"]: return {"score": 0.8} # Lower score than model2
            return {"score": 0.0, "latency": float('inf'), "calls": 0} # Default for others
        self.manager_mock.performance_tracker.get_metrics = MagicMock(side_effect=get_metrics_side_effect)
        
        # Scenario: model1 (7B, 0.8), model2 (3B, 0.9), model4 (3B, 0.8), model3 (1B, 0.85)
        # Expected order: model1, model2, model4, model3
        # (Size desc -> Perf desc -> ID asc)

        tried_models_on_key = set()
        
        selected_alternates = asyncio.run(
            _select_alternate_models(
                self.manager_mock, # manager_mock has its own settings attribute if needed by other parts
                provider="openrouter",
                original_model=self.original_model_id,
                tried_models_on_key=tried_models_on_key,
                max_alternates=1
            )
        )
        
        self.assertEqual(len(selected_alternates), 1)
        self.assertIn(self.model1_free["id"], selected_alternates)
        self.assertNotIn(self.model2_paid["id"], selected_alternates)
        self.assertNotIn(self.model3_free["id"], selected_alternates)

    @patch('src.agents.failover_handler.model_registry')
    @patch('src.agents.failover_handler.settings')
    def test_fallback_to_random_selection(self, mock_settings_module, mock_model_registry_module):
        mock_settings_module.MODEL_TIER = "FREE"
        self.manager_mock.performance_tracker.get_ranked_models = AsyncMock(return_value=[])
        
        mock_model_registry_module.get_available_models_dict.return_value = {
            "openrouter": [
                self.model1_free, 
                self.model2_paid, 
                self.model3_free, 
                {"id": self.original_model_id}
            ]
        }
        
        tried_models_on_key = set()
        
        with patch('src.agents.failover_handler.random.shuffle', side_effect=lambda x: x):
            selected_alternates = asyncio.run(
                _select_alternate_models(
                    self.manager_mock,
                    provider="openrouter",
                    original_model=self.original_model_id,
                    tried_models_on_key=tried_models_on_key,
                    max_alternates=2 
                )
            )
        
        self.assertEqual(len(selected_alternates), 2)
        self.assertIn(self.model1_free["id"], selected_alternates)
        self.assertIn(self.model3_free["id"], selected_alternates)
        self.assertNotIn(self.model2_paid["id"], selected_alternates)
        self.assertNotIn(self.original_model_id, selected_alternates)

    @patch('src.agents.failover_handler.model_registry')
    @patch('src.agents.failover_handler.settings')
    def test_no_suitable_models(self, mock_settings_module, mock_model_registry_module):
        mock_settings_module.MODEL_TIER = "PAID_ONLY_TIER"
        self.manager_mock.performance_tracker.get_ranked_models = AsyncMock(return_value=[self.model1_free["id"]])
        
        mock_model_registry_module.get_available_models_dict.return_value = {
            "openrouter": [self.model1_free, {"id": self.original_model_id}]
        }
        
        tried_models_on_key = {self.model1_free["id"]}
        
        selected_alternates = asyncio.run(
            _select_alternate_models(
                self.manager_mock,
                provider="openrouter",
                original_model=self.original_model_id,
                tried_models_on_key=tried_models_on_key,
                max_alternates=3
            )
        )
        self.assertEqual(len(selected_alternates), 0)

    @patch('src.agents.failover_handler.model_registry')
    @patch('src.agents.failover_handler.settings')
    def test_performance_selection_error_fallback(self, mock_settings_module, mock_model_registry_module):
        mock_settings_module.MODEL_TIER = "ALL"
        self.manager_mock.performance_tracker.get_ranked_models = AsyncMock(side_effect=Exception("Performance Tracker Error"))
        
        mock_model_registry_module.get_available_models_dict.return_value = {
            "openrouter": [self.model1_free, self.model2_paid, {"id": self.original_model_id}]
        }
        
        tried_models_on_key = set()
        with patch('src.agents.failover_handler.random.shuffle', side_effect=lambda x: x):
            selected_alternates = asyncio.run(
                _select_alternate_models(
                    self.manager_mock,
                    provider="openrouter",
                    original_model=self.original_model_id,
                    tried_models_on_key=tried_models_on_key,
                    max_alternates=1
                )
            )
        
        self.assertEqual(len(selected_alternates), 1)
        self.assertTrue(selected_alternates[0] == self.model1_free["id"] or selected_alternates[0] == self.model2_paid["id"])


class TestHandleAgentModelFailoverInitialModelSelection(unittest.TestCase):
    def setUp(self):
        self.manager_mock = MagicMock(spec=AgentManager) # Use spec for AgentManager
        self.manager_mock.performance_tracker = AsyncMock()
        
        # Mock settings object that will be accessed by the SUT (handle_agent_model_failover)
        # This settings object is imported directly in failover_handler.py
        self.settings_patch = patch('src.agents.failover_handler.settings', spec=Settings)
        self.mock_settings_module = self.settings_patch.start() # Start patch and get the mock

        self.manager_mock.model_registry = MagicMock()
        self.manager_mock.key_manager = AsyncMock()
        self.manager_mock.send_to_ui = AsyncMock()
        self.manager_mock.state_manager = MagicMock()
        self.manager_mock.agents = {} # To store agent_mock for lookup by handle_agent_model_failover

        self.agent_mock = MagicMock(spec=Agent)
        self.agent_mock.agent_id = "test_agent_001"
        self.agent_mock.provider_name = "openrouter" 
        self.agent_mock.model = "openrouter/original_model:free"
        self.agent_mock.llm_provider = MagicMock()
        self.agent_mock._failover_state = {} 
        self.agent_mock.agent_config = {"config": {}} 
        self.manager_mock.agents[self.agent_mock.agent_id] = self.agent_mock # Add agent to manager's dict

        # Sample models - now including num_parameters
        self.model_r1_free_large = {"id": "openrouter/ranked1:free", "num_parameters": 7_000_000_000}
        self.model_r2_paid_medium = {"id": "openrouter/ranked2", "num_parameters": 3_000_000_000}
        self.model_r3_free_small = {"id": "openrouter/ranked3:free", "num_parameters": 1_000_000_000}
        self.model_r4_free_medium = {"id": "openrouter/ranked4:free", "num_parameters": 3_000_000_000} # Same size as r2, but free

        self.model_reg1_free = {"id": "openrouter/registry1:free", "num_parameters": 2_000_000_000}
        self.model_reg2_paid = {"id": "openrouter/registry2", "num_parameters": 8_000_000_000} # Large but paid

        self.local_model_l_large = {"id": "ollama-local-test/local_large", "num_parameters": 13_000_000_000}
        self.local_model_m_medium_perf = {"id": "ollama-local-test/local_medium_high_perf", "num_parameters": 7_000_000_000}
        self.local_model_s_small = {"id": "ollama-local-test/local_small", "num_parameters": 3_000_000_000}
        self.local_model_m_medium_alpha = {"id": "ollama-local-test/alpha_local_medium", "num_parameters": 7_000_000_000}


    def tearDown(self):
        self.settings_patch.stop() # Important to stop patches

    @patch('src.agents.failover_handler._try_switch_agent', new_callable=AsyncMock)
    @patch('src.agents.failover_handler._check_provider_health', new_callable=AsyncMock)
    async def test_initial_external_model_selects_largest_free_high_perf(self, mock_check_health, mock_try_switch):
        mock_try_switch.return_value = False 
        mock_check_health.return_value = True

        self.mock_settings_module.MODEL_TIER = "FREE"
        self.manager_mock.key_manager.get_active_key_config.return_value = {"api_key": "test_key_free"}
        
        # model_registry returns all models for the provider "openrouter"
        self.manager_mock.model_registry.get_available_models_dict.return_value = {
            "openrouter": [
                self.model_r1_free_large,    # 7B, free
                self.model_r2_paid_medium,   # 3B, paid
                self.model_r3_free_small,    # 1B, free
                self.model_r4_free_medium    # 3B, free
            ],
            "ollama-local-test": [] 
        }
        
        # Mock performance_tracker.get_metrics (used by comprehensive sort)
        # Base provider name for openrouter is "openrouter"
        def get_metrics_side_effect(provider_base, model_id_suffix):
            if provider_base == "openrouter":
                if model_id_suffix == self.model_r1_free_large["id"]: return {"score": 0.8}
                if model_id_suffix == self.model_r4_free_medium["id"]: return {"score": 0.9} # Higher score
                if model_id_suffix == self.model_r3_free_small["id"]: return {"score": 0.85}
            return {"score": 0.0} # Default for others like paid model
        self.manager_mock.performance_tracker.get_metrics = MagicMock(side_effect=get_metrics_side_effect)

        self.agent_mock.provider_name = "someother_provider/failed_model"
        self.agent_mock.model = "someother_provider/failed_model"
        
        self.agent_mock._failover_state = {} # Reset state for each test

        error_obj = ValueError("Simulated model error")
        await handle_agent_model_failover(self.manager_mock, self.agent_mock.agent_id, error_obj)

        # Comprehensive sort order for FREE tier, excluding paid (model_r2_paid_medium):
        # 1. model_r1_free_large (7B, 0.8)
        # 2. model_r4_free_medium (3B, 0.9)
        # 3. model_r3_free_small (1B, 0.85)
        # Expected initial model: model_r1_free_large

        initial_model_call_args = None
        for call_args_tuple in mock_try_switch.call_args_list:
            call_args = call_args_tuple[0]
            if call_args[2] == "openrouter": # provider_name
                initial_model_call_args = call_args
                break
        
        self.assertIsNotNone(initial_model_call_args, "No call to _try_switch_agent for 'openrouter' found.")
        self.assertEqual(initial_model_call_args[3], self.model_r1_free_large["id"]) # target_model


    @patch('src.agents.failover_handler._try_switch_agent', new_callable=AsyncMock)
    @patch('src.agents.failover_handler._check_provider_health', new_callable=AsyncMock)
    async def test_initial_external_model_fallback_to_original(self, mock_check_health, mock_try_switch):
        mock_try_switch.return_value = False
        mock_check_health.return_value = True

        self.mock_settings_module.MODEL_TIER = "FREE"
        # Original model is free and has some size
        self.agent_mock.model = "openrouter/original_model:free" 
        self.agent_mock.provider_name = "someother_provider/failed_model" # Original provider/model that failed
        self.agent_mock.original_model = self.agent_mock.model # Store for failover_state
        
        self.manager_mock.key_manager.get_active_key_config.return_value = {"api_key": "test_key_fallback"}
        
        # All comprehensively sorted models are unsuitable (e.g., already tried or tier incompatible if not handled by sort)
        # Here, we make the sort return empty to force fallback.
        # This requires mocking sort_models_by_size_performance_id or ensuring candidate_external_model_infos is empty.
        # Let's make candidate_external_model_infos empty by making all models from registry "tried".
        self.manager_mock.model_registry.get_available_models_dict.return_value = {
            "openrouter": [ # These models are available in registry
                {"id": self.agent_mock.model, "num_parameters": 2_000_000_000}, # Original
                self.model_r1_free_large, # Will be marked as tried
                self.model_r4_free_medium # Will be marked as tried
            ],
            "ollama-local-test": []
        }
        # Performance metrics don't matter if sort is forced to be empty or models are all tried
        self.manager_mock.performance_tracker.get_metrics = MagicMock(return_value={"score": 0.5})

        self.agent_mock._failover_state = {} # Reset state
        # Mark all potential candidates from comprehensive sort as already tried for this key
        self.agent_mock._failover_state["tried_models_on_current_external_key"] = {
            self.model_r1_free_large["id"], 
            self.model_r4_free_medium["id"]
        }
        # Note: original_model is NOT in tried_models_on_current_external_key for this fallback to work

        error_obj = ValueError("Simulated model error")
        await handle_agent_model_failover(self.manager_mock, self.agent_mock.agent_id, error_obj)
        
        initial_model_call_args = None
        for call_args_tuple in mock_try_switch.call_args_list:
            call_args = call_args_tuple[0]
            if call_args[2] == "openrouter":
                 initial_model_call_args = call_args
                 break
        self.assertIsNotNone(initial_model_call_args)
        self.assertEqual(initial_model_call_args[3], self.agent_mock.model) # Should select agent's original model


    @patch('src.agents.failover_handler._try_switch_agent', new_callable=AsyncMock)
    @patch('src.agents.failover_handler._check_provider_health', new_callable=AsyncMock)
    async def test_initial_local_model_selects_largest_high_perf(self, mock_check_health, mock_try_switch):
        mock_try_switch.return_value = False
        mock_check_health.return_value = True 

        self.agent_mock.provider_name = "openrouter/failed_model"
        self.agent_mock.model = "openrouter/failed_model"
        
        self.manager_mock.model_registry.get_available_models_dict.return_value = {
            "ollama-local-test": [
                self.local_model_l_large,         # 13B
                self.local_model_m_medium_perf,   # 7B, higher perf
                self.local_model_s_small,         # 3B
                self.local_model_m_medium_alpha   # 7B, lower perf (or same, then alpha ID)
            ],
            "openrouter": [] 
        }
        
        def get_metrics_side_effect(provider_base, model_id_suffix):
            if provider_base == "ollama-local-test":
                if model_id_suffix == self.local_model_l_large["id"]: return {"score": 0.7}
                if model_id_suffix == self.local_model_m_medium_perf["id"]: return {"score": 0.9}
                if model_id_suffix == self.local_model_s_small["id"]: return {"score": 0.8}
                if model_id_suffix == self.local_model_m_medium_alpha["id"]: return {"score": 0.8} # Same score as s_small, diff size
            return {"score": 0.0}
        self.manager_mock.performance_tracker.get_metrics = MagicMock(side_effect=get_metrics_side_effect)
        
        self.agent_mock._failover_state = {} # Reset state

        error_obj = ValueError("Simulated model error")
        await handle_agent_model_failover(self.manager_mock, self.agent_mock.agent_id, error_obj)

        # Expected sort: local_large (13B,0.7), local_medium_high_perf (7B,0.9), local_medium_alpha (7B,0.8), local_small (3B,0.8)
        # So, first choice should be local_large.
        initial_model_call_args = None
        for call_args_tuple in mock_try_switch.call_args_list:
            call_args = call_args_tuple[0]
            if call_args[2] == "ollama-local-test": 
                 initial_model_call_args = call_args
                 break
        self.assertIsNotNone(initial_model_call_args)
        self.assertEqual(initial_model_call_args[3], self.local_model_l_large["id"])


    @patch('src.agents.failover_handler._try_switch_agent', new_callable=AsyncMock)
    @patch('src.agents.failover_handler._check_provider_health', new_callable=AsyncMock)
    async def test_initial_local_model_fallback_if_all_tried_or_unsuitable(self, mock_check_health, mock_try_switch):
        mock_try_switch.return_value = False 
        mock_check_health.return_value = True

        self.agent_mock.provider_name = "openrouter/failed_model"
        self.agent_mock.model = "openrouter/failed_model"
        
        # All models in registry will be marked as "tried"
        self.manager_mock.model_registry.get_available_models_dict.return_value = {
            "ollama-local-test": [self.local_model_l_large, self.local_model_m_medium_perf],
            "openrouter": []
        }
        self.manager_mock.performance_tracker.get_metrics = MagicMock(return_value={"score": 0.5}) # Perf doesn't matter if all tried
        
        self.agent_mock._failover_state = {} # Reset state
        self.agent_mock._failover_state["tried_models_per_local_provider"] = {
            "ollama-local-test": {self.local_model_l_large["id"], self.local_model_m_medium_perf["id"]}
        }
        
        error_obj = ValueError("Simulated model error")
        
        await handle_agent_model_failover(self.manager_mock, self.agent_mock.agent_id, error_obj)
        
        # The logic inside handle_agent_model_failover for local providers:
        # 1. Tries performance-ranked models.
        #    - local_model_r2 is returned by perf tracker.
        #    - It's checked against tried_models_per_local_provider. It's there, so it's skipped.
        # 2. Falls back to alphabetically sorted models from model_registry, excluding already processed/tried.
        #    - Models available for fallback: local_model_zeta_r1, local_model_alpha_reg1.
        #    - Alphabetical sort: local_model_alpha_reg1, then local_model_zeta_r1.
        #    - First attempt will be local_model_alpha_reg1.
        
        first_local_attempt_args = None
        for call_args_tuple in mock_try_switch.call_args_list:
            call_args = call_args_tuple[0] # Positional arguments
            if call_args[2] == "ollama-local-test": # provider_name
                # We are looking for the first model TRIED in the fallback logic
                if call_args[3] != self.local_model_r2["id"]: # Ensure it's not the one from performance that was skipped
                    first_local_attempt_args = call_args
                    break
        
        self.assertIsNotNone(first_local_attempt_args, "No fallback call to _try_switch_agent for 'ollama-local-test' found.")
        self.assertEqual(first_local_attempt_args[3], self.local_model_alpha_reg1["id"])


if __name__ == '__main__':
    unittest.main()
