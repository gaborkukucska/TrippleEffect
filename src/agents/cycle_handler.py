# START OF FILE src/agents/cycle_handler.py
import asyncio
import json
import logging
import time
import re
from typing import TYPE_CHECKING, Dict, Any, Optional, List

from src.llm_providers.base import ToolResultDict, MessageDict
from src.agents.core import Agent
from src.config.settings import settings

from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_AWAITING_CG_REVIEW, AGENT_STATUS_AWAITING_USER_REVIEW_CG, # Added for CG
    AGENT_STATUS_ERROR, AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_PLANNING, ADMIN_STATE_CONVERSATION, ADMIN_STATE_STARTUP,
    PM_STATE_STARTUP, PM_STATE_MANAGE, PM_STATE_WORK, PM_STATE_BUILD_TEAM_TASKS,
    WORKER_STATE_WAIT, REQUEST_STATE_TAG_PATTERN,
    CONSTITUTIONAL_GUARDIAN_AGENT_ID # Added for CG
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

    async def _get_cg_verdict(self, original_agent_final_text: str) -> Optional[str]:
        if not original_agent_final_text or original_agent_final_text.isspace():
            logger.warning("CG review requested for empty or whitespace-only text. Skipping LLM call and returning <OK/>.")
            return "<OK/>"

        cg_agent = self._manager.agents.get(CONSTITUTIONAL_GUARDIAN_AGENT_ID)
        original_cg_status = None # Define before try block

        if not cg_agent:
            logger.error(f"Constitutional Guardian agent '{CONSTITUTIONAL_GUARDIAN_AGENT_ID}' not found. Failing open (assuming <OK/>).")
            return "<OK/>"
        
        if not cg_agent.llm_provider:
            logger.error(f"Constitutional Guardian agent '{CONSTITUTIONAL_GUARDIAN_AGENT_ID}' has no LLM provider. Failing open (assuming <OK/>).")
            return "<OK/>"

        original_cg_status = cg_agent.status
        cg_agent.set_status(AGENT_STATUS_PROCESSING)
        if hasattr(self._manager, 'push_agent_status_update'):
            await self._manager.push_agent_status_update(CONSTITUTIONAL_GUARDIAN_AGENT_ID)
        else:
            logger.error("AgentManager instance not found or lacks push_agent_status_update, cannot update CG status for UI during processing.")

        verdict_to_return = None # Initialize verdict

        try: # Outer try for the main logic + status reset
            text_parts = []
            if hasattr(settings, 'GOVERNANCE_PRINCIPLES') and settings.GOVERNANCE_PRINCIPLES:
                for principle in settings.GOVERNANCE_PRINCIPLES:
                    if principle.get("enabled", False):
                        text_parts.append(f"Principle: {principle.get('name', 'N/A')} (ID: {principle.get('id', 'N/A')})\n{principle.get('text', 'N/A')}")
            governance_text = "\n\n---\n\n".join(text_parts) if text_parts else "No specific governance principles provided."
            cg_prompt_template = settings.PROMPTS.get("cg_system_prompt", "")
            
            if not cg_prompt_template:
                logger.error("System prompt for Constitutional Guardian (cg_system_prompt) not found. Failing open (assuming <OK/>).")
                verdict_to_return = "<OK/>"
            
            if verdict_to_return is None: # Only proceed if no error above from missing prompt template
                formatted_cg_system_prompt = cg_prompt_template.format(governance_principles_text=governance_text)
                cg_history: List[MessageDict] = [
                    {"role": "system", "content": formatted_cg_system_prompt},
                    {"role": "system", "content": f"---\nText for Constitutional Review:\n---\n{original_agent_final_text}"}
                ]
                max_tokens_for_verdict = 250

                try: # Inner try for LLM call and parsing (original try...except block content)
                    logger.info(f"Requesting CG verdict via stream_completion for text: '{original_agent_final_text[:100]}...'")
                    provider_stream = cg_agent.llm_provider.stream_completion(
                        messages=cg_history, model=cg_agent.model,
                        temperature=cg_agent.temperature, max_tokens=max_tokens_for_verdict
                    )
                    full_verdict_text = ""
                    async for event in provider_stream:
                        if event.get("type") == "response_chunk":
                            full_verdict_text += event.get("content", "")
                        elif event.get("type") == "error":
                            logger.error(f"Error during CG LLM stream: {event.get('content')}", exc_info=event.get('_exception_obj'))
                            full_verdict_text = "<OK/>" # Fail-open
                            break
                    stripped_verdict = full_verdict_text.strip()
                    logger.info(f"CG Verdict received (raw full text from stream): '{stripped_verdict}'")

                    OK_TAG = "<OK/>"
                    CONCERN_START_TAG = "<CONCERN>"
                    CONCERN_END_TAG = "</CONCERN>"
                    MALFORMED_CONCERN_MSG = "Constitutional Guardian expressed a concern, but the format was malformed."
                    MALFORMED_INCONCLUSIVE_MSG = "Constitutional Guardian returned a malformed or inconclusive verdict."
                    # ERROR_PROCESSING_MSG is assigned directly in except block now
                    IMPLICIT_OK_PHRASES = [
                        "no constitutional content", "no issues found",
                        "seems to be a friendly greeting",
                        "no substantial text related to constitutional matters", "fully complies"
                    ]

                    if OK_TAG in stripped_verdict:
                        if "concern" in stripped_verdict.lower(): # Ambiguity check
                            verdict_to_return = MALFORMED_CONCERN_MSG
                        else:
                            verdict_to_return = OK_TAG
                    else: # Not an explicit OK, check for concerns or other patterns
                        concern_start_index = stripped_verdict.find(CONCERN_START_TAG)
                        concern_end_index = -1
                        if concern_start_index != -1:
                            concern_end_index = stripped_verdict.find(CONCERN_END_TAG, concern_start_index + len(CONCERN_START_TAG))

                        if concern_start_index != -1 and concern_end_index != -1: # Well-formed concern
                            concern_detail = stripped_verdict[concern_start_index + len(CONCERN_START_TAG):concern_end_index].strip()
                            if concern_detail:
                                verdict_to_return = f"{CONCERN_START_TAG}{concern_detail}{CONCERN_END_TAG}"
                            else: # Tags present, but empty content
                                verdict_to_return = MALFORMED_CONCERN_MSG
                        else: # Not a well-formed concern, check for other signals
                            has_concern_start_tag_only = (concern_start_index != -1 and concern_end_index == -1)
                            contains_concern_keyword = "concern" in stripped_verdict.lower()
                            if has_concern_start_tag_only or contains_concern_keyword:
                                verdict_to_return = MALFORMED_CONCERN_MSG
                            elif stripped_verdict: # Must be non-empty to check for implicit OK
                                is_implicit_ok = False
                                for phrase in IMPLICIT_OK_PHRASES:
                                    if phrase in stripped_verdict.lower():
                                        is_implicit_ok = True; break
                                if is_implicit_ok:
                                    verdict_to_return = OK_TAG
                                else: # No implicit OK, and no other pattern matched
                                    verdict_to_return = MALFORMED_INCONCLUSIVE_MSG
                            else: # Empty stripped_verdict
                                verdict_to_return = MALFORMED_INCONCLUSIVE_MSG

                except Exception as e:
                    logger.error(f"Error during Constitutional Guardian LLM call or verdict parsing: {e}", exc_info=True)
                    verdict_to_return = "Constitutional Guardian encountered an error during verdict processing."

        finally: # Outer finally
            if cg_agent:
                final_status_to_set = original_cg_status if original_cg_status is not None else AGENT_STATUS_IDLE
                cg_agent.set_status(final_status_to_set)
                if hasattr(self._manager, 'push_agent_status_update'):
                    await self._manager.push_agent_status_update(CONSTITUTIONAL_GUARDIAN_AGENT_ID)
                else:
                    logger.error("AgentManager instance not found or lacks push_agent_status_update, cannot revert CG status for UI.")

        return verdict_to_return

    # Removed _request_cg_review method as its functionality is integrated into _get_cg_verdict and run_cycle

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        logger.critical(f"!!! CycleHandler: run_cycle TASK STARTED for Agent '{agent.agent_id}' (Retry: {retry_count}) !!!")
        
        # Initialize context once, parts of it might be reset if recheck occurs
        context = CycleContext(
            agent=agent, manager=self._manager, retry_count=retry_count, # retry_count for the overall cycle attempt
            current_provider_name=agent.provider_name, current_model_name=agent.model,
            current_model_key_for_tracking=f"{agent.provider_name}/{agent.model}",
            max_retries_for_cycle=settings.MAX_STREAM_RETRIES,
            retry_delay_for_cycle=settings.RETRY_DELAY_SECONDS,
            current_db_session_id=self._manager.current_session_db_id
        )
        
        # Outer loop to handle priority rechecks by restarting the thinking process
        while True:
            logger.debug(f"CycleHandler '{agent.agent_id}': Starting/Restarting thinking process within run_cycle's main loop.")
            # Reset per-iteration flags in context (those not reset by CycleContext init or prepare_llm_call_data)
            context.last_error_obj = None
            context.last_error_content = None
            context.action_taken_this_cycle = False
            context.thought_produced_this_cycle = False
            context.state_change_requested_this_cycle = False
            context.executed_tool_successfully_this_cycle = False # Reset tool success for this iteration
            context.cycle_completed_successfully = False # Assume not successful until proven otherwise in this iteration
            context.needs_reactivation_after_cycle = False # Reset reactivation need for this iteration
            context.trigger_failover = False # Reset failover trigger

            # Ensure the list of failed models for *this current provider switch attempt* is managed correctly
            # This set is more about the current provider selection than the entire cycle handler attempt.
            if hasattr(agent, '_failed_models_this_cycle'):
                agent._failed_models_this_cycle.add(context.current_model_key_for_tracking)
            else:
                agent._failed_models_this_cycle = {context.current_model_key_for_tracking}

            agent_generator = None # Ensure generator is reset for each iteration of the while True loop

            try: # This try block is for one pass of LLM call and its event processing
                await self._prompt_assembler.prepare_llm_call_data(context) # Ensures history_for_call is fresh
                agent.set_status(AGENT_STATUS_PROCESSING)

                agent_generator = agent.process_message(history_override=context.history_for_call)

                llm_stream_ended_cleanly = True # Flag to see if the event loop finished or broke early
                async for event in agent_generator:
                    event_type = event.get("type")
                    logger.debug(f"CycleHandler '{agent.agent_id}': Received Event from Agent.process_message: Type='{event_type}', Keys={list(event.keys())}")

                    if event_type == "error":
                        context.last_error_obj = event.get('_exception_obj', ValueError(event.get('content', 'Unknown Agent Core Error')))
                        context.last_error_content = event.get("content", "[CycleHandler Error]: Unknown error from agent processing.")
                        # self._outcome_determiner.determine_cycle_outcome(context) # Moved to after recheck logic
                        if context.current_db_session_id:
                            await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_error", content=context.last_error_content)
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "workflow_executed":
                        context.action_taken_this_cycle = True
                        workflow_result_data = event.get("result_data")
                        logger.critical(f"CycleHandler '{agent.agent_id}': workflow_executed event data: {workflow_result_data}")
                        if not workflow_result_data or not isinstance(workflow_result_data, dict):
                            context.last_error_content = "Workflow execution event malformed (result_data missing or not a dict)."; context.last_error_obj = ValueError(context.last_error_content)
                            llm_stream_ended_cleanly = False; break
                        try: workflow_result = WorkflowResult(**workflow_result_data)
                        except Exception as pydantic_err:
                            context.last_error_content = f"Workflow result parsing error: {pydantic_err}"; context.last_error_obj = pydantic_err
                            llm_stream_ended_cleanly = False; break
                        logger.info(f"CycleHandler '{agent.agent_id}': Processing workflow result for '{workflow_result.workflow_name}'. Success: {workflow_result.success}")
                        if workflow_result.next_agent_state: self._manager.workflow_manager.change_state(agent, workflow_result.next_agent_state)
                        if workflow_result.next_agent_status: agent.set_status(workflow_result.next_agent_status)
                        if workflow_result.ui_message_data: await self._manager.send_to_ui(workflow_result.ui_message_data)

                        # START of inserted code block
                        if agent.agent_id == BOOTSTRAP_AGENT_ID and workflow_result.ui_message_data and workflow_result.ui_message_data.get('type') == 'project_pending_approval':
                            project_title = workflow_result.ui_message_data.get('project_title')
                            if project_title:
                                system_message_content = f"[Framework Notification: Project '{project_title}' has been created and is now awaiting user approval. You should inform the user about this status and wait for their approval. Do not re-plan this item.]"
                                framework_notification_message: MessageDict = {"role": "system", "content": system_message_content}
                                agent.message_history.append(framework_notification_message)
                                logger.info(f"CycleHandler '{agent.agent_id}': Injected project pending approval notification into history for project '{project_title}'.")
                                if context.current_db_session_id: # Log this important injection to DB as well
                                    try:
                                        await self._manager.db_manager.log_interaction(
                                            session_id=context.current_db_session_id,
                                            agent_id=agent.agent_id,
                                            role="system_framework_notification", # A new role to distinguish this
                                            content=system_message_content
                                        )
                                        logger.debug(f"CycleHandler '{agent.agent_id}': Logged framework notification to DB.")
                                    except Exception as db_log_err:
                                        logger.error(f"CycleHandler '{agent.agent_id}': Failed to log framework notification to DB: {db_log_err}", exc_info=True)
                        # END of inserted code block

                        if workflow_result.tasks_to_schedule:
                            for task_agent, task_retry_count in workflow_result.tasks_to_schedule:
                                if task_agent and isinstance(task_agent, Agent): await self._manager.schedule_cycle(task_agent, task_retry_count)
                                else: logger.warning(f"CycleHandler '{agent.agent_id}': Workflow '{workflow_result.workflow_name}' invalid agent schedule request.")
                        if workflow_result.success:
                            context.cycle_completed_successfully = True
                            context.needs_reactivation_after_cycle = not (workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule)) and \
                                                                bool(workflow_result.next_agent_state or workflow_result.tasks_to_schedule) # Complex: needs careful thought
                        else:
                            context.last_error_content = f"Workflow '{workflow_result.workflow_name}' failed: {workflow_result.message}"; context.last_error_obj = ValueError(context.last_error_content)
                            context.needs_reactivation_after_cycle = not (workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule)) and \
                                                                (not workflow_result.next_agent_state and workflow_result.next_agent_status != AGENT_STATUS_ERROR)
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "malformed_tool_call":
                        context.action_taken_this_cycle = True; raw_llm_response_with_error = event.get("raw_assistant_response")
                        # ... (logging, db interaction, feedback prep as before) ...
                        malformed_tool_name = event.get("tool_name"); parsing_error_msg = event.get("error_message")
                        logger.warning(f"Agent {agent.agent_id} produced malformed XML for tool '{malformed_tool_name}'. Error: {parsing_error_msg}")
                        if context.current_db_session_id and raw_llm_response_with_error: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id,agent_id=agent.agent_id,role="assistant",content=raw_llm_response_with_error)
                        detailed_tool_usage = "Could not retrieve detailed usage for this tool."
                        if malformed_tool_name and malformed_tool_name in self._manager.tool_executor.tools:
                            try: detailed_tool_usage = self._manager.tool_executor.tools[malformed_tool_name].get_detailed_usage()
                            except Exception as usage_exc: logger.error(f"Failed to get detailed usage for tool {malformed_tool_name}: {usage_exc}")
                        feedback_to_agent = (f"[Framework Feedback: XML Parsing Error]\nYour previous attempt to use the '{malformed_tool_name}' tool failed because the XML structure was malformed.\nError detail: {parsing_error_msg}\n\nPlease carefully review your XML syntax and ensure all tags are correctly opened, closed, and nested. Pay special attention to the content within tags, ensuring it's plain text and any special XML characters (like '<', '>', '&') are avoided or properly escaped if absolutely necessary.\n\nCorrect usage for the '{malformed_tool_name}' tool:\n{detailed_tool_usage}")
                        agent.message_history.append({"role": "system", "content": feedback_to_agent})
                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id,agent_id=agent.agent_id,role="system_error_feedback",content=feedback_to_agent)
                        await self._manager.send_to_ui({"type": "system_error_feedback","agent_id": agent.agent_id,"tool_name": malformed_tool_name,"error_message": parsing_error_msg,"detailed_usage": detailed_tool_usage,"original_attempt": raw_llm_response_with_error})
                        context.needs_reactivation_after_cycle = True; context.last_error_content = f"Malformed XML for tool '{malformed_tool_name}'"; context.cycle_completed_successfully = False
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "agent_thought":
                        context.action_taken_this_cycle = True; context.thought_produced_this_cycle = True
                        # ... (existing thought processing, KB saving) ...
                        thought_content = event.get("content") # Simplified for brevity here
                        if thought_content and context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant_thought", content=thought_content)
                        if thought_content: await self._manager.send_to_ui(event) # Save to KB logic omitted for diff brevity but would be here

                    elif event_type == "agent_state_change_requested":
                        context.action_taken_this_cycle = True; context.state_change_requested_this_cycle = True; requested_state = event.get("requested_state")
                        if self._manager.workflow_manager.change_state(agent, requested_state): context.needs_reactivation_after_cycle = True
                        else: context.needs_reactivation_after_cycle = True
                        # ... (db logging, UI send) ...
                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="agent_state_change", content=f"State changed to: {requested_state}")
                        await self._manager.send_to_ui(event)
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "tool_requests":
                        context.action_taken_this_cycle = True; tool_calls = event.get("calls", []); raw_assistant_response = event.get("raw_assistant_response")
                        # ... (append assistant message to history, db log) ...
                        if raw_assistant_response:
                            assistant_message_for_history: MessageDict = {"role": "assistant", "content": raw_assistant_response}
                            if tool_calls: assistant_message_for_history["tool_calls"] = tool_calls
                            agent.message_history.append(assistant_message_for_history)
                        if context.current_db_session_id and raw_assistant_response: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=raw_assistant_response, tool_calls=tool_calls)
                        
                        all_tool_results_for_history: List[MessageDict] = [] ; any_tool_success = False
                        for i, call_data in enumerate(tool_calls):
                            tool_name = call_data.get("name"); tool_id = call_data.get("id"); tool_args = call_data.get("arguments", {})
                            result_dict = await self._interaction_handler.execute_single_tool(agent, tool_id, tool_name, tool_args, self._manager.current_project, self._manager.current_session)
                            if result_dict:
                                history_item: MessageDict = {"role": "tool", "tool_call_id": result_dict.get("call_id", tool_id or f"unknown_id_{i}"), "name": result_dict.get("name", tool_name or f"unknown_tool_{i}"), "content": str(result_dict.get("content", "[Tool Error: No content]"))}
                                all_tool_results_for_history.append(history_item)
                                result_content_str = str(result_dict.get("content", ""))
                                if not result_content_str.lower().startswith(("[toolerror", "error:", "[toolexec error")): any_tool_success = True
                                # ... (db log tool result, UI send) ...
                                if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="tool", content=result_content_str, tool_results=[result_dict])
                                await self._manager.send_to_ui({**result_dict, "type": "tool_result", "agent_id": agent.agent_id, "tool_sequence": f"{i+1}_of_{len(tool_calls)}"})
                            else: all_tool_results_for_history.append({"role": "tool", "tool_call_id": tool_id or f"unknown_call_{i}", "name": tool_name or f"unknown_tool_{i}", "content": "[Tool Error: No result object]"})
                        for res_hist_item in all_tool_results_for_history: agent.message_history.append(res_hist_item)
                        context.executed_tool_successfully_this_cycle = any_tool_success; context.needs_reactivation_after_cycle = True
                        llm_stream_ended_cleanly = False; break

                    elif event_type in ["response_chunk", "status", "final_response", "invalid_state_request_output"]:
                        if event_type == "final_response":
                            final_content = event.get("content"); original_event_data = event
                            if final_content and agent.agent_id != CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                                cg_verdict = await self._get_cg_verdict(final_content)
                                if cg_verdict == "<OK/>":
                                    if context.current_db_session_id and (not agent.message_history or not agent.message_history[-1].get("tool_calls")): await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content)
                                    await self._manager.send_to_ui(original_event_data)
                                else: # CG Concern
                                    agent.cg_original_text = final_content; agent.cg_concern_details = cg_verdict; agent.cg_original_event_data = original_event_data
                                    agent.cg_awaiting_user_decision = True; agent.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG)
                                    await self._manager.send_to_ui({"type": "cg_concern", "agent_id": agent.agent_id, "original_text": final_content, "concern_details": cg_verdict})
                                    context.action_taken_this_cycle = True; context.needs_reactivation_after_cycle = False
                                    llm_stream_ended_cleanly = False; break
                            else: # No content or CG agent itself
                                if context.current_db_session_id and final_content and (not agent.message_history or not agent.message_history[-1].get("tool_calls")): await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content)
                                await self._manager.send_to_ui(original_event_data)
                        elif event_type == "invalid_state_request_output": # ... (db log, UI send) ...
                            context.action_taken_this_cycle = True; context.needs_reactivation_after_cycle = True
                            if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_warning", content=f"Agent attempted invalid state change: {event.get('content')}")
                            await self._manager.send_to_ui(event)
                        else: await self._manager.send_to_ui(event) # response_chunk, status

                    elif event_type == "pm_startup_missing_task_list_after_think":
                        # ... (feedback prep, append to history, db log) ...
                        feedback_content = ("[Framework Feedback for PM Retry]\nYour previous output consisted only of a <think> block. In the PM_STATE_STARTUP, you must provide the <task_list> XML structure after your thoughts. Please ensure your entire response includes the XML task list as specified in your instructions.")
                        agent.message_history.append({"role": "system", "content": feedback_content})
                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_feedback", content=feedback_content)
                        context.action_taken_this_cycle = True; context.cycle_completed_successfully = False; context.needs_reactivation_after_cycle = True
                        context.last_error_content = "PM startup missing task list after think."
                        await self._manager.send_to_ui({**event, "feedback_provided": True})
                        llm_stream_ended_cleanly = False; break
                    else: logger.warning(f"CycleHandler: Unknown event type '{event_type}' from agent '{agent.agent_id}'.")

                # This block handles cases where the LLM stream finished without any specific break-worthy event.
                if llm_stream_ended_cleanly and not context.last_error_obj and not context.action_taken_this_cycle:
                    # Intervention logic for PM agent stuck after team creation
                    if agent.agent_type == AGENT_TYPE_PM and \
                       agent.state == PM_STATE_BUILD_TEAM_TASKS and \
                       not agent.intervention_applied_for_build_team_tasks:

                        team_created_successfully = False
                        created_team_id_for_message = "team_NameNotRetrieved" # Default
                        if agent.message_history:
                            for i in range(len(agent.message_history) -1, -1, -1):
                                msg = agent.message_history[i]
                                if msg.get("role") == "tool" and msg.get("name") == "manage_team":
                                    tool_content = msg.get("content", "")
                                    if "create_team" in tool_content.lower() and \
                                       ("successfully" in tool_content.lower() or "created" in tool_content.lower()):
                                        team_created_successfully = True
                                        match = re.search(r'\"created_team_id\":\s*\"([^\"]+)\"', tool_content)
                                        if match:
                                            created_team_id_for_message = match.group(1)
                                        else:
                                            if i > 0 and agent.message_history[i-1].get("role") == "assistant":
                                                prev_msg_tool_calls = agent.message_history[i-1].get("tool_calls")
                                                if prev_msg_tool_calls and isinstance(prev_msg_tool_calls, list):
                                                    for call in prev_msg_tool_calls:
                                                        if call.get("name") == "manage_team" and call.get("arguments", {}).get("action") == "create_team":
                                                            created_team_id_for_message = call.get("arguments", {}).get("team_id", created_team_id_for_message)
                                                            break
                                        break
                                if msg.get("role") == "assistant":
                                    break

                        if team_created_successfully:
                            logger.info(f"CycleHandler: PM agent '{agent.agent_id}' in state '{agent.state}' returned empty. Applying intervention after successful team creation.")

                            intervention_message_content = (
                                f"[Framework Intervention]: Team '{created_team_id_for_message}' is now created. "
                                "Your mandatory next action is to list available tools to proceed with agent creation. "
                                "Output ONLY the following XML: <tool_information><action>list_tools</action></tool_information>"
                            )
                            intervention_message: MessageDict = {"role": "system", "content": intervention_message_content}
                            agent.message_history.append(intervention_message)

                            if context.current_db_session_id:
                                await self._manager.db_manager.log_interaction(
                                    session_id=context.current_db_session_id,
                                    agent_id=agent.agent_id,
                                    role="system_intervention",
                                    content=intervention_message_content
                                )

                            agent.intervention_applied_for_build_team_tasks = True
                            context.needs_reactivation_after_cycle = True
                            context.action_taken_this_cycle = True
                            context.cycle_completed_successfully = True

                    # Original logic for processing final_content_from_buffer starts here
                    if context.needs_reactivation_after_cycle and agent.intervention_applied_for_build_team_tasks and not agent.text_buffer.strip():
                        pass
                    else:
                        final_content_from_buffer = agent.text_buffer.strip()
                        if final_content_from_buffer:
                            agent.text_buffer = ""; mock_event_data = {"type": "final_response", "content": final_content_from_buffer, "agent_id": agent.agent_id}
                            context.action_taken_this_cycle = True
                            if agent.agent_id != CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                                cg_verdict = await self._get_cg_verdict(final_content_from_buffer)
                                if cg_verdict == "<OK/>":
                                    if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content_from_buffer)
                                    await self._manager.send_to_ui(mock_event_data)
                                    context.cycle_completed_successfully = True
                                else:
                                    agent.cg_original_text = final_content_from_buffer; agent.cg_concern_details = cg_verdict; agent.cg_original_event_data = mock_event_data
                                    agent.cg_awaiting_user_decision = True; agent.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG)
                                    await self._manager.send_to_ui({"type": "cg_concern", "agent_id": agent.agent_id, "original_text": final_content_from_buffer, "concern_details": cg_verdict})
                                    context.needs_reactivation_after_cycle = False
                                    context.cycle_completed_successfully = False
                            else:
                                if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content_from_buffer)
                                await self._manager.send_to_ui(mock_event_data)
                                context.cycle_completed_successfully = True
                        else:
                            logger.info(f"Agent '{agent.agent_id}' cycle resulted in no errors, no actions, and no text_buffer content. Cycle considered complete but no output.")
                            context.cycle_completed_successfully = True

                # Determine if this iteration of LLM call was successful before recheck
                if not context.last_error_obj and context.action_taken_this_cycle:
                    context.cycle_completed_successfully = True # Default to true if action taken and no error yet
                elif not context.last_error_obj and not context.action_taken_this_cycle and llm_stream_ended_cleanly: # No action, no error, stream finished
                    context.cycle_completed_successfully = True


                # --- PRIORITY RECHECK POINT ---
                if agent.needs_priority_recheck:
                    agent.needs_priority_recheck = False # Reset the flag
                    logger.info(f"CycleHandler: Agent {agent.agent_id} ({agent.persona}) performing priority recheck after LLM output due to new message.")
                    if context.current_db_session_id:
                        await self._manager.db_manager.log_interaction(
                            session_id=context.current_db_session_id, agent_id=agent.agent_id,
                            role="system_internal", content="Priority recheck triggered. Restarting agent's thinking process."
                        )
                    # context flags are reset at the start of the while True loop.
                    # History will be re-prepared by prepare_llm_call_data.
                    if agent_generator: await agent_generator.aclose(); agent_generator = None # Close current generator
                    continue # Restart the outer `while True` loop to re-run agent.process_message

                # If no recheck, then this iteration of the LLM call is done. Break from while True.
                break # Exit while True loop, proceed to outer finally for outcome determination and scheduling.

            except Exception as e: # Handles exceptions from _prompt_assembler or agent.process_message setup
                logger.critical(f"CycleHandler: UNHANDLED EXCEPTION during agent '{agent.agent_id}' cycle setup or early processing: {e}", exc_info=True)
                context.last_error_obj = e
                context.last_error_content = f"[CycleHandler CRITICAL]: Unhandled exception - {type(e).__name__}"
                context.trigger_failover = True
                if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_error", content=context.last_error_content)
                await self._manager.send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": context.last_error_content})
                break # Exit while True loop, proceed to outer finally
            finally:
                if agent_generator: # Ensure generator from this iteration is closed if it was opened
                    try:
                        if agent_generator.ag_running: await agent_generator.aclose()
                        elif not agent_generator.ag_running and agent_generator.ag_frame is not None : await agent_generator.aclose() # Already closed or never started properly
                    except Exception as close_err: logger.warning(f"Error closing agent generator for '{agent.agent_id}' in inner finally: {close_err}", exc_info=True)
        
        # --- This is the original finally block of run_cycle ---
        # It runs AFTER the `while True` loop (and its inner try/except/finally) has exited.
        context.llm_call_duration_ms = (time.perf_counter() - context.start_time) * 1000 # Measure total time including rechecks for now

        # Determine final outcome of the cycle (potentially after rechecks)
        # The context.cycle_completed_successfully, context.last_error_obj etc. should reflect the *last* attempt if rechecked.
        self._outcome_determiner.determine_cycle_outcome(context)

        if not context.is_provider_level_error:
            success_for_metrics = context.cycle_completed_successfully and not context.is_key_related_error
            await self._manager.performance_tracker.record_call(
                provider=context.current_provider_name or "unknown", model_id=context.current_model_name or "unknown",
                duration_ms=context.llm_call_duration_ms, success=success_for_metrics
            )

        await self._next_step_scheduler.schedule_next_step(context)
        logger.info(f"CycleHandler: Finished cycle logic for Agent '{agent.agent_id}'. Final status for this attempt: {agent.status}. State: {agent.state}")
