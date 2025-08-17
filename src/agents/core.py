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

# --- Import status and state constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_ERROR,
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK,
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE,
    WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT,
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
MARKDOWN_FENCE_XML_PATTERN = r"```(?:[a-zA-Z]*\n)?\s*(<({tool_names})>[\s\S]*?</\2>)\s*\n?```"
THINK_TAG_PATTERN = r"<think>([\s\S]*?)</think>"
ROBUST_THINK_TAG_PATTERN = re.compile(r"<think>(.*?)(?:</think>|<(?=[^/]))", re.DOTALL | re.IGNORECASE)

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
        self.agent_type: str = config.get("agent_type", "worker")
        self.agent_config: Dict[str, Any] = agent_config 
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'agent_type', 'api_key', 'base_url', 'referer', 'max_tokens', 'project_name_context', 'initial_plan_description']}
        self.llm_provider: BaseLLMProvider = llm_provider
        self.manager: 'AgentManager' = manager
        self.status: str = AGENT_STATUS_IDLE
        self.state: Optional[str] = None
        self.current_tool_info: Optional[Dict[str, str]] = None
        self.current_plan: Optional[str] = None 
        self.current_task_id: Optional[str] = None
        self.message_history: List[MessageDict] = []
        self._last_api_key_used: Optional[str] = None
        self._failed_models_this_cycle: set = set()
        self._pm_needs_initial_list_tools: bool = False
        self._awaiting_project_approval: bool = False
        self.needs_priority_recheck: bool = False
        self.intervention_applied_for_build_team_tasks: bool = False
        self.text_buffer: str = ""
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"
        self.raw_xml_tool_call_pattern = None
        self.markdown_xml_tool_call_pattern = None
        self.think_pattern = ROBUST_THINK_TAG_PATTERN
        self.initial_plan_description: Optional[str] = config.get("initial_plan_description")
        self.kick_off_task_count_for_build: Optional[int] = None # For PM to track how many workers to create
        self.successfully_created_agent_count_for_build: int = 0 # Counter for created workers in build phase
        
        # Attributes for Constitutional Guardian interaction
        self.cg_original_text: Optional[str] = None
        self.cg_concern_details: Optional[str] = None
        self.cg_original_event_data: Optional[Dict[str, Any]] = None
        self.cg_awaiting_user_decision: bool = False
        self.cg_review_start_time: Optional[float] = None


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

    def set_state(self, new_state: str):
        if self.state != new_state: logger.info(f"Agent {self.agent_id}: Workflow State changed from '{self.state}' to '{new_state}'"); self.state = new_state
        else: logger.debug(f"Agent {self.agent_id}: set_state called with current state '{new_state}'. No change.")

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
        
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(history_to_use)}. State: {self.state}")
        try:
            if not self.ensure_sandbox_exists():
                self.set_status(AGENT_STATUS_ERROR)
                yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]", "_exception_obj": OSError(f"Could not ensure sandbox {self.sandbox_path}")}; return

            provider_stream = self.llm_provider.stream_completion(
                messages=history_to_use, model=self.model, temperature=self.temperature,
                **self.provider_kwargs
            )

            async for event in provider_stream:
                event_type = event.get("type")
                if event_type == "response_chunk":
                    content = event.get("content", "")
                    if content: self.text_buffer += content; complete_assistant_response += content
                    yield {"type": "response_chunk", "content": content, "agent_id": self.agent_id}
                elif event_type == "status": event["agent_id"] = self.agent_id; yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    last_error_obj = event.get('_exception_obj', ValueError(error_content))
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id; event["content"] = error_content; event["_exception_obj"] = last_error_obj; stream_had_error = True; yield event; break
                else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
            logger.debug(f"Agent {self.agent_id}: Provider stream finished. Stream error: {stream_had_error}. Processing buffer.")

            if stream_had_error:
                logger.warning(f"Agent {self.agent_id}: Skipping final tag/tool parsing due to stream error."); return

            logger.debug(f"Agent {self.agent_id}: Raw response before post-processing:\n>>>\n{complete_assistant_response}\n<<<")

            buffer_to_process = self.text_buffer.strip()
            original_complete_response = complete_assistant_response

            # --- Workflow Trigger Check ---
            if hasattr(self.manager, 'workflow_manager'):
                workflow_result = await self.manager.workflow_manager.process_agent_output_for_workflow(self.manager, self, buffer_to_process)
                if workflow_result:
                    logger.info(f"Agent {self.agent_id}: Workflow '{workflow_result.workflow_name}' executed. Yielding and stopping.")
                    yield {"type": "workflow_executed", "workflow_name": workflow_result.workflow_name, "result_data": workflow_result.dict(), "agent_id": self.agent_id}
                    return

            # --- Refactored Action Processing ---
            events_to_yield = []
            remaining_buffer = buffer_to_process

            # 1. Process Thoughts
            think_match = self.think_pattern.search(remaining_buffer)
            if think_match:
                extracted_think_content = think_match.group(1).strip()
                if extracted_think_content:
                    logger.info(f"Agent {self.agent_id}: Detected <think> tag.")
                    events_to_yield.append({"type": "agent_thought", "content": extracted_think_content, "agent_id": self.agent_id})
                remaining_buffer = remaining_buffer.replace(think_match.group(0), '', 1).strip()

            # 2. Process Tool Calls
            if self.manager.tool_executor and self.raw_xml_tool_call_pattern:
                parsed_tool_calls_info = find_and_parse_xml_tool_calls(
                    remaining_buffer, self.manager.tool_executor.tools,
                    self.raw_xml_tool_call_pattern, self.markdown_xml_tool_call_pattern, self.agent_id
                )

                if parsed_tool_calls_info["parsing_errors"]:
                    first_error = parsed_tool_calls_info["parsing_errors"][0]
                    logger.warning(f"Agent {self.agent_id}: Malformed XML tool call detected. Yielding error and stopping.")
                    yield {
                        "type": "malformed_tool_call", "tool_name": first_error['tool_name'],
                        "error_message": first_error['error_message'], "malformed_xml_block": first_error['xml_block'],
                        "raw_assistant_response": original_complete_response, "agent_id": self.agent_id
                    }
                    return

                if parsed_tool_calls_info["valid_calls"]:
                    tool_requests = []
                    processed_spans = []
                    # Sort calls by their start position to process them in order
                    sorted_calls = sorted(parsed_tool_calls_info["valid_calls"], key=lambda x: x[2][0])

                    for tool_name_call, tool_args, span in sorted_calls:
                        call_id = f"xml_call_{self.agent_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                        tool_requests.append({"id": call_id, "name": tool_name_call, "arguments": tool_args})
                        processed_spans.append(span)

                    logger.info(f"Agent {self.agent_id}: Found {len(tool_requests)} valid tool call(s).")
                    events_to_yield.append({
                        "type": "tool_requests", "calls": tool_requests,
                        "raw_assistant_response": original_complete_response, "agent_id": self.agent_id
                    })

                    # Remove tool calls from buffer to isolate remaining text
                    temp_buffer = ""
                    last_end = 0
                    for start, end in sorted(processed_spans):
                        temp_buffer += remaining_buffer[last_end:start]
                        last_end = end
                    temp_buffer += remaining_buffer[last_end:]
                    remaining_buffer = temp_buffer.strip()

            # 3. Process State Change Request
            cycle_handler_instance = getattr(self.manager, 'cycle_handler', None)
            manager_request_state_pattern = getattr(cycle_handler_instance, 'request_state_pattern', None) if cycle_handler_instance else None
            if manager_request_state_pattern:
                state_match = manager_request_state_pattern.search(remaining_buffer)
                if state_match:
                    requested_state = state_match.group(1)
                    if self.manager.workflow_manager.is_valid_state(self.agent_type, requested_state):
                        logger.info(f"Agent {self.agent_id}: Detected valid state request for '{requested_state}'.")
                        events_to_yield.append({"type": "agent_state_change_requested", "requested_state": requested_state, "agent_id": self.agent_id})
                        remaining_buffer = remaining_buffer.replace(state_match.group(0), '', 1).strip()
                    else:
                        logger.warning(f"Agent {self.agent_id}: Detected invalid state request '{requested_state}'. Ignoring.")
                        events_to_yield.append({"type": "invalid_state_request_output", "content": state_match.group(0), "agent_id": self.agent_id})
                        remaining_buffer = remaining_buffer.replace(state_match.group(0), '', 1).strip()

            # 4. Handle Final Textual Response (if any remains)
            if remaining_buffer:
                logger.info(f"Agent {self.agent_id}: Found remaining text to be treated as final response.")
                events_to_yield.append({"type": "final_response", "content": remaining_buffer, "agent_id": self.agent_id})

            # --- Yield all collected events ---
            if not events_to_yield:
                 logger.info(f"Agent {self.agent_id}: Buffer empty after processing. No actions or final response yielded.")
            else:
                for event in events_to_yield:
                    yield event

        except Exception as e:
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]", "_exception_obj": e}; return
        finally:
            self.text_buffer = ""
            logger.info(f"Agent {self.agent_id}: Finished processing cycle attempt. Status before CycleHandler: {self.status}")

    def get_state(self) -> Dict[str, Any]:
        state_info = {
            "agent_id": self.agent_id, "persona": self.persona, "status": self.status, "state": self.state,
            "agent_type": self.agent_type, "provider": self.provider_name, "model": self.model,
            "temperature": self.temperature, "message_history_length": len(self.message_history),
            "sandbox_path": str(self.sandbox_path),
            "xml_tool_parsing_enabled": (self.raw_xml_tool_call_pattern is not None)
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info: state_info["current_tool"] = self.current_tool_info
        if self.status == AGENT_STATUS_PLANNING and self.current_plan: state_info["current_plan"] = self.current_plan
        return state_info

    def clear_history(self):
        logger.info(f"Clearing message history for Agent {self.agent_id}"); self.message_history = []