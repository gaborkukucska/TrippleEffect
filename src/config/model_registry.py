# START OF FILE src/config/model_registry.py
import asyncio
import logging
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING, Any
import aiohttp
import json
import copy # Import copy for deepcopy in getters
import ipaddress # For naming discovered providers

# Import the new network scanner utility
from src.utils.network_utils import scan_for_local_apis

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
    from various providers. Includes local network scanning for Ollama/LiteLLM.
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
        # Stores raw models before filtering, keyed by unique provider name (e.g., ollama-local-192-168-1-10)
        self._raw_models: Dict[str, List[ModelInfo]] = {}
        # Stores models available *after* filtering and reachability checks
        self.available_models: Dict[str, List[ModelInfo]] = {}
        # Stores reachable provider info: unique_provider_name -> base_url
        self._reachable_providers: Dict[str, str] = {}
        # Use MODEL_TIER from the passed settings object
        self._model_tier: str = getattr(self.settings, 'MODEL_TIER', 'ALL').upper()
        logger.info(f"ModelRegistry initialized. MODEL_TIER='{self._model_tier}'.")

    async def _verify_and_fetch_models(self, base_url: str) -> Optional[Tuple[str, str, List[ModelInfo]]]:
        """
        Verifies a potential local API endpoint and fetches its models.
        Returns (unique_provider_name, verified_base_url, models_list) or None.
        """
        logger.debug(f"Verifying potential local API at {base_url}...")
        verified_provider_name: Optional[str] = None
        models_list: List[ModelInfo] = []

        # --- Generate unique provider name based on IP ---
        ip_suffix = "unknown-host" # Default
        try:
            # Ensure http:// or https:// prefix for splitting
            if not base_url.startswith(('http://', 'https://')):
                base_url_for_parse = f"http://{base_url}" # Assume http if missing
            else:
                base_url_for_parse = base_url

            host = base_url_for_parse.split('//')[1].split('/')[0].split(':')[0]

            if host.lower() == "localhost":
                ip_suffix = "127-0-0-1" # Use loopback IP for localhost
            else:
                # Try parsing as IP address
                ip_obj = ipaddress.ip_address(host)
                # Sanitize IP for use in name
                ip_suffix = str(ip_obj).replace('.', '-')
        except Exception as e:
            # Keep ip_suffix as "unknown-host" if parsing fails
            logger.warning(f"Could not parse IP from host '{host}' in base_url {base_url} for naming: {e}")

        # 1. Try Ollama check first (/api/tags)
        ollama_check_url = f"{base_url}/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ollama_check_url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None) # Allow flexible content types
                        models_data = data.get("models", [])
                        if isinstance(models_data, list): # Check if it looks like Ollama's response
                            unique_provider_name = f"ollama-local-{ip_suffix}"
                            # Store model ID only, provider name is the key in the dict
                            models_list = [ModelInfo(id=m.get("name")) for m in models_data if m.get("name")]
                            logger.info(f"Verified Ollama endpoint at {base_url} ({unique_provider_name}). Found {len(models_list)} models.")
                            verified_provider_name = unique_provider_name # Mark as verified
                        else:
                            logger.debug(f"Endpoint {ollama_check_url} returned 200 but response format doesn't match Ollama /api/tags.")
                    else:
                        logger.debug(f"Ollama check failed for {base_url}. Status: {response.status}")
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
            logger.debug(f"Ollama check: Connection failed or timed out for {base_url}.")
        except Exception as e:
            logger.warning(f"Error during Ollama check for {base_url}: {e}", exc_info=False)

        # 2. If not verified as Ollama, try OpenAI standard check (/v1/models or /models)
        if not verified_provider_name:
            openai_check_urls = [f"{base_url}/v1/models", f"{base_url}/models"]
            for check_url in openai_check_urls:
                logger.debug(f"Trying OpenAI compatible check: {check_url}")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(check_url, timeout=5) as response:
                            if response.status == 200:
                                data = await response.json(content_type=None) # Allow flexible content types
                                models_data = data.get("data", []) # Standard OpenAI format
                                # Check if it's a list and items have an 'id'
                                if isinstance(models_data, list) and models_data and isinstance(models_data[0], dict) and "id" in models_data[0]:
                                    # Use a generic name like litellm-local-<ip> or similar
                                    unique_provider_name = f"litellm-local-{ip_suffix}" # Assume LiteLLM or similar
                                    # Store model ID only
                                    models_list = [ModelInfo(id=m.get("id")) for m in models_data if m.get("id")]
                                    logger.info(f"Verified OpenAI compatible endpoint at {base_url} ({unique_provider_name}). Found {len(models_list)} models.")
                                    verified_provider_name = unique_provider_name # Mark as verified
                                    break # Stop checking OpenAI endpoints if one works
                                else:
                                    logger.debug(f"Endpoint {check_url} returned 200 but response format doesn't match OpenAI /models.")
                            else:
                                logger.debug(f"OpenAI compatible check failed for {check_url}. Status: {response.status}")
                except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
                     logger.debug(f"OpenAI compatible check: Connection failed or timed out for {check_url}.")
                except Exception as e:
                     logger.warning(f"Error during OpenAI compatible check for {check_url}: {e}", exc_info=False)
                # If verified, break the inner loop
                if verified_provider_name:
                    break

        if verified_provider_name and models_list:
            # Add provider name to each model info dict for consistency if needed later
            for model_info in models_list:
                model_info['provider'] = verified_provider_name
            return verified_provider_name, base_url, models_list
        else:
            logger.debug(f"Could not verify {base_url} as either Ollama or OpenAI compatible.")
            return None


    async def discover_models_and_providers(self):
        """ Coordinates provider reachability checks and model discovery, including local network scan. """
        self._reachable_providers.clear()
        self._raw_models = {} # Reset raw models completely
        logger.info("Starting provider reachability checks and discovery...")

        # --- 1. Discover Remote Providers (Based on .env config) ---
        remote_discovery_tasks = []
        if self.settings.is_provider_configured("openrouter"):
            or_base = self.settings.OPENROUTER_BASE_URL or OPENROUTER_MODELS_URL.rsplit('/', 1)[0]
            self._reachable_providers["openrouter"] = or_base.rstrip('/')
            logger.debug(f"Adding OpenRouter for discovery (API key(s) present). Base: {self._reachable_providers['openrouter']}")
            remote_discovery_tasks.append(self._discover_openrouter_models())
        else:
            logger.debug("Skipping OpenRouter discovery: No API key found in settings.")

        if self.settings.is_provider_configured("openai"):
            oa_base = self.settings.OPENAI_BASE_URL or "https://api.openai.com/v1"
            self._reachable_providers["openai"] = oa_base.rstrip('/')
            logger.debug(f"Adding OpenAI for discovery (API key(s) present). Base: {self._reachable_providers['openai']}")
            remote_discovery_tasks.append(self._discover_openai_models())
        else:
            logger.debug("Skipping OpenAI discovery: No API key found in settings.")
        # Add checks/discovery tasks for other remote providers similarly...

        # --- 2. Discover Local Providers (Network Scan + Configured) ---
        discovered_local_urls = set()

        # Add explicitly configured local URLs first (if not using proxy for Ollama)
        if not (self.settings.USE_OLLAMA_PROXY and "ollama" in self._reachable_providers): # Don't add direct if proxy is reachable
             if self.settings.OLLAMA_BASE_URL:
                 discovered_local_urls.add(self.settings.OLLAMA_BASE_URL.rstrip('/'))
        if self.settings.LITELLM_BASE_URL:
            discovered_local_urls.add(self.settings.LITELLM_BASE_URL.rstrip('/'))

        # Perform network scan if enabled
        if self.settings.LOCAL_API_SCAN_ENABLED:
            try:
                logger.info("Starting local network scan...")
                scanned_urls = await scan_for_local_apis(
                    ports=self.settings.LOCAL_API_SCAN_PORTS,
                    subnet_config=self.settings.LOCAL_API_SCAN_SUBNET,
                    timeout=self.settings.LOCAL_API_SCAN_TIMEOUT
                )
                discovered_local_urls.update(scanned_urls)
                logger.info(f"Local network scan completed. Found {len(scanned_urls)} potential endpoints.")
            except Exception as scan_err:
                logger.error(f"Error during local network scan: {scan_err}", exc_info=True)

        # Add localhost defaults if not already found and not explicitly configured elsewhere
        localhost_ollama = f"http://localhost:{DEFAULT_OLLAMA_PORT}"
        localhost_litellm = f"http://localhost:{DEFAULT_LITELLM_PORT}"
        if not self.settings.OLLAMA_BASE_URL and localhost_ollama not in discovered_local_urls:
            discovered_local_urls.add(localhost_ollama)
        if not self.settings.LITELLM_BASE_URL and localhost_litellm not in discovered_local_urls:
            discovered_local_urls.add(localhost_litellm)

        # Verify all potential local URLs (scanned + configured + defaults)
        if discovered_local_urls:
            logger.info(f"Verifying {len(discovered_local_urls)} potential local endpoints...")
            verification_tasks = [self._verify_and_fetch_models(url) for url in discovered_local_urls]
            verification_results = await asyncio.gather(*verification_tasks, return_exceptions=True)

            processed_urls = set() # Track URLs that have been successfully verified
            for result in verification_results:
                if isinstance(result, tuple) and len(result) == 3:
                    provider_name, verified_url, models = result
                    if provider_name and verified_url and models:
                        # Ensure provider name is unique if multiple instances found
                        # (e.g., ollama-local-127-0-0-1, ollama-local-192-168-1-10)
                        base_name = provider_name
                        counter = 1
                        unique_provider_name = provider_name
                        while unique_provider_name in self._reachable_providers:
                            unique_provider_name = f"{base_name}-{counter}"
                            counter += 1

                        self._reachable_providers[unique_provider_name] = verified_url
                        # Add provider name to each model info dict
                        for m in models: m['provider'] = unique_provider_name
                        self._raw_models[unique_provider_name] = models # Store raw models under unique name
                        processed_urls.add(verified_url)
                        logger.info(f"Successfully registered discovered local provider '{unique_provider_name}' at {verified_url} with {len(models)} models.")
                    else:
                         logger.debug(f"Verification task returned invalid data: {result}")
                elif isinstance(result, Exception):
                    logger.error(f"Error during local API verification task: {result}", exc_info=result)
                elif result is None:
                    pass # Verification failed, already logged in _verify_and_fetch_models
                else:
                     logger.warning(f"Unexpected result type from verification task: {type(result)}")

        # --- 3. Handle Ollama Proxy Separately (if enabled and not already covered by scan) ---
        if self.settings.USE_OLLAMA_PROXY:
            proxy_url = f"http://localhost:{self.settings.OLLAMA_PROXY_PORT}"
            if proxy_url not in processed_urls: # Only check if not already verified by scan/config
                logger.debug(f"Ollama proxy enabled. Explicitly checking proxy URL: {proxy_url}")
                # Use _verify_and_fetch_models to check proxy and get its models
                proxy_result = await self._verify_and_fetch_models(proxy_url)
                if isinstance(proxy_result, tuple) and len(proxy_result) == 3:
                    provider_name, verified_url, models = proxy_result
                    if provider_name and verified_url and models:
                         # Use a distinct name for the proxy provider
                         proxy_provider_name = "ollama-proxy"
                         self._reachable_providers[proxy_provider_name] = verified_url
                         for m in models: m['provider'] = proxy_provider_name
                         self._raw_models[proxy_provider_name] = models
                         logger.info(f"Successfully registered Ollama Proxy '{proxy_provider_name}' at {verified_url} with {len(models)} models.")
                elif isinstance(proxy_result, Exception):
                     logger.error(f"Error during Ollama Proxy verification task: {proxy_result}", exc_info=proxy_result)
                else:
                     logger.warning(f"Ollama proxy enabled in settings but verification failed at {proxy_url}. Proxy will be unavailable.")


        # --- 4. Run Remote Discovery Tasks Concurrently ---
        if remote_discovery_tasks:
            logger.info(f"Running {len(remote_discovery_tasks)} remote discovery tasks concurrently...")
            results = await asyncio.gather(*remote_discovery_tasks, return_exceptions=True)
            # Log any errors from remote discovery tasks
            for i, result in enumerate(results):
                 if isinstance(result, Exception):
                     # Attempt to identify which task failed (might be fragile)
                     failed_task_provider = "Unknown Remote"
                     # This mapping needs to be kept in sync with the order tasks are added
                     provider_map = []
                     if self.settings.is_provider_configured("openrouter"): provider_map.append("openrouter")
                     if self.settings.is_provider_configured("openai"): provider_map.append("openai")
                     # Add other remote providers here if needed
                     if i < len(provider_map):
                         failed_task_provider = provider_map[i]

                     logger.error(f"Error during model discovery for provider '{failed_task_provider}': {result}", exc_info=result)
        else:
             logger.info("No remote discovery tasks to run.")


        # --- 5. Final Filtering and Logging ---
        if not self._reachable_providers:
             logger.warning("No providers found reachable after all checks. Model registry will be empty.")
             self.available_models = {}
             return

        self._apply_filters()
        logger.info("Provider and model discovery/filtering complete.")
        self._log_available_models()


    # --- Keep existing model discovery methods for remote providers ---
    async def _discover_openrouter_models(self):
        """ Fetches models using the confirmed reachable URL and an API key. """
        logger.debug("Discovering OpenRouter models...")
        provider_url = self._reachable_providers.get("openrouter")
        if not provider_url: logger.warning("Skipping OpenRouter model discovery: provider not marked as reachable."); return

        openrouter_keys = self.settings.PROVIDER_API_KEYS.get("openrouter")
        if not openrouter_keys:
            logger.error("Cannot discover OpenRouter models: No API key found in settings.")
            self._reachable_providers.pop("openrouter", None)
            return
        discovery_key = openrouter_keys[0]

        models_url = f"{provider_url}/models"
        headers = {"Authorization": f"Bearer {discovery_key}"}

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                logger.debug(f"Requesting OpenRouter models from {models_url} using key ending '...{discovery_key[-4:]}'")
                async with session.get(models_url, timeout=20) as response:
                    if response.status == 200:
                        data = await response.json(); models_data = data.get("data", [])
                        if models_data:
                            # Store under the canonical 'openrouter' key
                            self._raw_models["openrouter"] = [ModelInfo(id=m.get("id"), name=m.get("name"), description=m.get("description"), provider="openrouter") for m in models_data if m.get("id")]
                            logger.info(f"Discovered {len(self._raw_models['openrouter'])} models from OpenRouter.")
                        else: logger.warning("OpenRouter models endpoint returned empty data list.")
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to fetch OpenRouter models from {models_url}. Status: {response.status}, Response: {error_text[:200]}")
                        self._reachable_providers.pop("openrouter", None)
                        logger.warning("Removed OpenRouter from reachable providers due to model discovery failure.")
        except Exception as e:
             logger.error(f"Error fetching OpenRouter models from {models_url}: {e}", exc_info=True)
             self._reachable_providers.pop("openrouter", None)
             logger.warning("Removed OpenRouter from reachable providers due to exception during model discovery.")


    async def _discover_openai_models(self):
        """ Manually adds common OpenAI models if the provider is potentially reachable (key exists). """
        logger.debug("Checking for configured OpenAI...")
        if "openai" not in self._reachable_providers:
            logger.debug("Skipping OpenAI model adding: provider not marked as reachable.")
            return
        common_openai = ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
        # Ensure the key exists before adding
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
        self.available_models = {} # Start fresh
        logger.debug(f"Applying filters. Tier='{self._model_tier}'. Reachable providers: {list(self._reachable_providers.keys())}")

        for provider, models in self._raw_models.items():
            if provider not in self._reachable_providers:
                logger.warning(f"Provider '{provider}' found in raw models but not in reachable list. Skipping filtering.")
                continue # Skip if provider somehow isn't marked reachable

            # Determine if it's a local provider based on naming convention or type
            is_local = "-local-" in provider or provider == "ollama-proxy"

            if is_local:
                 # Include all models for reachable local providers
                 self.available_models[provider] = models
                 logger.debug(f"Included all {len(models)} discovered models for reachable local provider '{provider}'.")
            elif provider in ["openrouter", "openai"]: # Handle known remote providers
                filtered_list = []
                for model in models:
                    model_id = model.get("id", ""); is_free = ":free" in model_id.lower() if provider == "openrouter" else False
                    if self._model_tier == "FREE":
                        if is_free: filtered_list.append(model)
                    elif self._model_tier == "ALL":
                        filtered_list.append(model)
                if filtered_list: # Only add provider if it has models after filtering
                    self.available_models[provider] = filtered_list
                    logger.debug(f"Included {len(filtered_list)} models for reachable remote provider '{provider}' after tier filtering.")
            else:
                 # This case should ideally not happen if raw_models only contains reachable providers
                 logger.warning(f"Provider '{provider}' found in raw models but not recognized as local or known remote during filtering. Skipping.")

        # Clean up empty provider entries just in case
        self.available_models = {p: m for p, m in self.available_models.items() if m}


    # --- Getter Methods (Updated for dynamic local providers) ---
    def get_available_models_list(self, provider: Optional[str] = None) -> List[str]:
        """ Gets a list of available model IDs, optionally filtered by provider (base name or specific instance). """
        model_ids = set()
        provider_order = ["ollama", "litellm", "openrouter", "openai"] # Base types for ordering

        # Handle specific provider request
        if provider:
            # Check exact match first (e.g., 'openrouter', 'ollama-local-192-168-1-10')
            if provider in self.available_models:
                 return sorted([m.get("id", "unknown") for m in self.available_models[provider]])
            else:
                 # Check if it's a base name for local providers (e.g., 'ollama')
                 matching_providers = [p for p in self.available_models if p.startswith(f"{provider}-local-") or p == f"{provider}-proxy"]
                 if matching_providers:
                      for p_match in matching_providers:
                           model_ids.update([m.get("id", "unknown") for m in self.available_models[p_match]])
                      return sorted(list(model_ids))
                 else:
                      return [] # Provider not found

        # Get all models if no specific provider requested, maintaining some order
        processed_providers = set()
        final_model_list = []

        # Process known base types and their dynamic instances first
        for p_base in provider_order:
             # Add models from discovered local instances of this type
             matching_local = sorted([p for p in self.available_models if p.startswith(f"{p_base}-local-") or p == f"{p_base}-proxy"])
             for p_local in matching_local:
                  if p_local not in processed_providers:
                       final_model_list.extend([m.get("id", "unknown") for m in self.available_models[p_local]])
                       processed_providers.add(p_local)
             # Add models from the base remote provider if it exists (e.g., openrouter)
             if p_base in self.available_models and p_base not in processed_providers:
                  final_model_list.extend([m.get("id", "unknown") for m in self.available_models[p_base]])
                  processed_providers.add(p_base)

        # Add any remaining providers not in the standard order
        for p_rem in sorted(self.available_models.keys()):
            if p_rem not in processed_providers:
                 final_model_list.extend([m.get("id", "unknown") for m in self.available_models[p_rem]])

        # Return unique sorted list
        return sorted(list(set(final_model_list)))


    def get_available_models_dict(self) -> Dict[str, List[ModelInfo]]:
        """ Returns a deep copy of the available models dictionary. """
        return copy.deepcopy(self.available_models)

    def find_provider_for_model(self, model_id: str) -> Optional[str]:
        """ Finds the unique provider name (potentially dynamic) for a given model ID. """
        target_model_name = model_id
        provider_prefix_search = None

        # Handle prefixed names like 'ollama/llama3'
        if '/' in model_id:
            provider_prefix_search, target_model_name = model_id.split('/', 1)

        # Search all available providers
        for provider_name, models in self.available_models.items():
            # Check if the model exists under this provider
            if any(m.get("id") == target_model_name for m in models):
                # If a prefix was provided, ensure it matches the provider's base type
                if provider_prefix_search:
                    base_provider_type = provider_name.split('-local-')[0].split('-proxy')[0]
                    if base_provider_type == provider_prefix_search:
                        return provider_name
                else:
                    # No prefix provided, return the first match
                    return provider_name

        # If prefix search failed, maybe the prefix itself is the provider name (e.g., 'openrouter')
        if provider_prefix_search and provider_prefix_search in self.available_models:
             if any(m.get("id") == target_model_name for m in self.available_models[provider_prefix_search]):
                  return provider_prefix_search

        logger.debug(f"Could not find provider for model ID '{model_id}' (target name: '{target_model_name}')")
        return None


    def get_formatted_available_models(self) -> str:
        """ Formats the available models for display, grouping local instances. """
        if not self.available_models: return "Model Availability: No models discovered or available after filtering."
        lines = ["**Currently Available Models (Based on config & tier):**"]; found_any = False
        # Group by base type for better display
        provider_groups: Dict[str, List[Tuple[str, List[ModelInfo]]]] = {
            "ollama": [], "litellm": [], "openrouter": [], "openai": [], "other": []
        }
        for provider, models in self.available_models.items():
            base_type = "other"
            if provider.startswith("ollama"): base_type = "ollama"
            elif provider.startswith("litellm"): base_type = "litellm"
            elif provider == "openrouter": base_type = "openrouter"
            elif provider == "openai": base_type = "openai"
            provider_groups[base_type].append((provider, models))

        try:
            for base_type in ["ollama", "litellm", "openrouter", "openai", "other"]:
                group = sorted(provider_groups[base_type], key=lambda item: item[0]) # Sort instances alphabetically
                if not group: continue

                for provider, models in group:
                     model_names = sorted([m.get("id", "?") for m in models])
                     if model_names:
                         # Determine tag (Local Discovered, Local Proxy, Remote)
                         tag = ""
                         if provider == "ollama-proxy": tag = " (Local Proxy)"
                         elif "-local-" in provider: tag = " (Local Discovered)"
                         elif provider in ["openrouter", "openai"]: tag = " (Remote)" # Add tag for remote

                         # Add prefix for local models for clarity
                         prefix = ""
                         if tag: # Add prefix only if it's local or proxy
                             prefix = provider.split('-local-')[0].split('-proxy')[0] + "/"

                         display_names = [f"{prefix}{name}" for name in model_names]

                         lines.append(f"- **{provider}{tag}**: `{', '.join(display_names)}`")
                         found_any = True

            if not found_any: return "Model Availability: No models discovered or available after filtering."
            return "\n".join(lines)
        except Exception as e: logger.error(f"Error formatting available models: {e}"); return "**Error:** Could not format available models list."

    def is_model_available(self, provider: str, model_id: str) -> bool:
        """ Checks if a specific model ID is available under a given provider name (potentially dynamic). """
        if provider not in self.available_models: return False
        # Model IDs in the registry do not have prefixes
        return any(m.get("id") == model_id for m in self.available_models[provider])

    def get_reachable_provider_url(self, provider: str) -> Optional[str]:
        """ Gets the base URL for a reachable provider (potentially dynamic). """
        return self._reachable_providers.get(provider)

    def _log_available_models(self):
        """ Logs the available models, grouped by provider type. """
        if not self.available_models: logger.warning("No models available after discovery and filtering."); return
        logger.info("--- Available Models (from Reachable Providers) ---")
        # Group by base type for better display
        provider_groups: Dict[str, List[Tuple[str, List[ModelInfo]]]] = {
            "ollama": [], "litellm": [], "openrouter": [], "openai": [], "other": []
        }
        for provider, models in self.available_models.items():
            base_type = "other"
            if provider.startswith("ollama"): base_type = "ollama"
            elif provider.startswith("litellm"): base_type = "litellm"
            elif provider == "openrouter": base_type = "openrouter"
            elif provider == "openai": base_type = "openai"
            provider_groups[base_type].append((provider, models))

        for base_type in ["ollama", "litellm", "openrouter", "openai", "other"]:
            group = sorted(provider_groups[base_type], key=lambda item: item[0]) # Sort instances alphabetically
            if not group: continue
            for provider, models in group:
                model_names = sorted([m.get("id", "?") for m in models])
                # Determine tag (Local Discovered, Local Proxy, Remote)
                tag = ""
                if provider == "ollama-proxy": tag = " (Local Proxy)"
                elif "-local-" in provider: tag = " (Local Discovered)"
                elif provider in ["openrouter", "openai"]: tag = " (Remote)"

                # Add prefix for local models for clarity
                prefix = ""
                if tag: # Add prefix only if it's local or proxy
                    prefix = provider.split('-local-')[0].split('-proxy')[0] + "/"

                display_names = [f"{prefix}{name}" for name in model_names]
                logger.info(f"  {provider}{tag}: {len(display_names)} models -> {display_names}")

        logger.info("--------------------------------------------------")

# END OF FILE src/config/model_registry.py
