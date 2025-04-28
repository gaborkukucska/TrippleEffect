# START OF FILE src/agents/constants.py
import re
import openai # For exception types

# --- Core Agent/Framework IDs ---
BOOTSTRAP_AGENT_ID = "admin_ai"

# --- Agent Status Constants ---
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_PLANNING = "planning" # Agent is generating a plan (distinct from Admin AI state)
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"

# --- Admin AI Workflow States ---
ADMIN_STATE_STARTUP = "startup" # Initial state before first user request is processed
ADMIN_STATE_CONVERSATION = "conversation" # Ongoing interaction after startup/plan
ADMIN_STATE_PLANNING = "planning" # Admin AI is actively creating a plan
ADMIN_STATE_WORK_DELEGATED = "work_delegated" # Plan submitted, waiting for PM completion
# Add other states like 'communicating', 'collaborating' later if needed

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
    # openai.RateLimitError, # Often treated as key-related for quarantine/failover
    # aiohttp exceptions could be added here if needed globally, but often handled per-provider
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
