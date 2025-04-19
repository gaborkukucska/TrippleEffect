# START OF FILE src/agents/cycle_handler.py
import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Dict, Any, Optional, List, AsyncGenerator

# Import base types and status constants
from src.llm_providers.base import ToolResultDict, MessageDict
from src.agents.core import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_ERROR, Agent
)
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool

# Import specific exception types we might want to handle differently
import openai # To check for openai specific errors

# Type hinting for AgentManager and InteractionHandler
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.interaction_handler import AgentInteractionHandler

logger = logging.getLogger(__name__)

# Constants
MAX_STREAM_RETRIES = 3
STREAM_RETRY_DELAYS = [5.0, 10.0, 10.0]
MAX_FAILOVER_ATTEMPTS = 3 # Defined here for use in cycle handler

# Define retryable exceptions (consider adding specific openai errors if needed)
RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    # Consider if RateLimitError should always trigger key cycling first
    # openai.RateLimitError # Let's handle RateLimit specifically
)
# Specific Status Codes for Retry (includes Ollama 500 now)
RETRYABLE_STATUS_CODES = [429, 500, 502, 503, 504]


class AgentCycleHandler:
    """
    Handles the agent execution cycle, including retries for transient errors
    and triggering failover (via AgentManager) for persistent/fatal errors.
    Records performance metrics. Passes exception objects to failover handler.
    """
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager = manager
        self._interaction_handler = interaction_handler
        logger.info("AgentCycleHandler initialized.")

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        agent_id = agent.agent_id
        current_provider = agent.provider_name
        current_model = agent.model
        logger.info(f"CycleHandler: Starting cycle for Agent '{agent_id}' (Model: {current_provider}/{current_model}, Retry: {retry_count}).")

        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        cycle_completed_successfully = False
        trigger_failover = False
        last_error_obj: Optional[Exception] = None # Store the actual exception object
        last_error_content = "" # Keep error string for logging
        is_retryable_error_type = False
        is_key_related_error = False

        history_len_before = len(agent.message_history)
        executed_tool_successfully_this_cycle = False
        needs_reactivation_after_cycle = False
        start_time = time.perf_counter()
        llm_call_duration_ms = 0.0

        if not hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle = set()
        current_model_key = f"{current_provider}/{current_model}"
        agent._failed_models_this_cycle.add(current_model_key)
        logger.debug(f"Agent '{agent_id}' attempting model '{current_model_key}'. Failed this sequence so far: {agent._failed_models_this_cycle}")

        try:
            agent_generator = agent.process_message()
            while True:
                try:
                    event = await agent_generator.asend(None)
                except StopAsyncIteration:
                    logger.info(f"CycleHandler: Agent '{agent_id}' generator finished normally.")
                    # Check if a tool was executed in the last step before StopIteration
                    if executed_tool_successfully_this_cycle:
                        needs_reactivation_after_cycle = True
                    cycle_completed_successfully = True; break
                except Exception as gen_err:
                    logger.error(f"CycleHandler: Generator error for '{agent_id}': {gen_err}", exc_info=True)
                    last_error_obj = gen_err # Store exception object
                    last_error_content = f"[Manager Error: Unexpected error in generator handler - {gen_err}]"
                    is_retryable_error_type = False; is_key_related_error = False; trigger_failover = True; break # Treat generator errors as fatal for this attempt

                event_type = event.get("type")
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self._manager.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content})
                elif event_type == "error":
                    last_error_obj = event.get('_exception_obj', ValueError(event.get('content', 'Unknown Error')))
                    last_error_content = event.get("content", "[Agent Error: Unknown error from provider]")
                    logger.error(f"CycleHandler: Agent '{agent_id}' reported error event: {last_error_content}")
                    if isinstance(last_error_obj, RETRYABLE_EXCEPTIONS): is_retryable_error_type = True; is_key_related_error = False; trigger_failover = False; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered retryable exception type: {type(last_error_obj).__name__}")
                    elif isinstance(last_error_obj, openai.APIStatusError) and (last_error_obj.status_code in RETRYABLE_STATUS_CODES or last_error_obj.status_code >= 500): is_retryable_error_type = True; is_key_related_error = False; trigger_failover = False; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered retryable status code: {last_error_obj.status_code}")
                    elif isinstance(last_error_obj, (openai.AuthenticationError, openai.PermissionDeniedError)) or (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code == 401): is_retryable_error_type = False; is_key_related_error = True; trigger_failover = True; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered key-related error: {type(last_error_obj).__name__}. Triggering failover/key cycle.")
                    elif isinstance(last_error_obj, openai.RateLimitError) or (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code == 429): is_retryable_error_type = False; is_key_related_error = True; trigger_failover = True; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered rate limit error. Triggering failover/key cycle.")
                    else: is_retryable_error_type = False; is_key_related_error = False; trigger_failover = True; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered non-retryable/unknown error: {type(last_error_obj).__name__}. Triggering failover.")
                    break # Exit inner loop on any error event
                elif event_type == "tool_requests":
                    all_tool_calls: List[Dict] = event.get("calls", [])
                    if not all_tool_calls: continue
                    logger.info(f"CycleHandler: Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"): agent.message_history.append({"role": "assistant", "content": agent_last_response})
                    mgmt_calls = []; other_calls = []; invalid_call_results = []
                    for call in all_tool_calls:
                         cid, tname, targs = call.get("id"), call.get("name"), call.get("arguments", {})
                         fail_res: Optional[ToolResultDict] = None
                         if cid and tname and isinstance(targs, dict):
                             if tname == ManageTeamTool.name: mgmt_calls.append(call)
                             else: other_calls.append(call)
                         else: fail_res = await self._interaction_handler.failed_tool_result(cid, tname);
                         if fail_res: invalid_call_results.append(fail_res)
                    if invalid_call_results:
                        for res in invalid_call_results: agent.message_history.append({"role": "tool", **res})
                    calls_to_execute = mgmt_calls + other_calls; activation_tasks = []; manager_action_feedback = []; executed_tool_successfully_this_cycle = False
                    if calls_to_execute:
                        logger.info(f"CycleHandler: Executing {len(calls_to_execute)} tool(s) sequentially for '{agent_id}'.")
                        await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})
                        for call in calls_to_execute:
                             call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']
                             result = await self._interaction_handler.execute_single_tool(agent, call_id, tool_name, tool_args, project_name=self._manager.current_project, session_name=self._manager.current_session)
                             if result:
                                 raw_content_hist = result.get("content", "[Tool Error: No content]"); tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_hist)}
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id: agent.message_history.append(tool_msg)
                                 tool_exec_success = not str(raw_content_hist).strip().startswith(("Error:", "[ToolExec Error:", "[Manager Error:"))
                                 if tool_exec_success: executed_tool_successfully_this_cycle = True
                                 raw_tool_output = result.get("_raw_result")
                                 if tool_name == ManageTeamTool.name:
                                     if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                         action = raw_tool_output.get("action"); params = raw_tool_output.get("params", {}); act_success, act_msg, act_data = await self._interaction_handler.handle_manage_team_action(action, params, agent_id)
                                         feedback = {"call_id": call_id, "action": action, "success": act_success, "message": act_msg};
                                         if act_data: feedback["data"] = act_data; manager_action_feedback.append(feedback)
                                     elif isinstance(raw_tool_output, dict): manager_action_feedback.append({"call_id": call_id, "action": raw_tool_output.get("action"), "success": False, "message": raw_tool_output.get("message", "Tool exec failed.")})
                                     else: manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result."})
                                 elif tool_name == SendMessageTool.name:
                                     target_id = call['arguments'].get("target_agent_id"); msg_content = call['arguments'].get("message_content")
                                     if target_id and msg_content is not None: activation_task = await self._interaction_handler.route_and_activate_agent_message(agent_id, target_id, msg_content);
                                     if activation_task: activation_tasks.append(activation_task)
                                     else: manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": "Validation Error: Missing target/content."})
                             else: manager_action_feedback.append({"call_id": call_id, "action": tool_name, "success": False, "message": "Tool execution failed (no result)."})
                        if activation_tasks: await asyncio.gather(*activation_tasks); logger.info(f"CycleHandler: Completed activation tasks for '{agent_id}'.")
                        if manager_action_feedback:
                             feedback_appended = False
                             for fb in manager_action_feedback:
                                 fb_content = f"[Manager Result for {fb.get('action', 'N/A')} (Call ID: {fb['call_id']})]: Success={fb['success']}. Message: {fb['message']}"
                                 if fb.get("data"):
                                     try: data_str = json.dumps(fb['data'], indent=2); fb_content += f"\nData:\n{data_str[:1500]}{'... (truncated)' if len(data_str) > 1500 else ''}"
                                     except TypeError: fb_content += "\nData: [Unserializable]"
                                 fb_msg: MessageDict = {"role": "tool", "tool_call_id": fb['call_id'], "content": fb_content}
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != fb_content: agent.message_history.append(fb_msg); feedback_appended = True
                             if feedback_appended: needs_reactivation_after_cycle = True
                        if executed_tool_successfully_this_cycle and not manager_action_feedback: logger.debug(f"CycleHandler: Successful standard tool exec for '{agent_id}'. Setting reactivate flag."); needs_reactivation_after_cycle = True
                    # --- ADDED: Check if cycle should continue after tools ---
                    # If tools were executed, the agent needs another turn to process the results.
                    if calls_to_execute:
                        logger.debug(f"CycleHandler: Tools executed for '{agent_id}', breaking inner loop to allow reactivation.")
                        break # Exit the 'while True' loop to proceed to finally block
                    # --- END ADD ---
                else: logger.warning(f"CycleHandler: Unknown event type '{event_type}' from '{agent_id}'.")
        except Exception as e:
            logger.error(f"CycleHandler: Error during core processing for '{agent_id}': {e}", exc_info=True)
            last_error_obj = e; last_error_content = f"[Manager Error: Unexpected error in cycle handler - {e}]"; is_retryable_error_type = False; is_key_related_error = False; trigger_failover = True; cycle_completed_successfully = False
            try: await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
            except Exception as ui_err: logger.error(f"Error sending error status to UI in cycle handler: {ui_err}")
        finally:
            if agent_generator:
                try: await agent_generator.aclose()
                except Exception as close_err: logger.error(f"CycleHandler: Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            end_time = time.perf_counter(); llm_call_duration_ms = (end_time - start_time) * 1000
            call_success = (cycle_completed_successfully or needs_reactivation_after_cycle) and not trigger_failover
            logger.debug(f"Cycle outcome for {current_provider}/{current_model} (Retry: {retry_count}): Success={call_success}, Duration={llm_call_duration_ms:.2f}ms, Error? {bool(last_error_obj)}")
            try: await self._manager.performance_tracker.record_call(provider=current_provider, model_id=current_model, duration_ms=llm_call_duration_ms, success=call_success)
            except Exception as record_err: logger.error(f"Failed to record performance metrics for {current_provider}/{current_model}: {record_err}", exc_info=True)

            # --- Refined Decision Logic ---
            if trigger_failover:
                logger.warning(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) requires failover. Error Type: {type(last_error_obj).__name__}. Triggering failover handler.")
                asyncio.create_task(self._manager.handle_agent_model_failover(agent_id, last_error_obj))
            elif is_retryable_error_type and retry_count < MAX_STREAM_RETRIES:
                 retry_delay = STREAM_RETRY_DELAYS[retry_count]
                 logger.warning(f"CycleHandler: Transient error for '{agent_id}' on {current_model_key}. Retrying same model/key in {retry_delay:.1f}s ({retry_count + 1}/{MAX_STREAM_RETRIES})... Last Error: {last_error_content}")
                 await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying '{current_model}' (Attempt {retry_count + 2})..."})
                 await asyncio.sleep(retry_delay)
                 agent.set_status(AGENT_STATUS_IDLE)
                 asyncio.create_task(self._manager.schedule_cycle(agent, retry_count + 1))
            elif needs_reactivation_after_cycle: # <<< THIS IS THE KEY PART <<<
                 logger.info(f"CycleHandler: Reactivating agent '{agent_id}' ({current_model_key}) after tool/feedback processing.")
                 agent.set_status(AGENT_STATUS_IDLE)
                 await asyncio.sleep(0) # <<< ADDED small sleep
                 asyncio.create_task(self._manager.schedule_cycle(agent, 0)) # Reset retry count and schedule
            else: # Clean finish or max retries reached for transient error
                 if not call_success and retry_count >= MAX_STREAM_RETRIES:
                      logger.error(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) reached max retries ({MAX_STREAM_RETRIES}) for transient errors. Triggering failover.")
                      asyncio.create_task(self._manager.handle_agent_model_failover(agent_id, last_error_obj or ValueError("Max retries reached")))
                 elif call_success:
                     history_len_after = len(agent.message_history)
                     if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                          logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) has new user message(s). Reactivating.")
                          if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                          agent.set_status(AGENT_STATUS_IDLE)
                          asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                     else:
                          logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) finished cycle cleanly, no reactivation needed.")
                          if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                          if agent.status != AGENT_STATUS_ERROR: agent.set_status(AGENT_STATUS_IDLE)
                 elif not call_success:
                      logger.warning(f"CycleHandler: Agent '{agent_id}' cycle ended without explicit success or triggering failover/retry. Setting Idle. Last Error: {last_error_content}")
                      if agent.status != AGENT_STATUS_ERROR: agent.set_status(AGENT_STATUS_IDLE)

            log_level = logging.ERROR if agent.status == AGENT_STATUS_ERROR else logging.INFO
            logger.log(log_level, f"CycleHandler: Finished cycle logic for Agent '{agent_id}'. Final status for this attempt: {agent.status}")
