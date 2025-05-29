# START OF FILE src/agents/cycle_handler.py
import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Dict, Any, Optional, List

from src.llm_providers.base import ToolResultDict, MessageDict
from src.agents.core import Agent
from src.config.settings import settings

from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_ERROR, AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_PLANNING, ADMIN_STATE_CONVERSATION, ADMIN_STATE_STARTUP,
    PM_STATE_STARTUP, PM_STATE_MANAGE, PM_STATE_WORK, PM_STATE_BUILD_TEAM_TASKS, # Added PM_STATE_BUILD_TEAM_TASKS
    WORKER_STATE_WAIT, REQUEST_STATE_TAG_PATTERN
)

# Import for keyword extraction
from src.utils.text_utils import extract_keywords_from_text
from src.tools.knowledge_base import KnowledgeBaseTool # Assuming this is the correct tool name

from src.agents.cycle_components import (
    CycleContext,
    PromptAssembler,
    LLMCaller, 
    CycleOutcomeDeterminer,
    NextStepScheduler
)

from src.workflows.base import WorkflowResult 

if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.interaction_handler import AgentInteractionHandler


logger = logging.getLogger(__name__)

class AgentCycleHandler:
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager = manager
        self._interaction_handler = interaction_handler
        
        self._prompt_assembler = PromptAssembler(self._manager)
        self._outcome_determiner = CycleOutcomeDeterminer()
        self._next_step_scheduler = NextStepScheduler(self._manager)
        
        self.request_state_pattern = REQUEST_STATE_TAG_PATTERN 
        logger.info("AgentCycleHandler initialized.")

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        logger.critical(f"!!! CycleHandler: run_cycle TASK STARTED for Agent '{agent.agent_id}' (Retry: {retry_count}) !!!")
        
        context = CycleContext(
            agent=agent, manager=self._manager, retry_count=retry_count,
            current_provider_name=agent.provider_name, current_model_name=agent.model,
            current_model_key_for_tracking=f"{agent.provider_name}/{agent.model}",
            max_retries_for_cycle=settings.MAX_STREAM_RETRIES,
            retry_delay_for_cycle=settings.RETRY_DELAY_SECONDS,
            current_db_session_id=self._manager.current_session_db_id
        )
        
        if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.add(context.current_model_key_for_tracking)
        else: agent._failed_models_this_cycle = {context.current_model_key_for_tracking}
        
        agent_generator = None
        try:
            await self._prompt_assembler.prepare_llm_call_data(context)
            agent.set_status(AGENT_STATUS_PROCESSING)
            
            agent_generator = agent.process_message(history_override=context.history_for_call)

            async for event in agent_generator:
                event_type = event.get("type")
                logger.debug(f"CycleHandler '{agent.agent_id}': Received Event from Agent.process_message: Type='{event_type}', Keys={list(event.keys())}")

                if event_type == "error":
                    context.last_error_obj = event.get('_exception_obj', ValueError(event.get('content', 'Unknown Agent Core Error')))
                    context.last_error_content = event.get("content", "[CycleHandler Error]: Unknown error from agent processing.")
                    self._outcome_determiner.determine_cycle_outcome(context)
                    if context.current_db_session_id:
                        await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_error", content=context.last_error_content)
                    break 

                elif event_type == "workflow_executed":
                    context.action_taken_this_cycle = True 
                    workflow_result_data = event.get("result_data")
                    
                    # --- ADD LOGGING FOR workflow_result_data ---
                    logger.critical(f"CycleHandler '{agent.agent_id}': workflow_executed event data: {workflow_result_data}")
                    # --- END LOGGING ---

                    if not workflow_result_data or not isinstance(workflow_result_data, dict): 
                        logger.error(f"CycleHandler '{agent.agent_id}': 'workflow_executed' event missing or malformed 'result_data'. Cannot process workflow outcome. Data: {workflow_result_data}")
                        context.last_error_content = "Workflow execution event malformed (result_data missing or not a dict)."
                        context.last_error_obj = ValueError(context.last_error_content)
                        break
                    
                    try:
                        workflow_result = WorkflowResult(**workflow_result_data)
                    except Exception as pydantic_err:
                        logger.error(f"CycleHandler '{agent.agent_id}': Failed to parse WorkflowResult from event data: {pydantic_err}. Data: {workflow_result_data}", exc_info=True)
                        context.last_error_content = f"Workflow result parsing error: {pydantic_err}"
                        context.last_error_obj = pydantic_err
                        break

                    logger.info(f"CycleHandler '{agent.agent_id}': Processing workflow result for '{workflow_result.workflow_name}'. Success: {workflow_result.success}")

                    if workflow_result.next_agent_state:
                        self._manager.workflow_manager.change_state(agent, workflow_result.next_agent_state)
                    if workflow_result.next_agent_status:
                        agent.set_status(workflow_result.next_agent_status)
                    
                    if workflow_result.ui_message_data:
                        await self._manager.send_to_ui(workflow_result.ui_message_data)

                    if workflow_result.tasks_to_schedule:
                        logger.info(f"CycleHandler '{agent.agent_id}': Workflow result has {len(workflow_result.tasks_to_schedule)} tasks to schedule.")
                        for task_agent, task_retry_count in workflow_result.tasks_to_schedule:
                            if task_agent and isinstance(task_agent, Agent): 
                                logger.info(f"CycleHandler '{agent.agent_id}': Workflow '{workflow_result.workflow_name}' scheduling agent '{task_agent.agent_id}' with retry {task_retry_count}.")
                                await self._manager.schedule_cycle(task_agent, task_retry_count) 
                            else:
                                logger.warning(f"CycleHandler '{agent.agent_id}': Workflow '{workflow_result.workflow_name}' requested scheduling for an invalid/None agent. Task agent: {task_agent}")
                    else:
                        logger.info(f"CycleHandler '{agent.agent_id}': Workflow result for '{workflow_result.workflow_name}' has no tasks_to_schedule.")

                    
                    if workflow_result.success:
                         context.cycle_completed_successfully = True 
                         # If workflow explicitly schedules the current agent, needs_reactivation_after_cycle can be false.
                         # Otherwise, if it was successful and didn't schedule the current agent, it might need reactivation
                         # if no state change occurred that would naturally lead to idling or further triggers.
                         # For now, if successful, assume workflow handles next steps.
                         if workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule):
                             context.needs_reactivation_after_cycle = False
                         elif not workflow_result.next_agent_state and not workflow_result.tasks_to_schedule:
                             # Successful workflow, no state change, no tasks scheduled FOR THIS AGENT.
                             # This might imply the agent should take another turn based on the new context.
                             # However, the default workflow model is one action per output.
                             context.needs_reactivation_after_cycle = False # Default to false for successful workflow
                             logger.debug(f"CycleHandler: Successful workflow '{workflow_result.workflow_name}' for agent '{agent.agent_id}' completed. No explicit reschedule of current agent. Agent goes idle in new/current state.")
                         else:
                             context.needs_reactivation_after_cycle = False

                    else: # Workflow failed
                         context.last_error_content = f"Workflow '{workflow_result.workflow_name}' failed: {workflow_result.message}"
                         context.last_error_obj = ValueError(context.last_error_content)
                         # If workflow failed but specified tasks_to_schedule (e.g. to retry itself)
                         if workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule):
                             context.needs_reactivation_after_cycle = False # Workflow handles retry
                         elif not workflow_result.tasks_to_schedule and not workflow_result.next_agent_state and workflow_result.next_agent_status != AGENT_STATUS_ERROR:
                             logger.info(f"CycleHandler: Workflow '{workflow_result.workflow_name}' failed for agent '{agent.agent_id}' but did not specify next steps or error status. Marking for reactivation to potentially retry/correct.")
                             context.needs_reactivation_after_cycle = True
                         else:
                             context.needs_reactivation_after_cycle = False 

                    break 

                elif event_type == "agent_thought":
                    context.action_taken_this_cycle = True; context.thought_produced_this_cycle = True
                    thought_content = event.get("content")
                    if not thought_content:
                        logger.warning(f"Agent {agent.agent_id} produced an empty thought. Skipping KB save.")
                    else:
                        # Log thought to main DB
                        if context.current_db_session_id:
                            await self._manager.db_manager.log_interaction(
                                session_id=context.current_db_session_id,
                                agent_id=agent.agent_id,
                                role="assistant_thought",
                                content=thought_content
                            )
                        
                        # Send thought to UI
                        await self._manager.send_to_ui(event)

                        # --- Save thought to Knowledge Base with extracted keywords ---
                        try:
                            extracted_keywords_list = extract_keywords_from_text(thought_content, max_keywords=5)
                            
                            default_keywords = ["agent_thought", agent.agent_id]
                            if agent.persona:
                                default_keywords.append(agent.persona.lower().replace(" ", "_"))
                            if agent.agent_type:
                                default_keywords.append(agent.agent_type.lower())

                            combined_keywords = list(set(default_keywords + extracted_keywords_list))
                            # Filter out any None or empty strings that might have slipped in
                            final_keywords_list = [kw for kw in combined_keywords if kw and kw.strip()]
                            
                            final_keywords_str = ",".join(final_keywords_list)
                            
                            logger.info(f"Agent {agent.agent_id} saving thought to KB. Keywords: '{final_keywords_str}'")

                            kb_tool_args = {
                                "action": "save_knowledge",
                                "content": thought_content,
                                "keywords": final_keywords_str,
                                "title": f"Agent Thought by {agent.agent_id} ({agent.persona}) at {time.strftime('%Y-%m-%d %H:%M:%S')}"
                            }
                            
                            # Assuming KnowledgeBaseTool.name is defined and tool_executor is available
                            if hasattr(self._manager.tool_executor, 'execute_tool') and hasattr(KnowledgeBaseTool, 'name'):
                                # Generate a unique call_id for this internal tool call
                                kb_call_id = f"internal_kb_save_{agent.agent_id}_{int(time.time() * 1000)}"
                                
                                # Execute directly via interaction_handler's method if suitable, or tool_executor
                                # For simplicity, using tool_executor directly, assuming it handles context correctly for internal calls
                                # Note: execute_single_tool in interaction_handler changes agent status, which might not be desired here.
                                # Let's construct a direct call to tool_executor's execute_tool or a similar method.
                                # For now, we'll simulate the direct execution path or assume a simplified internal call.
                                
                                kb_save_result = await self._manager.tool_executor.execute_tool(
                                    agent_id=agent.agent_id, # or a system/KB agent ID
                                    agent_sandbox_path=agent.sandbox_path, # KB tool might not need sandbox
                                    tool_name=KnowledgeBaseTool.name,
                                    tool_args=kb_tool_args,
                                    project_name=self._manager.current_project, # Pass current project/session context
                                    session_name=self._manager.current_session,
                                    manager=self._manager # Pass manager for context
                                )
                                if isinstance(kb_save_result, str) and "Error" in kb_save_result:
                                     logger.error(f"Agent {agent.agent_id}: Failed to save thought to KB. Result: {kb_save_result}")
                                elif isinstance(kb_save_result, dict) and kb_save_result.get("status") == "error":
                                     logger.error(f"Agent {agent.agent_id}: Failed to save thought to KB. Result: {kb_save_result.get('message')}")
                                else:
                                     logger.info(f"Agent {agent.agent_id}: Thought successfully saved to KB.")
                            else:
                                logger.warning(f"Agent {agent.agent_id}: KnowledgeBaseTool or tool_executor not properly configured for saving thought.")
                        except Exception as kb_exc:
                            logger.error(f"Agent {agent.agent_id}: Exception while saving thought to KB: {kb_exc}", exc_info=True)
                        # --- End KB Save ---

                elif event_type == "agent_state_change_requested":
                    context.action_taken_this_cycle = True; context.state_change_requested_this_cycle = True
                    requested_state = event.get("requested_state")
                    if self._manager.workflow_manager.change_state(agent, requested_state):
                        context.needs_reactivation_after_cycle = True 
                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="agent_state_change", content=f"State changed to: {requested_state}")
                    else:
                        context.needs_reactivation_after_cycle = True 
                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_warning", content=f"Agent attempted invalid state change to: {requested_state}")
                    await self._manager.send_to_ui(event); break 

                elif event_type == "tool_requests":
                    context.action_taken_this_cycle = True
                    tool_calls = event.get("calls", [])
                    raw_assistant_response = event.get("raw_assistant_response")
                    if raw_assistant_response:
                        assistant_message_for_history: MessageDict = {"role": "assistant", "content": raw_assistant_response}
                        if tool_calls: assistant_message_for_history["tool_calls"] = tool_calls
                        agent.message_history.append(assistant_message_for_history)
                    if context.current_db_session_id and raw_assistant_response:
                        await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=raw_assistant_response, tool_calls=tool_calls)
                    
                    all_tool_results_for_history: List[MessageDict] = [] # Stores formatted results for agent history
                    any_tool_success = False
                    
                    if len(tool_calls) > 1:
                        logger.info(f"CycleHandler '{agent.agent_id}': Processing {len(tool_calls)} tool calls sequentially.")

                    for i, call_data in enumerate(tool_calls):
                        tool_name = call_data.get("name")
                        tool_id = call_data.get("id")
                        tool_args = call_data.get("arguments", {})
                        
                        logger.info(f"CycleHandler '{agent.agent_id}': Executing tool {i+1}/{len(tool_calls)}: Name='{tool_name}', ID='{tool_id}'")
                        
                        # interaction_handler.execute_single_tool sets agent status to EXECUTING_TOOL
                        # and then back to PROCESSING. This is fine.
                        result_dict = await self._interaction_handler.execute_single_tool(
                            agent, 
                            tool_id, 
                            tool_name, 
                            tool_args, 
                            self._manager.current_project, 
                            self._manager.current_session
                        )
                        
                        if result_dict:
                            # Format for agent's message history
                            history_item: MessageDict = { # Ensure it's MessageDict compatible
                                "role": "tool",
                                "tool_call_id": result_dict.get("call_id", tool_id or "unknown_id_in_loop"),
                                "name": result_dict.get("name", tool_name or "unknown_tool_in_loop"), # Use original name if result doesn't have it
                                "content": str(result_dict.get("content", "[Tool Error: No content]"))
                            }
                            all_tool_results_for_history.append(history_item)
                            
                            result_content_str = str(result_dict.get("content", ""))
                            if not result_content_str.lower().startswith("[toolerror") and \
                               not result_content_str.lower().startswith("error:") and \
                               not result_content_str.lower().startswith("[toolexec error"):
                                any_tool_success = True
                                logger.info(f"CycleHandler '{agent.agent_id}': Tool '{tool_name}' (ID: {tool_id}) executed successfully.")
                            else:
                                logger.warning(f"CycleHandler '{agent.agent_id}': Tool '{tool_name}' (ID: {tool_id}) executed with error/failure: {result_content_str[:100]}...")

                            # Log individual tool result to DB
                            if context.current_db_session_id:
                                await self._manager.db_manager.log_interaction(
                                    session_id=context.current_db_session_id,
                                    agent_id=agent.agent_id,
                                    role="tool", # Individual tool execution
                                    content=result_content_str, # The content of this specific tool's result
                                    tool_results=[result_dict] # Pass the single result_dict in a list
                                )
                            
                            # Send individual tool result to UI
                            await self._manager.send_to_ui({
                                **result_dict, 
                                "type": "tool_result", 
                                "agent_id": agent.agent_id,
                                "tool_sequence": f"{i+1}_of_{len(tool_calls)}" # Add sequence info
                            })
                        else:
                            # Handle case where execute_single_tool might return None (should be rare)
                            logger.error(f"CycleHandler '{agent.agent_id}': Tool '{tool_name}' (ID: {tool_id}) execution returned None.")
                            history_item: MessageDict = {
                                "role": "tool", 
                                "tool_call_id": tool_id or f"unknown_call_{i}", 
                                "name": tool_name or f"unknown_tool_{i}", 
                                "content": "[Tool Error: Execution returned no result object]"
                            }
                            all_tool_results_for_history.append(history_item)
                    
                    # Append all collected tool results to the agent's history
                    for res_hist_item in all_tool_results_for_history:
                        agent.message_history.append(res_hist_item)
                    
                    context.executed_tool_successfully_this_cycle = any_tool_success
                    context.needs_reactivation_after_cycle = True # Agent needs to process the aggregated results
                    logger.info(f"CycleHandler '{agent.agent_id}': Finished processing {len(tool_calls)} tool calls. Any success: {any_tool_success}. Needs reactivation.")
                    break # Break from the agent_generator loop, as this cycle's LLM turn is done.

                elif event_type in ["response_chunk", "status", "final_response", "invalid_state_request_output"]:
                    if event_type == "final_response" and context.current_db_session_id and event.get("content"):
                        if not agent.message_history or not agent.message_history[-1].get("tool_calls"): 
                            await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=event.get("content"))
                    elif event_type == "invalid_state_request_output":
                        context.action_taken_this_cycle = True; context.needs_reactivation_after_cycle = True
                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_warning", content=f"Agent attempted invalid state change: {event.get('content')}")
                    await self._manager.send_to_ui(event)
                else:
                    logger.warning(f"CycleHandler: Unknown event type '{event_type}' from agent '{agent.agent_id}'.")
            
            if not context.last_error_obj and agent.text_buffer.strip() and not context.action_taken_this_cycle:
                final_content_from_buffer = agent.text_buffer.strip(); agent.text_buffer = ""
                if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content_from_buffer)
                await self._manager.send_to_ui({"type": "final_response", "content": final_content_from_buffer, "agent_id": agent.agent_id})

            if agent_generator and not context.last_error_obj and not context.action_taken_this_cycle : 
                context.cycle_completed_successfully = True
            elif not context.last_error_obj and context.action_taken_this_cycle: 
                context.cycle_completed_successfully = True


        except Exception as e:
            logger.critical(f"CycleHandler: UNHANDLED EXCEPTION during agent '{agent.agent_id}' cycle: {e}", exc_info=True)
            context.last_error_obj = e
            context.last_error_content = f"[CycleHandler CRITICAL]: Unhandled exception - {type(e).__name__}"
            context.trigger_failover = True 
            if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_error", content=context.last_error_content)
            await self._manager.send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": context.last_error_content})
        
        finally:
            if agent_generator:
                try: 
                    # Check if the generator is not already closed or exhausted
                    if not agent_generator.ag_running and agent_generator.ag_frame is not None:
                        await agent_generator.aclose()
                    elif agent_generator.ag_running:
                        logger.warning(f"Agent generator for '{agent.agent_id}' was still running in finally block, attempting to close.")
                        await agent_generator.aclose()

                except RuntimeError as r_err: # Specifically catch "aclose(): asynchronous generator is already running"
                    if "aclose(): asynchronous generator is already running" in str(r_err):
                        logger.warning(f"Agent generator for '{agent.agent_id}' was already closing: {r_err}")
                    else:
                        logger.error(f"RuntimeError closing agent generator for '{agent.agent_id}': {r_err}", exc_info=True)
                except Exception as close_err: 
                    logger.warning(f"Error closing agent generator for '{agent.agent_id}': {close_err}", exc_info=True)
            
            context.llm_call_duration_ms = (time.perf_counter() - context.start_time) * 1000

            if context.last_error_obj and not context.cycle_completed_successfully: 
                 self._outcome_determiner.determine_cycle_outcome(context) 
            elif not context.last_error_obj and context.cycle_completed_successfully: 
                 pass 
            else: 
                 self._outcome_determiner.determine_cycle_outcome(context) 

            if not context.is_provider_level_error: 
                success_for_metrics = context.cycle_completed_successfully and not context.is_key_related_error
                await self._manager.performance_tracker.record_call(
                    provider=context.current_provider_name or "unknown", model_id=context.current_model_name or "unknown",
                    duration_ms=context.llm_call_duration_ms, success=success_for_metrics
                )
            
            await self._next_step_scheduler.schedule_next_step(context)
            logger.info(f"CycleHandler: Finished cycle logic for Agent '{agent.agent_id}'. Final status for this attempt: {agent.status}. State: {agent.state}")