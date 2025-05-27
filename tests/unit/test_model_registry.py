import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import aiohttp # For mocking ClientSession

# Modules to be tested or used for mocking
from src.config.model_registry import ModelRegistry, ModelInfo
from src.config.settings import Settings

# Disable most logging output for tests unless specifically testing logging
import logging
logging.disable(logging.CRITICAL)


class TestModelRegistryParameterDiscovery(unittest.TestCase):

    def setUp(self):
        # Mock Settings object
        self.mock_settings = MagicMock(spec=Settings)
        self.mock_settings.MODEL_TIER = "ALL" # Allow all models for discovery tests
        self.mock_settings.LOCAL_API_SCAN_ENABLED = True # Assume enabled for Ollama tests
        self.mock_settings.LOCAL_API_SCAN_PORTS = [11434]
        self.mock_settings.LOCAL_API_SCAN_TIMEOUT = 0.1
        # Mock API keys if needed by discovery methods (e.g., OpenRouter requires one)
        self.mock_settings.PROVIDER_API_KEYS = {"openrouter": ["fake_or_key"]}
        self.mock_settings.OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1" # Ensure it's set
        self.mock_settings.is_provider_configured = MagicMock(return_value=True) # Assume providers are configured

        # Instantiate ModelRegistry with mocked settings
        self.model_registry = ModelRegistry(settings_obj=self.mock_settings)

    # --- Tests for _parse_ollama_parameter_string_to_int ---
    def test_parse_ollama_param_string_valid(self):
        parse_func = self.model_registry._parse_ollama_parameter_string_to_int
        self.assertEqual(parse_func("7B"), 7_000_000_000)
        self.assertEqual(parse_func("13.5B"), 13_500_000_000)
        self.assertEqual(parse_func("13.5b"), 13_500_000_000)
        self.assertEqual(parse_func("70G"), 70_000_000_000) # G for Gemma
        self.assertEqual(parse_func("180K"), 180_000)
        self.assertEqual(parse_func("3M"), 3_000_000)
        self.assertEqual(parse_func("0.5B"), 500_000_000)
        self.assertEqual(parse_func("  7B  "), 7_000_000_000) # With spaces

    def test_parse_ollama_param_string_invalid(self):
        parse_func = self.model_registry._parse_ollama_parameter_string_to_int
        self.assertIsNone(parse_func("unknown"))
        self.assertIsNone(parse_func("7X"))
        self.assertIsNone(parse_func("B7"))
        self.assertIsNone(parse_func(""))
        self.assertIsNone(parse_func(None)) # type: ignore
        self.assertIsNone(parse_func("1.2.3B"))


    @patch('aiohttp.ClientSession')
    async def test_discover_openrouter_models_with_parameters(self, mock_client_session_cls):
        # --- Mock OpenRouter API response ---
        mock_response_openrouter = AsyncMock()
        mock_response_openrouter.status = 200
        mock_response_openrouter.json = AsyncMock(return_value={
            "data": [
                {"id": "openrouter/model1", "name": "Model One", "architecture": {"n_parameters": 7000000000}},
                {"id": "openrouter/model2", "name": "Model Two", "architecture": {"n_parameters": "13B"}}, # Test string parsing if OR did this
                {"id": "openrouter/model3", "name": "Model Three", "architecture": {}}, # Missing n_parameters
                {"id": "openrouter/model4", "name": "Model Four"}, # Missing architecture
                {"id": "openrouter/model5", "name": "Model Five", "architecture": {"n_parameters": None}}, # None value
            ]
        })
        # Configure the session context manager
        mock_session_instance = AsyncMock()
        mock_session_instance.__aenter__.return_value.get.return_value = mock_response_openrouter
        mock_client_session_cls.return_value = mock_session_instance
        
        # --- Run discovery ---
        # Temporarily make openrouter reachable for this test
        self.model_registry._reachable_providers["openrouter"] = self.mock_settings.OPENROUTER_BASE_URL
        await self.model_registry._discover_openrouter_models()
        await self.model_registry._apply_filters() # Apply filters to populate available_models

        # --- Assertions ---
        openrouter_models = self.model_registry.available_models.get("openrouter", [])
        self.assertTrue(len(openrouter_models) > 0, "No OpenRouter models found after discovery")

        model_params = {m["id"]: m.get("num_parameters") for m in openrouter_models}
        
        self.assertEqual(model_params.get("openrouter/model1"), 7000000000)
        # The OpenRouter discovery currently expects int/float, not string "13B" for n_parameters.
        # If OpenRouter *could* send "13B", the parsing logic would need to be in _discover_openrouter_models.
        # For now, assuming it sends numeric or is None.
        self.assertIsNone(model_params.get("openrouter/model2"), "String '13B' should not be parsed by current OR logic, expect None")
        self.assertIsNone(model_params.get("openrouter/model3"))
        self.assertIsNone(model_params.get("openrouter/model4"))
        self.assertIsNone(model_params.get("openrouter/model5"))


    @patch('aiohttp.ClientSession')
    async def test_verify_and_fetch_ollama_models_with_parameters(self, mock_client_session_cls):
        # --- Mock Ollama API responses ---
        ollama_base_url = "http://localhost:11434"
        
        # Mock for /api/tags
        mock_response_tags = AsyncMock()
        mock_response_tags.status = 200
        mock_response_tags.json = AsyncMock(return_value={
            "models": [
                {"name": "ollama/llama3:8b", "digest": "abc", "size": 5000000000},
                {"name": "ollama/codegemma:7b", "digest": "def", "size": 4000000000},
                {"name": "ollama/phi3:3.8b", "digest": "ghi", "size": 2000000000}, # Will have specific param size from /show
                {"name": "ollama/no_details_model:latest", "digest": "jkl", "size": 1000000000}, # Will mock /show to fail or miss field
            ]
        })

        # Mocks for /api/show (one for each model)
        mock_response_show_llama3 = AsyncMock(); mock_response_show_llama3.status = 200
        mock_response_show_llama3.json = AsyncMock(return_value={"details": {"parameter_size": "8B"}})
        
        mock_response_show_codegemma = AsyncMock(); mock_response_show_codegemma.status = 200
        mock_response_show_codegemma.json = AsyncMock(return_value={"details": {"parameter_size": "7B"}})

        mock_response_show_phi3 = AsyncMock(); mock_response_show_phi3.status = 200
        mock_response_show_phi3.json = AsyncMock(return_value={"details": {"parameter_size": "3.8B"}}) # Test float parsing

        mock_response_show_no_details = AsyncMock(); mock_response_show_no_details.status = 200
        mock_response_show_no_details.json = AsyncMock(return_value={"details": {}}) # Missing parameter_size

        # Configure the session context manager to return different /api/show responses
        mock_session_instance = AsyncMock()
        
        # This mapping will determine which response is returned based on the URL or POST data
        def get_side_effect(url, **kwargs):
            if "/api/tags" in url: return mock_response_tags
            # For /api/show, it's a POST, so check kwargs['json']['name']
            if "/api/show" in url and kwargs.get('json'):
                model_name_requested = kwargs['json']['name']
                if model_name_requested == "ollama/llama3:8b": return mock_response_show_llama3
                if model_name_requested == "ollama/codegemma:7b": return mock_response_show_codegemma
                if model_name_requested == "ollama/phi3:3.8b": return mock_response_show_phi3
                if model_name_requested == "ollama/no_details_model:latest": return mock_response_show_no_details
            return AsyncMock(status=404) # Default fallback

        mock_session_instance.__aenter__.return_value.get = AsyncMock(side_effect=get_side_effect)
        mock_session_instance.__aenter__.return_value.post = AsyncMock(side_effect=get_side_effect) # /api/show is POST
        mock_client_session_cls.return_value = mock_session_instance

        # --- Run discovery (which includes _verify_and_fetch_models) ---
        # Need to mock scan_for_local_apis to return our test URL
        with patch('src.config.model_registry.scan_for_local_apis', AsyncMock(return_value=[ollama_base_url])):
            await self.model_registry.discover_models_and_providers()
            # _apply_filters is called internally by discover_models_and_providers

        # --- Assertions ---
        # Provider name will be generated, e.g., "ollama-local-localhost" or "ollama-local-127-0-0-1"
        # Find the discovered ollama provider
        discovered_ollama_provider_name = None
        for prov_name in self.model_registry.available_models.keys():
            if prov_name.startswith("ollama-local-"):
                discovered_ollama_provider_name = prov_name
                break
        
        self.assertIsNotNone(discovered_ollama_provider_name, "Ollama provider not found in available models.")
        
        ollama_models = self.model_registry.available_models.get(discovered_ollama_provider_name, [])
        self.assertTrue(len(ollama_models) == 4, f"Expected 4 Ollama models, found {len(ollama_models)}")

        model_params = {m["id"]: m.get("num_parameters") for m in ollama_models}

        self.assertEqual(model_params.get("ollama/llama3:8b"), 8_000_000_000)
        self.assertEqual(model_params.get("ollama/codegemma:7b"), 7_000_000_000)
        self.assertEqual(model_params.get("ollama/phi3:3.8b"), 3_800_000_000)
        self.assertIsNone(model_params.get("ollama/no_details_model:latest"))


if __name__ == '__main__':
    # Need to run asyncio tests with an async test runner or asyncio.run
    # For simplicity if running directly:
    # loop = asyncio.get_event_loop()
    # loop.run_until_complete(unittest.main(testRunner=unittest.TextTestRunner()))
    # However, unittest.main() is usually sufficient if tests are defined with async def and run with asyncio.run in a wrapper
    unittest.main()
