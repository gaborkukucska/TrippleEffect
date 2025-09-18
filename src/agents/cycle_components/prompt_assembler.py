# START OF FILE src/agents/cycle_components/prompt_assembler.py
import logging
import json
from typing import TYPE_CHECKING, List, Optional

from src.llm_providers.base import MessageDict
from src.agents.constants import BOOTSTRAP_AGENT_ID

if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager
    from src.agents.cycle_components.cycle_context import CycleContext

logger = logging.getLogger(__name__)

class PromptAssembler:
    """
    Assembles the system prompt and message history for an LLM call within a cycle.
    Handles injection of system health reports for Admin AI.
    """

    def __init__(self, manager: 'AgentManager'):
        self._manager = manager

    async def _generate_system_health_report(self, agent: 'Agent') -> Optional[str]:
        """
        Generates a concise system health report and task context for Admin AI.
        This is critical for preventing infinite loops by providing context continuity.
        """
        if not agent or not agent.message_history:
            return "[Framework Internal Status: System is initializing or history is fresh. This is not a user query.]"

        # CRITICAL FIX: For Admin AI in work state, provide comprehensive action history and task context
        if hasattr(agent, 'agent_type') and agent.agent_type == 'admin' and hasattr(agent, 'state') and agent.state == 'work':
            report_parts = ["[Framework Context Report for Work State]"]
            
            # 1. Extract the original task description
            original_task = agent.current_task_description
            if not original_task:
                for msg in reversed(agent.message_history):
                    if msg.get("role") == "user":
                        original_task = msg.get("content")
                        break
            original_task = original_task or "No specific task identified"
            
            report_parts.append(f"ORIGINAL TASK: {original_task}")
            
            # 2. Summarize recent actions and their outcomes
            recent_actions = []
            tool_results_summary = []
            
            # Look at last 10 messages for pattern analysis
            for msg in reversed(agent.message_history[-10:]):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tool_call in msg.get("tool_calls", []):
                        tool_name = tool_call.get("name", "unknown")
                        tool_args = tool_call.get("arguments", {})
                        action_summary = f"{tool_name}"
                        if isinstance(tool_args, dict) and "action" in tool_args:
                            action_summary += f"({tool_args['action']})"
                        recent_actions.append(action_summary)
                        
                elif msg.get("role") == "tool":
                    tool_name = msg.get("name", "unknown")
                    content = str(msg.get("content", ""))
                    
                    # Determine if tool was successful
                    success = True
                    try:
                        if content.startswith("{") and content.endswith("}"):
                            tool_data = json.loads(content)
                            if isinstance(tool_data, dict) and tool_data.get("status") == "error":
                                success = False
                    except:
                        if "error" in content.lower():
                            success = False
                    
                    status = "SUCCESS" if success else "FAILED"
                    result_preview = content[:100] + ("..." if len(content) > 100 else "")
                    tool_results_summary.append(f"{tool_name}: {status} - {result_preview}")
            
            # Limit to last 5 actions to keep prompt manageable
            if recent_actions:
                report_parts.append(f"RECENT ACTIONS: {', '.join(recent_actions[-5:])}")
            
            if tool_results_summary:
                report_parts.append(f"RECENT RESULTS: {'; '.join(tool_results_summary[-3:])}")
            
            # 3. Check for problematic patterns
            warnings = []
            
            # Check for repeated actions
            if len(recent_actions) >= 3:
                last_action = recent_actions[-1] if recent_actions else ""
                if recent_actions.count(last_action) >= 2:
                    warnings.append(f"WARNING: Action '{last_action}' repeated multiple times")
            
            # Check for consecutive failures
            recent_failures = [r for r in tool_results_summary[-3:] if "FAILED" in r]
            if len(recent_failures) >= 2:
                warnings.append("WARNING: Multiple recent tool failures detected")
            
            # Check for empty responses pattern
            empty_responses = 0
            for msg in reversed(agent.message_history[-5:]):
                if msg.get("role") == "assistant" and not msg.get("content", "").strip() and not msg.get("tool_calls"):
                    empty_responses += 1
                else:
                    break
            
            if empty_responses >= 2:
                warnings.append(f"CRITICAL: {empty_responses} consecutive empty responses detected")
            
            # ENHANCED: Check for tool execution loops
            if len(recent_actions) >= 4:
                # Check for identical consecutive tool executions
                last_two_actions = recent_actions[-2:]
                if len(set(last_two_actions)) == 1:  # Both actions are identical
                    warnings.append(f"CRITICAL: Repeated identical tool execution detected: '{last_two_actions[0]}'")
            
            if warnings:
                report_parts.append("ALERTS: " + "; ".join(warnings))
                report_parts.append("GUIDANCE: If you are repeating actions or getting stuck, try a different approach or summarize your progress and request a state change.")
            
            # 4. Provide progress context
            if hasattr(agent, '_work_cycle_count'):
                cycle_count = getattr(agent, '_work_cycle_count', 0)
                if cycle_count > 0:
                    report_parts.append(f"WORK SESSION: Cycle {cycle_count} - Focus on making measurable progress")
            
            # ENHANCED: Add specific guidance for breaking loops
            if "CRITICAL" in " ".join(warnings):
                report_parts.append("FORBIDDEN: You are NOT allowed to use the same tool again in this turn.")
            report_parts.append("MANDATORY ACTION: If you see repeated actions above, you MUST take a different approach. Choose a different tool or request a state change.")
            
            return "\n".join(report_parts)
        
        # For non-work states or non-admin agents, provide basic status
        return "[Framework Internal Status: System operational. Continue your work as needed.]"


    async def prepare_llm_call_data(self, context: 'CycleContext') -> None:
        """
        Prepares the final system prompt and message history for the LLM call,
        storing them in the `CycleContext`.

        Args:
            context: The CycleContext object for the current agent cycle.
        """
        agent = context.agent
        manager = self._manager # AgentManager is passed to PromptAssembler constructor

        # 1. Get State-Specific System Prompt using WorkflowManager
        if not hasattr(manager, 'workflow_manager'):
            logger.error("PromptAssembler: WorkflowManager not found on AgentManager! Cannot get state-specific prompt.")
            context.final_system_prompt = agent.final_system_prompt or manager.settings.DEFAULT_SYSTEM_PROMPT
        else:
            context.final_system_prompt = manager.workflow_manager.get_system_prompt(agent, manager)
        
        logger.debug(f"PromptAssembler: Base system prompt for agent '{agent.agent_id}' (Type: {agent.agent_type}, State: {agent.state}) set.")

        # 2. Prepare History for LLM Call
        history_for_call = agent.message_history.copy() # Start with agent's current history
        logger.debug(f"PromptAssembler '{agent.agent_id}': Raw agent.message_history (len {len(agent.message_history)}) before modifications: {json.dumps(agent.message_history, indent=2)}")
        # Ensure system prompt is at the start of history
        if not history_for_call or history_for_call[0].get("role") != "system":
            history_for_call.insert(0, {"role": "system", "content": context.final_system_prompt})
        else:
            # Overwrite if it exists, ensuring it's the latest one for the current state
            history_for_call[0] = {"role": "system", "content": context.final_system_prompt}

        # 3. Inject System Health Report (Admin AI only)
        if agent.agent_id == BOOTSTRAP_AGENT_ID: # Check agent ID
            system_health_report = await self._generate_system_health_report(agent)
            if system_health_report:
                health_msg: MessageDict = {"role": "system", "content": system_health_report}
                # Insert *after* the main system prompt but before other history
                if len(history_for_call) > 1:
                    history_for_call.insert(1, health_msg)
                else:
                    history_for_call.append(health_msg) # Append if only system prompt was there
                logger.debug(f"Injected system health report for Admin AI '{agent.agent_id}'.")
        
        context.history_for_call = history_for_call

        # 4. Log the history being sent to the LLM
        logger.debug(f"PromptAssembler: Final history being sent to LLM for agent '{agent.agent_id}' (state: {agent.state}, length {len(context.history_for_call)}):")
        for i, msg_to_log in enumerate(context.history_for_call):
            content_preview = str(msg_to_log.get('content'))[:200]
            tool_calls_preview = msg_to_log.get('tool_calls')
            log_line = f"  [{i}] Role: {msg_to_log.get('role')}, Content: {content_preview}{'...' if len(str(msg_to_log.get('content'))) > 200 else ''}"
            if tool_calls_preview:
                log_line += f", ToolCalls: {json.dumps(tool_calls_preview)}"
            logger.debug(log_line)
