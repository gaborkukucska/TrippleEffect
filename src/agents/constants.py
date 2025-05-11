# START OF FILE src/agents/constants.py
import re
import openai # For exception types
import aiohttp # For retryable exceptions

# --- Core Agent/Framework IDs ---
BOOTSTRAP_AGENT_ID = "admin_ai"

# --- Agent Type Constants ---
AGENT_TYPE_ADMIN = "admin" # The Admin AI of the framework.
AGENT_TYPE_PM = "pm" # Project Manager Agents
AGENT_TYPE_WORKER = "worker" # Worker Agents

# --- Default Workflow State ---
DEFAULT_STATE = "default" # The default backup state

# --- Admin AI Workflow States ---
ADMIN_STATE_STARTUP = "startup" # Admin startup state is where the framework starts after booting up
ADMIN_STATE_CONVERSATION = "admin_conversation" # Distinct conversation state for Admin
ADMIN_STATE_PLANNING = "planning" # The planning state for the Admin AI which initiates the planning workflow
ADMIN_STATE_WORK_DELEGATED = "work_delegated"
ADMIN_STATE_WORK = "work" # The work state for the Admin AI

# --- PM Agent Workflow State Constants ---
PM_STATE_STARTUP = "pm_startup" # Startup state for PM
PM_STATE_WORK = "pm_work" # State for active tool use/task execution for PM
PM_STATE_MANAGE = "pm_manage" # State for PM monitoring/management tasks

# --- WORKER Agent Workflow State Constants ---
WORKER_STATE_STARTUP = "worker_startup" # Startup state for Worker
WORKER_STATE_WORK = "worker_work" # State for active tool use/task execution for Worker
WORKER_STATE_WAIT = "worker_wait" # State for worker waiting

# --- Agent Operational Status Constants (Distinct from agent states!) ---
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_PLANNING = "planning"
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"

# --- LLM Provider Interaction Constants ---

# Default Retry Config (Consider sourcing from settings.py eventually)
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0

# Retryable Status Codes (Common across providers)
RETRYABLE_STATUS_CODES = [429, 500, 502, 503, 504]

# Retryable Exceptions (Mainly OpenAI/aiohttp based)
# Note: Specific providers might handle additional internal exceptions
RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    aiohttp.ClientPayloadError, # Error during response body processing (e.g., incomplete stream)
    aiohttp.ClientConnectorError, # Cannot connect to host error
    # openai.RateLimitError, # Often treated as key-related for quarantine/failover
    # Add other general aiohttp exceptions if needed
)

# Key-Related Errors (For Failover/Quarantine Logic)
KEY_RELATED_ERRORS = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.RateLimitError, # Include RateLimitError here for key cycling
)
KEY_RELATED_STATUS_CODES = [401, 403, 429] # Unauthorized, Forbidden, Too Many Requests

# --- Parsing Constants ---

# Regex for Admin AI state change requests in responses
# Modified regex to accept both self-closing (/>) and non-self-closing (>) tags
REQUEST_STATE_TAG_PATTERN = re.compile(r"<request_state\s+state=['\"](\w+)['\"]\s*/?>")

# --- Ollama Specific Constants ---

# Known valid Ollama options (Used for filtering kwargs in lifecycle/providers)
KNOWN_OLLAMA_OPTIONS = {
    "mirostat", "mirostat_eta", "mirostat_tau", "num_ctx", "num_gpu", "num_thread",
    "num_keep", "seed", "num_predict", "repeat_last_n", "repeat_penalty",
    "temperature", "tfs_z", "top_k", "top_p", "min_p", "use_mmap", "use_mlock",
    "numa", "num_batch", "main_gpu", "low_vram", "f16_kv", "logits_all",
    "vocab_only", "stop", "presence_penalty", "frequency_penalty", "penalize_newline",
    "typical_p"
}
