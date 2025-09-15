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
        self.default_task_assigned: bool = False # Flag for one-time default task injection
        
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
        yielded_chunks = False
        
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}. History length: {len(history_to_use)}. State: {self.state}")
        try:
            if not self.ensure_sandbox_exists():
                self.set_status(AGENT_STATUS_ERROR)
                yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox {self.sandbox_path}]", "_exception_obj": OSError(f"Could not ensure sandbox {self.sandbox_path}")}; return

            max_tokens_override = None

            provider_stream = self.llm_provider.stream_completion(
                messages=history_to_use, model=self.model, temperature=self.temperature,
                max_tokens=max_tokens_override, **self.provider_kwargs
            )

            async for event in provider_stream:
                event_type = event.get("type")
                if event_type == "response_chunk":
                    content = event.get("content", "")
                    if content: self.text_buffer += content; complete_assistant_response += content; yielded_chunks = True
                    yield {"type": "response_chunk", "content": content, "agent_id": self.agent_id}
                elif event_type == "status": event["agent_id"] = self.agent_id; yield event
                elif event_type == "error":
                    error_content = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    last_error_obj = event.get('_exception_obj', ValueError(error_content))
                    logger.error(f"Agent {self.agent_id}: Received error event from provider: {error_content}")
                    event["agent_id"] = self.agent_id; event["content"] = error_content; event["_exception_obj"] = last_error_obj; stream_had_error = True; yield event; break
                else: logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
            logger.debug(f"Agent {self.agent_id}: Provider stream finished. Stream error: {stream_had_error}. Processing buffer.")

            if not stream_had_error:
                logger.debug(f"Agent {self.agent_id}: Raw response before post-processing:\n>>>\n{complete_assistant_response}\n<<<")
                # Send raw response to UI for Internal Comms visibility
                if complete_assistant_response.strip():
                    yield {"type": "agent_raw_response", "content": complete_assistant_response, "agent_id": self.agent_id}
                buffer_to_process = self.text_buffer.strip() # Strip here for workflow processing
                original_complete_response = complete_assistant_response # Retain the full original response for potential error reporting

                # --- Check for Workflow Triggers First ---
                if hasattr(self.manager, 'workflow_manager'):
                    workflow_result: Optional['WorkflowResult'] = await self.manager.workflow_manager.process_agent_output_for_workflow(
                        self.manager, self, buffer_to_process
                    )
                    if workflow_result:
                        logger.info(f"Agent {self.agent_id}: Workflow '{workflow_result.workflow_name}' executed. Success: {workflow_result.success}. Message: {workflow_result.message}")
                        yield {"type": "workflow_executed", "workflow_name": workflow_result.workflow_name, "result_data": workflow_result.dict(), "agent_id": self.agent_id}
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
                    
                    if completion_thoughts and self.state in [PM_STATE_MANAGE, PM_STATE_WORK]:
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
                        is_valid_state_request = self.manager.workflow_manager.is_valid_state(self.agent_type, requested_state)
                        if is_valid_state_request:
                            logger.info(f"Agent {self.agent_id}: Detected valid state request tag for '{requested_state}': {state_request_tag}")
                            state_change_to_yield = {"type": "agent_state_change_requested", "requested_state": requested_state, "agent_id": self.agent_id}
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

                if self.agent_type == AGENT_TYPE_PM and \
                   self.state == PM_STATE_STARTUP and \
                   (not 'workflow_result' in locals() or not workflow_result):

                    pm_kickoff_trigger_tag = "task_list"
                    if hasattr(self.manager, 'workflow_manager') and self.manager.workflow_manager and \
                       hasattr(self.manager.workflow_manager, '_workflow_triggers'):
                        for trigger_key, wf_instance in self.manager.workflow_manager._workflow_triggers.items():
                            if hasattr(wf_instance, 'name') and wf_instance.name == "pm_project_kickoff":
                                pm_kickoff_trigger_tag = trigger_key[2]
                                break

                    if f"<{pm_kickoff_trigger_tag}>" not in final_cleaned_response_for_tools_or_text:
                        logger.warning(
                            f"Agent {self.agent_id} (PM) in state '{self.state}' did not output the expected '<{pm_kickoff_trigger_tag}>' tag. "
                            f"Forcing retry by re-requesting current state with feedback. Output was: '{final_cleaned_response_for_tools_or_text[:200]}...'"
                        )
                        feedback_content = (
                            f"[Framework Feedback for PM Retry]\n"
                            f"Your previous output did not contain the required '<{pm_kickoff_trigger_tag}>' XML structure. "
                            f"Please review your instructions for the '{self.state}' state and ensure your entire response is the XML task list as specified."
                        )
                        self.message_history.append({"role": "system", "content": feedback_content})
                        yield {"type": "agent_state_change_requested", "requested_state": PM_STATE_STARTUP, "agent_id": self.agent_id}
                        return

                tool_requests_to_yield = []

                if self.manager.tool_executor and self.raw_xml_tool_call_pattern:
                    parsed_tool_calls_info = find_and_parse_xml_tool_calls(
                        final_cleaned_response_for_tools_or_text, self.manager.tool_executor.tools,
                        self.raw_xml_tool_call_pattern, self.markdown_xml_tool_call_pattern, self.agent_id
                    )
                    valid_calls = parsed_tool_calls_info["valid_calls"]
                    parsing_errors = parsed_tool_calls_info["parsing_errors"]

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
                        yield {
                            "type": "malformed_tool_call",
                            "tool_name": "unknown", 
                            "error_message": "Tool call detected but parsing failed. Please ensure proper XML format without markdown code fences.",
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

                    # Clean the response to get only the text/thought part for the history
                    content_for_history = final_cleaned_response_for_tools_or_text
                    if valid_calls:
                        # The parser returns the span of the match. Use the span to get the exact
                        # string that was matched from the original buffer to prevent errors.
                        for _, _, match_span in valid_calls:
                            start, end = match_span
                            # Use the original, un-stripped text_buffer for slicing with the span
                            xml_block_str = self.text_buffer[start:end]
                            content_for_history = content_for_history.replace(xml_block_str, '')
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
                    if state_change_to_yield:
                        # Only a state change (no tools)
                        yield state_change_to_yield
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
