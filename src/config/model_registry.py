# START OF FILE src/config/model_registry.py
import asyncio
import logging
from typing import Dict, List, Optional, Set
import aiohttp
import json

# Import settings to access provider URLs/keys and MODEL_TIER
from src.config.settings import settings

logger = logging.getLogger(__name__)

# OpenRouter API endpoint for model details
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

class ModelInfo(Dict):
    """Simple dictionary subclass for type hinting model info."""
    pass

class ModelRegistry:
    """
    Handles discovery, filtering, and storage of available LLM models
    from various configured providers (OpenRouter, Ollama, LiteLLM).
    Applies filtering based on the MODEL_TIER environment variable.
    """

    def __init__(self):
        # Stores all models initially discovered, categorized by provider
        self._raw_models: Dict[str, List[ModelInfo]] = {
            "openrouter": [],
            "ollama": [],
            "litellm": [],
            "openai": [], # Placeholder, OpenAI doesn't have a standard public 'list models' endpoint
        }
        # Stores models available *after* filtering (tier, availability)
        self.available_models: Dict[str, List[ModelInfo]] = {
            "openrouter": [],
            "ollama": [],
            "litellm": [],
            "openai": [],
        }
        # Track which providers are configured based on settings
        self._configured_providers: Set[str] = set()
        self._model_tier: str = getattr(settings, 'MODEL_TIER', 'ALL').upper()

        logger.info(f"ModelRegistry initialized. MODEL_TIER='{self._model_tier}'.")

    async def discover_models(self):
        """
        Asynchronously discovers models from all potentially configured providers.
        This should be called once during application startup.
        """
        self._check_configured_providers()
        logger.info(f"Starting model discovery for configured providers: {list(self._configured_providers)}")

        discovery_tasks = []
        if "openrouter" in self._configured_providers:
            discovery_tasks.append(self._discover_openrouter_models())
        if "ollama" in self._configured_providers:
            discovery_tasks.append(self._discover_ollama_models())
        if "litellm" in self._configured_providers:
             discovery_tasks.append(self._discover_litellm_models())
        # Add task for OpenAI if a discovery method is implemented later

        await asyncio.gather(*discovery_tasks, return_exceptions=True)

        self._apply_filters()
        logger.info("Model discovery and filtering complete.")
        self._log_available_models()

    def _check_configured_providers(self):
        """Checks settings to see which providers have necessary config (URL/Key)."""
        self._configured_providers.clear()
        if settings.is_provider_configured("openrouter"): self._configured_providers.add("openrouter")
        if settings.is_provider_configured("ollama"): self._configured_providers.add("ollama")
        if settings.is_provider_configured("litellm"): self._configured_providers.add("litellm")
        if settings.is_provider_configured("openai"): self._configured_providers.add("openai")
        # Note: OpenAI is added but has no discovery method yet.

    async def _discover_openrouter_models(self):
        """Fetches model details from the OpenRouter API."""
        logger.debug("Discovering OpenRouter models...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(OPENROUTER_MODELS_URL, timeout=20) as response:
                    if response.status == 200:
                        data = await response.json()
                        models_data = data.get("data", [])
                        if models_data:
                             self._raw_models["openrouter"] = [ModelInfo(id=m.get("id"), name=m.get("name"), description=m.get("description")) for m in models_data if m.get("id")]
                             logger.info(f"Discovered {len(self._raw_models['openrouter'])} models from OpenRouter.")
                        else:
                             logger.warning("OpenRouter models endpoint returned empty data list.")
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to fetch OpenRouter models. Status: {response.status}, Response: {error_text[:200]}")
        except aiohttp.ClientError as e:
            logger.error(f"Error connecting to OpenRouter API ({OPENROUTER_MODELS_URL}): {e}")
        except asyncio.TimeoutError:
             logger.error(f"Timeout fetching models from OpenRouter API ({OPENROUTER_MODELS_URL}).")
        except Exception as e:
            logger.error(f"Unexpected error fetching OpenRouter models: {e}", exc_info=True)

    async def _discover_ollama_models(self):
        """Fetches model tags from the local Ollama API."""
        logger.debug("Discovering Ollama models...")
        ollama_url = settings.OLLAMA_BASE_URL
        if not ollama_url: return # Should be caught by _check_configured_providers, but double-check

        tags_endpoint = f"{ollama_url.rstrip('/')}/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(tags_endpoint, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        models_data = data.get("models", [])
                        if models_data:
                            # Extract just the name (which includes the tag)
                            self._raw_models["ollama"] = [ModelInfo(id=m.get("name"), name=m.get("name")) for m in models_data if m.get("name")]
                            logger.info(f"Discovered {len(self._raw_models['ollama'])} models from Ollama: {[m['id'] for m in self._raw_models['ollama']]}.")
                        else:
                             logger.info("Ollama /api/tags endpoint returned empty models list (no models pulled?).")
                    else:
                         error_text = await response.text()
                         logger.error(f"Failed to fetch Ollama models from {tags_endpoint}. Status: {response.status}, Response: {error_text[:200]}")
        except aiohttp.ClientError as e:
            logger.error(f"Error connecting to Ollama API ({tags_endpoint}): {e}")
        except asyncio.TimeoutError:
             logger.error(f"Timeout fetching models from Ollama API ({tags_endpoint}).")
        except Exception as e:
            logger.error(f"Unexpected error fetching Ollama models: {e}", exc_info=True)

    async def _discover_litellm_models(self):
        """Fetches model details from the LiteLLM /models endpoint."""
        logger.debug("Discovering LiteLLM models...")
        litellm_url = settings.LITELLM_BASE_URL
        if not litellm_url: return

        models_endpoint = f"{litellm_url.rstrip('/')}/models"
        headers = {}
        if settings.LITELLM_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LITELLM_API_KEY}"

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(models_endpoint, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        # LiteLLM /models returns a list like OpenAI's /v1/models
                        models_data = data.get("data", [])
                        if models_data:
                             # Assuming LiteLLM provides 'id' for the model name
                             self._raw_models["litellm"] = [ModelInfo(id=m.get("id"), name=m.get("id")) for m in models_data if m.get("id")]
                             logger.info(f"Discovered {len(self._raw_models['litellm'])} models from LiteLLM: {[m['id'] for m in self._raw_models['litellm']]}.")
                        else:
                             logger.warning(f"LiteLLM models endpoint ({models_endpoint}) returned empty data list.")
                    else:
                         error_text = await response.text()
                         logger.error(f"Failed to fetch LiteLLM models from {models_endpoint}. Status: {response.status}, Response: {error_text[:200]}")
        except aiohttp.ClientError as e:
            logger.error(f"Error connecting to LiteLLM API ({models_endpoint}): {e}")
        except asyncio.TimeoutError:
             logger.error(f"Timeout fetching models from LiteLLM API ({models_endpoint}).")
        except Exception as e:
            logger.error(f"Unexpected error fetching LiteLLM models: {e}", exc_info=True)

    def _apply_filters(self):
        """
        Filters the raw discovered models based on MODEL_TIER and provider availability.
        Prioritizes local models.
        """
        self.available_models = {provider: [] for provider in self._raw_models}
        logger.debug(f"Applying filters. Tier='{self._model_tier}'. Configured providers: {list(self._configured_providers)}")

        # 1. Process Local Providers (Ollama, LiteLLM) - Always included if configured
        for provider in ["ollama", "litellm"]:
            if provider in self._configured_providers:
                self.available_models[provider] = self._raw_models[provider]
                logger.debug(f"Included all {len(self.available_models[provider])} discovered models for local provider '{provider}'.")

        # 2. Process Remote Providers (OpenRouter, OpenAI) - Apply Tier filter
        for provider in ["openrouter", "openai"]:
            if provider in self._configured_providers:
                filtered_list = []
                for model in self._raw_models[provider]:
                    model_id = model.get("id", "")
                    is_free = ":free" in model_id.lower() # Simple check for OpenRouter free tier

                    if self._model_tier == "FREE":
                        if is_free:
                            filtered_list.append(model)
                        # else: logger.debug(f"Tier Filter: Skipping non-free model '{model_id}' for provider '{provider}'.")
                    else: # "ALL" tier
                        filtered_list.append(model)

                self.available_models[provider] = filtered_list
                logger.debug(f"Included {len(filtered_list)} models for remote provider '{provider}' after tier filtering.")

        # Remove providers with no available models after filtering
        self.available_models = {p: m for p, m in self.available_models.items() if m}

    def get_available_models_list(self, provider: Optional[str] = None) -> List[str]:
        """
        Returns a flat list of available model IDs, optionally filtered by provider.
        Prioritizes local models (Ollama, LiteLLM) first.
        """
        model_ids = []
        # Prioritize local
        for p in ["ollama", "litellm"]:
            if (provider is None or p == provider) and p in self.available_models:
                model_ids.extend([m.get("id", "unknown") for m in self.available_models[p]])
        # Then remote
        for p in ["openrouter", "openai"]:
             if (provider is None or p == provider) and p in self.available_models:
                model_ids.extend([m.get("id", "unknown") for m in self.available_models[p]])

        return sorted(list(set(model_ids))) # Return unique, sorted list

    def get_available_models_dict(self) -> Dict[str, List[ModelInfo]]:
        """Returns the full dictionary of available models, categorized by provider."""
        return self.available_models

    def get_formatted_available_models(self) -> str:
        """ Returns a formatted string listing available models, suitable for prompts. """
        if not self.available_models:
            return "Model Availability: No models discovered or available after filtering."

        lines = ["**Currently Available Models (Based on config & tier):**"]
        try:
            # Prioritize local
            for provider in ["ollama", "litellm"]:
                if provider in self.available_models:
                     model_names = sorted([m.get("id", "?") for m in self.available_models[provider]])
                     if model_names: lines.append(f"- **{provider} (Local)**: `{', '.join(model_names)}`")
            # Then remote
            for provider in ["openrouter", "openai"]:
                 if provider in self.available_models:
                     model_names = sorted([m.get("id", "?") for m in self.available_models[provider]])
                     if model_names: lines.append(f"- **{provider}**: `{', '.join(model_names)}`")

            if len(lines) == 1: # Only the header was added
                return "Model Availability: No models discovered or available after filtering."

            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error formatting available models: {e}")
            return "**Error:** Could not format available models list."

    def is_model_available(self, provider: str, model_id: str) -> bool:
        """Checks if a specific model ID is available for the given provider after filtering."""
        if provider not in self.available_models:
            return False
        return any(m.get("id") == model_id for m in self.available_models[provider])

    def _log_available_models(self):
        """Logs the final list of available models."""
        if not self.available_models:
            logger.warning("No models available after discovery and filtering.")
            return

        logger.info("--- Available Models ---")
        for provider, models in self.available_models.items():
            if models:
                 model_names = sorted([m.get("id", "?") for m in models])
                 logger.info(f"  {provider}: {len(model_names)} models -> {model_names}")
            else:
                 logger.info(f"  {provider}: 0 models")
        logger.info("------------------------")


# --- Optional: Create a singleton instance if needed globally ---
# model_registry = ModelRegistry()
