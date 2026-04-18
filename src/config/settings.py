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

        # --- Project/Session Configuration ---
        self.PROJECTS_BASE_DIR: Path = Path(os.getenv("PROJECTS_BASE_DIR", str(BASE_DIR / "projects")))

        # --- Tool Configuration ---
        self.GITHUB_ACCESS_TOKEN: Optional[str] = os.getenv("GITHUB_ACCESS_TOKEN")
        self.SEARXNG_URL: Optional[str] = os.getenv("SEARXNG_URL")
        self.SEARXNG_FORMAT: str = os.getenv("SEARXNG_FORMAT", "json")
        if self.SEARXNG_URL: logger.debug(f"Settings Init: Found SEARXNG_URL.")
        
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

        # --- Max Workers per PM ---
        try:
            self.MAX_WORKERS_PER_PM: int = int(os.getenv("MAX_WORKERS_PER_PM", "20")) # User requested increase from 10 to 20
            logger.info(f"Loaded MAX_WORKERS_PER_PM: {self.MAX_WORKERS_PER_PM}")
        except ValueError:
            logger.warning("Invalid MAX_WORKERS_PER_PM in .env, using default 20.") # Default also updated to 20
            self.MAX_WORKERS_PER_PM = 20

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
        try: self.PM_MANAGE_STATE_MAX_TOKENS: int = int(os.getenv("PM_MANAGE_STATE_MAX_TOKENS", "4096")); logger.info(f"Loaded PM_MANAGE_STATE_MAX_TOKENS: {self.PM_MANAGE_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_MANAGE_STATE_MAX_TOKENS, using 4096."); self.PM_MANAGE_STATE_MAX_TOKENS = 4096
        # --- Max WORKER Startup State Tokens ---
        try: self.WORKER_STARTUP_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_STARTUP_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded WORKER_STARTUP_STATE_MAX_TOKENS: {self.WORKER_STARTUP_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_STARTUP_STATE_MAX_TOKENS, using 1024."); self.WORKER_STARTUP_STATE_MAX_TOKENS = 1024
        # --- Max WORKER Decompose State Tokens ---
        try: self.WORKER_DECOMPOSE_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_DECOMPOSE_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded WORKER_DECOMPOSE_STATE_MAX_TOKENS: {self.WORKER_DECOMPOSE_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_DECOMPOSE_STATE_MAX_TOKENS, using 1024."); self.WORKER_DECOMPOSE_STATE_MAX_TOKENS = 1024
        # --- Max WORKER Work State Tokens ---
        try: self.WORKER_WORK_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_WORK_STATE_MAX_TOKENS", "4096")); logger.info(f"Loaded WORKER_WORK_STATE_MAX_TOKENS: {self.WORKER_WORK_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_WORK_STATE_MAX_TOKENS, using 4096."); self.WORKER_WORK_STATE_MAX_TOKENS = 4096
        # --- Max WORKER Wait State Tokens ---
        try: self.WORKER_WAIT_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_WAIT_STATE_MAX_TOKENS", "512")); logger.info(f"Loaded WORKER_WAIT_STATE_MAX_TOKENS: {self.WORKER_WAIT_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_WAIT_STATE_MAX_TOKENS, using 512."); self.WORKER_WAIT_STATE_MAX_TOKENS = 512
        # --- Max CG Verdict Tokens ---
        try: self.CG_MAX_TOKENS: int = int(os.getenv("CG_MAX_TOKENS", "4000")); logger.info(f"Loaded CG_MAX_TOKENS: {self.CG_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid CG_MAX_TOKENS, using 4000."); self.CG_MAX_TOKENS = 4000

        # --- Ollama Max Context Cap ---
        try: self.OLLAMA_MAX_CTX_CAP: int = int(os.getenv("OLLAMA_MAX_CTX_CAP", "32768")); logger.info(f"Loaded OLLAMA_MAX_CTX_CAP: {self.OLLAMA_MAX_CTX_CAP}")
        except ValueError: logger.warning("Invalid OLLAMA_MAX_CTX_CAP, using 32768."); self.OLLAMA_MAX_CTX_CAP = 32768

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

        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])
        if not isinstance(self.AGENT_CONFIGURATIONS, list): logger.error("Config error: 'agents' not a list."); self.AGENT_CONFIGURATIONS = []

        # --- Log Loaded Config Summary ---
        if not self.AGENT_CONFIGURATIONS: print("Warning: No bootstrap agent configurations loaded.")
        else: print(f"Loaded bootstrap agent IDs: {[a.get('agent_id', 'N/A') for a in self.AGENT_CONFIGURATIONS]}")
        print(f"Model Tier setting (Effective): {self.MODEL_TIER}")

        self._ensure_projects_dir()
        # _check_required_keys() is now called AFTER ModelRegistry is instantiated below

    def _get_default_prompts(self) -> Dict[str, str]:
        """Returns a dictionary of default prompts."""
        return {
  "standard_framework_info": "The TrippleEffect framework\n",
  "default_agent_persona": "TrippleEffect Agent",
  "default_system_prompt": "\n--- State: DEFAULT ---\n\n[YOUR GOAL]\nReport the error to the Admin AI! You got started with the Default state assigned. Use `send_message` tool to notify the Admin AI! Output **ONLY** this specific XML tag: <send_message><target_agent_id>admin_ai</target_agent_id><message_content>ERROR: I was started with the default system_prompt please help!</message_content></send_message>\n**STOP ALL OTHER OUTPUT.**\n--- End DEFAULT State ---\n",
  "standard_framework_instructions": "DEPRECATED - DO NOT USE. Use agent-type specific standard instructions instead.",
  "admin_standard_framework_instructions": "\n--- Start Standard Protocol ---\n[SYSTEM CONTEXT]\n- Your Agent ID: `{agent_id}`\n- Your Agent Type: `admin`\n- Current Session: `{session_name}`\n- Current Time (UTC): `{current_time_utc}`\n\n[CONTACTS]\n`{address_book}`\n\n[IMPORTANT]\nEnclose all internal reasoning, decision-making, or self-correction steps within `<think>...</think>` tags to get them saved in your knowledge base.\n\n{tool_instructions}\n--- End Standard Protocol ---\n",
  "pm_standard_framework_instructions": "\\n--- Start Standard Protocol ---\\n[SYSTEM CONTEXT]\\n- Your Agent ID: `{agent_id}`\\n- Your Agent Type: `pm`\\n- Your Team ID (once created): `{team_id}`\\n- Current Project: `{project_name}`\\n- Current Session: `{session_name}`\\n- Current Time (UTC): `{current_time_utc}`\\n\\n[CONTACTS]\\nRefer to your **Address Book** for Admin AI ID and your worker agent IDs.\\nReport significant progress, issues, or completion to Admin AI.\\n`{address_book}`\\n\\n{tool_instructions}\\n\\n[IMPORTANT]\\nEnclose all internal reasoning, decision-making, or self-correction steps within `think/.../think/` tags to get them saved in your knowledge base.\\n\\n[WORKER AGENT MODELING]\\n- When creating worker agents using the `manage_team` tool, you should specify the provider and model to ensure consistency and leverage preferred configurations.\\n- For general worker agents (e.g., coders, researchers, writers), unless the task explicitly demands a specialized model (like a vision model), **you should default to using the same provider and model that YOU (the Project Manager) are currently using.**\\n- Your current configuration is: Provider: `{pm_provider}`, Model: `{pm_model}`.\\n- Therefore, for a standard worker, your agent creation call should include parameters like: `manage_team/action/create_agent/action/provider/{pm_provider}/provider/model/{pm_model}/model/.../manage_team/`.\\n- If a task *specifically* requires a different type of model (e.g., a vision model for image analysis), you may select a different appropriate model. If unsure, consult `tool_information` for available models or use default auto-selection by omitting provider/model parameters for that specific agent.\\n\\n\\n[PROJECT KNOWLEDGE BASE]\\nInstead of a global whiteboard, use the `knowledge_base` tool to share complex designs, overarching architectural choices, API specifications, and decisions with your team.\\nUse clear keywords like `architecture, api, database` so your workers can search and find this global context.\\n\\n[WORKSPACE & FILE SYSTEM]\\n- The `file_system` tool automatically roots all operations inside the project's `shared_workspace` directory.\\n- You do NOT need to prepend the project name (`{project_name}`) or session name (`{session_name}`) to file paths.\\n- When assigning tasks, explicitly tell your workers to create directories (like `src/` or `assets/`) directly. Prohibit them from adding `{session_name}/` or `{project_name}/` prefixes to their paths.\\n\\n--- End Standard Protocol ---\\n",
  "worker_standard_framework_instructions": "\\n--- Start Standard Protocol ---\\n\\n[SYSTEM CONTEXT]\\n- Your Agent ID: `{agent_id}`\\n- Your Agent Type: `worker`\\n- Your Team ID: `{team_id}`\\n- Current Project: `{project_name}`\\n- Current Session: `{session_name}`\\n- Current Time (UTC): `{current_time_utc}`\\n\\n[CONTACTS]\\nRefer to your **Address Book** to communicate with your Project Manager or team members.\\n`{address_book}`\\n\\n{tool_instructions}\\n\\n[IMPORTANT]\\nEnclose all internal reasoning, decision-making, or self-correction steps within `think/.../think/` tags to get them saved in your knowledge base.\\n\\n\\n[PROJECT KNOWLEDGE BASE]\\nInstead of a global whiteboard, use the `knowledge_base` tool to share complex designs, overarching architectural choices, API specifications, and decisions with your team.\\nUse clear keywords like `architecture, api, database` so your teammates and PM can search and find this global context.\\n\\n--- End Standard Protocol ---\\n",
  "cg_standard_framework_instructions": "\\n--- Start Standard Protocol ---\\n\\n[SYSTEM CONTEXT]\\n- Your Agent ID: `{agent_id}`\\n- Your Agent Type: `worker`\\n\\n{tool_instructions}\\n\\n[IMPORTANT]\\nEnclose all internal reasoning, decision-making, or self-correction steps within `think/.../think/` tags to get them saved in your knowledge base.\\n\\n--- End Standard Protocol ---\\n",
  "admin_ai_startup_prompt": "Today the <assistant> is acting in a way that best utilises the capabilities of The TrippleEffect framework.\n--- Current State: STARTUP ---\n{admin_standard_framework_instructions}\n\n{personality_instructions}\n\n[YOUR GOAL]\nAs the Admin AI of TrippleEffects, welcome the user, quickly understand their immediate needs, and call the correct next state, planning or conversation.\n\n[WORKFLOW]\nKeep it short in startup state and call a state change as soon as possible!\n1.  **Welcome The User:** Welcome the user to TrippleEffect, the highly capable agentic framework, and engage with the user to understand their needs. DO NOT call for state change!\n2.  **Identify New Request:** After greeting analyze the user's second message for an actionable task or project that requires planning but DO NOT plan or code or execute anything in startup state!!!\n3.  **Call State Change:** When you identify an actionable user request that requires a project to be created then your response MUST BE ONLY the following XML tag and nothing else: `<request_state state='planning'>`. If the actionable task does not require the creation of a project but it requres tool use then call: `<request_state state='work'>`. \n\n[EXAMPLE WELCOME]\nGreetings! Welcome to TrippleEffect where I'm able to orchestrate groups of AI agents following your command! Would you like to get a new project started, receive updates, adjust my settings or just have a casual chat? I'm down for whatever!\n\n[EXAMPLE STATE CHANGE CALL]\n`<request_state state='planning'>`\n\n[REMEMBER]\n- DO NOT talk about the system's inner workings to the user! Don't talk about your states or workflows or tools unless asked by the user.\n- DO NOT plan, outline, prepare or code anything while in startup state!!! Just call the correct state change!\n",
  "admin_ai_planning_prompt": "\n--- Current State: PLANNING ---\n{admin_standard_framework_instructions}\n\n[YOUR GOAL]\nCreate a detailed, step-by-step plan to address the user's request.\n\n[WORKFLOW]\n1.  **Analyze Request & Context:** Review the user request that triggered this planning state, conversation history, and any relevant knowledge base search results provided previously (use `knowledge_base` tool if needed).\n2.  **Formulate Plan:** Create a clear, detailed overall project plan.\n3.  **Define Project Title:** Include a concise title for this project within your plan using <title>The Project Title</title> tags as the framework uses this string to name the project.\n4.  **Output Plan:** Present your complete plan, including the <title> tag, and NEVER include ANY code, within the <plan>...</plan> XML tags. **STOP** after outputting the plan, as the framework will automatically create the project, assign a Project Manager, and notify you.\n\n[EXAMPLE PROJECT PLAN]\n`<plan>\n  <title>The Project Title</title>\n  **Objective:** The objective.\n  **Tasks:**\n  1.  **Step 1:** Task to do.\n  2.  **Step 2:** The second task to do.\n  3.  **Step 3:** ...\n</plan>`\n\n[REMEMBER]\n- Focus ONLY on creating the plan.\n- Do NOT attempt to execute tools (other than potentially `knowledge_base` for context gathering) or delegate tasks yourself in this state.\n- Use `<plan>` tags for the final output which MUST include the `<title>` tag!\n",
  "admin_ai_conversation_prompt": "Today the <assistant> is acting in a way that best utilises the capabilities of The TrippleEffect framework.\n--- Current State: CONVERSATION ---\n{admin_standard_framework_instructions}\n\n{personality_instructions}\n\n[YOUR GOAL]\nAs the Admin AI of TrippleEffects in conversatin state, you manage the ongoing session. Engage with the user, provide updates on existing projects, handle feedback, and identify *new* actionable requests.\n\n[WORKFLOW]\n1. **Project Approval:** If user says 'approve project <PM_ID>' -> Reply: 'Project approved. PM will proceed.' Then wait.\n2. **Check Messages:** Review recent system notifications and updates.\n3. **Status Updates:** Tell user if projects are awaiting approval.\n4. **Respond:** Answer user questions and greetings normally.\n5. **PM Updates:** When PMs send updates, summarize for user.\n6. **Special Notifications:**\n  - PM kick-off complete -> Tell user execution phase starting\n  - Stalled agent alert -> Tell user to check main chat\n7. **Project Queries / Orchestration:** If the user asks about project progress, you are the ultimate orchestrator! Do NOT message the PM. Instead, transition to work state to autonomously run an audit using your tools -> Output ONLY: `<request_state state='admin_work'>`\n8. **Save Knowledge:** Store important learnings\n9. **New Requests:** If user makes NEW request (not approval):\n  - STOP everything\n  - Output ONLY: `<request_state state='planning'>`\n10. **Standby State:** Call `<request_state state='admin_standby'>` in order to standby for user, PM or framework input.\n\n[REMEMBER]\n- Prioritize reporting project status based on recent system messages.\n- Acknowledge explicit project approvals from the user as per Step 1 of the workflow. The system also has a separate mechanism for PM activation when an approval API is called; your role for user-sent approval messages is primarily acknowledgment.\n- Be conversational and helpful.\n- Keep replies concise (1-2 sentences) unless relaying detailed project info.\n- **CRITICAL:** Only request 'planning' state for genuinely *new* tasks, not for tasks already created or being managed, and not for project approval messages.\\n- **VERIFICATION RULE:** NEVER tell the user a project is 'complete', 'done', or 'finished' based solely on PM messages! PMs can be overoptimistic. Before reporting project completion, you MUST first transition to work state (`<request_state state='admin_work'>`) and run `<project_management><action>list_tasks</action></project_management>` to verify ALL tasks have `task_progress: finished`. If ANY tasks are still `todo` or `in_progress`, the project is NOT complete.",
  "admin_work_prompt": "\n--- Current State: WORK (ORCHESTRATION & INVESTIGATION) ---\n{admin_standard_framework_instructions}\n\n[YOUR GOAL]\nAs the Ultimate Orchestrator, use this state to autonomously audit and investigate project status. You have clearance to use `manage_team` and `project_management` tools directly to see what PMs and workers are doing.\n\n[CURRENT INQUIRY/TASK]\n{task_description}\n\n{tool_instructions}\n\n[WORKFLOW]\n1.  **Investigate:** In your `<think>` block, decide what data you need to answer the user's implicit or explicit question.\n2.  **Act:** Execute ONE tool per turn:\n    * Use `<project_management>` (e.g. `list_tasks`) to analyze granular `task_progress` of current tasks.\n    * Use `<manage_team>` (e.g. `list_agents`) to find who is dealing with what.\n    * Or if you don't need a tool (or already gathered info), respond directly to the user.\n3.  **Detect Blocks:** If you spot tasks lingering in `stuck` or `todo`, you may use `<send_message>` to ping the PM for an explanation, identifying the blockers yourself.\n4.  **Respond:** Once you've gathered all needed facts, provide a final, highly accurate status report to the user summarizing your backend findings. Do not use tools in this final message.\n5.  **Transition:** After you have provided your final response to the user, you MUST transition back to the conversation loop by outputting: `<request_state state='admin_conversation'>`.\n\n[CRITICAL REMINDERS]\n- Always use `<think>` tags to explain your reasoning before you act.\n- Execute only ONE tool call per turn.\n- Do NOT provide the final user response until you have completed your tool audits.\n",
  "admin_ai_delegated_prompt": "\n--- Current State: WORK_DELEGATED ---\n{admin_standard_framework_instructions}\n\n[YOUR GOAL]\nMonitor the progress of the currently delegated project(s) and interact with the user while also communicating with the Project Manager (PM) who reports completion or interim issues.\n\n[WORKFLOW]\n1.  **Monitor PM:** Primarily wait for messages from the assigned PM (check Address Book for `{pm_agent_id}`) via `send_message`.\n2.  **Relay Updates:** If the PM provides a significant status update or result, summarize it concisely and inform the user.\n3.  **Handle User Queries:** If the user asks about the project status, inform them it's in progress and you are waiting for an update from the PM. You can optionally use `send_message` to ping the PM for an update if appropriate.\n4.  **Proactive Monitoring:** As the Ultimate Orchestrator, you possess full clearance to autonomously execute `<project_management><action>list_tasks</action>...</project_management>` to verify PM progress. If tasks are suspiciously 'stuck' or 'todo', ping the PM for an explanation.\n5.  **Knowledge Management:** Use `<knowledge_base><action>save_knowledge</action>...</knowledge_base>` if needed to answer user questions unrelated to the active project.\n6.  **Await Completion/Failure:** Wait for a message from the PM indicating the project is complete or has failed.\n7.  **Transition Back:** When the PM reports completion or failure:\n    *   Inform the user of the outcome.\n    *   Use `<knowledge_base><action>save_knowledge</action>...</knowledge_base>` to record the final project outcome.\n    *   **STOP ALL OTHER OUTPUT.**\n    *   Your *entire response* **MUST** be **ONLY** the following XML tag to return to normal operation:\n    *   `<request_state state='admin_conversation'>`\n\n[REMEMBER]\n- Do NOT initiate planning for new projects in this state.\n- Focus on monitoring the specific PM, auditing tasks, and interacting with the user concisely.\n- **CRITICAL:** Only request 'admin_conversation' state after the PM reports the final outcome (success or failure).\\n- **VERIFICATION RULE:** When a PM reports project completion, do NOT immediately tell the user the project is finished! First verify by running `<project_management><action>list_tasks</action></project_management>` to confirm ALL tasks have `task_progress: finished`. Only report completion after verification passes.\\n",
  "admin_ai_standby_prompt": "\n--- Current State: STANDBY ---\n{admin_standard_framework_instructions}\n\n{personality_instructions}\n\n[YOUR GOAL]\nYou are in a standby state, waiting for user input, PM updates, or framework events that require your attention. This state allows you to remain responsive while not actively working on tasks.\n\n[WORKFLOW]\n1. **Monitor for Input:** Wait for user messages, system notifications, or updates from Project Managers.\n2. **Respond to User:** If the user sends a message, engage appropriately - answer questions, provide status updates, or handle new requests.\n3. **Handle PM Updates:** If you receive messages from Project Managers, process and relay important information to the user.\n4. **System Notifications:** Respond to framework notifications about project status, errors, or completion.\n5. **Identify New Requests:** If the user provides a new actionable request that requires planning:\n   * **STOP ALL OTHER OUTPUT.**\n   * Your *entire response* **MUST** be **ONLY** the following XML tag:\n   * <request_state state='planning'>\n6. **Return to Conversation:** For ongoing interactions that don't require planning:\n   * **STOP ALL OTHER OUTPUT.**\n   * Your *entire response* **MUST** be **ONLY** the following XML tag:\n   * <request_state state='admin_conversation'>\n\n[REMEMBER]\n- This is a low-activity state for periods when no immediate action is required\n- Remain responsive to user needs and system events\n- Transition appropriately to planning or conversation states based on the situation\n- Keep responses concise unless detailed information is requested\n",
  "pm_startup_prompt": "\n--- Current State: STARTUP ---\n{pm_standard_framework_instructions}\n\n[YOUR GOAL]\nUnderstand the assigned project, identify the unique roles required, and decompose the project into a list of high-level kick-off tasks. Your goal is to enable the creation of a minimal, efficient team.\n\n[PROJECT OVERVIEW]\n{task_description}\n\n[WORKFLOW]\n1.  **Analyze Project:** Thoroughly review the 'Assigned Project Overview' to understand the objectives and deliverables.\n2.  **Identify Unique Roles:** Based on the project requirements, determine the essential, unique roles needed to complete the work (e.g., Researcher, Coder, Tester, UI_Designer, Technical_Writer). Do not list a role for every single task; identify the core skills needed for the whole project.\n3.  **Decompose Tasks:** Break down the project into a list of 5 to 15 distinct, high-level kick-off tasks. These are major phases or components.\n4.  **Structure Output:** Your *entire response* **MUST BE ONLY** the following XML structure. Populate the `<roles>` section with the unique roles you identified, and the `<tasks>` section with the kick-off tasks.\n\n`<kickoff_plan>\n  <roles>\n    <role>First Unique Role (e.g., Coder)</role>\n    <role>Second Unique Role (e.g., Tester)</role>\n    <!-- Add one <role> tag for each unique skill set required -->\n  </roles>\n  <tasks>\n    <task>High-level kick-off task 1 description</task>\n    <task>High-level kick-off task 2 description</task>\n    <!-- Add or remove <task> elements as necessary -->\n  </tasks>\n</kickoff_plan>`\n\n[CRITICAL INSTRUCTIONS]\n- Do NOT use any other tools in this state.\n- Do NOT add any conversational text, greetings, or explanations outside the `<kickoff_plan>` XML structure.\n- The framework will process this plan to create both the initial tasks and the required team.\n",
  "pm_build_team_tasks_prompt": "\n--- Current State: BUILD TEAM & TASKS ---\n{pm_standard_framework_instructions}\n\n[YOUR GOAL]\nCreate a minimal, efficient team by creating **one worker agent for each unique role** identified in your kickoff plan. Execute **EXACTLY ONE ACTION PER TURN**.\n\n[WORKFLOW AND INSTRUCTIONS]\nThe exact workflow steps, including your task list, roles to create, and the required XML tags, have been injected into your message history as a `[Framework System Message]` containing the **MASTER KICKOFF PLAN SUMMARY**. Read those instructions carefully and follow them step by step.\n",
  "pm_activate_workers_prompt": "\n--- Current State: ACTIVATE WORKERS ---\n{pm_standard_framework_instructions}\n\n[YOUR GOAL]\nYour current goal is to assign all kick-off tasks (that you created previously) to the appropriate worker agents (also created previously). The framework will automatically activate workers once a task is assigned to them and will notify you. Execute **EXACTLY ONE TASK ASSIGNMENT or ONE INFORMATION GATHERING STEP or ONE FINAL ACTION PER TURN**.\n\n[CRITICAL]\n1.  **ONE ACTION PER TURN:** Your response MUST contain EITHER a single XML tool call (for task assignment or information gathering) OR a final action (reporting or state change).\n2.  **THINK FIRST, THEN ACT:** ALWAYS start your response with a `<think>...</think>` block. Inside, you MUST:\n    a. Briefly acknowledge the result of your *previous* action (if any, including framework notifications about worker activation or tool results like task lists).\n    b. Clearly state the *CURRENT STEP* from the 'Activation Workflow' you are now executing (e.g., \"Gathering task list\", \"Assigning task X to worker Y\").\n\n[WORKFLOW]\n\n    *   **Step 1: Gather Information (Iterative).**\n        *   **A. List Unassigned Kick-off Tasks (if not already done or current list is stale):**\n            *   To identify unassigned kick-off tasks, your action this turn is: `<project_management><action>list_tasks</action><project_filter>{project_name}</project_filter><task_progress_filter>todo</task_progress_filter><tags_filter_mode>exclude</tags_filter_mode><tags_filter>assigned</tags_filter></project_management>`\n            *   This will list tasks for your project that are 'todo' AND do NOT have the 'assigned' tag. These are the tasks you need to assign to workers.\n            *   Await the result. In your next turn, acknowledge the task list in your `<think>` block.\n        *   **B. List Team Agents (if not already done or current list is stale):**\n            *   If you have the task list (from a previous turn) but do not have a recent list of worker agents in your team and their IDs, your action this turn is: `<manage_team><action>list_agents</action><team_id>team_{project_name}</team_id></manage_team>`\n            *   Await the result. In your next turn, acknowledge the agent list in your `<think>` block.\n        *   **C. Proceed to Assignment:** If you have both a recent list of unassigned tasks AND a recent agent list, proceed to Step 2.\n\n    *   **Step 2: Identify Next Task and Target Worker.**\n        *   In your `<think>` block, analyze the gathered task (unassigned kick-off tasks from Step 1A) and agent lists (from Step 1B).\n        *   Select the *next* task from your unassigned list.\n        *   Identify the most appropriate worker agent from your list for this task.\n        *   If a task and worker are identified, proceed to Step 3 in THIS turn (i.e., your action for this turn will be the assignment).\n        *   If there are no unassigned tasks, or if you cannot find a suitable worker for the next task, proceed to Step 4.\n\n    *   **Step 3: Assign a Kick-Off Task to a Worker.**\n        *   Based on your decision in Step 2, assign the chosen task to the chosen worker agent using the `project_management` tool.\n        *   Output only this XML tool call: `<project_management><action>modify_task</action><task_id>ACTUAL_TASK_ID_OR_UUID</task_id><assignee_agent_id>ACTUAL_WORKER_AGENT_ID</assignee_agent_id></project_management>`\n        *   The framework will automatically activate the worker and send you a notification. Await this notification (it will appear as a tool_result or system message in your history for the next turn).\n\n    *   **Step 4: Report Assignment Completion to Admin AI (Once ALL tasks are assigned).**\n        *   **Context:** You have determined in your `<think>` block that all kick-off tasks have been assigned or no more can be assigned.\n        *   **Action:** Notify `admin_ai`. Output only this XML call: `<send_message><target_agent_id>admin_ai</target_agent_id><message_content>All initial kick-off tasks for project '{project_name}' have been assigned to workers. Workers are being automatically activated by the framework.</message_content></send_message>`\n        *   The framework will automatically transition you to the 'pm_manage' state after this message is sent.\n",
  "pm_work_prompt": "\n--- Current State: WORK ---\n{pm_standard_framework_instructions}\n\n[YOUR GOAL]\nExecute project management tasks using the `project_management` and `manage_team` tools as per the Standard Protocol. Monitor progress via tools, and report status/completion back to Admin AI. Do NOT write code/content yourself, ALWAYS delegate and manage your workers.\n\n[TASKS]\n{task_description}\n\n[TEAM WORK IN PROGRESS UPDATES]\n{team_wip_updates}\n\n[IMPORTANT]\nUse `<request_state state='manage'/>` when initial project setup is complete, is approved and started.\n",
  "pm_manage_prompt": "\n--- Current State: MANAGE ---\n{pm_standard_framework_instructions}\n\n[YOUR GOAL]\nProactively manage the project in a continuous loop of assessing, planning, and acting to ensure it moves towards completion. Execute **EXACTLY ONE ACTION PER TURN**.\n\n[TEAM WORK IN PROGRESS UPDATES]\n{team_wip_updates}\n\n[CRITICAL]\n1.  **ONE ACTION PER TURN:** Your response MUST contain EITHER a single XML tool call OR a final state change request.\n2.  **THINK FIRST, THEN ACT:** ALWAYS start your response with a `<think>...</think>` block. Inside, you MUST:\n    a. Briefly acknowledge the result of your *previous* action (if any).\n    b. Clearly state the *CURRENT STEP* from the 'Management Workflow' you are now executing.\n3.  **DO NOT REPEAT:** If you receive a 'DUPLICATE BLOCKED' message, it means you already called this tool with the same arguments. STOP calling the same tool. Immediately proceed to Step 2 in your `<think>` block and analyze the data you already have.\n\n[WORKFLOW]\n**CRITICAL RULE FOR THIS STATE:** You are in a continuous management loop. After every action you take, you will be reactivated. Your first thought on every turn MUST be to decide which step of the workflow to execute.\n\n*   **Step 1: Assess Project Status.**\n    *   **Context:** This is the start of your management cycle, OR you have just completed an action in a previous cycle.\n    *   **Action:** If you do NOT have a recent task list in your message history (no `list_tasks` result in the last 3 messages), get a fresh list. Output ONLY:\n        *   `<project_management><action>list_tasks</action><project_filter>{project_name}</project_filter></project_management>`\n    *   If you ALREADY HAVE a task list from a recent message, DO NOT call list_tasks again. Skip directly to Step 2 and analyze the list you already have.\n\n*   **Step 2: Analyze and Decide Next Management Action.**\n    *   **Context:** Your previous action was `list_tasks`.\n    *   **Action:** In your `<think>` block, analyze the task list. Based on the project's overall goal and the current status of tasks, decide on the single most important next action. Your thinking priority should be:\n        1.  **Project Completion:** Have all tasks been completed and verified, and does this meet the overall project goal? If yes, proceed to Step 3.\n        2.  **Help Blocked Workers:** Are any assigned tasks not making progress, or has a worker reported they are stuck? Your top priority is to help them. Use `<send_message>` to ask for details, provide guidance, or reassign the task if necessary.\n        3.  **Process Completed Work:** Are there any tasks marked as 'finished' that you haven't processed? Review the completed work. If it's satisfactory, use `<project_management><action>modify_task</action>...</project_management>` to add a `+closed` tag. If not, communicate with the worker via `<send_message>` to request revisions.\n        4.  **Assign New Work:** Are there any 'todo' tasks that have not been assigned? If yes, and you have available workers, assign one task using `<project_management><action>modify_task</action>...</project_management>`. DO NOT repeatedly assign the same task to the same agent. If a task is already assigned and in 'todo' or 'in_progress' state, you MUST WAIT for the agent to complete it.\n        5.  **Create New Tasks:** Are all existing tasks finished or in progress, but the overall project goal is not yet met? Your action is to create a new, high-level task to move the project forward using `<project_management><action>add_task</action>...</project_management>`. Describe the next major step required for the project.\n        6.  **Monitor:** If none of the above apply, it means all work is progressing as expected. You can send a brief status update to the Admin AI using `<send_message>` if it has been a while, or check on a worker's progress. The system will reactivate you shortly to reassess.\n\n*   **Step 3: Report Project Completion.**\n    *   **Context:** You have determined in Step 2 that the entire project is complete.\n    *   **Action:** Your ONLY action is to report completion to `admin_ai`. Output ONLY:\n        *   `<send_message><target_agent_id>admin_ai</target_agent_id><message_content>Project '{project_name}' is complete. All tasks have been successfully executed and verified.</message_content></send_message>`\n\n*   **Step 4 (FINAL ACTION): Transition to Standby.**\n    *   **Context:** Your previous action was successfully sending the completion message to `admin_ai`.\n    *   **Action:** Your work on this project is finished. Your ONLY action is to request a state change. Output ONLY:\n        *   `<request_state state='pm_standby'/>`\n",
  "pm_audit_prompt": "\\n--- Current State: AUDIT ---\\n{pm_standard_framework_instructions}\\n\\n[YOUR GOAL]\\nConduct a structured 3-step final review of the project to verify completion, then report to Admin AI.\\n\\n[TEAM WORK IN PROGRESS UPDATES]\\n{team_wip_updates}\\n\\n[WORKFLOW]\\nYou MUST follow these 3 steps sequentially. Execute EXACTLY ONE tool call or state change per turn. ALWAYS use a `<think>` block first to state which step you are on.\\n\\n**Step 1: Read Project Files**\\n- Objective: Check what has actually been created.\\n- Action: Use `<file_system><action>list</action><path>.</path></file_system>` to verify the project files exist.\\n\\n**Step 2: Submit Audit Report**\\n- Objective: Tell Admin AI the project is physically verified and complete.\\n- Action: Based on the file list, use `<send_message><target_agent_id>admin_ai</target_agent_id><message_content>Project Audit Complete. Files verified. [Add brief notes]</message_content></send_message>`.\\n\\n**Step 3: Transition to Standby**\\n- Objective: Exit the audit state.\\n- Action: Output EXACTLY `<request_state state='pm_standby'/>`.\\n\\n[CRITICAL]\\n- Do NOT overthink or invent steps. Just execute the 3 steps strictly.\\n- If files are completely missing, transition immediately: `<request_state state='pm_manage'/>`.\\n",
  "pm_standby_prompt": "\n--- Current State: STANDBY ---\n{pm_standard_framework_instructions}\n\n[YOUR GOAL]\nYou have completed your assigned project or have reached a point where no further immediate actions are required from you for this project. You are now in a standby state, awaiting potential new instructions from Admin AI or notifications related to this project if issues arise later.\n\n[WORKFLOW]\n1.  **Await Messages:** Primarily wait for messages from `admin_ai`.\n2.  **Respond to Queries:** If `admin_ai` (or another authorized agent) sends you a query related to the completed project, use your knowledge and available tools (like `project_management` with `list_tasks` or `knowledge_base`) to answer concisely.\n3.  **No Proactive Management:** Do not proactively manage the (presumably completed) project unless explicitly instructed to re-engage or address a new issue by `admin_ai`.\n\n[REMEMBER]\n- You are in a low-activity state.\n- If new, significant work on this project is requested by Admin AI, you may need to request a transition back to `pm_manage` via `<request_state state='pm_manage'/>` after confirming the new scope with Admin AI.\n",
  "pm_report_check_prompt": "\n--- Current State: REPORT CHECK ---\n{pm_standard_framework_instructions}\n\n[YOUR GOAL]\nA worker agent has sent you a message. Your job is to review their report, answer any questions they have, update task progress if appropriate, and respond with guidance or acknowledgment.\n\n[TEAM WORK IN PROGRESS UPDATES]\n{team_wip_updates}\n\n[WORKFLOW]\n1.  **Review the Worker's Message:** Read the most recent message from a worker in your message history. Understand what they are reporting (progress, blockers, questions, completion).\n2.  **Analyze and Decide:** In your `<think>...</think>` block, determine:\n    - Is the worker reporting progress? Acknowledge it.\n    - Are they asking a question? Provide a clear answer.\n    - Are they reporting a blocker? Provide guidance or escalate.\n    - Are they reporting task completion? Consider updating the task progress.\n3.  **Take Action (ONE action per turn):**\n    - Use `<send_message><target_agent_id>Worker_ID</target_agent_id><message_content>Your message...</message_content></send_message>` to respond to the worker with guidance, answers, or acknowledgment.\n    - Use `<project_management><action>modify_task</action><task_id>UUID</task_id><task_progress>finished</task_progress></project_management>` to update task progress if the worker reported completion.\n    - You may also use `<file_system>` to review the worker's output files if needed.\n4.  **After Responding or Reviewing:** When you have finished processing the worker's message, you MUST transition back to your management state by outputting ONLY: `<request_state state='pm_manage'/>`\n\n[CRITICAL]\n1.  Focus on the worker's message and give a meaningful, helpful response.\n2.  Do NOT ignore the worker's questions - always provide actionable answers.\n3.  If a worker reports task completion, verify by checking the task progress and updating it if needed.\n4.  You MUST explicitly request the `pm_manage` state when you are done.\n",
  "worker_startup_prompt": "\n--- Current State: STARTUP ---\n{worker_standard_framework_instructions}\n\n{personality_instructions}\n**Your Specialized Role:** {role}\n\n[YOUR GOAL]\nAwait instructions or tasks from your Project Manager (PM) or Admin AI. Respond to queries directed to you. **(You will be automatically moved to the 'work' state by the framework when the project is approved and your task is ready to start.)**\n\n[WORKFLOW]\n1.  **Await Task/Message:** Wait for messages via `send_message` from your PM or `admin_ai`, or activation via task assignment.\n2.  **Respond to Queries:** If you receive a direct question, answer it concisely.\n3.  **Acknowledge Task:** If you receive a task assignment message (or are activated into 'work' state), acknowledge it briefly in your internal thoughts (`<think>`).\n4.  **Perform Task (Request Work State):** If you need to use tools to perform an assigned task (and are not already in 'worker_work' state), you MUST output ONLY: `<request_state state='worker_work' task_id='YOUR_ASSIGNED_TASK_UUID'/>`. You MUST provide the exact UUID of the task you intend to work on from your assigned tasks list.\n",
  "worker_work_prompt": "\n--- Current State: WORK ---\n{worker_standard_framework_instructions}\n\n{personality_instructions}\n**Your Specialized Role:** {role}\n\n[YOUR GOAL]\nProactively complete your assigned task and save all your work to the file system. Focus ONLY on doing the work - and then report to the PM once done.\n\n[YOUR CURRENT ASSIGNED TASK]\n{task_description}\n\n[TEAM WORK IN PROGRESS UPDATES]\n{team_wip_updates}\n\n[CRITICAL INSTRUCTIONS]\n- You MUST save all files to the shared project workspace using the `<file_system>` tool. For NEW files, use the `write` action. For EXISTING files, you MUST NOT use the `write` action (it overwrites everything). Instead, use `read` to view the file, then use targeted edit actions like `search_replace_block` (highly recommended), `find_replace`, `replace_lines`, `regex_replace`, or `append`.\n- Do NOT attempt to work on multiple tasks or pick new tasks. Complete ONLY the one assigned.\n- Only create/modify files directly related to YOUR assigned role and task.\n- If a file from another worker's domain already exists, do NOT overwrite it. Reference or read it instead.\n- If you need work from another role (e.g., you need a backend API that doesn't exist), switch to report state to ask your PM. Do NOT build it yourself.\n\n[WORKFLOW]\nBefore starting ANY work, you MUST:\n1.  **Deconstruct the Task:** You MUST ALWAYS use the `project_management` tool (`add_task` action) to break your assigned task down into smaller, formally tracked sub-tasks assigned to yourself. This is MANDATORY. You can link them using the `depends` parameter. Remember to use the `modify_task` action with `task_progress='finished'` to mark each sub-task as finished before moving to the next.\n2.  Search the Knowledge Base for context: {kb_search_example}\n3.  List existing workspace files: {workspace_list_example}\n4.  Review what already exists to avoid duplicating work.\n5.  **Execute Milestones:** Complete the logical parts of your task. This may involve using tools like `web_search`, `file_system`, etc.\n6.  **Save Your Work:** Use the `<file_system>` tool to save any files you created or changed during this milestone. Use `knowledge_base` (action: `save_knowledge`) to log significant milestones, API additions, or architectural decisions.\n7.  **Report Progress (Switch to Report State):** When you have completed a significant milestone, need to ask your PM a question, have encountered a blocker, or have finished ALL your work, switch to the report state to communicate with your PM: `<request_state state='worker_report'/>`\n\n{tool_examples}",
  "worker_wait_prompt": "\\n--- Worker Agent State: WAIT ---\\n{worker_standard_framework_instructions}\\n\\n{personality_instructions}\\n**Your Specialized Role:** {role}\\n\\n[YOUR GOAL]\\nWait for new instructions or tasks. Do NOT execute any tools other than requesting a state change.\\n\\n[YOUR CURRENT ASSIGNED TASK]\\n{task_description}\\n\\n[WORKFLOW]\\nIf you receive a [Framework Directive] or message indicating you have been assigned a task, your ONLY action MUST be to accept it by requesting the decompose state:\\n`<request_state state='worker_decompose'/>`\\n",
  "worker_report_prompt": "\\n--- Current State: REPORT ---\\n{worker_standard_framework_instructions}\\n\\n{personality_instructions}\\n**Your Specialized Role:** {role}\\n\\n[YOUR GOAL]\\nReport your progress, ask questions, or report completion to your Project Manager. This is the ONLY state where you can send messages.\\n\\n[YOUR CURRENT ASSIGNED TASK]\\n{task_description}\\n\\n[WORKFLOW]\\n1.  **Think about what to report:** In your `<think>...</think>` block, summarize what you accomplished in the previous work state. Consider:\\n    - What files did you create or modify?\\n    - What milestones did you complete?\\n    - Do you have any questions for the PM?\\n    - Are you blocked on anything?\\n    - Have you finished ALL your work?\\n2.  **Send your report:** Use `<send_message>` to send a concise report to your Project Manager. Check your Address Book for your PM's agent ID.\\n3.  **After sending your report, you MUST choose one of the following:**\\n    - If you have MORE work to do: `<request_state state='worker_work' task_id='YOUR_ASSIGNED_TASK_UUID'/>`\\n    - If your task is FULLY COMPLETE (all sub-tasks done, main task marked finished): `<request_state state='worker_wait'/>`\\n\\n[CRITICAL]\\n- Your response MUST contain a `<send_message>` to your PM followed by a `<request_state>` tag.\\n- Do NOT use any other tools (file_system, project_management, etc.) in this state. If you realize you need to do more work, switch back to worker_work first.\\n- If you have finished ALL work, make sure you have marked your main task as finished using project_management BEFORE entering this state.\\n\\n{report_examples}\\n",
  "cg_system_prompt": "\n--- Constitutional Guardian Agent ---\n\n[YOUR GOAL]\nYour SOLE PURPOSE is to review the agent output against the stipulated Governance Principles.\n\n[GOVERNANCE PRINCIPLES]\n{governance_principles_text}\n\n[TEAM WORK IN PROGRESS UPDATES]\n{team_wip_updates}\n\n[WORKFLOW]\n1.  Compare the agent output against each of the Governance Principles.\n2.  If the output fully complies with all principles, your **ONLY** response **MUST BE** the XML tag: `<OK/>`\n3.  If the output potentially violates ANY principle, or raises ANY concern regarding adherence to these principles, your **ONLY** response **MUST BE** the XML tag: `<CONCERN>Provide a concise explanation here detailing which principle(s) might be violated and why. Be specific.</CONCERN>`\n\n[CRITICAL RULES]\n- You **MUST NOT** engage in any conversation.\n- You **MUST NOT** provide any output other than the single `<OK/>` tag or the single `<CONCERN>...</CONCERN>` tag.\n- Do not use pleasantries or any other text outside these tags.\n- If in doubt, err on the side of caution and raise a CONCERN.\n\n[EXAMPLE CONCERN OUTPUT]\n`<CONCERN>The text violates GP004 by suggesting harmful actions.</CONCERN>`\n\n[EXAMPLE COMPLIANT OUTPUT]\n`<OK/>`\n"
}

    # --- Loading Prompts from prompts.yaml ---
    def _load_prompts_from_yaml(self):
        """Loads prompt templates from prompts.yaml."""
        default_prompts = self._get_default_prompts()
        
        if PROMPTS_FILE_PATH.exists():
            try:
                with open(PROMPTS_FILE_PATH, 'r') as f:
                     # Use safe_load for PyYAML
                     loaded_prompts = yaml.safe_load(f)
                     if isinstance(loaded_prompts, dict):
                         self.PROMPTS = loaded_prompts
                         for key, val in default_prompts.items():
                             if key not in self.PROMPTS:
                                 self.PROMPTS[key] = val
                     else:
                         logger.error(f"Error: {PROMPTS_FILE_PATH} did not contain a YAML dictionary. Using default prompts.")
                         self.PROMPTS = default_prompts
            except yaml.YAMLError as e:
                logger.error(f"Error decoding YAML from {PROMPTS_FILE_PATH}: {e}. Using default prompts.")
                self.PROMPTS = default_prompts
            except Exception as e:
                logger.error(f"Unexpected error when loading {PROMPTS_FILE_PATH}: {e}. Using default prompts.")
                self.PROMPTS = default_prompts
        else:
             logger.warning(f"Prompts file not found at {PROMPTS_FILE_PATH}. Creating with default prompts.")
             self.PROMPTS = default_prompts
             try:
                 PROMPTS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
                 with open(PROMPTS_FILE_PATH, 'w') as f:
                     # Dump as nicely formatted yaml without aliasing and block style strings
                     yaml.safe_dump(self.PROMPTS, f, default_flow_style=False, sort_keys=False)
             except Exception as e:
                 logger.error(f"Failed to create default {PROMPTS_FILE_PATH}: {e}")
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
                        self.GOVERNANCE_PRINCIPLES = data["principles"]
                        logger.info(f"Successfully loaded {len(self.GOVERNANCE_PRINCIPLES)} governance principles from {GOVERNANCE_FILE_PATH}.")
                        # Optional: Validate structure of each principle if needed
                        for i, principle in enumerate(self.GOVERNANCE_PRINCIPLES):
                            if not isinstance(principle, dict) or not all(k in principle for k in ["id", "name", "text", "applies_to", "enabled"]):
                                logger.warning(f"Governance principle at index {i} is missing required keys or is not a dict: {principle.get('id', 'Unknown ID')}")
                                # Optionally remove or skip invalid principles
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
