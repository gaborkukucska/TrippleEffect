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
import logging # Added logging

# Import settings for defaults and BASE_DIR
from src.config.settings import settings, BASE_DIR

# Import BaseLLMProvider for type hinting and interface adherence
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolResultDict

# Import the parser function
from src.agents.agent_tool_parser import find_and_parse_xml_tool_calls

# --- Import status and state constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_ERROR,
    ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, # Added Admin States
    AGENT_STATE_WORK, AGENT_TYPE_PM # Added AGENT_STATE_WORK, AGENT_TYPE_PM
)
# --- END Import status and state constants ---

# Import AgentManager for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager # Removed BOOTSTRAP_AGENT_ID import here
# Import the constant directly
from src.agents.constants import BOOTSTRAP_AGENT_ID

logger = logging.getLogger(__name__)

# Tool Call Patterns (XML only)
XML_TOOL_CALL_PATTERN = None # Compiled in __init__
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})>[\s\S]*?</\2>)\s*\n?```" # Compiled in __init__
# Plan/Think Tag Patterns
PLAN_TAG_PATTERN = r"<plan>([\s\S]*?)</plan>" # Pattern to extract plan content
THINK_TAG_PATTERN = r"<think>([\s\S]*?)</think>" # Pattern to extract think content
# --- NEW: Robust think pattern ---
ROBUST_THINK_TAG_PATTERN = re.compile(r"<think>(.*?)(?:</think>|<(?=[^/]))", re.DOTALL | re.IGNORECASE) # Capture until </think> OR the next opening tag
# --- END NEW ---

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating via an injected LLM provider, managing its sandbox,
    parsing XML tool calls from responses (handling potential markdown fences),
    detecting planning phases, and yielding requests. Tracks its own status.
    Relies on the system_prompt within its config for all instructions.
    """
    def __init__(
        self,
        agent_config: Dict[str, Any],
        llm_provider: BaseLLMProvider, # Inject the provider instance
        manager: 'AgentManager' # Manager is now required
        ):
        """
        Initializes an Agent instance using configuration and injected dependencies.
        The final system prompt, including tool descriptions, should be present
        within the agent_config dictionary. Also compiles tool parsing regex.
        """
        config: Dict[str, Any] = agent_config.get("config", {})
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")
        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL) # This will be set correctly later by lifecycle
        # Initialize final_system_prompt as empty. It will be set by AgentWorkflowManager via CycleHandler before the first call.
        self.final_system_prompt: str = ""
        # Store the original config prompt separately if needed (e.g., for Admin AI personality)
        self._config_system_prompt: str = config.get("system_prompt", "")
        # if not self.final_system_prompt: logger.error(f"Agent {self.agent_id}: 'system_prompt' is missing or empty!"); self.final_system_prompt = "You are a helpful assistant." # Removed this check
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        # --- NEW: Add agent_type ---
        self.agent_type: str = config.get("agent_type", "worker") # Default to worker if not specified
        # --- END NEW ---
        self.agent_config: Dict[str, Any] = agent_config
        # Exclude max_tokens here as it's handled explicitly in process_message
        # Also exclude system_prompt from provider_kwargs now
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'api_key', 'base_url', 'referer', 'max_tokens']}
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager
        # logger.debug(f"Agent {self.agent_id} using final system prompt (first 500 chars):\n{self.final_system_prompt[:500]}...") # Removed log as prompt is initially empty

        # State management
        self.status: str = AGENT_STATUS_IDLE # Operational status (idle, processing, etc.)
        # --- NEW: Add workflow state (initialized by lifecycle) ---
        self.state: Optional[str] = None # Initial state set by agent_lifecycle based on type
        # --- END NEW ---
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.current_plan: Optional[str] = None # Stores plan content when status is PLANNING
        # Initialize history empty. CycleHandler adds the system prompt before the first call.
        self.current_task_id: Optional[str] = None  # NEW: Current task ID
        self.message_history: List[MessageDict] = []
        # self.message_history.append({"role": "system", "content": self.final_system_prompt}) # Removed initial system message
        self._last_api_key_used: Optional[str] = None
        self._failed_models_this_cycle: set = set()
        # --- NEW: Flag for PM initial tool call ---
        self._pm_needs_initial_list_tools: bool = False # Set by WorkflowManager
        # --- END NEW ---
        # --- NEW: Flag for project approval status ---
        self._awaiting_project_approval: bool = False # Set by Manager during PM creation, cleared by API on approval
        # --- END NEW ---

        # Buffers for processing stream
        self.text_buffer: str = ""

        # Sandboxing
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"

        # Compile regex patterns
        self.raw_xml_tool_call_pattern = None
        self.markdown_xml_tool_call_pattern = None
        self.plan_pattern = re.compile(PLAN_TAG_PATTERN, re.IGNORECASE | re.DOTALL) # Compile plan pattern
        # self.think_pattern = re.compile(THINK_TAG_PATTERN, re.IGNORECASE | re.DOTALL) # Use ROBUST_THINK_TAG_PATTERN instead
        self.think_pattern = ROBUST_THINK_TAG_PATTERN # Use the robust one
        if self.manager and self.manager.tool_executor and self.manager.tool_executor.tools:
            tool_names = list(self.manager.tool_executor.tools.keys())
            if tool_names:
                safe_tool_names_lower = [re.escape(name.lower()) for name in tool_names]
                tool_names_pattern_group_lower = '|'.join(safe_tool_names_lower)
                raw_pattern_str = rf"<({tool_names_pattern_group_lower})>([\s\S]*?)</\1>"
                self.raw_xml_tool_call_pattern = re.compile(raw_pattern_str, re.IGNORECASE | re.DOTALL)
                md_xml_pattern_str = MARKDOWN_FENCE_XML_PATTERN.format(tool_names=tool_names_pattern_group_lower)
                self.markdown_xml_tool_call_pattern = re.compile(md_xml_pattern_str, re.IGNORECASE | re.DOTALL | re.MULTILINE)
                logger.info(f"Agent {self.agent_id}: Compiled XML tool patterns for tools: {tool_names}")
                logger.debug(f"Agent {self.agent_id}: Raw XML pattern: {self.raw_xml_tool_call_pattern.pattern}")
            else: logger.info(f"Agent {self.agent_id}: No tools found in executor, tool parsing disabled.")
        else: logger.warning(f"Agent {self.agent_id}: Manager or ToolExecutor not available during init, tool parsing disabled.")

        logger.info(f"Agent {self.agent_id} ({self.persona}) initialized. Type: {self.agent_type}. Status: {self.status}. State: {self.state}. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. LLM Provider Instance: {self.llm_provider}")

    # --- Status Management ---
    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None, plan_info: Optional[str] = None):
        """Updates the agent's status and optionally tool or plan info."""
        if self.status != new_status: logger.info(f"Agent {self.agent_id}: Status changed from '{self.status}' to '{new_status}'")
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        self.current_plan = plan_info if new_status == AGENT_STATUS_PLANNING else None
        if self.manager: asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else: logger.warning(f"Agent {self.agent_id}: Manager not set, cannot push status update.")

    # --- NEW: Workflow State Management ---
    def set_state(self, new_state: str):
        """Updates the agent's high-level workflow state."""
        # Optional: Add validation against defined ADMIN_STATE constants if needed
        if self.state != new_state:
            logger.info(f"Agent {self.agent_id}: Workflow State changed from '{self.state}' to '{new_state}'")
            self.state = new_state
            # Optionally push this state change to UI if needed, though status updates might be sufficient
            # if self.manager: asyncio.create_task(self.manager.push_agent_status_update(self.agent_id)) # Re-push status to include new state?
        else:
             logger.debug(f"Agent {self.agent_id}: set_state called with current state '{new_state}'. No change.")
    # --- END NEW ---

    # --- Dependency Setters ---
    def set_manager(self, manager: 'AgentManager'): self.manager = manager
    def set_tool_executor(self, tool_executor: Any): logger.warning(f"Agent {self.agent_id}: set_tool_executor called but ToolExecutor is no longer directly used by Agent.")

    # --- Sandbox Creation ---
    def ensure_sandbox_exists(self) -> bool:
        """Creates the agent's sandbox directory if it doesn't exist."""
        try: self.sandbox_path.mkdir(parents=True, exist_ok=True); return True
        except OSError as e: logger.error(f"Error creating sandbox directory for Agent {self.agent_id} at {self.sandbox_path}: {e}"); return False
        except Exception as e: logger.error(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}", exc_info=True); return False

    # --- Main Processing Logic ---
    async def process_message(self, history_override: Optional[List[MessageDict]] = None) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Processes the task based on the current message history (or override) using the LLM provider.
        Detects plan generation (<plan>...</plan>) for Admin AI.
        Parses the response stream for XML tool calls (using agent_tool_parser) and yields requests.
        Relies on CycleHandler for retry/failover logic.
        Enforces stricter output format for PM agent in 'work' state.

        Args:
            history_override (Optional[List[MessageDict]]): If provided, use this message
                history for the LLM call instead of the agent's internal history.
                Used for injecting transient context like health reports or time.
        """
        history_to_use = history_override if history_override is not None else self.message_history

        if self.status not in [AGENT_STATUS_IDLE]: logger.warning(f"Agent {self.agent_id} process_message called but agent is not idle (Status: {self.status})."); yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}; return
        if not self.llm_provider: logger.error(f"Agent {self.agent_id}: LLM Provider not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}; return
        if not self.manager: logger.error(f"Agent {self.agent_id}: Manager not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: Manager not configured]"}; return

        self.set_status(AGENT_STATUS_PROCESSING); self.text_buffer = ""; complete_assistant_response = ""; stream_had_error = False; last_error_obj = None; yielded_chunks = False # Added yielded_chunks flag
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(history_to_use)}")
        try:
            if not self.ensure_sandbox_exists(): self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]"}; return

            # --- NEW: Determine max_tokens based on state ---
            max_tokens_override = None
            if self.agent_type == AGENT_TYPE_PM and self.state == AGENT_STATE_WORK:
                max_tokens_override = settings.PM_WORK_STATE_MAX_TOKENS # Use configured limit
                logger.debug(f"Agent {self.agent_id}: Applying max_tokens limit of {max_tokens_override} for PM work state.")
            # --- END NEW ---

            provider_stream = self.llm_provider.stream_completion(
                messages=history_to_use,
                model=self.model,
                temperature=self.temperature,
                max_tokens=max_tokens_override, # Pass the override
                **self.provider_kwargs
            )

            content: Optional[str] = None
            async for event in provider_stream:
                event_type = event.get("type")
                if event_type == "response_chunk":
                     content = event.get("content", "");
                     if content:
                         self.text_buffer += content; complete_assistant_response += content
                         yielded_chunks = True # Set flag when chunk is yielded
                         # --- MODIFIED: Add agent_id to response_chunk event ---
                         event_to_yield = {"type": "response_chunk", "content": content, "agent_id": self.agent_id}
                         logger.debug(f"CORE YIELD (Chunk): {event_to_yield}") # <<< TEMP LOGGING
                         yield event_to_yield
                         # --- END MODIFICATION ---
                elif event_type == "status":
                    event["agent_id"] = self.agent_id
                    logger.debug(f"CORE YIELD (Status): {event}") # <<< TEMP LOGGING
                    yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    last_error_obj = event.get('_exception_obj', ValueError(error_content))
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id; event["content"] = error_content; event["_exception_obj"] = last_error_obj; stream_had_error = True; yield event
                else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
            logger.debug(f"Agent {self.agent_id}: Provider stream finished processing. Stream had error: {stream_had_error}. Processing final buffer.")

            if not stream_had_error:
                # --- ADDED: Log complete raw response before parsing ---
                logger.debug(f"Agent {self.agent_id}: Complete raw response buffer before post-processing:\n>>>\n{complete_assistant_response}\n<<<")
                # --- END ADDED ---
                # --- POST-STREAM PROCESSING ---
                buffer_to_process = self.text_buffer # Working copy
                logger.debug(f"CORE POST-STREAM: Buffer='{buffer_to_process[:100]}...', yielded_chunks={yielded_chunks}")
                original_complete_response = complete_assistant_response # Keep original for history if plan submitted
                state_change_requested = False # Flag to track if state change was handled
                plan_submitted = False # Flag to track if plan was handled
                think_content_extracted = None # Store extracted think content
                tool_calls_found = [] # Store parsed tool calls

                # --- MOVED: Process Think Tag FIRST ---
                think_match = self.think_pattern.search(buffer_to_process) # Use robust pattern
                if think_match:
                        extracted_think_content = think_match.group(1).strip()
                        full_think_block = think_match.group(0) # Get the exact matched block
                        if extracted_think_content:
                            logger.info(f"Agent {self.agent_id}: Detected robust <think> tag content.")
                            think_content_extracted = extracted_think_content # Store it
                            event_to_yield = {"type": "agent_thought", "content": extracted_think_content, "agent_id": self.agent_id}
                            logger.debug(f"CORE YIELD (Thought): {event_to_yield}")
                            yield event_to_yield
                        else:
                             logger.info(f"Agent {self.agent_id}: Found <think> tag but content was empty.")
                        # Remove the matched think block
                        buffer_to_process = buffer_to_process.replace(full_think_block, '', 1).strip()
                # --- END MOVED ---

                # 1. Check for State Request FIRST (Robust Search)
                state_request_tag = None
                requested_state = None
                cycle_handler_instance = getattr(self.manager, 'cycle_handler', None)
                manager_request_state_pattern = getattr(cycle_handler_instance, 'request_state_pattern', None) if cycle_handler_instance else None

                if manager_request_state_pattern:
                    state_match = manager_request_state_pattern.search(buffer_to_process)
                    if state_match:
                        requested_state = state_match.group(1)
                        state_request_tag = state_match.group(0)
                        # --- ADDED: Check if the requested state is specifically 'planning' ---
                        if requested_state == 'planning':
                            logger.info(f"Agent {self.agent_id}: Detected EXACT state request tag for 'planning': {state_request_tag}")
                            # --- MODIFIED: Yield remaining text BEFORE state change ---
                            remaining_text_after_tag = buffer_to_process.replace(state_request_tag, '', 1).strip()
                            if remaining_text_after_tag:
                                logger.debug(f"Agent {self.agent_id}: Found remaining text after state tag: '{remaining_text_after_tag[:50]}...' Yielding as final_response first.")
                                # Add to history if needed
                                if not self.message_history or self.message_history[-1].get("content") != remaining_text_after_tag or self.message_history[-1].get("role") != "assistant":
                                    self.message_history.append({"role": "assistant", "content": remaining_text_after_tag})
                                # Yield final response
                                final_response_event = {"type": "final_response", "content": remaining_text_after_tag}
                                logger.debug(f"CORE YIELD (Final Response before State Change): {final_response_event}")
                                yield final_response_event
                            # Now yield the state change request
                            event_to_yield = {"type": "agent_state_change_requested", "requested_state": requested_state, "agent_id": self.agent_id}
                            logger.debug(f"CORE YIELD (State Request): {event_to_yield}")
                            yield event_to_yield # <<< YIELDS the request
                            # --- ADDED: Stop processing immediately after yielding state change request ---
                            logger.info(f"Agent {self.agent_id}: State change requested to '{requested_state}'. Terminating current cycle processing.")
                            return # Stop the generator here
                            # --- END ADDED ---
                        else:
                            # Log that a state change tag was found, but not for 'planning'
                            logger.warning(f"Agent {self.agent_id}: Detected state request tag for state '{requested_state}', but only 'planning' is currently processed here. Ignoring tag: {state_request_tag}")
                            # Continue processing the buffer as if the tag wasn't a valid state change request
                            buffer_to_process = buffer_to_process # Ensure buffer remains unchanged for further processing
                        # --- END ADDED ---


                # 2. Check for Think Tag NEXT (Robust Extraction)
                        if remaining_text_after_tag:
                            logger.debug(f"Agent {self.agent_id}: Found remaining text after state tag: '{remaining_text_after_tag[:50]}...' Yielding as final_response first.")
                            # Add to history if needed
                            if not self.message_history or self.message_history[-1].get("content") != remaining_text_after_tag or self.message_history[-1].get("role") != "assistant":
                                self.message_history.append({"role": "assistant", "content": remaining_text_after_tag})
                            # Yield final response
                            final_response_event = {"type": "final_response", "content": remaining_text_after_tag}
                            logger.debug(f"CORE YIELD (Final Response before State Change): {final_response_event}")
                            yield final_response_event
                        # Now yield the state change request
                        event_to_yield = {"type": "agent_state_change_requested", "requested_state": requested_state, "agent_id": self.agent_id}
                        logger.debug(f"CORE YIELD (State Request): {event_to_yield}")
                        yield event_to_yield # <<< YIELDS the request
                        # --- ADDED: Stop processing immediately after yielding state change request ---
                        logger.info(f"Agent {self.agent_id}: State change requested to '{requested_state}'. Terminating current cycle processing.")
                        return # Stop the generator here
                        # --- END ADDED ---
                        # state_change_requested = True # No longer needed
                        # buffer_to_process = remaining_text_after_tag # No longer needed
                        # logger.debug(f"Agent {self.agent_id}: Continuing buffer processing after state change request...") # No longer needed


                # 3. Check for Plan Tag (Admin AI in PLANNING state only)
                # Only process plan tag if no state change was requested
                if not state_change_requested:
                    plan_content = None
                    is_admin_planning = (self.agent_id == BOOTSTRAP_AGENT_ID and getattr(self, 'state', None) == ADMIN_STATE_PLANNING)
                    plan_match = self.plan_pattern.search(buffer_to_process) # Search the potentially cleaned buffer

                    if is_admin_planning and plan_match:
                        plan_content = plan_match.group(1).strip()
                        logger.info(f"Agent {self.agent_id}: Detected <plan> tag while in PLANNING state. Yielding admin_plan_submitted event.")
                        self.set_status(AGENT_STATUS_PLANNING, plan_info=plan_content)
                        if original_complete_response and (not self.message_history or self.message_history[-1].get("content") != original_complete_response or self.message_history[-1].get("role") != "assistant"):
                            self.message_history.append({"role": "assistant", "content": original_complete_response})
                        event_to_yield = {"type": "admin_plan_submitted", "plan_content": plan_content, "agent_id": self.agent_id}
                        logger.debug(f"CORE YIELD (Plan): {event_to_yield}")
                        yield event_to_yield
                        plan_submitted = True # Mark plan as submitted
                        buffer_to_process = self.plan_pattern.sub('', buffer_to_process).strip() # Clean buffer

                    elif plan_match: # Log if plan tag found in wrong state/agent
                         logger.warning(f"Agent {self.agent_id}: Detected <plan> tag but agent is not Admin AI in PLANNING state. Ignoring plan tag.")
                         # Still remove the tag even if ignored
                         buffer_to_process = self.plan_pattern.sub('', buffer_to_process).strip()


                # 4. Process remaining buffer for Tool Calls
                # Process tools regardless of whether state change or plan was submitted, using the potentially cleaned buffer
                # if not state_change_requested and not plan_submitted: # REMOVED Condition
                final_cleaned_response = buffer_to_process # Use the buffer after state/think/plan removal
                if self.manager.tool_executor:
                    tool_calls_found = find_and_parse_xml_tool_calls( final_cleaned_response, self.manager.tool_executor.tools, self.raw_xml_tool_call_pattern, self.markdown_xml_tool_call_pattern, self.agent_id )

                    # --- NEW: Stricter validation for PM in WORK state ---
                    is_pm_work_state = (self.agent_type == AGENT_TYPE_PM and self.state == AGENT_STATE_WORK)
                    if is_pm_work_state:
                        # Expecting ONLY a single tool call after optional <think>
                        if tool_calls_found and len(tool_calls_found) == 1:
                            # Extract the matched tool call string using start/end indices
                            match_start, match_end = tool_calls_found[0][2]
                            matched_tool_string = final_cleaned_response[match_start:match_end]
                            # Check if removing the matched tool string leaves any non-whitespace characters
                            if not final_cleaned_response.replace(matched_tool_string, '', 1).strip(): # <-- FIXED HERE
                                logger.debug(f"PM Agent {self.agent_id} in WORK state: Valid output format found (single tool call).")
                                # Proceed to yield tool request below
                            else:
                                # Invalid format: Found a single tool call but also other text
                                error_msg = f"PM Agent {self.agent_id} in WORK state provided invalid output. Expected ONLY <think> (optional) + single XML tool call. Found extra text around tool call: '{final_cleaned_response[:200]}...'"
                                logger.error(error_msg)
                                yield {"type": "error", "agent_id": self.agent_id, "content": f"[Framework Violation] {error_msg}", "_exception_obj": ValueError(error_msg)}
                                tool_calls_found = [] # Clear calls
                                final_cleaned_response = "" # Clear response
                        elif not tool_calls_found and not final_cleaned_response:
                             logger.info(f"PM Agent {self.agent_id} in WORK state: No tool call or text found. Assuming cycle completion (no action needed).")
                             # Let the loop finish, CycleHandler will set to IDLE if no reactivation needed
                        else:
                            # Invalid format for PM work state (e.g., multiple tools, or just text)
                            error_msg = f"PM Agent {self.agent_id} in WORK state provided invalid output. Expected ONLY <think> (optional) + single XML tool call OR ONLY <request_state>. Found: '{final_cleaned_response[:200]}...'"
                            logger.error(error_msg)
                            # Yield an error event instead of tool request or final response
                            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Framework Violation] {error_msg}", "_exception_obj": ValueError(error_msg)}
                            tool_calls_found = [] # Clear any potentially parsed calls to prevent yielding below
                            final_cleaned_response = "" # Clear response to prevent yielding final_response
                    # --- END Stricter validation ---

                    if tool_calls_found:
                        logger.info(f"Agent {self.agent_id}: {len(tool_calls_found)} tool call(s) found in final cleaned buffer.")
                        tool_requests_list = []
                        for tool_name, tool_args, (match_start, match_end) in tool_calls_found:
                            call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                            tool_requests_list.append({"id": call_id, "name": tool_name, "arguments": tool_args})
                            await asyncio.sleep(0.001)
                        self.set_status(AGENT_STATUS_AWAITING_TOOL)
                        logger.info(f"Agent {self.agent_id}: Yielding {len(tool_requests_list)} XML tool request(s).")
                        # Use original_complete_response for history if think tag was present
                        response_for_history = original_complete_response if think_content_extracted else final_cleaned_response
                        if response_for_history and (not self.message_history or self.message_history[-1].get("content") != response_for_history or self.message_history[-1].get("role") != "assistant"):
                            self.message_history.append({"role": "assistant", "content": response_for_history})
                        event_to_yield = {"type": "tool_requests", "calls": tool_requests_list, "raw_assistant_response": response_for_history}
                        logger.debug(f"CORE YIELD (Tool Request): {event_to_yield}")
                        yield event_to_yield
                        # Stop processing after yielding tool requests
                        # --- MODIFICATION: Only return if tools were actually found and yielded ---
                        if tool_requests_list:
                            return
                        # --- END MODIFICATION ---
                    # else: # No tools found

                    # 5. Yield Final Response if anything remains and no tools/plan/state change handled it
                    # Check if anything remains *after* all tag processing attempts
                    final_cleaned_response = buffer_to_process # Re-check buffer
                    if final_cleaned_response and not tool_calls_found and not plan_submitted and not state_change_requested:
                        # Only yield final response if no other action was taken based on tags
                        logger.debug(f"Agent {self.agent_id}: Yielding final_response event for remaining text.")
                        if not self.message_history or self.message_history[-1].get("content") != final_cleaned_response or self.message_history[-1].get("role") != "assistant":
                            self.message_history.append({"role": "assistant", "content": final_cleaned_response})
                        event_to_yield = {"type": "final_response", "content": final_cleaned_response}
                        logger.debug(f"CORE YIELD (Final Response): {event_to_yield}")
                        yield event_to_yield
                    elif not final_cleaned_response and not tool_calls_found and not plan_submitted and not state_change_requested:
                         logger.info(f"Agent {self.agent_id}: Buffer is empty after all tag processing. No final response yielded.")
                # --- END Post-stream processing ---

            else: # stream_had_error
                logger.warning(f"Agent {self.agent_id}: Skipping final tag/tool parsing due to stream error.")
        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]", "_exception_obj": e}
        finally:
            self.text_buffer = ""; logger.info(f"Agent {self.agent_id}: Finished processing cycle attempt. Final Status before CycleHandler action: {self.status}")

    # --- get_state and clear_history ---
    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent, including operational status and workflow state."""
        state_info = {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "status": self.status, # Operational status
            "state": self.state, # Workflow state (e.g., conversation, planning)
            "agent_type": self.agent_type, # Agent type (admin, pm, worker) - NEW
            "provider": self.provider_name,
            "model": self.model,
            "temperature": self.temperature,
            "message_history_length": len(self.message_history),
            "sandbox_path": str(self.sandbox_path),
            "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None)
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info: state_info["current_tool"] = self.current_tool_info
        if self.status == AGENT_STATUS_PLANNING and self.current_plan: state_info["current_plan"] = self.current_plan
        return state_info

    def clear_history(self):
        """Clears message history. The system prompt will be added by CycleHandler."""
        # The system prompt is now added dynamically by CycleHandler, so just clear the list.
        logger.info(f"Clearing message history for Agent {self.agent_id}"); self.message_history = []
