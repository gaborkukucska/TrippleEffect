# START OF FILE src/agents/constants.py

# Agent Status Constants
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_PLANNING = "planning"
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"

# --- Admin AI Workflow States ---
ADMIN_STATE_STARTUP = "startup" # Initial state before first user request is processed
ADMIN_STATE_CONVERSATION = "conversation" # Ongoing interaction after startup/plan
ADMIN_STATE_PLANNING = "planning" # Admin AI is actively creating a plan
ADMIN_STATE_WORK_DELEGATED = "work_delegated" # Plan submitted, waiting for PM completion
# Add other states like 'communicating', 'collaborating' later if needed
