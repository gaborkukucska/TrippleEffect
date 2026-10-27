# START OF FILE src/config/settings.py
import os
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
import json
import yaml # For loading governance principles
import re # Import regex for key matching

# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent.parent # Define BASE_DIR here for module scope

# Define path to the prompts file
PROMPTS_FILE_PATH = BASE_DIR / 'prompts.yaml'
GOVERNANCE_FILE_PATH = BASE_DIR / 'governance.yaml' # Path for governance principles

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
        def is_provider_discovered(self, provider_name: str) -> bool: return False
        def get_model_info(self, model_id: str) -> Optional[Dict]: return None
    _ModelRegistry = DummyModelRegistry # Assign Dummy to temp variable

logger = logging.getLogger(__name__)

# --- Round 3p: a key set twice in .env ---------------------------------------------------------------------------------------
# 2026-10-25 20:00 (laptop, qwen3.5:4b): the .env had a "When using Ollama on laptop" block with OLLAMA_MAX_CTX_CAP=24576 and
# OLLAMA_CONCURRENCY_LIMIT=1, and further down the "Ollama Specific Overrides" block of .env.example with 32768 and 3.
# python-dotenv keeps the LAST value, so the run used 32768 and 3 - and nothing said so.
_ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _env_value(raw: str) -> str:
    """The value of a .env assignment as python-dotenv reads it (quotes removed, ' # comment' after a bare value removed)."""
    text = raw.strip()
    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        return text[1:end] if end > 0 else text[1:]
    return re.split(r"\s+#", text, maxsplit=1)[0].strip()


def env_file_duplicates(path: Path) -> Dict[str, List[tuple]]:
    """
    Round 3p: {name: [(line number, value), ...]} of the keys a .env file assigns more than once with DIFFERENT values (the
    last one is the one that applies). Commented lines are ignored. Never raises.
    """
    found: Dict[str, List[tuple]] = {}
    try:
        for number, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            match = _ENV_ASSIGNMENT_RE.match(line)
            if match:
                found.setdefault(match.group(1), []).append((number, _env_value(match.group(2))))
    except OSError:
        return {}
    return {name: items for name, items in found.items() if len(items) > 1 and len({value for _n, value in items}) > 1}


def env_duplicate_warnings(path: Path, secret_name_re: str = r"KEY|TOKEN|SECRET|PASSWORD") -> List[str]:
    """Round 3p: one warning text per key that .env sets twice with different values (secret values are not shown)."""
    warnings_found = []
    for name, items in env_file_duplicates(path).items():
        secret = re.search(secret_name_re, name, re.IGNORECASE) is not None
        shown = ", ".join(f"line {number}: {'<hidden>' if secret else repr(value)}" for number, value in items)
        warnings_found.append(f".env sets {name} {len(items)} times with different values ({shown}). The LAST one "
                              f"(line {items[-1][0]}) applies; remove or comment out the others.")
    return warnings_found


# --- Governance principle validation (Round 2m) ---
# Before Round 2m a principle without 'name' or 'text' was kept and made every system prompt fail with a KeyError, and a
# list entry that was not a dict raised AttributeError in the validation itself, which emptied the whole list.
GOVERNANCE_PRINCIPLE_KEYS = ("id", "name", "text", "applies_to", "enabled")
GOVERNANCE_PRINCIPLE_DISPLAY_KEYS = ("name", "text")


def optional_float_setting(name: str, default: str) -> Optional[float]:
    """
    Round 2y: an optional number from the environment: `default` when the variable is not set, None when it is set to an
    empty value (or 'none' / 'default'), and `default` (with a warning) when it is not a number.
    """
    raw = os.getenv(name)
    text = default if raw is None else raw.strip()
    if text.lower() in ("", "none", "default"):
        return None
    try:
        return float(text)
    except ValueError:
        logger.warning(f"Invalid {name} {raw!r}, using {default}.")
        return float(default) if default.strip() else None


def usable_governance_principles(entries: list) -> list:
    """
    The principles that can be shown to agents. Entries that are not dicts or lack 'name' or 'text' are dropped with a
    warning; entries that only lack 'id', 'applies_to' or 'enabled' are kept with a warning (a missing 'enabled' means
    disabled, a missing 'applies_to' means it applies to nobody).
    """
    usable = []
    for index, principle in enumerate(entries):
        if not isinstance(principle, dict):
            logger.warning(f"Governance principle at index {index} is not a dictionary and is ignored: {principle!r}")
            continue
        missing = [key for key in GOVERNANCE_PRINCIPLE_KEYS if key not in principle]
        label = principle.get("id", f"index {index}")
        if any(key in missing for key in GOVERNANCE_PRINCIPLE_DISPLAY_KEYS):
            logger.warning(f"Governance principle {label} is missing required keys: {', '.join(missing)}. It is ignored.")
            continue
        if missing:
            logger.warning(f"Governance principle {label} is missing required keys: {', '.join(missing)}. It is kept.")
        usable.append(principle)
    return usable


class Settings:
    """
    Holds application settings, loaded from environment variables, config.yaml, and prompts.yaml.
    Manages API keys (supporting multiple keys per provider), base URLs,
    default agent parameters, initial agent configs, and standard prompts.
    Uses ConfigManager to load configurations synchronously at startup.
    ModelRegistry is instantiated *after* settings is loaded.
    Provides checks for provider configuration status.
    Controls model availability via MODEL_TIER (LOCAL, FREE, ALL).
    """
    def __init__(self):
        # --- Provider URLs and Referer (from .env) ---
        self.OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
        self.VLLM_BASE_URL: Optional[str] = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
        self.VLLM_API_KEY: Optional[str] = os.getenv("VLLM_API_KEY", "EMPTY")
        self.OPENROUTER_BASE_URL: Optional[str] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.OPENROUTER_REFERER: Optional[str] = os.getenv("OPENROUTER_REFERER")

        # --- Local API Discovery Settings (from .env) ---
        self.LOCAL_API_SCAN_ENABLED: bool = os.getenv("LOCAL_API_SCAN_ENABLED", "true").lower() == "true"
        ports_str = os.getenv("LOCAL_API_SCAN_PORTS", "11434,8000")
        try:
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

        # --- Custom Local API URLs ---
        self.OLLAMA_API_URLS: List[str] = [url.strip() for url in os.getenv("OLLAMA_API_URLS", "").split(",") if url.strip()]
        self.VLLM_API_URLS: List[str] = [url.strip() for url in os.getenv("VLLM_API_URLS", "").split(",") if url.strip()]
        # Round 2l: with OLLAMA_API_URLS set, the default localhost Ollama is only used when this is true
        self.OLLAMA_INCLUDE_LOCALHOST: bool = os.getenv("OLLAMA_INCLUDE_LOCALHOST", "false").strip().lower() in ("1", "true", "yes", "on")
        # --- Load Multiple API Keys ---
        self.PROVIDER_API_KEYS: Dict[str, List[str]] = {}
        provider_key_pattern = re.compile(r"^([A-Z_]+)_API_KEY(?:_(\d+))?$")
        known_provider_env_prefixes = ["OPENAI", "OPENROUTER", "LITELLM", "ANTHROPIC", "GOOGLE", "DEEPSEEK", "VLLM"]
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

        # --- START Model Tier ---
        valid_tiers = ["LOCAL", "FREE", "ALL"]
        self.MODEL_TIER: str = os.getenv("MODEL_TIER", "LOCAL").upper()
        if self.MODEL_TIER not in valid_tiers:
             logger.warning(f"Warning: Invalid MODEL_TIER '{self.MODEL_TIER}'. Valid options: {valid_tiers}. Defaulting to 'LOCAL'.")
             self.MODEL_TIER = "LOCAL"
        logger.info(f"Settings Init: Effective MODEL_TIER = {self.MODEL_TIER}")

        # --- END Model Tier ---

        # --- Native Tool Calling Config ---
        self.NATIVE_TOOL_CALLING_ENABLED: bool = os.getenv("NATIVE_TOOL_CALLING_ENABLED", "true").lower() == "true"
        logger.info(f"Settings Init: Native Tool Calling Enabled = {self.NATIVE_TOOL_CALLING_ENABLED}")

        # --- Retry/Failover Config ---
        try: self.MAX_STREAM_RETRIES: int = int(os.getenv("MAX_STREAM_RETRIES", "3"))
        except ValueError: logger.warning("Invalid MAX_STREAM_RETRIES, using default 3."); self.MAX_STREAM_RETRIES = 3
        try: self.RETRY_DELAY_SECONDS: float = float(os.getenv("RETRY_DELAY_SECONDS", "5.0"))
        except ValueError: logger.warning("Invalid RETRY_DELAY_SECONDS, using default 5.0."); self.RETRY_DELAY_SECONDS = 5.0
        try: self.MAX_FAILOVER_ATTEMPTS: int = int(os.getenv("MAX_FAILOVER_ATTEMPTS", "3"))
        except ValueError: logger.warning("Invalid MAX_FAILOVER_ATTEMPTS, using default 3."); self.MAX_FAILOVER_ATTEMPTS = 3
        try: self.MAX_CYCLE_TURNS: int = int(os.getenv("MAX_CYCLE_TURNS", "15"))
        except ValueError: logger.warning("Invalid MAX_CYCLE_TURNS, using default 15."); self.MAX_CYCLE_TURNS = 15
        try: self.SUMMARIZER_TRIGGER_THRESHOLD: int = int(os.getenv("SUMMARIZER_TRIGGER_THRESHOLD", "7000"))
        except ValueError: logger.warning("Invalid SUMMARIZER_TRIGGER_THRESHOLD, using default 7000."); self.SUMMARIZER_TRIGGER_THRESHOLD = 7000
        logger.info(f"Retry/Failover settings loaded: MaxRetries={self.MAX_STREAM_RETRIES}, Delay={self.RETRY_DELAY_SECONDS}s, MaxFailover={self.MAX_FAILOVER_ATTEMPTS}, MaxTurns={self.MAX_CYCLE_TURNS}")

        # --- PM Manage State Timer Interval ---
        try:
            self.PM_MANAGE_CHECK_INTERVAL_SECONDS: float = float(os.getenv("PM_MANAGE_CHECK_INTERVAL_SECONDS", "60.0"))
            logger.info(f"Loaded PM_MANAGE_CHECK_INTERVAL_SECONDS: {self.PM_MANAGE_CHECK_INTERVAL_SECONDS}s")
        except ValueError:
            logger.warning("Invalid PM_MANAGE_CHECK_INTERVAL_SECONDS in .env, using default 60.0.")
            self.PM_MANAGE_CHECK_INTERVAL_SECONDS = 60.0

        # --- CG Heartbeat Timer Intervals ---
        try:
            self.CG_STRICTNESS_LEVEL: int = int(os.getenv("CG_STRICTNESS_LEVEL", "2"))
            if self.CG_STRICTNESS_LEVEL not in [1, 2, 3]:
                logger.warning(f"Invalid CG_STRICTNESS_LEVEL {self.CG_STRICTNESS_LEVEL}, using default 2 (Moderate).")
                self.CG_STRICTNESS_LEVEL = 2
                
            self.CG_HEARTBEAT_INTERVAL_SECONDS: float = float(os.getenv("CG_HEARTBEAT_INTERVAL_SECONDS", "60.0"))
            self.CG_STALLED_THRESHOLD_SECONDS: float = float(os.getenv("CG_STALLED_THRESHOLD_SECONDS", "300.0"))
            logger.info(f"Loaded CG settings: Strictness Level={self.CG_STRICTNESS_LEVEL}, Heartbeat Interval={self.CG_HEARTBEAT_INTERVAL_SECONDS}s, Threshold={self.CG_STALLED_THRESHOLD_SECONDS}s")
        except ValueError:
            logger.warning("Invalid CG settings in .env, using defaults (Strictness=2, 60s/300s).")
            self.CG_STRICTNESS_LEVEL = 2
            self.CG_HEARTBEAT_INTERVAL_SECONDS = 60.0
            self.CG_STALLED_THRESHOLD_SECONDS = 300.0

        # --- Round 2o: Constitutional Guardian escalation ladder (src/agents/cg_escalation.py) ---
        def _positive_float(name: str, default: float) -> float:
            try:
                value = float(os.getenv(name, str(default)))
                return value if value > 0 else default
            except ValueError:
                logger.warning(f"Invalid {name} in .env, using {default}.")
                return default
        # an undecided concern passes to the next decider after this long
        self.CG_DECISION_TIMEOUT_SECONDS: float = _positive_float("CG_DECISION_TIMEOUT_SECONDS", 300.0)
        # a concern whose author was asked to revise is closed if no revised version comes within this long
        self.CG_REVISION_TIMEOUT_SECONDS: float = _positive_float("CG_REVISION_TIMEOUT_SECONDS", 900.0)
        # revised versions that still raise a concern before it passes to the next decider
        self.CG_MAX_REVISIONS_PER_LEVEL: int = int(_positive_float("CG_MAX_REVISIONS_PER_LEVEL", 2))

        # --- Round 2r: agents left in the 'error' status are resumed by the watchdog (src/agents/error_recovery.py) ---
        # an agent still in 'error' this long after the watchdog first saw it is resumed (checked every
        # PM_MANAGE_CHECK_INTERVAL_SECONDS)
        self.ERROR_RECOVERY_DELAY_SECONDS: float = _positive_float("ERROR_RECOVERY_DELAY_SECONDS", 60.0)
        # resumptions per agent and hour; after that its supervisor (worker -> PM, PM -> Admin AI) gets one report
        self.ERROR_RECOVERY_MAX_PER_HOUR: int = int(_positive_float("ERROR_RECOVERY_MAX_PER_HOUR", 3))

        # --- Project/Session Configuration ---
        self.PROJECTS_BASE_DIR: Path = Path(os.getenv("PROJECTS_BASE_DIR", str(BASE_DIR / "projects")))

        # --- Tool Configuration ---
        self.GITHUB_ACCESS_TOKEN: Optional[str] = os.getenv("GITHUB_ACCESS_TOKEN")
        self.SEARXNG_URL: Optional[str] = os.getenv("SEARXNG_URL")
        self.SEARXNG_FORMAT: str = os.getenv("SEARXNG_FORMAT", "json")
        if self.SEARXNG_URL: logger.debug(f"Settings Init: Found SEARXNG_URL.")

        # --- Authentication Settings ---
        import secrets
        self.SECRET_KEY: str = os.getenv("SECRET_KEY", "")
        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_urlsafe(64)
            logger.warning("SECRET_KEY not set in .env. Generated a random key for this session. Set SECRET_KEY in .env for persistent sessions across restarts.")
        self.ALLOW_MULTI_USER_REGISTRATION: bool = os.getenv("ALLOW_MULTI_USER_REGISTRATION", "false").lower() == "true"
        try:
            self.JWT_EXPIRATION_MINUTES: int = int(os.getenv("JWT_EXPIRATION_MINUTES", "43200"))
        except ValueError:
            logger.warning("Invalid JWT_EXPIRATION_MINUTES, using default 43200 (30 days).")
            self.JWT_EXPIRATION_MINUTES = 43200
        self.JWT_ALGORITHM: str = "HS256"
        logger.info(f"Settings Init: Auth Settings - MultiUser={self.ALLOW_MULTI_USER_REGISTRATION}, JWT Expiry={self.JWT_EXPIRATION_MINUTES}min")

        # --- Load Prompts from YAML ---
        self._load_prompts_from_yaml()

        # --- Load Governance Principles from YAML ---
        self.GOVERNANCE_PRINCIPLES: List[Dict[str, Any]] = [] # Initialize attribute
        self._load_governance_principles()


        # --- Default Agent Configuration ---
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openrouter")
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "google/gemma-2-9b-it:free")
        self.DEFAULT_SYSTEM_PROMPT: str = self.PROMPTS.get("default_system_prompt", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = self.PROMPTS.get("default_agent_persona", "Assistant Agent")

        # --- Security Settings ---
        self.DISABLE_COMMAND_EXECUTION: bool = os.getenv("DISABLE_COMMAND_EXECUTION", "False").lower() in ("true", "1", "yes")
        logger.info(f"Loaded DISABLE_COMMAND_EXECUTION: {self.DISABLE_COMMAND_EXECUTION}")

        # --- Command Policy Gate & Shell Sandbox (Review S1/S2/S3) ---
        # enforce = block matching commands, warn = log only, off = no checks
        self.COMMAND_POLICY_MODE: str = os.getenv("COMMAND_POLICY_MODE", "enforce").strip().lower()
        if self.COMMAND_POLICY_MODE not in ("enforce", "warn", "off"):
            logger.warning(f"Invalid COMMAND_POLICY_MODE '{self.COMMAND_POLICY_MODE}'. Using 'enforce'.")
            self.COMMAND_POLICY_MODE = "enforce"
        # Extra environment variable names agent shells may inherit (explicit opt-in, even for secrets)
        self.SANDBOX_ENV_PASSTHROUGH: List[str] = [v.strip() for v in os.getenv("SANDBOX_ENV_PASSTHROUGH", "").split(",") if v.strip()]
        # Give agent shells their own HOME so ~/.npmrc, ~/.git-credentials etc. are not picked up
        self.SANDBOX_ISOLATE_HOME: bool = os.getenv("SANDBOX_ISOLATE_HOME", "true").lower() in ("true", "1", "yes")
        self.SANDBOX_HOME_DIR: Path = Path(os.getenv("SANDBOX_HOME_DIR", str(BASE_DIR / "sandboxes" / "_shell_home")))
        logger.info(f"Loaded shell sandbox settings: PolicyMode={self.COMMAND_POLICY_MODE}, IsolateHome={self.SANDBOX_ISOLATE_HOME}, Passthrough={self.SANDBOX_ENV_PASSTHROUGH}")

        # --- Three-Tier Agent Limits ---
        try:
            # Fallback to MAX_WORKERS_PER_PM for backward compatibility if MAX_WORKERS_PER_TEAM isn't set
            legacy_workers = os.getenv("MAX_WORKERS_PER_PM")
            worker_default = legacy_workers if legacy_workers else "20"
            self.MAX_WORKERS_PER_TEAM: int = int(os.getenv("MAX_WORKERS_PER_TEAM", worker_default))
            logger.info(f"Loaded MAX_WORKERS_PER_TEAM: {self.MAX_WORKERS_PER_TEAM}")
        except ValueError:
            logger.warning("Invalid MAX_WORKERS_PER_TEAM in .env, using default 20.")
            self.MAX_WORKERS_PER_TEAM = 20
            
        try:
            self.MAX_PMS_PER_SESSION: int = int(os.getenv("MAX_PMS_PER_SESSION", "5"))
            logger.info(f"Loaded MAX_PMS_PER_SESSION: {self.MAX_PMS_PER_SESSION}")
        except ValueError:
            logger.warning("Invalid MAX_PMS_PER_SESSION in .env, using default 5.")
            self.MAX_PMS_PER_SESSION = 5
            
        try:
            self.MAX_TEAMS_PER_SESSION: int = int(os.getenv("MAX_TEAMS_PER_SESSION", "10"))
            logger.info(f"Loaded MAX_TEAMS_PER_SESSION: {self.MAX_TEAMS_PER_SESSION}")
        except ValueError:
            logger.warning("Invalid MAX_TEAMS_PER_SESSION in .env, using default 10.")
            self.MAX_TEAMS_PER_SESSION = 10

        # --- Max ADMIN AI Local Tokens ---
        try: self.ADMIN_AI_LOCAL_MAX_TOKENS: int = int(os.getenv("ADMIN_AI_LOCAL_MAX_TOKENS", "4096")); logger.info(f"Loaded ADMIN_AI_LOCAL_MAX_TOKENS: {self.ADMIN_AI_LOCAL_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid ADMIN_AI_LOCAL_MAX_TOKENS, using 4096."); self.ADMIN_AI_LOCAL_MAX_TOKENS = 4096
        # --- Max PM Startup State Tokens ---
        try: self.PM_STARTUP_STATE_MAX_TOKENS: int = int(os.getenv("PM_STARTUP_STATE_MAX_TOKENS", "4096")); logger.info(f"Loaded PM_STARTUP_STATE_MAX_TOKENS: {self.PM_STARTUP_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_STARTUP_STATE_MAX_TOKENS, using 4096."); self.PM_STARTUP_STATE_MAX_TOKENS = 4096
        # --- Max PM Work State Tokens ---
        try: self.PM_WORK_STATE_MAX_TOKENS: int = int(os.getenv("PM_WORK_STATE_MAX_TOKENS", "4096")); logger.info(f"Loaded PM_WORK_STATE_MAX_TOKENS: {self.PM_WORK_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_WORK_STATE_MAX_TOKENS, using 4096."); self.PM_WORK_STATE_MAX_TOKENS = 4096
        # --- Max PM Manage State Tokens ---
        try: self.PM_MANAGE_STATE_MAX_TOKENS: int = int(os.getenv("PM_MANAGE_STATE_MAX_TOKENS", "12288")); logger.info(f"Loaded PM_MANAGE_STATE_MAX_TOKENS: {self.PM_MANAGE_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_MANAGE_STATE_MAX_TOKENS, using 12288."); self.PM_MANAGE_STATE_MAX_TOKENS = 12288
        # --- Max WORKER Startup State Tokens ---
        try: self.WORKER_STARTUP_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_STARTUP_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded WORKER_STARTUP_STATE_MAX_TOKENS: {self.WORKER_STARTUP_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_STARTUP_STATE_MAX_TOKENS, using 1024."); self.WORKER_STARTUP_STATE_MAX_TOKENS = 1024
        # --- Max WORKER Decompose State Tokens ---
        try: self.WORKER_DECOMPOSE_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_DECOMPOSE_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded WORKER_DECOMPOSE_STATE_MAX_TOKENS: {self.WORKER_DECOMPOSE_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_DECOMPOSE_STATE_MAX_TOKENS, using 1024."); self.WORKER_DECOMPOSE_STATE_MAX_TOKENS = 1024
        # --- Max WORKER Work State Tokens ---
        try: self.WORKER_WORK_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_WORK_STATE_MAX_TOKENS", "16384")); logger.info(f"Loaded WORKER_WORK_STATE_MAX_TOKENS: {self.WORKER_WORK_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_WORK_STATE_MAX_TOKENS, using 16384."); self.WORKER_WORK_STATE_MAX_TOKENS = 16384
        # --- Max WORKER Wait State Tokens ---
        try: self.WORKER_WAIT_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_WAIT_STATE_MAX_TOKENS", "512")); logger.info(f"Loaded WORKER_WAIT_STATE_MAX_TOKENS: {self.WORKER_WAIT_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_WAIT_STATE_MAX_TOKENS, using 512."); self.WORKER_WAIT_STATE_MAX_TOKENS = 512
        # --- Max CG Verdict Tokens ---
        try: self.CG_MAX_TOKENS: int = int(os.getenv("CG_MAX_TOKENS", "4000")); logger.info(f"Loaded CG_MAX_TOKENS: {self.CG_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid CG_MAX_TOKENS, using 4000."); self.CG_MAX_TOKENS = 4000

        # --- Ollama Max Context Cap ---
        try: self.OLLAMA_MAX_CTX_CAP: int = int(os.getenv("OLLAMA_MAX_CTX_CAP", "32768")); logger.info(f"Loaded OLLAMA_MAX_CTX_CAP: {self.OLLAMA_MAX_CTX_CAP}")
        except ValueError: logger.warning("Invalid OLLAMA_MAX_CTX_CAP, using 32768."); self.OLLAMA_MAX_CTX_CAP = 32768

        # --- Ollama sampling penalties (Round 2y) ---
        # 2026-10-22 15:29 the Ollama server log showed every request sampled with repeat_penalty 1.15 (the framework's
        # old default) and presence_penalty 1.5 (the model's own default); both push the model away from tokens it has
        # just written - the repeated '<parameter=...>' / '</parameter>' tags of a tool call. Qwen3.5 is meant to run
        # coding / tool calling with presence_penalty 0 and repetition penalty 1.0. An empty value sends nothing (the
        # model's own default then applies).
        self.OLLAMA_REPEAT_PENALTY: Optional[float] = optional_float_setting("OLLAMA_REPEAT_PENALTY", "1.0")
        self.OLLAMA_PRESENCE_PENALTY: Optional[float] = optional_float_setting("OLLAMA_PRESENCE_PENALTY", "0.0")
        logger.info(f"Loaded OLLAMA_REPEAT_PENALTY: {self.OLLAMA_REPEAT_PENALTY}, "
                    f"OLLAMA_PRESENCE_PENALTY: {self.OLLAMA_PRESENCE_PENALTY} (None = the model's own default)")

        # --- Round 3u: requests with tools attached are streamed and collected ---
        # 2026-10-26 a proxy in front of the Ollama server (Cloudflare) answered 76 requests with "524" after 47-120 s: a
        # request with tools was sent with stream=false, so nothing came back until the whole reply was done. Streamed, the
        # reply flows while it is generated and is collected into the same events. false = the old non-streamed request.
        self.OLLAMA_STREAM_TOOL_CALLS: bool = os.getenv("OLLAMA_STREAM_TOOL_CALLS", "true").strip().lower() in ("1", "true", "yes", "on")
        logger.info(f"Loaded OLLAMA_STREAM_TOOL_CALLS: {self.OLLAMA_STREAM_TOOL_CALLS}")

        # --- Round 3y: the CODE MAP of a worker's task files (src/utils/code_map.py) ---
        # Rebuilt from the files at every call of a worker in worker_decompose / worker_work / worker_test: what each file of
        # its task exports (with parameters and return values), what stays inside the file, and the code its tests check.
        # The most characters it may add to a call (about 4 per token); 0 switches it off.
        try: self.CODE_MAP_MAX_CHARS: int = max(0, int(os.getenv("CODE_MAP_MAX_CHARS", "4200")))
        except ValueError: logger.warning("Invalid CODE_MAP_MAX_CHARS, using 4200."); self.CODE_MAP_MAX_CHARS = 4200
        logger.info(f"Loaded CODE_MAP_MAX_CHARS: {self.CODE_MAP_MAX_CHARS}")

        # --- Ollama Concurrency Limit ---
        try: self.OLLAMA_CONCURRENCY_LIMIT: int = int(os.getenv("OLLAMA_CONCURRENCY_LIMIT", "2")); logger.info(f"Loaded OLLAMA_CONCURRENCY_LIMIT: {self.OLLAMA_CONCURRENCY_LIMIT}")
        except ValueError: logger.warning("Invalid OLLAMA_CONCURRENCY_LIMIT, using 2."); self.OLLAMA_CONCURRENCY_LIMIT = 2

        # --- Load Initial Configurations using ConfigManager ---
        raw_config_data: Dict[str, Any] = {}
        try:
            raw_config_data = config_manager.get_config_data_sync()
            logger.info(f"Settings Init: Raw config data loaded. Keys: {list(raw_config_data.keys())}")
            print("Successfully loaded initial config via ConfigManager.")
        except Exception as e: logger.error(f"Failed to load initial config via ConfigManager: {e}", exc_info=True); raw_config_data = {}

        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])  # type: ignore[assignment]
        if not isinstance(self.AGENT_CONFIGURATIONS, list): logger.error("Config error: 'agents' not a list."); self.AGENT_CONFIGURATIONS = []

        # --- Log Loaded Config Summary ---
        if not self.AGENT_CONFIGURATIONS: print("Warning: No bootstrap agent configurations loaded.")
        else: print(f"Loaded bootstrap agent IDs: {[a.get('agent_id', 'N/A') for a in self.AGENT_CONFIGURATIONS]}")
        print(f"Model Tier setting (Effective): {self.MODEL_TIER}")

        self._ensure_projects_dir()
        # _check_required_keys() is now called AFTER ModelRegistry is instantiated below

    # --- Loading Prompts from prompts.yaml ---
    def _load_prompts_from_yaml(self):
        """Loads prompt templates from prompts.yaml."""
        self.PROMPTS = {}
        if PROMPTS_FILE_PATH.exists():
            try:
                with open(PROMPTS_FILE_PATH, 'r') as f:
                     loaded_prompts = yaml.safe_load(f)
                     if isinstance(loaded_prompts, dict):
                         self.PROMPTS = loaded_prompts
                     else:
                         logger.error(f"Error: {PROMPTS_FILE_PATH} did not contain a YAML dictionary. Prompts might fail.")
            except Exception as e:
                logger.error(f"Unexpected error when loading {PROMPTS_FILE_PATH}: {e}. Prompts might fail.")
        else:
             logger.error(f"CRITICAL ERROR: Prompts file not found at {PROMPTS_FILE_PATH}. Agent prompts will be empty!")
    # --- END Loading prompts ---
    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data if it doesn't exist."""
        try:
             self.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {self.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {self.PROJECTS_BASE_DIR}: {e}")

    # --- Loading Governance Principles from YAML ---
    def _load_governance_principles(self):
        """Loads governance principles from governance.yaml."""
        self.GOVERNANCE_PRINCIPLES: List[Dict[str, Any]] = [] # Ensure attribute is initialized
        try:
            if GOVERNANCE_FILE_PATH.exists():
                with open(GOVERNANCE_FILE_PATH, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) # Use safe_load for security
                    if isinstance(data, dict) and "principles" in data and isinstance(data["principles"], list):
                        self.GOVERNANCE_PRINCIPLES = usable_governance_principles(data["principles"])
                        logger.info(f"Successfully loaded {len(self.GOVERNANCE_PRINCIPLES)} governance principles from {GOVERNANCE_FILE_PATH}.")
                    else:
                        logger.error(f"Governance file {GOVERNANCE_FILE_PATH} has incorrect structure. Expected a dictionary with a 'principles' list. Using empty list.")
                        self.GOVERNANCE_PRINCIPLES = []
            else:
                logger.warning(f"Governance principles file not found at {GOVERNANCE_FILE_PATH}. Using empty list.")
                self.GOVERNANCE_PRINCIPLES = []
        except yaml.YAMLError as e:
            logger.error(f"Error decoding YAML from {GOVERNANCE_FILE_PATH}: {e}. Using empty list for governance principles.")
            self.GOVERNANCE_PRINCIPLES = []
        except Exception as e:
            logger.error(f"Unexpected error loading governance principles file {GOVERNANCE_FILE_PATH}: {e}. Using empty list.", exc_info=True)
            self.GOVERNANCE_PRINCIPLES = []
    # --- END Loading Governance Principles ---

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
        if self.SEARXNG_URL: logger.info(f"✅ SearXNG URL: Found at {self.SEARXNG_URL}")
        else: logger.info("ℹ️ INFO: SEARXNG_URL not set (Web Search uses fallback).")
        print("-" * 30)

    def get_provider_config(self, provider_name: str) -> Dict[str, Any]:
        """
        Gets the NON-KEY configuration (base_url, referer) for a provider.
        Keys are managed and added by the ProviderKeyManager.
        """
        config = {}
        provider_name = provider_name.lower()
        if provider_name == "openai": config['base_url'] = self.OPENAI_BASE_URL
        elif provider_name == "vllm": config['base_url'] = self.VLLM_BASE_URL
        elif provider_name == "openrouter":
             referer = self.OPENROUTER_REFERER or f"http://localhost:8000/{self.DEFAULT_PERSONA}" # Fallback referer
             config['base_url'] = self.OPENROUTER_BASE_URL
             config['referer'] = referer
        else:
             if provider_name: logger.debug(f"Req base config for unknown provider '{provider_name}'")
        return {k: v for k, v in config.items() if v is not None}

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
            # For generic local provider types, configuration depends on whether local discovery is permitted by MODEL_TIER.
            # Actual instance availability is checked by ModelRegistry.
            if self.MODEL_TIER in ["LOCAL", "ALL"]:
                logger.debug(f"is_provider_configured check for generic local type '{provider_name}': Returning True (MODEL_TIER is '{self.MODEL_TIER}', discovery permitted).")
                return True
            else:
                logger.debug(f"is_provider_configured check for generic local type '{provider_name}': Returning False (MODEL_TIER is '{self.MODEL_TIER}', local discovery not permitted).")
                return False
        else:
            # Check remote providers for a non-empty list of API keys.
            is_configured = provider_name in self.PROVIDER_API_KEYS and bool(self.PROVIDER_API_KEYS[provider_name])
            logger.debug(f"is_provider_configured check for remote '{provider_name}': Keys found = {is_configured}")
            return is_configured

    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific bootstrap agent's configuration dictionary by its ID."""
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 if agent_conf_entry.get('agent_id') == agent_id:
                     return agent_conf_entry.get('config', {})
        return None

    def get_formatted_allowed_models(self) -> str:
        """ Delegates to ModelRegistry. Requires discover_models() to have been run. """
        global model_registry # Ensure global is used
        return model_registry.get_formatted_available_models()


# --- Create Singleton Instances  ---
settings = Settings()

# --- Instantiate ModelRegistry *after* settings is created ---
model_registry = _ModelRegistry(settings)
print("Instantiated ModelRegistry singleton.")

# --- Call _check_required_keys AFTER ModelRegistry is available  ---
try:
    settings._check_required_keys()
    logger.info("Initial provider configuration check completed.")
except Exception as check_err:
     logger.error(f"Error running initial provider configuration check: {check_err}", exc_info=True)

# --- End of File ---
