# START OF FILE src/agents/cycle_components/prompt_assembler.py
import logging
import json
from typing import TYPE_CHECKING, List, Optional

from src.llm_providers.base import MessageDict
from src.agents.constants import BOOTSTRAP_AGENT_ID, AGENT_TYPE_WORKER, AGENT_TYPE_PM, WORKER_STATE_REPORT, WORKER_STATE_WORK, WORKER_STATE_WAIT
from src.config.settings import settings

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
                    except (json.JSONDecodeError, TypeError, ValueError):
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

    async def _generate_workspace_tree_report(self) -> Optional[str]:
        if not self._manager.current_project or not self._manager.current_session:
            return None
            
        import os
        import re
        
        safe_project_name = re.sub(r'[^\w\-. ]', '_', self._manager.current_project)
        workspace_path = settings.PROJECTS_BASE_DIR / safe_project_name / self._manager.current_session / "shared_workspace"
        
        if not workspace_path.exists():
            return None
            
        EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env', 'dist', 'build', '.idea', '.vscode'}
        MAX_FILES = 200
        MAX_DEPTH = 4
        
        # Build tree string
        tree_lines = []
        file_count = 0
        
        for root, dirs, files in os.walk(workspace_path):
            # Prune excluded directories from traversal
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            level = str(root).replace(str(workspace_path), '').count(os.sep)
            
            # Prune directories based on max depth
            if level > MAX_DEPTH:
                continue
                
            indent = '  ' * level
            if level > 0:
                tree_lines.append(f"{indent}|-- {os.path.basename(root)}/")
                
            for f in files:
                if file_count >= MAX_FILES:
                    break
                tree_lines.append(f"{indent}  |-- {f}")
                file_count += 1
                
            if file_count >= MAX_FILES:
                tree_lines.append(f"{indent}  |-- ... (Truncated: Maximum {MAX_FILES} files returned)")
                break
                
        if not tree_lines:
            return None
            
        tree_str = "\n".join(tree_lines)
        return (f"[SHARED WORKSPACE TREE (Current State)]\n{tree_str}\n"
                f"(Note: This tree shows core files currently existing in the shared_workspace. "
                f"Read them to avoid duplicating work. Deep dependency directories like node_modules "
                f"and .git are hidden for brevity.)")

    def _generate_worker_tasks_report(self, agent: 'Agent') -> Optional[str]:
        """
        Generates a concise report of tasks assigned to this worker agent.
        This ensures the worker always knows its task UUIDs for state transitions.
        """
        if not self._manager.current_project or not self._manager.current_session:
            return None

        try:
            from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
            if not TASKLIB_AVAILABLE:
                return None

            pm_tool = ProjectManagementTool()
            tw = pm_tool._get_taskwarrior_instance(self._manager.current_project, self._manager.current_session)
            if not tw:
                return None

            # Query tasks assigned to this specific worker
            tasks = tw.tasks.filter(assignee=agent.agent_id, status='pending')
            task_list = list(tasks)

            if not task_list:
                return None

            # Format the report
            lines = [f"[YOUR ASSIGNED TASKS - {len(task_list)} task(s)]"]
            active_task_id = getattr(agent, 'active_task_id', None)
            for task in task_list:
                uuid = task['uuid']
                desc = task['description'] or 'No description'
                try:
                    progress = task['task_progress'] or 'todo'
                except (KeyError, AttributeError):
                    progress = 'todo'
                truncated_desc = (desc[:80] + '...') if len(desc) > 80 else desc
                active_marker = " ◄ ACTIVE" if active_task_id and str(uuid) == str(active_task_id) else ""
                lines.append(f"  - [{progress}] {truncated_desc} (task_id: {uuid}){active_marker}")

            lines.append("")
            lines.append("[IMPORTANT] When transitioning to work state, you MUST specify the task_id:")
            lines.append("  <request_state state='worker_work' task_id='PASTE_UUID_HERE'/>")

            return "\n".join(lines)

        except Exception as e:
            logger.warning(f"PromptAssembler: Failed to generate worker tasks report for '{agent.agent_id}': {e}")
            return None


    async def prepare_llm_call_data(self, context: 'CycleContext') -> None:
        """
        Prepares the final system prompt and message history for the LLM call,
        storing them in the `CycleContext`.

        Uses state-aware tracking to prevent regenerating the system prompt every
        cycle. The prompt is only regenerated when the agent's (type, state) changes,
        ensuring that the LLM sees a stable system prompt and properly accumulates
        context from tool results and framework directives.

        Args:
            context: The CycleContext object for the current agent cycle.
        """
        agent = context.agent
        manager = self._manager # AgentManager is passed to PromptAssembler constructor

        # 1. Get State-Specific System Prompt - only regenerate on state change
        # Track the (agent_type, state) pair to detect actual state transitions.
        # This replaces the old prefix-based comparison which failed because:
        #   a) The system prompt was never persisted back to agent.message_history
        #   b) Dynamic fields (current_time_utc, address_book) changed every cycle
        current_state_key = (agent.agent_type, agent.state)
        last_prompt_state = getattr(agent, '_last_system_prompt_state', None)
        
        has_existing_system_prompt = (
            len(agent.message_history) > 0 and
            agent.message_history[0].get("role") == "system"
        )
        needs_new_context = getattr(agent, '_needs_initial_work_context', False)

        if last_prompt_state == current_state_key and has_existing_system_prompt and not needs_new_context:
            # Same state as when we last injected - reuse the existing prompt
            context.final_system_prompt = agent.message_history[0]["content"]
            logger.debug(f"PromptAssembler: Reusing existing system prompt for '{agent.agent_id}' (state unchanged: {agent.state}).")
        else:
            # State changed or no prompt exists yet - generate fresh
            if not hasattr(manager, 'workflow_manager'):
                logger.error("PromptAssembler: WorkflowManager not found on AgentManager! Cannot get state-specific prompt.")
                context.final_system_prompt = agent.final_system_prompt or settings.DEFAULT_SYSTEM_PROMPT
            else:
                context.final_system_prompt = manager.workflow_manager.get_system_prompt(agent, manager)
            logger.debug(f"PromptAssembler: Generated fresh system prompt for agent '{agent.agent_id}' (Type: {agent.agent_type}, State: {agent.state}).")
            
            if needs_new_context:
                agent._needs_initial_work_context = False

        # 1.5 Filter read messages from history
        if getattr(agent, 'read_message_ids', None):
            filtered_history = []
            for msg in agent.message_history:
                if msg.get("message_id") and msg.get("message_id") in agent.read_message_ids:
                    logger.debug(f"PromptAssembler '{agent.agent_id}': Filtering out read message {msg.get('message_id')}")
                    continue
                filtered_history.append(msg)
            agent.message_history = filtered_history

        # 2. Prepare History for LLM Call
        history_for_call = agent.message_history.copy() # Start with agent's current history
        logger.debug(f"PromptAssembler '{agent.agent_id}': Raw agent.message_history (len {len(agent.message_history)}) before modifications: {json.dumps(agent.message_history, indent=2)}")

        if last_prompt_state != current_state_key:
            # State changed - inject new prompt and persist to both copy AND original
            logger.debug(f"PromptAssembler: Injecting fresh state prompt for '{agent.agent_id}' (State: {agent.state}).")
            system_msg: MessageDict = {"role": "system", "content": context.final_system_prompt}
            if has_existing_system_prompt:
                history_for_call[0] = system_msg
                agent.message_history[0] = {"role": "system", "content": context.final_system_prompt}
            else:
                history_for_call.insert(0, system_msg)
                agent.message_history.insert(0, {"role": "system", "content": context.final_system_prompt})
            # Mark the state as injected so subsequent cycles in this state reuse it
            agent._last_system_prompt_state = current_state_key
        else:
            logger.debug(f"PromptAssembler: Preserving existing state prompt for '{agent.agent_id}' (State: {agent.state}).")

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

        # 3.5 Inject Workspace Tree (PM and Worker only)
        if hasattr(agent, 'agent_type') and agent.agent_type in ['pm', 'worker']:
            workspace_tree_report = await self._generate_workspace_tree_report()
            if workspace_tree_report:
                tree_msg: MessageDict = {"role": "system", "content": workspace_tree_report}
                # Insert after the main system prompt but before other history
                if len(history_for_call) > 1:
                    history_for_call.insert(1, tree_msg)
                else:
                    history_for_call.append(tree_msg)
                logger.debug(f"Injected shared_workspace tree report for {agent.agent_type} '{agent.agent_id}'.")

        # 3.55 Inject Worker Assigned Tasks Report (Worker only)
        if hasattr(agent, 'agent_type') and agent.agent_type == AGENT_TYPE_WORKER:
            worker_tasks_report = self._generate_worker_tasks_report(agent)
            if worker_tasks_report:
                tasks_msg: MessageDict = {"role": "system", "content": worker_tasks_report}
                # Insert after system prompt (and workspace tree if present)
                insert_pos = min(2, len(history_for_call))
                history_for_call.insert(insert_pos, tasks_msg)
                logger.debug(f"Injected assigned tasks report for worker '{agent.agent_id}'.")

        # 3.6 Inject Message Read/Ack instructions (Workers and PMs)
        if hasattr(agent, 'agent_type') and agent.agent_type in [AGENT_TYPE_WORKER, AGENT_TYPE_PM]:
            # Count unread messages in history
            unread_messages = []
            for msg in history_for_call:
                msg_id = msg.get("message_id")
                if msg_id and msg_id not in getattr(agent, 'read_message_ids', set()):
                    unread_messages.append(msg_id)

            if unread_messages:
                read_instruction = (
                    f"[MESSAGE ACKNOWLEDGEMENT SYSTEM]\n"
                    f"You have {len(unread_messages)} unread message(s). After reading and understanding each message, "
                    f"acknowledge it by calling: <mark_message_read><message_id>MSG_ID</message_id></mark_message_read>\n"
                    f"Unread message IDs: {', '.join(unread_messages[:5])}\n"
                    f"This will filter the message from your future context to save space. "
                    f"Do NOT mark a message as read until you have fully understood and acted on it."
                )
                read_msg: MessageDict = {"role": "system", "content": read_instruction}
                # Insert after system prompt position
                insert_pos = min(2, len(history_for_call))
                history_for_call.insert(insert_pos, read_msg)
                logger.debug(f"Injected mark_message_read instructions for {agent.agent_type} '{agent.agent_id}' with {len(unread_messages)} unread messages.")

            # 3.7 Report-state safety check: remind worker of unread messages before reporting
            if agent.agent_type == AGENT_TYPE_WORKER and agent.state == WORKER_STATE_REPORT and unread_messages:
                safety_msg = (
                    f"[REPORT SAFETY CHECK - IMPORTANT]\n"
                    f"Before sending your report, verify you have addressed ALL unread messages.\n"
                    f"You have {len(unread_messages)} unread message(s) that may contain instructions you haven't acted on yet.\n"
                    f"Unread IDs: {', '.join(unread_messages[:5])}\n"
                    f"Compare these against your completed sub-tasks to ensure nothing was missed.\n"
                    f"If you find a missed instruction, switch back to worker_work to address it before reporting."
                )
                safety_system_msg: MessageDict = {"role": "system", "content": safety_msg}
                history_for_call.append(safety_system_msg)
                logger.info(f"Injected report safety check for worker '{agent.agent_id}' with {len(unread_messages)} unread messages.")

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
