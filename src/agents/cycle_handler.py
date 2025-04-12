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
    AGENT_STATUS_ERROR, Agent # Removed AWAITING_USER_OVERRIDE
)
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool

# Type hinting for AgentManager and InteractionHandler
if TYPE_CHECKING:
    # Adjusted import path based on directory structure
    from src.agents.manager import AgentManager
    from src.agents.interaction_handler import AgentInteractionHandler

logger = logging.getLogger(__name__)

# --- Constants defined in AgentManager, re-declared here for clarity ---
# These should ideally be sourced from a single config location later
MAX_STREAM_RETRIES = 3 # Max retries for *transient* errors on the *same* model/key
STREAM_RETRY_DELAYS = [5.0, 10.0, 10.0] # Delays in seconds for retries
MAX_FAILOVER_ATTEMPTS = 3 # Limit distinct models tried per original cycle request
# --- End Constants ---

# List of substrings indicating a potentially retryable transient error
# This is a basic check; more robust checks might involve specific error types/codes
RETRYABLE_ERROR_INDICATORS = [
    "RateLimitError", "Status 429", "Status 503", "APITimeoutError",
    "timeout error", "Connection closed during stream", "ClientConnectorError",
    "Connection refused", "Temporary failure", "Service Unavailable",
    "Could not connect", "Network error"
]

# List of substrings indicating a fatal error unlikely to succeed on retry with the same model/key
# These should trigger failover directly.
FATAL_ERROR_INDICATORS = [
    "llama runner process has terminated", "exit status",
    "APIError during stream", # OpenAI APIError during stream often indicates a deeper issue
    "error processing stream chunk",
    "failed to decode stream chunk",
    "Provider returned error", # General provider-side issue
    "Client Error 4", # Covers 400, 401, 403, 404 etc.
    "AuthenticationError", "BadRequestError",
    "PermissionDeniedError", "NotFoundError",
    "Invalid Authentication", "Invalid API Key",
    "invalid_api_key", "account_deactivated" # OpenRouter specific
]


class AgentCycleHandler:
    """
    Handles the agent execution cycle. Triggers failover via AgentManager
    for persistent errors after attempting retries for transient errors.
    Records performance metrics. User override mechanism removed.
    """
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager = manager
        self._interaction_handler = interaction_handler
        logger.info("AgentCycleHandler initialized.")

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        """
        Manages the agent's process_message generator. Handles events, tools,
        retries for transient errors, triggers failover for fatal/persistent errors,
        and manages reactivation logic. Records metrics.

        Args:
            agent (Agent): The agent instance to run the cycle for.
            retry_count (int): The current retry attempt number for a transient error
                               on the *current* model/provider configuration. Resets
                               when failover occurs.
        """
        agent_id = agent.agent_id
        current_provider = agent.provider_name
        current_model = agent.model
        logger.info(f"CycleHandler: Starting cycle for Agent '{agent_id}' (Model: {current_provider}/{current_model}, Retry: {retry_count}).")

        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        cycle_completed_successfully = False
        trigger_failover = False
        is_retryable_transient_error = False
        last_error_content = ""
        history_len_before = len(agent.message_history)
        executed_tool_successfully_this_cycle = False
        needs_reactivation_after_cycle = False
        start_time = time.perf_counter()
        llm_call_duration_ms = 0.0

        # --- Initialize or ensure failover tracking set exists ---
        # _failed_models_this_cycle tracks models attempted *during this entire failover sequence*
        # originating from the initial user request or reactivation. It's reset only when
        # the agent successfully completes a cycle or enters a final ERROR state for the sequence.
        if not hasattr(agent, '_failed_models_this_cycle'):
            agent._failed_models_this_cycle = set()
        # Add the current model attempt to the set for this sequence
        current_model_key = f"{current_provider}/{current_model}"
        agent._failed_models_this_cycle.add(current_model_key)
        logger.debug(f"Agent '{agent_id}' attempting model '{current_model_key}'. Failed this cycle sequence so far: {agent._failed_models_this_cycle}")
        # --- End Failover Tracking Init ---

        try:
            # --- Main agent processing loop ---
            agent_generator = agent.process_message()
            while True:
                try:
                    event = await agent_generator.asend(None)
                except StopAsyncIteration:
                    logger.info(f"CycleHandler: Agent '{agent_id}' generator finished normally.")
                    cycle_completed_successfully = True # Mark cycle as successful if generator finishes
                    break # Exit the 'while True' loop
                except Exception as gen_err:
                    logger.error(f"CycleHandler: Generator error for '{agent_id}': {gen_err}", exc_info=True)
                    last_error_content = f"[Manager Error: Unexpected error in generator handler - {gen_err}]"
                    # Treat unexpected generator errors as fatal for this model attempt
                    is_retryable_transient_error = False
                    trigger_failover = True
                    break # Exit the 'while True' loop

                event_type = event.get("type")

                # Process Non-Error Events
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self._manager.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content})

                # Process Error Event from the provider stream
                elif event_type == "error":
                    last_error_content = event.get("content", "[Agent Error: Unknown error from provider]")
                    logger.error(f"CycleHandler: Agent '{agent_id}' reported error: {last_error_content}")

                    # --- Determine if the error is retryable or fatal ---
                    error_lower = last_error_content.lower()
                    is_retryable_transient_error = any(indicator.lower() in error_lower for indicator in RETRYABLE_ERROR_INDICATORS)
                    is_fatal_error = any(indicator.lower() in error_lower for indicator in FATAL_ERROR_INDICATORS)

                    # Fatal errors override retryable ones
                    if is_fatal_error:
                         is_retryable_transient_error = False
                         trigger_failover = True
                         logger.warning(f"CycleHandler: Detected FATAL error for agent '{agent_id}'. Triggering failover. Error: {last_error_content}")
                    elif is_retryable_transient_error:
                         # Retryable error, failover trigger depends on retry_count below
                         logger.warning(f"CycleHandler: Detected RETRYABLE error for agent '{agent_id}'. Will attempt retry if count allows. Error: {last_error_content}")
                         trigger_failover = False # Don't trigger failover yet
                    else:
                         # Unknown error type - treat as fatal for this attempt to be safe
                         is_retryable_transient_error = False
                         trigger_failover = True
                         logger.warning(f"CycleHandler: Detected UNKNOWN error type for agent '{agent_id}'. Treating as fatal for this attempt. Triggering failover. Error: {last_error_content}")
                    # --- End error classification ---

                    break # Break on ANY error from the stream

                # Process Tool Requests Event
                elif event_type == "tool_requests":
                    # (Tool handling logic remains the same as previous version - ensure correct indentation)
                    all_tool_calls: List[Dict] = event.get("calls", [])
                    if not all_tool_calls: continue
                    logger.info(f"CycleHandler: Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")

                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                        agent.message_history.append({"role": "assistant", "content": agent_last_response})

                    mgmt_calls = []; other_calls = []; invalid_call_results = []
                    for call in all_tool_calls:
                        cid, tname, targs = call.get("id"), call.get("name"), call.get("arguments", {})
                        if cid and tname and isinstance(targs, dict):
                            if tname == ManageTeamTool.name: mgmt_calls.append(call)
                            else: other_calls.append(call)
                        else:
                            fail_res = await self._interaction_handler.failed_tool_result(cid, tname)
                            if fail_res: invalid_call_results.append(fail_res)

                    if invalid_call_results:
                        for res in invalid_call_results:
                            agent.message_history.append({"role": "tool", **res})

                    calls_to_execute = mgmt_calls + other_calls
                    activation_tasks = []
                    manager_action_feedback = []
                    executed_tool_successfully_this_cycle = False

                    if calls_to_execute:
                        logger.info(f"CycleHandler: Executing {len(calls_to_execute)} tool(s) sequentially for '{agent_id}'.")
                        await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})

                        for call in calls_to_execute:
                             call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']
                             # Pass project/session context from manager to tool executor
                             result = await self._interaction_handler.execute_single_tool(
                                 agent, call_id, tool_name, tool_args,
                                 project_name=self._manager.current_project,
                                 session_name=self._manager.current_session
                             )

                             if result:
                                 raw_content_hist = result.get("content", "[Tool Error: No content]")
                                 tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_hist)}

                                 # Avoid duplicate history entries
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id:
                                     agent.message_history.append(tool_msg)

                                 tool_exec_success = not str(raw_content_hist).strip().startswith(("Error:", "[ToolExec Error:", "[Manager Error:"))
                                 if tool_exec_success:
                                     executed_tool_successfully_this_cycle = True

                                 raw_tool_output = result.get("_raw_result") # Get raw for specific handling

                                 # Handle ManageTeamTool feedback
                                 if tool_name == ManageTeamTool.name:
                                     if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                         action = raw_tool_output.get("action")
                                         params = raw_tool_output.get("params", {})
                                         act_success, act_msg, act_data = await self._interaction_handler.handle_manage_team_action(action, params, agent_id)
                                         feedback = {"call_id": call_id, "action": action, "success": act_success, "message": act_msg}
                                         if act_data: feedback["data"] = act_data
                                         manager_action_feedback.append(feedback)
                                     elif isinstance(raw_tool_output, dict):
                                         manager_action_feedback.append({"call_id": call_id, "action": raw_tool_output.get("action"), "success": False, "message": raw_tool_output.get("message", "Tool execution failed.")})
                                     else:
                                         manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result structure."})

                                 # Handle SendMessageTool routing
                                 elif tool_name == SendMessageTool.name:
                                     target_id = call['arguments'].get("target_agent_id")
                                     msg_content = call['arguments'].get("message_content")
                                     if target_id and msg_content is not None:
                                         activation_task = await self._interaction_handler.route_and_activate_agent_message(agent_id, target_id, msg_content)
                                         if activation_task: activation_tasks.append(activation_task)
                                     else:
                                         manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": "Validation Error: Missing target_id or message_content."})
                             else:
                                 # Handle case where execute_single_tool returned None (should be rare)
                                 manager_action_feedback.append({"call_id": call_id, "action": tool_name, "success": False, "message": "Tool execution failed unexpectedly (no result)."})

                        if activation_tasks:
                            await asyncio.gather(*activation_tasks)
                            logger.info(f"CycleHandler: Completed activation tasks for '{agent_id}'.")

                        # Append manager feedback messages to history
                        if manager_action_feedback:
                             feedback_appended = False
                             for fb in manager_action_feedback:
                                 fb_content = f"[Manager Result for {fb.get('action', 'N/A')} (Call ID: {fb['call_id']})]: Success={fb['success']}. Message: {fb['message']}"
                                 if fb.get("data"):
                                     try: data_str = json.dumps(fb['data'], indent=2); fb_content += f"\nData:\n{data_str[:1500]}{'... (truncated)' if len(data_str) > 1500 else ''}"
                                     except TypeError: fb_content += "\nData: [Unserializable Data]"
                                 fb_msg: MessageDict = {"role": "tool", "tool_call_id": fb['call_id'], "content": fb_content}
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != fb_content:
                                     agent.message_history.append(fb_msg); feedback_appended = True
                             if feedback_appended:
                                 needs_reactivation_after_cycle = True

                        # If non-manager tools executed successfully, reactivate
                        if executed_tool_successfully_this_cycle and not manager_action_feedback:
                             logger.debug(f"CycleHandler: Successful standard tool execution for '{agent_id}'. Setting reactivation flag.")
                             needs_reactivation_after_cycle = True

                else:
                    logger.warning(f"CycleHandler: Unknown event type '{event_type}' from '{agent_id}'.")

            # --- End of 'while True' loop processing events ---

        except Exception as e: # Catch errors in the main loop setup/handling (not generator itself)
            logger.error(f"CycleHandler: Error during cycle processing for '{agent_id}': {e}", exc_info=True)
            last_error_content = f"[Manager Error: Unexpected error in cycle handler - {e}]"
            is_retryable_transient_error = False # Treat unexpected handler errors as fatal
            trigger_failover = True
            cycle_completed_successfully = False
            try: await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
            except Exception as ui_err: logger.error(f"Error sending error status to UI in cycle handler: {ui_err}")

        # --- *** FINALLY BLOCK STARTS HERE *** ---
        finally:
            # Close generator if it exists and hasn't finished
            if agent_generator:
                try: await agent_generator.aclose()
                except Exception as close_err: logger.error(f"CycleHandler: Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            # --- Record Performance Metrics ---
            end_time = time.perf_counter()
            llm_call_duration_ms = (end_time - start_time) * 1000
            # Success is defined as cycle completing without error *or* finishing tool execution that needs reactivation
            call_success = cycle_completed_successfully or needs_reactivation_after_cycle
            logger.debug(f"Cycle outcome for {current_provider}/{current_model} (Retry: {retry_count}): Success={call_success}, Duration={llm_call_duration_ms:.2f}ms")
            try:
                await self._manager.performance_tracker.record_call(
                    provider=current_provider, model_id=current_model,
                    duration_ms=llm_call_duration_ms, success=call_success
                )
                logger.debug(f"Successfully recorded metrics for {current_provider}/{current_model}")
            except Exception as record_err:
                 logger.error(f"Failed to record performance metrics for {current_provider}/{current_model}: {record_err}", exc_info=True)

            # --- Retry / Failover / Reactivation Logic ---
            if not call_success: # An error occurred during the cycle
                if is_retryable_transient_error and retry_count < MAX_STREAM_RETRIES:
                    # --- *** Perform Retry *** ---
                    retry_delay = STREAM_RETRY_DELAYS[retry_count]
                    logger.warning(f"CycleHandler: Transient error for '{agent_id}' on {current_model_key}. Retrying same model in {retry_delay:.1f}s ({retry_count + 1}/{MAX_STREAM_RETRIES})... Last Error: {last_error_content}")
                    await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying '{current_model}' (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES}, delay {retry_delay:.1f}s)..."})
                    await asyncio.sleep(retry_delay)
                    agent.set_status(AGENT_STATUS_IDLE) # Set back to idle before retrying
                    # Schedule the *same* agent again with incremented retry count
                    asyncio.create_task(self._manager.schedule_cycle(agent, retry_count + 1))
                    # _failed_models_this_cycle is NOT cleared here, as it tracks the whole sequence
                    # --- *** End Retry *** ---
                else:
                    # --- *** Trigger Failover *** ---
                    # Error was fatal OR max retries reached for a transient error
                    logger.warning(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) failed. Error was fatal or max retries ({retry_count}/{MAX_STREAM_RETRIES}) reached. Triggering failover. Last Error: {last_error_content}")
                    # The agent's _failed_models_this_cycle set already contains the failed model.
                    # AgentManager's failover logic will use this set.
                    asyncio.create_task(self._manager.handle_agent_model_failover(agent_id, last_error_content))
                    # Agent status will be set by the failover handler (either IDLE for next attempt or ERROR if failover fails)
                    # --- *** End Failover Trigger *** ---

            elif needs_reactivation_after_cycle: # No error, but needs to continue processing (e.g., after tool use)
                 logger.info(f"CycleHandler: Reactivating agent '{agent_id}' ({current_model_key}) after successful tool/feedback processing.")
                 # Clear the failover tracking set ONLY if the cycle was successful AND needs reactivation
                 # This signifies the current model worked for this step.
                 # if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear() # Clearing here might be too soon if subsequent steps fail. Let Manager handle reset on final success.
                 agent.set_status(AGENT_STATUS_IDLE)
                 asyncio.create_task(self._manager.schedule_cycle(agent, 0)) # Reset retry count for next step
            else: # No error, cycle finished cleanly (final_response or generator stop), no tools requiring reactivation
                 history_len_after = len(agent.message_history)
                 if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                      # New user message arrived *during* the cycle (unlikely but possible)
                      logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) has new user message(s). Reactivating.")
                      # Clear failover set as the original task sequence might be changing
                      if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                      agent.set_status(AGENT_STATUS_IDLE)
                      asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                 else:
                      # Cycle finished successfully without needing immediate reactivation
                      logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) finished cycle cleanly, no immediate reactivation needed.")
                      # Clear the failover tracking set as this sequence was successful
                      if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                      if agent.status != AGENT_STATUS_ERROR: # Don't overwrite ERROR state if somehow set
                          agent.set_status(AGENT_STATUS_IDLE)

            # Log final status determined by the logic above
            log_level = logging.ERROR if agent.status == AGENT_STATUS_ERROR else logging.INFO
            logger.log(log_level, f"CycleHandler: Finished cycle logic for Agent '{agent_id}'. Final status for this attempt: {agent.status}")
            # --- *** END OF FINALLY BLOCK *** ---
