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

# Define a limited set of common local IPs to check as a last resort
COMMON_LOCAL_IPS_TO_CHECK = ["192.168.1.1", "192.168.0.1", "10.0.0.1"]

class ModelInfo(Dict):
    """Simple dictionary subclass for type hinting model info."""
    pass

class ModelRegistry:
    """
    Handles discovery, filtering, and storage of available LLM models
    from various providers. First checks provider reachability based on .env
    settings or localhost defaults (with limited network fallback), then discovers
    models for reachable providers. Applies filtering based on the MODEL_TIER
    environment variable. Accepts settings object for configuration access.
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
        await self._discover_providers() # This now includes prioritized local checks

        if not self._reachable_providers:
             logger.warning("No providers found reachable. Skipping model discovery.")
             self.available_models = {}
             return

        logger.info(f"Reachable providers found: {list(self._reachable_providers.items())}. Starting model discovery...")
        self._raw_models = {provider: [] for provider in self._raw_models} # Reset raw

        discovery_tasks = []
        # Create discovery tasks only for providers confirmed reachable
        if "openrouter" in self._reachable_providers: discovery_tasks.append(self._discover_openrouter_models())
        if "ollama" in self._reachable_providers: discovery_tasks.append(self._discover_ollama_models())
        if "litellm" in self._reachable_providers: discovery_tasks.append(self._discover_litellm_models())
        if "openai" in self._reachable_providers: discovery_tasks.append(self._discover_openai_models()) # Add OpenAI discovery

        await asyncio.gather(*discovery_tasks, return_exceptions=True)

        self._apply_filters()
        logger.info("Provider and model discovery/filtering complete.")
        self._log_available_models()

    async def _discover_providers(self):
        """
        Checks reachability of configured or local providers using self.settings.
        Prioritizes local checks: .env URL -> localhost -> common IPs.
        """
        tasks = []
        # Check remote providers based on settings config (synchronous checks, just add to reachable if key exists)
        if self.settings.is_provider_configured("openrouter"):
             or_base = self.settings.OPENROUTER_BASE_URL or OPENROUTER_MODELS_URL.rsplit('/', 1)[0]
             self._reachable_providers["openrouter"] = or_base.rstrip('/')
             logger.debug(f"Marked OpenRouter as reachable (API key present). Base: {self._reachable_providers['openrouter']}")
        if self.settings.is_provider_configured("openai"):
             oa_base = self.settings.OPENAI_BASE_URL or "https://api.openai.com/v1"
             self._reachable_providers["openai"] = oa_base.rstrip('/')
             logger.debug(f"Marked OpenAI as reachable (API key present). Base: {self._reachable_providers['openai']}")
        # Add checks for other remote providers (Anthropic, Google, DeepSeek) similarly if needed

        # --- Prioritized Local Provider Checks ---
        local_providers_to_check = [
            ("ollama", self.settings.OLLAMA_BASE_URL, DEFAULT_OLLAMA_PORT),
            ("litellm", self.settings.LITELLM_BASE_URL, DEFAULT_LITELLM_PORT)
        ]

        local_check_tasks = []
        for provider_name, env_url, default_port in local_providers_to_check:
            local_check_tasks.append(self._check_local_provider_prioritized(provider_name, env_url, default_port))

        # Run local checks concurrently
        await asyncio.gather(*local_check_tasks, return_exceptions=True)
        # The results of local checks are stored directly in self._reachable_providers by the called methods.


    async def _check_local_provider_prioritized(self, provider_name: str, env_url: Optional[str], default_port: int):
        """
        Checks local provider reachability in order: .env URL -> localhost -> common IPs.
        Stores the *first* successful URL found in self._reachable_providers.
        """
        logger.debug(f"Starting prioritized reachability check for {provider_name}...")

        # 1. Check .env URL if provided
        if env_url:
            env_url_base = env_url.rstrip('/')
            logger.debug(f"Checking {provider_name} using URL from .env: {env_url_base}")
            if await self._check_single_local_url(provider_name, env_url_base, ".env"):
                self._reachable_providers[provider_name] = env_url_base # Store and return if successful
                logger.info(f"Found reachable {provider_name} via .env URL: {env_url_base}")
                return

        # 2. Check localhost if .env URL failed or wasn't provided
        localhost_url = f"http://localhost:{default_port}"
        logger.debug(f"Checking {provider_name} using default localhost URL: {localhost_url}")
        if await self._check_single_local_url(provider_name, localhost_url, "localhost default"):
            self._reachable_providers[provider_name] = localhost_url # Store and return
            logger.info(f"Found reachable {provider_name} via localhost default: {localhost_url}")
            return

        # 3. Check common local IPs as a last resort
        logger.debug(f"Checking {provider_name} using common local IPs: {COMMON_LOCAL_IPS_TO_CHECK}")
        for ip in COMMON_LOCAL_IPS_TO_CHECK:
            common_ip_url = f"http://{ip}:{default_port}"
            if await self._check_single_local_url(provider_name, common_ip_url, f"network guess ({ip})"):
                self._reachable_providers[provider_name] = common_ip_url # Store and return
                logger.info(f"Found reachable {provider_name} via network guess: {common_ip_url}")
                return

        logger.info(f"{provider_name} was not found reachable via .env, localhost, or common local IPs.")


    async def _check_single_local_url(self, provider_name: str, base_url: str, source_description: str) -> bool:
        """
        Checks reachability for a single local provider URL.

        Args:
            provider_name (str): Name of the provider (e.g., 'ollama').
            base_url (str): The base URL to check (e.g., 'http://localhost:11434').
            source_description (str): Description of where the URL came from (for logging).

        Returns:
            bool: True if reachable and valid response, False otherwise.
        """
        health_endpoint = "/" if provider_name == "ollama" else "/health" # Use / for Ollama, /health for LiteLLM
        full_check_url = f"{base_url}{health_endpoint}"
        try:
            # Increased timeout slightly for potentially slower network checks
            async with aiohttp.ClientSession() as session:
                async with session.get(full_check_url, timeout=5) as response:
                    if response.status == 200:
                        text_content = await response.text()
                        is_valid = False
                        if provider_name == "ollama" and "Ollama is running" in text_content:
                            is_valid = True
                        elif provider_name == "litellm":
                            # Check for simple "healthy" text or valid JSON response
                            if "healthy" in text_content.lower():
                                is_valid = True
                            else:
                                try:
                                    # Try parsing as JSON, accept any dict as potentially valid
                                    json_resp = await response.json(content_type=None)
                                    if isinstance(json_resp, dict):
                                        is_valid = True
                                except (json.JSONDecodeError, aiohttp.ContentTypeError, aiohttp.ClientResponseError):
                                    logger.debug(f"LiteLLM health check at {full_check_url} ({source_description}) returned non-JSON/unhealthy text: {text_content[:100]}")
                                    is_valid = False # Explicitly false if JSON parse fails and no "healthy" text

                        if is_valid:
                            logger.debug(f"Successfully reached {provider_name} via {source_description} at: {base_url}")
                            return True
                        else:
                            logger.debug(f"{provider_name} found at {base_url} ({source_description}) but health check response was unexpected.")
                            return False
                    else:
                        logger.debug(f"Failed to reach {provider_name} via {source_description} at {full_check_url}. Status: {response.status}")
                        return False
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
            logger.debug(f"{provider_name} not reachable via {source_description} at {base_url}.")
            return False
        except Exception as e:
            logger.error(f"Error checking reachability for {provider_name} via {source_description} at {base_url}: {e}", exc_info=False)
            return False

    async def _discover_openrouter_models(self):
        """ Fetches models using the confirmed reachable URL. """
        logger.debug("Discovering OpenRouter models...")
        provider_url = self._reachable_providers.get("openrouter")
        if not provider_url: logger.warning("Skipping OpenRouter model discovery: provider not reachable."); return
        models_url = f"{provider_url}/models" # Assumes base_url is correct (e.g., https://openrouter.ai/api/v1)
        headers = {"Authorization": f"Bearer {self.settings.OPENROUTER_API_KEY}"} if self.settings.OPENROUTER_API_KEY else {}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
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
        ollama_url = self._reachable_providers.get("ollama")
        if not ollama_url: logger.warning("Skipping Ollama model discovery: provider not reachable."); return
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
        litellm_url = self._reachable_providers.get("litellm")
        if not litellm_url: logger.warning("Skipping LiteLLM model discovery: provider not reachable."); return
        models_endpoint = f"{litellm_url}/models"
        headers = {}
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

    async def _discover_openai_models(self):
        """
        Manually adds common OpenAI models if the provider is reachable.
        Does not currently hit the /v1/models endpoint as it requires auth differently.
        """
        logger.debug("Checking for configured OpenAI...")
        if "openai" not in self._reachable_providers:
            logger.debug("Skipping OpenAI model adding: provider not reachable/configured.")
            return

        common_openai = ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
        # Ensure provider key exists even if list is empty initially
        if "openai" not in self._raw_models: self._raw_models["openai"] = []
        # Only add if not already present (e.g., from a future /v1/models call)
        added_count = 0
        for model_name in common_openai:
             if not any(existing_m.get('id') == model_name for existing_m in self._raw_models["openai"]):
                 self._raw_models["openai"].append(ModelInfo(id=model_name, name=model_name, provider="openai"))
                 added_count += 1
        if added_count > 0:
             logger.info(f"Manually added {added_count} common models for configured OpenAI provider: {common_openai}")
        else:
             logger.debug("Common OpenAI models already present or none to add.")


    def _apply_filters(self):
        """ Filters raw models based on reachability and tier using self._model_tier. """
        # Initialize available_models only for providers confirmed reachable
        self.available_models = {provider: [] for provider in self._reachable_providers}
        logger.debug(f"Applying filters. Tier='{self._model_tier}'. Reachable providers: {list(self._reachable_providers.keys())}")

        # Iterate through raw models discovered for *all* potential providers
        for provider, models in self._raw_models.items():
            # Skip if this provider wasn't found reachable
            if provider not in self._reachable_providers: continue

            # Apply tier filtering based on provider type
            if provider in ["ollama", "litellm"]:
                 # Local providers bypass tier filtering
                 self.available_models[provider] = models
                 logger.debug(f"Included all {len(models)} discovered models for reachable local provider '{provider}'.")
            elif provider in ["openrouter", "openai"]: # Add other remote providers here if needed
                filtered_list = []
                for model in models:
                    model_id = model.get("id", "")
                    # --- Tier Filtering Logic ---
                    # Determine if the model is considered free (simple check for now)
                    # OpenRouter uses ':free', OpenAI doesn't have an explicit free tag via common models
                    is_free = ":free" in model_id.lower() if provider == "openrouter" else False # Assume OpenAI models aren't tagged free

                    if self._model_tier == "FREE":
                        if is_free: # Only include if explicitly free
                             filtered_list.append(model)
                        # else: logger.debug(f"Skipping non-free model '{model_id}' for provider '{provider}' due to FREE tier setting.")
                    elif self._model_tier == "ALL":
                        filtered_list.append(model) # Include all models if tier is ALL
                    # Add other tiers like "PAID_ONLY" if needed
                    # --- End Tier Filtering ---
                self.available_models[provider] = filtered_list
                logger.debug(f"Included {len(filtered_list)} models for reachable remote provider '{provider}' after tier filtering.")

        # Clean up providers with no available models after filtering
        self.available_models = {p: m for p, m in self.available_models.items() if m}


    # --- Getter Methods (Remain the same) ---

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
        Prioritizes local providers. Handles models with provider prefix (e.g., ollama/llama3).
        """
        # Check if model_id includes the provider prefix
        if '/' in model_id:
            provider_prefix, model_suffix = model_id.split('/', 1)
            if provider_prefix in self.available_models:
                 if any(m.get("id") == model_suffix for m in self.available_models[provider_prefix]):
                     return provider_prefix
            # If prefix doesn't match known provider or model isn't found under that provider,
            # proceed to check based on the full model_id against other providers (like openrouter)

        # Check providers in priority order
        provider_order = ["ollama", "litellm", "openrouter", "openai"]
        processed_providers = set()

        for provider_name in provider_order:
            if provider_name in self.available_models:
                 # Check full ID first (for non-prefixed IDs like openrouter)
                 if any(m.get("id") == model_id for m in self.available_models[provider_name]):
                     return provider_name
                 # Check suffix if provider matches (for prefixed IDs like ollama/llama3)
                 if '/' in model_id:
                      provider_prefix, model_suffix = model_id.split('/', 1)
                      if provider_name == provider_prefix and any(m.get("id") == model_suffix for m in self.available_models[provider_name]):
                           return provider_name
                 processed_providers.add(provider_name)

        # Check remaining providers (less common case)
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
                     if model_names:
                         local_tag = " (Local)" if provider in ["ollama", "litellm"] else ""
                         # Add provider prefix for local models for clarity in prompt
                         display_names = [f"{provider}/{name}" if local_tag else name for name in model_names]
                         lines.append(f"- **{provider}{local_tag}**: `{', '.join(display_names)}`")
                         found_any = True; processed_providers.add(provider)
            # Add remaining providers if any
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
            if provider in self.available_models:
                models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                local_tag = " (Local)" if provider in ["ollama", "litellm"] else ""
                display_names = [f"{provider}/{name}" if local_tag else name for name in model_names]
                logger.info(f"  {provider}{local_tag}: {len(display_names)} models -> {display_names}")
                logged_providers.add(provider)
        # Log remaining providers
        for provider in self.available_models:
            if provider not in logged_providers:
                models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                logger.info(f"  {provider}: {len(model_names)} models -> {model_names}")
        logger.info("--------------------------------------------------")
