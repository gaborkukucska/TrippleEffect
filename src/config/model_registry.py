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

        # Use gather with return_exceptions=True to log errors without stopping others
        results = await asyncio.gather(*discovery_tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # Attempt to identify which task failed based on order (may be brittle)
                failed_task_provider = "Unknown"
                task_index = 0
                if "openrouter" in self._reachable_providers and i == task_index: failed_task_provider = "OpenRouter"; task_index+=1
                elif "ollama" in self._reachable_providers and i == task_index: failed_task_provider = "Ollama"; task_index+=1
                elif "litellm" in self._reachable_providers and i == task_index: failed_task_provider = "LiteLLM"; task_index+=1
                elif "openai" in self._reachable_providers and i == task_index: failed_task_provider = "OpenAI"

                logger.error(f"Error during model discovery for provider '{failed_task_provider}': {result}", exc_info=result)


        self._apply_filters()
        logger.info("Provider and model discovery/filtering complete.")
        self._log_available_models()

    async def _discover_providers(self):
        """
        Checks reachability of configured or local providers using self.settings.
        Prioritizes local checks: .env URL -> localhost -> common IPs.
        """
        # Check remote providers based on settings config (synchronous checks)
        # Add provider to reachable list only if keys are configured
        if self.settings.is_provider_configured("openrouter"):
             or_base = self.settings.OPENROUTER_BASE_URL or OPENROUTER_MODELS_URL.rsplit('/', 1)[0]
             self._reachable_providers["openrouter"] = or_base.rstrip('/')
             logger.debug(f"Marked OpenRouter as potentially reachable (API key(s) present). Base: {self._reachable_providers['openrouter']}")
        else: logger.debug("Skipping OpenRouter reachability: No API key found in settings.")

        if self.settings.is_provider_configured("openai"):
             oa_base = self.settings.OPENAI_BASE_URL or "https://api.openai.com/v1"
             self._reachable_providers["openai"] = oa_base.rstrip('/')
             logger.debug(f"Marked OpenAI as potentially reachable (API key(s) present). Base: {self._reachable_providers['openai']}")
        else: logger.debug("Skipping OpenAI reachability: No API key found in settings.")
        # Add checks for other remote providers similarly

        # --- Prioritized Local Provider Checks (Run concurrently) ---
        local_providers_to_check = [
            ("ollama", self.settings.OLLAMA_BASE_URL, DEFAULT_OLLAMA_PORT),
            ("litellm", self.settings.LITELLM_BASE_URL, DEFAULT_LITELLM_PORT)
        ]
        local_check_tasks = [
            self._check_local_provider_prioritized(provider_name, env_url, default_port)
            for provider_name, env_url, default_port in local_providers_to_check
        ]
        await asyncio.gather(*local_check_tasks, return_exceptions=True)
        # Local check results are stored directly in self._reachable_providers


    async def _check_local_provider_prioritized(self, provider_name: str, env_url: Optional[str], default_port: int):
        """
        Checks local provider reachability.
        For Ollama: If proxy is enabled, checks ONLY the proxy URL. Otherwise, checks .env -> localhost -> common IPs.
        For others: Checks .env -> localhost -> common IPs.
        """
        logger.debug(f"Starting prioritized reachability check for {provider_name}...")

        # --- Special Handling for Ollama Proxy ---
        if provider_name == "ollama" and self.settings.USE_OLLAMA_PROXY:
            proxy_port = self.settings.OLLAMA_PROXY_PORT
            proxy_url = f"http://localhost:{proxy_port}"
            logger.debug(f"Ollama proxy is enabled. Checking ONLY proxy URL: {proxy_url}")
            if await self._check_single_local_url("ollama", proxy_url, "proxy"):
                self._reachable_providers["ollama"] = proxy_url
                logger.info(f"Found reachable Ollama via enabled proxy: {proxy_url}")
                return # Found via proxy, stop checking
            else:
                logger.warning(f"Ollama proxy enabled in settings but NOT reachable at {proxy_url}. Ollama will be unavailable.")
                return # Proxy enabled but unreachable, do not fall back to direct checks

        # --- Standard Check Logic (for non-Ollama or Ollama with proxy disabled) ---
        if env_url:
            env_url_base = env_url.rstrip('/')
            logger.debug(f"Checking {provider_name} using URL from .env: {env_url_base}")
            if await self._check_single_local_url(provider_name, env_url_base, ".env"):
                self._reachable_providers[provider_name] = env_url_base
                logger.info(f"Found reachable {provider_name} via .env URL: {env_url_base}")
                return

        localhost_url = f"http://localhost:{default_port}"
        logger.debug(f"Checking {provider_name} using default localhost URL: {localhost_url}")
        if await self._check_single_local_url(provider_name, localhost_url, "localhost default"):
            self._reachable_providers[provider_name] = localhost_url
            logger.info(f"Found reachable {provider_name} via localhost default: {localhost_url}")
            return

        logger.debug(f"Checking {provider_name} using common local IPs: {COMMON_LOCAL_IPS_TO_CHECK}")
        for ip in COMMON_LOCAL_IPS_TO_CHECK:
            common_ip_url = f"http://{ip}:{default_port}"
            if await self._check_single_local_url(provider_name, common_ip_url, f"network guess ({ip})"):
                self._reachable_providers[provider_name] = common_ip_url
                logger.info(f"Found reachable {provider_name} via network guess: {common_ip_url}")
                return

        logger.info(f"{provider_name} was not found reachable via checked methods.")


    async def _check_single_local_url(self, provider_name: str, base_url: str, source_description: str) -> bool:
        """ Checks reachability for a single local provider URL. """
        # If checking the proxy itself, just check for 200 OK on root path.
        # Otherwise, use provider-specific health endpoint and content check.
        if source_description == "proxy":
            health_endpoint = "/" # Proxy's root endpoint
            content_check_required = False
        else:
            health_endpoint = "/" if provider_name == "ollama" else "/health"
            content_check_required = True

        full_check_url = f"{base_url}{health_endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(full_check_url, timeout=5) as response:
                    if response.status == 200:
                        # If it's the proxy check, 200 OK is enough
                        if not content_check_required:
                            logger.debug(f"Successfully reached proxy via {source_description} at: {base_url}"); return True

                        # For direct checks, validate content
                        text_content = await response.text()
                        is_valid = False
                        if provider_name == "ollama" and "Ollama is running" in text_content: is_valid = True
                        elif provider_name == "litellm":
                            if "healthy" in text_content.lower(): is_valid = True
                            else:
                                try:
                                    # Try parsing as JSON as a fallback check for some LiteLLM versions
                                    json_resp = await response.json(content_type=None)
                                    if isinstance(json_resp, dict): is_valid = True
                                except (json.JSONDecodeError, aiohttp.ContentTypeError, aiohttp.ClientResponseError): is_valid = False

                        if is_valid:
                            logger.debug(f"Successfully reached {provider_name} via {source_description} at: {base_url} (Content Validated)"); return True
                        else:
                            logger.debug(f"{provider_name} found at {base_url} ({source_description}) but health check response content was unexpected."); return False
                    else:
                        logger.debug(f"Failed to reach {provider_name} via {source_description} at {full_check_url}. Status: {response.status}"); return False
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
            logger.debug(f"{provider_name} not reachable via {source_description} at {base_url}."); return False
        except Exception as e:
            logger.error(f"Error checking reachability for {provider_name} via {source_description} at {base_url}: {e}", exc_info=False); return False

    async def _discover_openrouter_models(self):
        """ Fetches models using the confirmed reachable URL and an API key. """
        logger.debug("Discovering OpenRouter models...")
        provider_url = self._reachable_providers.get("openrouter")
        if not provider_url: logger.warning("Skipping OpenRouter model discovery: provider not marked as reachable."); return

        # --- Use the first available key from settings for discovery ---
        openrouter_keys = self.settings.PROVIDER_API_KEYS.get("openrouter")
        if not openrouter_keys:
            logger.error("Cannot discover OpenRouter models: No API key found in settings.")
            # Remove from reachable if discovery fails due to missing key
            self._reachable_providers.pop("openrouter", None)
            return
        discovery_key = openrouter_keys[0] # Use the first key for discovery
        # --- End Key Selection ---

        models_url = f"{provider_url}/models"
        headers = {"Authorization": f"Bearer {discovery_key}"} # Add Authorization header

        try:
            # Pass headers to ClientSession
            async with aiohttp.ClientSession(headers=headers) as session:
                logger.debug(f"Requesting OpenRouter models from {models_url} using key ending '...{discovery_key[-4:]}'")
                async with session.get(models_url, timeout=20) as response:
                    if response.status == 200:
                        data = await response.json(); models_data = data.get("data", [])
                        if models_data:
                            self._raw_models["openrouter"] = [ModelInfo(id=m.get("id"), name=m.get("name"), description=m.get("description"), provider="openrouter") for m in models_data if m.get("id")]
                            logger.info(f"Discovered {len(self._raw_models['openrouter'])} models from OpenRouter.")
                        else: logger.warning("OpenRouter models endpoint returned empty data list.")
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to fetch OpenRouter models from {models_url}. Status: {response.status}, Response: {error_text[:200]}")
                        # If discovery fails, remove from reachable to prevent usage attempts
                        self._reachable_providers.pop("openrouter", None)
                        logger.warning("Removed OpenRouter from reachable providers due to model discovery failure.")
        except Exception as e:
             logger.error(f"Error fetching OpenRouter models from {models_url}: {e}", exc_info=True)
             # If discovery fails, remove from reachable
             self._reachable_providers.pop("openrouter", None)
             logger.warning("Removed OpenRouter from reachable providers due to exception during model discovery.")

    async def _discover_ollama_models(self):
        """ Fetches models using the DIRECT Ollama URL, bypassing the proxy for this specific request. """
        logger.debug("Discovering Ollama models (using direct connection)...")

        # Determine the *direct* URL, ignoring the proxy setting for this discovery step
        direct_ollama_url = self.settings.OLLAMA_BASE_URL or f"http://localhost:{DEFAULT_OLLAMA_PORT}"
        direct_ollama_url = direct_ollama_url.rstrip('/')
        logger.debug(f"Using direct URL for Ollama model discovery: {direct_ollama_url}")

        # Check if Ollama provider was marked as reachable (even if via proxy) - if not, skip discovery
        if "ollama" not in self._reachable_providers:
             logger.warning("Skipping Ollama model discovery: provider not marked as reachable (proxy or direct check failed earlier).")
             return

        tags_endpoint = f"{direct_ollama_url}/api/tags"
        try:
            # Use a new session for the direct request
            async with aiohttp.ClientSession() as session:
                async with session.get(tags_endpoint, timeout=10) as response:
                    if response.status == 200:
                         data = await response.json(); models_data = data.get("models", [])
                         if models_data:
                              self._raw_models["ollama"] = [ModelInfo(id=m.get("name"), name=m.get("name"), provider="ollama") for m in models_data if m.get("name")]
                              # Corrected logging statement to use direct_ollama_url
                              logger.info(f"Discovered {len(self._raw_models['ollama'])} models from Ollama at {direct_ollama_url}: {[m['id'] for m in self._raw_models['ollama']]}.")
                         else:
                              # Corrected logging statement to use direct_ollama_url
                              logger.info(f"Ollama /api/tags endpoint at {direct_ollama_url} returned empty models list.")
                    # Corrected indentation for the else block
                    else:
                        error_text = await response.text(); logger.error(f"Failed to fetch Ollama models from {tags_endpoint}. Status: {response.status}, Response: {error_text[:200]}")
                        self._reachable_providers.pop("ollama", None); logger.warning("Removed Ollama from reachable providers due to model discovery failure.")
        except Exception as e:
            logger.error(f"Error fetching Ollama models from {tags_endpoint}: {e}", exc_info=True)
            self._reachable_providers.pop("ollama", None); logger.warning("Removed Ollama from reachable providers due to exception during model discovery.")


    async def _discover_litellm_models(self):
        """ Fetches models using the confirmed reachable URL. """
        # (Logic remains the same as previous version)
        logger.debug("Discovering LiteLLM models...")
        litellm_url = self._reachable_providers.get("litellm")
        if not litellm_url: logger.warning("Skipping LiteLLM model discovery: provider not reachable."); return
        models_endpoint = f"{litellm_url}/models"
        headers = {}
        # Use the key from settings if available (assuming LiteLLM might need it)
        litellm_keys = self.settings.PROVIDER_API_KEYS.get("litellm")
        if litellm_keys: headers["Authorization"] = f"Bearer {litellm_keys[0]}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(models_endpoint, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json(); models_data = data.get("data", [])
                        if models_data:
                            self._raw_models["litellm"] = [ModelInfo(id=m.get("id"), name=m.get("id"), provider="litellm") for m in models_data if m.get("id")]
                            logger.info(f"Discovered {len(self._raw_models['litellm'])} models from LiteLLM at {litellm_url}: {[m['id'] for m in self._raw_models['litellm']]}.")
                        else: logger.warning(f"LiteLLM models endpoint ({models_endpoint}) returned empty data list.")
                    else:
                        error_text = await response.text(); logger.error(f"Failed to fetch LiteLLM models from {models_endpoint}. Status: {response.status}, Response: {error_text[:200]}")
                        self._reachable_providers.pop("litellm", None); logger.warning("Removed LiteLLM from reachable providers due to model discovery failure.")
        except Exception as e:
            logger.error(f"Error fetching LiteLLM models from {models_endpoint}: {e}", exc_info=True)
            self._reachable_providers.pop("litellm", None); logger.warning("Removed LiteLLM from reachable providers due to exception during model discovery.")


    async def _discover_openai_models(self):
        """ Manually adds common OpenAI models if the provider is potentially reachable (key exists). """
        # (Logic remains the same as previous version)
        logger.debug("Checking for configured OpenAI...")
        if "openai" not in self._reachable_providers:
            logger.debug("Skipping OpenAI model adding: provider not marked as reachable.")
            return
        common_openai = ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
        if "openai" not in self._raw_models: self._raw_models["openai"] = []
        added_count = 0
        for model_name in common_openai:
             if not any(existing_m.get('id') == model_name for existing_m in self._raw_models["openai"]):
                 self._raw_models["openai"].append(ModelInfo(id=model_name, name=model_name, provider="openai"))
                 added_count += 1
        if added_count > 0: logger.info(f"Manually added {added_count} common models for potentially reachable OpenAI provider: {common_openai}")
        else: logger.debug("Common OpenAI models already present or none to add.")


    def _apply_filters(self):
        """ Filters raw models based on reachability and tier using self._model_tier. """
        # (Logic remains the same as previous version)
        self.available_models = {provider: [] for provider in self._reachable_providers}
        logger.debug(f"Applying filters. Tier='{self._model_tier}'. Reachable providers: {list(self._reachable_providers.keys())}")
        for provider, models in self._raw_models.items():
            if provider not in self._reachable_providers: continue
            if provider in ["ollama", "litellm"]:
                 self.available_models[provider] = models
                 logger.debug(f"Included all {len(models)} discovered models for reachable local provider '{provider}'.")
            elif provider in ["openrouter", "openai"]:
                filtered_list = []
                for model in models:
                    model_id = model.get("id", ""); is_free = ":free" in model_id.lower() if provider == "openrouter" else False
                    if self._model_tier == "FREE":
                        if is_free: filtered_list.append(model)
                    elif self._model_tier == "ALL":
                        filtered_list.append(model)
                self.available_models[provider] = filtered_list
                logger.debug(f"Included {len(filtered_list)} models for reachable remote provider '{provider}' after tier filtering.")
        self.available_models = {p: m for p, m in self.available_models.items() if m}


    # --- Getter Methods (Remain the same) ---
    def get_available_models_list(self, provider: Optional[str] = None) -> List[str]:
        model_ids = []
        provider_order = ["ollama", "litellm", "openrouter", "openai"]
        processed_providers = set()
        for p in provider_order:
            if (provider is None or p == provider) and p in self.available_models:
                model_ids.extend([m.get("id", "unknown") for m in self.available_models[p]])
                processed_providers.add(p)
        if provider is None:
            for p in self.available_models:
                if p not in processed_providers:
                     model_ids.extend([m.get("id", "unknown") for m in self.available_models[p]])
        return sorted(list(set(model_ids)))

    def get_available_models_dict(self) -> Dict[str, List[ModelInfo]]:
        return copy.deepcopy(self.available_models)

    def find_provider_for_model(self, model_id: str) -> Optional[str]:
        if '/' in model_id:
            provider_prefix, model_suffix = model_id.split('/', 1)
            if provider_prefix in self.available_models:
                 if any(m.get("id") == model_suffix for m in self.available_models[provider_prefix]):
                     return provider_prefix
        provider_order = ["ollama", "litellm", "openrouter", "openai"]
        processed_providers = set()
        for provider_name in provider_order:
            if provider_name in self.available_models:
                 if any(m.get("id") == model_id for m in self.available_models[provider_name]): return provider_name
                 if '/' in model_id:
                      provider_prefix, model_suffix = model_id.split('/', 1)
                      if provider_name == provider_prefix and any(m.get("id") == model_suffix for m in self.available_models[provider_name]): return provider_name
                 processed_providers.add(provider_name)
        for provider_name in self.available_models:
             if provider_name not in processed_providers:
                 if any(m.get("id") == model_id for m in self.available_models[provider_name]): return provider_name
        return None

    def get_formatted_available_models(self) -> str:
        if not self.available_models: return "Model Availability: No models discovered or available after filtering."
        lines = ["**Currently Available Models (Based on config & tier):**"]; found_any = False; provider_order = ["ollama", "litellm", "openrouter", "openai"]
        try:
            processed_providers = set()
            for provider in provider_order:
                if provider in self.available_models:
                     models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                     if model_names:
                         local_tag = " (Local)" if provider in ["ollama", "litellm"] else ""
                         display_names = [f"{provider}/{name}" if local_tag else name for name in model_names]
                         lines.append(f"- **{provider}{local_tag}**: `{', '.join(display_names)}`")
                         found_any = True; processed_providers.add(provider)
            for provider in self.available_models:
                 if provider not in processed_providers:
                      models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                      if model_names: lines.append(f"- **{provider}**: `{', '.join(model_names)}`"); found_any = True
            if not found_any: return "Model Availability: No models discovered or available after filtering."
            return "\n".join(lines)
        except Exception as e: logger.error(f"Error formatting available models: {e}"); return "**Error:** Could not format available models list."

    def is_model_available(self, provider: str, model_id: str) -> bool:
        if provider not in self.available_models: return False
        return any(m.get("id") == model_id for m in self.available_models[provider])

    def get_reachable_provider_url(self, provider: str) -> Optional[str]:
        return self._reachable_providers.get(provider)

    def _log_available_models(self):
        if not self.available_models: logger.warning("No models available after discovery and filtering."); return
        logger.info("--- Available Models (from Reachable Providers) ---"); provider_order = ["ollama", "litellm", "openrouter", "openai"]; logged_providers = set()
        for provider in provider_order:
            if provider in self.available_models:
                models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                local_tag = " (Local)" if provider in ["ollama", "litellm"] else ""
                display_names = [f"{provider}/{name}" if local_tag else name for name in model_names]
                logger.info(f"  {provider}{local_tag}: {len(display_names)} models -> {display_names}")
                logged_providers.add(provider)
        for provider in self.available_models:
            if provider not in logged_providers:
                models = self.available_models[provider]; model_names = sorted([m.get("id", "?") for m in models])
                logger.info(f"  {provider}: {len(model_names)} models -> {model_names}")
        logger.info("--------------------------------------------------")
