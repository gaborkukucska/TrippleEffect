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
    AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE, Agent
)
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool


# Type hinting for AgentManager and InteractionHandler
if TYPE_CHECKING:
    from src.agents.manager import AgentManager, MAX_STREAM_RETRIES, STREAM_RETRY_DELAYS
    from src.agents.interaction_handler import AgentInteractionHandler

logger = logging.getLogger(__name__)

# --- Keywords indicating a potentially fatal/non-transient provider error ---
# These might suggest trying an override sooner rather than retrying the same model.
FATAL_ERROR_INDICATORS = [
    # Ollama specific
    "llama runner process has terminated",
    "exit status",
    # General connection issues during stream likely indicate persistent problem
    "Connection closed during stream",
    "ClientConnectionError",
    # Add other indicators as needed (e.g., specific context length errors)
    # "context length", # Be careful with this one, might be transient
]

class AgentCycleHandler:
    """
    Handles the execution cycle of a single agent's turn, including
    processing messages, handling tool calls, errors, and retries.
    *** Modified error handling to potentially trigger user override faster
    for specific fatal-seeming errors. ***
    """
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager = manager
        self._interaction_handler = interaction_handler
        logger.info("AgentCycleHandler initialized.")

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        """
        Manages the asynchronous generator returned by agent.process_message().
        Handles events yielded by the agent (chunks, tool calls, errors).
        Delegates tool execution/handling to AgentInteractionHandler.
        Handles stream errors with retries/override calls back to AgentManager.
        *** Triggers override faster for fatal errors. ***
        Determines if agent needs reactivation based on results or new messages.

        Args:
            agent: The Agent instance to run the cycle for.
            retry_count: The current retry attempt count for stream errors.
        """
        # Re-import constants within the method scope if needed, or ensure they are accessible
        from src.agents.manager import MAX_STREAM_RETRIES, STREAM_RETRY_DELAYS

        agent_id = agent.agent_id
        logger.info(f"CycleHandler: Starting cycle for Agent '{agent_id}' (Retry: {retry_count}).")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        current_cycle_error = False
        is_fatal_error = False # *** NEW Flag ***
        is_retryable_transient_error = False # Flag for standard retryable errors
        last_error_content = ""
        history_len_before = len(agent.message_history)
        executed_tool_successfully_this_cycle = False
        needs_reactivation_after_cycle = False # Flag for self-reactivation

        try:
            # Get the generator from the agent
            agent_generator = agent.process_message()

            # Process events from the generator
            while True:
                try:
                    event = await agent_generator.asend(None)
                except StopAsyncIteration:
                    logger.info(f"CycleHandler: Agent '{agent_id}' generator finished normally.")
                    break
                except Exception as gen_err:
                    logger.error(f"CycleHandler: Generator error for '{agent_id}': {gen_err}", exc_info=True)
                    current_cycle_error = True
                    last_error_content = f"[Manager Error: Unexpected error in generator handler - {gen_err}]"
                    # Treat unexpected generator errors as potentially fatal
                    is_fatal_error = True
                    break

                event_type = event.get("type")

                # --- Handle Non-Error Events ---
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self._manager.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                             agent.message_history.append({"role": "assistant", "content": final_content})

                # --- Handle Error Event ---
                elif event_type == "error":
                    last_error_content = event.get("content", "[Agent Error]")
                    logger.error(f"CycleHandler: Agent '{agent_id}' reported error: {last_error_content}")

                    # *** NEW: Check for fatal error indicators ***
                    is_fatal_error = any(ind.lower() in last_error_content.lower() for ind in FATAL_ERROR_INDICATORS)
                    if is_fatal_error:
                         logger.warning(f"CycleHandler: Detected potentially fatal error indicator for agent '{agent_id}'.")

                    # Check if likely a *retryable* transient stream issue (only if not fatal)
                    if not is_fatal_error:
                        is_retryable_transient_error = any(ind.lower() in last_error_content.lower() for ind in ["RateLimitError", "Status 429", "Status 503", "APITimeoutError", "timeout error"]) # Refine keywords as needed

                    # Set flag and break loop on ANY error to handle retry/override logic
                    current_cycle_error = True
                    break

                # --- Handle Tool Requests Event ---
                elif event_type == "tool_requests":
                    # (Tool handling logic remains the same as previous version)
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
                        for res in invalid_call_results: agent.message_history.append({"role": "tool", **res})

                    calls_to_execute = mgmt_calls + other_calls
                    activation_tasks = []
                    manager_action_feedback = []
                    executed_tool_successfully_this_cycle = False

                    if calls_to_execute:
                        logger.info(f"CycleHandler: Executing {len(calls_to_execute)} tool(s) sequentially for '{agent_id}'.")
                        await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})

                        for call in calls_to_execute:
                            call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']
                            result = await self._interaction_handler.execute_single_tool(
                                agent, call_id, tool_name, tool_args,
                                project_name=self._manager.current_project,
                                session_name=self._manager.current_session
                            )
                            if result:
                                raw_content_hist = result.get("content", "[Tool Error: No content]")
                                tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_hist)}
                                if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id:
                                     agent.message_history.append(tool_msg)
                                tool_exec_success = not str(raw_content_hist).strip().startswith(("Error:", "[ToolExec Error:", "[Manager Error:"))
                                if tool_exec_success: executed_tool_successfully_this_cycle = True
                                raw_tool_output = result.get("_raw_result")
                                if tool_name == ManageTeamTool.name:
                                    if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                        action = raw_tool_output.get("action"); params = raw_tool_output.get("params", {})
                                        act_success, act_msg, act_data = await self._interaction_handler.handle_manage_team_action(action, params, agent_id)
                                        feedback = {"call_id": call_id, "action": action, "success": act_success, "message": act_msg}
                                        if act_data: feedback["data"] = act_data
                                        manager_action_feedback.append(feedback)
                                    elif isinstance(raw_tool_output, dict): manager_action_feedback.append({"call_id": call_id, "action": raw_tool_output.get("action"), "success": False, "message": raw_tool_output.get("message", "Tool execution failed.")})
                                    else: manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result structure."})
                                elif tool_name == SendMessageTool.name:
                                    target_id = call['arguments'].get("target_agent_id"); msg_content = call['arguments'].get("message_content")
                                    if target_id and msg_content is not None:
                                        activation_task = await self._interaction_handler.route_and_activate_agent_message(agent_id, target_id, msg_content)
                                        if activation_task: activation_tasks.append(activation_task)
                                    else: manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": "Validation Error: Missing target_id or message_content."})
                            else: manager_action_feedback.append({"call_id": call_id, "action": tool_name, "success": False, "message": "Tool execution failed unexpectedly (no result)."})
                        if activation_tasks: await asyncio.gather(*activation_tasks); logger.info(f"CycleHandler: Completed activation tasks for '{agent_id}'.")
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
                             if feedback_appended: needs_reactivation_after_cycle = True
                        if executed_tool_successfully_this_cycle and not manager_action_feedback:
                             logger.debug(f"CycleHandler: Successful standard tool execution for '{agent_id}'. Setting reactivation flag.")
                             needs_reactivation_after_cycle = True
                else: # Unknown event type
                    logger.warning(f"CycleHandler: Unknown event type '{event_type}' from '{agent_id}'.")

        except Exception as e:
             logger.error(f"CycleHandler: Error handling generator for '{agent_id}': {e}", exc_info=True)
             current_cycle_error = True
             is_fatal_error = True # Treat this as fatal too
             last_error_content = f"[Manager Error: Unexpected error in generator handler - {e}]"
             await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
        finally:
            # Ensure generator is closed
            if agent_generator:
                try: await agent_generator.aclose()
                except Exception as close_err: logger.error(f"CycleHandler: Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            # --- Modified Retry / Override / Reactivation Logic ---
            if current_cycle_error:
                if is_fatal_error:
                     # *** NEW: Immediately request override for fatal errors ***
                     logger.error(f"CycleHandler: Agent '{agent_id}' encountered fatal error. Requesting user override immediately.")
                     agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
                     await self._manager.request_user_override(agent_id, f"[Fatal Error] {last_error_content}")
                elif is_retryable_transient_error and retry_count < MAX_STREAM_RETRIES:
                     # Standard retry logic for transient errors
                     retry_delay = STREAM_RETRY_DELAYS[retry_count]
                     logger.warning(f"CycleHandler: Transient error for '{agent_id}'. Retrying in {retry_delay:.1f}s ({retry_count + 1}/{MAX_STREAM_RETRIES})...")
                     await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES}, delay {retry_delay}s)..."})
                     await asyncio.sleep(retry_delay)
                     agent.set_status(AGENT_STATUS_IDLE)
                     asyncio.create_task(self._manager.schedule_cycle(agent, retry_count + 1))
                elif is_retryable_transient_error: # Max retries reached for transient error
                     logger.error(f"CycleHandler: Agent '{agent_id}' failed transient error after {MAX_STREAM_RETRIES} retries. Requesting user override.")
                     agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
                     await self._manager.request_user_override(agent_id, f"[Max Retries Reached] {last_error_content}")
                else: # Other non-fatal, non-transient errors (set error state)
                     logger.error(f"CycleHandler: Agent '{agent_id}' encountered non-fatal, non-transient error. Setting status to ERROR.")
                     agent.set_status(AGENT_STATUS_ERROR)
                     await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Agent Error] {last_error_content}"})

            elif needs_reactivation_after_cycle: # No error, but needs reactivation
                 logger.info(f"CycleHandler: Reactivating agent '{agent_id}' after successful tool/feedback processing.")
                 agent.set_status(AGENT_STATUS_IDLE)
                 asyncio.create_task(self._manager.schedule_cycle(agent, 0))
            else: # No error, check for new messages
                 history_len_after = len(agent.message_history)
                 if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                      logger.info(f"CycleHandler: Agent '{agent_id}' has new user message(s). Reactivating.")
                      agent.set_status(AGENT_STATUS_IDLE)
                      asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                 else:
                      logger.debug(f"CycleHandler: Agent '{agent_id}' finished cycle cleanly, no immediate reactivation needed.")
                      if agent.status not in [AGENT_STATUS_AWAITING_USER_OVERRIDE, AGENT_STATUS_ERROR]:
                          agent.set_status(AGENT_STATUS_IDLE)

            # Log final status
            log_level = logging.ERROR if agent.status in [AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE] else logging.INFO
            logger.log(log_level, f"CycleHandler: Finished cycle for Agent '{agent_id}'. Final status: {agent.status}")
