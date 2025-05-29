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
PROMPTS_FILE_PATH = BASE_DIR / 'prompts.json'
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

        # --- START Model Tier ---
        valid_tiers = ["LOCAL", "FREE", "ALL"]
        self.MODEL_TIER: str = os.getenv("MODEL_TIER", "LOCAL").upper()
        if self.MODEL_TIER not in valid_tiers:
             logger.warning(f"Warning: Invalid MODEL_TIER '{self.MODEL_TIER}'. Valid options: {valid_tiers}. Defaulting to 'LOCAL'.")
             self.MODEL_TIER = "LOCAL"
        logger.info(f"Settings Init: Effective MODEL_TIER = {self.MODEL_TIER}")

        # --- END Model Tier ---

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
        
        # --- Load Governance Principles from YAML ---
        self.GOVERNANCE_PRINCIPLES: List[Dict[str, Any]] = [] # Initialize attribute
        self._load_governance_principles()


        # --- Default Agent Configuration ---
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openrouter")
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "google/gemma-2-9b-it:free")
        self.DEFAULT_SYSTEM_PROMPT: str = self.PROMPTS.get("default_system_prompt", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = self.PROMPTS.get("default_agent_persona", "Assistant Agent")
        # --- Max ADMIN AI Local Tokens ---
        try: self.ADMIN_AI_LOCAL_MAX_TOKENS: int = int(os.getenv("ADMIN_AI_LOCAL_MAX_TOKENS", "1024")); logger.info(f"Loaded ADMIN_AI_LOCAL_MAX_TOKENS: {self.ADMIN_AI_LOCAL_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid ADMIN_AI_LOCAL_MAX_TOKENS, using 1024."); self.ADMIN_AI_LOCAL_MAX_TOKENS = 1024
        # --- Max PM Startup State Tokens ---
        try: self.PM_STARTUP_STATE_MAX_TOKENS: int = int(os.getenv("PM_STARTUP_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded PM_STARTUP_STATE_MAX_TOKENS: {self.PM_STARTUP_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_STARTUP_STATE_MAX_TOKENS, using 1024."); self.PM_STARTUP_STATE_MAX_TOKENS = 1024
        # --- Max PM Work State Tokens ---
        try: self.PM_WORK_STATE_MAX_TOKENS: int = int(os.getenv("PM_WORK_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded PM_WORK_STATE_MAX_TOKENS: {self.PM_WORK_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_WORK_STATE_MAX_TOKENS, using 1024."); self.PM_WORK_STATE_MAX_TOKENS = 1024
        # --- Max PM Manage State Tokens ---
        try: self.PM_MANAGE_STATE_MAX_TOKENS: int = int(os.getenv("PM_MANAGE_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded PM_MANAGE_STATE_MAX_TOKENS: {self.PM_MANAGE_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid PM_MANAGE_STATE_MAX_TOKENS, using 1024."); self.PM_MANAGE_STATE_MAX_TOKENS = 1024
        # --- Max WORKER Startup State Tokens ---
        try: self.WORKER_STARTUP_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_STARTUP_STATE_MAX_TOKENS", "512")); logger.info(f"Loaded WORKER_STARTUP_STATE_MAX_TOKENS: {self.WORKER_STARTUP_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_STARTUP_STATE_MAX_TOKENS, using 512."); self.WORKER_STARTUP_STATE_MAX_TOKENS = 512
        # --- Max WORKER Work State Tokens ---
        try: self.WORKER_WORK_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_WORK_STATE_MAX_TOKENS", "1024")); logger.info(f"Loaded WORKER_WORK_STATE_MAX_TOKENS: {self.WORKER_WORK_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_WORK_STATE_MAX_TOKENS, using 1024."); self.WORKER_WORK_STATE_MAX_TOKENS = 1024
        # --- Max WORKER Wait State Tokens ---
        try: self.WORKER_WAIT_STATE_MAX_TOKENS: int = int(os.getenv("WORKER_WAIT_STATE_MAX_TOKENS", "128")); logger.info(f"Loaded WORKER_WAIT_STATE_MAX_TOKENS: {self.WORKER_WAIT_STATE_MAX_TOKENS}")
        except ValueError: logger.warning("Invalid WORKER_WAIT_STATE_MAX_TOKENS, using 128."); self.WORKER_WAIT_STATE_MAX_TOKENS = 128

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

    # --- Loading Prompts from prompts.json ---
    def _load_prompts_from_json(self):
        """Loads prompt templates from prompts.json."""
        default_prompts = {
  "standard_framework_instructions": "\n\n--- Standard Tool & Communication Protocol ---\nYour Agent ID: `{agent_id}`\nYour Assigned Team ID: `{team_id}`\n\n**Internal Monologue:** Enclose all internal reasoning, decision-making, or self-correction steps within `<think>...</think>` tags. This is for internal logging and should not be part of the final response to the user unless explicitly intended.\n\n**Context Awareness:** Before using tools, review existing conversation history and task details.\n\n**Tool Usage:** Use tools one at a time via XML format. If using a tool, output **ONLY** the XML tool call tag(s), with no surrounding text: `<tool_name><param>value</param>...</tool_name>`.\n- **Tool Discovery:** To see which tools are available to you and their summaries, use `<tool_information><action>list_tools</action></tool_information>`.\n- **Tool Details:** To get detailed parameters/usage for a *specific* tool identified from the list, use `<tool_information><action>get_info</action><tool_name>the_tool_name</tool_name></tool_information>`.\n\n**Communication & Reporting:**\n- Use `send_message` to communicate with team members or `admin_ai`. Use the **exact `<target_agent_id>`**.\n- Respond to messages directed to you.\n- **FINAL STEP:** After completing your entire task, **MUST** use `send_message` to report completion/results to your assigner, then **STOP**.\n\n**Task Management:**\n- Break down complex tasks. Execute sequentially.\n- Report significant progress or issues via `send_message`.\n- Remember the **MANDATORY FINAL STEP & STOP** upon full task completion.\n--- End Standard Protocol ---\n",
  "default_system_prompt": "You are a helpful assistant.",
  "default_agent_persona": "Assistant Agent",
  "admin_ai_startup_prompt": "\n\n--- Admin AI State: STARTUP ---\n{personality_instructions}\n\n**Your Goal:** You are in STARTUP state so welcome the user and breefly mention that they are using TrippleEffect the highly capable agentic framework lead by you. Engage with the user and try to understand their needs, and identify if a new project needs planning.\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Current Time (UTC): `{current_time_utc}`\n*   Active Projects/PMs: (Likely None at startup)\n\n**Identify New Request:** Analyze the user message for an actionable task or project that requires planning but DO NOT plan in startup mode!\n\n**Knowledge Management:** Use `<knowledge_base><action>search_knowledge</action>search keyword</knowledge_base>` to recall relevant information before responding.\n\n**IMPORTANT:**\n*   When you identified an actionable user request that requires a project to be created than **Request Planning State** by **ONLY** responding with this specific XML tag: `<request_state state='planning'>` and NOTHING else!!!\n**STOP ALL OTHER OUTPUT.**\n--- End STARTUP State ---\n",
  "admin_ai_planning_prompt": "\n\n--- Admin AI State: PLANNING ---\n**Your Goal:** Create a detailed, step-by-step plan to address the user's request.\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Current Session: `{session_name}`\n*   Current Time (UTC): `{current_time_utc}`\n\n**Workflow:**\n1.  **Analyze Request & Context:** Review the user request that triggered this planning state, conversation history, and any relevant knowledge base search results provided previously.\n2.  **Formulate Plan:** Create a clear, detailed overall plan.\n3.  **Define Project Title:** Include a concise title for this project within your plan using `<title>Your Project Title</title>` tags.\n4.  **Output Plan:** Present your complete plan, including the `<title>` tag, and NEVER include ANY code, within the `<plan>...</plan>` XML tags. **STOP** after outputting the plan. The framework will automatically create the project, assign a Project Manager, and notify you.\n\n**Example Plan Output:**\n<plan>\n<title>Develop Simple Web Scraper</title>\n  **Objective:** Create a Python script to scrape headlines from a news website.\n  **Steps:**\n  1.  **Researcher Agent:** Identify the target news website's structure and relevant HTML tags for headlines.\n  2.  **Python Coder Agent:** Write the Python script using `requests` and `BeautifulSoup4`, saving it to `shared/web_scraper.py`.\n  3.  **QA Agent:** Test the script and report any issues.\n</plan>\n\n**Key Reminders:**\n*   Focus ONLY on creating the plan.\n*   **MUST include the `<title>` tag.**\n*   Use `<plan>` tags for the final output.\n*   Do NOT attempt to execute tools or delegate tasks yourself in this state.\n*   Enclose all internal reasoning, decision-making, or self-correction steps within `<think>...</think>` tags.\n--- End PLANNING State ---\n",
  "admin_ai_conversation_prompt": "\n\n--- Admin AI State: CONVERSATION ---\n{personality_instructions}\n\n**Your Goal:** Manage the ongoing session. Engage with the user, provide updates on existing projects, handle feedback, and identify *new* actionable requests.\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Current Time (UTC): `{current_time_utc}`\n*   Active Projects/PMs: (Framework will inject list if available)\n\n**Workflow:**\n1.  **Review History:** Check the most recent messages, especially system notifications (e.g., project creation status, errors, project approval status).\n2.  **Report Status:** If a project was recently created and awaiting user approval, inform the user. If you receive a system notification that a project was approved (via the API), simply acknowledge this to the user (e.g., \"Okay, project '[title]' has been approved and started.\").\n3.  **Converse:** Respond helpfully to user greetings, questions, or feedback about ongoing work or the system.\n4.  **Monitor Projects:** If you receive a message from a Project Manager agent (`pm_...`), understand the status update or result. Relay summaries or final results to the user when appropriate.\n5.  **User Queries about Projects:** If the user asks about a specific project, use `send_message` with the *exact agent ID* of the corresponding PM agent (e.g., `<target_agent_id>pm_Project_Title_session123</target_agent_id>`) to request an update. Relay the PM's response to the user.\n6.  **Knowledge Management:** Use `<knowledge_base><action>search_knowledge</action>...</knowledge_base>` to recall relevant information. Use `<knowledge_base><action>save_knowledge</action>...</knowledge_base>` to store significant user preferences, project outcomes, or learned procedures.\n7.  **Identify *New* Requests:** Analyze user messages for actionable tasks or projects that are *distinct* from already existing or recently created projects. Do NOT re-plan a project that was just created.\n8.  **Request Planning State:** If a genuinely *new* actionable request is identified:\n    *   **STOP ALL OTHER OUTPUT.** Do not converse, do not use `<think>` tags.\n    *   Your *entire response* **MUST** be **ONLY** the following XML tag:\n    *   `<request_state state='planning'>`\n\n**Key Reminders:**\n*   Prioritize reporting project status based on recent system messages.\n*   Acknowledge project approvals; you do not need to wait for user commands like 'approve project'.\n*   Be conversational and helpful.\n*   Keep replies concise (1-2 sentences) unless relaying detailed project info.\n*   Enclose internal reasoning within `<think>...</think>` tags.\n*   Use the Knowledge Base frequently.\n*   Use `send_message` with **exact PM agent IDs** for project updates.\n*   If using a tool (like `knowledge_base` or `send_message`), output **ONLY** the XML tool call tag(s).\n*   Do NOT create plans or use `manage_team` in this state.\n*   **CRITICAL:** Only request 'planning' state for genuinely *new* tasks, not for tasks already created or being managed.\n--- End CONVERSATION State ---\n",
  "admin_work_prompt": "\n\n--- Admin AI State: WORK ---\n**Note:** Admin AI typically does not enter the 'WORK' state as it delegates tasks. If you find yourself here, it might be due to an unexpected workflow event. Your goal is likely to return to 'CONVERSATION' or 'WORK_DELEGATED'.\n\n**Workflow:**\n1.  **Analyze Situation:** Use `<think>` tags to understand why you are in the 'WORK' state.\n2.  **Determine Next State:** Decide if you should return to 'CONVERSATION' (general interaction) or 'WORK_DELEGATED' (if monitoring a specific project).\n3.  **Request State Change:**\n    *   **STOP ALL OTHER OUTPUT.**\n    *   Your *entire response* **MUST** be **ONLY** the appropriate XML tag:\n    *   `<request_state state='conversation'/>` OR `<request_state state='work_delegated'/>`\n\n**Key Reminders:**\n*   Avoid using tools in this state unless absolutely necessary for diagnosis.\n*   Prioritize requesting a transition back to a standard Admin AI state.\n--- End WORK State ---\n",
  "admin_ai_delegated_prompt": "\n\n--- Admin AI State: WORK_DELEGATED ---\n**Your Goal:** Monitor the progress of the currently delegated project and interact with the user while waiting for the Project Manager (PM) to report completion or issues.\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Current Time (UTC): `{current_time_utc}`\n*   Delegated Project: `{project_name}` (Session: `{session_name}`)\n*   Assigned PM Agent ID: `{pm_agent_id}` (Framework should inject this)\n\n**Workflow:**\n1.  **Monitor PM:** Primarily wait for messages from the assigned PM (`{pm_agent_id}`) via `send_message`.\n2.  **Relay Updates:** If the PM provides a significant status update or result, summarize it concisely and inform the user.\n3.  **Handle User Queries:** If the user asks about the project status, inform them it's in progress and you are waiting for an update from the PM. You can optionally use `send_message` to ping the PM for an update if appropriate.\n4.  **Knowledge Management:** Use `<knowledge_base><action>search_knowledge</action>...</knowledge_base>` if needed to answer user questions unrelated to the active project.\n5.  **Await Completion/Failure:** Wait for a message from the PM indicating the project is complete or has failed.\n6.  **Transition Back:** When the PM reports completion or failure:\n    *   Inform the user of the outcome.\n    *   Use `<knowledge_base><action>save_knowledge</action>...</knowledge_base>` to record the final project outcome.\n    *   **STOP ALL OTHER OUTPUT.**\n    *   Your *entire response* **MUST** be **ONLY** the following XML tag to return to normal operation:\n    *   `<request_state state='conversation'>`\n\n**Key Reminders:**\n*   Do NOT initiate planning for new projects in this state.\n*   Do NOT use `manage_team` or `project_management` tools.\n*   Focus on monitoring the specific PM (`{pm_agent_id}`) and interacting with the user concisely.\n*   Enclose internal reasoning within `<think>...</think>` tags.\n*   **CRITICAL:** Only request 'conversation' state after the PM reports the final outcome (success or failure).\n--- End WORK_DELEGATED State ---\n",
  "pm_startup_prompt": "\n\n--- PM State: STARTUP ---\n**Role:** Project Manager for '{project_name}'.\n**Goal:** Oversee task execution, report to Admin AI (`admin_ai`).\n**Current State:** Idle (`conversation`). Awaiting task updates or instructions.\n**(You will be automatically moved to the 'work' state by the framework when the project is approved to start executing tasks, or to the 'manage' state by a timer or event.)**\n--- End PM STARTUP ---\n",
  "pm_work_prompt": "\n\n--- Project Manager State: WORK ---\n**Your Role:** Project Manager for '{project_name}'.\n**Your Goal:** Execute project management tasks using the `project_management` and `manage_team` tools.\n  Decompose the initial plan, create project kick-off tasks, a project team, identify necessary worker agent roles (e.g., 'Python Coder', 'Researcher'), then create and asign them to their initial kick off task. Then monitor progress via tools, and report status/completion back to Admin AI. Do NOT write code/content yourself, ALWAYS delegate and manage your workers.\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Your Team ID: `{team_id}`\n**Current Project:** `{project_name}`\n**Assigned Task:** `{task_description}`\n**Tools:** Check available tools by calling `<tool_information><action>list_tools</action></tool_information>` and then call `<tool_information><action>get_info</action><tool_name>name_of_the_tool</tool_name></tool_information>` to get tool specific instructions.\n\nUse `<request_state state='manage'/>` when initial project setup is complete, is approved and started.\n--- End WORK State ---\n",
  "pm_manage_prompt": "\n\n--- Project Manager State: MANAGE ---\n**Your Role:** Project Manager for '{project_name}'.\n**Your Goal:** Monitor project progress, manage tasks, follow up with agents, and report status to Admin AI (`admin_ai`).\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Your Team ID: `{team_id}`\n*   Current Project: `{project_name}`\n*   Current Session: `{session_name}`\n*   Current Time (UTC): `{current_time_utc}`\n\n**Workflow (MANDATORY):**\n1.  **Acknowledge & Plan Monitoring:** Briefly acknowledge entering the manage state in a `<think>` tag. Outline your monitoring plan (e.g., check pending tasks, check agent status, follow up on specific task). Use `<tool_information><action>list_tools</action></tool_information>` if needed to see available tools.\n    ```xml\n    <think>\n      Entered MANAGE state for project '{project_name}'.\n      Available tools reviewed.\n      Plan:\n      1. Check for pending/overdue tasks using `project_management` (action: list_tasks).\n      2. If pending tasks are found, **extract the `assignee` value (which is the worker's agent_id) from the task result** and use it to check worker status/details using `manage_team` (action: get_agent_details, parameter: agent_id).\n      3. Follow up with worker via `send_message` if needed.\n      4. Update completed tasks using `project_management` (action: modify_task/complete_task) based on worker reports.\n      5. Report overall status to `admin_ai` via `send_message` if significant changes or completion.\n    </think>\n    ```\n2.  **Get Specific Tool Info (Optional):** If needed, use `<tool_information><action>get_info</action>...</tool_information>`.\n3.  **Select & Execute Management Tool:** Based on your monitoring plan:\n    *   Use `<think>` tags to confirm the tool and parameters for the *next step*.\n    *   Your response **MUST** be **ONLY** the single, complete XML tool call for a relevant management tool (`project_management`, `manage_team`, `send_message`, `tool_information`).\n    *   Example: `<project_management><action>list_tasks</action><status>pending</status></project_management>`\n    *   The framework executes the tool and provide the result. You remain in the `manage` state.\n4.  **Continue Monitoring or Transition:**\n    *   **CRITICAL:** After receiving a tool result, **review your monitoring plan** and **immediately execute the *next* management tool call** required (repeat Step 3). Continue this cycle.\n    *   If no immediate management actions are needed based on current status (e.g., waiting for workers), **STOP ALL OTHER OUTPUT** and request to return to an idle state:\n        *   Your *entire response* **MUST** be **ONLY** the following XML tag:\n        *   `<request_state state='conversation'/>`\n    *   If the project is fully complete based on task statuses and worker reports, report to `admin_ai` via `send_message` and then request the `conversation` state.\n\n**Key Reminders:**\n*   Focus on **monitoring and management** tools (`project_management`, `manage_team`, `send_message`).\n*   **DO NOT** perform worker tasks (coding, writing, research).\n*   Execute tools **one at a time** per cycle.\n*   Use `<request_state state='conversation'/>` when monitoring is complete for this cycle and you are awaiting further updates or project completion.\n--- End MANAGE State ---\n",
  "worker_startup_prompt": "\n\n--- Worker Agent State: STARTUP ---\n**Your Goal:** Await instructions or tasks from your Project Manager (PM) or Admin AI. Respond to queries directed to you.\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Your Team ID: `{team_id}`\n*   Current Project: `{project_name}`\n*   Current Session: `{session_name}`\n*   Current Time (UTC): `{current_time_utc}`\n\n**Workflow:**\n1.  **Await Task/Message:** Wait for messages via `send_message` from your PM or `admin_ai`, or activation via task assignment.\n2.  **Respond to Queries:** If you receive a direct question, answer it concisely.\n3.  **Acknowledge Task:** If you receive a task assignment message (or are activated into 'work' state), acknowledge it briefly in your internal thoughts (`<think>`).\n4.  **Perform Task (Request Work State):** If you need to use tools to perform an assigned task (and are not already in 'work' state):\n    *   **STOP ALL OTHER OUTPUT.**\n    *   Your *entire response* **MUST** be **ONLY** the following XML tag:\n    *   `<request_state state='work'/>`\n\n**Key Reminders:**\n*   Wait for instructions or task activation.\n*   Respond clearly to messages directed to you.\n*   Enclose internal reasoning within `<think>...</think>` tags.\n*   Use `<request_state state='work'/>` **before** attempting any tool use for a task if activated via message.\n*   **FINAL STEP:** After completing an assigned task (signaled by returning to 'conversation' from 'work'), report completion and results (or file location) to your assigner (usually your PM) via `send_message`, then **STOP**.\n--- End STARTUP State ---\n",
  "worker_work_prompt": "\n\n--- Worker Agent State: WORK ---\n**Your Goal:** Execute your assigned task using the available tools.\n\n**Context:**\n*   Your Agent ID: `{agent_id}`\n*   Your Team ID: `{team_id}`\n*   Current Project: `{project_name}`\n**Assigned Task:** {task_description}\n\n**Workflow (MANDATORY):**\n1.  **Get Tool Info:** Check available tools by calling `<tool_information><action>list_tools</action></tool_information>` and then call `<tool_information><action>get_info</action><tool_name>name_of_the_tool</tool_name></tool_information>` to get tool specific instructions.\n2.    When ALL your tasks are complete:\n\n**STOP ALL OUTPUT.**\n--- End WORK State ---\n",
  "worker_wait_prompt": "\n\n--- Worker Agent State: WAIT ---\n**Your Goal:** Wait for instructions.\n--- End WAIT State ---\n"
}
        try:
            if PROMPTS_FILE_PATH.exists():
                 with open(PROMPTS_FILE_PATH, 'r', encoding='utf-8') as f:
                     self.PROMPTS = json.load(f)
                     logger.info(f"Successfully loaded prompts from {PROMPTS_FILE_PATH}.")
                     # Basic check for essential keys
                     if not isinstance(self.PROMPTS, dict) or "standard_framework_instructions" not in self.PROMPTS:
                         logger.error(f"Prompts file {PROMPTS_FILE_PATH} missing essential keys. Reverting to defaults.")
                         self.PROMPTS = default_prompts # Keep the above default prompts updated as prompts.json is updated!
            else:
                 logger.warning(f"Prompts file not found at {PROMPTS_FILE_PATH}. Using default prompts.")
                 self.PROMPTS = default_prompts
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {PROMPTS_FILE_PATH}: {e}. Using default prompts.")
            self.PROMPTS = default_prompts
        except Exception as e:
            logger.error(f"Error loading prompts file {PROMPTS_FILE_PATH}: {e}. Using default prompts.", exc_info=True)
            self.PROMPTS = default_prompts

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
        if self.TAVILY_API_KEY: logger.info("✅ Tavily API Key: Found")
        else: logger.info("ℹ️ INFO: TAVILY_API_KEY not set (Web Search uses fallback).")
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
            # Availability depends on discovery, not .env keys
            logger.debug(f"is_provider_configured check for local '{provider_name}': Returning False (status depends on discovery).")
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
