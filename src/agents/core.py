# START OF FILE src/agents/core.py
import asyncio
import json
import re # For XML parsing
import os
import time # For call IDs
import traceback # For detailed error logging
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from pathlib import Path
import html # For unescaping XML parameter values
import logging
import xml.etree.ElementTree as ET # For parsing workflow trigger content

# Import settings for defaults and BASE_DIR
from src.config.settings import settings, BASE_DIR

# Import BaseLLMProvider for type hinting and interface adherence
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolResultDict

# Import the parser function
from src.agents.agent_tool_parser import find_and_parse_xml_tool_calls
from src.agents.tool_activity import state_tool_allowlist, is_call_imitation_text  # Round 2k, 2o

# --- Import status and state constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_ERROR,
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK,
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_AUDIT, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS, PM_STATE_REPORT_CHECK,
    WORKER_STATE_STARTUP, WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK, WORKER_STATE_TEST, WORKER_STATE_WAIT, WORKER_STATE_REPORT,
    DEFAULT_STATE
)
# --- END Import status and state constants ---

# Import AgentManager for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.workflows.base import WorkflowResult # For type hinting workflow_result
# Import the constant directly
from src.agents.constants import BOOTSTRAP_AGENT_ID

logger = logging.getLogger(__name__)

# Tool Call Patterns (XML only)
XML_TOOL_CALL_PATTERN = None
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})(?:\s+[^>]*)?(?:>[\s\S]*?</\2>|/>))\s*\n?```"
THINK_TAG_PATTERN = r"<think>([\s\S]*?)</think>"
ROBUST_THINK_TAG_PATTERN = re.compile(r"<think>(.*?)(?:</think>|<(?=[^/]))", re.DOTALL | re.IGNORECASE)

def detect_autoregressive_loop(text: str, min_pattern_length: int = 20, min_repetitions: int = 4) -> bool:
    """Detects if the end of the text contains a strictly repeating autoregressive loop."""
    text_len = len(text)
    if text_len < min_pattern_length * min_repetitions:
        return False
    
    max_pattern = min(1000, text_len // min_repetitions)
    
    for pattern_length in range(min_pattern_length, max_pattern + 1):
        pattern = text[-pattern_length:]
        is_loop = True
        for i in range(1, min_repetitions):
            start_idx = -(pattern_length * (i + 1))
            end_idx = -(pattern_length * i)
            segment = text[start_idx:] if end_idx == 0 else text[start_idx:end_idx]
            if segment != pattern:
                is_loop = False
                break
        if is_loop:
            return True
    return False

MAX_TOKENS_BOOST_FACTOR = 4


def boosted_max_tokens(base: Optional[int], consecutive_cut_offs: int) -> Optional[int]:
    """Round 2d: the output budget after `consecutive_cut_offs` cut-off replies in a row: base x2 per cut-off, at most x4."""
    if base is None or not consecutive_cut_offs or consecutive_cut_offs < 0:
        return base
    return min(base * (2 ** consecutive_cut_offs), base * MAX_TOKENS_BOOST_FACTOR)


def budget_after_cut_off(base: Optional[int], consecutive_cut_offs: int, floor: Optional[int] = None) -> Optional[int]:
    """
    Round 3o: boosted_max_tokens(), but after a cut-off never below `floor`. A worker in worker_decompose (2048 tokens) that
    writes its whole file there is cut off at 2048, 4096 and 8192 tokens before a retry fits (2026-10-25 13:32:24-13:35:16:
    W2's 14,390-character tests/test_snake.py, 2.5 minutes): after the first cut-off it gets at least the worker_work budget.
    """
    boosted = boosted_max_tokens(base, consecutive_cut_offs)
    if boosted is not None and floor and consecutive_cut_offs and consecutive_cut_offs > 0 and boosted < floor:
        return floor
    return boosted


def cut_off_boost_level(agent: Any) -> int:
    """
    Round 2n: how many doublings the output budget of the agent's next call gets: the cut-offs in a row, or - while the
    agent stays in the state where a reply was cut off - the highest level reached there. 2026-10-20 23:21-23:24 W1's
    complete src/app.py was cut off at 4096 tokens three times (about 85 s each): after each cut-off the retry made a
    small call (list a directory, pip install), the budget fell back to 4096 and the next complete file was cut off again.
    """
    streak = int(getattr(agent, '_consecutive_cut_off_replies', 0) or 0)
    sticky = int(getattr(agent, '_cut_off_boost_level', 0) or 0)
    if sticky and getattr(agent, '_cut_off_boost_state', None) != getattr(agent, 'state', None):
        sticky = 0
    return max(streak, sticky)


# Round 2x: replies that tried to act but delivered nothing usable (cut off at the output limit, output lost, context
# window full) are retried and NOT counted as "empty" by the Constitutional Guardian - up to this many in a row.
UNUSABLE_REPLY_RETRY_CAP = 4


def unusable_reply_kind(agent: Any) -> Optional[str]:
    """Round 2x: 'context_full', 'cut_off' or 'output_lost' for the agent's last reply, else None."""
    if getattr(agent, '_context_window_full', False) is True:
        return "context_full"
    if getattr(agent, '_last_reply_cut_off', False) is True:
        return "cut_off"
    if getattr(agent, '_last_reply_output_lost', False) is True:
        return "output_lost"
    return None


def excused_unusable_reply(agent: Any) -> bool:
    """
    Round 2x: True if the agent's last reply was unusable for a reason that is not the agent going silent, and it is at
    most the UNUSABLE_REPLY_RETRY_CAP-th such reply in a row. 2026-10-22 11:44-12:19: the Guardian blocked W2 six times for
    "2 consecutive empty responses" that were dropped tool calls and cut-off file writes, and each block moved it to
    worker_wait (512 output tokens), where its next file write was cut off again.
    """
    streak = getattr(agent, '_consecutive_unusable_replies', 0)
    if isinstance(streak, bool) or not isinstance(streak, int):
        return False
    return unusable_reply_kind(agent) is not None and 0 < streak <= UNUSABLE_REPLY_RETRY_CAP


def build_request_state_schema(agent_type, state, valid_states):
    """
    Builds the native `request_state` pseudo-tool schema for an agent, or None if it must not be offered.
    - A PM in pm_startup must output the JSON kickoff plan, so the tool is not offered at all.
    - Other PM states get a description listing the states a PM can really request (the old text always
      listed worker states, which a PM can never enter).
    - Admin and worker agents get exactly the schema they always had.
    """
    if agent_type == AGENT_TYPE_PM and state == PM_STATE_STARTUP:
        return None
    if agent_type == AGENT_TYPE_PM:
        shown = [s for s in (valid_states or []) if s and s not in ("default", PM_STATE_STARTUP, state)]
        state_desc = "The destination state to transition to."
        if shown:
            state_desc += " Valid states for you: " + ", ".join(shown) + "."
        task_desc = "Only used by workers; leave empty."
    else:
        state_desc = "The destination state to transition to (e.g., worker_work, worker_test, worker_report, worker_wait)."
        task_desc = "REQUIRED when transitioning to 'worker_work' or 'worker_test' state. The UUID of the sub-task you are choosing to work on."
    return {
        "type": "function",
        "function": {
            "name": "request_state",
            "description": "Request a state transition for the agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "description": state_desc},
                    "task_id": {"type": ["string", "null"], "description": task_desc},
                    "task_description": {"type": "string", "description": "Optional description of the task."},
                },
                "required": ["state"],
            },
        },
    }


class Agent:
    def __init__(
        self,
        agent_config: Dict[str, Any],
        llm_provider: BaseLLMProvider,
        manager: 'AgentManager'
        ):
        config: Dict[str, Any] = agent_config.get("config", {})
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")
        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        self.final_system_prompt: str = "" 
        self._config_system_prompt: str = config.get("system_prompt", "") 
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        self.role: str = config.get("role", "")
        self.agent_type: str = config.get("agent_type", "worker")
        self.agent_config: Dict[str, Any] = agent_config 
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'api_key', 'base_url', 'referer', 'max_tokens', 'project_name_context', 'initial_plan_description', 'admin_memory_context', 'role', 'personality']}
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager
        self.status: str = AGENT_STATUS_IDLE
        self.state: Optional[str] = None
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.current_plan: Optional[str] = None
        self.current_task_id: Optional[str] = None
        self.current_task_description: Optional[str] = None # Holds the task for the current work state
        self.message_history: List[MessageDict] = []
        self._last_api_key_used: Optional[str] = None
        self._failed_models_this_cycle: set = set()
        self._pm_needs_initial_list_tools: bool = False
        self._awaiting_project_approval: bool = False
        self.needs_priority_recheck: bool = False
        self.intervention_applied_for_build_team_tasks: bool = False
        self.text_buffer: str = ""
        self._pm_report_check_cycle_count: int = 0
        self._pm_audit_cycle_count: int = 0
        self._pm_audit_attempt_count: int = 0
        safe_agent_id = self.agent_id if self.agent_id.startswith(("agent_", "pm_", "admin_")) else f"agent_{self.agent_id}"
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / safe_agent_id
        self.raw_xml_tool_call_pattern = None
        self.markdown_xml_tool_call_pattern = None
        self.think_pattern = ROBUST_THINK_TAG_PATTERN
        self._failover_state: Optional[Dict[str, Any]] = None
        self.initial_plan_description: Optional[str] = config.get("initial_plan_description")
        self.kick_off_task_count_for_build: Optional[int] = None # For PM to track how many workers to create
        self.successfully_created_agent_count_for_build: int = 0 # Counter for created workers in build phase
        self.default_task_assigned: bool = False # Flag for one-time default task injection
        
        # Attributes for Constitutional Guardian interaction
        self.cg_original_text: Optional[str] = None
        self.cg_concern_details: Optional[str] = None
        self.cg_original_event_data: Optional[Dict[str, Any]] = None
        self.cg_awaiting_user_decision: bool = False
        self.cg_review_start_time: Optional[float] = None

        # Dynamic tracking attributes set by workflow_manager / cycle_handler
        self._consecutive_empty_work_cycles: int = 0
        self._work_cycle_count: int = 0
        self._project_completed: bool = False
        self._project_completion_time: Optional[float] = None
        self._periodic_cycle_count: int = 0
        self._periodic_cycle_window_start: float = 0.0
        self._last_periodic_check_time: float = 0.0
        self._manage_unproductive_cycles: int = 0
        self._work_completion_history: List[Dict[str, Any]] = []
        self._state_transition_history: List[Dict[str, Any]] = []
        self._needs_initial_work_context: bool = False
        self._injected_task_description: Optional[str] = None
        self._last_system_prompt_state: Optional[Tuple[str, Optional[str]]] = None  # Tracks (agent_type, state) for prompt reuse
        self._pre_report_check_state: Optional[str] = None # Tracks previous state before auto-switching to report check
        self.message_inbox: List[MessageDict] = [] # Inbox for messages received while busy
        self.unassigned_tasks_summary: List[Dict[str, Any]] = []
        self.read_message_ids: Dict[str, float] = {} # Track read messages with TTL
        self.active_task_id: Optional[str] = None # Currently active task UUID for UI tracking

        # Dynamic tracking attributes set by CycleHandler (declared here for type safety)
        self._cycles_without_transition: int = 0
        self._last_state_for_watchdog: Optional[str] = None
        self._consecutive_workflow_failures: int = 0
        self._recent_error_feedback: Dict[str, float] = {}
        self._send_msg_multi_tool_error_count: int = 0
        self._duplicate_tool_call_count: int = 0
        self._duplicate_failure_count: int = 0
        self._consecutive_tool_failures: int = 0
        self._all_kickoff_task_uuids: set = set()
        self._consecutive_empty_responses: int = 0
        # Round 2d: replies cut off at the output token limit (see boosted_max_tokens / the cycle handler's notice)
        self._last_reply_cut_off: bool = False
        self._consecutive_cut_off_replies: int = 0
        self._last_cut_off_max_tokens: Optional[int] = None
        self._last_base_max_tokens: Optional[int] = None
        # Round 2n: a cut-off keeps the larger budget while the agent stays in that state (see cut_off_boost_level)
        self._cut_off_boost_level: int = 0
        self._cut_off_boost_state: Optional[str] = None
        # Round 2n: token counts of the last reply (from 'finish_info') and whether the context window was full
        self._last_prompt_tokens: int = 0
        self._last_output_tokens: int = 0
        self._last_num_ctx: Optional[int] = None
        self._context_window_full: bool = False
        # Round 2x: a reply whose output the server dropped (see ollama_provider.is_output_lost), and how many replies in a
        # row were unusable (cut off, output lost or context window full - see excused_unusable_reply)
        self._last_reply_output_lost: bool = False
        self._last_reply_lost_tokens: int = 0  # Round 2y: the output tokens that never arrived (lost_output_tokens)
        self._consecutive_output_lost_replies: int = 0
        self._consecutive_unusable_replies: int = 0
        self._call_imitation_text: Optional[str] = None  # Round 2o: a reply that only imitated a tool call in text
        # Round 2h: thinking of the last reply (from the provider's 'finish_info'), and a one-reply switch to turn it off
        self._last_reply_thinking_chars: int = 0
        self._last_reply_thinking_tail: str = ""
        self._think_off_next_call: bool = False
        self._framework_forced_standby_count: int = 0
        self._manage_cycle_cooldown_until: float = 0.0
        self._last_standby_wake_time: float = 0.0
        self._seen_waiting_workers: set = set()
        self.last_error_content: str = ""

        # PM kickoff workflow attributes
        self.kick_off_roles: List[str] = []
        self.kick_off_tasks: List[str] = []
        self.target_worker_agents_for_build: int = 0


        if self.manager and self.manager.tool_executor and self.manager.tool_executor.tools:
            tool_names = list(self.manager.tool_executor.tools.keys())
            if tool_names:
                safe_tool_names_lower = [re.escape(name.lower()) for name in tool_names]
                tool_names_pattern_group_lower = '|'.join(safe_tool_names_lower)
                raw_pattern_str = rf"<({tool_names_pattern_group_lower})(?:\s+[^>]*)?(?:>[\s\S]*?</\1>|/>)"
                self.raw_xml_tool_call_pattern = re.compile(raw_pattern_str, re.IGNORECASE | re.DOTALL)
                md_xml_pattern_str = MARKDOWN_FENCE_XML_PATTERN.format(tool_names=tool_names_pattern_group_lower)
                self.markdown_xml_tool_call_pattern = re.compile(md_xml_pattern_str, re.IGNORECASE | re.DOTALL | re.MULTILINE)
                logger.info(f"Agent {self.agent_id}: Compiled XML tool patterns for tools: {tool_names}")
            else: logger.info(f"Agent {self.agent_id}: No tools found in executor, tool parsing disabled.")
        else: logger.warning(f"Agent {self.agent_id}: Manager or ToolExecutor not available, tool parsing disabled.")
        logger.info(f"Agent {self.agent_id} ({self.persona}) initialized. Type: {self.agent_type}. Status: {self.status}. State: {self.state}. Provider: {self.provider_name}, Model: {self.model}.")

    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None, plan_info: Optional[str] = None):
        if self.status != new_status:
            logger.info(f"Agent {self.agent_id}: Status changed from '{self.status}' to '{new_status}'")
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        self.current_plan = plan_info if new_status == AGENT_STATUS_PLANNING else None
        if self.manager: asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))

    def mark_message_read(self, message_id: str) -> None:
        """Mark a message as read with a timestamp for TTL pruning."""
        self.read_message_ids[message_id] = time.time()
        # Prune entries older than 1 hour (3600 seconds)
        if len(self.read_message_ids) > 500:
            cutoff = time.time() - 3600
            self.read_message_ids = {
                mid: ts for mid, ts in self.read_message_ids.items()
                if ts > cutoff
            }

    def set_state(self, new_state: str):
        if self.state != new_state: 
            logger.info(f"Agent {self.agent_id}: Workflow State changed from '{self.state}' to '{new_state}'")
            # Round 2x: every state change restarts the cycle handler's cycles-in-one-state watchdog. It only noticed a
            # change at the start of a cycle, so PM1's separate visits to pm_report_check (with pm_standby in between and
            # no cycle there) were counted as one: "18 cycles without changing from state 'pm_report_check'" (2026-10-22).
            self._cycles_without_transition = 0
            self._last_state_for_watchdog = None
            # Track state transitions for Admin AI work state loop detection
            if not hasattr(self, '_state_transition_history'):
                self._state_transition_history = []
            self._state_transition_history.append({
                'from_state': self.state,
                'to_state': new_state,
                'timestamp': time.time()
            })
            # Keep only last 10 transitions to avoid memory issues
            if len(self._state_transition_history) > 10:
                self._state_transition_history = self._state_transition_history[-10:]
            self.state = new_state
        else: 
            logger.debug(f"Agent {self.agent_id}: set_state called with current state '{new_state}'. No change.")

    def ensure_sandbox_exists(self) -> bool:
        try: self.sandbox_path.mkdir(parents=True, exist_ok=True); return True
        except Exception as e: logger.error(f"Error creating sandbox for Agent {self.agent_id}: {e}", exc_info=True); return False

    async def process_message(self, history_override: Optional[List[MessageDict]] = None) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        history_to_use = history_override if history_override is not None else self.message_history
        if not self.llm_provider:
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]", "_exception_obj": ValueError("LLM Provider not configured")}; return
        if not self.manager:
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "content": "[Agent Error: Manager not configured]", "_exception_obj": ValueError("Manager not configured")}; return

        self.text_buffer = ""
        complete_assistant_response = ""
        stream_had_error = False
        last_error_obj = None
        yielded_chunks = False
        
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(history_to_use)}. State: {self.state}")
        try:
            if not self.ensure_sandbox_exists():
                self.set_status(AGENT_STATUS_ERROR)
                yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]", "_exception_obj": OSError(f"Could not ensure sandbox {self.sandbox_path}")}; return

            max_tokens_override = None
            # Determine max_tokens based on agent type and state from settings
            if self.agent_type == AGENT_TYPE_PM:
                if self.state == PM_STATE_STARTUP: max_tokens_override = settings.PM_STARTUP_STATE_MAX_TOKENS
                elif self.state == PM_STATE_WORK: max_tokens_override = settings.PM_WORK_STATE_MAX_TOKENS
                elif self.state == PM_STATE_MANAGE: max_tokens_override = settings.PM_MANAGE_STATE_MAX_TOKENS
                elif self.state == PM_STATE_AUDIT: max_tokens_override = settings.PM_MANAGE_STATE_MAX_TOKENS
            elif self.agent_type == AGENT_TYPE_WORKER:
                if self.state == WORKER_STATE_STARTUP: max_tokens_override = settings.WORKER_STARTUP_STATE_MAX_TOKENS
                elif self.state == WORKER_STATE_DECOMPOSE: max_tokens_override = settings.WORKER_DECOMPOSE_STATE_MAX_TOKENS
                elif self.state in [WORKER_STATE_WORK, WORKER_STATE_TEST]: max_tokens_override = settings.WORKER_WORK_STATE_MAX_TOKENS
                elif self.state == WORKER_STATE_WAIT: max_tokens_override = settings.WORKER_WAIT_STATE_MAX_TOKENS
            self._last_base_max_tokens = max_tokens_override  # Round 2d: the configured budget of this call
            if max_tokens_override is not None:
                # Round 2d: after replies that were CUT OFF at the output limit, the next call gets a larger budget (x2 per
                # cut-off in a row, at most x4) so a complete tool call fits. The configured value stays the normal budget.
                cut_off_floor = (settings.WORKER_WORK_STATE_MAX_TOKENS  # Round 3o: a write cut off in decompose
                                 if self.agent_type == AGENT_TYPE_WORKER and self.state == WORKER_STATE_DECOMPOSE else None)
                max_tokens_override = budget_after_cut_off(max_tokens_override, cut_off_boost_level(self),
                                                           cut_off_floor)  # Round 2n: sticky
                logger.info(f"Agent {self.agent_id}: Applying max_tokens={max_tokens_override} for state '{self.state}'")
            self._last_reply_cut_off = False  # Round 2d: set again below if this reply is cut off
            self._last_cut_off_max_tokens = max_tokens_override

            tool_schemas = None
            if settings.NATIVE_TOOL_CALLING_ENABLED and self.manager and getattr(self.manager, 'tool_executor', None):
                tool_schemas = []
                for tool_name, tool in self.manager.tool_executor.tools.items():
                    # Round 2o: 'resolve_concern' only while a Constitutional Guardian concern waits for this agent's decision
                    if tool_name == "resolve_concern":
                        escalation = getattr(self.manager, "cg_escalation", None)
                        if escalation is None or not escalation.pending_for_decider(self.agent_id):
                            continue
                    # Restrict specific states to only the tools they actually need
                    if self.agent_type == AGENT_TYPE_WORKER:
                        if self.state == WORKER_STATE_DECOMPOSE and tool_name not in ['project_management', 'file_system', 'codebase_search', 'mark_message_read', 'send_message']:
                            continue
                        # Round 2k: the same list the executor enforces (tool_activity.state_tool_allowlist)
                        allowed_here = state_tool_allowlist(self.agent_type, self.state)
                        if allowed_here is not None and tool_name not in allowed_here:
                            continue
                    elif self.agent_type == AGENT_TYPE_PM:
                        if self.state == PM_STATE_STARTUP:
                            # In startup, PM must focus strictly on outputting the JSON kickoff plan
                            # No tools needed; pseudo-tool request_state is available if needed.
                            continue
                        if self.state == PM_STATE_BUILD_TEAM_TASKS and tool_name not in ['manage_team', 'tool_information', 'mark_message_read', 'send_message']:
                            continue
                            
                    # Apply standard auth checks for native tools
                    tool_auth_level = getattr(tool, 'auth_level', 'worker')
                    is_authorized = False
                    if self.agent_type == AGENT_TYPE_ADMIN:
                        is_authorized = True
                    elif self.agent_type == AGENT_TYPE_PM:
                        is_authorized = tool_auth_level in ['pm', 'worker']
                    elif self.agent_type == AGENT_TYPE_WORKER:
                        is_authorized = tool_auth_level == 'worker'
                        
                    if is_authorized:
                        tool_schemas.append(tool.get_json_schema())
                        
                # Inject request_state as a pseudo-tool so LLM knows its schema (not offered to a PM in pm_startup)
                _valid_states_for_type = getattr(getattr(self.manager, 'workflow_manager', None), '_valid_states', {}).get(self.agent_type, [])
                _request_state_schema = build_request_state_schema(self.agent_type, self.state, _valid_states_for_type)
                if _request_state_schema:
                    tool_schemas.append(_request_state_schema)

            if tool_schemas is not None and not tool_schemas:
                tool_schemas = None

            from contextlib import aclosing
            finish_reason_received = None  # Round 2d: done_reason reported by the provider ('finish_info' event)
            finish_event_data: Dict[str, Any] = {}  # Round 2n
            self._context_window_full = False
            self._last_reply_output_lost = False  # Round 2x: set again below if this reply's output is lost
            self._last_reply_lost_tokens = 0
            self._call_imitation_text = None  # Round 2o
            # Round 2h: what this reply thought (from 'finish_info'), and thinking switched off for a retry after a reply
            # that only thought.
            self._last_reply_thinking_chars = 0
            self._last_reply_thinking_tail = ""
            call_kwargs = dict(self.provider_kwargs or {})
            if getattr(self, '_think_off_next_call', False):
                self._think_off_next_call = False
                from src.llm_providers.ollama_provider import thinking_off_kwargs
                think_off = thinking_off_kwargs(self.llm_provider)
                if think_off:
                    call_kwargs.update(think_off)
                    logger.info(f"Agent {self.agent_id}: thinking switched off for this reply (the last reply only thought).")
            
            async with aclosing(self.llm_provider.stream_completion(
                messages=history_to_use, model=self.model, temperature=self.temperature,
                max_tokens=max_tokens_override, tools=tool_schemas, **call_kwargs
            )) as provider_stream:
                
                native_tool_calls_received = []
                chunk_counter = 0
    
                async for event in provider_stream:
                    event_type = event.get("type")
                    if event_type == "response_chunk":
                        content = event.get("content", "")
                        if content: 
                            self.text_buffer += content
                            complete_assistant_response += content
                            yielded_chunks = True
                            chunk_counter += 1
                            
                            if chunk_counter % 25 == 0 and detect_autoregressive_loop(self.text_buffer):
                                error_msg = "[LLM Error] Autoregressive string loop detected. Terminating stream to prevent hang."
                                logger.error(f"Agent {self.agent_id}: {error_msg}")
                                error_event = {"type": "error", "content": error_msg, "_exception_obj": ValueError(error_msg), "agent_id": self.agent_id}
                                yield error_event
                                stream_had_error = True
                                break
                            
                            # CRITICAL FIX: Hard limit on output size to prevent runaway hallucinations bloating KV cache
                            if len(self.text_buffer) > 32000:  # ~8k tokens max
                                error_msg = "[LLM Error] Runaway generation detected (exceeded 32,000 chars). Terminating stream to prevent KV cache exhaustion."
                                logger.error(f"Agent {self.agent_id}: {error_msg}")
                                error_event = {"type": "error", "content": error_msg, "_exception_obj": ValueError(error_msg), "agent_id": self.agent_id}
                                yield error_event
                                stream_had_error = True
                                break


                        yield {"type": "response_chunk", "content": content, "agent_id": self.agent_id}
                    elif event_type == "native_tool_calls":
                        native_tool_calls_received.extend(event.get("tool_calls", []))
                    elif event_type == "finish_info":
                        finish_reason_received = event.get("done_reason")
                        self._last_reply_thinking_chars = int(event.get("thinking_chars") or 0)
                        self._last_reply_thinking_tail = str(event.get("thinking_tail") or "")
                        finish_event_data = event  # Round 2n: token counts for the context-window check below
                    elif event_type == "status": event["agent_id"] = self.agent_id; yield event
                    elif event_type == "error":
                        error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                        last_error_obj = event.get('_exception_obj', ValueError(error_content))
                        logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                        event["agent_id"] = self.agent_id; event["content"] = error_content; event["_exception_obj"] = last_error_obj; stream_had_error = True; yield event; break
                    else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
            logger.debug(f"Agent {self.agent_id}: Provider stream finished. Stream error: {stream_had_error}. Processing buffer.")
            # Round 2n: the real token counts of this reply, and whether it stopped because the CONTEXT WINDOW was full
            if finish_event_data:
                try:
                    self._last_prompt_tokens = int(finish_event_data.get("prompt_tokens") or 0)
                    self._last_output_tokens = int(finish_event_data.get("output_tokens") or 0)
                    self._last_num_ctx = int(finish_event_data["num_ctx"]) if finish_event_data.get("num_ctx") else None
                except (TypeError, ValueError):
                    pass
            context_full = False
            if finish_reason_received == "length" and not native_tool_calls_received and finish_event_data:
                from src.llm_providers.ollama_provider import is_context_window_full
                context_full = is_context_window_full(
                    finish_reason_received, finish_event_data.get("prompt_tokens"), finish_event_data.get("output_tokens"),
                    finish_event_data.get("num_ctx"), finish_event_data.get("num_predict"))
            # Round 2d: remember whether this reply was cut off at the output limit before it produced a tool call
            if context_full:
                # Round 2n: 2026-10-20 23:59 W1 (147 messages) filled the 32,768-token window; the framework took that for
                # an output cut-off, doubled the output budget and the Guardian blocked W1 for 2 "empty" replies. A full
                # window needs a shorter history (the cycle handler condenses it), not a larger output budget.
                self._context_window_full = True
                try:
                    # Round 3c: a cached prompt is reported smaller: a full window holds at least num_ctx - output tokens
                    window_tokens = int(finish_event_data.get("num_ctx") or 0) - int(finish_event_data.get("output_tokens") or 0)
                    if window_tokens > self._last_prompt_tokens:
                        self._last_prompt_tokens = window_tokens
                except (TypeError, ValueError):
                    pass
                logger.warning(f"Agent {self.agent_id}: the CONTEXT WINDOW is full ({finish_event_data.get('prompt_tokens')} "
                               f"prompt + {finish_event_data.get('output_tokens')} output tokens of num_ctx "
                               f"{finish_event_data.get('num_ctx')}); the history must be condensed.")
            elif finish_reason_received == "length" and not native_tool_calls_received:
                self._last_reply_cut_off = True
                self._consecutive_cut_off_replies = getattr(self, '_consecutive_cut_off_replies', 0) + 1
                # Round 2n: keep the larger budget for the rest of this state
                self._cut_off_boost_level = max(cut_off_boost_level(self), self._consecutive_cut_off_replies)
                self._cut_off_boost_state = self.state
                logger.warning(f"Agent {self.agent_id}: reply cut off at the output limit ({max_tokens_override} tokens) without a "
                               f"complete tool call ({self._consecutive_cut_off_replies} in a row).")
            elif finish_reason_received is not None:
                self._consecutive_cut_off_replies = 0
            # Round 2x: a reply that finished normally with output tokens but delivered nothing - its tool call was dropped
            if (not stream_had_error and finish_event_data and not context_full and not self._last_reply_cut_off):
                from src.llm_providers.ollama_provider import lost_output_tokens
                lost_tokens = lost_output_tokens(finish_reason_received, finish_event_data.get("output_tokens"),
                                                 self._last_reply_thinking_chars, len(complete_assistant_response.strip()),
                                                 len(native_tool_calls_received))
                if lost_tokens:
                    self._last_reply_output_lost = True
                    self._last_reply_lost_tokens = lost_tokens
                    self._consecutive_output_lost_replies = getattr(self, '_consecutive_output_lost_replies', 0) + 1
                    logger.warning(f"Agent {self.agent_id}: reply finished with {finish_event_data.get('output_tokens')} "
                                   f"output tokens but delivered no text and no tool call (output lost, "
                                   f"{self._consecutive_output_lost_replies} in a row) - most likely a malformed tool call.")
            if finish_reason_received is not None and not stream_had_error:
                if not self._last_reply_output_lost:
                    self._consecutive_output_lost_replies = 0
                if context_full or self._last_reply_cut_off or self._last_reply_output_lost:
                    self._consecutive_unusable_replies = getattr(self, '_consecutive_unusable_replies', 0) + 1
                else:
                    self._consecutive_unusable_replies = 0
            # Round 2h: a reply that only thought (no text, no tool call) is retried without thinking on every path - the
            # pm_startup kick-off retry too (PM1 thought 15,737 chars and wrote nothing, 2026-10-19 21:10-21:25)
            # Round 2x: not a reply whose output was lost - it did more than think; its tool call was dropped
            if (not stream_had_error and not native_tool_calls_received and not complete_assistant_response.strip()
                    and self._last_reply_thinking_chars > 0 and not self._last_reply_output_lost):
                self._think_off_next_call = True

            if not stream_had_error:
                logger.debug(f"Agent {self.agent_id}: Raw response before post-processing:\n>>>\n{complete_assistant_response}\n<<<")
                # Send raw response to UI for Internal Comms visibility
                if complete_assistant_response.strip():
                    yield {"type": "agent_raw_response", "content": complete_assistant_response, "agent_id": self.agent_id}
                buffer_to_process = self.text_buffer.strip() # Strip here for workflow processing
                original_complete_response = complete_assistant_response # Retain the full original response for potential error reporting

                # --- Check for Workflow Triggers First ---
                workflow_result: Optional['WorkflowResult'] = None
                if hasattr(self.manager, 'workflow_manager'):
                    workflow_result = await self.manager.workflow_manager.process_agent_output_for_workflow(
                        self.manager, self, buffer_to_process
                    )
                    if workflow_result:
                        logger.info(f"Agent {self.agent_id}: Workflow '{workflow_result.workflow_name}' executed. Success: {workflow_result.success}. Message: {workflow_result.message}")
                        yield {"type": "workflow_executed", "workflow_name": workflow_result.workflow_name, "result_data": workflow_result.model_dump() if hasattr(workflow_result, "model_dump") else workflow_result.dict(), "agent_id": self.agent_id}
                        # If a workflow was triggered and executed, its result dictates the next steps.
                        # The Agent.process_message should return here, and CycleHandler will use the WorkflowResult.
                        return
                # --- END Workflow Trigger Check ---

                # If no workflow was triggered, proceed with normal parsing (think, state_request, tools)
                think_content_extracted = None
                remaining_text_after_processing_tags = buffer_to_process # Use the stripped buffer

                think_match = self.think_pattern.search(remaining_text_after_processing_tags)
                if think_match:
                    extracted_think_content = think_match.group(1).strip()
                    full_think_block = think_match.group(0)
                    if extracted_think_content:
                        logger.info(f"Agent {self.agent_id}: Detected robust <think> tag content.")
                        think_content_extracted = extracted_think_content
                        yield {"type": "agent_thought", "content": extracted_think_content, "agent_id": self.agent_id}
                    remaining_text_after_processing_tags = remaining_text_after_processing_tags.replace(full_think_block, '', 1).strip()

                # --- PM Startup Missing Task List Check ---
                if think_content_extracted and \
                   not native_tool_calls_received and \
                   (not remaining_text_after_processing_tags or remaining_text_after_processing_tags.isspace()) and \
                   self.agent_type == AGENT_TYPE_PM and \
                   self.state == PM_STATE_STARTUP:
                    logger.warning(
                        f"Agent {self.agent_id} (PM) in state '{self.state}' provided only a <think> tag "
                        f"and no further content, but a task list is expected. Yielding specific event."
                    )
                    yield {"type": "pm_startup_missing_task_list_after_think", "agent_id": self.agent_id}
                    return
                # --- END PM Startup Missing Task List Check ---

                # --- Enhanced Completion Detection for PM Agents ---
                if self.agent_type == AGENT_TYPE_PM and think_content_extracted:
                    # Check if PM is thinking about project completion
                    completion_indicators = [
                        "project.*complete", "all.*tasks.*done", "work.*finished", 
                        "project.*finished", "nothing.*left.*to.*do", "no.*remaining.*tasks",
                        "all.*work.*completed", "ready.*to.*close", "project.*successful"
                    ]
                    
                    thinking_lower = think_content_extracted.lower()
                    completion_thoughts = any(re.search(pattern, thinking_lower) for pattern in completion_indicators)
                    
                    if completion_thoughts and self.state in [PM_STATE_MANAGE, PM_STATE_WORK, PM_STATE_AUDIT]:
                        logger.info(f"Agent {self.agent_id} (PM) showing completion thoughts. Checking project status.")
                        yield {"type": "pm_completion_detection", "agent_id": self.agent_id, "thinking_content": think_content_extracted}
                        # Allow processing to continue for normal tool calls or state changes
                # --- END Enhanced Completion Detection for PM Agents ---

                state_request_tag = None; requested_state = None
                cycle_handler_instance = getattr(self.manager, 'cycle_handler', None)
                manager_request_state_pattern = getattr(cycle_handler_instance, 'request_state_pattern', None) if cycle_handler_instance else None

                cleaned_for_state_regex = remaining_text_after_processing_tags # Already stripped
                if (cleaned_for_state_regex.startswith("```xml") and cleaned_for_state_regex.endswith("```")) or \
                   (cleaned_for_state_regex.startswith("```") and cleaned_for_state_regex.endswith("```")):
                    cleaned_for_state_regex = re.sub(r"^```(?:xml)?\s*|\s*```$", "", cleaned_for_state_regex).strip()
                if cleaned_for_state_regex.startswith("`") and cleaned_for_state_regex.endswith("`"):
                    cleaned_for_state_regex = cleaned_for_state_regex[1:-1].strip()

                # Check for state change requests, but don't return early if tools are also present
                state_change_to_yield = None
                if manager_request_state_pattern:
                    state_match = manager_request_state_pattern.search(cleaned_for_state_regex)
                    if state_match:
                        requested_state = state_match.group(1)
                        state_request_tag = state_match.group(0)
                        # Resolve any alias to canonical name before validation and event propagation
                        requested_state = self.manager.workflow_manager.resolve_state_alias(self.agent_type, requested_state)
                        is_valid_state_request = self.manager.workflow_manager.is_valid_state(self.agent_type, requested_state)
                        if is_valid_state_request:
                            logger.info(f"Agent {self.agent_id}: Detected valid state request tag for '{requested_state}': {state_request_tag}")
                            
                            task_description_for_work_state = None
                            task_id_from_tag = state_match.group(2) if state_match.lastindex and state_match.lastindex >= 2 else None
                            # If transitioning to 'work' state, capture the preceding text as the task.
                            if requested_state == ADMIN_STATE_WORK:
                                task_description_for_work_state = remaining_text_after_processing_tags.split(state_request_tag)[0].strip()
                                logger.info(f"Agent {self.agent_id}: Extracted task description for work state: '{task_description_for_work_state[:100]}...'")

                            state_change_to_yield = {
                                "type": "agent_state_change_requested",
                                "requested_state": requested_state,
                                "agent_id": self.agent_id,
                                "task_description": task_description_for_work_state,
                                "task_id": task_id_from_tag
                            }

                            if cleaned_for_state_regex == state_request_tag: # If the *entire cleaned output* was the state request
                                yield state_change_to_yield
                                return # State change is the only action
                            else:
                                # Remove state tag from remaining text for tool parsing, but don't return yet
                                remaining_text_after_processing_tags = remaining_text_after_processing_tags.replace(state_request_tag, '', 1).strip()
                        else:
                            logger.warning(f"Agent {self.agent_id}: Detected invalid state request '{requested_state}'. Ignoring tag: {state_request_tag}")
                            if cleaned_for_state_regex == state_request_tag:
                                yield {"type": "invalid_state_request_output", "content": state_request_tag, "agent_id": self.agent_id}; return
                            remaining_text_after_processing_tags = remaining_text_after_processing_tags.replace(state_request_tag, '', 1).strip()

                final_cleaned_response_for_tools_or_text = remaining_text_after_processing_tags

                tool_requests_to_yield = []
                valid_calls: List[Any] = []
                parsing_errors: List[Any] = []

                unknown_tool_requests: List[Dict[str, Any]] = []  # Round 2n
                if not native_tool_calls_received and self.manager and getattr(self.manager, 'tool_executor', None):
                    # Round 2z: a reply that is ONLY a tool call written as JSON text runs as that call. 2026-10-22 16:23:35
                    # Admin AI (OpenRouter, nemotron-3.5-lightning) replied '{"name": "request_state", "arguments": {"state":
                    # "planning"}}'; it was shown to the user as text and the project never started.
                    from src.llm_providers.tool_call_format import json_text_tool_calls
                    known_names = list(self.manager.tool_executor.tools.keys()) + ["request_state"]
                    text_calls = json_text_tool_calls(final_cleaned_response_for_tools_or_text, known_names)
                    if text_calls:
                        logger.info(f"Agent {self.agent_id}: reply was {len(text_calls)} tool call(s) written as JSON text; "
                                    "running them as native tool calls.")
                        native_tool_calls_received = text_calls
                        final_cleaned_response_for_tools_or_text = ""
                if native_tool_calls_received:
                    logger.info(f"Agent {self.agent_id} received {len(native_tool_calls_received)} native tool calls. Bypassing XML parsing.")
                    for call_data in native_tool_calls_received:
                        func = call_data.get("function", {})
                        tool_name = func.get("name")
                        try:
                            arguments = func.get("arguments", {})
                            if isinstance(arguments, str):
                                arguments = json.loads(arguments)
                            call_id = call_data.get("id", f"native_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}")
                            if self.agent_type == AGENT_TYPE_PM and self.state == PM_STATE_STARTUP:
                                logger.warning(f"Agent {self.agent_id}: Ignoring native call to '{tool_name}' in {PM_STATE_STARTUP} (no tools are offered; output the JSON kickoff plan).")
                            elif tool_name in self.manager.tool_executor.tools:
                                tool_requests_to_yield.append({"id": call_id, "name": tool_name, "arguments": arguments})
                            elif tool_name == "request_state":
                                requested_state = arguments.get("state")
                                if requested_state:
                                    logger.info(f"Agent {self.agent_id}: Detected native tool call for state change to '{requested_state}'.")
                                    requested_state = self.manager.workflow_manager.resolve_state_alias(self.agent_type, requested_state)
                                    if self.manager.workflow_manager.is_valid_state(self.agent_type, requested_state):
                                        state_change_to_yield = {
                                            "type": "agent_state_change_requested",
                                            "requested_state": requested_state,
                                            "agent_id": self.agent_id,
                                            "task_description": arguments.get("task_description"),
                                            "task_id": arguments.get("task_id")
                                        }
                                    else:
                                        logger.warning(f"Agent {self.agent_id}: Invalid native state request '{requested_state}'.")
                                        parsing_errors.append({"tool_name": tool_name, "error_message": f"Invalid state requested: {requested_state}", "xml_block": str(call_data)})
                            else:
                                # Round 2n: an unknown name ('manage_tool', 2026-10-20 23:16:39) was dropped silently and the
                                # model was told its reply was EMPTY. A name the executor can resolve (resolve_tool_name) is
                                # passed on; an unresolvable one is passed on only when the reply has no other call, so the
                                # model hears that the tool does not exist.
                                from src.tools.executor import resolve_tool_name
                                if resolve_tool_name(tool_name, self.manager.tool_executor.tools.keys()):
                                    logger.warning(f"Agent {self.agent_id}: Native Tool name '{tool_name}' not found; passing it "
                                                   "to the executor, which runs it as the tool it means.")
                                    tool_requests_to_yield.append({"id": call_id, "name": tool_name, "arguments": arguments})
                                else:
                                    logger.warning(f"Agent {self.agent_id}: Native Tool name '{tool_name}' not found in ToolExecutor.")
                                    unknown_tool_requests.append({"id": call_id, "name": tool_name, "arguments": arguments})
                        except Exception as e:
                            logger.error(f"Failed to parse native tool call arguments for {tool_name}: {e}")
                            parsing_errors.append({"tool_name": tool_name, "error_message": f"Native JSON Args Error: {str(e)}", "xml_block": str(call_data)})

                    if unknown_tool_requests and not tool_requests_to_yield and not state_change_to_yield:
                        tool_requests_to_yield.extend(unknown_tool_requests)  # Round 2n: the executor reports them

                elif self.manager.tool_executor and self.raw_xml_tool_call_pattern:
                    parsed_tool_calls_info = find_and_parse_xml_tool_calls(
                        final_cleaned_response_for_tools_or_text, self.manager.tool_executor.tools,
                        self.raw_xml_tool_call_pattern, self.markdown_xml_tool_call_pattern, self.agent_id
                    )
                    valid_calls = list(parsed_tool_calls_info["valid_calls"])
                    parsing_errors = list(parsed_tool_calls_info["parsing_errors"])

                    if parsing_errors: # Check for parsing errors first
                        first_error = parsing_errors[0]
                        logger.warning(f"Agent {self.agent_id}: Malformed XML tool call detected. Tool: {first_error['tool_name']}, Error: {first_error['error_message']}")
                        yield {
                            "type": "malformed_tool_call",
                            "tool_name": first_error["tool_name"],
                            "error_message": first_error["error_message"],
                            "malformed_xml_block": first_error["xml_block"],
                            "raw_assistant_response": original_complete_response, # Use the original full response here
                            "agent_id": self.agent_id
                        }
                        return # Stop processing this turn, agent needs to correct
                    
                    # Enhanced detection for potential tool calls that weren't parsed
                    elif not valid_calls and hasattr(self.manager, 'cycle_handler') and \
                         hasattr(self.manager.cycle_handler, '_detect_potential_tool_calls') and \
                         self.manager.cycle_handler._detect_potential_tool_calls(final_cleaned_response_for_tools_or_text):
                        logger.warning(f"Agent {self.agent_id}: Detected potential tool calls but parsing failed completely.")
                        
                        # Diagnose specific XML issues to give better feedback
                        issues = []
                        tool_names = list(self.manager.tool_executor.tools.keys()) if self.manager.tool_executor else []
                        for tool in tool_names:
                            open_tags = len(re.findall(rf"<{tool}\b[^>]*>", final_cleaned_response_for_tools_or_text, re.IGNORECASE))
                            close_tags = len(re.findall(rf"</{tool}>", final_cleaned_response_for_tools_or_text, re.IGNORECASE))
                            if open_tags > close_tags:
                                issues.append(f"You opened <{tool}> {open_tags} time(s) but only closed it {close_tags} time(s). Every tool call MUST end with </{tool}>.")
                            elif close_tags > open_tags:
                                issues.append(f"You have {close_tags} </{tool}> closing tags but only {open_tags} opening <{tool}> tags.")
                        
                        if not issues:
                            issues.append("Check for missing ending tags (like </action> or </content>), unescaped characters like '<' or '&' inside your parameters, or text appearing outside the XML blocks.")
                            
                        specific_error_msg = "Tool call detected but parsing failed. Please fix these issues:\n- " + "\n- ".join(issues)
                        
                        yield {
                            "type": "malformed_tool_call",
                            "tool_name": "unknown", 
                            "error_message": specific_error_msg,
                            "malformed_xml_block": final_cleaned_response_for_tools_or_text,
                            "raw_assistant_response": original_complete_response,
                            "agent_id": self.agent_id
                        }
                        return # Stop processing this turn, agent needs to correct

                    if valid_calls:
                        calls_to_process = valid_calls

                        if len(calls_to_process) > 1:
                             logger.info(f"Agent {self.agent_id} found {len(calls_to_process)} tool calls in a single response. Processing all.")

                        for call_data in calls_to_process:
                            tool_name_call, tool_args, _ = call_data
                            if tool_name_call in self.manager.tool_executor.tools:
                                call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                                tool_requests_to_yield.append({"id": call_id, "name": tool_name_call, "arguments": tool_args})
                            else:
                                logger.warning(f"Agent {self.agent_id}: Tool name '{tool_name_call}' parsed from LLM output, but not found in ToolExecutor. Skipping this call.")

                if tool_requests_to_yield:
                    logger.debug(f"Agent {self.agent_id} preparing to yield {len(tool_requests_to_yield)} tool requests.")

                    # We no longer remove the tool call XML from the content so the LLM remembers its actions.
                    # This prevents the LLM from being "blind" to the exact parameters it just generated.
                    content_for_history = final_cleaned_response_for_tools_or_text
                    # if valid_calls:
                    #     # Sort spans in reverse order to avoid index shifting during removal
                    #     sorted_spans = sorted([call[2] for call in valid_calls], reverse=True)
                    #     
                    #     for start, end in sorted_spans:
                    #         # Remove the tool call XML from the content
                    #         content_for_history = content_for_history[:start] + content_for_history[end:]

                    content_for_history = content_for_history.strip()

                    yield {
                        "type": "tool_requests",
                        "calls": tool_requests_to_yield,
                        "raw_assistant_response": original_complete_response,
                        "content_for_history": content_for_history, # Pass cleaned content
                        "agent_id": self.agent_id
                    }
                    # If there was also a state change request, yield it after the tools
                    if state_change_to_yield:
                        logger.debug(f"Agent {self.agent_id}: Also yielding state change after tool requests.")
                        yield state_change_to_yield
                    return
                else:
                    # No tool requests yielded
                    
                    # --- PM Startup Missing Task List Check (Moved here to allow tool use first) ---
                    if self.agent_type == AGENT_TYPE_PM and \
                       self.state == PM_STATE_STARTUP and \
                       not state_change_to_yield:

                        pm_kickoff_trigger_tag = "kickoff_plan"
                        if hasattr(self.manager, 'workflow_manager') and self.manager.workflow_manager and \
                           hasattr(self.manager.workflow_manager, '_workflow_triggers'):
                            for trigger_key, wf_instance in self.manager.workflow_manager._workflow_triggers.items():
                                if hasattr(wf_instance, 'name') and wf_instance.name == "pm_project_kickoff":
                                    pm_kickoff_trigger_tag = getattr(wf_instance, 'trigger_tag_name', "kickoff_plan")
                                    break

                        # If we reached here without returning, the JSON workflow trigger did not fire successfully.
                        logger.warning(
                            f"Agent {self.agent_id} (PM) in state '{self.state}' did not output a valid kickoff plan. "
                            f"Forcing retry by re-requesting current state with feedback. Output was: '{final_cleaned_response_for_tools_or_text[:200]}...'"
                        )
                        feedback_content = (
                            f"[Framework Feedback for PM Retry]\n"
                            f"Your previous output was not recognized as a valid project kickoff plan.\n"
                            f"Please review your instructions for the '{self.state}' state and ensure your response contains the required JSON code block. "
                            f"Do not include conversational text outside of your `<think>` tags.\n"
                            f"The JSON must be strictly valid: every string value (including references such as \"depends_on\": \"task_1\") must be in double quotes; no comments, no trailing commas, no bare words."
                        )
                        self.message_history.append({"role": "system", "content": feedback_content})
                        yield {"type": "agent_state_change_requested", "requested_state": PM_STATE_STARTUP, "agent_id": self.agent_id}
                        return
                    # --- END PM Startup Missing Task List Check ---

                    if state_change_to_yield:
                        # Only a state change (no tools)
                        yield state_change_to_yield
                        return
                    elif final_cleaned_response_for_tools_or_text and is_call_imitation_text(final_cleaned_response_for_tools_or_text):
                        # Round 2o: the reply only imitated a tool call in text ("[Action Taken: called ...]"): nothing ran.
                        # It is not shown, not reviewed and not kept; the empty-reply retry tells the agent to really call.
                        self._call_imitation_text = final_cleaned_response_for_tools_or_text
                        logger.warning(f"Agent {self.agent_id}: reply only imitated a tool call in text, nothing was executed: "
                                       f"'{final_cleaned_response_for_tools_or_text[:150]}'")
                        return
                    elif final_cleaned_response_for_tools_or_text:
                        # Only a final textual response
                        yield {"type": "final_response", "content": final_cleaned_response_for_tools_or_text, "agent_id": self.agent_id}
                        return
                    else:
                        if think_content_extracted:
                            logger.info(f"Agent {self.agent_id}: Only a <think> block was produced. No further output.")
                        else:
                            logger.info(f"Agent {self.agent_id}: Buffer empty after processing tags. No final response or action yielded.")
                        return
            else:
                logger.warning(f"Agent {self.agent_id}: Skipping final tag/tool parsing due to stream error."); return

        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]", "_exception_obj": e}; return
        finally:
            self.text_buffer = ""
            logger.info(f"Agent {self.agent_id}: Finished processing cycle attempt. Status before CycleHandler: {self.status}")

    def get_state(self) -> Dict[str, Any]:
        estimated_tokens = 0
        max_tokens = 8192 # default Fallback
        
        # Estimate context sequence tokens
        if self.manager:
            from src.agents.cycle_components.context_summarizer import get_context_summarizer
            summarizer = get_context_summarizer(self.manager)
            estimated_tokens = summarizer.estimate_token_count(self.message_history)
            
            # Extract actual numerical maximum from provider's model details
            try:
                registry = getattr(self.manager, 'model_registry', None)
                if registry and hasattr(registry, 'get_model_info'):
                    model_info = registry.get_model_info(self.model)
                    if model_info:
                        num_ctx = model_info.get("model_num_ctx")
                        if num_ctx:
                            max_tokens = num_ctx
                            # Round 2n: an Ollama model really gets the capped window
                            from src.config.model_registry import base_provider_type
                            if base_provider_type(self.provider_name) == "ollama":
                                from src.llm_providers.ollama_provider import effective_ollama_num_ctx
                                max_tokens = effective_ollama_num_ctx(num_ctx)
            except Exception:
                pass
                
        state_info = {
            "agent_id": self.agent_id, "persona": self.persona, "status": self.status, "state": self.state,
            "agent_type": self.agent_type, "provider": self.provider_name, "model": self.model,
            "temperature": self.temperature, "message_history_length": len(self.message_history),
            "estimated_tokens": estimated_tokens, "max_tokens": max_tokens,
            "sandbox_path": str(self.sandbox_path),
            "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None)
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info: state_info["current_tool"] = self.current_tool_info
        if self.status == AGENT_STATUS_PLANNING and self.current_plan: state_info["current_plan"] = self.current_plan
        return state_info

    def clear_history(self):
        logger.info(f"Clearing message history for Agent {self.agent_id}"); self.message_history = []
