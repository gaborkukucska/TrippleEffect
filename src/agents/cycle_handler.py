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
        """
        Directly calls the Constitutional Guardian agent's LLM provider to get a verdict.
        This method bypasses the usual agent scheduling and cycle handling for the CG agent
        to get an immediate verdict.
        """
        if not original_agent_final_text or original_agent_final_text.isspace():
            logger.warning("CG review requested for empty or whitespace-only text. Skipping LLM call and returning <OK/>.")
            return "<OK/>"

        cg_agent = self._manager.agents.get(CONSTITUTIONAL_GUARDIAN_AGENT_ID)

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
            logger.error("AgentManager instance not found or lacks push_agent_status_update, cannot update CG status for UI.")

        # Format governance principles text
        text_parts = []
        if hasattr(settings, 'GOVERNANCE_PRINCIPLES') and settings.GOVERNANCE_PRINCIPLES:
            for principle in settings.GOVERNANCE_PRINCIPLES:
                if principle.get("enabled", False):
                    text_parts.append(f"Principle: {principle.get('name', 'N/A')} (ID: {principle.get('id', 'N/A')})\n{principle.get('text', 'N/A')}")
        
        governance_text = "\n\n---\n\n".join(text_parts) if text_parts else "No specific governance principles provided."

        cg_prompt_template = settings.PROMPTS.get("cg_system_prompt", "")
        if not cg_prompt_template:
            logger.error("System prompt for Constitutional Guardian (cg_system_prompt) not found. Failing open (assuming <OK/>).")
            return "<OK/>"

        formatted_cg_system_prompt = cg_prompt_template.format(governance_principles_text=governance_text)
        
        cg_history: List[MessageDict] = [
            {"role": "system", "content": formatted_cg_system_prompt},
            {"role": "system", "content": f"---\nText for Constitutional Review:\n---\n{original_agent_final_text}"}
        ]

        max_tokens_for_verdict = 250 # Verdicts should be concise

        try:
            logger.info(f"Requesting CG verdict via stream_completion for text: '{original_agent_final_text[:100]}...'")
            provider_stream = cg_agent.llm_provider.stream_completion(
                messages=cg_history,
                model=cg_agent.model,
                temperature=cg_agent.temperature,
                max_tokens=max_tokens_for_verdict
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

            # Define constants for parsing
            OK_TAG = "<OK/>"
            CONCERN_START_TAG = "<CONCERN>"
            CONCERN_END_TAG = "</CONCERN>"
            MALFORMED_CONCERN_MSG = "Constitutional Guardian expressed a concern, but the format was malformed."
            MALFORMED_INCONCLUSIVE_MSG = "Constitutional Guardian returned a malformed or inconclusive verdict."
            ERROR_PROCESSING_MSG = "Constitutional Guardian encountered an error during verdict processing."

            IMPLICIT_OK_PHRASES = [
                "no constitutional content", "no issues found",
                "seems to be a friendly greeting",
                "no substantial text related to constitutional matters", "fully complies"
            ]

            # 1. Explicit <OK/> check
            if OK_TAG in stripped_verdict:
                # Ambiguity check: if <OK/> is present, but "concern" keyword also appears, it's problematic.
                if "concern" in stripped_verdict.lower():
                    logger.warning(f"Ambiguous CG response: Contained '{OK_TAG}' but also the word 'concern'. Full response: '{stripped_verdict}'. Treating as malformed concern.")
                    return MALFORMED_CONCERN_MSG
                logger.info(f"CG verdict contains explicit '{OK_TAG}'. Parsing as OK.")
                return OK_TAG

            # 2. Well-formed <CONCERN>details</CONCERN>
            concern_start_index = stripped_verdict.find(CONCERN_START_TAG)
            concern_end_index = -1
            if concern_start_index != -1:
                concern_end_index = stripped_verdict.find(CONCERN_END_TAG, concern_start_index + len(CONCERN_START_TAG))

            if concern_start_index != -1 and concern_end_index != -1:
                concern_detail = stripped_verdict[concern_start_index + len(CONCERN_START_TAG):concern_end_index].strip()
                if concern_detail:
                    logger.info(f"Extracted well-formed concern detail: '{concern_detail}'")
                    return f"{CONCERN_START_TAG}{concern_detail}{CONCERN_END_TAG}"
                else: # Tags present, but empty content
                    logger.warning(f"CG verdict has '{CONCERN_START_TAG}...{CONCERN_END_TAG}' tags but the content is empty. Treating as malformed concern.")
                    return MALFORMED_CONCERN_MSG

            # 3. Malformed or Keyword-based Concern
            # This includes <CONCERN> without </CONCERN> or just the word "concern"
            has_concern_start_tag_only = (concern_start_index != -1 and concern_end_index == -1)
            contains_concern_keyword = "concern" in stripped_verdict.lower()

            # If it has a start tag only, OR it contains "concern" keyword AND wasn't a well-formed concern already handled
            if has_concern_start_tag_only or contains_concern_keyword:
                logger.warning(f"CG verdict indicates a concern but is malformed or keyword-based. Has start tag only: {has_concern_start_tag_only}. Contains 'concern' keyword: {contains_concern_keyword}. Original: '{stripped_verdict}'")
                return MALFORMED_CONCERN_MSG

            # 4. Implicit OK (Only if no explicit OK and NO concern signals at all were found above)
            if stripped_verdict: # Must be non-empty
                # This check is done after concern checks to ensure no concern signal was present
                for phrase in IMPLICIT_OK_PHRASES:
                    if phrase in stripped_verdict.lower():
                        logger.info(f"Implicit OK detected due to positive sentiment ('{phrase}') and lack of any concern signals. Verdict: '{stripped_verdict}'")
                        return OK_TAG

            # 5. Fallback to Malformed/Inconclusive
            if not stripped_verdict:
                logger.warning("CG returned an empty or whitespace-only verdict. Treating as malformed/inconclusive.")
            else:
                logger.warning(f"CG verdict '{stripped_verdict}' does not meet any specific OK or Concern criteria. Treating as malformed/inconclusive.")
            return MALFORMED_INCONCLUSIVE_MSG

        try:
            try:
                logger.info(f"Requesting CG verdict via stream_completion for text: '{original_agent_final_text[:100]}...'")
                provider_stream = cg_agent.llm_provider.stream_completion(
                    messages=cg_history,
                    model=cg_agent.model,
                    temperature=cg_agent.temperature,
                    max_tokens=max_tokens_for_verdict
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

                # Define constants for parsing
                OK_TAG = "<OK/>"
                CONCERN_START_TAG = "<CONCERN>"
                CONCERN_END_TAG = "</CONCERN>"
                MALFORMED_CONCERN_MSG = "Constitutional Guardian expressed a concern, but the format was malformed."
                MALFORMED_INCONCLUSIVE_MSG = "Constitutional Guardian returned a malformed or inconclusive verdict."
                ERROR_PROCESSING_MSG = "Constitutional Guardian encountered an error during verdict processing."

                IMPLICIT_OK_PHRASES = [
                    "no constitutional content", "no issues found",
                    "seems to be a friendly greeting",
                    "no substantial text related to constitutional matters", "fully complies"
                ]

                # 1. Explicit <OK/> check
                if OK_TAG in stripped_verdict:
                    # Ambiguity check: if <OK/> is present, but "concern" keyword also appears, it's problematic.
                    if "concern" in stripped_verdict.lower():
                        logger.warning(f"Ambiguous CG response: Contained '{OK_TAG}' but also the word 'concern'. Full response: '{stripped_verdict}'. Treating as malformed concern.")
                        return MALFORMED_CONCERN_MSG
                    logger.info(f"CG verdict contains explicit '{OK_TAG}'. Parsing as OK.")
                    return OK_TAG

                # 2. Well-formed <CONCERN>details</CONCERN>
                concern_start_index = stripped_verdict.find(CONCERN_START_TAG)
                concern_end_index = -1
                if concern_start_index != -1:
                    concern_end_index = stripped_verdict.find(CONCERN_END_TAG, concern_start_index + len(CONCERN_START_TAG))

                if concern_start_index != -1 and concern_end_index != -1:
                    concern_detail = stripped_verdict[concern_start_index + len(CONCERN_START_TAG):concern_end_index].strip()
                    if concern_detail:
                        logger.info(f"Extracted well-formed concern detail: '{concern_detail}'")
                        return f"{CONCERN_START_TAG}{concern_detail}{CONCERN_END_TAG}"
                    else: # Tags present, but empty content
                        logger.warning(f"CG verdict has '{CONCERN_START_TAG}...{CONCERN_END_TAG}' tags but the content is empty. Treating as malformed concern.")
                        return MALFORMED_CONCERN_MSG

                # 3. Malformed or Keyword-based Concern
                # This includes <CONCERN> without </CONCERN> or just the word "concern"
                has_concern_start_tag_only = (concern_start_index != -1 and concern_end_index == -1)
                contains_concern_keyword = "concern" in stripped_verdict.lower()

                # If it has a start tag only, OR it contains "concern" keyword AND wasn't a well-formed concern already handled
                if has_concern_start_tag_only or contains_concern_keyword:
                    logger.warning(f"CG verdict indicates a concern but is malformed or keyword-based. Has start tag only: {has_concern_start_tag_only}. Contains 'concern' keyword: {contains_concern_keyword}. Original: '{stripped_verdict}'")
                    return MALFORMED_CONCERN_MSG

                # 4. Implicit OK (Only if no explicit OK and NO concern signals at all were found above)
                if stripped_verdict: # Must be non-empty
                    # This check is done after concern checks to ensure no concern signal was present
                    for phrase in IMPLICIT_OK_PHRASES:
                        if phrase in stripped_verdict.lower():
                            logger.info(f"Implicit OK detected due to positive sentiment ('{phrase}') and lack of any concern signals. Verdict: '{stripped_verdict}'")
                            return OK_TAG

                # 5. Fallback to Malformed/Inconclusive
                if not stripped_verdict:
                    logger.warning("CG returned an empty or whitespace-only verdict. Treating as malformed/inconclusive.")
                else:
                    logger.warning(f"CG verdict '{stripped_verdict}' does not meet any specific OK or Concern criteria. Treating as malformed/inconclusive.")
                return MALFORMED_INCONCLUSIVE_MSG

            except Exception as e:
                logger.error(f"Error during Constitutional Guardian LLM call or verdict parsing: {e}", exc_info=True)
                return ERROR_PROCESSING_MSG
        finally:
            if cg_agent: # Check if cg_agent was valid from the start of the method
                final_status_to_set = original_cg_status if original_cg_status is not None else AGENT_STATUS_IDLE
                cg_agent.set_status(final_status_to_set)
                if hasattr(self._manager, 'push_agent_status_update'):
                    await self._manager.push_agent_status_update(CONSTITUTIONAL_GUARDIAN_AGENT_ID)
                else:
                    logger.error("AgentManager instance not found or lacks push_agent_status_update, cannot revert CG status for UI.")

    # Removed _request_cg_review method as its functionality is integrated into _get_cg_verdict and run_cycle

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
                    
                    logger.critical(f"CycleHandler '{agent.agent_id}': workflow_executed event data: {workflow_result_data}")

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
                         if workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule):
                             context.needs_reactivation_after_cycle = False
                         elif not workflow_result.next_agent_state and not workflow_result.tasks_to_schedule:
                             context.needs_reactivation_after_cycle = False 
                             logger.debug(f"CycleHandler: Successful workflow '{workflow_result.workflow_name}' for agent '{agent.agent_id}' completed. No explicit reschedule of current agent. Agent goes idle in new/current state.")
                         else:
                             context.needs_reactivation_after_cycle = False
                    else: 
                         context.last_error_content = f"Workflow '{workflow_result.workflow_name}' failed: {workflow_result.message}"
                         context.last_error_obj = ValueError(context.last_error_content)
                         if workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule):
                             context.needs_reactivation_after_cycle = False 
                         elif not workflow_result.tasks_to_schedule and not workflow_result.next_agent_state and workflow_result.next_agent_status != AGENT_STATUS_ERROR:
                             logger.info(f"CycleHandler: Workflow '{workflow_result.workflow_name}' failed for agent '{agent.agent_id}' but did not specify next steps or error status. Marking for reactivation to potentially retry/correct.")
                             context.needs_reactivation_after_cycle = True
                         else:
                             context.needs_reactivation_after_cycle = False 
                    break 

                elif event_type == "malformed_tool_call":
                    context.action_taken_this_cycle = True
                    malformed_tool_name = event.get("tool_name")
                    parsing_error_msg = event.get("error_message")
                    # malformed_xml_block = event.get("malformed_xml_block") # Available if needed for logging
                    raw_llm_response_with_error = event.get("raw_assistant_response")

                    logger.warning(f"Agent {agent.agent_id} produced malformed XML for tool '{malformed_tool_name}'. Error: {parsing_error_msg}")

                    # Log the original assistant message that contained the error
                    if context.current_db_session_id and raw_llm_response_with_error:
                        await self._manager.db_manager.log_interaction(
                            session_id=context.current_db_session_id,
                            agent_id=agent.agent_id,
                            role="assistant", # Log it as what the assistant tried to say
                            content=raw_llm_response_with_error,
                            # No tool_calls since it was malformed
                        )

                    detailed_tool_usage = "Could not retrieve detailed usage for this tool."
                    if malformed_tool_name and malformed_tool_name in self._manager.tool_executor.tools:
                        try:
                            detailed_tool_usage = self._manager.tool_executor.tools[malformed_tool_name].get_detailed_usage()
                        except Exception as usage_exc:
                            logger.error(f"Failed to get detailed usage for tool {malformed_tool_name}: {usage_exc}")

                    feedback_to_agent = (
                        f"[Framework Feedback: XML Parsing Error]\n"
                        f"Your previous attempt to use the '{malformed_tool_name}' tool failed because the XML structure was malformed.\n"
                        f"Error detail: {parsing_error_msg}\n\n"
                        f"Please carefully review your XML syntax and ensure all tags are correctly opened, closed, and nested. "
                        f"Pay special attention to the content within tags, ensuring it's plain text and any special XML characters (like '<', '>', '&') are avoided or properly escaped if absolutely necessary.\n\n"
                        f"Correct usage for the '{malformed_tool_name}' tool:\n{detailed_tool_usage}"
                    )

                    agent.message_history.append({"role": "system", "content": feedback_to_agent})
                    if context.current_db_session_id:
                        await self._manager.db_manager.log_interaction(
                            session_id=context.current_db_session_id,
                            agent_id=agent.agent_id,
                            role="system_error_feedback",
                            content=feedback_to_agent
                        )

                    await self._manager.send_to_ui({
                        "type": "system_error_feedback",
                        "agent_id": agent.agent_id,
                        "tool_name": malformed_tool_name,
                        "error_message": parsing_error_msg,
                        "detailed_usage": detailed_tool_usage,
                        "original_attempt": raw_llm_response_with_error
                    })

                    context.needs_reactivation_after_cycle = True
                    context.last_error_content = f"Malformed XML for tool '{malformed_tool_name}'"
                    context.cycle_completed_successfully = False
                    break

                elif event_type == "agent_thought":
                    context.action_taken_this_cycle = True; context.thought_produced_this_cycle = True
                    thought_content = event.get("content")
                    if not thought_content:
                        logger.warning(f"Agent {agent.agent_id} produced an empty thought. Skipping KB save.")
                    else:
                        if context.current_db_session_id:
                            await self._manager.db_manager.log_interaction(
                                session_id=context.current_db_session_id,
                                agent_id=agent.agent_id,
                                role="assistant_thought",
                                content=thought_content
                            )
                        await self._manager.send_to_ui(event)
                        try:
                            extracted_keywords_list = extract_keywords_from_text(thought_content, max_keywords=5)
                            default_keywords = ["agent_thought", agent.agent_id]
                            if agent.persona: default_keywords.append(agent.persona.lower().replace(" ", "_"))
                            if agent.agent_type: default_keywords.append(agent.agent_type.lower())
                            final_keywords_list = [kw for kw in list(set(default_keywords + extracted_keywords_list)) if kw and kw.strip()]
                            final_keywords_str = ",".join(final_keywords_list)
                            logger.info(f"Agent {agent.agent_id} saving thought to KB. Keywords: '{final_keywords_str}'")
                            kb_tool_args = {
                                "action": "save_knowledge",
                                "content": thought_content,
                                "keywords": final_keywords_str,
                                "title": f"Agent Thought by {agent.agent_id} ({agent.persona}) at {time.strftime('%Y-%m-%d %H:%M:%S')}"
                            }
                            if hasattr(self._manager.tool_executor, 'execute_tool') and hasattr(KnowledgeBaseTool, 'name'):
                                kb_save_result = await self._manager.tool_executor.execute_tool(
                                    agent_id=agent.agent_id, 
                                    agent_sandbox_path=agent.sandbox_path,
                                    tool_name=KnowledgeBaseTool.name,
                                    tool_args=kb_tool_args,
                                    project_name=self._manager.current_project,
                                    session_name=self._manager.current_session,
                                    manager=self._manager
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
                    
                    all_tool_results_for_history: List[MessageDict] = [] 
                    any_tool_success = False
                    
                    if len(tool_calls) > 1:
                        logger.info(f"CycleHandler '{agent.agent_id}': Processing {len(tool_calls)} tool calls sequentially.")

                    for i, call_data in enumerate(tool_calls):
                        tool_name = call_data.get("name")
                        tool_id = call_data.get("id")
                        tool_args = call_data.get("arguments", {})
                        
                        logger.info(f"CycleHandler '{agent.agent_id}': Executing tool {i+1}/{len(tool_calls)}: Name='{tool_name}', ID='{tool_id}'")
                        
                        result_dict = await self._interaction_handler.execute_single_tool(
                            agent, tool_id, tool_name, tool_args, 
                            self._manager.current_project, self._manager.current_session
                        )
                        
                        if result_dict:
                            history_item: MessageDict = { 
                                "role": "tool",
                                "tool_call_id": result_dict.get("call_id", tool_id or "unknown_id_in_loop"),
                                "name": result_dict.get("name", tool_name or "unknown_tool_in_loop"), 
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
                            if context.current_db_session_id:
                                await self._manager.db_manager.log_interaction(
                                    session_id=context.current_db_session_id, agent_id=agent.agent_id, role="tool",
                                    content=result_content_str, tool_results=[result_dict]
                                )
                            await self._manager.send_to_ui({
                                **result_dict, "type": "tool_result", "agent_id": agent.agent_id,
                                "tool_sequence": f"{i+1}_of_{len(tool_calls)}"
                            })
                        else:
                            logger.error(f"CycleHandler '{agent.agent_id}': Tool '{tool_name}' (ID: {tool_id}) execution returned None.")
                            history_item: MessageDict = {
                                "role": "tool", "tool_call_id": tool_id or f"unknown_call_{i}", 
                                "name": tool_name or f"unknown_tool_{i}", 
                                "content": "[Tool Error: Execution returned no result object]"
                            }
                            all_tool_results_for_history.append(history_item)
                    
                    for res_hist_item in all_tool_results_for_history:
                        agent.message_history.append(res_hist_item)
                    
                    context.executed_tool_successfully_this_cycle = any_tool_success
                    context.needs_reactivation_after_cycle = True 
                    logger.info(f"CycleHandler '{agent.agent_id}': Finished processing {len(tool_calls)} tool calls. Any success: {any_tool_success}. Needs reactivation.")
                    break 

                elif event_type in ["response_chunk", "status", "final_response", "invalid_state_request_output"]:
                    if event_type == "final_response":
                        final_content = event.get("content")
                        original_event_data = event # Store original event

                        if final_content and agent.agent_id != CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                            cg_verdict = await self._get_cg_verdict(final_content)

                            if cg_verdict == "<OK/>": # Explicitly check for <OK/>
                                logger.info(f"CG Verdict OK for agent '{agent.agent_id}'. Proceeding normally.")
                                if context.current_db_session_id:
                                    if not agent.message_history or not agent.message_history[-1].get("tool_calls"):
                                        await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content)
                                await self._manager.send_to_ui(original_event_data)
                            else: # Any other string is a concern (either extracted detail or "Malformed..." string)
                                logger.info(f"CG CONCERN for agent '{agent.agent_id}'. Pausing agent and notifying UI.")
                                concern_details = cg_verdict # This is now the extracted concern string or malformed message
                                agent.cg_original_text = final_content
                                agent.cg_concern_details = concern_details
                                agent.cg_original_event_data = original_event_data
                                agent.cg_awaiting_user_decision = True
                                agent.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG)
                                await self._manager.send_to_ui({
                                    "type": "cg_concern",
                                    "agent_id": agent.agent_id,
                                    "original_text": final_content,
                                    "concern_details": concern_details
                                })
                                context.action_taken_this_cycle = True
                                context.needs_reactivation_after_cycle = False
                                break
                        else: # No content in final_response, or it's the CG agent itself
                            if context.current_db_session_id and final_content: 
                                if not agent.message_history or not agent.message_history[-1].get("tool_calls"):
                                    await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content)
                            await self._manager.send_to_ui(original_event_data)
                    elif event_type == "invalid_state_request_output":
                        context.action_taken_this_cycle = True
                        context.needs_reactivation_after_cycle = True 
                        if context.current_db_session_id:
                            await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_warning", content=f"Agent attempted invalid state change: {event.get('content')}")
                        await self._manager.send_to_ui(event)
                    else: # For "response_chunk", "status"
                        await self._manager.send_to_ui(event)

                elif event_type == "pm_startup_missing_task_list_after_think":
                    logger.warning(
                        f"CycleHandler '{agent.agent_id}': PM startup missing task list after think. "
                        f"Agent ID: {event.get('agent_id')}. Forcing retry with feedback."
                    )
                    # Append feedback to PM's message history
                    feedback_content = (
                        "[Framework Feedback for PM Retry]\n"
                        "Your previous output consisted only of a <think> block. "
                        "In the PM_STATE_STARTUP, you must provide the <task_list> XML structure after your thoughts. "
                        "Please ensure your entire response includes the XML task list as specified in your instructions."
                    )
                    agent.message_history.append({"role": "system", "content": feedback_content})
                    if context.current_db_session_id:
                        await self._manager.db_manager.log_interaction(
                            session_id=context.current_db_session_id,
                            agent_id=agent.agent_id,
                            role="system_feedback",
                            content=feedback_content
                        )

                    context.action_taken_this_cycle = True # An action (feedback) was taken
                    context.cycle_completed_successfully = False # Cycle did not achieve its goal
                    context.needs_reactivation_after_cycle = True # Force reschedule
                    # No specific error object, but cycle was not successful in its primary aim
                    context.last_error_content = "PM startup missing task list after think."
                    await self._manager.send_to_ui({**event, "feedback_provided": True}) # Notify UI if helpful
                    break # Stop processing further events in this cycle
                else:
                    logger.warning(f"CycleHandler: Unknown event type '{event_type}' from agent '{agent.agent_id}'.")
            
            if not context.last_error_obj and not context.action_taken_this_cycle: 
                final_content_from_buffer = agent.text_buffer.strip()
                if final_content_from_buffer:
                    agent.text_buffer = "" 
                    mock_event_data = {"type": "final_response", "content": final_content_from_buffer, "agent_id": agent.agent_id}

                    if agent.agent_id != CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                        cg_verdict = await self._get_cg_verdict(final_content_from_buffer)

                        if cg_verdict == "<OK/>": # Explicitly check for <OK/>
                            logger.info(f"CG Verdict OK for agent '{agent.agent_id}' (from buffer). Proceeding normally.")
                            if context.current_db_session_id:
                                await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content_from_buffer)
                            await self._manager.send_to_ui(mock_event_data)
                        else: # Any other string is a concern
                            logger.info(f"CG CONCERN for agent '{agent.agent_id}' (from buffer). Pausing agent and notifying UI.")
                            concern_details = cg_verdict
                            agent.cg_original_text = final_content_from_buffer
                            agent.cg_concern_details = concern_details
                            agent.cg_original_event_data = mock_event_data
                            agent.cg_awaiting_user_decision = True
                            agent.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG)
                            await self._manager.send_to_ui({
                                "type": "cg_concern",
                                "agent_id": agent.agent_id,
                                "original_text": final_content_from_buffer,
                                "concern_details": concern_details
                            })
                            context.action_taken_this_cycle = True
                            context.needs_reactivation_after_cycle = False
                    else: 
                        if final_content_from_buffer: 
                            if context.current_db_session_id:
                                await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content_from_buffer)
                            await self._manager.send_to_ui(mock_event_data)
                else:
                    logger.info(f"Agent '{agent.agent_id}' cycle resulted in no errors, no actions, and no text_buffer content.")

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
                    if not agent_generator.ag_running and agent_generator.ag_frame is not None:
                        await agent_generator.aclose()
                    elif agent_generator.ag_running:
                        logger.warning(f"Agent generator for '{agent.agent_id}' was still running in finally block, attempting to close.")
                        await agent_generator.aclose()
                except RuntimeError as r_err: 
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
