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
    # Added MAX_FAILOVER_ATTEMPTS
    from src.agents.manager import AgentManager, MAX_STREAM_RETRIES, STREAM_RETRY_DELAYS, MAX_FAILOVER_ATTEMPTS
    from src.agents.interaction_handler import AgentInteractionHandler

logger = logging.getLogger(__name__)

FAILOVER_TRIGGERS = [
    "llama runner process has terminated", "exit status",
    "Connection closed during stream", "ClientConnectionError",
    "APIError during stream", "error processing stream chunk",
    "failed to decode stream chunk", "Provider returned error",
    "Client Error 4", "AuthenticationError", "BadRequestError",
    "PermissionDeniedError", "NotFoundError"
]

class AgentCycleHandler:
    """
    Handles the agent execution cycle. Triggers failover via AgentManager
    for persistent errors. Records performance metrics.
    User override mechanism removed.
    """
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager = manager
        self._interaction_handler = interaction_handler
        logger.info("AgentCycleHandler initialized.")

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        """
        Manages the agent's process_message generator. Handles events, tools,
        retries (for specific transient errors), and triggers failover for
        other errors via AgentManager. Records metrics.
        """
        # Need MAX_FAILOVER_ATTEMPTS for logging/context, remove if not needed
        from src.agents.manager import MAX_STREAM_RETRIES, STREAM_RETRY_DELAYS, MAX_FAILOVER_ATTEMPTS

        agent_id = agent.agent_id
        current_provider = agent.provider_name
        current_model = agent.model
        logger.info(f"CycleHandler: Starting cycle for Agent '{agent_id}' (Retry: {retry_count}). Provider: {current_provider}, Model: {current_model}")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        current_cycle_error = False
        trigger_failover = False
        is_retryable_transient_error = False
        last_error_content = ""
        history_len_before = len(agent.message_history)
        executed_tool_successfully_this_cycle = False
        needs_reactivation_after_cycle = False
        start_time = time.perf_counter()
        llm_call_duration_ms = 0.0

        if not hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle = set()
        agent._failed_models_this_cycle.add(f"{current_provider}/{current_model}")
        logger.debug(f"Agent '{agent_id}' attempting model '{current_provider}/{current_model}'. Failed this cycle so far: {agent._failed_models_this_cycle}")


        try:
            agent_generator = agent.process_message()
            while True:
                try: event = await agent_generator.asend(None)
                except StopAsyncIteration: logger.info(f"CycleHandler: Agent '{agent_id}' generator finished normally."); break
                except Exception as gen_err:
                    logger.error(f"CycleHandler: Generator error for '{agent_id}': {gen_err}", exc_info=True)
                    current_cycle_error = True; last_error_content = f"[Manager Error: Unexpected error in generator handler - {gen_err}]"; trigger_failover = True; break

                event_type = event.get("type")

                # Non-Error Events
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self._manager.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                             agent.message_history.append({"role": "assistant", "content": final_content})
                # Error Event
                elif event_type == "error":
                    last_error_content = event.get("content", "[Agent Error]")
                    logger.error(f"CycleHandler: Agent '{agent_id}' reported error: {last_error_content}")
                    current_cycle_error = True
                    is_retryable_transient_error = not trigger_failover and any(ind.lower() in last_error_content.lower() for ind in ["RateLimitError", "Status 429", "Status 503", "APITimeoutError", "timeout error"])
                    if not is_retryable_transient_error:
                         trigger_failover = any(ind.lower() in last_error_content.lower() for ind in FAILOVER_TRIGGERS)
                         if trigger_failover: logger.warning(f"CycleHandler: Detected error likely requiring failover for agent '{agent_id}'. Error: {last_error_content}")
                    break # Break on ANY error
                # Tool Requests Event
                elif event_type == "tool_requests":
                    all_tool_calls: List[Dict] = event.get("calls", [])
                    if not all_tool_calls: continue; logger.info(f"CycleHandler: Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"): agent.message_history.append({"role": "assistant", "content": agent_last_response})
                    mgmt_calls = []; other_calls = []; invalid_call_results = []
                    for call in all_tool_calls:
                        cid, tname, targs = call.get("id"), call.get("name"), call.get("arguments", {})
                        if cid and tname and isinstance(targs, dict):
                            if tname == ManageTeamTool.name: mgmt_calls.append(call)
                            else: other_calls.append(call)
                        else: fail_res = await self._interaction_handler.failed_tool_result(cid, tname);
                            if fail_res: invalid_call_results.append(fail_res)
                    if invalid_call_results:
                        for res in invalid_call_results: agent.message_history.append({"role": "tool", **res})
                    calls_to_execute = mgmt_calls + other_calls; activation_tasks = []; manager_action_feedback = []; executed_tool_successfully_this_cycle = False
                    if calls_to_execute:
                        logger.info(f"CycleHandler: Executing {len(calls_to_execute)} tool(s) sequentially for '{agent_id}'."); await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})
                        for call in calls_to_execute:
                            call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']; result = await self._interaction_handler.execute_single_tool(agent, call_id, tool_name, tool_args, project_name=self._manager.current_project, session_name=self._manager.current_session)
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
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != fb_content: agent.message_history.append(fb_msg); feedback_appended = True
                             if feedback_appended: needs_reactivation_after_cycle = True
                        if executed_tool_successfully_this_cycle and not manager_action_feedback: logger.debug(f"CycleHandler: Successful standard tool execution for '{agent_id}'. Setting reactivation flag."); needs_reactivation_after_cycle = True
                else: logger.warning(f"CycleHandler: Unknown event type '{event_type}' from '{agent_id}'.")

        except Exception as e:
             logger.error(f"CycleHandler: Error handling generator for '{agent_id}': {e}", exc_info=True)
             current_cycle_error = True; trigger_failover = True; last_error_content = f"[Manager Error: Unexpected error in generator handler - {e}]"
             try: await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
             except Exception as ui_err: logger.error(f"Error sending error status to UI in cycle handler: {ui_err}")
        finally:
            # Close generator
            if agent_generator:
                try: await agent_generator.aclose()
                except Exception as close_err: logger.error(f"CycleHandler: Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            # Record Performance Metrics
            end_time = time.perf_counter(); llm_call_duration_ms = (end_time - start_time) * 1000
            call_success = not current_cycle_error
            logger.debug(f"Cycle outcome for {current_provider}/{current_model}: Success={call_success}, Duration={llm_call_duration_ms:.2f}ms")
            try: await self._manager.performance_tracker.record_call(provider=current_provider, model_id=current_model, duration_ms=llm_call_duration_ms, success=call_success)
                 logger.debug(f"Successfully recorded metrics for {current_provider}/{current_model}")
            except Exception as record_err: logger.error(f"Failed to record performance metrics for {current_provider}/{current_model}: {record_err}", exc_info=True)

            # --- Modified Error Handling: Prioritize Failover, then Retry, then FINAL ERROR ---
            if current_cycle_error:
                if is_retryable_transient_error and retry_count < MAX_STREAM_RETRIES:
                     # Standard retry logic for specific transient errors
                     retry_delay = STREAM_RETRY_DELAYS[retry_count]
                     logger.warning(f"CycleHandler: Transient error for '{agent_id}'. Retrying same model in {retry_delay:.1f}s ({retry_count + 1}/{MAX_STREAM_RETRIES})...")
                     await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES}, delay {retry_delay}s)..."})
                     await asyncio.sleep(retry_delay); agent.set_status(AGENT_STATUS_IDLE)
                     asyncio.create_task(self._manager.schedule_cycle(agent, retry_count + 1))
                else:
                     # Trigger failover attempt for fatal, non-transient, or max-retries-reached errors
                     logger.warning(f"CycleHandler: Agent '{agent_id}' encountered persistent/fatal error or max retries reached for transient error. Triggering failover attempt. Last Error: {last_error_content}")
                     # --- ** CHANGED: Call failover handler unconditionally here ** ---
                     asyncio.create_task(self._manager.handle_agent_model_failover(agent_id, last_error_content))
                     # The failover handler will eventually set status to ERROR if it fails completely

            elif needs_reactivation_after_cycle: # No error, but needs reactivation
                 logger.info(f"CycleHandler: Reactivating agent '{agent_id}' after successful tool/feedback processing.")
                 if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                 agent.set_status(AGENT_STATUS_IDLE); asyncio.create_task(self._manager.schedule_cycle(agent, 0))
            else: # No error, check for new messages or idle
                 history_len_after = len(agent.message_history)
                 if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                      logger.info(f"CycleHandler: Agent '{agent_id}' has new user message(s). Reactivating.")
                      if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                      agent.set_status(AGENT_STATUS_IDLE); asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                 else:
                      logger.debug(f"CycleHandler: Agent '{agent_id}' finished cycle cleanly, no immediate reactivation needed.")
                      if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                      # --- ** CHANGED: Don't set to ERROR here ** ---
                      if agent.status != AGENT_STATUS_ERROR: # Only set idle if not already error
                          agent.set_status(AGENT_STATUS_IDLE)

            # Log final status (which might be IDLE even if failover was triggered)
            log_level = logging.ERROR if agent.status == AGENT_STATUS_ERROR else logging.INFO
            logger.log(log_level, f"CycleHandler: Finished cycle logic for Agent '{agent_id}'. Current status: {agent.status}")
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
    from src.agents.manager import AgentManager, MAX_STREAM_RETRIES, STREAM_RETRY_DELAYS, MAX_FAILOVER_ATTEMPTS # Added MAX_FAILOVER_ATTEMPTS
    from src.agents.interaction_handler import AgentInteractionHandler

logger = logging.getLogger(__name__)

FAILOVER_TRIGGERS = [
    "llama runner process has terminated", "exit status",
    "Connection closed during stream", "ClientConnectionError",
    "APIError during stream", "error processing stream chunk",
    "failed to decode stream chunk", "Provider returned error",
    "Client Error 4", "AuthenticationError", "BadRequestError",
    "PermissionDeniedError", "NotFoundError"
]

class AgentCycleHandler:
    """
    Handles the execution cycle of an agent's turn. Includes logic for retries
    on transient errors and triggers model/provider failover via AgentManager
    for persistent or fatal errors. Records performance metrics.
    """
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager = manager
        self._interaction_handler = interaction_handler
        logger.info("AgentCycleHandler initialized.")

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        """
        Manages the agent's process_message generator. Handles events, tools,
        retries, failover triggers, and reactivation logic. Records metrics.
        """
        from src.agents.manager import MAX_STREAM_RETRIES, STREAM_RETRY_DELAYS, MAX_FAILOVER_ATTEMPTS

        agent_id = agent.agent_id
        current_provider = agent.provider_name
        current_model = agent.model
        logger.info(f"CycleHandler: Starting cycle for Agent '{agent_id}' (Retry: {retry_count}). Provider: {current_provider}, Model: {current_model}")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        current_cycle_error = False
        trigger_failover = False
        is_retryable_transient_error = False
        last_error_content = ""
        history_len_before = len(agent.message_history)
        executed_tool_successfully_this_cycle = False
        needs_reactivation_after_cycle = False
        start_time = time.perf_counter()
        llm_call_duration_ms = 0.0

        if not hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle = set()
        agent._failed_models_this_cycle.add(f"{current_provider}/{current_model}")
        logger.debug(f"Agent '{agent_id}' attempting model '{current_provider}/{current_model}'. Failed this cycle so far: {agent._failed_models_this_cycle}")

        try:
            agent_generator = agent.process_message()
            while True:
                try: event = await agent_generator.asend(None)
                except StopAsyncIteration: logger.info(f"CycleHandler: Agent '{agent_id}' generator finished normally."); break
                except Exception as gen_err:
                    logger.error(f"CycleHandler: Generator error for '{agent_id}': {gen_err}", exc_info=True)
                    current_cycle_error = True; last_error_content = f"[Manager Error: Unexpected error in generator handler - {gen_err}]"; trigger_failover = True; break

                event_type = event.get("type")

                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self._manager.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                             agent.message_history.append({"role": "assistant", "content": final_content})

                elif event_type == "error":
                    last_error_content = event.get("content", "[Agent Error]")
                    logger.error(f"CycleHandler: Agent '{agent_id}' reported error: {last_error_content}")
                    current_cycle_error = True
                    is_retryable_transient_error = not trigger_failover and any(ind.lower() in last_error_content.lower() for ind in ["RateLimitError", "Status 429", "Status 503", "APITimeoutError", "timeout error"])
                    if not is_retryable_transient_error:
                         trigger_failover = any(ind.lower() in last_error_content.lower() for ind in FAILOVER_TRIGGERS)
                         if trigger_failover: logger.warning(f"CycleHandler: Detected error likely requiring failover for agent '{agent_id}'. Error: {last_error_content}")
                    break

                elif event_type == "tool_requests":
                    all_tool_calls: List[Dict] = event.get("calls", [])
                    if not all_tool_calls: continue; logger.info(f"CycleHandler: Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"): agent.message_history.append({"role": "assistant", "content": agent_last_response})
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
                    calls_to_execute = mgmt_calls + other_calls; activation_tasks = []; manager_action_feedback = []; executed_tool_successfully_this_cycle = False
                    if calls_to_execute:
                        logger.info(f"CycleHandler: Executing {len(calls_to_execute)} tool(s) sequentially for '{agent_id}'."); await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})
                        for call in calls_to_execute:
                            call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']; result = await self._interaction_handler.execute_single_tool(agent, call_id, tool_name, tool_args, project_name=self._manager.current_project, session_name=self._manager.current_session)
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
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != fb_content: agent.message_history.append(fb_msg); feedback_appended = True
                             if feedback_appended: needs_reactivation_after_cycle = True
                        if executed_tool_successfully_this_cycle and not manager_action_feedback: logger.debug(f"CycleHandler: Successful standard tool execution for '{agent_id}'. Setting reactivation flag."); needs_reactivation_after_cycle = True
                else: logger.warning(f"CycleHandler: Unknown event type '{event_type}' from '{agent_id}'.")

        except Exception as e:
             logger.error(f"CycleHandler: Error handling generator for '{agent_id}': {e}", exc_info=True)
             current_cycle_error = True; trigger_failover = True; last_error_content = f"[Manager Error: Unexpected error in generator handler - {e}]"
             try: await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
             except Exception as ui_err: logger.error(f"Error sending error status to UI in cycle handler: {ui_err}")
        finally:
            # Close generator
            if agent_generator:
                try: await agent_generator.aclose()
                except Exception as close_err: logger.error(f"CycleHandler: Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            # --- *** CORRECTED INDENTATION FOR THIS BLOCK *** ---
            # Record Performance Metrics
            end_time = time.perf_counter(); llm_call_duration_ms = (end_time - start_time) * 1000
            call_success = not current_cycle_error
            logger.debug(f"Cycle outcome for {current_provider}/{current_model}: Success={call_success}, Duration={llm_call_duration_ms:.2f}ms")
            try:
                 await self._manager.performance_tracker.record_call(provider=current_provider, model_id=current_model, duration_ms=llm_call_duration_ms, success=call_success)
                 logger.debug(f"Successfully recorded metrics for {current_provider}/{current_model}")
            except Exception as record_err:
                 logger.error(f"Failed to record performance metrics for {current_provider}/{current_model}: {record_err}", exc_info=True)

            # --- Modified Error Handling / Failover / Retry / Reactivation Logic ---
            # This whole block needs to be inside the finally, at the same level as the metric recording block above
            if current_cycle_error:
                if is_retryable_transient_error and retry_count < MAX_STREAM_RETRIES:
                     # Standard retry logic
                     retry_delay = STREAM_RETRY_DELAYS[retry_count]
                     logger.warning(f"CycleHandler: Transient error for '{agent_id}'. Retrying same model in {retry_delay:.1f}s ({retry_count + 1}/{MAX_STREAM_RETRIES})...")
                     await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES}, delay {retry_delay}s)..."})
                     await asyncio.sleep(retry_delay)
                     agent.set_status(AGENT_STATUS_IDLE)
                     asyncio.create_task(self._manager.schedule_cycle(agent, retry_count + 1))
                else:
                     # Trigger failover
                     logger.warning(f"CycleHandler: Agent '{agent_id}' encountered persistent/fatal error or max retries reached. Triggering failover attempt. Last Error: {last_error_content}")
                     asyncio.create_task(self._manager.handle_agent_model_failover(agent_id, last_error_content))

            elif needs_reactivation_after_cycle:
                 logger.info(f"CycleHandler: Reactivating agent '{agent_id}' after successful tool/feedback processing.")
                 if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                 agent.set_status(AGENT_STATUS_IDLE)
                 asyncio.create_task(self._manager.schedule_cycle(agent, 0))
            else:
                 history_len_after = len(agent.message_history)
                 if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                      logger.info(f"CycleHandler: Agent '{agent_id}' has new user message(s). Reactivating.")
                      if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                      agent.set_status(AGENT_STATUS_IDLE)
                      asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                 else:
                      logger.debug(f"CycleHandler: Agent '{agent_id}' finished cycle cleanly, no immediate reactivation needed.")
                      if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                      if agent.status not in [AGENT_STATUS_AWAITING_USER_OVERRIDE, AGENT_STATUS_ERROR]:
                          agent.set_status(AGENT_STATUS_IDLE)

            # Log final status
            log_level = logging.ERROR if agent.status in [AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE] else logging.INFO
            logger.log(log_level, f"CycleHandler: Finished cycle logic for Agent '{agent_id}'. Current status: {agent.status}")
            # --- *** END CORRECTED INDENTATION BLOCK *** ---
