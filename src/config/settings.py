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
    load_dotenv(dotenv_path=dotenv_path, override=True)
    print(f"Loaded environment variables from: {dotenv_path} (Override=True)")
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
    # Use an alias to avoid potential name clashes if ModelRegistry is also instantiated here later
    from src.config.model_registry import ModelRegistry as ModelRegistryClass
    print("Successfully imported ModelRegistry class.")
    _ModelRegistry = ModelRegistryClass # Assign to temp variable for instantiation later
except ImportError as e:
    print(f"Error importing ModelRegistry class: {e}. Dynamic model discovery will not be available.")
    class DummyModelRegistry: # Define Dummy if import fails
        available_models: Dict = {}
        _reachable_providers: Dict = {} # Add dummy attribute needed by _check_required_keys
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
    Controls model availability via MODEL_TIER (LOCAL, FREE, ALL).
    """
    def __init__(self):
        # --- Provider URLs and Referer (from .env) ---
        self.OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
        self.OPENROUTER_BASE_URL: Optional[str] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.OPENROUTER_REFERER: Optional[str] = os.getenv("OPENROUTER_REFERER")

        # --- Local API Discovery Settings (from .env) ---
        self.LOCAL_API_SCAN_ENABLED: bool = os.getenv("LOCAL_API_SCAN_ENABLED", "true").lower() == "true"
        try:
            ports_str = os.getenv("LOCAL_API_SCAN_PORTS", "11434,8000")
            self.LOCAL_API_SCAN_PORTS: List[int] = [int(p.strip()) for p in ports_str.split(',') if p.strip()]
        except ValueError:
            logger.warning(f"Invalid LOCAL_API_SCAN_PORTS value '{ports_str}'. Using default [11434, 8000].")
            self.LOCAL_API_SCAN_PORTS = [11434, 8000]
        try:
            self.LOCAL_API_SCAN_TIMEOUT: float = float(os.getenv("LOCAL_API_SCAN_TIMEOUT", "0.5"))
        except ValueError:
            logger.warning("Invalid LOCAL_API_SCAN_TIMEOUT value. Using default 0.5.")
            self.LOCAL_API_SCAN_TIMEOUT = 0.5
        logger.info(f"Local API Discovery settings: Enabled={self.LOCAL_API_SCAN_ENABLED}, Ports={self.LOCAL_API_SCAN_PORTS}, Timeout={self.LOCAL_API_SCAN_TIMEOUT}s")

        # --- Load Multiple API Keys ---
        self.PROVIDER_API_KEYS: Dict[str, List[str]] = {}
        provider_key_pattern = re.compile(r"^([A-Z_]+)_API_KEY(?:_(\d+))?$")
        known_provider_env_prefixes = ["OPENAI", "OPENROUTER", "LITELLM", "ANTHROPIC", "GOOGLE", "DEEPSEEK"]
        logger.debug("Settings Init: Scanning environment variables for API keys...")
        for key, value in os.environ.items():
            match = provider_key_pattern.match(key)
            if match and value:
                provider_prefix = match.group(1); key_index_str = match.group(2); key_index = int(key_index_str) if key_index_str else 0
                if provider_prefix == "TAVILY": logger.debug(f"Settings Init: Skipping TAVILY_API_KEY '{key}'."); continue
                normalized_provider = provider_prefix.lower()
                if normalized_provider not in self.PROVIDER_API_KEYS: self.PROVIDER_API_KEYS[normalized_provider] = []
                self.PROVIDER_API_KEYS[normalized_provider].append(value)
                logger.debug(f"Settings Init: Found Provider API key: EnvVar='{key}', Provider='{normalized_provider}', Index='{key_index}'")
        for provider in self.PROVIDER_API_KEYS: self.PROVIDER_API_KEYS[provider].sort()
        for provider, keys in self.PROVIDER_API_KEYS.items(): logger.info(f"Settings Init: Loaded {len(keys)} API key(s) for provider: {provider}")

        # --- Refactored Model Tier ---
        valid_tiers = ["LOCAL", "FREE", "ALL"]
        self.MODEL_TIER: str = os.getenv("MODEL_TIER", "LOCAL").upper()
        if self.MODEL_TIER not in valid_tiers:
             logger.warning(f"Warning: Invalid MODEL_TIER '{self.MODEL_TIER}'. Valid options: {valid_tiers}. Defaulting to 'LOCAL'.")
             self.MODEL_TIER = "LOCAL"
        logger.info(f"Settings Init: Effective MODEL_TIER = {self.MODEL_TIER}")

        # --- REMOVED: MODEL_COST ---

        # --- Retry/Failover Config ---
        try: self.MAX_STREAM_RETRIES: int = int(os.getenv("MAX_STREAM_RETRIES", "3"))
        except ValueError: logger.warning("Invalid MAX_STREAM_RETRIES, using default 3."); self.MAX_STREAM_RETRIES = 3
        try: self.RETRY_DELAY_SECONDS: float = float(os.getenv("RETRY_DELAY_SECONDS", "5.0"))
        except ValueError: logger.warning("Invalid RETRY_DELAY_SECONDS, using default 5.0."); self.RETRY_DELAY_SECONDS = 5.0
        try: self.MAX_FAILOVER_ATTEMPTS: int = int(os.getenv("MAX_FAILOVER_ATTEMPTS", "3"))
        except ValueError: logger.warning("Invalid MAX_FAILOVER_ATTEMPTS, using default 3."); self.MAX_FAILOVER_ATTEMPTS = 3
        logger.info(f"Retry/Failover settings loaded: MaxRetries={self.MAX_STREAM_RETRIES}, Delay={self.RETRY_DELAY_SECONDS}s, MaxFailover={self.MAX_FAILOVER_ATTEMPTS}")

        # --- PM Manage State Timer Interval ---
        try:
            self.PM_MANAGE_CHECK_INTERVAL_SECONDS: float = float(os.getenv("PM_MANAGE_CHECK_INTERVAL_SECONDS", "60.0"))
            logger.info(f"Loaded PM_MANAGE_CHECK_INTERVAL_SECONDS: {self.PM_MANAGE_CHECK_INTERVAL_SECONDS}s")
        except ValueError:
            logger.warning("Invalid PM_MANAGE_CHECK_INTERVAL_SECONDS in .env, using default 60.0.")
            self.PM_MANAGE_CHECK_INTERVAL_SECONDS = 60.0

        # --- Project/Session Configuration ---
        self.PROJECTS_BASE_DIR: Path = Path(os.getenv("PROJECTS_BASE_DIR", str(BASE_DIR / "projects")))

        # --- Tool Configuration ---
        self.GITHUB_ACCESS_TOKEN: Optional[str] = os.getenv("GITHUB_ACCESS_TOKEN")
        self.TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY")
        if self.TAVILY_API_KEY: logger.debug("Settings Init: Found TAVILY_API_KEY.")

        # --- Load Prompts from JSON ---
        self._load_prompts_from_json()

        # --- Default Agent Configuration ---
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openrouter")
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "google/gemma-2-9b-it:free")
        self.DEFAULT_SYSTEM_PROMPT: str = self.PROMPTS.get("default_system_prompt", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = self.PROMPTS.get("default_agent_persona", "Assistant Agent")
        # --- Max Tokens ---
        try: self.ADMIN_AI_LOCAL_MAX_TOKENS: int = int(os.getenv("ADMIN_AI_LOCAL_MAX_TOKENS", "512")); logger.info(f"Loaded ADMIN_AI_LOCAL_MAX_TOKENS: {self.ADMIN_AI_LOCAL_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid ADMIN_AI_LOCAL_MAX_TOKENS, using 512."); self.ADMIN_AI_LOCAL_MAX_TOKENS = 512
        try: self.PM_WORK_STATE_MAX_TOKENS: int = int(os.getenv("PM_WORK_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded PM_WORK_STATE_MAX_TOKENS: {self.PM_WORK_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_WORK_STATE_MAX_TOKENS, using 1024."); self.PM_WORK_STATE_MAX_TOKENS = 1024

        # --- Load Initial Configurations using ConfigManager ---
        raw_config_data: Dict[str, Any] = {}
        try:
            raw_config_data = config_manager.get_config_data_sync()
            logger.info(f"Settings Init: Raw config data loaded. Keys: {list(raw_config_data.keys())}")
            print("Successfully loaded initial config via ConfigManager.")
        except Exception as e: logger.error(f"Failed to load initial config via ConfigManager: {e}", exc_info=True); raw_config_data = {}

        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])
        if not isinstance(self.AGENT_CONFIGURATIONS, list): logger.error("Config error: 'agents' not a list."); self.AGENT_CONFIGURATIONS = []
        self.TEAMS_CONFIG: Dict[str, List[str]] = raw_config_data.get("teams", {}) # Deprecated

        # --- Log Loaded Config Summary ---
        if not self.AGENT_CONFIGURATIONS: print("Warning: No bootstrap agent configurations loaded.")
        else: print(f"Loaded bootstrap agent IDs: {[a.get('agent_id', 'N/A') for a in self.AGENT_CONFIGURATIONS]}")
        print(f"Model Tier setting (Effective): {self.MODEL_TIER}")

        self._ensure_projects_dir()
        # _check_required_keys() is now called AFTER ModelRegistry is instantiated below

    # --- METHOD RESTORED ---
    def _load_prompts_from_json(self):
        """Loads prompt templates from prompts.json."""
        default_prompts = {
            "standard_framework_instructions": "--- Standard Tool & Communication Protocol ---\nYour Agent ID: `{agent_id}`\nYour Assigned Team ID: `{team_id}`\n{tool_descriptions_xml}\n--- End Standard Protocol ---",
            "admin_ai_startup_prompt": "You are Admin AI in STARTUP state.",
            "admin_ai_conversation_prompt": "You are Admin AI in CONVERSATION state.",
            "admin_ai_planning_prompt": "You are Admin AI in PLANNING state.",
            "admin_ai_delegated_prompt": "You are Admin AI in WORK_DELEGATED state.",
            "pm_conversation_prompt": "You are Project Manager in CONVERSATION state.",
            "agent_conversation_prompt": "You are Worker Agent in CONVERSATION state.",
            "worker_work_prompt": "You are Worker Agent in WORK state.",
            "pm_work_prompt": "You are Project Manager in WORK state.",
            "pm_manage_prompt": "You are Project Manager in MANAGE state.",
            "default_system_prompt": "You are a helpful assistant.",
            "default_agent_persona": "Assistant Agent"
        }
        try:
            if PROMPTS_FILE_PATH.exists():
                 with open(PROMPTS_FILE_PATH, 'r', encoding='utf-8') as f:
                     self.PROMPTS = json.load(f)
                     logger.info(f"Successfully loaded prompts from {PROMPTS_FILE_PATH}.")
                     # Basic check for essential keys
                     if not isinstance(self.PROMPTS, dict) or "standard_framework_instructions" not in self.PROMPTS:
                         logger.error(f"Prompts file {PROMPTS_FILE_PATH} missing essential keys. Reverting to defaults.")
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

    # --- METHOD RESTORED ---
    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data if it doesn't exist."""
        try:
             self.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {self.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {self.PROJECTS_BASE_DIR}: {e}")


    # --- METHOD RESTORED & MODIFIED ---
    def _check_required_keys(self):
        """Checks provider configuration status based on found keys/URLs, respecting MODEL_TIER."""
        providers_used_in_bootstrap = {self.DEFAULT_AGENT_PROVIDER}
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 provider = agent_conf_entry.get("config", {}).get("provider")
                 if provider: providers_used_in_bootstrap.add(provider)

        print("-" * 30); logger.info("Provider Configuration Check:")
        # Include providers explicitly mentioned in config, even if no keys are in .env yet
        all_providers_to_check = set(self.PROVIDER_API_KEYS.keys()) | {"ollama", "litellm"} | providers_used_in_bootstrap

        for provider in sorted(list(all_providers_to_check)):
             is_configured = self.is_provider_configured(provider) # Uses updated logic
             is_used = provider in providers_used_in_bootstrap
             is_remote = provider not in ["ollama", "litellm"]
             num_keys = len(self.PROVIDER_API_KEYS.get(provider, []))

             # Skip checks for remote providers if tier is LOCAL
             if self.MODEL_TIER == "LOCAL" and is_remote:
                 if is_used: logger.warning(f"⚠️ {provider.capitalize()}: Used by config but MODEL_TIER=LOCAL (Ignored).")
                 else: logger.info(f"ℹ️ {provider.capitalize()}: Remote provider skipped (MODEL_TIER=LOCAL).")
                 continue

             # Local provider checks (status depends on discovery, logged by registry)
             if not is_remote:
                 # This check happens *before* discovery finishes, so rely on is_used for warnings
                 if is_used:
                      # We can't know if it *will be* discovered yet, so just note it's used
                      logger.info(f"ℹ️ {provider.capitalize()}: Local provider used by config (Discovery pending).")
                 else:
                      logger.info(f"ℹ️ INFO: {provider.capitalize()} not explicitly used by config (Discovery pending).")
             # Remote provider checks (for FREE or ALL tiers)
             else:
                 if is_configured:
                     config_details = self.get_provider_config(provider)
                     detail_parts = [f"{num_keys} Key(s)"]
                     if config_details.get('base_url'): detail_parts.append("Base URL Set")
                     if config_details.get('referer'): detail_parts.append("Referer Set")
                     logger.info(f"✅ {provider.capitalize()}: Configured ({', '.join(detail_parts)})")
                 elif is_used:
                     logger.warning(f"⚠️ WARNING: {provider.capitalize()} used by config but NOT configured (Missing API Key?).")
                 else:
                     logger.info(f"ℹ️ INFO: {provider.capitalize()} not configured and not used by config.")

        # Tool Specific Keys
        if self.GITHUB_ACCESS_TOKEN: logger.info("✅ GitHub Access Token: Found")
        else: logger.info("ℹ️ INFO: GITHUB_ACCESS_TOKEN not set (GitHub tool limited).")
        if self.TAVILY_API_KEY: logger.info("✅ Tavily API Key: Found")
        else: logger.info("ℹ️ INFO: TAVILY_API_KEY not set (Web Search uses fallback).")
        print("-" * 30)

    # --- METHOD RESTORED ---
    def get_provider_config(self, provider_name: str) -> Dict[str, Any]:
        """
        Gets the NON-KEY configuration (base_url, referer) for a provider.
        Keys are managed and added by the ProviderKeyManager.
        """
        config = {}
        provider_name = provider_name.lower()
        if provider_name == "openai": config['base_url'] = self.OPENAI_BASE_URL
        elif provider_name == "openrouter":
             referer = self.OPENROUTER_REFERER or f"http://localhost:8000/{self.DEFAULT_PERSONA}" # Fallback referer
             config['base_url'] = self.OPENROUTER_BASE_URL
             config['referer'] = referer
        else:
             if provider_name: logger.debug(f"Req base config for unknown provider '{provider_name}'")
        return {k: v for k, v in config.items() if v is not None}

    # --- METHOD RESTORED & MODIFIED ---
    def is_provider_configured(self, provider_name: str) -> bool:
        """
        Checks if a provider has its essential configuration set.
        - Remote providers require at least one API key in PROVIDER_API_KEYS.
        - Local providers ('ollama', 'litellm') always return False here;
          their status depends on runtime discovery via ModelRegistry.
        """
        provider_name = provider_name.lower()
        if provider_name.startswith("ollama-local") or provider_name.startswith("litellm-local"):
            # For dynamic local providers, check if they are discovered by ModelRegistry
            return model_registry.is_provider_discovered(provider_name)
        elif provider_name in ["ollama", "litellm"]:
            # Availability depends on discovery, not .env keys
            logger.debug(f"is_provider_configured check for local '{provider_name}': Returning False (status depends on discovery).")
            return False
        else:
            # Check remote providers for a non-empty list of API keys.
            is_configured = provider_name in self.PROVIDER_API_KEYS and bool(self.PROVIDER_API_KEYS[provider_name])
            logger.debug(f"is_provider_configured check for remote '{provider_name}': Keys found = {is_configured}")
            return is_configured


    # --- METHOD RESTORED ---
    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific bootstrap agent's configuration dictionary by its ID."""
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 if agent_conf_entry.get('agent_id') == agent_id:
                     return agent_conf_entry.get('config', {})
        return None

    # --- METHOD RESTORED ---
    def get_formatted_allowed_models(self) -> str:
        """ Delegates to ModelRegistry. Requires discover_models() to have been run. """
        global model_registry # Ensure global is used
        return model_registry.get_formatted_available_models()


# --- Create Singleton Instances (RESTORED) ---
settings = Settings()

# --- Instantiate ModelRegistry *after* settings is created (RESTORED) ---
model_registry = _ModelRegistry(settings)
print("Instantiated ModelRegistry singleton.")

# --- Call _check_required_keys AFTER ModelRegistry is available (RESTORED) ---
try:
    settings._check_required_keys()
    logger.info("Initial provider configuration check completed.")
except Exception as check_err:
     logger.error(f"Error running initial provider configuration check: {check_err}", exc_info=True)

# --- End of File ---
