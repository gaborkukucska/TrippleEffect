# START OF FILE src/agents/cycle_handler.py
import asyncio
import json
import logging
import time
import re
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple

from src.llm_providers.base import ToolResultDict, MessageDict  # type: ignore[import]
from src.agents.core import Agent  # type: ignore[import]
from src.config.settings import settings  # type: ignore[import]

from src.agents.constants import (  # type: ignore[import]
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_AWAITING_CG_REVIEW, AGENT_STATUS_AWAITING_USER_REVIEW_CG, # Added for CG
    AGENT_STATUS_ERROR, AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_PLANNING, ADMIN_STATE_CONVERSATION, ADMIN_STATE_STARTUP, ADMIN_STATE_WORK, ADMIN_STATE_STANDBY,
    PM_STATE_STARTUP, PM_STATE_MANAGE, PM_STATE_WORK, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS, PM_STATE_STANDBY,
    PM_STATE_REPORT_CHECK,
    WORKER_STATE_WAIT, WORKER_STATE_WORK, WORKER_STATE_REPORT, WORKER_STATE_DECOMPOSE,
    REQUEST_STATE_TAG_PATTERN,
    CONSTITUTIONAL_GUARDIAN_AGENT_ID, # Added for CG
    BOOTSTRAP_AGENT_ID
)

# Import for keyword extraction
from src.utils.text_utils import extract_keywords_from_text  # type: ignore[import]
from src.tools.knowledge_base import KnowledgeBaseTool  # type: ignore[import]
from src.tools.error_handler import tool_error_handler, ErrorType  # type: ignore[import]

from src.agents.cycle_components import (  # type: ignore[import]
    CycleContext,
    PromptAssembler,
    LLMCaller, 
    CycleOutcomeDeterminer,
    NextStepScheduler,
    AgentHealthMonitor
)
from src.agents.cycle_components.xml_validator import XMLValidator  # type: ignore[import]
from src.agents.cycle_components.context_summarizer import ContextSummarizer  # type: ignore[import]

from src.workflows.base import WorkflowResult  # type: ignore[import]

if TYPE_CHECKING:
    from src.agents.manager import AgentManager  # type: ignore[import]
    from src.agents.interaction_handler import AgentInteractionHandler  # type: ignore[import]


import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class AgentCycleHandler:
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager: Any = manager
        self._interaction_handler: Any = interaction_handler
        
        self._prompt_assembler: Any = PromptAssembler(self._manager)
        self._outcome_determiner: Any = CycleOutcomeDeterminer()
        self._next_step_scheduler: Any = NextStepScheduler(self._manager)
        self._xml_validator: Any = XMLValidator()
        self._context_summarizer: Any = ContextSummarizer(self._manager)
        self._health_monitor: Any = AgentHealthMonitor(self._manager)
        
        self.request_state_pattern: Any = REQUEST_STATE_TAG_PATTERN 
        self._tool_execution_stats: Dict[str, int] = {"total_calls": 0, "successful_calls": 0, "failed_calls": 0}
        logger.info("AgentCycleHandler initialized with enhanced tool execution monitoring, XML validation, context summarization, and agent health monitoring.")

    def _handle_worker_task_tracking(self, agent: 'Agent', requested_state: str, task_id: Optional[str] = None) -> None:
        """
        When a worker transitions to worker_work with a task_id, automatically:
        1. Set agent.active_task_id so the UI can display it.
        2. Update the Taskwarrior database to mark the task as 'doing'.
        """
        if agent.agent_type != AGENT_TYPE_WORKER:
            return
        resolved = self._manager.workflow_manager.resolve_state_alias(agent.agent_type, requested_state)
        if resolved != WORKER_STATE_WORK:
            return

        if not task_id:
            raise ValueError(f"You MUST specify a 'task_id' parameter when transitioning to '{WORKER_STATE_WORK}' state.\nIf you do not know your task_id:\n1. Check previous tool responses if you just created the task.\n2. Otherwise, use the project_management tool with action='list_tasks' to find the correct ID.\nThen retry with the attribute: <request_state state='worker_work' task_id='THE_ID'/>.")

        agent.active_task_id = task_id
        logger.info(f"CycleHandler: Worker '{agent.agent_id}' set active_task_id='{task_id}' for work state transition.")

        # Auto-update Taskwarrior progress to doing
        try:
            if self._manager.current_project and self._manager.current_session:
                from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
                if TASKLIB_AVAILABLE:
                    pm_tool = ProjectManagementTool()
                    tw = pm_tool._get_taskwarrior_instance(self._manager.current_project, self._manager.current_session)
                    if tw:
                        try:
                            # Resolve aliases (workers use short IDs like "15", not UUIDs)
                            aliases = pm_tool._load_aliases(self._manager.current_project, self._manager.current_session)
                            resolved_id = str(task_id).strip()
                            if resolved_id in aliases:
                                resolved_id = aliases[resolved_id]
                            
                            # Look up task by UUID or numeric ID
                            if '-' in resolved_id and len(resolved_id) > 10:
                                task = tw.tasks.get(uuid=resolved_id)
                            elif resolved_id.isdigit():
                                task = tw.tasks.get(id=int(resolved_id))
                            else:
                                task = None
                            
                            if task:
                                # CRITICAL FIX: Prevent worker from selecting a task that is already decomposed
                                # tasklib Task uses bracket access, not .get()
                                try:
                                    current_progress = task['task_progress']
                                except (KeyError, AttributeError):
                                    current_progress = None
                                    
                                if current_progress == 'decomposed':
                                    raise ValueError(f"Task '{task_id}' has already been marked as 'decomposed'. You MUST select one of the new sub-tasks you created, NOT the parent task.")
                                    
                                task['task_progress'] = 'doing'
                                task.save()
                                logger.info(f"CycleHandler: Auto-updated task '{task_id}' (resolved: '{resolved_id}') to 'doing' for worker '{agent.agent_id}'.")
                                # Auto-trigger UI refresh for tasks
                                try:
                                    import asyncio
                                    import json
                                    from src.api.websocket_manager import broadcast
                                    try:
                                        asyncio.get_running_loop().create_task(
                                            broadcast(json.dumps({
                                                "type": "project_tasks_updated",
                                                "project_name": self._manager.current_project,
                                                "session_name": self._manager.current_session
                                            }))
                                        )
                                    except RuntimeError:
                                        pass # Not in an event loop, should rarely happen here
                                except Exception as e:
                                    logger.warning(f"CycleHandler: Failed to trigger UI task refresh: {e}")
                            else:
                                logger.warning(f"CycleHandler: Could not find task '{task_id}' (resolved: '{resolved_id}') in Taskwarrior for auto-update.")
                        except ValueError:
                            raise # Re-raise our validation error
                        except Exception as tw_err:
                            logger.warning(f"CycleHandler: Failed to auto-update task '{task_id}': {tw_err}")
        except ValueError:
            raise # Re-raise validation error
        except Exception as e:
            logger.warning(f"CycleHandler: Error auto-updating task '{task_id}' to in_progress: {e}")

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

    async def _get_cg_verdict(self, agent, original_agent_final_text: str) -> Optional[str]:
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
                formatted_cg_system_prompt = cg_prompt_template.format(
                    governance_principles_text=governance_text,
                    team_wip_updates=self._manager.workflow_manager._build_team_wip_updates(agent, self._manager)
                )
                cg_history: List[MessageDict] = [
                    {"role": "system", "content": formatted_cg_system_prompt},
                    {"role": "system", "content": f"---\nText for Constitutional Review:\n---\n{original_agent_final_text}"}
                ]
                max_tokens_for_verdict = getattr(settings, 'CG_MAX_TOKENS', 4000)

                try: # Inner try for LLM call and parsing (original try...except block content)
                    from contextlib import aclosing
                    
                    logger.info(f"Requesting CG verdict via stream_completion for text: '{original_agent_final_text[:100]}...'")
                    async with aclosing(cg_agent.llm_provider.stream_completion(
                        messages=cg_history, model=cg_agent.model,
                        temperature=cg_agent.temperature, max_tokens=max_tokens_for_verdict
                    )) as stream:
                        full_verdict_text = ""
                        async for event in stream:
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
                            else: # Empty stripped_verdict — fail-open to prevent indefinite agent stalls
                                logger.warning("CG returned empty verdict. Failing open (treating as <OK/>) to prevent agent stall.")
                                verdict_to_return = OK_TAG
                except Exception as eval_e:
                    logger.error(f"Error during Constitutional Guardian evaluation: {eval_e}", exc_info=True)
                    verdict_to_return = "<OK/>" # Fail-open in case of evaluation error

        except Exception as outer_e:
            logger.error(f"Critical error setting up Constitutional Guardian evaluation: {outer_e}", exc_info=True)
            verdict_to_return = "<OK/>" # Fail-open in case of critical setup error

        finally: # Outer finally
            if cg_agent:
                final_status_to_set = original_cg_status if original_cg_status is not None else AGENT_STATUS_IDLE
                cg_agent.set_status(final_status_to_set)
                if hasattr(self._manager, 'push_agent_status_update'):
                    await self._manager.push_agent_status_update(CONSTITUTIONAL_GUARDIAN_AGENT_ID)
                else:
                    logger.error("AgentManager instance not found or lacks push_agent_status_update, cannot revert CG status for UI.")

        # Ensure verdict_to_return is ALWAYS assigned before returning
        if verdict_to_return is None:
            logger.error("verdict_to_return was None at the end of CG evaluation. Defaulting to fail-open (<OK/>).")
            verdict_to_return = "<OK/>"

        logger.info(f"Final CG evaluation verdict being returned: '{verdict_to_return[:50]}...'")
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

            if last_tool_call and isinstance(last_tool_call, dict):
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
            "**CRITICAL REMINDERS:**\n"
            "1. Execute ONLY ONE tool call per response to avoid overwhelming yourself\n"
            "2. DO NOT repeat the same tool with identical parameters\n"
            "3. After testing a tool, provide a brief summary of what you learned\n"
            "4. When you have completed testing, transition to conversation state with: <request_state state='conversation'/>\n\n"
            f"**For context, here is a summary of tools currently available to you:**\n{tool_list_str}"
        )
        return task_message


    def _has_too_many_recent_system_messages(self, agent: Agent, threshold: int = 3, lookback: int = 5) -> bool:
        """Check if too many recent system messages would flood the LLM context."""
        if not agent.message_history:
            return False
        recent = agent.message_history[-lookback:]
        system_count = sum(1 for m in recent if m.get("role") == "system")
        return system_count >= threshold

    def _deduplicate_pm_framework_messages(self, agent: Agent) -> None:
        """
        Remove old [Framework System Message] entries from PM agent history,
        keeping only the most recent one. This prevents message accumulation
        that confuses the LLM into re-executing already-completed actions
        (e.g. creating duplicate agents for the same role).

        Matches all framework message variants:
        - [Framework System Message]
        - [Framework System Message - DUPLICATE BLOCKED]
        - [Framework System Message - AUTO-ADVANCE]

        NOTE: Also catches role='user' messages starting with these prefixes,
        because pm_kickoff_workflow injects the kickoff plan directive as a
        'user' message starting with '[Framework System Message]'. Without this,
        that message persists across cycles and appears duplicated alongside the
        newer 'system' role framework messages added by cycle_handler.
        """
        if not agent.message_history:
            return

        # Use a broad prefix to catch all variants:
        # "[Framework System Message]", "[Framework System Message - DUPLICATE BLOCKED]", etc.
        # Also catch Constitutional Guardian messages to prevent them from accumulating
        FRAMEWORK_MSG_PREFIXES = ["[Framework System Message", "[Constitutional Guardian"]
        # Find indices of all framework and CG messages (both 'system' and 'user' roles)
        framework_msg_indices: List[int] = []
        for i, msg in enumerate(agent.message_history):
            role = msg.get("role")
            if role in ("system", "user"):  # Check both roles — kickoff directive uses 'user'
                content = msg.get("content", "")
                if any(content.startswith(prefix) for prefix in FRAMEWORK_MSG_PREFIXES):
                    # DO NOT delete if it contains the Master Kickoff Plan or Tool Usage Info
                    if "**MASTER KICKOFF PLAN SUMMARY**" in content or "**create_agent Tool Usage:**" in content:
                        continue
                    framework_msg_indices.append(i)

        # Remove ALL previous framework messages, because a new,
        # updated one will be injected immediately after this is called.
        if len(framework_msg_indices) > 0:
            indices_to_remove = set(framework_msg_indices)
            original_len = len(agent.message_history)
            agent.message_history = [
                msg for i, msg in enumerate(agent.message_history)
                if i not in indices_to_remove
            ]
            removed_count = original_len - len(agent.message_history)
            logger.info(
                f"CycleHandler: Deduplicated PM '{agent.agent_id}' history - "
                f"removed {removed_count} old framework messages, "
                f"history reduced from {original_len} to {len(agent.message_history)} messages."
            )

    def _detect_cross_cycle_duplicate_tool_call(self, agent: Agent, tool_name: str, tool_args: Dict[str, Any]) -> Optional[str]:
        """
        Detect if a tool call with the same name and arguments already succeeded
        in a previous cycle (i.e., it exists as an assistant+tool pair in history).
        
        Returns the content of the previous successful tool result if a duplicate
        is found, or None if this is a fresh call.
        """
        if not agent.message_history:
            return None
        
        # Create signature for the current call
        current_sig = (tool_name, json.dumps(tool_args, sort_keys=True))
        
        def _is_equivalent(sig1: Tuple[str, str], sig2: Tuple[str, str]) -> bool:
            if sig1 == sig2:
                return True
            tname1, targs1_json = sig1
            tname2, targs2_json = sig2
            if tname1 != tname2:
                return False
            if tname1 == "manage_team":
                try:
                    args1 = json.loads(targs1_json)
                    args2 = json.loads(targs2_json)
                    if args1.get("action") == "create_agent" and args2.get("action") == "create_agent":
                        r1 = args1.get("role")
                        r2 = args2.get("role")
                        if r1 and r2 and str(r1).strip().lower() == str(r2).strip().lower():
                            return True
                except Exception:
                    pass
            return False

        # Scan history in reverse for assistant messages with matching tool calls
        for i in range(len(agent.message_history) - 1, -1, -1):
            msg = agent.message_history[i]
            if msg.get("role") != "assistant":
                continue
            
            for tc in msg.get("tool_calls", []):
                prev_sig = (tc.get("name"), json.dumps(tc.get("arguments", {}), sort_keys=True))
                if _is_equivalent(prev_sig, current_sig):
                    # Found a matching call - now look for its successful tool result
                    tc_id = tc.get("id")
                    for j in range(i + 1, len(agent.message_history)):
                        result_msg = agent.message_history[j]
                        if (result_msg.get("role") == "tool" and
                            result_msg.get("tool_call_id") == tc_id):
                            result_content = result_msg.get("content", "")
                            # Check if the result was successful
                            try:
                                result_data = json.loads(result_content)
                                if isinstance(result_data, dict) and result_data.get("status") == "error":
                                    continue  # Previous call failed, allow retry
                            except (json.JSONDecodeError, TypeError):
                                pass
                            
                            # Also check for plain text error markers often used by native framework tools
                            if isinstance(result_content, str):
                                if (
                                    "[Tool Execution Failed]" in result_content or 
                                    "[Framework Error]" in result_content or 
                                    "[Error]" in result_content or
                                    result_content.strip() == ""
                                ):
                                    continue # Previous call essentially failed, allow retry
                            
                            return result_content  # Found a successful duplicate
        
        return None

    async def run_cycle(self, agent: Agent, retry_count: int = 0): # pyright: ignore[reportGeneralTypeIssues]
        logger.critical(f"!!! CycleHandler: run_cycle TASK STARTED for Agent '{agent.agent_id}' (Retry: {retry_count}) !!!")
        
        # --- NEW: Watchdog for Agent Stalls (cycles without state transitions) ---
        if not hasattr(agent, '_cycles_without_transition'):
            agent._cycles_without_transition = 0
            agent._last_state_for_watchdog = agent.state

        if agent.state == agent._last_state_for_watchdog:
            agent._cycles_without_transition += 1
        else:
            agent._cycles_without_transition = 0
            agent._last_state_for_watchdog = agent.state

        if agent._cycles_without_transition >= 10:
            logger.warning(f"Watchdog: Agent '{agent.agent_id}' has been running for {agent._cycles_without_transition} cycles without changing from state '{agent.state}'. Flagging for intervention.")
            if agent._cycles_without_transition % 5 == 0:  # Inject every 5 cycles after hitting 10
                intervention_msg = f"[Framework Watchdog Intervention]: You have been in the '{agent.state}' state for {agent._cycles_without_transition} cycles without transitioning. If you are stuck or have completed your work, please forcefully transition your state using <request_state state='worker_wait' /> or the appropriate state for your role."
                agent.message_history.append({"role": "system", "content": intervention_msg})
        # -------------------------------------------------------------------------
        
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
            agent._failed_models_this_cycle = {context.current_model_key_for_tracking}

            agent_generator = None # Ensure generator is reset for each iteration of the while True loop
            thought_content_for_history = None # <<<< MODIFICATION: Initialize var to hold thought
            cycle_text_content = "" # <<<< FIX: Track text buffer before it gets cleared

            try: # This try block is for one pass of LLM call and its event processing
                await self._prompt_assembler.prepare_llm_call_data(context) # Ensures history_for_call is fresh

                # (Proactive start-of-cycle deduplication was removed from here because it
                # prematurely erased the tool usage instructions and kickoff plan generated
                # in the previous turn before the LLM could read them.)
                
                # Check if context summarization is needed for small local LLMs
                try:
                    # Estimate current token count
                    estimated_tokens = self._context_summarizer.estimate_token_count(context.history_for_call)
                    # Get max tokens from agent's LLM provider (default to 8000 if not available)
                    max_tokens = getattr(agent.llm_provider, 'max_tokens', 8000) if agent.llm_provider else 8000
                    
                    # Look up model's native num_ctx for dynamic threshold
                    model_num_ctx = None
                    if hasattr(self._manager, 'model_registry') and self._manager.model_registry:
                        _model_info = self._manager.model_registry.get_model_info(agent.model)
                        if _model_info:
                            model_num_ctx = _model_info.get('model_num_ctx')
                    
                    if await self._context_summarizer.should_summarize_context(agent.agent_id, estimated_tokens, max_tokens, message_history=context.history_for_call, model_num_ctx=model_num_ctx):
                        logger.info(f"CycleHandler: Context summarization needed for agent '{agent.agent_id}' due to token limits")
                        try:
                            # CRITICAL FIX: Preserve context anchors for Admin AI work state
                            context_anchors = []
                            if agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_WORK:
                                # Preserve the original task description and recent meaningful interactions
                                for msg in reversed(agent.message_history[-10:]):  # Look at last 10 messages
                                    if msg.get("role") == "user" or (msg.get("role") == "system" and "current task" in msg.get("content", "").lower()):
                                        context_anchors.append(msg)
                                        if len(context_anchors) >= 3:  # Preserve up to 3 anchor messages
                                            break
                                context_anchors.reverse()  # Restore chronological order
                                logger.info(f"CycleHandler: Preserved {len(context_anchors)} context anchors for Admin AI '{agent.agent_id}'")
                            
                            success, summarized_context = await self._context_summarizer.summarize_agent_context(
                                agent.agent_id, context.history_for_call
                            )
                            if success and summarized_context:
                                summarized_context_list: List[Any] = list(summarized_context)
                                # CRITICAL FIX: Insert context anchors into summarized context
                                if context_anchors:
                                    # Insert anchors at the appropriate position (before the last few messages)
                                    insert_point = max(1, len(summarized_context_list) - 3)  # Keep system prompt at start
                                    for i, anchor in enumerate(context_anchors):
                                        summarized_context_list.insert(insert_point + i, anchor)
                                    logger.info(f"CycleHandler: Inserted {len(context_anchors)} context anchors into summarized context")
                                
                                context.history_for_call = summarized_context_list
                                # CRITICAL FIX: Also update the agent's persistent message history with anchors
                                agent.message_history = list(summarized_context_list)
                                logger.info(f"CycleHandler: Context successfully summarized for agent '{agent.agent_id}', reduced to {len(summarized_context_list)} messages")
                                logger.critical(f"CycleHandler: PERSISTENT HISTORY UPDATED for agent '{agent.agent_id}' - new persistent length: {len(agent.message_history)}")
                                
                                # Notify UI about context summarization
                                await self._manager.send_to_ui({
                                    "type": "context_summarization",
                                    "agent_id": agent.agent_id,
                                    "original_message_count": len(agent.message_history),
                                    "summarized_message_count": len(summarized_context_list),
                                    "context_anchors_preserved": len(context_anchors),
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
                    current_buffer = getattr(agent, 'text_buffer', '')
                    if current_buffer:
                        cycle_text_content = current_buffer
                        
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

                        # (Framework Notification for Admin AI was removed to prevent redundant greetings)

                        if workflow_result.tasks_to_schedule:
                            for task_agent, task_retry_count in workflow_result.tasks_to_schedule:
                                if task_agent and isinstance(task_agent, Agent):
                                    if task_agent.agent_id == agent.agent_id:
                                        # CRITICAL FIX: Do NOT call schedule_cycle for the current agent inline.
                                        # Doing so fires a concurrent asyncio task that starts a new LLM call
                                        # while this cycle's finally block hasn't run yet, causing DOUBLE LLM calls.
                                        # Instead, defer to next_step_scheduler by setting the reactivation flag.
                                        logger.info(
                                            f"CycleHandler '{agent.agent_id}': Workflow '{workflow_result.workflow_name}' "
                                            f"requests re-scheduling of SELF. Deferring to NextStepScheduler "
                                            f"(setting needs_reactivation_after_cycle=True) to prevent concurrent LLM calls."
                                        )
                                        context.needs_reactivation_after_cycle = True
                                    else:
                                        # For OTHER agents, scheduling inline is safe — they run independently.
                                        await self._manager.schedule_cycle(task_agent, task_retry_count)
                                else: logger.warning(f"CycleHandler '{agent.agent_id}': Workflow '{workflow_result.workflow_name}' invalid agent schedule request.")
                        if workflow_result.success:
                            context.cycle_completed_successfully = True
                            # needs_reactivation_after_cycle may already be True from self-scheduling above.
                            # Only override if it wasn't already set for self-rescheduling.
                            if not context.needs_reactivation_after_cycle:
                                context.needs_reactivation_after_cycle = bool(workflow_result.next_agent_state or workflow_result.tasks_to_schedule)

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
                        
                        # CRITICAL FIX: Enhanced XML error feedback deduplication to prevent accumulation
                        # CRITICAL FIX: Enhanced XML error feedback deduplication to prevent accumulation
                        # Create broader error signatures to catch similar XML errors
                        safe_parsing_msg = parsing_error_msg or ""
                        base_error_signature = f"xml_error_{malformed_tool_name}"
                        detailed_error_signature = f"malformed_{malformed_tool_name}_{safe_parsing_msg[:30]}"
                        
                        if not hasattr(agent, '_recent_error_feedback'):
                            agent._recent_error_feedback = {}
                        
                        # CRITICAL: Check for any recent XML errors for this tool (much shorter timeframe)
                        current_time = time.time()
                        has_recent_error = False
                        
                        # Check if we've given feedback for this tool recently (last 30 seconds)
                        for error_key, error_time in agent._recent_error_feedback.items():
                            if (error_key.startswith(f"xml_error_{malformed_tool_name}") or 
                                error_key.startswith(f"malformed_{malformed_tool_name}")) and \
                               (current_time - error_time) < 30:  # Only 30 seconds
                                has_recent_error = True
                                break
                        
                        # CRITICAL: Also check if we have too many XML error messages in recent history
                        xml_error_messages_in_recent_history: int = 0
                        if hasattr(agent, 'message_history') and agent.message_history:
                            for msg in agent.message_history[-5:]:  # Check last 5 messages
                                if (msg.get('role') == 'system' and 
                                    'XML Parsing Error' in msg.get('content', '')):
                                    xml_error_messages_in_recent_history += 1
                        
                        # Only provide feedback if ALL conditions are met
                        should_provide_feedback = (
                            not has_recent_error and 
                            xml_error_messages_in_recent_history < 2  # Max 2 XML error messages in recent history
                        )
                        
                        if should_provide_feedback:
                            detailed_tool_usage = "Could not retrieve detailed usage for this tool."
                            if malformed_tool_name and malformed_tool_name in self._manager.tool_executor.tools:
                                try: 
                                    detailed_tool_usage = self._manager.tool_executor.tools[malformed_tool_name].get_detailed_usage()
                                except Exception as usage_exc: 
                                    logger.error(f"Failed to get detailed usage for tool {malformed_tool_name}: {usage_exc}")
                            
                            # CRITICAL FIX: Removed enhanced feedback for markdown fences, as the parser already strips them.
                            if "list_tools" in parsing_error_msg:
                                feedback_to_agent = (f"[Framework Feedback: Tool Usage Error]\n"
                                                   f"You attempted to use '{malformed_tool_name}' with tool_name='list_tools', but 'list_tools' is not a tool name - it's an action.\n"
                                                   f"Correct usage: <tool_information><action>list_tools</action></tool_information>\n"
                                                   f"This will list all available tools and their summaries.")
                            elif malformed_tool_name == "unknown":
                                feedback_to_agent = (f"[Framework Feedback: XML Parsing Error]\n"
                                                   f"Your tool call was malformed: {parsing_error_msg}\n"
                                                   f"Please check your syntax. You must specify a valid tool name from your system instructions.")
                            else:
                                feedback_to_agent = (f"[Framework Feedback: XML Parsing Error]\n"
                                                   f"Your XML for '{malformed_tool_name}' was malformed: {parsing_error_msg}\n"
                                                   f"Please check your XML syntax.\n\n"
                                                   f"Correct usage for '{malformed_tool_name}':\n{detailed_tool_usage}")
                            
                            agent.message_history.append({"role": "system", "content": feedback_to_agent})
                            agent._recent_error_feedback[base_error_signature] = time.time()  # Record when we provided this feedback
                            
                            if context.current_db_session_id: 
                                await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id,agent_id=agent.agent_id,role="system_error_feedback",content=feedback_to_agent)
                                
                            await self._manager.send_to_ui({"type": "system_error_feedback","agent_id": agent.agent_id,"tool_name": malformed_tool_name,"error_message": parsing_error_msg,"content": feedback_to_agent,"detailed_usage": detailed_tool_usage,"original_attempt": raw_llm_response_with_error})
                            
                            logger.info(f"CycleHandler: Provided XML error feedback to '{agent.agent_id}' for error pattern: {base_error_signature}")
                        else:
                            # CRITICAL FIX: If we're skipping feedback due to accumulation, aggressively clean existing XML errors  
                            logger.warning(f"CycleHandler: XML error feedback spam detected for '{agent.agent_id}' - triggering history cleanup")
                            
                            # Remove excessive XML error messages from history to prevent overwhelming the AI
                            if hasattr(agent, 'message_history') and agent.message_history:
                                original_length = len(agent.message_history)
                                cleaned_history = []
                                xml_error_count: int = 0
                                
                                for msg in agent.message_history:
                                    # Count and filter XML error messages
                                    if (msg.get('role') == 'system' and 
                                        'XML Parsing Error' in msg.get('content', '')):
                                        xml_error_count += 1
                                        # Only keep the most recent XML error message
                                        if xml_error_count <= 1:
                                            cleaned_history.append(msg)
                                        else:
                                            logger.info(f"CycleHandler: Removing duplicate XML error message #{xml_error_count}")
                                    else:
                                        cleaned_history.append(msg)
                                
                                if len(cleaned_history) != original_length:
                                    agent.message_history = cleaned_history
                                    logger.critical(f"CycleHandler: CLEANED XML error spam - reduced history from {original_length} to {len(cleaned_history)} messages for agent '{agent.agent_id}'")
                                    
                                    # Force database update
                                    if context.current_db_session_id:
                                        try:
                                            await self._manager.db_manager.log_interaction(
                                                session_id=context.current_db_session_id,
                                                agent_id=agent.agent_id,
                                                role="system_cleanup",
                                                content=f"XML error spam cleanup: Removed {original_length - len(cleaned_history)} duplicate error messages"
                                            )
                                        except Exception as db_err:
                                            logger.error(f"CycleHandler: Failed to log cleanup to DB: {db_err}")
                            
                            logger.info(f"CycleHandler: Skipped duplicate XML error feedback for '{agent.agent_id}' - error pattern seen recently: {base_error_signature}")
                        
                        context.needs_reactivation_after_cycle = True; context.last_error_content = f"Malformed XML for tool '{malformed_tool_name}'"; context.cycle_completed_successfully = False
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "agent_thought":
                        context.action_taken_this_cycle = True
                        context.thought_produced_this_cycle = True
                        thought_content = event.get("content")
                        if thought_content:
                            # <<<< MODIFICATION: Capture the thought for message history
                            thought_content_for_history = f"<think>{thought_content}</think>"
                            if context.current_db_session_id:
                                await self._manager.db_manager.log_interaction(
                                    session_id=context.current_db_session_id,
                                    agent_id=agent.agent_id,
                                    role="assistant_thought",
                                    content=thought_content
                                )
                            await self._manager.send_to_ui(event)
                            # Knowledge base saving logic could go here as well

                    elif event_type == "agent_raw_response":
                        # Forward raw agent responses to the UI for display in Internal Comms
                        raw_content = event.get("content", "")
                        
                        # --- START ADMIN VERIFICATION HARD GATE ---
                        if agent.agent_id == BOOTSTRAP_AGENT_ID and raw_content:
                            lower_content = raw_content.lower()
                            if ("complete" in lower_content or "finished" in lower_content or "done" in lower_content) and "project" in lower_content:
                                is_actually_complete = True
                                pending_count = 0
                                try:
                                    if self._manager.current_project and self._manager.current_session:
                                        from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
                                        if TASKLIB_AVAILABLE:
                                            pm_tool = ProjectManagementTool()
                                            tw = pm_tool._get_taskwarrior_instance(self._manager.current_project, self._manager.current_session)
                                            if tw:
                                                active_tasks = tw.tasks.filter(status='pending')
                                                pending_count = len(active_tasks)
                                                if pending_count > 0:
                                                    is_actually_complete = False
                                except Exception as e:
                                    logger.warning(f"CycleHandler: Failed to verify task completion for Admin AI hard gate: {e}")
                                
                                if not is_actually_complete:
                                    logger.warning(f"CycleHandler: HARD GATE ACTIVATED. Admin AI attempted to report completion while {pending_count} tasks are pending.")
                                    # Inject a framework message and rewrite the UI payload
                                    system_message = (
                                        "[Framework System Message - HARD GATE BLOCK]: Your response was BLOCKED from reaching the user. "
                                        f"You claimed the project was complete, but the backend database shows {pending_count} PENDING tasks. "
                                        "You MUST transition to 'admin_work' and use <project_management><action>list_tasks</action></project_management> to verify the actual task status, "
                                        "or ping the PM for an explanation. Do not falsely report completion."
                                    )
                                    agent.message_history.append({"role": "system", "content": system_message})
                                    raw_content = f"[Framework Blocked false completion claim from Admin! {pending_count} tasks are still pending. Forcing Admin audit...]"
                                    event["content"] = raw_content
                        # --- END ADMIN VERIFICATION HARD GATE ---

                        if raw_content:
                            await self._manager.send_to_ui(event)
                            logger.debug(f"CycleHandler '{agent.agent_id}': Forwarded agent_raw_response to UI")

                    elif event_type == "agent_state_change_requested":
                        context.action_taken_this_cycle = True
                        context.state_change_requested_this_cycle = True
                        requested_state = event.get("requested_state")
                        task_description = event.get("task_description")
                        event_task_id = event.get("task_id")

                        # Auto-track worker task when transitioning to work state
                        try:
                            self._handle_worker_task_tracking(agent, requested_state, event_task_id)
                        except ValueError as ve:
                            logger.warning(f"CycleHandler: State transition blocked for '{agent.agent_id}': {ve}")
                            err_msg = f"[Framework Error]: State transition rejected: {ve}"
                            agent.message_history.append({"role": "system", "content": err_msg})
                            context.needs_reactivation_after_cycle = True
                            continue # Stop processing this event; do NOT apply state change

                        # Record the agent's action in its history so it knows it made the request
                        history_content = f"<request_state state='{requested_state}'"
                        if event_task_id:
                            history_content += f" task_id='{event_task_id}'"
                        if task_description:
                            # optional if needed, usually not logged but good to keep it exact
                            history_content += f" task_description='{task_description}'"
                        history_content += "/>"
                        
                        agent.message_history.append({
                            "role": "assistant",
                            "content": history_content
                        })

                        # <<< --- ROBUST FIX START --- >>>
                        # If transitioning to work state with no task, find the last user message.
                        if requested_state == ADMIN_STATE_WORK and (not task_description or task_description.isspace()):
                            logger.warning(f"CycleHandler: Admin AI '{agent.agent_id}' state change to '{ADMIN_STATE_WORK}' has no task description. Searching history for last user message.")
                            found_task = False
                            for msg in reversed(agent.message_history):
                                if msg.get("role") == "user":
                                    task_description = msg.get("content") or ""
                                    logger.info(f"CycleHandler: Found last user message and set as task description: '{str(task_description)[:100]}...'")
                                    found_task = True
                                    break
                            if not found_task:
                                logger.error(f"CycleHandler: Could not find a previous user message to use as task description for state change to '{ADMIN_STATE_WORK}'. The agent might loop.")
                        # <<< --- ROBUST FIX END --- >>>
                        # Removing the interception logic that prevented PM from going to standby.
                        # The PM MUST be allowed to go to standby while workers are active to avoid busy-waiting loops.

                        state_change_success = self._manager.workflow_manager.change_state(agent, requested_state, task_description=task_description)
                        if state_change_success:
                            # Reactivate unless transitioning to an idle state
                            idle_states = [PM_STATE_STANDBY, ADMIN_STATE_CONVERSATION, ADMIN_STATE_STANDBY, WORKER_STATE_WAIT]
                            
                            # Resolve alias just to be safe
                            resolved_requested = self._manager.workflow_manager.resolve_state_alias(agent.agent_type, requested_state)
                            
                            if resolved_requested in idle_states:
                                context.needs_reactivation_after_cycle = False
                                logger.info(f"CycleHandler: Agent '{agent.agent_id}' transitioned to idle state '{resolved_requested}'. Not reactivating.")
                            else:
                                context.needs_reactivation_after_cycle = True
                            
                            # CRITICAL FIX: For Admin AI work state, inject task reminder to prevent infinite loops
                            if agent.agent_type == AGENT_TYPE_ADMIN and requested_state == ADMIN_STATE_WORK and task_description:
                                task_reminder_message = (
                                    f"[Framework Context]: You are now in work state. Your current task is: {task_description[:200]}{'...' if len(task_description) > 200 else ''}\n\n"
                                    "Work systematically towards completing this specific task. Avoid repeating the same tool calls. "
                                    "When you have made sufficient progress or completed the task, provide a comprehensive summary "
                                    "and request state change back to 'conversation'."
                                )
                                agent.message_history.append({"role": "system", "content": task_reminder_message})
                                logger.info(f"CycleHandler: Injected task reminder for Admin AI '{agent.agent_id}' work state transition")
                                
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_task_context",
                                        content=task_reminder_message
                                    )
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
                            # change_state returned False. Check if it's already in the requested state.
                            resolved_requested = self._manager.workflow_manager.resolve_state_alias(agent.agent_type, requested_state)
                            if agent.state == resolved_requested:
                                # Agent is already in the requested state.
                                # Determine if this is a terminal/idle state where re-requesting is expected behavior.
                                TERMINAL_IDLE_STATES = {"worker_wait", "pm_standby", "admin_standby"}
                                if resolved_requested in TERMINAL_IDLE_STATES:
                                    # Agent is correctly waiting — do NOT reactivate or inject a confusing directive.
                                    # This prevents infinite loops where the agent keeps requesting its own state.
                                    logger.info(
                                        f"CycleHandler: Agent '{agent.agent_id}' re-requested idle state "
                                        f"'{resolved_requested}'. This is correct behavior — agent will remain idle "
                                        f"until the framework reactivates it."
                                    )
                                    context.needs_reactivation_after_cycle = False
                                    # Don't append any message — the agent should just go to sleep
                                else:
                                    # Non-terminal state (e.g. worker_work, pm_manage) — reactivate with reminder
                                    logger.info(f"CycleHandler: Agent '{agent.agent_id}' is already in state '{resolved_requested}'. Reactivating with reminder.")
                                    context.needs_reactivation_after_cycle = True
                                    agent.message_history.append({
                                        "role": "system",
                                        "content": f"[Framework Directive]: You requested to change to '{requested_state}', but you are already in this state. Please proceed with executing tools to fulfill your current goal."
                                    })
                            else:
                                # Truly invalid state transition. Let it reactivate to correct itself (with an error message).
                                context.needs_reactivation_after_cycle = True
                                invalid_msg = f"[Framework Error]: Invalid state requested: '{requested_state}'. Please check valid states for your role."
                                agent.message_history.append({"role": "system", "content": invalid_msg})

                        if state_change_success:
                            if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="agent_state_change", content=f"State changed to: {requested_state}")
                            
                            # CRITICAL FIX: Append a user message so the next cycle has a clear trigger to start generating
                            # Without this, the last message is the assistant's <request_state> tag, causing empty outputs
                            agent.message_history.append({
                                "role": "user",
                                "content": f"[System State Change]: State changed to {requested_state}. Your instructions have been updated. Please proceed."
                            })
                        
                        await self._manager.send_to_ui(event)
                        llm_stream_ended_cleanly = False; break

                    elif event_type == "tool_requests":
                        context.action_taken_this_cycle = True
                        tool_calls = event.get("calls", [])
                        raw_assistant_response = event.get("raw_assistant_response")
                        content_for_history = event.get("content_for_history")

                        # Variables to hold state change info until tools finish executing
                        deferred_state_change = None
                        deferred_task_desc = None
                        deferred_task_id = None  # task_id from <request_state> tag

                        # CRITICAL FIX: Check if the raw response also contains a state change request
                        # This handles the case where agent produces both state change + tool calls in same response
                        if raw_assistant_response and hasattr(self, 'request_state_pattern'):
                            state_match = self.request_state_pattern.search(raw_assistant_response)
                            if state_match:
                                requested_state = state_match.group(1)
                                embedded_task_id = state_match.group(2) if state_match.lastindex and state_match.lastindex >= 2 else None
                                # Resolve alias to canonical state name
                                requested_state = self._manager.workflow_manager.resolve_state_alias(agent.agent_type, requested_state)
                                if self._manager.workflow_manager.is_valid_state(agent.agent_type, requested_state):
                                    logger.info(f"CycleHandler: Processing embedded state change request '{requested_state}' from tool_requests response")
                                    context.state_change_requested_this_cycle = True
                                    context.needs_reactivation_after_cycle = True  # Ensure the agent wakes up in the new state

                                    # <<< --- ROBUST FIX START --- >>>
                                    # If this is a transition from startup that includes a tool call,
                                    # inject a synthetic thought to bridge the context for the next cycle.
                                    if agent.state == ADMIN_STATE_STARTUP and requested_state == ADMIN_STATE_WORK and tool_calls:
                                        synthetic_thought = (
                                            "<think>The user's request requires tool use. "
                                            "I am transitioning to the 'work' state and executing the first tool call "
                                            "to gather necessary information to complete the task.</think>"
                                        )
                                        logger.info(f"Injecting synthetic thought for startup->work transition for agent '{agent.agent_id}'.")
                                        # Prepend this synthetic thought to any existing thought content
                                        if thought_content_for_history:
                                            thought_content_for_history = synthetic_thought + "\n" + (thought_content_for_history or "")
                                        else:
                                            thought_content_for_history = synthetic_thought
                                    # <<< --- ROBUST FIX END --- >>>

                                    task_description_for_state_change = None
                                    if requested_state == ADMIN_STATE_WORK:
                                        # Prioritize the task description from the event, if available
                                        task_description_for_state_change = event.get("task_description")
                                        
                                        if not task_description_for_state_change:
                                            # Find the last user message to use as the task description
                                            for msg in reversed(agent.message_history):
                                                if msg.get("role") == "user":
                                                    task_description_for_state_change = msg.get("content")
                                                    break
                                        
                                        if not task_description_for_state_change:
                                            # If no user message, check for a message from another agent
                                            for msg in reversed(agent.message_history):
                                                if msg.get("role") == "user" and "[From @" in msg.get("content"):
                                                    task_description_for_state_change = msg.get("content")
                                                    break
                                        
                                        if not task_description_for_state_change:
                                            logger.warning(f"CycleHandler: Could not find a previous user or agent message to use as task description for state change to '{requested_state}'.")

                                    deferred_state_change = requested_state
                                    deferred_task_desc = task_description_for_state_change
                                    deferred_task_id = embedded_task_id  # Pass task_id through for deferred handling

                        # Construct and append the assistant message for history
                        # Construct and append the assistant message for history
                        # The `content_for_history` from the event now correctly contains only the conversational part of the response,
                        # with tool call XML properly stripped by the logic in `agent.core`.
                        # We will preserve this conversational text, as modern tool-calling models support it.
                        # This fixes a bug where legitimate conversational text was being discarded.

                        # <<<< MODIFICATION: Prepend thought to history content
                        final_content_for_history: str = ""
                        if thought_content_for_history:
                            final_content_for_history += thought_content_for_history + "\n"
                        if content_for_history:
                            final_content_for_history += content_for_history

                        if deferred_state_change:
                            history_state_tag = f"\n<request_state state='{deferred_state_change}'"
                            if deferred_task_id:
                                history_state_tag += f" task_id='{deferred_task_id}'"
                            if deferred_task_desc:
                                history_state_tag += f" task_description='{deferred_task_desc}'"
                            history_state_tag += "/>"
                            final_content_for_history += history_state_tag

                        # CRITICAL FIX: Changed `... or None` to `... or ""` to prevent null content,
                        # which causes the agent to lose context and loop. An empty string is valid.
                        assistant_message_for_history: MessageDict = {"role": "assistant", "content": final_content_for_history.strip() or ""}

                        if tool_calls:
                            assistant_message_for_history["tool_calls"] = tool_calls
                        agent.message_history.append(assistant_message_for_history)

                        # Log the interaction to the database.
                        # The 'content' should be the conversational part, not the raw response which includes tool calls.
                        if context.current_db_session_id:
                            await self._manager.db_manager.log_interaction(
                                session_id=context.current_db_session_id,
                                agent_id=agent.agent_id,
                                role="assistant",
                                content=content_for_history, # Use the cleaned content for logging
                                tool_calls=tool_calls
                            )
                        
                        all_tool_results_for_history: List[MessageDict] = [] ; any_tool_success = False
                        tool_results_text = ""  # NEW: Collect tool results as text to append to assistant message
                        
                        # FIX: Deduplicate identical tool calls to prevent duplicate operations
                        seen_tool_signatures: set = set()
                        deduplicated_tool_calls: list = []
                        cross_cycle_duplicate_blocked: bool = False # Flag to prevent state interventions from overriding duplicate warnings
                        for call_data in tool_calls:
                            # Create a signature from tool name + serialized arguments
                            sig = (call_data.get("name"), json.dumps(call_data.get("arguments", {}), sort_keys=True))
                            if sig in seen_tool_signatures:
                                logger.warning(
                                    f"CycleHandler: Skipping duplicate tool call '{call_data.get('name')}' "
                                    f"with identical arguments for agent '{agent.agent_id}'"
                                )
                                continue
                            seen_tool_signatures.add(sig)
                            deduplicated_tool_calls.append(call_data)
                        
                        if len(deduplicated_tool_calls) < len(tool_calls):
                            logger.info(
                                f"CycleHandler: Deduplicated {len(tool_calls) - len(deduplicated_tool_calls)} "
                                f"identical tool calls for agent '{agent.agent_id}'"
                            )
                        
                        # --- PRE-PROCESS SEND_MESSAGE MULTI-TOOL CIRCUIT BREAKER ---
                        has_send = any(t.get("name") == "send_message" for t in deduplicated_tool_calls)
                        other_tools = [t for t in deduplicated_tool_calls if t.get("name") not in ("send_message", "mark_message_read")]
                        
                        if has_send and len(other_tools) > 0 and not (len(other_tools) == 0 and deferred_state_change is not None):
                            if not hasattr(agent, '_send_msg_multi_tool_error_count'): agent._send_msg_multi_tool_error_count = 0
                            agent._send_msg_multi_tool_error_count += 1
                            
                            if agent._send_msg_multi_tool_error_count >= 3:
                                logger.warning(f"Agent {agent.agent_id}: send_message multi-tool circuit breaker triggered. Forcing send_message ONLY.")
                                deduplicated_tool_calls = [t for t in deduplicated_tool_calls if t.get("name") in ("send_message", "mark_message_read")]
                                agent._send_msg_multi_tool_error_count = 0
                        elif has_send:
                            if hasattr(agent, '_send_msg_multi_tool_error_count'): agent._send_msg_multi_tool_error_count = 0
                        # --- END PRE-PROCESS ---

                        send_message_blocked_by_multi_tool = False
                        for i, call_data in enumerate(deduplicated_tool_calls):
                            tool_name = call_data.get("name"); tool_id = call_data.get("id"); tool_args = call_data.get("arguments", {})
                            
                            # --- CROSS-CYCLE DUPLICATE DETECTION ---
                            # Check if this exact tool call already succeeded in a previous cycle.
                            # If so, skip re-execution and inject a forceful directive to advance.
                            is_eligible_pm = agent.agent_type == AGENT_TYPE_PM and agent.state in [PM_STATE_ACTIVATE_WORKERS, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_MANAGE]
                            is_eligible_worker = agent.agent_type == AGENT_TYPE_WORKER
                            
                            # FIX: In pm_manage, list_tasks and list_agents are monitoring operations whose
                            # results change over time as workers act. Exempt them
                            # from duplicate detection so the PM always gets fresh status.
                            is_manage_monitoring_read = (
                                agent.agent_type == AGENT_TYPE_PM
                                and agent.state == PM_STATE_MANAGE
                                and (
                                    (tool_name == "project_management" and tool_args.get("action") == "list_tasks")
                                    or
                                    (tool_name == "manage_team" and tool_args.get("action") == "list_agents")
                                )
                            )
                            if is_manage_monitoring_read:
                                logger.debug(
                                    f"CycleHandler: Allowing {tool_args.get('action')} for PM '{agent.agent_id}' in pm_manage "
                                    f"(monitoring read - exempt from duplicate detection)."
                                )
                            
                            if (is_eligible_pm or is_eligible_worker) and not is_manage_monitoring_read:
                                prev_result = self._detect_cross_cycle_duplicate_tool_call(agent, tool_name, tool_args)
                                if prev_result is not None:
                                    # Track duplicate count for escalation
                                    if not hasattr(agent, '_duplicate_tool_call_count'):
                                        agent._duplicate_tool_call_count = 0
                                    agent._duplicate_tool_call_count += 1
                                    
                                    logger.warning(
                                        f"CycleHandler: CROSS-CYCLE DUPLICATE DETECTED for '{agent.agent_id}' - "
                                        f"tool '{tool_name}' with same args already succeeded. "
                                        f"Duplicate count: {agent._duplicate_tool_call_count}. Skipping re-execution."
                                    )
                                    
                                    # Use a TRUNCATED cached result to save context tokens
                                    # The agent already saw this result - they don't need the full thing again
                                    # However, we must provide enough data for read-only tools so they don't loop endlessly trying to re-fetch forgotten context.
                                    is_read_tool = False
                                    if tool_name == "file_system" and isinstance(tool_args, dict) and tool_args.get("action") in ["read", "read_file", "list", "list_directory"]:
                                        is_read_tool = True
                                    elif tool_name in ["knowledge_base", "web_search", "github_tool", "tool_information"]:
                                        is_read_tool = True
                                        
                                    MAX_CACHED_RESULT_LEN = 4000 if is_read_tool else 200
                                    truncated_result = prev_result
                                    if len(prev_result) > MAX_CACHED_RESULT_LEN:
                                        truncated_result = prev_result[:MAX_CACHED_RESULT_LEN] + f"\n... [TRUNCATED - duplicate call, full result ({len(prev_result)} chars) was already returned to you previously]"
                                    history_item: MessageDict = {
                                        "role": "tool",
                                        "tool_call_id": tool_id or f"cached_id_{i}",
                                        "name": tool_name or f"unknown_tool_{i}",
                                        "content": truncated_result
                                    }
                                    all_tool_results_for_history.append(history_item)
                                    any_tool_success = True
                                    
                                    # Inject escalated directive based on duplicate count
                                    if agent._duplicate_tool_call_count >= 3:
                                        if agent.agent_type == AGENT_TYPE_PM and agent.state in [
                                            PM_STATE_ACTIVATE_WORKERS, PM_STATE_BUILD_TEAM_TASKS
                                        ]:
                                            cross_cycle_duplicate_blocked = True
                                            action_performed = tool_args.get("action", "")
                                            
                                            # Special fast-path: list_tasks → auto-execute list_agents
                                            if action_performed == "list_tasks" and agent.state == PM_STATE_ACTIVATE_WORKERS:
                                                logger.warning(
                                                    f"CycleHandler: AUTO-ADVANCING PM '{agent.agent_id}' after "
                                                    f"{agent._duplicate_tool_call_count} duplicate '{tool_name}' calls. "
                                                    f"Executing list_agents automatically."
                                                )
                                                team_id = f"team_{agent.agent_config.get('config', {}).get('project_name_context', 'Unknown')}"
                                                auto_result = await self._interaction_handler.execute_single_tool(
                                                    agent, f"auto_list_agents_{i}", "manage_team",
                                                    {"action": "list_agents", "team_id": team_id},
                                                    self._manager.current_project, self._manager.current_session
                                                )
                                                if auto_result:
                                                    auto_history: MessageDict = {
                                                        "role": "tool",
                                                        "tool_call_id": f"auto_list_agents_{i}",
                                                        "name": "manage_team",
                                                        "content": str(auto_result.get("content", "[Error]"))
                                                    }
                                                    all_tool_results_for_history.append(auto_history)
                                                    escalation_msg: MessageDict = {
                                                        "role": "system",
                                                        "content": (
                                                            "[Framework System Message - AUTO-ADVANCE]: You called list_tasks "
                                                            f"{agent._duplicate_tool_call_count} times with identical arguments. "
                                                            "The framework has automatically executed list_agents for you. "
                                                            "The agent list result is shown above. "
                                                            "Your MANDATORY next action is to assign the first unassigned task "
                                                            "to a suitable worker agent using: "
                                                            "<project_management><action>modify_task</action>"
                                                            "<task_id>TASK_UUID</task_id>"
                                                            "<assignee_agent_id>WORKER_ID</assignee_agent_id>"
                                                            "<tags>+WORKER_ID,assigned</tags>"
                                                            "</project_management>"
                                                        )
                                                    }
                                                    self._deduplicate_pm_framework_messages(agent)
                                                    all_tool_results_for_history.append(escalation_msg)
                                                    # CRITICAL FIX: Do NOT reset to 0. Set to string threshold - 1 (which is 2)
                                                    # so the next duplicate triggers the general FORCE-ADVANCE below.
                                                    agent._duplicate_tool_call_count = 2 
                                            else:
                                                # GENERAL FORCE-ADVANCE: For any tool type, force PM to pm_standby
                                                # FIX: If already in pm_manage, inject corrective directive instead of
                                                # a no-op state change that resets the counter and loops forever.
                                                if agent.state == PM_STATE_MANAGE:
                                                    logger.warning(
                                                        f"CycleHandler: PM '{agent.agent_id}' stuck in "
                                                        f"'{PM_STATE_MANAGE}' after {agent._duplicate_tool_call_count} "
                                                        f"duplicate '{tool_name}' calls. Injecting STUCK message and forcing to STANDBY."
                                                    )
                                                    self._manager.workflow_manager.change_state(agent, PM_STATE_STANDBY)
                                                    escalation_msg: MessageDict = {
                                                        "role": "system",
                                                        "content": (
                                                            f"[Framework System Message - STUCK LOOP DETECTED]: You have called "
                                                            f"'{tool_name}' {agent._duplicate_tool_call_count} times with "
                                                            f"identical arguments in the pm_manage state. "
                                                            f"To prevent infinite loops, you have been forcibly transitioned to 'pm_standby'. "
                                                            f"You will remain in standby until workers complete tasks or report back."
                                                        )
                                                    }
                                                    self._deduplicate_pm_framework_messages(agent)
                                                    all_tool_results_for_history.append(escalation_msg)
                                                    agent._duplicate_tool_call_count = 0
                                                else:
                                                    logger.warning(
                                                        f"CycleHandler: FORCE-ADVANCING PM '{agent.agent_id}' to "
                                                        f"'{PM_STATE_STANDBY}' after {agent._duplicate_tool_call_count} "
                                                        f"duplicate '{tool_name}' calls in state '{agent.state}'."
                                                    )
                                                    self._manager.workflow_manager.change_state(agent, PM_STATE_STANDBY)
                                                    escalation_msg: MessageDict = {
                                                        "role": "system",
                                                        "content": (
                                                            f"[Framework System Message - FORCE ADVANCE]: You called "
                                                            f"'{tool_name}' {agent._duplicate_tool_call_count} times with "
                                                            f"identical arguments. The framework has force-advanced you to "
                                                            f"the '{PM_STATE_STANDBY}' state to prevent compute waste. "
                                                            f"You will be periodically reactivated when the framework checks on worker progress."
                                                        )
                                                    }
                                                    self._deduplicate_pm_framework_messages(agent)
                                                    all_tool_results_for_history.append(escalation_msg)
                                                    agent._duplicate_tool_call_count = 0
                                                context.needs_reactivation_after_cycle = True
                                        elif agent.agent_type == AGENT_TYPE_WORKER:
                                            # Escalation for workers - force to worker_report after 3 duplicates
                                            cross_cycle_duplicate_blocked = True
                                            logger.warning(
                                                f"CycleHandler: HARD-ADVANCING worker '{agent.agent_id}' after "
                                                f"{agent._duplicate_tool_call_count} duplicate '{tool_name}' calls."
                                            )
                                            # ACTUALLY change the state to worker_report
                                            agent.set_state("worker_report")
                                            escalation_msg: MessageDict = {
                                                "role": "system",
                                                "content": (
                                                    f"[Constitutional Guardian - HARD ADVANCE]: You have called the exact same "
                                                    f"tool ('{tool_name}') with identical arguments {agent._duplicate_tool_call_count} times "
                                                    "without making progress (the result was returned exactly as cached). "
                                                    "Because you were stuck in a loop, your task state has been forcibly changed to 'worker_report'. "
                                                    "Please analyze what happened, outline your blockers, and report to the PM."
                                                )
                                            }
                                            if hasattr(self, '_deduplicate_pm_framework_messages'):
                                                self._deduplicate_pm_framework_messages(agent)
                                            all_tool_results_for_history.append(escalation_msg)
                                            
                                            # Reset to 0 since we forced a state change and want them to report cleanly
                                            agent._duplicate_tool_call_count = 0
                                    else:
                                        # Use CG to evaluate duplicate block
                                        verdict = "BLOCK_AND_GUIDE"
                                        feedback = f"You already called '{tool_name}' with these exact arguments and it succeeded. You MUST use <request_state> to move to the next step."
                                        
                                        if hasattr(self._health_monitor, 'evaluate_duplicate_tool_call'):
                                            try:
                                                verdict, feedback = await self._health_monitor.evaluate_duplicate_tool_call(
                                                    agent, tool_name, tool_args, getattr(agent, '_duplicate_tool_call_count', 1)
                                                )
                                            except Exception as e:
                                                logger.error(f"CycleHandler: Error evaluating duplicate tool call: {e}")
                                                
                                        if verdict == "ALLOW":
                                            # We allow the duplicate tool execution to proceed
                                            logger.info(f"Agent {agent.agent_id} allowed to run duplicate tool '{tool_name}' by CG.")
                                            cross_cycle_duplicate_blocked = False
                                            if hasattr(agent, '_duplicate_tool_call_count'):
                                                agent._duplicate_tool_call_count = 0
                                            
                                            # We DON'T want to continue/skip next steps - let it execute normally
                                            pass
                                        else:
                                            cross_cycle_duplicate_blocked = True
                                            if verdict == "ESCALATE":
                                                agent.set_state("worker_report" if agent.agent_type == "worker" else agent.state)
                                                feedback = f"CRITICAL LOOP ESCALATION: {feedback} Your state has been automatically adjusted to help break the loop."
                                                
                                            escalation_msg: MessageDict = {
                                                "role": "system",
                                                "content": f"[Framework System Message - DUPLICATE CAUGHT]: {feedback}"
                                            }
                                            
                                            if hasattr(self, '_deduplicate_pm_framework_messages'):
                                                self._deduplicate_pm_framework_messages(agent)
                                            all_tool_results_for_history.append(escalation_msg)
                                            
                                            # Log to DB
                                            if context.current_db_session_id:
                                                await self._manager.db_manager.log_interaction(
                                                    session_id=context.current_db_session_id,
                                                    agent_id=agent.agent_id,
                                                    role="tool",
                                                    content=str(prev_result),
                                                    tool_results=[{"call_id": tool_id, "name": tool_name, "content": str(prev_result)}]
                                                )
                                            
                                            continue  # Skip actual tool execution
                                else:
                                    # Not a duplicate - reset counter
                                    if hasattr(agent, '_duplicate_tool_call_count'):
                                        agent._duplicate_tool_call_count = 0
                            # --- END CROSS-CYCLE DUPLICATE DETECTION ---
                            
                            # --- SEND_MESSAGE MULTI-TOOL CONSTRAINT ---
                            if agent.agent_type != "pm" and any(t.get("name") not in ("send_message", "mark_message_read") for t in deduplicated_tool_calls) and tool_name == "send_message":
                                non_send_message_tools = [t for t in deduplicated_tool_calls if t.get("name") not in ("send_message", "mark_message_read")]
                                has_only_state_change_companion = (len(non_send_message_tools) == 0 and deferred_state_change is not None)
                                
                                if has_only_state_change_companion:
                                    logger.info(f"Agent {agent.agent_id}: send_message paired with state change '{deferred_state_change}' — allowing both.")
                                    result_dict = await self._interaction_handler.execute_single_tool(agent, tool_id, tool_name, tool_args, self._manager.current_project, self._manager.current_session)
                                else:
                                    other_tool_names = [t.get("name") for t in non_send_message_tools]
                                    logger.warning(f"Agent {agent.agent_id} attempted to use send_message alongside {other_tool_names}. Blocking send_message only.")
                                    send_message_blocked_by_multi_tool = True
                                    error_msg = (
                                        f"ERROR: Your other tool call(s) ({', '.join(other_tool_names)}) were executed successfully. "
                                        f"CRITICAL INSTRUCTION: You attempted to use send_message alongside other active tool calls. "
                                        f"You may ONLY run send_message alongside a state transition request <request_state...>. "
                                        f"Please wait until all other tasks are complete, then send your message alongside your state transition."
                                    )
                                    result_dict = {
                                        "status": "error",
                                        "message": error_msg,
                                        "content": error_msg,
                                        "call_id": tool_id or f"unknown_id_{i}",
                                        "name": tool_name
                                    }
                            else:
                                result_dict = await self._interaction_handler.execute_single_tool(agent, tool_id, tool_name, tool_args, self._manager.current_project, self._manager.current_session)
                            # --- END SEND_MESSAGE MULTI-TOOL CONSTRAINT ---
                            
                            if result_dict:
                                history_item: MessageDict = {"role": "tool", "tool_call_id": result_dict.get("call_id", tool_id or f"unknown_id_{i}"), "name": result_dict.get("name", tool_name or f"unknown_tool_{i}"), "content": str(result_dict.get("content", "[Tool Error: No content]"))}
                                all_tool_results_for_history.append(history_item)
                                result_content_str = str(result_dict.get("content", ""))
                                
                                # NEW: Append tool result to text format for assistant message
                                tool_results_text += f"\n\n[Tool Result: {tool_name}]\n{result_content_str}"
                                
                                tool_was_successful = True # Assume success by default
                                try:
                                    # Attempt to parse the content as JSON. This handles tools that
                                    # return structured results (like file_system will).
                                    tool_result_data = json.loads(result_content_str)
                                    if isinstance(tool_result_data, dict) and tool_result_data.get("status") == "error":
                                        tool_was_successful = False
                                except (json.JSONDecodeError, TypeError):
                                    # If it's not valid JSON, fall back to string checks for compatibility.
                                    # Check for both "error" and "failed" — tool failures return
                                    # "[Tool Execution Failed]" which doesn't contain "error".
                                    result_lower = result_content_str.lower()
                                    if "error" in result_lower or "[tool execution failed]" in result_lower:
                                        tool_was_successful = False

                                if tool_was_successful:
                                    any_tool_success = True
                                    setattr(agent, '_empty_response_retry_count', 0)  # Reset empty response counter on success
                                    if tool_name == "mark_message_read":
                                        msg_id = tool_args.get("message_id")
                                        if msg_id:
                                            agent.read_message_ids.add(msg_id)
                                            logger.info(f"CycleHandler: Marked message {msg_id} as read for agent {agent.agent_id}")
                                # ... (db log tool result, UI send) ...
                                if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="tool", content=result_content_str, tool_results=[result_dict])
                                await self._manager.send_to_ui({**result_dict, "type": "tool_result", "agent_id": agent.agent_id, "tool_sequence": f"{i+1}_of_{len(tool_calls)}"})
                            else: 
                                all_tool_results_for_history.append({"role": "tool", "tool_call_id": tool_id or f"unknown_call_{i}", "name": tool_name or f"unknown_tool_{i}", "content": "[Tool Error: No result object]"})
                                tool_results_text += f"\n\n[Tool Result: {tool_name}]\n[Tool Error: No result object]"
                        
                        # NOTE: Tool results are appended as separate 'tool' role messages by
                        # NextStepScheduler.schedule_next_step (via context.all_tool_results).
                        # Do NOT also inline them into the assistant message content, as that
                        # creates duplicate data in the LLM context window.
                        
                        context.all_tool_results = all_tool_results_for_history
                        
                        # ROOT CAUSE FIX: Don't automatically set reactivation flag after tool execution
                        # Let the NextStepScheduler decide if reactivation is needed based on agent state and tool results
                        context.executed_tool_successfully_this_cycle = any_tool_success
                        # ROOT CAUSE FIX: Removed automatic reactivation - this was causing the infinite loop
                        logger.critical(f"CycleHandler: Tool execution completed for agent '{agent.agent_id}': executed_tool_successfully_this_cycle={any_tool_success}, allowing natural processing")
                        
                        # ENHANCED: For Admin AI in work state, ensure proper reactivation even after tool execution
                        if agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_WORK:
                            logger.info(f"CycleHandler: Admin AI '{agent.agent_id}' in work state executed tools successfully - ensuring reactivation")

                        # --- START: PM Post-Tool State Transitions ---
                        # Track tool success/failure for PM loop detection
                        if agent.agent_type == AGENT_TYPE_PM and tool_calls and len(tool_calls) >= 1:
                            called_tool_name = tool_calls[-1].get("name")  # Use last tool call
                            
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
                        
                        if agent.agent_type == AGENT_TYPE_PM and any_tool_success and tool_calls and len(tool_calls) >= 1:
                            called_tool_name = tool_calls[-1].get("name")  # Use last tool call for directive matching
                            if agent.state == PM_STATE_ACTIVATE_WORKERS and called_tool_name == "send_message":
                                # This is the final "report to admin" message. Transition to manage state.
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' sent completion message. Auto-transitioning to PM_STATE_MANAGE.")
                                self._manager.workflow_manager.change_state(agent, PM_STATE_MANAGE)
                                # Reactivate immediately to start the management loop.
                                context.needs_reactivation_after_cycle = True




                        # --- START: WORKER Decompose State Interventions ---
                        if agent.agent_type == AGENT_TYPE_WORKER and \
                           agent.state == WORKER_STATE_DECOMPOSE and \
                           any_tool_success and tool_calls and len(tool_calls) >= 1:

                            called_tool_name = tool_calls[-1].get("name")
                            called_tool_args = tool_calls[-1].get("arguments", {})

                            if called_tool_name == "project_management" and called_tool_args.get("action") == "add_task":
                                logger.info(f"CycleHandler: Worker '{agent.agent_id}' successfully added a sub-task. Injecting directive to transition.")
                                directive_message_content = (
                                    "[Framework System Message]: You have successfully added a sub-task.\n"
                                    "If you need to add more sub-tasks, continue using the project_management tool.\n"
                                    "If you have finished decomposing your assignment into sub-tasks, your MANDATORY NEXT ACTION is to output ONLY:\n"
                                    "<request_state state='worker_work' task_id='[ID OF ONE OF YOUR NEW SUB-TASKS]'/>\n"
                                    "Note: You CANNOT use your originally assigned task ID. You must choose a sub-task you just created."
                                )
                                directive_msg: MessageDict = {"role": "system", "content": directive_message_content}
                                all_tool_results_for_history.append(directive_msg)
                                context.needs_reactivation_after_cycle = True
                        # --- END WORKER Decompose State Interventions ---

                        # --- START: PM Build Team Tasks State Interventions ---
                        if agent.agent_type == AGENT_TYPE_PM and \
                           agent.state == PM_STATE_BUILD_TEAM_TASKS and \
                           tool_calls and len(tool_calls) >= 1:

                            called_tool_name = tool_calls[-1].get("name")  # Use last tool call
                            called_tool_args = tool_calls[-1].get("arguments", {})
                            directive_message_content = None

                            if called_tool_name == "manage_team" and called_tool_args.get("action") == "create_team":
                                # Auto-execute tool_information to get create_agent schema
                                # instead of asking the LLM (small models loop on create_team)
                                try:
                                    tool_info_result = await self._manager.tool_executor.execute_tool(
                                        agent_id=agent.agent_id,
                                        agent_sandbox_path=agent.sandbox_path,
                                        tool_name="tool_information",
                                        tool_args={"action": "get_info", "tool_name": "manage_team", "sub_action": "create_agent"},
                                        project_name=self._manager.current_project,
                                        session_name=self._manager.current_session,
                                        manager=self._manager
                                    )
                                    tool_info_text = tool_info_result.get("message", "") if isinstance(tool_info_result, dict) else str(tool_info_result)
                                    logger.info(f"CycleHandler: Auto-executed tool_information for PM '{agent.agent_id}' create_agent schema ({len(tool_info_text)} chars)")
                                except Exception as e:
                                    logger.error(f"CycleHandler: Failed to auto-execute tool_information for PM '{agent.agent_id}': {e}")
                                    tool_info_text = ""

                                directive_message_content = (
                                    "[Framework System Message]: Team creation complete. Step 1 is DONE \u2014 do NOT call create_team again.\n\n"
                                    "The framework has automatically retrieved the create_agent tool information for you (Step 2 complete).\n\n"
                                    f"**create_agent Tool Usage:**\n{tool_info_text}\n\n"
                                    "Your MANDATORY next action is Step 3: Create your first worker agent based on your kickoff plan using the "
                                    "'<manage_team><action>create_agent</action>...' XML format shown above."
                                )
                            elif called_tool_name == "tool_information" and \
                                 called_tool_args.get("action") == "get_info" and \
                                 called_tool_args.get("tool_name") == "manage_team" and \
                                 called_tool_args.get("sub_action") == "create_agent":
                                # This was the second action (Getting create_agent info)
                                agent.successfully_created_agent_count_for_build = 0 # Reset counter before first create
                                directive_message_content = (
                                    "[Framework System Message]: You have successfully retrieved the detailed information for the 'manage_team' tool with sub_action 'create_agent'. "
                                    "Your MANDATORY next action is to proceed with Step 2 of your workflow: Create your first worker agent based on your kickoff plan using the "
                                    "'<manage_team><action>create_agent</action>...' XML format."
                                )
                            elif called_tool_name == "manage_team" and called_tool_args.get("action") == "create_agent":
                                # This was an agent creation action. This is the new, context-aware intervention logic.
                                if any_tool_success:
                                    agent.successfully_created_agent_count_for_build += 1

                                # Get up-to-date team information
                                team_id = self._manager.state_manager.get_agent_team(agent.agent_id)
                                current_worker_agents = []
                                if team_id:
                                    # We get the Agent objects and filter for workers, then get their IDs and Personas
                                    all_agents_in_team = self._manager.state_manager.get_agents_in_team(team_id)
                                    current_worker_agents = [f"{a.agent_id} ({a.persona})" for a in all_agents_in_team if a.agent_type == AGENT_TYPE_WORKER]

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
                                        "More worker agents are required by your kickoff plan or the limit hasn't been reached.\n\n"
                                        f"Your MANDATORY next action is to create the next worker agent (Worker #{next_agent_num}).\n"
                                        "Review your kickoff plan to decide which role to create next.\n"
                                        "IMPORTANT: Do NOT create another agent with a role you have already created. Check the agent IDs above."
                                    )

                            if directive_message_content:
                                # CRITICAL FIX: Deduplicate old framework messages before adding new one
                                self._deduplicate_pm_framework_messages(agent)
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' in '{agent.state}', after tool '{called_tool_name}', injecting directive: {directive_message_content[:100]}...")
                                directive_msg: MessageDict = {"role": "system", "content": directive_message_content}
                                all_tool_results_for_history.append(directive_msg) # Append to history after tool results
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
                             tool_calls and len(tool_calls) >= 1 and \
                             not cross_cycle_duplicate_blocked:  # Match on last tool call and not blocked

                            called_tool_name = tool_calls[-1].get("name")  # Use last tool call
                            called_tool_args = tool_calls[-1].get("arguments", {})
                            directive_message_content = None

                            if called_tool_name == "project_management":
                                last_tool_result_content = "{}"
                                for item in reversed(all_tool_results_for_history):
                                    if item.get("role") == "tool":
                                        last_tool_result_content = item.get("content", "{}")
                                        break
                                
                                try:
                                    tool_result_json = json.loads(last_tool_result_content)
                                    tool_status = tool_result_json.get("status")
                                except json.JSONDecodeError:
                                    # Fallback to check if it's a string from a standard non-JSON tool
                                    tool_status = "error" if "error" in last_tool_result_content.lower() else "success"
                                    tool_result_json = {"status": tool_status, "message": last_tool_result_content}

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
                                        agent._all_kickoff_task_uuids = set()  # Track all kickoff UUIDs for dependency checking
                                        for task in tasks:
                                            if not isinstance(task, dict): continue
                                            uuid = task.get("uuid")
                                            if uuid:
                                                agent._all_kickoff_task_uuids.add(uuid)
                                            assignee = task.get("assignee", "")
                                            # We only want truly unassigned tasks in unassigned_tasks_summary
                                            if assignee is not None and str(assignee).strip() != "": continue
                                            
                                            desc = task.get("description", "No description").strip().replace('\n', ' ')
                                            truncated_desc = (desc[:75] + '...') if len(desc) > 75 else desc
                                            if uuid:
                                                task_summary_lines.append(f"- {truncated_desc} (UUID: {uuid})")
                                                agent.unassigned_tasks_summary.append({"uuid": uuid, "description": desc, "depends": task.get("depends", [])})

                                        summary_str = "\n".join(task_summary_lines) if task_summary_lines else "No unassigned tasks found."
                                        directive_message_content = (
                                            f"[Framework System Message]: Task list retrieved successfully. Here is a summary of the unassigned tasks:\n"
                                            f"{summary_str}\n\n"
                                            "Your mandatory next action is to get the list of available agents using the `<manage_team><action>list_agents</action>...</manage_team>` tool."
                                        )
                                    elif action_performed == "modify_task":
                                        # The tool result contains the definitive UUID of the task that was modified
                                        assigned_task_uuid = tool_result_json.get("task_uuid")
                                        
                                        # Fallback to the requested task_id if task_uuid isn't in the response yet
                                        if not assigned_task_uuid:
                                            assigned_task_uuid = str(called_tool_args.get("task_id", "")).strip()

                                        if hasattr(agent, 'unassigned_tasks_summary') and isinstance(agent.unassigned_tasks_summary, list) and assigned_task_uuid:
                                            new_summary = []
                                            for t in agent.unassigned_tasks_summary:
                                                is_match = False
                                                if str(t.get("uuid")) == str(assigned_task_uuid): is_match = True
                                                # Fallback fuzzy match just in case
                                                elif t.get("description") and str(assigned_task_uuid).lower() in t.get("description").lower(): is_match = True
                                                
                                                if not is_match:
                                                    new_summary.append(t)
                                            agent.unassigned_tasks_summary = new_summary

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
                                            # Check which remaining tasks actually have NO unmet dependencies
                                            assigned_uuids = set()
                                            if hasattr(agent, 'unassigned_tasks_summary'):
                                                # Collect UUIDs of tasks we've already assigned (not in remaining list)
                                                all_kickoff = getattr(agent, '_all_kickoff_task_uuids', set())
                                                remaining_uuids = {t.get("uuid") for t in remaining_tasks}
                                                assigned_uuids = all_kickoff - remaining_uuids

                                            actionable_count = 0
                                            for task_info in remaining_tasks:
                                                desc = task_info.get("description", "No description")
                                                uuid = task_info.get("uuid")
                                                deps = task_info.get("depends", [])
                                                truncated_desc = (desc[:75] + '...') if len(desc) > 75 else desc
                                                # A task is actionable if all its dependencies are in assigned_uuids (already assigned)
                                                has_unmet_deps = any(dep not in assigned_uuids for dep in deps) if deps else False
                                                dep_note = " [BLOCKED - has unmet dependencies]" if has_unmet_deps else ""
                                                task_summary_lines.append(f"- {truncated_desc} (UUID: {uuid}){dep_note}")
                                                if not has_unmet_deps:
                                                    actionable_count += 1

                                            summary_str = "\n".join(task_summary_lines)

                                            if actionable_count > 0:
                                                directive_message_content = (
                                                    f"[Framework System Message]: Task assignment processed successfully. Here are the remaining unassigned tasks:\n"
                                                    f"{summary_str}\n\n"
                                                    "Your mandatory next action is to assign the next ACTIONABLE (non-blocked) task from this list to a suitable agent."
                                                )
                                            else:
                                                # All remaining tasks are blocked by dependencies
                                                project_name = agent.agent_config.get("config", {}).get("project_name_context", "Unknown Project")
                                                directive_message_content = (
                                                    f"[Framework System Message]: Task assignment processed successfully. The remaining tasks all have unmet dependencies:\n"
                                                    f"{summary_str}\n\n"
                                                    "All actionable kick-off tasks have been assigned. Your MANDATORY next action is to report this to the Admin AI and transition to manage state. "
                                                    f"Use the send_message tool to send the following message to '{BOOTSTRAP_AGENT_ID}':\n"
                                                    f"'Project `{project_name}` initial actionable kick-off tasks assigned. Remaining tasks are dependency-blocked and will be assigned as prerequisites complete.'"
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
                                # CRITICAL FIX: Deduplicate old framework messages before adding new one
                                self._deduplicate_pm_framework_messages(agent)
                                logger.info(f"CycleHandler: PM '{agent.agent_id}' in '{agent.state}', after tool '{called_tool_name}', injecting directive: {directive_message_content[:100]}...")
                                directive_msg: MessageDict = {"role": "system", "content": directive_message_content}
                                all_tool_results_for_history.append(directive_msg)
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
                                all_tool_results_for_history.append(directive_msg)
                                if context.current_db_session_id:
                                    await self._manager.db_manager.log_interaction(
                                        session_id=context.current_db_session_id,
                                        agent_id=agent.agent_id,
                                        role="system_intervention",
                                        content=directive_message_content
                                    )
                        # --- END: PM Manage State Interventions ---

                        # --- DEFERRED EMBEDDED STATE CHANGE ---
                        # Execute the embedded state change AFTER all tools have finished executing.
                        # This prevents the state change from altering the agent's state before 
                        # tools that depend on the original state (like send_message in worker_report) can run.
                        if deferred_state_change:
                            if send_message_blocked_by_multi_tool:
                                logger.warning(f"Blocking deferred_state_change '{deferred_state_change}' for '{agent.agent_id}' because send_message was blocked.")
                                invalid_msg = f"[Framework Error]: Your state transition to '{deferred_state_change}' was BLOCKED because your <send_message> call failed (you cannot use send_message alongside other active tools). Please review the feedback from your other tools, then repeat your send_message and request_state alone."
                                agent.message_history.append({"role": "system", "content": invalid_msg})
                            else:
                                try:
                                    # Auto-track worker task when transitioning to work state
                                    self._handle_worker_task_tracking(agent, deferred_state_change, deferred_task_id)
                                    
                                    if self._manager.workflow_manager.change_state(agent, deferred_state_change, task_description=deferred_task_desc):
                                        logger.info(f"CycleHandler: Successfully changed agent '{agent.agent_id}' state to '{deferred_state_change}' after tool processing")
                                        
                                        # CRITICAL FIX: Append a user message so the next cycle has a clear trigger to start generating
                                        # Without this, the last message is the assistant's <request_state> tag, causing empty outputs
                                        agent.message_history.append({
                                            "role": "user",
                                            "content": f"[System State Change]: State changed to {deferred_state_change}. Your instructions have been updated. Please proceed."
                                        })
                                    else:
                                        resolved_requested = self._manager.workflow_manager.resolve_state_alias(agent.agent_type, deferred_state_change)
                                        if agent.state == resolved_requested:
                                            logger.info(f"CycleHandler: Agent '{agent.agent_id}' is already in state '{resolved_requested}'. Reactivating with reminder.")
                                            context.needs_reactivation_after_cycle = True
                                            agent.message_history.append({
                                                "role": "system",
                                                "content": f"[Framework Directive]: You requested to change to '{deferred_state_change}', but you are already in this state. Please proceed with executing tools to fulfill your current goal."
                                            })
                                        else:
                                            logger.warning(f"CycleHandler: Failed to change agent '{agent.agent_id}' state to '{deferred_state_change}' after tool processing")
                                            # Truly invalid state transition. Let it reactivate to correct itself (with an error message).
                                            invalid_msg = f"[Framework Error]: Invalid state requested: '{deferred_state_change}'. Please check valid states for your role."
                                            agent.message_history.append({"role": "system", "content": invalid_msg})
                                except ValueError as ve:
                                    # Catch validation errors from task tracking (e.g. trying to select a decomposed task)
                                    logger.warning(f"CycleHandler: State transition blocked for '{agent.agent_id}': {ve}")
                                    err_msg = f"[Framework Error]: State transition rejected: {ve}"
                                    agent.message_history.append({"role": "system", "content": err_msg})
                                    context.needs_reactivation_after_cycle = True
                        # --- END DEFERRED EMBEDDED STATE CHANGE ---

                        llm_stream_ended_cleanly = False; break

                    elif event_type in ["response_chunk", "status", "final_response", "invalid_state_request_output"]:
                        if event_type == "final_response":
                            context.action_taken_this_cycle = True
                            final_content = event.get("content")
                            original_event_data = event
    
                            # <<<< MODIFICATION: Prepend thought to final content if it exists
                            if thought_content_for_history:
                                final_content = f"{thought_content_for_history}\n{final_content}"
                                original_event_data['content'] = final_content # Update event data for UI/logging
    
                            # --- START: Worker Auto-Save File Feature ---
                            if agent.agent_type == AGENT_TYPE_WORKER and final_content and "<request_state state='worker_wait'/>" in final_content:
                                logger.info(f"CycleHandler: Worker '{agent.agent_id}' produced final content. Checking for files to auto-save.")
                                # Regex to find all markdown code blocks
                                code_blocks = re.findall(r"```(?:\w+)?\n(.*?)\n```", final_content, re.DOTALL)
                                saved_files_count: int = 0
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
                                # Skip CG review for tool-call-only responses (framework operations, not user-facing text)
                                if re.match(r'^\s*(<tool_call>.*?</tool_call>\s*)+$', final_content, re.DOTALL):
                                    logger.info(f"CycleHandler: Skipping CG review for tool-call-only response from '{agent.agent_id}'")
                                    cg_verdict = "<OK/>"
                                else:
                                    cg_verdict = await self._get_cg_verdict(agent, final_content)
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
                        elif event_type == "invalid_state_request_output":
                            invalid_content = event.get("content", "")
                            # CRITICAL FIX: Append the LLM's response to history so the next cycle
                            # doesn't see identical input and produce identical output (loop breaker)
                            agent.message_history.append({"role": "assistant", "content": invalid_content})
                            
                            # Try to resolve the alias and provide helpful feedback
                            requested_state_match = re.search(r"state=['\"]([\w_]+)['\"]", invalid_content)
                            resolved_hint = ""
                            if requested_state_match and hasattr(self._manager, 'workflow_manager'):
                                raw_state = requested_state_match.group(1)
                                resolved = self._manager.workflow_manager.resolve_state_alias(agent.agent_type, raw_state)
                                if resolved != raw_state:
                                    resolved_hint = f" Did you mean '{resolved}'? Try: <request_state state='{resolved}'/>"
                            
                            valid_states = self._manager.workflow_manager._valid_states.get(agent.agent_type, [])
                            feedback_msg = (
                                f"[Framework Feedback]: Your state request '{invalid_content}' used an unrecognized state name. "
                                f"Valid states for your role are: {valid_states}.{resolved_hint} "
                                f"Please use the exact state name from this list."
                            )
                            agent.message_history.append({"role": "system", "content": feedback_msg})
                            
                            context.action_taken_this_cycle = True; context.needs_reactivation_after_cycle = True
                            if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="system_warning", content=f"Agent attempted invalid state change: {invalid_content}. Feedback provided.")
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
                        if cycle_text_content.strip():
                            # If there's content in text buffer, check if it's just thinking
                            buffer_content = cycle_text_content.strip()
                            if '<think>' in buffer_content.lower() and not any(tool_name in buffer_content.lower() 
                                for tool_name in ['<project_management>', '<manage_team>', '<send_message>']):
                                recent_think_only = True
                        else:
                            # If no text buffer but also no action taken, this indicates a problematic cycle
                            recent_think_only = True
                            
                        # Reset cooldown tracking if agent was productive
                        if not recent_think_only:
                            agent._framework_forced_standby_count = 0
                            agent._manage_unproductive_cycles = 0

                        if recent_think_only:
                            logger.info(f"CycleHandler: PM agent '{agent.agent_id}' in MANAGE state produced only thinking without action. Applying enhanced intervention.")

                            # Set a cooldown to prevent immediate re-triggering by the periodic timer
                            agent._manage_cycle_cooldown_until = time.time() + 30  # 30 second cooldown
                            
                            # Count consecutive non-productive cycles
                            if not hasattr(agent, '_manage_unproductive_cycles'):
                                agent._manage_unproductive_cycles = 0
                            agent._manage_unproductive_cycles += 1

                            if agent._manage_unproductive_cycles >= 3:
                                # Track framework-forced standbys for progressive cooldown
                                if not hasattr(agent, '_framework_forced_standby_count'):
                                    agent._framework_forced_standby_count = 0
                                agent._framework_forced_standby_count += 1
                                
                                cooldown_seconds = min(30 * (2 ** (agent._framework_forced_standby_count - 1)), 300)

                                # After 3 unproductive cycles, force transition to standby directly
                                logger.warning(f"CycleHandler: PM agent '{agent.agent_id}' had {agent._manage_unproductive_cycles} unproductive MANAGE cycles. Force-transitioning to standby state (Cooldown: {cooldown_seconds}s).")
                                
                                standby_message_content = (
                                    "[Framework Intervention]: You have completed multiple management cycles without taking concrete action. "
                                    "Your project appears to be in a stable state. You will now transition to standby mode. "
                                    "Output ONLY the following XML: <request_state state='pm_standby'/>"
                                )
                                agent.message_history.append({"role": "system", "content": standby_message_content})
                                
                                # Set state immediately to ensure it takes effect if LLM fails
                                agent.set_state(PM_STATE_STANDBY)
                                # Explicitly update the cooldown timer
                                agent._last_standby_wake_time = time.time() + cooldown_seconds - 60 # Set to trigger when cooldown expires (-60 compensates for baseline delay in manager)
                                
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
                                # Instead of forcing list_tasks, instruct PM to go to standby
                                # This prevents unnecessary token-consuming task listing
                                directive_message_content = (
                                    f"[Framework Intervention]: This is your {agent._manage_unproductive_cycles} consecutive management cycle without concrete action. "
                                    "All workers appear to be busy. You should transition to standby and wait for worker reports. "
                                    "Output ONLY: <request_state state='pm_standby'/>"
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

                    # Intervention logic for PM agent stuck after team creation
                    if agent.agent_type == AGENT_TYPE_PM and \
                       agent.state == PM_STATE_BUILD_TEAM_TASKS and \
                       not getattr(agent, 'intervention_applied_for_build_team_tasks', False):

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
                    if context.needs_reactivation_after_cycle and getattr(agent, 'intervention_applied_for_build_team_tasks', False) and not cycle_text_content.strip():
                        pass
                    elif context.action_taken_this_cycle and not cycle_text_content.strip():
                        logger.debug(f"CycleHandler: Agent '{agent.agent_id}' produced no text response, but action was taken (e.g. native tools). Skipping empty response nudge.")
                        context.cycle_completed_successfully = True
                    else:
                        final_content_from_buffer = cycle_text_content.strip()
                        if final_content_from_buffer:
                            cycle_text_content = ""; mock_event_data = {"type": "final_response", "content": final_content_from_buffer, "agent_id": agent.agent_id}
                            context.action_taken_this_cycle = True
                            if agent.agent_id != CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                                cg_verdict = await self._get_cg_verdict(agent, final_content_from_buffer)
                                if cg_verdict == "<OK/>":
                                    if context.current_db_session_id: await self._manager.db_manager.log_interaction(session_id=context.current_db_session_id, agent_id=agent.agent_id, role="assistant", content=final_content_from_buffer)
                                    await self._manager.send_to_ui(mock_event_data)
                                    context.cycle_completed_successfully = True
                                    
                                    # NEW: Auto-transition Admin AI from conversation to standby
                                    if agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_CONVERSATION:
                                        if not context.state_change_requested_this_cycle and not context.executed_tool_successfully_this_cycle:
                                            logger.info(f"CycleHandler: Admin AI '{agent.agent_id}' in conversation state produced text response. Auto-transitioning to standby mode.")
                                            self._manager.workflow_manager.change_state(agent, ADMIN_STATE_STANDBY)
                                            context.needs_reactivation_after_cycle = False
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
                            # --- EMPTY RESPONSE NUDGE-AND-RETRY ---
                            # Instead of silently accepting, inject a nudge and immediately retry
                            empty_retry_count = getattr(agent, '_empty_response_retry_count', 0)
                            max_empty_retries = 2
                            
                            if empty_retry_count < max_empty_retries:
                                empty_retry_count += 1
                                setattr(agent, '_empty_response_retry_count', empty_retry_count)
                                
                                nudge_msg = (
                                    f"[Framework Nudge]: Your last response was empty (attempt {empty_retry_count}/{max_empty_retries}). "
                                    f"You MUST produce output. Review your current state ('{agent.state}') and "
                                    f"take a concrete action: use a tool, provide analysis, or request a state transition."
                                )
                                agent.message_history.append({"role": "system", "content": nudge_msg})
                                logger.warning(
                                    f"CycleHandler: Agent '{agent.agent_id}' produced empty response. "
                                    f"Injecting nudge and scheduling immediate retry ({empty_retry_count}/{max_empty_retries})."
                                )
                                
                                context.needs_reactivation_after_cycle = True
                                context.action_taken_this_cycle = True
                                context.cycle_completed_successfully = True
                            else:
                                # Max retries exhausted - accept the empty response and reset
                                setattr(agent, '_empty_response_retry_count', 0)
                                logger.info(f"Agent '{agent.agent_id}' cycle resulted in no errors, no actions, and no text content after {max_empty_retries} nudge retries. Cycle considered complete but no output.")
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
                    if agent_generator:
                        try:
                            import asyncio
                            await asyncio.wait_for(agent_generator.aclose(), timeout=1.0)
                        except Exception:
                            pass
                        agent_generator = None # Close current generator
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
                        logger.info(f"CycleHandler '{agent.agent_id}': Closing agent_generator in finally block (ag_running={getattr(agent_generator, 'ag_running', 'N/A')}, ag_frame={'set' if getattr(agent_generator, 'ag_frame', None) else 'None'}).")
                        import asyncio
                        await asyncio.wait_for(agent_generator.aclose(), timeout=2.0)
                    except Exception as close_err: logger.warning(f"Error closing agent generator for '{agent.agent_id}' in inner finally: {close_err}", exc_info=True)
        
        # --- This is the original finally block of run_cycle ---
        # It runs AFTER the `while True` loop (and its inner try/except/finally) has exited.
        context.llm_call_duration_ms = (time.perf_counter() - context.start_time) * 1000 # Measure total time including rechecks for now

        # Determine final outcome of the cycle (potentially after rechecks)
        # The context.cycle_completed_successfully, context.last_error_obj etc. should reflect the *last* attempt if rechecked.
        self._outcome_determiner.determine_cycle_outcome(context)

        # Record agent health metrics after cycle completion
        if not context.is_provider_level_error:
            final_output_content = cycle_text_content or ''
            logger.debug(f"CycleHandler: Recording agent cycle for '{agent.agent_id}' with content length: {len(final_output_content)}")
            self._health_monitor.record_agent_cycle(
                agent=agent,
                content=final_output_content,
                has_action=context.action_taken_this_cycle,
                has_thought=context.thought_produced_this_cycle,
                took_meaningful_action=context.executed_tool_successfully_this_cycle or context.state_change_requested_this_cycle
            )
    
            # Constitutional Guardian Health Intervention Check
            try:
                logger.debug(f"CycleHandler: Analyzing agent health for '{agent.agent_id}'")
                needs_intervention, problem_desc, recovery_plan = await self._health_monitor.analyze_agent_health(agent)
                if needs_intervention and recovery_plan:
                    logger.error(f"CycleHandler: Constitutional Guardian intervening for agent '{agent.agent_id}': {problem_desc}")
                    success = await self._health_monitor.execute_recovery_plan(agent, recovery_plan)
                    if success:
                        # After successful Constitutional Guardian intervention, let NextStepScheduler handle reactivation
                        context.needs_reactivation_after_cycle = True
                        logger.warning(f"CycleHandler: Constitutional Guardian intervention successful for '{agent.agent_id}', agent will be reactivated via NextStepScheduler")
                        
                        # Ensure agent's task state completely resets to force re-planning on stalling / empty response loops
                        recovery_type = recovery_plan.get("type", "")
                        if recovery_type in ["minimal_response_pattern", "stuck_in_state", "tool_information_loop_violation", "empty_response_violation"]:
                            # CRITICAL FIX: Agent-type-aware state recovery.
                            # Previously, all agents were blindly reset to worker_wait/worker_report,
                            # which caused PM agents to fall into the DEFAULT state and become non-functional.
                            if agent.agent_type == AGENT_TYPE_PM:
                                # PM agents should NEVER be set to worker states.
                                # Reset to pm_manage (safe default) so PM can reassess project status.
                                target_state = PM_STATE_MANAGE
                                logger.warning(
                                    f"CycleHandler: Constitutional Guardian Intervention Reset - "
                                    f"PM agent '{agent.agent_id}' resetting to '{target_state}' "
                                    f"(recovery_type: {recovery_type})"
                                )
                                agent.needs_priority_recheck = True
                                agent.set_state(target_state)
                                
                            elif agent.agent_type == AGENT_TYPE_ADMIN:
                                # Admin agents should reset to admin_standby
                                target_state = ADMIN_STATE_STANDBY
                                logger.warning(
                                    f"CycleHandler: Constitutional Guardian Intervention Reset - "
                                    f"Admin agent '{agent.agent_id}' resetting to '{target_state}' "
                                    f"(recovery_type: {recovery_type})"
                                )
                                agent.needs_priority_recheck = True
                                agent.set_state(target_state)
                                
                            else:
                                # Worker agents — original logic
                                has_task_record = (hasattr(agent, 'metadata') and 
                                               isinstance(agent.metadata, dict) and 
                                               'task_id' in agent.metadata)
                                
                                target_state = "worker_report" if has_task_record else "worker_wait"
                                logger.warning(
                                    f"CycleHandler: Constitutional Guardian Intervention Reset - "
                                    f"Changing worker '{agent.agent_id}' state to '{target_state}'"
                                )
                                
                                agent.needs_priority_recheck = True
                                agent.set_state(target_state)
                                    
                                if hasattr(agent, "current_task_id"):
                                    agent.current_task_id = None
                                if hasattr(agent, "active_task_id"):
                                    agent.active_task_id = None
    
                        # CRITICAL FIX: Removed direct schedule_cycle() call here.
                else:
                    logger.debug(f"CycleHandler: No intervention needed for agent '{agent.agent_id}'")
            except Exception as health_error:
                logger.error(f"CycleHandler: Error during Constitutional Guardian health monitoring for '{agent.agent_id}': {health_error}", exc_info=True)
        else:
            logger.debug(f"CycleHandler: Skipping health monitoring for agent '{agent.agent_id}' due to provider-level error.")

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
