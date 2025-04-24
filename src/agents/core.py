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

# --- NEW: Import status constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_ERROR
)
# --- END NEW ---

# Import AgentManager for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Tool Call Patterns (XML only)
XML_TOOL_CALL_PATTERN = None # Compiled in __init__
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})>[\s\S]*?</\2>)\s*\n?```" # Compiled in __init__
# Plan/Think Tag Patterns
PLAN_TAG_PATTERN = r"<plan>([\s\S]*?)</plan>" # Pattern to extract plan content
THINK_TAG_PATTERN = r"<think>([\s\S]*?)</think>" # Pattern to extract think content

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
        self.final_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        if not self.final_system_prompt: logger.error(f"Agent {self.agent_id}: 'system_prompt' is missing or empty!"); self.final_system_prompt = "You are a helpful assistant."
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        self.agent_config: Dict[str, Any] = agent_config
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager
        logger.debug(f"Agent {self.agent_id} using final system prompt (first 500 chars):\n{self.final_system_prompt[:500]}...")

        # State management
        self.status: str = AGENT_STATUS_IDLE # Uses imported constant
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.current_plan: Optional[str] = None # New state for planning phase
        self.message_history: List[MessageDict] = []
        self.message_history.append({"role": "system", "content": self.final_system_prompt})
        self._last_api_key_used: Optional[str] = None
        self._failed_models_this_cycle: set = set()

        # Buffers for processing stream
        self.text_buffer: str = ""

        # Sandboxing
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"

        # Compile regex patterns
        self.raw_xml_tool_call_pattern = None
        self.markdown_xml_tool_call_pattern = None
        self.plan_pattern = re.compile(PLAN_TAG_PATTERN, re.IGNORECASE | re.DOTALL) # Compile plan pattern
        self.think_pattern = re.compile(THINK_TAG_PATTERN, re.IGNORECASE | re.DOTALL) # Compile think pattern
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

        logger.info(f"Agent {self.agent_id} ({self.persona}) initialized. Status: {self.status}. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. LLM Provider Instance: {self.llm_provider}")

    # --- Status Management ---
    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None, plan_info: Optional[str] = None):
        """Updates the agent's status and optionally tool or plan info."""
        if self.status != new_status: logger.info(f"Agent {self.agent_id}: Status changed from '{self.status}' to '{new_status}'")
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        self.current_plan = plan_info if new_status == AGENT_STATUS_PLANNING else None
        if self.manager: asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else: logger.warning(f"Agent {self.agent_id}: Manager not set, cannot push status update.")

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
    # --- *** MODIFIED: Added history_override parameter *** ---
    async def process_message(self, history_override: Optional[List[MessageDict]] = None) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Processes the task based on the current message history (or override) using the LLM provider.
        Detects plan generation (<plan>...</plan>) for Admin AI.
        Parses the response stream for XML tool calls (using agent_tool_parser) and yields requests.
        Relies on CycleHandler for retry/failover logic.

        Args:
            history_override (Optional[List[MessageDict]]): If provided, use this message
                history for the LLM call instead of the agent's internal history.
                Used for injecting transient context like health reports or time.
        """
        # --- *** MODIFIED: Use history_to_use *** ---
        history_to_use = history_override if history_override is not None else self.message_history
        # --- *** END MODIFICATION *** ---

        # Uses imported constants
        if self.status not in [AGENT_STATUS_IDLE]: logger.warning(f"Agent {self.agent_id} process_message called but agent is not idle (Status: {self.status})."); yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}; return
        if not self.llm_provider: logger.error(f"Agent {self.agent_id}: LLM Provider not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}; return
        if not self.manager: logger.error(f"Agent {self.agent_id}: Manager not set."); self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": "[Agent Error: Manager not configured]"}; return

        self.set_status(AGENT_STATUS_PROCESSING); self.text_buffer = ""; complete_assistant_response = ""; stream_had_error = False; last_error_obj = None
        # --- *** MODIFIED: Use history_to_use *** ---
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(history_to_use)}")
        try:
            if not self.ensure_sandbox_exists(): self.set_status(AGENT_STATUS_ERROR); yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]"}; return

            provider_stream = self.llm_provider.stream_completion(
                messages=history_to_use, # Pass the potentially modified history
                model=self.model,
                temperature=self.temperature,
                **self.provider_kwargs
            )
            # --- *** END MODIFICATION *** ---

            content: Optional[str] = None
            async for event in provider_stream:
                event_type = event.get("type")
                if event_type == "response_chunk":
                     content = event.get("content", "");
                     if content: self.text_buffer += content; complete_assistant_response += content; yield {"type": "response_chunk", "content": content}
                elif event_type == "status": event["agent_id"] = self.agent_id; yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    last_error_obj = event.get('_exception_obj', ValueError(error_content))
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id; event["content"] = error_content; event["_exception_obj"] = last_error_obj; stream_had_error = True; yield event
                else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
            logger.debug(f"Agent {self.agent_id}: Provider stream finished processing. Stream had error: {stream_had_error}. Processing final buffer.")

            if not stream_had_error:
                # Check for plan or think tags
                plan_content = None
                tag_type = None
                plan_match = self.plan_pattern.search(self.text_buffer.strip()) if self.plan_pattern else None
                think_match = self.think_pattern.search(self.text_buffer.strip()) if hasattr(self, 'think_pattern') and self.think_pattern else None

                if plan_match:
                    plan_content = plan_match.group(1).strip()
                    tag_type = "plan"
                elif think_match: # Prioritize <plan> if both exist, otherwise use <think>
                    plan_content = think_match.group(1).strip()
                    tag_type = "think"

                # --- START MODIFICATION ---
                if tag_type and self.agent_id == "admin_ai": # Check if it's the admin agent and a plan/think tag was found
                    logger.info(f"Agent {self.agent_id}: Detected <{tag_type}> tag. Yielding admin_plan_submitted event.")
                    self.set_status(AGENT_STATUS_PLANNING, plan_info=plan_content)
                    # Store the assistant response containing the plan/think in the *actual* history
                    if complete_assistant_response and (not self.message_history or self.message_history[-1].get("content") != complete_assistant_response or self.message_history[-1].get("role") != "assistant"):
                        self.message_history.append({"role": "assistant", "content": complete_assistant_response})
                    # Yield the NEW event type for the cycle handler
                    yield {"type": "admin_plan_submitted", "plan_content": plan_content, "agent_id": self.agent_id}
                    return # Stop processing here, wait for framework intervention
                elif tag_type == "plan": # Handle <plan> tags from non-admin agents (keep original behavior)
                     logger.info(f"Agent {self.agent_id}: Detected <plan> tag (non-admin). Yielding standard plan_generated.")
                     self.set_status(AGENT_STATUS_PLANNING, plan_info=plan_content)
                     if complete_assistant_response and (not self.message_history or self.message_history[-1].get("content") != complete_assistant_response or self.message_history[-1].get("role") != "assistant"):
                         self.message_history.append({"role": "assistant", "content": complete_assistant_response})
                     yield {"type": "plan_generated", "plan_content": plan_content, "agent_id": self.agent_id} # Original event for non-admin
                     return
                # --- END MODIFICATION ---

                # If no plan/think tag was processed, continue to check for tool calls
                parsed_tool_calls = []
                if self.manager.tool_executor:
                      parsed_tool_calls = find_and_parse_xml_tool_calls( self.text_buffer, self.manager.tool_executor.tools, self.raw_xml_tool_call_pattern, self.markdown_xml_tool_call_pattern, self.agent_id )
                if parsed_tool_calls:
                     logger.info(f"Agent {self.agent_id}: {len(parsed_tool_calls)} tool call(s) found in final buffer.")
                     tool_requests_list = []
                     for tool_name, tool_args, (match_start, match_end) in parsed_tool_calls:
                         call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                         tool_requests_list.append({"id": call_id, "name": tool_name, "arguments": tool_args})
                         await asyncio.sleep(0.001)
                     self.set_status(AGENT_STATUS_AWAITING_TOOL)
                     logger.info(f"Agent {self.agent_id}: Yielding {len(tool_requests_list)} XML tool request(s).")
                     # Store the assistant response containing tool calls in the *actual* history
                     if complete_assistant_response and (not self.message_history or self.message_history[-1].get("content") != complete_assistant_response or self.message_history[-1].get("role") != "assistant"):
                         self.message_history.append({"role": "assistant", "content": complete_assistant_response})
                     yield {"type": "tool_requests", "calls": tool_requests_list, "raw_assistant_response": complete_assistant_response}
                else:
                     logger.debug(f"Agent {self.agent_id}: No plan or tool calls found in final buffer.")
                     if complete_assistant_response:
                         logger.debug(f"Agent {self.agent_id}: Yielding final_response event.")
                         # Store the final assistant response in the *actual* history
                         if not self.message_history or self.message_history[-1].get("content") != complete_assistant_response or self.message_history[-1].get("role") != "assistant":
                             self.message_history.append({"role": "assistant", "content": complete_assistant_response})
                         yield {"type": "final_response", "content": complete_assistant_response}
            else: logger.warning(f"Agent {self.agent_id}: Skipping final plan/tool parsing due to stream error.")
        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]", "_exception_obj": e}
        finally:
            self.text_buffer = ""; logger.info(f"Agent {self.agent_id}: Finished processing cycle attempt. Final Status before CycleHandler action: {self.status}")

    # --- get_state and clear_history ---
    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent."""
        state = { "agent_id": self.agent_id, "persona": self.persona, "status": self.status, "provider": self.provider_name, "model": self.model, "temperature": self.temperature, "message_history_length": len(self.message_history), "sandbox_path": str(self.sandbox_path), "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None) }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info: state["current_tool"] = self.current_tool_info
        if self.status == AGENT_STATUS_PLANNING and self.current_plan: state["current_plan"] = self.current_plan
        return state
    def clear_history(self):
        """Clears message history, keeps system prompt."""
        logger.info(f"Clearing message history for Agent {self.agent_id}"); self.message_history = [{"role": "system", "content": self.final_system_prompt}]
