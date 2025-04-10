# START OF FILE src/config/model_registry.py
import asyncio
import logging
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING
import aiohttp
import json
import copy # Import copy for deepcopy in getters

# Use type hinting to avoid circular import runtime error
if TYPE_CHECKING:
    from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Default Ports and Public Endpoints
DEFAULT_OLLAMA_PORT = 11434
DEFAULT_LITELLM_PORT = 4000 # Adjust if your LiteLLM default is different
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

class ModelInfo(Dict):
    """Simple dictionary subclass for type hinting model info."""
    pass

class ModelRegistry:
    """
    Handles discovery, filtering, and storage of available LLM models
    from various providers. First checks provider reachability based on .env
    settings or localhost defaults, then discovers models for reachable providers.
    Applies filtering based on the MODEL_TIER environment variable.
    Accepts settings object for configuration access.
    """

    def __init__(self, settings_obj: 'Settings'):
        """
        Initializes the registry.

        Args:
            settings_obj (Settings): The loaded application settings instance.
        """
        self.settings = settings_obj # Store settings instance
        self._raw_models: Dict[str, List[ModelInfo]] = {
            "openrouter": [], "ollama": [], "litellm": [], "openai": [],
        }
        # Stores models available *after* filtering and reachability checks
        self.available_models: Dict[str, List[ModelInfo]] = {}
        # Stores reachable provider info: provider_name -> base_url
        self._reachable_providers: Dict[str, str] = {}
        # Use MODEL_TIER from the passed settings object
        self._model_tier: str = getattr(self.settings, 'MODEL_TIER', 'ALL').upper()
        logger.info(f"ModelRegistry initialized. MODEL_TIER='{self._model_tier}'.")

    async def discover_models_and_providers(self):
        """ Coordinates provider reachability checks and model discovery. """
        self._reachable_providers.clear()
        logger.info("Starting provider reachability checks...")
        await self._discover_providers()

        if not self._reachable_providers:
             logger.warning("No providers found reachable. Skipping model discovery.")
             self.available_models = {}
             return

        logger.info(f"Reachable providers found: {list(self._reachable_providers.keys())}. Starting model discovery...")
        self._raw_models = {provider: [] for provider in self._raw_models} # Reset raw

        discovery_tasks = []
        if "openrouter" in self._reachable_providers: discovery_tasks.append(self._discover_openrouter_models())
        if "ollama" in self._reachable_providers: discovery_tasks.append(self._discover_ollama_models())
        if "litellm" in self._reachable_providers: discovery_tasks.append(self._discover_litellm_models())

        await asyncio.gather(*discovery_tasks, return_exceptions=True)

        # Manually add common OpenAI models if provider is reachable
        if "openai" in self._reachable_providers:
             common_openai = ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
             # Ensure provider key is added even if list is empty initially
             if "openai" not in self._raw_models: self._raw_models["openai"] = []
             self._raw_models["openai"].extend([ModelInfo(id=m, name=m, provider="openai") for m in common_openai if not any(existing_m.get('id') == m for existing_m in self._raw_models["openai"])])
             logger.info(f"Manually added common models for configured OpenAI provider: {common_openai}")

        self._apply_filters()
        logger.info("Provider and model discovery/filtering complete.")
        self._log_available_models()

    async def _discover_providers(self):
        """ Checks reachability of configured or local providers using self.settings. """
        tasks = []
        # Check remote providers based on settings config
        if self.settings.is_provider_configured("openrouter"):
             or_base = self.settings.OPENROUTER_BASE_URL or OPENROUTER_MODELS_URL.rsplit('/', 1)[0]
             self._reachable_providers["openrouter"] = or_base
             logger.debug("Marked OpenRouter as reachable (API key present).")
        if self.settings.is_provider_configured("openai"):
             oa_base = self.settings.OPENAI_BASE_URL or "https://api.openai.com/v1"
             self._reachable_providers["openai"] = oa_base
             logger.debug("Marked OpenAI as reachable (API key present).")
        # Add checks for other remote providers (Anthropic, Google, DeepSeek) similarly if needed

        # Check local providers (Ollama, LiteLLM)
        tasks.append(self._check_local_provider("ollama", self.settings.OLLAMA_BASE_URL, DEFAULT_OLLAMA_PORT))
        tasks.append(self._check_local_provider("litellm", self.settings.LITELLM_BASE_URL, DEFAULT_LITELLM_PORT))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_local_provider(self, provider_name: str, env_url: Optional[str], default_port: int):
        """ Checks reachability for a local provider using provided URLs/ports. """
        check_url = None; source = ""
        if env_url: check_url = env_url.rstrip('/'); source = ".env"; logger.debug(f"Checking {provider_name} reachability using URL from .env: {check_url}")
        else: check_url = f"http://localhost:{default_port}"; source = "localhost default"; logger.debug(f"Checking {provider_name} reachability using default localhost URL: {check_url}")

        health_endpoint = "/" if provider_name == "ollama" else "/health"; full_check_url = f"{check_url}{health_endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(full_check_url, timeout=3) as response:
                    if response.status == 200:
                        text_content = await response.text(); is_valid = False
                        if provider_name == "ollama" and "Ollama is running" in text_content: is_valid = True
                        elif provider_name == "litellm":
                             try:
                                 # Use await response.json(content_type=None) to be more flexible
                                 json_resp = await response.json(content_type=None);
                                 if isinstance(json_resp, dict): is_valid = True
                             except (json.JSONDecodeError, aiohttp.ContentTypeError, aiohttp.ClientResponseError):
                                  if "healthy" in text_content.lower(): is_valid = True
                                  else: logger.warning(f"LiteLLM health check at {full_check_url} returned non-JSON/unhealthy: {text_content[:100]}")
                        if is_valid: self._reachable_providers[provider_name] = check_url; logger.info(f"Successfully reached {provider_name} via {source} at: {check_url}")
                        else: logger.warning(f"{provider_name} found at {check_url} ({source}) but health check response was unexpected.")
                    else: logger.warning(f"Failed to reach {provider_name} via {source} at {check_url}. Status: {response.status}")
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError): logger.info(f"{provider_name} not reachable via {source} at {check_url}.")
        except Exception as e: logger.error(f"Error checking reachability for {provider_name} via {source} at {check_url}: {e}", exc_info=False)

    async def _discover_openrouter_models(self):
        """ Fetches models using the confirmed reachable URL. """
        logger.debug("Discovering OpenRouter models...")
        provider_url = self._reachable_providers.get("openrouter");
        if not provider_url: return
        models_url = f"{provider_url.rstrip('/')}/models"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(models_url, timeout=20) as response:
                    if response.status == 200:
                        data = await response.json(); models_data = data.get("data", [])
                        if models_data:
                            # Include provider in ModelInfo
                            self._raw_models["openrouter"] = [ModelInfo(id=m.get("id"), name=m.get("name"), description=m.get("description"), provider="openrouter") for m in models_data if m.get("id")]
                            logger.info(f"Discovered {len(self._raw_models['openrouter'])} models from OpenRouter.")
                        else: logger.warning("OpenRouter models endpoint returned empty data list.")
                    else: error_text = await response.text(); logger.error(f"Failed to fetch OpenRouter models from {models_url}. Status: {response.status}, Response: {error_text[:200]}")
        except Exception as e: logger.error(f"Error fetching OpenRouter models from {models_url}: {e}", exc_info=True)

    async def _discover_ollama_models(self):
        """ Fetches models using the confirmed reachable URL. """
        logger.debug("Discovering Ollama models...")
        ollama_url = self._reachable_providers.get("ollama");
        if not ollama_url: return
        tags_endpoint = f"{ollama_url}/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(tags_endpoint, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json(); models_data = data.get("models", [])
                        if models_data:
                             # Include provider in ModelInfo
                             self._raw_models["ollama"] = [ModelInfo(id=m.get("name"), name=m.get("name"), provider="ollama") for m in models_data if m.get("name")]
                             logger.info(f"Discovered {len(self._raw_models['ollama'])} models from Ollama at {ollama_url}: {[m['id'] for m in self._raw_models['ollama']]}.")
                        else: logger.info(f"Ollama /api/tags endpoint at {ollama_url} returned empty models list.")
                    else: error_text = await response.text(); logger.error(f"Failed to fetch Ollama models from {tags_endpoint}. Status: {response.status}, Response: {error_text[:200]}")
        except Exception as e: logger.error(f"Error fetching Ollama models from {tags_endpoint}: {e}", exc_info=True)

    async def _discover_litellm_models(self):
        """ Fetches models using the confirmed reachable URL. """
        logger.debug("Discovering LiteLLM models...")
        litellm_url = self._reachable_providers.get("litellm");
        if not litellm_url: return
        models_endpoint = f"{litellm_url}/models"; headers = {}
        if self.settings.LITELLM_API_KEY: headers["Authorization"] = f"Bearer {self.settings.LITELLM_API_KEY}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(models_endpoint, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json(); models_data = data.get("data", [])
                        if models_data:
                            # Include provider in ModelInfo
                            self._raw_models["litellm"] = [ModelInfo(id=m.get("id"), name=m.get("id"), provider="litellm") for m in models_data if m.get("id")]
                            logger.info(f"Discovered {len(self._raw_models['litellm'])} models from LiteLLM at {litellm_url}: {[m['id'] for m in self._raw_models['litellm']]}.")
                        else: logger.warning(f"LiteLLM models endpoint ({models_endpoint}) returned empty data list.")
                    else: error_text = await response.text(); logger.error(f"Failed to fetch LiteLLM models from {models_endpoint}. Status: {response.status}, Response: {error_text[:200]}")
        except Exception as e: logger.error(f"Error fetching LiteLLM models from {models_endpoint}: {e}", exc_info=True)

    def _apply_filters(self):
        """ Filters raw models based on reachability and tier using self._model_tier. """
        self.available_models = {provider: [] for provider in self._reachable_providers}
        logger.debug(f"Applying filters. Tier='{self._model_tier}'. Reachable providers: {list(self._reachable_providers.keys())}")
        for provider, models in self._raw_models.items():
            if provider not in self._reachable_providers: continue
            if provider in ["ollama", "litellm"]:
                 self.available_models[provider] = models; logger.debug(f"Included all {len(models)} discovered models for reachable local provider '{provider}'.")
            elif provider in ["openrouter", "openai"]:
                filtered_list = []
                for model in models:
                    model_id = model.get("id", ""); is_free = ":free" in model_id.lower()
                    if self._model_tier == "FREE":
                        if is_free: filtered_list.append(model)
                    else: filtered_list.append(model)
                self.available_models[provider] = filtered_list; logger.debug(f"Included {len(filtered_list)} models for reachable remote provider '{provider}' after tier filtering.")
        self.available_models = {p: m for p, m in self.available_models.items() if m}

    # --- Getter Methods (Restored) ---

    def get_available_models_list(self, provider: Optional[str] = None) -> List[str]:
        """
        Returns a flat list of available model IDs, optionally filtered by provider.
        Prioritizes local models (Ollama, LiteLLM) first.
        """
        model_ids = []
        provider_order = ["ollama", "litellm", "openrouter", "openai"] # Prioritization order
        processed_providers = set()

        # Process in priority order
        for p in provider_order:
            if (provider is None or p == provider) and p in self.available_models:
                model_ids.extend([m.get("id", "unknown") for m in self.available_models[p]])
                processed_providers.add(p)

        # Add any remaining available providers not in the explicit order
        if provider is None:
            for p in self.available_models:
                if p not in processed_providers:
                     model_ids.extend([m.get("id", "unknown") for m in self.available_models[p]])

        return sorted(list(set(model_ids))) # Return unique, sorted list


    def get_available_models_dict(self) -> Dict[str, List[ModelInfo]]:
        """Returns a deep copy of the dictionary of available models, categorized by provider."""
        return copy.deepcopy(self.available_models) # Use deepcopy for safety


    def find_provider_for_model(self, model_id: str) -> Optional[str]:
        """
        Searches available models to find the provider for a given model ID.
        Prioritizes local providers.
        """
        provider_order = ["ollama", "litellm", "openrouter", "openai"] # Prioritization order
        processed_providers = set()

        for provider_name in provider_order:
            if provider_name in self.available_models:
                 if any(m.get("id") == model_id for m in self.available_models[provider_name]):
                     return provider_name
                 processed_providers.add(provider_name)

        # Check remaining providers
        for provider_name in self.available_models:
             if provider_name not in processed_providers:
                 if any(m.get("id") == model_id for m in self.available_models[provider_name]):
                     return provider_name
        return None


    def get_formatted_available_models(self) -> str:
        """ Returns a formatted string listing available models, suitable for prompts. """
        if not self.available_models: return "Model Availability: No models discovered or available after filtering."
        lines = ["**Currently Available Models (Based on config & tier):**"]; found_any = False; provider_order = ["ollama", "litellm", "openrouter", "openai"]
        try:
            processed_providers = set()
            for provider in provider_order:
                if provider in self.available_models:
                     models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                     if model_names: local_tag = " (Local)" if provider in ["ollama", "litellm"] else ""; lines.append(f"- **{provider}{local_tag}**: `{', '.join(model_names)}`"); found_any = True; processed_providers.add(provider)
            for provider in self.available_models:
                 if provider not in processed_providers:
                      models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                      if model_names: lines.append(f"- **{provider}**: `{', '.join(model_names)}`"); found_any = True
            if not found_any: return "Model Availability: No models discovered or available after filtering."
            return "\n".join(lines)
        except Exception as e: logger.error(f"Error formatting available models: {e}"); return "**Error:** Could not format available models list."


    def is_model_available(self, provider: str, model_id: str) -> bool:
        """Checks if a specific model ID is available for the given provider after filtering."""
        if provider not in self.available_models: return False
        return any(m.get("id") == model_id for m in self.available_models[provider])


    def get_reachable_provider_url(self, provider: str) -> Optional[str]:
        """Returns the confirmed reachable base URL for a provider."""
        return self._reachable_providers.get(provider)


    def _log_available_models(self):
        """Logs the final list of available models from reachable providers."""
        if not self.available_models: logger.warning("No models available after discovery and filtering."); return
        logger.info("--- Available Models (from Reachable Providers) ---"); provider_order = ["ollama", "litellm", "openrouter", "openai"]; logged_providers = set()
        for provider in provider_order:
            if provider in self.available_models: models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models]); logger.info(f"  {provider}: {len(model_names)} models -> {model_names}"); logged_providers.add(provider)
        for provider in self.available_models:
            if provider not in logged_providers: models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models]); logger.info(f"  {provider}: {len(model_names)} models -> {model_names}")
        logger.info("--------------------------------------------------")
