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
    PM_STATE_STARTUP, PM_STATE_MANAGE, PM_STATE_WORK, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS, PM_STATE_STANDBY,
    WORKER_STATE_WAIT, REQUEST_STATE_TAG_PATTERN,
    CONSTITUTIONAL_GUARDIAN_AGENT_ID, # Added for CG
    BOOTSTRAP_AGENT_ID
)

# Import for keyword extraction
from src.utils.text_utils import extract_keywords_from_text
from src.tools.knowledge_base import KnowledgeBaseTool # Assuming this is the correct tool name

from src.agents.cycle_components import (
    CycleContext,
    PromptAssembler,
    LLMCaller, 
    CycleOutcomeDeterminer,
    NextStepScheduler,
    AgentHealthMonitor
)
from src.agents.cycle_components.xml_validator import XMLValidator
from src.agents.cycle_components.context_summarizer import ContextSummarizer

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
        self._xml_validator = XMLValidator()
        self._context_summarizer = ContextSummarizer(self._manager)
        self._health_monitor = AgentHealthMonitor(self._manager)
        
        self.request_state_pattern = REQUEST_STATE_TAG_PATTERN 
        self._tool_execution_stats = {"total_calls": 0, "successful_calls": 0, "failed_calls": 0}
        logger.info("AgentCycleHandler initialized with enhanced tool execution monitoring, XML validation, context summarization, and agent health monitoring.")

    def _report_tool_execution_stats(self):
        """Report current tool execution statistics"""
        if self._tool_execution_stats["total_calls"] > 0:
            success_rate = (self._tool_execution_stats["successful_calls"] / self._tool_execution_stats["total_calls"]) * 100
            logger.info(f"CycleHandler Tool Stats - Total: {self._tool_execution_stats['total_calls']}, "
                       f"Successful: {self._tool_execution_stats['successful_calls']}, "
                       f"Failed: {self._tool_execution_stats['failed_calls']}, "
                       f"Success Rate: {success_rate:.1f}%")
        
        # Also report ToolExecutor stats if available
        if hasattr(self._manager, 'tool_executor') and hasattr(self._manager.tool_executor, 'report_execution_stats'):
            executor_stats = self._manager.tool_executor.report_execution_stats()
            return executor_stats
        return None

    def _detect_potential_tool_calls(self, text: str) -> bool:
        """
        Enhanced detection for potential tool calls that failed to parse properly.
        This covers various malformed patterns that agents might produce, while avoiding
        false positives on legitimate workflow XML tags like <plan>, <task_list>, etc.
        
        Args:
            text: The text to analyze for potential tool calls
            
        Returns:
            bool: True if potential tool calls are detected, False otherwise
        """
        if not text or not text.strip():
            return False
            
        text_lower = text.lower()
        
        # Get available tool names for pattern matching
        if not (hasattr(self._manager, 'tool_executor') and 
                hasattr(self._manager.tool_executor, 'tools') and 
                self._manager.tool_executor.tools):
            return False
            
        tool_names = list(self._manager.tool_executor.tools.keys())
        
        # Exclude legitimate workflow trigger tags to avoid false positives
        workflow_tags = {'plan', 'task_list', 'request_state', 'think'}
        
        # Pattern 1: Markdown fenced XML with malformed brackets (SPECIFIC TO TOOL NAMES ONLY)
        # Example: ```tool_information><action>list_tools</action></tool_information>```
        malformed_fence_patterns = []
        for tool_name in tool_names:
            # Only check for markdown fences containing actual tool names
            escaped_tool = re.escape(tool_name)
            malformed_fence_patterns.extend([
                # Missing opening bracket for specific tool names
                rf'```[^`]*?{escaped_tool}>.*?</{escaped_tool}>[^`]*?```',
                # Tool names with malformed opening in markdown fences
                rf'```[^`]*?{escaped_tool}[^>]*>.*?</{escaped_tool}>[^`]*?```'
            ])
        
        # Pattern 2: XML-like structures ONLY for actual tool names (not workflow tags)
        tool_specific_patterns = []
        for tool_name in tool_names:
            escaped_tool = re.escape(tool_name)
            tool_specific_patterns.extend([
                # Missing opening bracket for specific tool names only
                rf'{escaped_tool}>[^<>]*</{escaped_tool}>',
                # Malformed opening bracket for specific tool names
                rf'<{escaped_tool}[^>]*>[^<]*</{escaped_tool}>'
            ])
        
        # Pattern 3: Action indicators combined with tool names (more specific)
        action_indicators_with_tools = []
        for tool_name in tool_names:
            if any(keyword in tool_name.lower() for keyword in ['action', 'tool', 'manage', 'project', 'send', 'file']):
                action_indicators_with_tools.extend([
                    f'<action>[^<]*{tool_name}',
                    f'{tool_name}[^<]*<action>',
                    f'<{tool_name}[^>]*action[^>]*>'
                ])
        
        # Check malformed fence patterns (tool names only)
        for pattern in malformed_fence_patterns:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                logger.debug(f"CycleHandler: Detected malformed fence pattern for tool name: {pattern}")
                return True
                
        # Check tool-specific XML patterns (avoiding workflow tags)
        for pattern in tool_specific_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                # Double-check this isn't a workflow tag being caught
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    matched_text = match.group(0).lower()
                    # Skip if it's a legitimate workflow tag
                    is_workflow_tag = any(tag in matched_text for tag in workflow_tags)
                    if not is_workflow_tag:
                        logger.debug(f"CycleHandler: Detected tool-specific XML pattern: {pattern}")
                        return True
                        
        # Check for action indicators combined with tool names
        for pattern in action_indicators_with_tools:
            if re.search(pattern, text, re.IGNORECASE):
                logger.debug(f"CycleHandler: Detected action indicator with tool name: {pattern}")
                return True
        
        # Pattern 4: The exact malformed pattern from original logs (very specific)
        # ```tool_information><action>list_tools</action></tool_information>```
        exact_original_pattern = r'```[^`]*?[a-zA-Z_]+>[^<]*<[^>]*>[^<]*</[^>]*>[^<]*</[^>]*>[^`]*```'
        if re.search(exact_original_pattern, text, re.IGNORECASE | re.DOTALL):
            logger.debug(f"CycleHandler: Detected exact original malformed pattern")
            return True
            
        return False

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
                    
                    # CRITICAL FIX: Enhanced error messages with specific diagnostic information
                    def generate_enhanced_error_msg(verdict_text: str, error_type: str) -> str:
                        """Generate detailed error messages for Constitutional Guardian verdict parsing issues"""
                        base_msgs = {
                            "malformed_concern": "Constitutional Guardian expressed a concern, but the format was malformed",
                            "malformed_inconclusive": "Constitutional Guardian returned a malformed or inconclusive verdict",
                            "empty_response": "Constitutional Guardian provided no response content"
                        }
                        
                        diagnostic_info = f"[CG Diagnostic] Raw verdict: '{verdict_text[:200]}{'...' if len(verdict_text) > 200 else ''}'"
                        if len(verdict_text) > 200:
                            diagnostic_info += f" (truncated from {len(verdict_text)} chars)"
                        
                        enhanced_msg = f"{base_msgs.get(error_type, 'Constitutional Guardian processing error')}\n{diagnostic_info}"
                        
                        # Add specific suggestions based on error type
                        if error_type == "malformed_concern":
                            enhanced_msg += "\n[Suggestion] Expected format: <CONCERN>specific concern text</CONCERN>"
                        elif error_type == "malformed_inconclusive":
                            enhanced_msg += "\n[Suggestion] Expected either <OK/> or <CONCERN>text</CONCERN>"
                        
                        return enhanced_msg

                    IMPLICIT_OK_PHRASES = [
                        "no constitutional content", "no issues found",
                        "seems to be a friendly greeting",
                        "no substantial text related to constitutional matters", "fully complies"
                    ]

                    if OK_TAG in stripped_verdict:
                        if "concern" in stripped_verdict.lower(): # Ambiguity check
                            verdict_to_return = generate_enhanced_error_msg(stripped_verdict, "malformed_concern")
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
                                verdict_to_return = generate_enhanced_error_msg(stripped_verdict, "malformed_concern")
                        else: # Not a well-formed concern, check for other signals
                            has_concern_start_tag_only = (concern_start_index != -1 and concern_end_index == -1)
                            contains_concern_keyword = "concern" in stripped_verdict.lower()
                            if has_concern_start_tag_only or contains_concern_keyword:
                                verdict_to_return = generate_enhanced_error_msg(stripped_verdict, "malformed_concern")
                            elif stripped_verdict: # Must be non-empty to check for implicit OK
                                is_implicit_ok = False
                                for phrase in IMPLICIT_OK_PHRASES:
                                    if phrase in stripped_verdict.lower():
                                        is_implicit_ok = True; break
                                if is_implicit_ok:
                                    verdict_to_return = OK_TAG
                                else: # No implicit OK, and no other pattern matched
                                    verdict_to_return = generate_enhanced_error_msg(stripped_verdict, "malformed_inconclusive")
                            else: # Empty stripped_verdict
                                verdict_to_return = generate_enhanced_error_msg("", "empty_response")

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

    def _generate_empty_response_guidance(self, agent: 'Agent') -> str:
        """Generates specific guidance for an agent stuck in an empty response loop."""
        base_message = "[Framework Intervention]: You have produced multiple empty responses, indicating you are stuck. "
        if agent.agent_type == AGENT_TYPE_ADMIN and agent.state == 'work':
            # Analyze recent history for context
            last_tool_call = None
            for msg in reversed(agent.message_history):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    last_tool_call = msg["tool_calls"][0]
                    break

            if last_tool_call:
                tool_name = last_tool_call.get("name")
                guidance = (
                    f"Your last action was an attempt to use the '{tool_name}' tool. "
                    "You are now in a loop. To proceed, you MUST take a different action. "
                    "1. Re-evaluate your goal. What are you trying to accomplish? "
                    "2. Use the `<tool_information><action>list_tools</action></tool_information>` to see all available tools. "
                    "3. Choose a DIFFERENT tool to continue your task or provide a comprehensive summary of your findings and request a state change."
                )
            else:
                guidance = (
                    "You have not taken any meaningful action recently. "
                    "To proceed, you MUST take a concrete action. "
                    "Use `<tool_information><action>list_tools</action></tool_information>` to see available tools and test one, "
                    "or provide a summary of your work so far."
                )
            return base_message + guidance

        return base_message + "Please review your objective and take a concrete step to move forward."

    def _generate_admin_work_completion_message(self, agent: 'Agent') -> str:
        """
        Generates a specific, actionable task for an Admin AI that is stuck in a work loop.
        This task instructs the agent to systematically test available tools to break the loop.
        """
        logger.info(f"Generating tool-testing task for stuck Admin AI '{agent.agent_id}'.")

        # Get the list of available tools to make the prompt more intelligent.
        tool_list_str = "Could not retrieve tool list."
        if hasattr(self._manager, 'tool_executor') and self._manager.tool_executor:
            try:
                # Using a method that gets a simple list for the agent's type.
                tool_list_str = self._manager.tool_executor.get_available_tools_list_str(agent.agent_type)
            except Exception as e:
                logger.error(f"Error getting tool list for tool testing task generation: {e}")

        # Frame the new task as a system intervention.
        task_message = (
            "[Framework Intervention]: You appear to be stuck in a work loop without a specific task. "
            "A new task has been assigned to you to ensure progress.\n\n"
            "**Your New Task: Systematically Test Available Tools**\n\n"
            "**Step 1: Discover all available tools.**\n"
            "Your first action MUST be to output the following XML to get a list of all tools you can use:\n"
            "```xml\n"
            "<tool_information><action>list_tools</action></tool_information>\n"
            "```\n\n"
            "**Step 2: Analyze and Test.**\n"
            "After you receive the list, pick ONE tool from the list that you have not recently used and test one of its actions. "
            "Use the `get_info` action of the `tool_information` tool first if you are unsure how to use it.\n\n"
            f"**For context, here is a summary of tools currently available to you:**\n{tool_list_str}"
        )
        return task_message

    # Removed _request_cg_review method as its functionality is integrated into _get_cg_verdict and run_cycle

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        logger.critical(f"!!! CycleHandler: run_cycle TASK STARTED for Agent '{agent.agent_id}' (Retry: {retry_count}) !!!")
        
        # CRITICAL FIX: Check if agent is awaiting Constitutional Guardian review before proceeding
        if agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG:
            logger.warning(f"CycleHandler: Agent '{agent.agent_id}' is awaiting Constitutional Guardian user review. Skipping cycle execution.")
            await self._manager.send_to_ui({
                "type": "cg_cycle_blocked", 
                "agent_id": agent.agent_id, 
                "message": f"Agent '{agent.agent_id}' cycle blocked: awaiting Constitutional Guardian user decision"
            })
            return
        
        # Also check for other blocking statuses that should prevent cycle execution
        blocking_statuses = [AGENT_STATUS_AWAITING_USER_REVIEW_CG, AGENT_STATUS_ERROR]
        if agent.status in blocking_statuses:
            logger.warning(f"CycleHandler: Agent '{agent.agent_id}' has blocking status '{agent.status}'. Skipping cycle execution.")
            await self._manager.send_to_ui({
                "type": "cycle_blocked", 
                "agent_id": agent.agent_id, 
                "status": agent.status,
                "message": f"Agent '{agent.agent_id}' cycle blocked due to status: {agent.status}"
            })
            return
        
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
            context.turn_count += 1
            logger.debug(f"CycleHandler '{agent.agent_id}': Starting/Restarting thinking process within run_cycle's main loop. Turn: {context.turn_count}")

            if context.turn_count > settings.MAX_CYCLE_TURNS:
                error_message = f"Agent '{agent.agent_id}' exceeded the maximum of {settings.MAX_CYCLE_TURNS} turns in a single cycle. Forcing error state to prevent infinite loop."
                logger.critical(error_message)
                agent.set_status(AGENT_STATUS_ERROR)
                if context.current_db_session_id:
                    await self._manager.db_manager.log_interaction(
                        session_id=context.current_db_session_id,
                        agent_id=agent.agent_id,
                        role="system_error",
                        content=error_message
                    )
                await self._manager.send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": error_message})
                break # Exit the while loop

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
                
                # Check if context summarization is needed for small local LLMs
                try:
                    # Estimate current token count
                    estimated_tokens = self._context_summarizer.estimate_token_count(context.history_for_call)
                    # Get max tokens from agent's LLM provider (default to 8000 if not available)
                    max_tokens = getattr(agent.llm_provider, 'max_tokens', 8000) if agent.llm_provider else 8000
                    
                    if await self._context_summarizer.should_summarize_context(agent.agent_id, estimated_tokens, max_tokens):
                        logger.info(f"CycleHandler: Context summarization needed for agent '{agent.agent_id}' due to token limits")
                        try:
                            success, summarized_context = await self._context_summarizer.summarize_agent_context(
                                agent.agent_id, context.history_for_call
                            )
                            if success and summarized_context:
                                context.history_for_call = summarized_context
                                # CRITICAL FIX: Also update the agent's persistent message history
                                # This prevents repeated summarization and maintains context continuity
                                agent.message_history = summarized_context.copy()
                                logger.info(f"CycleHandler: Context successfully summarized for agent '{agent.agent_id}', reduced to {len(summarized_context)} messages")
                                logger.critical(f"CycleHandler: PERSISTENT HISTORY UPDATED for agent '{agent.agent_id}' - new persistent length: {len(agent.message_history)}")
                                
                                # Notify UI about context summarization
                                await self._manager.send_to_ui({
                                    "type": "context_summarization",
                                    "agent_id": agent.agent_id,
                                    "original_message_count": len(agent.message_history),
                                    "summarized_message_count": len(summarized_context),
                                    "estimated_token_reduction": "50-75%"
                                })
                        except Exception as summarization_error:
                            logger.error(f"CycleHandler: Context summarization failed for agent '{agent.agent_id}': {summarization_error}", exc_info=True)
                            # Continue with original context if summarization fails
                except Exception as context_check_error:
                    logger.error(f"CycleHandler: Error checking context summarization for agent '{agent.agent_id}': {context_check_error}", exc_info=True)
                    # Continue with original context if checking fails
                
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
                            # Default reactivation logic
                            context.needs_reactivation_after_cycle = not (workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule)) and \
                                                                bool(workflow_result.next_agent_state or workflow_result.tasks_to_schedule)

                            # Specific intervention for Admin AI after project_creation workflow
                            if agent.agent_id == BOOTSTRAP_AGENT_ID and workflow_result.workflow_name == "project_creation":
                                logger.info(f"CycleHandler '{agent.agent_id}': ProjectCreationWorkflow completed. Explicitly setting needs_reactivation_after_cycle to False for Admin AI.")
                                context.needs_reactivation_after_cycle = False
                        else:
                            context.last_error_content = f"Workflow '{workflow_result.workflow_name}' failed: {workflow_result.message}"; context.last_error_obj = ValueError(context.last_error_content)
                            # Default reactivation logic for failed workflow
                            context.needs_reactivation_after_cycle = not (workflow_result.tasks_to_schedule and any(ts_agent.agent_id == agent.agent_id for ts_agent, _ in workflow_result.tasks_to_schedule)) and \
                                                                (not workflow_result.next_agent_state and workflow_result.next_agent_status != AGENT_STATUS_ERROR)
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "malformed_tool_call":
                        context.action_taken_this_cycle = True; raw_llm_response_with_error = event.get("raw_assistant_response")
                        malformed_tool_name = event.get("tool_name"); parsing_error_msg = event.get("error_message")
                        logger.warning(f"Agent {agent.agent_id} produced malformed XML for tool '{malformed_tool_name}'. Error: {parsing_error_msg}")
                        
                        # Try XML validation and recovery
                        recovered_xml = None
                        recovery_attempted = False
                        if raw_llm_response_with_error:
                            try:
                                validation_result = self._xml_validator.validate_xml(raw_llm_response_with_error)
                                if not validation_result['is_valid']:
                                    logger.info(f"CycleHandler: Attempting XML recovery for agent '{agent.agent_id}'")
                                    recovery_result = self._xml_validator.recover_xml(raw_llm_response_with_error)
                                    recovery_attempted = True
                                    
                                    if recovery_result['success']:
                                        recovered_xml = recovery_result['recovered_xml']
                                        logger.info(f"CycleHandler: XML recovery successful for agent '{agent.agent_id}'. Applied fixes: {recovery_result['applied_fixes']}")
                                        
                                        # Try to extract tool calls from recovered XML
                                        extracted_calls = self._xml_validator.extract_tool_calls(recovered_xml)
                                        if extracted_calls:
                                            logger.info(f"CycleHandler: Extracted {len(extracted_calls)} tool calls from recovered XML")
                                            await self._manager.send_to_ui({
                                                "type": "xml_recovery_success",
                                                "agent_id": agent.agent_id,
                                                "original_xml": raw_llm_response_with_error[:200] + "...",
                                                "recovered_xml": recovered_xml[:200] + "...",
                                                "recovered_calls": len(extracted_calls),
                                                "applied_fixes": recovery_result['applied_fixes']
                                            })
                                            # Continue processing with recovered tool calls - skip the rest of malformed handling
                                            context.needs_reactivation_after_cycle = True
                                            context.cycle_completed_successfully = True
                                            llm_stream_ended_cleanly = False; break
                                        else:
                                            logger.warning(f"CycleHandler: XML recovery succeeded but no tool calls could be extracted for agent '{agent.agent_id}'")
                                    else:
                                        logger.warning(f"CycleHandler: XML recovery failed for agent '{agent.agent_id}'. Error: {recovery_result.get('error', 'Unknown error')}. Suggestions: {recovery_result.get('suggestions', [])}")
                            except Exception as xml_recovery_error:
                                logger.error(f"CycleHandler: Exception during XML recovery for agent '{agent.agent_id}': {xml_recovery_error}", exc_info=True)
                        
                        # If recovery failed or wasn't attempted, continue with original error handling
                        if context.current_db_session_id and raw_llm_response_with_error: 
                            await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id,agent_id=agent.agent_id,role="assistant",content=raw_llm_response_with_error)
                        
                        # Check if we've already provided feedback for this specific error pattern to prevent loops
                        error_signature = f"malformed_{malformed_tool_name}_{parsing_error_msg[:50]}"
                        if not hasattr(agent, '_recent_error_feedback'):
                            agent._recent_error_feedback = {}
                            
                        # Only provide feedback if we haven't seen this exact error recently
                        if error_signature not in agent._recent_error_feedback or (time.time() - agent._recent_error_feedback[error_signature]) > 300:  # 5 minutes
                            detailed_tool_usage = "Could not retrieve detailed usage for this tool."
                            if malformed_tool_name and malformed_tool_name in self._manager.tool_executor.tools:
                                try: 
                                    detailed_tool_usage = self._manager.tool_executor.tools[malformed_tool_name].get_detailed_usage()
                                except Exception as usage_exc: 
                                    logger.error(f"Failed to get detailed usage for tool {malformed_tool_name}: {usage_exc}")
                            
                            # Generate more helpful feedback that addresses the specific XML issue
                            if "list_tools" in parsing_error_msg:
                                feedback_to_agent = (f"[Framework Feedback: Tool Usage Error]\n"
                                                   f"You attempted to use '{malformed_tool_name}' with tool_name='list_tools', but 'list_tools' is not a tool name - it's an action.\n"
                                                   f"Correct usage: <tool_information><action>list_tools</action></tool_information>\n"
                                                   f"This will list all available tools and their summaries.")
                            else:
                                feedback_to_agent = (f"[Framework Feedback: XML Parsing Error]\n"
                                                   f"Your XML for '{malformed_tool_name}' was malformed: {parsing_error_msg}\n"
                                                   f"Please check your XML syntax. Remove any markdown code fences (```) around XML.\n\n"
                                                   f"Correct usage for '{malformed_tool_name}':\n{detailed_tool_usage}")
                            
                            agent.message_history.append({"role": "system", "content": feedback_to_agent})
                            agent._recent_error_feedback[error_signature] = time.time()  # Record when we provided this feedback
                            
                            if context.current_db_session_id: 
                                await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id,agent_id=agent.agent_id,role="system_error_feedback",content=feedback_to_agent)
                                
                            await self._manager.send_to_ui({"type": "system_error_feedback","agent_id": agent.agent_id,"tool_name": malformed_tool_name,"error_message": parsing_error_msg,"detailed_usage": detailed_tool_usage,"original_attempt": raw_llm_response_with_error})
                            
                            logger.info(f"CycleHandler: Provided XML error feedback to '{agent.agent_id}' for error pattern: {error_signature}")
                        else:
                            logger.info(f"CycleHandler: Skipped duplicate XML error feedback for '{agent.agent_id}' - error pattern seen recently: {error_signature}")
                        
                        context.needs_reactivation_after_cycle = True; context.last_error_content = f"Malformed XML for tool '{malformed_tool_name}'"; context.cycle_completed_successfully = False
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "agent_thought":
                        context.action_taken_this_cycle = True; context.thought_produced_this_cycle = True
                        # ... (existing thought processing, KB saving) ...
                        thought_content = event.get("content") # Simplified for brevity here
                        if thought_content and context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant_thought", content=thought_content)
                        if thought_content: await self._manager.send_to_ui(event) # Save to KB logic omitted for diff brevity but would be here

                    elif event_type == "agent_raw_response":
                        # Forward raw agent responses to the UI for display in Internal Comms
                        raw_content = event.get("content")
                        if raw_content:
                            await self._manager.send_to_ui(event)
                            logger.debug(f"CycleHandler '{agent.agent_id}': Forwarded agent_raw_response to UI")

                    elif event_type == "agent_state_change_requested":
                        context.action_taken_this_cycle = True; context.state_change_requested_this_cycle = True; requested_state = event.get("requested_state")
                        if self._manager.workflow_manager.change_state(agent, requested_state):
                            context.needs_reactivation_after_cycle = True
                            # --- START MODIFICATION: Inject directive after PM state change to pm_activate_workers ---
                            if agent.agent_type == AGENT_TYPE_PM and requested_state == PM_STATE_ACTIVATE_WORKERS:
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' successfully changed to '{PM_STATE_ACTIVATE_WORKERS}'. Injecting specific follow-up directive.")
                                directive_for_activate_workers = (
                                    f"[Framework System Message]: You are now in state '{PM_STATE_ACTIVATE_WORKERS}'. "
                                    "Your MANDATORY next action is to begin Step 1 of your workflow: Identify the first Kick-Off Task and a suitable Worker Agent. "
                                    "Use `<project_management><action>list_tasks</action>...</project_management>` and/or "
                                    "`<manage_team><action>list_agents</action>...</manage_team>` as needed. "
                                    "Remember to use `<think>...</think>` before acting."
                                )
                                agent.message_history.append({"role": "system", "content": directive_for_activate_workers})
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_intervention",
                                        content=directive_for_activate_workers
                                    )
                            # --- END MODIFICATION ---
                        else:
                            # If change_state returned False (e.g., invalid state), still likely needs reactivation to retry or get error feedback.
                            context.needs_reactivation_after_cycle = True

                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="agent_state_change", content=f"State changed to: {requested_state}")
                        await self._manager.send_to_ui(event)
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "tool_requests":
                        context.action_taken_this_cycle = True; tool_calls = event.get("calls", []); raw_assistant_response = event.get("raw_assistant_response")
                        
                        # CRITICAL FIX: Check if the raw response also contains a state change request
                        # This handles the case where agent produces both state change + tool calls in same response
                        if raw_assistant_response and hasattr(self, 'request_state_pattern'):
                            state_match = self.request_state_pattern.search(raw_assistant_response)
                            if state_match:
                                requested_state = state_match.group(1)
                                if self._manager.workflow_manager.is_valid_state(agent.agent_type, requested_state):
                                    logger.info(f"CycleHandler: Processing embedded state change request '{requested_state}' from tool_requests response")
                                    context.state_change_requested_this_cycle = True
                                    if self._manager.workflow_manager.change_state(agent, requested_state):
                                        logger.info(f"CycleHandler: Successfully changed agent '{agent.agent_id}' state to '{requested_state}' during tool processing")
                                    else:
                                        logger.warning(f"CycleHandler: Failed to change agent '{agent.agent_id}' state to '{requested_state}' during tool processing")
                        # ... (append assistant message to history, db log) ...
                        if raw_assistant_response or tool_calls:
                            # If tool_calls are present, content should be None for most models.
                            content_for_history = None if tool_calls else raw_assistant_response

                            assistant_message_for_history: MessageDict = {"role": "assistant", "content": content_for_history}
                            if tool_calls:
                                assistant_message_for_history["tool_calls"] = tool_calls

                            agent.message_history.append(assistant_message_for_history)

                            # Log the interaction to the database
                            if context.current_db_session_id:
                                await self._manager.db_manager.log_interaction(
                                    session_id=context.current_db_session_id,
                                    agent_id=agent.agent_id,
                                    role="assistant",
                                    content=raw_assistant_response,  # Log the original raw response for debugging
                                    tool_calls=tool_calls
                                )
                        
                        all_tool_results_for_history: List[MessageDict] = [] ; any_tool_success = False
                        for i, call_data in enumerate(tool_calls):
                            tool_name = call_data.get("name"); tool_id = call_data.get("id"); tool_args = call_data.get("arguments", {})
                            result_dict = await self._interaction_handler.execute_single_tool(agent, tool_id, tool_name, tool_args, self._manager.current_project, self._manager.current_session)
                            if result_dict:
                                history_item: MessageDict = {"role": "tool", "tool_call_id": result_dict.get("call_id", tool_id or f"unknown_id_{i}"), "name": result_dict.get("name", tool_name or f"unknown_tool_{i}"), "content": str(result_dict.get("content", "[Tool Error: No content]"))}
                                all_tool_results_for_history.append(history_item)
                                result_content_str = str(result_dict.get("content", ""))
                                tool_was_successful = True # Assume success by default
                                try:
                                    # Attempt to parse the content as JSON. This handles tools that
                                    # return structured results (like file_system will).
                                    tool_result_data = json.loads(result_content_str)
                                    if isinstance(tool_result_data, dict) and tool_result_data.get("status") == "error":
                                        tool_was_successful = False
                                except (json.JSONDecodeError, TypeError):
                                    # If it's not valid JSON, fall back to the old string check for compatibility.
                                    if "error" in result_content_str.lower():
                                        tool_was_successful = False

                                if tool_was_successful:
                                    any_tool_success = True
                                # ... (db log tool result, UI send) ...
                                if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="tool", content=result_content_str, tool_results=[result_dict])
                                await self._manager.send_to_ui({**result_dict, "type": "tool_result", "agent_id": agent.agent_id, "tool_sequence": f"{i+1}_of_{len(tool_calls)}"})
                            else: all_tool_results_for_history.append({"role": "tool", "tool_call_id": tool_id or f"unknown_call_{i}", "name": tool_name or f"unknown_tool_{i}", "content": "[Tool Error: No result object]"})
                        # CRITICAL FIX: Add diagnostic logging for message history persistence  
                        logger.critical(f"CycleHandler: BEFORE appending tool results - agent '{agent.agent_id}' message_history length: {len(agent.message_history)}, history_id: {id(agent.message_history)}")
                        for i, res_hist_item in enumerate(all_tool_results_for_history): 
                            agent.message_history.append(res_hist_item)
                            logger.critical(f"CycleHandler: APPENDED tool result {i+1}/{len(all_tool_results_for_history)} to agent '{agent.agent_id}' - new length: {len(agent.message_history)}")
                        logger.critical(f"CycleHandler: AFTER appending all tool results - agent '{agent.agent_id}' message_history length: {len(agent.message_history)}, history_id: {id(agent.message_history)}")
                        
                        # CRITICAL FIX: Set tool success flags IMMEDIATELY after tool execution
                        # This must happen BEFORE any PM-specific intervention logic to ensure Admin AI gets reactivated
                        context.executed_tool_successfully_this_cycle = any_tool_success
                        context.needs_reactivation_after_cycle = True
                        logger.critical(f"CycleHandler: CRITICAL - Tool success flags set for agent '{agent.agent_id}': executed_tool_successfully_this_cycle={any_tool_success}, needs_reactivation_after_cycle=True")

                        # --- START: PM Post-Tool State Transitions ---
                        # Track tool success/failure for PM loop detection
                        if agent.agent_type == AGENT_TYPE_PM and tool_calls and len(tool_calls) == 1:
                            called_tool_name = tool_calls[0].get("name")
                            
                            # Check for persistent tool failures that could cause loops
                            if not any_tool_success:
                                # Increment consecutive failure counter
                                if not hasattr(agent, '_consecutive_tool_failures'):
                                    agent._consecutive_tool_failures = 0
                                agent._consecutive_tool_failures += 1
                                
                                # If too many consecutive failures, force intervention
                                if agent._consecutive_tool_failures >= 3:
                                    logger.error(f"CycleHandler: PM '{agent.agent_id}' had {agent._consecutive_tool_failures} consecutive tool failures. Forcing error state to prevent loop.")
                                    agent.set_status(AGENT_STATUS_ERROR)
                                    error_message = f"Agent '{agent.agent_id}' had {agent._consecutive_tool_failures} consecutive tool execution failures. Stopped to prevent infinite loop."
                                    agent.message_history.append({"role": "system", "content": f"[Framework Error]: {error_message}"})
                                    
                                    if context.current_db_session_id:
                                        await self._manager.db_manager.log_interaction(
                                            session_id=context.current_db_session_id,
                                            agent_id=agent.agent_id,
                                            role="system_error",
                                            content=error_message
                                        )
                                    await self._manager.send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": error_message})
                                    context.needs_reactivation_after_cycle = False
                                    break  # Break from tool processing loop
                            else:
                                # Reset failure counter on success
                                agent._consecutive_tool_failures = 0
                        
                        if agent.agent_type == AGENT_TYPE_PM and any_tool_success and tool_calls and len(tool_calls) == 1:
                            called_tool_name = tool_calls[0].get("name")
                            if agent.state == PM_STATE_ACTIVATE_WORKERS and called_tool_name == "send_message":
                                # This is the final "report to admin" message. Transition to manage state.
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' sent completion message. Auto-transitioning to PM_STATE_MANAGE.")
                                self._manager.workflow_manager.change_state(agent, PM_STATE_MANAGE)
                                # Reactivate immediately to start the management loop.
                                context.needs_reactivation_after_cycle = True


                        # --- START: PM Build Team Tasks State Interventions ---
                        if agent.agent_type == AGENT_TYPE_PM and \
                           agent.state == PM_STATE_BUILD_TEAM_TASKS and \
                           any_tool_success and \
                           tool_calls and len(tool_calls) == 1: # Ensure only one tool was called

                            called_tool_name = tool_calls[0].get("name")
                            called_tool_args = tool_calls[0].get("arguments", {})
                            directive_message_content = None

                            if called_tool_name == "manage_team" and called_tool_args.get("action") == "create_team":
                                # This was the first action in the state (Team Creation)
                                directive_message_content = (
                                    "[Framework System Message]: Team creation process initiated. "
                                    "Your MANDATORY next action is to get specific instructions for creating an agent. "
                                    "Output ONLY the following XML: <tool_information><action>get_info</action><tool_name>manage_team</tool_name><sub_action>create_agent</sub_action></tool_information>"
                                )
                            elif called_tool_name == "tool_information" and \
                                 called_tool_args.get("action") == "get_info" and \
                                 called_tool_args.get("tool_name") == "manage_team" and \
                                 called_tool_args.get("sub_action") == "create_agent":
                                # This was the second action (Getting create_agent info)
                                agent.successfully_created_agent_count_for_build = 0 # Reset counter before first create
                                directive_message_content = (
                                    "[Framework System Message]: You have successfully retrieved the detailed information for the 'manage_team' tool with sub_action 'create_agent'. "
                                    "Your MANDATORY next action is to proceed with Step 2 of your workflow: Create the First Worker Agent using the "
                                    "'<manage_team><action>create_agent</action>...' XML format, referring to your initial kick-off tasks list."
                                )
                            elif called_tool_name == "manage_team" and called_tool_args.get("action") == "create_agent":
                                # This was an agent creation action. This is the new, context-aware intervention logic.
                                agent.successfully_created_agent_count_for_build += 1

                                # Get up-to-date team information
                                team_id = self._manager.state_manager.get_agent_team(agent.agent_id)
                                current_worker_agents = []
                                if team_id:
                                    # We get the Agent objects and filter for workers, then get their IDs
                                    all_agents_in_team = self._manager.state_manager.get_agents_in_team(team_id)
                                    current_worker_agents = [a.agent_id for a in all_agents_in_team if a.agent_type == AGENT_TYPE_WORKER]

                                created_count = len(current_worker_agents) # Use the actual count from the state manager
                                # *** FIX: Corrected variable name from kick_off_task_count_for_build to target_worker_agents_for_build ***
                                target_workers = getattr(agent, 'target_worker_agents_for_build', -1)
                                max_workers_allowed = settings.MAX_WORKERS_PER_PM

                                # Construct the context block for the message
                                team_status_context = (
                                    f"  - Target Worker Agents: {target_workers if target_workers != -1 else 'Not specified, max allowed: ' + str(max_workers_allowed)}\n"
                                    f"  - Worker Agents Created So Far: {created_count}\n"
                                    f"  - Current Worker Agent IDs in Team: {current_worker_agents if current_worker_agents else 'None'}"
                                )

                                # Determine the next action based on the counts
                                proceed_to_next_step = False
                                reason = ""
                                if target_workers != -1:
                                    # Primary logic: Compare created agents to the target number from the kickoff plan
                                    if created_count >= target_workers:
                                        proceed_to_next_step = True
                                        reason = f"you have created all {target_workers} planned worker agents."
                                    elif created_count >= max_workers_allowed:
                                        proceed_to_next_step = True
                                        reason = f"you have reached the maximum allowed limit of {max_workers_allowed} worker agents."
                                else:
                                    # Fallback logic if target_worker_agents_for_build is somehow not set
                                    logger.warning(f"CycleHandler: PM '{agent.agent_id}' in build state but 'target_worker_agents_for_build' is not set. Using max_workers fallback logic.")
                                    if created_count >= max_workers_allowed:
                                        proceed_to_next_step = True
                                        reason = f"the target number of agents was not specified, and you have reached the maximum allowed limit of {max_workers_allowed} worker agents."

                                if proceed_to_next_step:
                                    directive_message_content = (
                                        f"[Framework System Message]: Agent creation processed.\n"
                                        "[CURRENT TEAM STATUS]\n"
                                        f"{team_status_context}\n\n"
                                        f"[CONCLUSION]\n"
                                        f"Because {reason}, your work in this state is complete.\n\n"
                                        "Your MANDATORY next action is to proceed to Step 4 of your workflow: Request 'Activate Workers' State by outputting ONLY the following XML:\n"
                                        "<request_state state='pm_activate_workers'/>"
                                    )
                                else:
                                    # More agents need to be created
                                    next_agent_num = created_count + 1
                                    directive_message_content = (
                                        f"[Framework System Message]: Agent creation processed.\n"
                                        "[CURRENT TEAM STATUS]\n"
                                        f"{team_status_context}\n\n"
                                        "[CONCLUSION]\n"
                                        "More worker agents are required.\n\n"
                                        f"Your MANDATORY next action is to proceed with Step 3 of your workflow: Create the next worker agent (Worker #{next_agent_num}), referring to your initial kick-off tasks list."
                                    )

                            if directive_message_content:
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' in '{agent.state}', after tool '{called_tool_name}', injecting directive: {directive_message_content[:100]}...")
                                directive_msg: MessageDict = {"role": "system", "content": directive_message_content}
                                agent.message_history.append(directive_msg) # Append to live history
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_intervention",
                                        content=directive_message_content
                                    )
                        # --- END: PM Build Team Tasks State Interventions ---

                        # --- START: PM Activate Workers State Interventions ---
                        elif agent.agent_type == AGENT_TYPE_PM and \
                             agent.state == PM_STATE_ACTIVATE_WORKERS and \
                             any_tool_success and \
                             tool_calls and len(tool_calls) == 1:

                            called_tool_name = tool_calls[0].get("name")
                            called_tool_args = tool_calls[0].get("arguments", {})
                            directive_message_content = None

                            if called_tool_name == "project_management":
                                last_tool_result_content = all_tool_results_for_history[0].get("content", "{}")
                                try:
                                    tool_result_json = json.loads(last_tool_result_content)
                                    tool_status = tool_result_json.get("status")
                                except json.JSONDecodeError:
                                    tool_status = "error"
                                    tool_result_json = {}

                                if tool_status == "error":
                                    error_message = tool_result_json.get("message", "An unspecified error occurred.")
                                    directive_message_content = (
                                        f"[Framework Feedback: Tool Error]\nYour last action resulted in an error: '{error_message}'.\n"
                                        "Please review the error and your previous steps. Ensure you are using the correct information, such as valid UUIDs for tasks from the `list_tasks` results. "
                                        "Do not invent placeholder IDs. Correct your approach and try again."
                                    )
                                else: # Success
                                    action_performed = called_tool_args.get("action")
                                    if action_performed == "list_tasks":
                                        tasks = tool_result_json.get("tasks", [])
                                        task_summary_lines = []
                                        agent.unassigned_tasks_summary = [] # Clear previous summary
                                        for task in tasks:
                                            uuid = task.get("uuid")
                                            desc = task.get("description", "No description").strip().replace('\n', ' ')
                                            truncated_desc = (desc[:75] + '...') if len(desc) > 75 else desc
                                            if uuid:
                                                task_summary_lines.append(f"- {truncated_desc} (UUID: {uuid})")
                                                agent.unassigned_tasks_summary.append({"uuid": uuid, "description": desc})

                                        summary_str = "\n".join(task_summary_lines) if task_summary_lines else "No unassigned tasks found."
                                        directive_message_content = (
                                            f"[Framework System Message]: Task list retrieved successfully. Here is a summary of the unassigned tasks:\n"
                                            f"{summary_str}\n\n"
                                            "Your mandatory next action is to get the list of available agents using the `<manage_team><action>list_agents</action>...</manage_team>` tool."
                                        )
                                    elif action_performed == "modify_task":
                                        assigned_task_uuid = called_tool_args.get("task_id")
                                        if hasattr(agent, 'unassigned_tasks_summary') and isinstance(agent.unassigned_tasks_summary, list) and assigned_task_uuid:
                                            # Remove the assigned task from our summary
                                            agent.unassigned_tasks_summary = [t for t in agent.unassigned_tasks_summary if t.get("uuid") != assigned_task_uuid]

                                        # Now, generate a new summary of remaining tasks
                                        remaining_tasks = getattr(agent, 'unassigned_tasks_summary', [])
                                        if not remaining_tasks:
                                            project_name = agent.agent_config.get("config", {}).get("project_name_context", "Unknown Project")
                                            directive_message_content = (
                                                "[Framework System Message]: Last task assignment processed successfully. All kick-off tasks have now been assigned.\n\n"
                                                "Your MANDATORY next action is to report this completion to the Admin AI. "
                                                f"Use the send_message tool to send the following message to '{BOOTSTRAP_AGENT_ID}':\n"
                                                f"'Project `{project_name}` kick-off phase complete. All initial tasks have been assigned to workers.'"
                                            )
                                        else:
                                            task_summary_lines = []
                                            for task_info in remaining_tasks:
                                                desc = task_info.get("description", "No description")
                                                uuid = task_info.get("uuid")
                                                truncated_desc = (desc[:75] + '...') if len(desc) > 75 else desc
                                                task_summary_lines.append(f"- {truncated_desc} (UUID: {uuid})")
                                            summary_str = "\n".join(task_summary_lines)
                                            directive_message_content = (
                                                f"[Framework System Message]: Task assignment processed successfully. Here are the remaining unassigned tasks:\n"
                                                f"{summary_str}\n\n"
                                                "Your mandatory next action is to assign the next task from this list to a suitable agent."
                                            )
                            elif called_tool_name == "manage_team" and called_tool_args.get("action") == "list_agents":
                                # This intervention is now more intelligent. It re-presents the simplified task list.
                                task_summary_lines = []
                                if hasattr(agent, 'unassigned_tasks_summary') and agent.unassigned_tasks_summary:
                                    for task_info in agent.unassigned_tasks_summary:
                                        desc = task_info.get("description", "No description")
                                        uuid = task_info.get("uuid")
                                        truncated_desc = (desc[:75] + '...') if len(desc) > 75 else desc
                                        task_summary_lines.append(f"- {truncated_desc} (UUID: {uuid})")

                                summary_str = "\n".join(task_summary_lines) if task_summary_lines else "No unassigned tasks found in summary. Please re-list tasks if needed."
                                directive_message_content = (
                                    "[Framework System Message]: You now have the list of available agents. For your convenience, here is the summary of unassigned tasks you previously retrieved:\n"
                                    f"{summary_str}\n\n"
                                    "Your mandatory next action is to assign the first task from this list to a suitable agent using its correct UUID."
                                )

                            if directive_message_content:
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' in '{agent.state}', after tool '{called_tool_name}', injecting directive: {directive_message_content[:100]}...")
                                directive_msg: MessageDict = {"role": "system", "content": directive_message_content}
                                agent.message_history.append(directive_msg)
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_intervention",
                                        content=directive_message_content
                                    )
                        # --- END: PM Activate Workers State Interventions ---

                        # --- START: PM Manage State Interventions ---
                        elif agent.agent_type == AGENT_TYPE_PM and \
                            agent.state == PM_STATE_MANAGE and \
                            any_tool_success and \
                            tool_calls and len(tool_calls) == 1:

                            called_tool_name = tool_calls[0].get("name")
                            called_tool_args = tool_calls[0].get("arguments", {})
                            directive_message_content = None

                            if called_tool_name == "project_management" and called_tool_args.get("action") == "list_tasks":
                                # After listing tasks, the agent needs to analyze and decide.
                                # The prompt itself guides this, so we just confirm and let it proceed.
                                # A more advanced implementation could analyze the task list here and provide a more specific directive.
                                directive_message_content = (
                                    "[Framework System Message]: You have the current task list. "
                                    "Your MANDATORY next action is to analyze the list as per your workflow (Step 2) "
                                    "and execute the single most appropriate management action (e.g., assign task, review work, or send a status update)."
                                )
                            elif called_tool_name == "send_message" and called_tool_args.get("target_agent_id") == BOOTSTRAP_AGENT_ID:
                                # This handles the case after the PM reports project completion to the Admin AI.
                                if "is complete" in called_tool_args.get("message_content", "").lower():
                                    directive_message_content = (
                                        "[Framework System Message]: You have successfully reported project completion. "
                                        "Your MANDATORY next action is to transition to a standby state. "
                                        "Output ONLY the following XML: <request_state state='pm_standby'/>"
                                    )

                            if directive_message_content:
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' in '{agent.state}', after tool '{called_tool_name}', injecting directive: {directive_message_content[:100]}...")
                                directive_msg: MessageDict = {"role": "system", "content": directive_message_content}
                                agent.message_history.append(directive_msg)
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_intervention",
                                        content=directive_message_content
                                    )
                        # --- END: PM Manage State Interventions ---

                        llm_stream_ended_cleanly = False; break

                # This block handles cases where the LLM stream finished without any specific break-worthy event.
                if llm_stream_ended_cleanly and not context.last_error_obj and not context.action_taken_this_cycle:
                    # This block is now primarily for handling final text responses that are not part of other events.
                    # The flawed "empty response" loop detection has been removed and is now handled exclusively
                    # by the AgentHealthMonitor, which is aware of recent meaningful actions.

                    # Reset empty response counter for other agent types or successful cycles
                    if hasattr(agent, '_consecutive_empty_responses'):
                        agent._consecutive_empty_responses = 0

                    # NEW: Enhanced intervention logic for PM agent stuck in MANAGE state producing only <think>
                    if agent.agent_type == AGENT_TYPE_PM and \
                       agent.state == PM_STATE_MANAGE and \
                       not getattr(agent, '_manage_cycle_cooldown_until', 0) > time.time():

                        # Check if the agent's recent output was only thinking without action
                        recent_think_only = False
                        if agent.text_buffer.strip():
                            # If there's content in text buffer, check if it's just thinking
                            buffer_content = agent.text_buffer.strip()
                            if '<think>' in buffer_content.lower() and not any(tool_name in buffer_content.lower() 
                                for tool_name in ['<project_management>', '<manage_team>', '<send_message>']):
                                recent_think_only = True
                        else:
                            # If no text buffer but also no action taken, this indicates a problematic cycle
                            recent_think_only = True

                        if recent_think_only:
                            logger.info(f"CycleHandler: PM agent '{agent.agent_id}' in MANAGE state produced only thinking without action. Applying enhanced intervention.")

                            # Set a cooldown to prevent immediate re-triggering by the periodic timer
                            agent._manage_cycle_cooldown_until = time.time() + 30  # 30 second cooldown
                            
                            # Count consecutive non-productive cycles
                            if not hasattr(agent, '_manage_unproductive_cycles'):
                                agent._manage_unproductive_cycles = 0
                            agent._manage_unproductive_cycles += 1

                            if agent._manage_unproductive_cycles >= 3:
                                # After 3 unproductive cycles, transition to a standby state
                                logger.warning(f"CycleHandler: PM agent '{agent.agent_id}' had {agent._manage_unproductive_cycles} unproductive MANAGE cycles. Transitioning to standby state.")
                                
                                standby_message_content = (
                                    "[Framework Intervention]: You have completed multiple management cycles without taking concrete action. "
                                    "Your project appears to be in a stable state. You will now transition to standby mode. "
                                    "Output ONLY the following XML: <request_state state='pm_standby'/>"
                                )
                                agent.message_history.append({"role": "system", "content": standby_message_content})
                                
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_intervention",
                                        content=standby_message_content
                                    )
                                
                                # Reset the counter since we're forcing a state change
                                agent._manage_unproductive_cycles = 0
                                context.needs_reactivation_after_cycle = True
                                context.action_taken_this_cycle = True
                                context.cycle_completed_successfully = True
                            else:
                                # Provide specific directive to take action
                                directive_message_content = (
                                    f"[Framework Intervention]: This is your {agent._manage_unproductive_cycles} consecutive management cycle without concrete action. "
                                    "Your MANDATORY next action is to perform Step 1 of your workflow: "
                                    "Use `<project_management><action>list_tasks</action></project_management>` to assess the current project status. "
                                    "Do NOT just think - you must execute this tool call."
                                )
                                agent.message_history.append({"role": "system", "content": directive_message_content})
                                
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_intervention",
                                        content=directive_message_content
                                    )
                                
                                context.needs_reactivation_after_cycle = True
                                context.action_taken_this_cycle = True
                                context.cycle_completed_successfully = True

                    elif event_type in ["response_chunk", "status", "final_response", "invalid_state_request_output"]:
                        if event_type == "final_response":
                            final_content = event.get("content"); original_event_data = event

                            # --- START: Worker Auto-Save File Feature ---
                            if agent.agent_type == AGENT_TYPE_WORKER and final_content and "<request_state state='worker_wait'/>" in final_content:
                                logger.info(f"CycleHandler: Worker '{agent.agent_id}' produced final content. Checking for files to auto-save.")
                                # Regex to find all markdown code blocks
                                code_blocks = re.findall(r"```(?:\w+)?\n(.*?)\n```", final_content, re.DOTALL)
                                saved_files_count = 0
                                for block in code_blocks:
                                    # Regex to find a filename comment, e.g., # file: path/to/file.js or <!-- file: index.html -->
                                    match = re.search(r"^(?:#|//|<!--)\s*file:\s*([\w\-\./_]+)\s*(?:-->)?", block)
                                    if match:
                                        filepath = match.group(1).strip()
                                        # The rest of the block is the content
                                        file_content = block[match.end():].strip()
                                        logger.info(f"CycleHandler: Found file '{filepath}' in worker output. Attempting to save.")
                                        try:
                                            # Use the ToolExecutor to write the file
                                            # Note: This is an internal, framework-level call, so we use a specific agent_id for logging/auth if needed
                                            tool_result = await self._interaction_handler.execute_single_tool(
                                                agent=agent, # Pass the original agent for context
                                                call_id="internal_auto_save",
                                                tool_name="file_system",
                                                tool_args={"action": "write_file", "filepath": filepath, "content": file_content},
                                                project_name=self._manager.current_project,
                                                session_name=self._manager.current_session
                                            )
                                            if tool_result and tool_result.get("status") == "success":
                                                saved_files_count += 1
                                                logger.info(f"CycleHandler: Successfully auto-saved file '{filepath}' for worker '{agent.agent_id}'.")
                                                # Optional: Notify UI about the saved file
                                                await self._manager.send_to_ui({
                                                    "type": "system_notification",
                                                    "agent_id": agent.agent_id,
                                                    "content": f"Framework auto-saved file: {filepath}"
                                                })
                                            else:
                                                logger.error(f"CycleHandler: Failed to auto-save file '{filepath}'. Reason: {tool_result.get('message') if tool_result else 'Unknown error'}")
                                        except Exception as e:
                                            logger.error(f"CycleHandler: Exception during auto-save of file '{filepath}': {e}", exc_info=True)
                                if saved_files_count > 0:
                                    logger.info(f"CycleHandler: Auto-save complete. Saved {saved_files_count} file(s) from worker '{agent.agent_id}' output.")
                            # --- END: Worker Auto-Save File Feature ---

                            if final_content and agent.agent_id != CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                                cg_verdict = await self._get_cg_verdict(final_content)
                                if cg_verdict == "<OK/>":
                                    if context.current_db_session_id and (not agent.message_history or not agent.message_history[-1].get("tool_calls")): await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content)
                                    await self._manager.send_to_ui(original_event_data)
                                else: # CG Concern
                                    agent.cg_original_text = final_content; agent.cg_concern_details = cg_verdict; agent.cg_original_event_data = original_event_data
                                    agent.cg_awaiting_user_decision = True; agent.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG); agent.cg_review_start_time = time.time()
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
                        feedback_content = ("Framework Feedback for PM Retry]\nYour previous output consisted only of a <think> block. In the PM_STATE_STARTUP, you must provide the <task_list> XML structure after your thoughts. Please ensure your entire response includes the XML task list as specified in your instructions.")
                        agent.message_history.append({"role": "system", "content": feedback_content})
                        if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_feedback", content=feedback_content)
                        context.action_taken_this_cycle = True; context.cycle_completed_successfully = False; context.needs_reactivation_after_cycle = True
                        context.last_error_content = "PM startup missing task list after think."
                        await self._manager.send_to_ui({**event, "feedback_provided": True})
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "pm_completion_detection":
                        # Enhanced completion detection - check if project is actually complete
                        context.action_taken_this_cycle = True
                        thinking_content = event.get("thinking_content", "")
                        
                        logger.info(f"CycleHandler: PM '{agent.agent_id}' showing completion thoughts. Triggering project status verification.")
                        
                        # Inject a directive to verify project completion
                        completion_verification_directive = (
                            "[Framework System Message]: You have expressed thoughts about project completion. "
                            "Your MANDATORY next action is to verify the actual project status. "
                            "Use `<project_management><action>list_tasks</action></project_management>` to check for any remaining tasks. "
                            "If no unassigned tasks remain and all work is truly complete, report completion to the Admin AI using: "
                            f"`<send_message><target_agent_id>{BOOTSTRAP_AGENT_ID}</target_agent_id><message_content>Project [PROJECT_NAME] is complete. All tasks have been finished successfully.</message_content></send_message>` "
                            "followed by requesting standby state: `<request_state state='pm_standby'/>`"
                        )
                        
                        agent.message_history.append({"role": "system", "content": completion_verification_directive})
                        
                        if context.current_db_session_id:
                            await self._manager.db_manager.log_interaction(
                                session_id=context.current_db_session_id,
                                agent_id=agent.agent_id,
                                role="system_completion_verification",
                                content=completion_verification_directive
                            )
                        
                        context.needs_reactivation_after_cycle = True
                        context.cycle_completed_successfully = True
                        
                        await self._manager.send_to_ui({
                            "type": "pm_completion_verification_triggered",
                            "agent_id": agent.agent_id,
                            "thinking_content": thinking_content
                        })
                        
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
                                "Your mandatory next action is to get specific instructions for creating an agent. "
                                "Output ONLY the following XML: <tool_information><action>get_info</action><tool_name>manage_team</tool_name><sub_action>create_agent</sub_action></tool_information>"
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

        # Record agent health metrics after cycle completion
        final_output_content = getattr(agent, 'text_buffer', '') or ''
        self._health_monitor.record_agent_cycle(
            agent=agent,
            content=final_output_content,
            has_action=context.action_taken_this_cycle,
            has_thought=context.thought_produced_this_cycle,
            took_meaningful_action=context.executed_tool_successfully_this_cycle or context.state_change_requested_this_cycle
        )

        # Constitutional Guardian Health Intervention Check
        try:
            needs_intervention, problem_desc, recovery_plan = await self._health_monitor.analyze_agent_health(agent)
            if needs_intervention and recovery_plan:
                logger.error(f"CycleHandler: Constitutional Guardian intervening for agent '{agent.agent_id}': {problem_desc}")
                success = await self._health_monitor.execute_recovery_plan(agent, recovery_plan)
                if success:
                    # After successful Constitutional Guardian intervention, schedule immediate reactivation
                    context.needs_reactivation_after_cycle = True
                    logger.error(f"CycleHandler: Constitutional Guardian intervention successful for '{agent.agent_id}', agent will be reactivated")
                    
                    # If this was a critical violation (empty/identical responses), force immediate reactivation
                    violation_types = ["empty_response_violation", "identical_response_violation"]
                    if recovery_plan.get("type") in violation_types:
                        # Override normal scheduling to ensure immediate reactivation
                        await self._manager.schedule_cycle(agent, retry_count=0)
                        logger.error(f"CycleHandler: CRITICAL VIOLATION - Immediately reactivating agent '{agent.agent_id}' after Constitutional Guardian intervention")
        except Exception as health_error:
            logger.error(f"CycleHandler: Error during Constitutional Guardian health monitoring for '{agent.agent_id}': {health_error}", exc_info=True)

        if not context.is_provider_level_error:
            success_for_metrics = context.cycle_completed_successfully and not context.is_key_related_error
            await self._manager.performance_tracker.record_call(
                provider=context.current_provider_name or "unknown", model_id=context.current_model_name or "unknown",
                duration_ms=context.llm_call_duration_ms, success=success_for_metrics
            )

        await self._next_step_scheduler.schedule_next_step(context)
        
        # Report tool execution stats periodically
        if self._tool_execution_stats["total_calls"] % 10 == 0 and self._tool_execution_stats["total_calls"] > 0:
            self._report_tool_execution_stats()
            
        logger.info(f"CycleHandler: Finished cycle logic for Agent '{agent.agent_id}'. Final status for this attempt: {agent.status}. State: {agent.state}")
