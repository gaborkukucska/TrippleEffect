# START OF FILE src/config/settings.py
import os
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
import json
import re # Import regex for key matching

# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent.parent # Define BASE_DIR here for module scope

# Define path to the prompts file
PROMPTS_FILE_PATH = BASE_DIR / 'prompts.json'

# Explicitly load .env file from the project root directory
dotenv_path = BASE_DIR / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
    print(f"Loaded environment variables from: {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}. Environment variables might not be loaded.")

# Import ConfigManager - this should be safe as it doesn't import settings
try:
    from src.config.config_manager import config_manager
    print("Successfully imported config_manager instance.")
except ImportError as e:
     print(f"Error importing config_manager: {e}. Agent configurations will not be loaded dynamically.")
     class DummyConfigManager: # Define Dummy if import fails
         def get_config_data_sync(self): return {"agents": [], "teams": {}}
         async def get_config(self): return []
         async def get_teams(self): return {}
         async def get_full_config(self): return {}
         async def load_config(self): return {"agents": [], "teams": {}}
         async def add_agent(self, a): return False
         async def update_agent(self, a, d): return False
         async def delete_agent(self, a): return False
     config_manager = DummyConfigManager()


# Import ModelRegistry class only
try:
    from src.config.model_registry import ModelRegistry as ModelRegistryClass # Rename to avoid name clash
    print("Successfully imported ModelRegistry class.")
    _ModelRegistry = ModelRegistryClass # Assign to temp variable
except ImportError as e:
    print(f"Error importing ModelRegistry class: {e}. Dynamic model discovery will not be available.")
    class DummyModelRegistry: # Define Dummy if import fails
        available_models: Dict = {}
        def __init__(self, settings_obj=None): pass # Accept arg even if dummy
        async def discover_models_and_providers(self): logger.error("ModelRegistry unavailable."); pass
        def get_available_models_list(self, p=None): return []
        def get_formatted_available_models(self): return "Model Registry Unavailable."
        def is_model_available(self, p, m): return False
        def get_available_models_dict(self) -> Dict[str, List[Any]]: return {}
        def find_provider_for_model(self, model_id: str) -> Optional[str]: return None
        def get_reachable_provider_url(self, provider: str) -> Optional[str]: return None
    _ModelRegistry = DummyModelRegistry # Assign Dummy to temp variable


logger = logging.getLogger(__name__)


class Settings:
    """
    Holds application settings, loaded from environment variables, config.yaml, and prompts.json.
    Manages API keys (supporting multiple keys per provider), base URLs,
    default agent parameters, initial agent configs, and standard prompts.
    Uses ConfigManager to load configurations synchronously at startup.
    ModelRegistry is instantiated *after* settings are loaded.
    Provides checks for provider configuration status.
    """
    def __init__(self):
        # --- Provider URLs and Referer (from .env) ---
        self.OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
        self.OPENROUTER_BASE_URL: Optional[str] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.OPENROUTER_REFERER: Optional[str] = os.getenv("OPENROUTER_REFERER")
        self.OLLAMA_BASE_URL: Optional[str] = os.getenv("OLLAMA_BASE_URL") # Allow None, discovery will try localhost. Overridden by proxy if enabled.
        self.LITELLM_BASE_URL: Optional[str] = os.getenv("LITELLM_BASE_URL") # Allow None, discovery will try localhost

        # --- Ollama Proxy Settings (from .env) ---
        self.USE_OLLAMA_PROXY: bool = os.getenv("USE_OLLAMA_PROXY", "false").lower() == "true"
        self.OLLAMA_PROXY_PORT: int = int(os.getenv("OLLAMA_PROXY_PORT", "3000"))
        # OLLAMA_PROXY_TARGET_URL is used by the proxy itself, not directly by Python settings

        # --- Load Multiple API Keys ---
        self.PROVIDER_API_KEYS: Dict[str, List[str]] = {}
        provider_key_pattern = re.compile(r"^([A-Z_]+)_API_KEY(?:_(\d+))?$")
        known_provider_env_prefixes = [
            "OPENAI", "OPENROUTER", "LITELLM", "ANTHROPIC", "GOOGLE", "DEEPSEEK" # Add more as needed
        ]
        logger.info("Scanning environment variables for API keys...")
        for key, value in os.environ.items():
            match = provider_key_pattern.match(key)
            if match and value:
                provider_prefix = match.group(1)
                key_index_str = match.group(2)
                # Special case for Tavily - Treat it as a separate key, not a generic provider
                if provider_prefix == "TAVILY":
                     continue # Skip adding TAVILY to PROVIDER_API_KEYS

                normalized_provider = provider_prefix.lower()
                if provider_prefix in known_provider_env_prefixes:
                    if normalized_provider not in self.PROVIDER_API_KEYS:
                        self.PROVIDER_API_KEYS[normalized_provider] = []
                    self.PROVIDER_API_KEYS[normalized_provider].append(value)
                    key_index = int(key_index_str) if key_index_str else -1
                    logger.debug(f"Found Provider API key for '{normalized_provider}' (Index: {key_index})")
        for provider, keys in self.PROVIDER_API_KEYS.items():
             logger.info(f"Loaded {len(keys)} API key(s) for provider: {provider}")

        # --- Model Tier ---
        self.MODEL_TIER: str = os.getenv("MODEL_TIER", "FREE").upper() # Default to FREE now
        if self.MODEL_TIER not in ["FREE", "ALL"]:
             logger.warning(f"Warning: Invalid MODEL_TIER '{self.MODEL_TIER}'. Defaulting to 'FREE'.")
             self.MODEL_TIER = "FREE"

        # --- Project/Session Configuration (from .env) ---
        self.PROJECTS_BASE_DIR: Path = Path(os.getenv("PROJECTS_BASE_DIR", str(BASE_DIR / "projects")))

        # --- Tool Configuration (from .env) ---
        self.GITHUB_ACCESS_TOKEN: Optional[str] = os.getenv("GITHUB_ACCESS_TOKEN")
        # --- NEW: Tavily API Key ---
        self.TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY")
        if self.TAVILY_API_KEY: logger.debug("Found TAVILY_API_KEY in environment.")
        # --- END NEW ---


        # --- Load Prompts from JSON ---
        self._load_prompts_from_json() # Call the new method

        # --- Default Agent Configuration (use values from prompts.json or .env as fallback) ---
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openrouter")
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "google/gemini-flash-1.5:free")
        # Get defaults from loaded prompts, fallback to env/hardcoded if JSON load failed
        self.DEFAULT_SYSTEM_PROMPT: str = self.PROMPTS.get("default_system_prompt", os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant."))
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = self.PROMPTS.get("default_agent_persona", os.getenv("DEFAULT_PERSONA", "Assistant Agent"))

        # --- Load Initial Configurations using ConfigManager ---
        raw_config_data: Dict[str, Any] = {}
        try:
            raw_config_data = config_manager.get_config_data_sync()
            print("Successfully loaded initial config via ConfigManager.get_config_data_sync()")
        except Exception as e:
            logger.error(f"Failed to load initial config via ConfigManager: {e}", exc_info=True)

        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])
        if not isinstance(self.AGENT_CONFIGURATIONS, list):
            logger.error("Config format error: 'agents' key is not a list. Resetting to empty.")
            self.AGENT_CONFIGURATIONS = []

        # Deprecated: Static team config is no longer primary way
        self.TEAMS_CONFIG: Dict[str, List[str]] = raw_config_data.get("teams", {})
        if self.TEAMS_CONFIG:
            logger.warning("Config Warning: Static 'teams' definition found in config.yaml. This section is deprecated and ignored by AgentManager.")

        # --- Log Loaded Config Summary ---
        if not self.AGENT_CONFIGURATIONS: print("Warning: No bootstrap agent configurations loaded.")
        else: print(f"Loaded bootstrap agent IDs: {[a.get('agent_id', 'N/A') for a in self.AGENT_CONFIGURATIONS]}")
        print(f"Model Tier setting: {self.MODEL_TIER}")

        self._ensure_projects_dir()
        self._check_required_keys() # Run check after loading everything


    def _load_prompts_from_json(self):
        """Loads prompt templates from prompts.json."""
        # Default prompts in case file loading fails
        default_prompts = {
            "standard_framework_instructions": "--- Standard Tool & Communication Protocol ---\nYour Agent ID: `{agent_id}`\nYour Assigned Team ID: `{team_id}`\n{tool_descriptions_xml}\n--- End Standard Protocol ---",
            "admin_ai_operational_instructions": "--- Admin AI Core Operational Workflow ---\n{tool_descriptions_xml}\n--- End Admin AI Core Operational Workflow ---",
            "default_system_prompt": "You are a helpful assistant.",
            "default_agent_persona": "Assistant Agent"
        }
        try:
            if PROMPTS_FILE_PATH.exists():
                 with open(PROMPTS_FILE_PATH, 'r', encoding='utf-8') as f:
                     loaded_prompts = json.load(f)
                     # Basic validation
                     if isinstance(loaded_prompts, dict) and \
                        "standard_framework_instructions" in loaded_prompts and \
                        "admin_ai_operational_instructions" in loaded_prompts and \
                        "default_system_prompt" in loaded_prompts and \
                        "default_agent_persona" in loaded_prompts:
                          self.PROMPTS = loaded_prompts
                          logger.info(f"Successfully loaded prompts from {PROMPTS_FILE_PATH}.")
                     else:
                          logger.error(f"Invalid structure in {PROMPTS_FILE_PATH}. Using default prompts.")
                          self.PROMPTS = default_prompts
            else:
                 logger.warning(f"Prompts file not found at {PROMPTS_FILE_PATH}. Using default prompts.")
                 self.PROMPTS = default_prompts
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {PROMPTS_FILE_PATH}: {e}. Using default prompts.")
            self.PROMPTS = default_prompts
        except Exception as e:
            logger.error(f"Error loading prompts file {PROMPTS_FILE_PATH}: {e}. Using default prompts.", exc_info=True)
            self.PROMPTS = default_prompts


    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data if it doesn't exist."""
        try:
             self.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {self.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {self.PROJECTS_BASE_DIR}: {e}")


    def _check_required_keys(self):
        """Checks provider configuration status based on found keys/URLs."""
        providers_used_in_bootstrap = {self.DEFAULT_AGENT_PROVIDER}
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 provider = agent_conf_entry.get("config", {}).get("provider")
                 if provider: providers_used_in_bootstrap.add(provider)

        print("-" * 30); logger.info("Provider Configuration Check:")
        all_known_providers = set(self.PROVIDER_API_KEYS.keys()) | {"ollama", "litellm"}

        for provider in sorted(list(all_known_providers)):
             is_configured = self.is_provider_configured(provider)
             is_used = provider in providers_used_in_bootstrap
             num_keys = len(self.PROVIDER_API_KEYS.get(provider, []))
             config_details = self.get_provider_config(provider)
             detail_parts = []

             if is_configured:
                 if provider == "ollama":
                     if self.USE_OLLAMA_PROXY: detail_parts.append(f"Proxy Enabled (Port: {self.OLLAMA_PROXY_PORT})")
                     if self.OLLAMA_BASE_URL: detail_parts.append(f"Direct URL Set ({self.OLLAMA_BASE_URL})")
                     elif not self.USE_OLLAMA_PROXY: detail_parts.append("Direct URL Not Set (will use defaults/discovery)")
                 elif provider == "litellm":
                     if config_details.get('base_url'): detail_parts.append("URL Set")
                     else: detail_parts.append("URL Not Set (will use defaults/discovery)")
                 else: # Remote providers
                     detail_parts.append(f"{num_keys} Key(s)")
                     if config_details.get('base_url'): detail_parts.append("Base URL Set")
                     if config_details.get('referer'): detail_parts.append("Referer Set")
                 logger.info(f"✅ {provider.capitalize()}: Configured ({', '.join(detail_parts)})")
             elif is_used:
                 logger.warning(f"⚠️ WARNING: {provider.capitalize()} used by bootstrap/default but NOT configured.")
             else:
                 logger.info(f"ℹ️ INFO: {provider.capitalize()} not configured and not explicitly used by bootstrap agents.")

        # --- Log Tool Specific Keys ---
        if self.GITHUB_ACCESS_TOKEN: logger.info("✅ GitHub Access Token: Found (for GitHub tool)")
        else: logger.info("ℹ️ INFO: GITHUB_ACCESS_TOKEN not set. GitHub tool may not function fully.")
        # --- NEW: Tavily Check ---
        if self.TAVILY_API_KEY: logger.info("✅ Tavily API Key: Found (for Web Search tool)")
        else: logger.info("ℹ️ INFO: TAVILY_API_KEY not set. Web Search tool will use scraping fallback.")
        # --- END NEW ---
        print("-" * 30)


    def get_provider_config(self, provider_name: str) -> Dict[str, Any]:
        """
        Gets the NON-KEY configuration (base_url, referer) for a provider.
        Keys are managed and added by the ProviderKeyManager.
        """
        config = {}
        provider_name = provider_name.lower()
        if provider_name == "openai": config['base_url'] = self.OPENAI_BASE_URL
        elif provider_name == "openrouter":
             referer = self.OPENROUTER_REFERER or f"http://localhost:8000/{self.DEFAULT_PERSONA}"
             config['base_url'] = self.OPENROUTER_BASE_URL
             config['referer'] = referer
        elif provider_name == "ollama": config['base_url'] = self.OLLAMA_BASE_URL
        elif provider_name == "litellm": config['base_url'] = self.LITELLM_BASE_URL
        else:
             if provider_name: logger.debug(f"Requested base provider config for potentially unknown provider '{provider_name}'")
        return {k: v for k, v in config.items() if v is not None}


    def is_provider_configured(self, provider_name: str) -> bool:
        """
        Checks if a provider has its essential configuration set in .env.
        - Remote providers require at least one API key.
        - LiteLLM requires LITELLM_BASE_URL.
        - Ollama requires OLLAMA_BASE_URL *or* USE_OLLAMA_PROXY=true.
        """
        provider_name = provider_name.lower()
        if provider_name == "ollama": return self.USE_OLLAMA_PROXY or bool(self.OLLAMA_BASE_URL)
        elif provider_name == "litellm": return bool(self.LITELLM_BASE_URL)
        else: return provider_name in self.PROVIDER_API_KEYS and bool(self.PROVIDER_API_KEYS[provider_name])


    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific bootstrap agent's configuration dictionary by its ID."""
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 if agent_conf_entry.get('agent_id') == agent_id:
                     return agent_conf_entry.get('config', {})
        return None


    def get_formatted_allowed_models(self) -> str:
        """ Delegates to ModelRegistry. Requires discover_models() to have been run. """
        global model_registry
        return model_registry.get_formatted_available_models()


# --- Create Singleton Instances ---
settings = Settings()

# --- Instantiate ModelRegistry *after* settings is created *** ---
model_registry = _ModelRegistry(settings)
print("Instantiated ModelRegistry singleton.")
# --- *** END INSTANTIATION *** ---
