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
DEFAULT_LITELLM_PORT = 8000 # Adjust if your LiteLLM default is different

# --- NEW: TypedDict for ModelInfo ---
from typing import TypedDict

class ModelInfo(TypedDict, total=False):
    """Structure for storing model information."""
    id: str
    name: Optional[str]
    description: Optional[str]
    provider: str
    num_parameters: Optional[int] # New field for parameter count
# --- END NEW ---

class ModelRegistry:
    """
    Handles discovery, filtering, and storage of available LLM models
    from various providers. Includes local network scanning for Ollama/LiteLLM.
    Applies filtering based on the MODEL_TIER setting (LOCAL, FREE, ALL).
    Accepts settings object for configuration access.
    """

    def __init__(self, settings_obj: 'Settings'):
        """
        Initializes the registry.

        Args:
            settings_obj (Settings): The loaded application settings instance.
        """
        self.settings = settings_obj # Store settings instance
        self._raw_models: Dict[str, List[ModelInfo]] = {}
        self.available_models: Dict[str, List[ModelInfo]] = {}
        self._reachable_providers: Dict[str, str] = {}
        self._verified_local_canonical_services: Set[Tuple[str, int]] = set()
        self._model_tier: str = getattr(self.settings, 'MODEL_TIER', 'FREE').upper()
        logger.info(f"ModelRegistry initialized. Effective MODEL_TIER='{self._model_tier}'.")

    def _parse_ollama_parameter_string_to_int(self, param_str: str) -> Optional[int]:
        """
        Parses Ollama's parameter string (e.g., "7B", "13B", "3.5M", "180K") into an integer.
        Returns None if parsing fails.
        """
        if not param_str or not isinstance(param_str, str):
            return None
        
        param_str = param_str.upper().strip()
        multiplier = 1
        
        if param_str.endswith('K'):
            multiplier = 1_000
            param_str = param_str[:-1]
        elif param_str.endswith('M'):
            multiplier = 1_000_000
            param_str = param_str[:-1]
        elif param_str.endswith('B') or param_str.endswith('G'): # G for some models, e.g. Llama variants
            multiplier = 1_000_000_000
            param_str = param_str[:-1]
            
        try:
            # Allow for float values like "3.5B"
            num_value = float(param_str)
            return int(num_value * multiplier)
        except ValueError:
            logger.warning(f"Could not parse parameter string '{param_str}' to number.")
            return None

    async def _verify_and_fetch_models(self, base_url: str) -> Optional[Tuple[str, str, List[ModelInfo]]]:
        """
        Verifies a potential local API endpoint and fetches its models.
        For Ollama, it also attempts to get parameter counts for each model.
        Returns (unique_provider_name, verified_base_url, models_list) or None.
        """
        logger.debug(f"Verifying potential local API at {base_url}...")
        verified_provider_name: Optional[str] = None
        raw_models_list: List[Dict[str, Any]] = [] # Store raw model data from /api/tags
        final_models_list: List[ModelInfo] = []

        # --- Generate unique provider name based on IP ---
        ip_suffix = "unknown-host" # Default
        try:
            if not base_url.startswith(('http://', 'https://')):
                base_url_for_parse = f"http://{base_url}"
            else:
                base_url_for_parse = base_url
            host = base_url_for_parse.split('//')[1].split('/')[0].split(':')[0]
            if host.lower() == "localhost": ip_suffix = "127-0-0-1"
            else: ip_obj = ipaddress.ip_address(host); ip_suffix = str(ip_obj).replace('.', '-')
        except Exception as e: logger.warning(f"Could not parse IP from host '{host}' in base_url {base_url} for naming: {e}")

        # 1. Try Ollama check first (/api/tags)
        ollama_check_url = f"{base_url}/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ollama_check_url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None) # Ollama might not set content_type correctly
                        raw_models_data = data.get("models", [])
                        if isinstance(raw_models_data, list):
                            unique_provider_name = f"ollama-local-{ip_suffix}"
                            # Store raw data to fetch details later
                            raw_models_list = [m for m in raw_models_data if m.get("name")] 
                            logger.info(f"Verified Ollama endpoint at {base_url} ({unique_provider_name}). Found {len(raw_models_list)} models from /api/tags.")
                            verified_provider_name = unique_provider_name # Mark as Ollama for detailed fetching

                            # Now fetch details for each model using /api/show
                            for raw_model_data in raw_models_list:
                                model_name = raw_model_data.get("name")
                                if not model_name: continue

                                model_info_dict: ModelInfo = {"id": model_name, "provider": unique_provider_name, "name": model_name}
                                try:
                                    show_url = f"{base_url}/api/show"
                                    async with session.post(show_url, json={"name": model_name}, timeout=10) as show_response: # Use POST as per Ollama docs
                                        if show_response.status == 200:
                                            details_data = await show_response.json(content_type=None)
                                            param_str = details_data.get("details", {}).get("parameter_size")
                                            if param_str:
                                                num_params = self._parse_ollama_parameter_string_to_int(param_str)
                                                if num_params is not None:
                                                    model_info_dict["num_parameters"] = num_params
                                                    logger.debug(f"Ollama model '{model_name}': parameters '{param_str}' -> {num_params}")
                                                else:
                                                    logger.debug(f"Ollama model '{model_name}': could not parse parameter string '{param_str}'.")
                                            else:
                                                logger.debug(f"Ollama model '{model_name}': 'parameter_size' not found in /api/show details.")
                                        else:
                                            logger.warning(f"Failed to get details for Ollama model '{model_name}' from {show_url}. Status: {show_response.status}")
                                except Exception as detail_err:
                                    logger.warning(f"Error fetching details for Ollama model '{model_name}': {detail_err}", exc_info=False)
                                final_models_list.append(model_info_dict)
                            if not final_models_list and raw_models_list: # If detail fetching failed for all but tags worked
                                logger.warning(f"Ollama endpoint {base_url}: Failed to fetch details for any models, using names from /api/tags only.")
                                final_models_list = [ModelInfo(id=m.get("name"), provider=unique_provider_name) for m in raw_models_list if m.get("name")]

                        else: logger.debug(f"Endpoint {ollama_check_url} returned 200 but response format doesn't match Ollama /api/tags.")
                    else: logger.debug(f"Ollama check failed for {base_url}. Status: {response.status}")
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError): logger.debug(f"Ollama check: Connection failed or timed out for {base_url}.")
        except Exception as e: logger.warning(f"Error during Ollama check for {base_url}: {e}", exc_info=False)

        # 2. If not verified as Ollama, try OpenAI standard check (/v1/models or /models)
        if not verified_provider_name: # Only proceed if not already identified as Ollama
            openai_check_urls = [f"{base_url}/v1/models", f"{base_url}/models"]
            for check_url in openai_check_urls:
                logger.debug(f"Trying OpenAI compatible check: {check_url}")
                try:
                    async with aiohttp.ClientSession() as session: # New session for LiteLLM check
                        async with session.get(check_url, timeout=5) as response:
                            if response.status == 200:
                                data = await response.json(content_type=None)
                                models_data_openai = data.get("data", [])
                                if isinstance(models_data_openai, list) and models_data_openai and isinstance(models_data_openai[0], dict) and "id" in models_data_openai[0]:
                                    unique_provider_name = f"litellm-local-{ip_suffix}" # Assume LiteLLM if OpenAI compatible
                                    # LiteLLM typically doesn't provide param counts via this endpoint
                                    final_models_list = [ModelInfo(id=m.get("id"), provider=unique_provider_name) for m in models_data_openai if m.get("id")]
                                    logger.info(f"Verified OpenAI compatible endpoint (assumed LiteLLM) at {base_url} ({unique_provider_name}). Found {len(final_models_list)} models.")
                                    verified_provider_name = unique_provider_name
                                    break 
                                else: logger.debug(f"Endpoint {check_url} returned 200 but response format doesn't match OpenAI /models.")
                            else: logger.debug(f"OpenAI compatible check failed for {check_url}. Status: {response.status}")
                except (aiohttp.ClientConnectorError, asyncio.TimeoutError): logger.debug(f"OpenAI compatible check: Connection failed or timed out for {check_url}.")
                except Exception as e: logger.warning(f"Error during OpenAI compatible check for {check_url}: {e}", exc_info=False)
                if verified_provider_name: break # Exit loop if verified

        if verified_provider_name and final_models_list:
            # Provider already set in model_info_dict during creation
            return verified_provider_name, base_url, final_models_list
        else:
            logger.debug(f"Could not verify {base_url} as either Ollama or OpenAI compatible.")
            return None


    async def discover_models_and_providers(self):
        """ Coordinates provider reachability checks and model discovery, respecting MODEL_TIER setting. """
        current_tier = self.settings.MODEL_TIER
        logger.info(f"Model Registry: Starting discovery. Effective MODEL_TIER = {current_tier}")

        self._reachable_providers.clear()
        self._raw_models = {}
        self._verified_local_canonical_services.clear()

        # --- 1. Discover Remote Providers (Conditional based on Tier) ---
        remote_discovery_tasks = []
        if current_tier in ["FREE", "ALL"]:
            logger.info(f"MODEL_TIER is {current_tier}, enabling remote provider discovery.")
            if self.settings.is_provider_configured("openrouter"):
                # ** FIX: Get base URL from settings object, which has default **
                or_base = self.settings.OPENROUTER_BASE_URL
                if or_base: # Ensure it's not None
                    self._reachable_providers["openrouter"] = or_base.rstrip('/')
                    logger.debug(f"Adding OpenRouter for discovery. Base: {self._reachable_providers['openrouter']}")
                    remote_discovery_tasks.append(self._discover_openrouter_models())
                else:
                    logger.warning("OpenRouter base URL is None in settings, cannot add for discovery.")
            else: logger.debug("Skipping OpenRouter discovery: Provider not configured in settings.")

            if self.settings.is_provider_configured("openai"):
                # ** FIX: Get base URL from settings object **
                oa_base = self.settings.OPENAI_BASE_URL or "https://api.openai.com/v1" # Fallback needed if None in settings
                self._reachable_providers["openai"] = oa_base.rstrip('/')
                logger.debug(f"Adding OpenAI for discovery. Base: {self._reachable_providers['openai']}")
                remote_discovery_tasks.append(self._discover_openai_models())
            else: logger.debug("Skipping OpenAI discovery: Provider not configured in settings.")
        else: # current_tier == "LOCAL"
             logger.info("MODEL_TIER=LOCAL, skipping remote provider discovery.")

        # --- 2. Discover Local Providers (Runs for all tiers) ---
        # (No changes needed in local discovery itself)
        urls_to_verify = set()
        processed_urls = set()
        logger.info("Adding default localhost endpoints for verification...")
        urls_to_verify.add(f"http://localhost:{DEFAULT_OLLAMA_PORT}")
        urls_to_verify.add(f"http://127.0.0.1:{DEFAULT_OLLAMA_PORT}")
        urls_to_verify.add(f"http://localhost:{DEFAULT_LITELLM_PORT}")
        urls_to_verify.add(f"http://127.0.0.1:{DEFAULT_LITELLM_PORT}")

        network_scan_urls = set()
        if self.settings.LOCAL_API_SCAN_ENABLED:
            logger.info("Starting network scan for local APIs...")
            try:
                scanned_urls = await scan_for_local_apis(ports=self.settings.LOCAL_API_SCAN_PORTS, timeout=self.settings.LOCAL_API_SCAN_TIMEOUT)
                network_scan_urls = {url for url in scanned_urls if not ("localhost" in url or "127.0.0.1" in url)}
                logger.info(f"Network scan completed. Found {len(network_scan_urls)} potential non-localhost endpoints.")
                urls_to_verify.update(network_scan_urls)
            except Exception as scan_err: logger.error(f"Error during local network scan: {scan_err}", exc_info=True)
        else: logger.info("Local network scan is disabled in settings.")

        logger.info(f"ModelRegistry: Full list of URLs to verify for local providers: {list(urls_to_verify)}")

        if urls_to_verify:
            logger.info(f"Verifying {len(urls_to_verify)} potential local endpoints...")
            urls_to_actually_verify = [url for url in urls_to_verify if url not in processed_urls]
            verification_tasks = [self._verify_and_fetch_models(url) for url in urls_to_actually_verify]
            verification_results = await asyncio.gather(*verification_tasks, return_exceptions=True)
            logger.info(f"ModelRegistry: Received {len(verification_results)} results from local provider verification tasks.")

            for result in verification_results:
                if isinstance(result, tuple) and len(result) == 3:
                    provider_name, verified_url, models = result
                    if provider_name and verified_url and models:
                        try:
                            host_part = verified_url.split('//')[1].split('/')[0].split(':')[0]
                            port_part_str = verified_url.split(':')[-1].split('/')[0]
                            port_part = int(port_part_str)
                            is_loopback = host_part == "localhost" or host_part == "127.0.0.1"
                            if is_loopback:
                                service_type = "unknown"
                                if provider_name.startswith("ollama-"): service_type = "ollama"
                                elif provider_name.startswith("litellm-"): service_type = "litellm"
                                canonical_service_id = (service_type, port_part)
                                if canonical_service_id in self._verified_local_canonical_services: logger.debug(f"Skipping registration for {verified_url}: Canonical service {canonical_service_id} already registered."); processed_urls.add(verified_url); continue
                        except Exception as e: logger.warning(f"Error during duplicate check for {verified_url}: {e}."); is_loopback = False

                        base_name = provider_name; counter = 1; unique_provider_name = provider_name
                        while unique_provider_name in self._reachable_providers: unique_provider_name = f"{base_name}-{counter}"; counter += 1
                        if provider_name.startswith("ollama-local-") and is_loopback:
                            canonical_name = "ollama-local"
                            if canonical_name not in self._reachable_providers: unique_provider_name = canonical_name; logger.info(f"Assigning canonical name '{unique_provider_name}' to verified localhost Ollama at {verified_url}")
                            else: logger.warning(f"Canonical name '{canonical_name}' already assigned. Using dynamic name '{unique_provider_name}' for Ollama at {verified_url}")

                        self._reachable_providers[unique_provider_name] = verified_url
                        for m in models: m['provider'] = unique_provider_name
                        self._raw_models[unique_provider_name] = models
                        processed_urls.add(verified_url)
                        if is_loopback: self._verified_local_canonical_services.add(canonical_service_id); processed_urls.add(f"http://localhost:{port_part}"); processed_urls.add(f"http://127.0.0.1:{port_part}")
                        logger.info(f"Successfully registered discovered provider '{unique_provider_name}' at {verified_url} with {len(models)} models.")
                elif isinstance(result, Exception): logger.error(f"Error during local API verification task: {result}", exc_info=result)
                elif result is None: pass
                else: logger.warning(f"Unexpected result type from verification task: {type(result)}")
        else: logger.info("No local endpoints found or configured to verify.")

        # Check if any local providers were actually added to _reachable_providers
        local_providers_found_this_run = [p for p in self._reachable_providers if p.startswith("ollama-local") or p.startswith("litellm-local") or p == "ollama-proxy"]
        if not local_providers_found_this_run:
            logger.warning("ModelRegistry: No local LLM providers were successfully verified or registered in this discovery cycle, despite attempts.")
        else:
            logger.info(f"ModelRegistry: Successfully verified and registered the following local providers in this cycle: {local_providers_found_this_run}")

        # --- 3. Run Remote Discovery Tasks Concurrently ---
        if remote_discovery_tasks:
            logger.info(f"Running {len(remote_discovery_tasks)} remote discovery tasks concurrently...")
            results = await asyncio.gather(*remote_discovery_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                 if isinstance(result, Exception):
                     provider_map = []
                     if current_tier in ["FREE", "ALL"]:
                        if self.settings.is_provider_configured("openrouter"): provider_map.append("openrouter")
                        if self.settings.is_provider_configured("openai"): provider_map.append("openai")
                     failed_task_provider = provider_map[i] if i < len(provider_map) else "Unknown Remote"
                     logger.error(f"Error during model discovery for provider '{failed_task_provider}': {result}", exc_info=result)
        else: logger.info("No remote discovery tasks scheduled based on MODEL_TIER setting.")

        # --- 4. Final Filtering and Logging ---
        if not self._reachable_providers:
             logger.warning("No providers found reachable after all checks. Model registry will be empty.")
             self.available_models = {}; return

        self._apply_filters() # Call the refactored filtering method
        logger.info("Provider and model discovery/filtering complete.")
        self._log_available_models()

    # --- Helper for Formatting/Logging (Unchanged) ---
    def _format_model_list_output(self, include_header: bool = True) -> str:
        if not self.available_models: return "Model Availability: No models discovered or available after filtering." if include_header else "No models available."
        lines = []; found_any = False
        provider_groups: Dict[str, List[Tuple[str, List[ModelInfo]]]] = {"ollama": [], "litellm": [], "openrouter": [], "openai": [], "other": []}
        for provider, models in self.available_models.items():
            base_type = "other"
            if provider.startswith("ollama"): base_type = "ollama"
            elif provider.startswith("litellm"): base_type = "litellm"
            elif provider == "openrouter": base_type = "openrouter"
            elif provider == "openai": base_type = "openai"
            provider_groups[base_type].append((provider, models))
        try:
            for base_type in ["ollama", "litellm", "openrouter", "openai", "other"]:
                group = sorted(provider_groups[base_type], key=lambda item: item[0])
                if not group: continue
                for provider, models in group:
                     model_names = sorted([m.get("id", "?") for m in models])
                     if model_names:
                         tag = ""; prefix = ""
                         if provider == "ollama-proxy": tag = " (Local Proxy)" # Keep proxy tag if it appears
                         elif "-local-" in provider: tag = " (Local Discovered)"
                         elif provider in ["openrouter", "openai"]: tag = " (Remote)"
                         if provider.startswith("ollama") or provider.startswith("litellm"): prefix = provider.split('-local-')[0].split('-proxy')[0] + "/"
                         display_names = [f"{prefix}{name}" for name in model_names]
                         if include_header: lines.append(f"- **{provider}{tag}**: `{', '.join(display_names)}`")
                         else: lines.append(f"  {provider}{tag}: {len(display_names)} models -> {display_names}")
                         found_any = True
            if not found_any: return "Model Availability: No models discovered or available after filtering." if include_header else "No models available."
            return "\n".join(lines)
        except Exception as e: logger.error(f"Error formatting available models: {e}"); return "**Error:** Could not format available models list." if include_header else "Error formatting model list."

    # --- Remote Model Discovery Methods (Unchanged) ---
    async def _discover_openrouter_models(self):
        logger.debug("Discovering OpenRouter models...")
        provider_url = self._reachable_providers.get("openrouter")
        if not provider_url: logger.warning("Skipping OpenRouter model discovery: provider not marked as reachable."); return
        openrouter_keys = self.settings.PROVIDER_API_KEYS.get("openrouter")
        if not openrouter_keys: logger.error("Cannot discover OpenRouter models: No API key found."); self._reachable_providers.pop("openrouter", None); return
        discovery_key = openrouter_keys[0]; models_url = f"{provider_url}/models"; headers = {"Authorization": f"Bearer {discovery_key}"}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                logger.debug(f"Requesting OpenRouter models from {models_url} using key ending '...{discovery_key[-4:]}'")
                async with session.get(models_url, timeout=20) as response:
                    if response.status == 200:
                        data = await response.json(); models_data = data.get("data", [])
                        if models_data:
                            processed_models = []
                            for m_data in models_data:
                                if m_data.get("id"):
                                    model_info: ModelInfo = {
                                        "id": m_data.get("id"),
                                        "name": m_data.get("name"),
                                        "description": m_data.get("description"),
                                        "provider": "openrouter"
                                    }
                                    # Attempt to get parameter count for OpenRouter models
                                    # This path might vary; common is architecture.n_parameters or similar
                                    architecture = m_data.get("architecture", {})
                                    if isinstance(architecture, dict):
                                        n_params = architecture.get("n_parameters")
                                        if isinstance(n_params, (int, float)): # Can be float for e.g. scientific notation
                                            model_info["num_parameters"] = int(n_params)
                                            logger.debug(f"OpenRouter model '{model_info['id']}': parameters {model_info['num_parameters']}")
                                        # Fallback: 'modality_max_tokens' is not num_params, but sometimes related to size
                                        # else:
                                        # context_length = m_data.get("context_length") # Example of another field
                                    processed_models.append(model_info)
                            self._raw_models["openrouter"] = processed_models
                            logger.info(f"Discovered {len(self._raw_models['openrouter'])} models from OpenRouter.")
                        else: logger.warning("OpenRouter models endpoint returned empty data list.")
                    else: error_text = await response.text(); logger.error(f"Failed to fetch OpenRouter models. Status: {response.status}, Response: {error_text[:200]}"); self._reachable_providers.pop("openrouter", None); logger.warning("Removed OpenRouter from reachable providers.")
        except Exception as e: logger.error(f"Error fetching OpenRouter models: {e}", exc_info=True); self._reachable_providers.pop("openrouter", None); logger.warning("Removed OpenRouter from reachable providers.")

    async def _discover_openai_models(self):
        logger.debug("Checking for configured OpenAI...")
        if "openai" not in self._reachable_providers: logger.debug("Skipping OpenAI model adding: provider not marked as reachable."); return
        common_openai = ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
        if "openai" not in self._raw_models: self._raw_models["openai"] = []
        added_count = 0
        # OpenAI API does not directly provide parameter counts for standard models via /v1/models
        # So, num_parameters will remain None for these unless manually added or fetched differently.
        for model_name in common_openai:
             if not any(existing_m.get('id') == model_name for existing_m in self._raw_models["openai"]):
                 self._raw_models["openai"].append(ModelInfo(id=model_name, name=model_name, provider="openai", num_parameters=None))
                 added_count += 1
        if added_count > 0: logger.info(f"Manually added {added_count} common models for OpenAI: {common_openai} (parameter counts not available from API).")
        else: logger.debug("Common OpenAI models already present or none to add.")


    # --- _apply_filters (REFACTORED for new MODEL_TIER logic) ---
    def _apply_filters(self):
        """ Filters raw models based on reachability and the MODEL_TIER setting. """
        self.available_models = {} # Start fresh
        current_tier = self.settings.MODEL_TIER # Get tier from settings
        logger.info(f"Model Registry: Applying filters. Tier='{current_tier}'. Reachable providers: {list(self._reachable_providers.keys())}")

        for provider, models in self._raw_models.items():
            if provider not in self._reachable_providers:
                logger.warning(f"Provider '{provider}' found in raw models but not in reachable list. Skipping filtering.")
                continue

            # Identify provider type (more robustly)
            is_local = provider.startswith("ollama-local") or provider.startswith("litellm-local") or provider == "ollama-proxy"
            logger.debug(f"Filtering provider '{provider}'. Identified as Local: {is_local}")

            if is_local:
                 # Local providers are always included, regardless of tier.
                 self.available_models[provider] = models
                 logger.info(f"Tier Filter ({current_tier}): Included all {len(models)} discovered models for reachable local provider '{provider}'.")
            elif current_tier != "LOCAL": # Process remote providers only if tier is FREE or ALL
                filtered_list = []
                skipped_count = 0
                for model in models:
                    model_id = model.get("id", "")
                    # **FIX**: Assume only OpenRouter uses ':free' suffix for now
                    is_free_remote = provider == "openrouter" and ":free" in model_id.lower()

                    if current_tier == "FREE":
                        if is_free_remote:
                            filtered_list.append(model)
                        else:
                            # Log skipped non-free models when tier is FREE
                            logger.debug(f"Tier Filter (FREE): Skipping remote model '{provider}/{model_id}' (not marked as free).")
                            skipped_count += 1
                    elif current_tier == "ALL":
                        # Include all models (free and non-free) for reachable remote providers
                        filtered_list.append(model)

                if filtered_list:
                    self.available_models[provider] = filtered_list
                    logger.info(f"Tier Filter ({current_tier}): Included {len(filtered_list)} models for reachable remote provider '{provider}'. Skipped: {skipped_count}.")
                else:
                    logger.info(f"Tier Filter ({current_tier}): No models included for remote provider '{provider}'. Skipped: {skipped_count}.")
            # else: current_tier is LOCAL, remote providers were already skipped during discovery initiation

        # Clean up empty provider entries
        original_count = len(self.available_models)
        self.available_models = {p: m for p, m in self.available_models.items() if m}
        if len(self.available_models) < original_count:
             logger.info(f"Removed {original_count - len(self.available_models)} providers with no available models after filtering.")

        logger.info(f"Filtering complete. Final available provider count: {len(self.available_models)}")


    # --- Getter Methods (Unchanged) ---
    def get_available_models_list(self, provider: Optional[str] = None) -> List[str]:
        """ Gets a list of available model IDs, optionally filtered by provider (base name or specific instance). """
        model_ids = set()
        provider_order = ["ollama", "litellm", "openrouter", "openai"]
        if provider:
            if provider in self.available_models: return sorted([m.get("id", "unknown") for m in self.available_models[provider]])
            else:
                 matching_providers = [p for p in self.available_models if p.startswith(f"{provider}-local-") or p == f"{provider}-proxy"]
                 if matching_providers:
                      for p_match in matching_providers: model_ids.update([m.get("id", "unknown") for m in self.available_models[p_match]])
                      return sorted(list(model_ids))
                 else: return []
        processed_providers = set(); final_model_list = []
        for p_base in provider_order:
             matching_local = sorted([p for p in self.available_models if p.startswith(f"{p_base}-local-") or p == f"{p_base}-proxy"])
             for p_local in matching_local:
                  if p_local not in processed_providers: final_model_list.extend([m.get("id", "unknown") for m in self.available_models[p_local]]); processed_providers.add(p_local)
             if p_base in self.available_models and p_base not in processed_providers: final_model_list.extend([m.get("id", "unknown") for m in self.available_models[p_base]]); processed_providers.add(p_base)
        for p_rem in sorted(self.available_models.keys()):
            if p_rem not in processed_providers: final_model_list.extend([m.get("id", "unknown") for m in self.available_models[p_rem]])
        return sorted(list(set(final_model_list)))


    def get_available_models_dict(self) -> Dict[str, List[ModelInfo]]:
        """ Returns a deep copy of the available models dictionary. """
        return copy.deepcopy(self.available_models)

    def find_provider_for_model(self, model_id: str) -> Optional[str]:
        """ Finds the unique provider name (potentially dynamic) for a given model ID. """
        target_model_name = model_id; provider_prefix_search = None
        if '/' in model_id: provider_prefix_search, target_model_name = model_id.split('/', 1)
        for provider_name, models in self.available_models.items():
            if any(m.get("id") == target_model_name for m in models):
                if provider_prefix_search:
                    base_provider_type = provider_name.split('-local-')[0].split('-proxy')[0]
                    if base_provider_type == provider_prefix_search: return provider_name
                else: return provider_name
        if provider_prefix_search and provider_prefix_search in self.available_models:
             if any(m.get("id") == target_model_name for m in self.available_models[provider_prefix_search]): return provider_prefix_search
        logger.debug(f"Could not find provider for model ID '{model_id}' (target name: '{target_model_name}')")
        return None


    def get_formatted_available_models(self) -> str:
        """ Formats the available models for display using the helper method. """
        return self._format_model_list_output(include_header=True)

    def is_model_available(self, provider: str, model_id: str) -> bool:
        """ Checks if a specific model ID is available under a given provider name (potentially dynamic). """
        if provider not in self.available_models: return False
        return any(m.get("id") == model_id for m in self.available_models[provider])

    def get_reachable_provider_url(self, provider: str) -> Optional[str]:
        """ Gets the base URL for a reachable provider (potentially dynamic). """
        return self._reachable_providers.get(provider)

    def is_provider_discovered(self, provider_name: str) -> bool:
        """ Checks if a provider is discovered. """
        return provider_name in self.available_models

    # --- _log_available_models (Unchanged) ---
    def _log_available_models(self):
        """ Logs the available models using the helper method. """
        log_output = self._format_model_list_output(include_header=False)
        if log_output == "No models available.":
             logger.warning("Model Availability: No models discovered or available after filtering.")
        else:
             logger.info("--- Available Models (Filtered by Reachability & Tier) ---")
             for line in log_output.splitlines(): logger.info(line)
             logger.info("-------------------------------------------------------------")

# END OF FILE src/config/model_registry.py
