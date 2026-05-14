# START OF FILE src/agents/workflow_manager.py
import logging
import datetime
import asyncio
import time
from typing import Dict, List, Optional, TYPE_CHECKING, Tuple, Any
import re
import json
import importlib
import inspect
from pathlib import Path
import html # For unescaping title if extracted via regex

from src.agents.constants import (
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK, ADMIN_STATE_STANDBY,
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_STANDBY,
    PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS,
    PM_STATE_REPORT_CHECK, PM_STATE_AUDIT,
    WORKER_STATE_STARTUP, WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK, WORKER_STATE_TEST, WORKER_STATE_REPORT, WORKER_STATE_WAIT,
    DEFAULT_STATE, BOOTSTRAP_AGENT_ID, AGENT_STATUS_IDLE
)
from src.config.settings import settings, BASE_DIR
from src.workflows.base import BaseWorkflow, WorkflowResult
from src.workflows.project_creation_workflow import ProjectCreationWorkflow
from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
from src.api.websocket_manager import broadcast


if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

class AgentWorkflowManager:
    def __init__(self):
        self._valid_states: Dict[str, List[str]] = {
            AGENT_TYPE_ADMIN: [ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK, ADMIN_STATE_STANDBY, DEFAULT_STATE],
            AGENT_TYPE_PM: [PM_STATE_STARTUP, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_REPORT_CHECK, PM_STATE_AUDIT, PM_STATE_STANDBY, DEFAULT_STATE],
            AGENT_TYPE_WORKER: [WORKER_STATE_STARTUP, WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK, WORKER_STATE_TEST, WORKER_STATE_REPORT, WORKER_STATE_WAIT, DEFAULT_STATE]
        }
        self._prompt_map: Dict[Tuple[str, str], str] = {
            (AGENT_TYPE_ADMIN, ADMIN_STATE_STARTUP): "admin_ai_startup_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_CONVERSATION): "admin_ai_conversation_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_PLANNING): "admin_ai_planning_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK_DELEGATED): "admin_ai_delegated_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK): "admin_work_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_STANDBY): "admin_ai_standby_prompt",
            (AGENT_TYPE_ADMIN, DEFAULT_STATE): "default_system_prompt",

            (AGENT_TYPE_PM, PM_STATE_STARTUP): "pm_startup_prompt",

            (AGENT_TYPE_PM, PM_STATE_BUILD_TEAM_TASKS): "pm_build_team_tasks_prompt",       
            (AGENT_TYPE_PM, PM_STATE_ACTIVATE_WORKERS): "pm_activate_workers_prompt",     
            (AGENT_TYPE_PM, PM_STATE_WORK): "pm_work_prompt",
            (AGENT_TYPE_PM, PM_STATE_MANAGE): "pm_manage_prompt",
            (AGENT_TYPE_PM, PM_STATE_REPORT_CHECK): "pm_report_check_prompt",
            (AGENT_TYPE_PM, PM_STATE_AUDIT): "pm_audit_prompt",
            (AGENT_TYPE_PM, PM_STATE_STANDBY): "pm_standby_prompt",
            (AGENT_TYPE_PM, DEFAULT_STATE): "default_system_prompt",

            (AGENT_TYPE_WORKER, WORKER_STATE_STARTUP): "worker_startup_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_DECOMPOSE): "worker_decompose_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_WORK): "worker_work_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_TEST): "worker_test_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_REPORT): "worker_report_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_WAIT): "worker_wait_prompt",
            (AGENT_TYPE_WORKER, DEFAULT_STATE): "default_system_prompt"
        }
        self._standard_instructions_map: Dict[str, str] = {
            AGENT_TYPE_ADMIN: "admin_standard_framework_instructions",
            AGENT_TYPE_PM: "pm_standard_framework_instructions",
            AGENT_TYPE_WORKER: "worker_standard_framework_instructions",
        }
        # Map of common LLM shorthand state names to their actual constant values.
        # Small LLMs (e.g. gemma3:4b) consistently produce short names like 'conversation'
        # instead of the full internal name 'admin_conversation', causing invalid state rejections.
        self._state_aliases: Dict[Tuple[str, str], str] = {
            (AGENT_TYPE_ADMIN, "conversation"): ADMIN_STATE_CONVERSATION,
            (AGENT_TYPE_ADMIN, "admin_conversation"): ADMIN_STATE_CONVERSATION,
            (AGENT_TYPE_ADMIN, "admin_standby"): ADMIN_STATE_STANDBY,
            (AGENT_TYPE_ADMIN, "standby"): ADMIN_STATE_STANDBY,
            (AGENT_TYPE_ADMIN, "admin_work"): ADMIN_STATE_WORK,
            (AGENT_TYPE_ADMIN, "work"): ADMIN_STATE_WORK,
            (AGENT_TYPE_PM, "startup"): PM_STATE_STARTUP,

            (AGENT_TYPE_PM, "build_team_tasks"): PM_STATE_BUILD_TEAM_TASKS,
            (AGENT_TYPE_PM, "activate_workers"): PM_STATE_ACTIVATE_WORKERS,
            (AGENT_TYPE_PM, "standby"): PM_STATE_STANDBY,
            (AGENT_TYPE_PM, "manage"): PM_STATE_MANAGE,
            (AGENT_TYPE_PM, "report_check"): PM_STATE_REPORT_CHECK,
            (AGENT_TYPE_PM, "audit"): PM_STATE_AUDIT,
            (AGENT_TYPE_PM, "work"): PM_STATE_WORK,
            (AGENT_TYPE_WORKER, "startup"): WORKER_STATE_STARTUP,
            (AGENT_TYPE_WORKER, "decompose"): WORKER_STATE_DECOMPOSE,
            (AGENT_TYPE_WORKER, "work"): WORKER_STATE_WORK,
            (AGENT_TYPE_WORKER, "test"): WORKER_STATE_TEST,
            (AGENT_TYPE_WORKER, "report"): WORKER_STATE_REPORT,
            (AGENT_TYPE_WORKER, "wait"): WORKER_STATE_WAIT,
        }
        self.workflows: Dict[str, BaseWorkflow] = {}
        self._workflow_triggers: Dict[Tuple[str, str, str], BaseWorkflow] = {}
        self._discover_and_register_workflows()
        logger.info("AgentWorkflowManager initialized.")

    def _discover_and_register_workflows(self):
        logger.info("AgentWorkflowManager: Discovering and registering workflows...")
        workflows_dir = BASE_DIR / "src" / "workflows"
        package_name = "src.workflows"
        if not workflows_dir.is_dir():
            logger.warning(f"Workflows directory not found at {workflows_dir}. No workflows will be loaded.")
            return
        for filepath in workflows_dir.glob("*.py"):
            module_name_local = filepath.stem
            if module_name_local.startswith("_") or module_name_local == "base":
                logger.debug(f"Skipping workflow module: {module_name_local}")
                continue
            module_name_full = f"{package_name}.{module_name_local}"
            logger.debug(f"Attempting to import workflow module: {module_name_full}")
            try:
                module = importlib.import_module(module_name_full)
                for name, cls in inspect.getmembers(module, inspect.isclass):
                    if (issubclass(cls, BaseWorkflow) and cls is not BaseWorkflow and cls.__module__ == module_name_full):
                        logger.debug(f"  Found potential workflow class: {name} in {module_name_full}")
                        try:
                            instance = cls()
                            if instance.name in self.workflows: logger.warning(f"  Workflow name conflict: '{instance.name}' from {module_name_full} already registered. Overwriting.")
                            self.workflows[instance.name] = instance
                            if instance.allowed_agent_type and instance.allowed_agent_state and instance.trigger_tag_name:
                                trigger_key = (instance.allowed_agent_type, instance.allowed_agent_state, instance.trigger_tag_name)
                                if trigger_key in self._workflow_triggers: logger.warning(f"  Workflow trigger conflict: Key {trigger_key} for workflow '{instance.name}' already points to '{self._workflow_triggers[trigger_key].name}'. Overwriting.")
                                self._workflow_triggers[trigger_key] = instance
                                logger.info(f"  Registered workflow: '{instance.name}' (Trigger: {instance.trigger_tag_name} for {instance.allowed_agent_type} in state {instance.allowed_agent_state})")
                            else: logger.warning(f"  Workflow '{instance.name}' missing required trigger registration attributes.")
                        except Exception as e: logger.error(f"  Error instantiating workflow class {cls.__name__} from {module_name_full}: {e}", exc_info=True)
            except Exception as e: logger.error(f"Error processing workflow module {module_name_full}: {e}", exc_info=True)
        logger.info(f"AgentWorkflowManager: Workflow discovery complete. {len(self.workflows)} workflows loaded. {len(self._workflow_triggers)} triggers registered.")

    def resolve_state_alias(self, agent_type: str, state: str) -> str:
        """Resolve a potentially aliased state name to its canonical form."""
        resolved = self._state_aliases.get((agent_type, state))
        if resolved:
            logger.info(f"WorkflowManager: Resolved state alias '{state}' -> '{resolved}' for agent type '{agent_type}'")
            return resolved
        return state

    def is_valid_state(self, agent_type: str, state: str) -> bool:
        # First try direct match, then try alias resolution
        if state in self._valid_states.get(agent_type, []):
            return True
        resolved = self.resolve_state_alias(agent_type, state)
        return resolved in self._valid_states.get(agent_type, [])

    def change_state(self, agent: 'Agent', requested_state: str, task_description: Optional[str] = None) -> bool:
        if not hasattr(agent, 'agent_type') or not agent.agent_type:
            logger.error(f"Cannot change state for agent '{agent.agent_id}': Missing 'agent_type'.")
            return False
        # Resolve any alias before proceeding
        requested_state = self.resolve_state_alias(agent.agent_type, requested_state)
        if self.is_valid_state(agent.agent_type, requested_state):
            current_state = agent.state
            if current_state != requested_state:
                logger.info(f"WorkflowManager: Changing state for agent '{agent.agent_id}' ({agent.agent_type}) from '{current_state}' to '{requested_state}'.")
                
                # ENHANCED: Check for problematic loop transitions and add context preservation
                if agent.agent_type == AGENT_TYPE_ADMIN and current_state == ADMIN_STATE_WORK:
                    # If transitioning out of work state, ensure task completion is recorded
                    self._record_work_completion(agent, current_state, requested_state)
                    
                    # Clear work-related tracking variables to prevent state contamination
                    if hasattr(agent, '_consecutive_empty_work_cycles'):
                        agent._consecutive_empty_work_cycles = 0
                    if hasattr(agent, '_work_cycle_count'):
                        agent._work_cycle_count = 0
                    if hasattr(agent, 'tool_information_loop_detected'):
                        delattr(agent, 'tool_information_loop_detected')
                    if hasattr(agent, 'tool_execution_loop_detected'):
                        delattr(agent, 'tool_execution_loop_detected')
                
                # If transitioning to the work state, set the task description
                if requested_state == ADMIN_STATE_WORK and task_description:
                    agent.current_task_description = task_description
                    logger.info(f"WorkflowManager: Set task description for agent '{agent.agent_id}' for work state: '{task_description[:100]}...'")

                if agent.agent_type == AGENT_TYPE_ADMIN:
                    # --- DELIVERY OF QUEUED MESSAGES ---
                    if requested_state in [ADMIN_STATE_CONVERSATION, ADMIN_STATE_STANDBY]:
                        if hasattr(agent, 'message_inbox') and agent.message_inbox:
                            logger.info(f"WorkflowManager: Admin AI '{agent.agent_id}' entering safe state '{requested_state}'. Delivering {len(agent.message_inbox)} queued messages from inbox.")
                            agent.message_inbox[0]["content"] = f"[System Note: The following message was queued while you were busy] {agent.message_inbox[0]['content']}"
                            agent.message_history.extend(agent.message_inbox)
                            agent.message_inbox = []
                            # Force fresh system prompt generation so messages are picked up clearly
                            agent._last_system_prompt_state = None
                    # --- END DELIVERY ---

                # Enhanced state tracking for PM completion detection
                if agent.agent_type == AGENT_TYPE_PM:
                    # Track state transition timing for completion detection
                    if not hasattr(agent, '_state_transition_history'):
                        agent._state_transition_history = []
                    
                    agent._state_transition_history.append({
                        'from_state': current_state,
                        'to_state': requested_state,
                        'timestamp': time.time()
                    })
                    
                    # Keep only last 10 transitions to avoid memory issues
                    if len(agent._state_transition_history) > 10:
                        agent._state_transition_history = agent._state_transition_history[-10:]
                    
                    # Check for completion state transition
                    if requested_state == PM_STATE_STANDBY:
                        logger.info(f"WorkflowManager: PM agent '{agent.agent_id}' transitioning to standby state - project likely complete.")
                        
                        # Mark project as complete in agent context
                        agent._project_completed = True
                        agent._project_completion_time = time.time()
                        
                        # Reset loop prevention counters
                        if hasattr(agent, '_periodic_cycle_count'):
                            agent._periodic_cycle_count = 0
                        if hasattr(agent, '_manage_unproductive_cycles'):
                            agent._manage_unproductive_cycles = 0
                        if hasattr(agent, '_pm_report_check_cycle_count'):
                            agent._pm_report_check_cycle_count = 0
                        if hasattr(agent, '_pm_audit_cycle_count'):
                            agent._pm_audit_cycle_count = 0
                            
                        # Clear any completion-related flags
                        if hasattr(agent, '_manage_cycle_cooldown_until'):
                            delattr(agent, '_manage_cycle_cooldown_until')

                agent.state = requested_state

                # PM State-specific logic
                if agent.agent_type == AGENT_TYPE_PM:
                    if requested_state == PM_STATE_MANAGE:
                        if current_state != PM_STATE_ACTIVATE_WORKERS:
                            agent._pm_needs_initial_list_tools = True
                            agent.clear_history()
                            agent._last_system_prompt_state = None  # Force fresh prompt generation after history clear
                            logger.info(f"WorkflowManager: Cleared history for PM agent '{agent.agent_id}' upon entering state 'PM_STATE_MANAGE' to ensure a clean start for the management loop.")
                        else:
                            logger.info(f"WorkflowManager: Preserved history for PM agent '{agent.agent_id}' entering 'PM_STATE_MANAGE' from '{current_state}' to keep completion context.")
                    elif hasattr(agent, '_pm_needs_initial_list_tools'):
                        agent._pm_needs_initial_list_tools = False

                    # Clear history when entering activate_workers state for a clean start.
                    # NOTE: Do NOT clear history for PM_STATE_BUILD_TEAM_TASKS here!
                    # PMKickoffWorkflow injects the MASTER KICKOFF PLAN directive into 
                    # message_history BEFORE returning WorkflowResult with next_agent_state=PM_STATE_BUILD_TEAM_TASKS.
                    # Clearing history here would destroy that critical directive, leaving the PM
                    # with no instructions on what roles/team to create.
                    if requested_state == PM_STATE_ACTIVATE_WORKERS:
                        agent.clear_history()
                        agent._last_system_prompt_state = None  # Force fresh prompt generation after history clear
                        logger.info(f"WorkflowManager: Cleared history for PM agent '{agent.agent_id}' upon entering state '{requested_state}'.")

                    # --- PM AUDIT ATTEMPT TRACKING & CIRCUIT BREAKER ---
                    if requested_state == PM_STATE_AUDIT:
                        agent._pm_audit_attempt_count += 1
                        
                        if agent._pm_audit_attempt_count > 2:
                            logger.warning(f"WorkflowManager: PM '{agent.agent_id}' has attempted audit {agent._pm_audit_attempt_count} times. Injecting warning about repeated audits.")
                            agent.message_history.append({
                                "role": "system",
                                "content": (
                                    f"[Framework Warning]: This is your audit attempt #{agent._pm_audit_attempt_count}. "
                                    "You have already audited this project multiple times. If tasks are still pending, "
                                    "you should focus on managing workers to complete them rather than re-auditing. "
                                    "Use list_tasks to check the ACTUAL task database first."
                                )
                            })
                        
                        # Clear history on audit entry for clean context (same as build_team_tasks)
                        agent.clear_history()
                        agent._last_system_prompt_state = None
                        logger.info(f"WorkflowManager: Cleared history for PM agent '{agent.agent_id}' upon entering state 'pm_audit' (attempt #{agent._pm_audit_attempt_count}).")
                    # --- END AUDIT TRACKING ---

                    # --- DELIVERY OF QUEUED MESSAGES ---
                    if requested_state in [PM_STATE_MANAGE, PM_STATE_STANDBY, PM_STATE_REPORT_CHECK, PM_STATE_AUDIT]:
                        if hasattr(agent, 'message_inbox') and agent.message_inbox:
                            logger.info(f"WorkflowManager: PM agent '{agent.agent_id}' entering safe state '{requested_state}'. Delivering {len(agent.message_inbox)} queued messages from inbox.")
                            # We prefix the first queued message with a small system note for context context
                            agent.message_inbox[0]["content"] = f"[System Note: The following message was queued while you were busy] {agent.message_inbox[0]['content']}"
                            agent.message_history.extend(agent.message_inbox)
                            agent.message_inbox = []
                            # Force fresh system prompt generation so messages are picked up clearly
                            agent._last_system_prompt_state = None
                    # --- END DELIVERY ---
                    
                # Worker State-specific logic
                elif agent.agent_type == AGENT_TYPE_WORKER:
                    # Track state transition history for workers (mirrors PM tracking)
                    if not hasattr(agent, '_state_transition_history'):
                        agent._state_transition_history = []
                    agent._state_transition_history.append({
                        'from_state': current_state,
                        'to_state': requested_state,
                        'timestamp': time.time()
                    })
                    if len(agent._state_transition_history) > 20:
                        agent._state_transition_history = agent._state_transition_history[-20:]

                    # --- DECOMPOSE TRANSITION VALIDATION ---
                    if current_state == WORKER_STATE_DECOMPOSE and requested_state == WORKER_STATE_WORK:
                        valid_transition = True
                        worker_actually_decomposed = False  # Track if this worker created real sub-tasks
                        if hasattr(agent, 'manager') and hasattr(agent.manager, 'tool_executor') and 'project_management' in agent.manager.tool_executor.tools:
                            pm_tool = agent.manager.tool_executor.tools['project_management']
                            try:
                                agent_proj = self._get_agent_project_name(agent, agent.manager)
                                agent_session = agent.manager.current_session
                                tw = pm_tool._get_taskwarrior_instance(agent_proj, agent_session) # type: ignore
                                
                                if tw and getattr(agent, 'current_task_id', None):
                                    aliases = pm_tool._load_aliases(agent_proj, agent_session) # type: ignore
                                    task_id_str = str(agent.current_task_id).strip()
                                    if task_id_str in aliases:
                                        task_id_str = aliases[task_id_str]
                                    
                                    try:
                                        if '-' in task_id_str and len(task_id_str) > 10:
                                            main_task = tw.tasks.get(uuid=task_id_str)
                                        elif task_id_str.isdigit():
                                            main_task = tw.tasks.get(id=int(task_id_str))
                                        else:
                                            main_task = None
                                            
                                        if main_task:
                                            subtasks = tw.tasks.filter(tags=f"parent:{main_task['uuid']}")
                                            
                                            # --- FIX: Distinguish real sub-tasks (created by this worker) from
                                            # unrelated kick-off tasks that merely depend on the parent ---
                                            worker_subtasks = []
                                            for st in subtasks:
                                                try:
                                                    assignee = str(st['assignee'] or '')
                                                except (KeyError, TypeError):
                                                    assignee = ''
                                                if assignee == agent.agent_id:
                                                    worker_subtasks.append(st)
                                            
                                            if len(worker_subtasks) > 0:
                                                # Worker genuinely decomposed: mark parent as decomposed and reassign to PM
                                                worker_actually_decomposed = True
                                                try:
                                                    main_task['task_progress'] = "decomposed"
                                                    
                                                    # Reassign task back to the PM to remove it from worker's active queue
                                                    pm_id = None
                                                    for pm_candidate in agent.manager.agents.values():
                                                        if pm_candidate.agent_type == AGENT_TYPE_PM:
                                                            pm_candidate_project_name = self._get_agent_project_name(pm_candidate, agent.manager)
                                                            if pm_candidate_project_name == agent_proj and pm_candidate.agent_id not in agent.manager.bootstrap_agents:
                                                                pm_id = pm_candidate.agent_id
                                                                break
                                                    if pm_id:
                                                        main_task['assignee'] = pm_id
                                                        
                                                    main_task.save()
                                                    asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": agent_proj, "session_name": agent_session})))
                                                    logger.info(f"WorkflowManager: Auto-marked decomposed parent task '{main_task['uuid']}' as decomposed for agent '{agent.agent_id}'.")
                                                    
                                                    # Provide feedback to the worker that this happened automatically
                                                    auto_complete_msg = (
                                                        f"[Framework Note] Since you successfully created sub-tasks for task '{agent.current_task_id}', "
                                                        f"the system has automatically marked the original parent task as 'decomposed'. "
                                                        f"You can now focus on executing the newly created sub-tasks."
                                                    )
                                                    if not hasattr(agent, 'message_history'):
                                                        agent.message_history = []
                                                    agent.message_history.append({"role": "system", "content": auto_complete_msg})
                                                except Exception as e:
                                                    # Ignore if already completed to avoid crashing
                                                    if "completed" not in str(e).lower() and "Cannot complete a completed task" not in str(e):
                                                        logger.error(f"WorkflowManager: Failed to auto-complete decomposed task '{getattr(main_task, 'uuid', task_id_str)}': {e}")
                                            else:
                                                # Worker skipped decomposition (no sub-tasks assigned to them).
                                                # Allow the transition and let them work on the parent task directly.
                                                logger.info(
                                                    f"WorkflowManager: Worker '{agent.agent_id}' skipped decomposition for task "
                                                    f"'{task_id_str}' (found {len(subtasks)} dependent task(s) but none assigned to this worker). "
                                                    f"Allowing direct work on the original task."
                                                )
                                                # Provide feedback so the worker knows it's working on the original task
                                                skip_decompose_msg = (
                                                    f"[Framework Note] You chose to skip decomposition for task '{agent.current_task_id}'. "
                                                    f"You will now work directly on this task. Focus on completing it efficiently."
                                                )
                                                if not hasattr(agent, 'message_history'):
                                                    agent.message_history = []
                                                agent.message_history.append({"role": "system", "content": skip_decompose_msg})
                                        else:
                                            logger.warning(f"WorkflowManager: Could not retrieve task '{task_id_str}' for validation. Proceeding without sub-task validation.")
                                    except Exception as e:
                                        logger.warning(f"WorkflowManager: Could not retrieve task '{task_id_str}' for validation: {e}. Proceeding without sub-task validation.")
                            except Exception as e:
                                logger.error(f"WorkflowManager: Error validating subtask creation for {agent.agent_id}: {e}", exc_info=True)
                        
                        # --- DECOMPOSE TO WORK: CONTEXT CLEARING ---
                        # Condense history to prevent autoregressive looping where the agent
                        # repeatedly outputs `<request_state state='worker_work'/>`
                        logger.info(f"WorkflowManager: Worker '{agent.agent_id}' transitioning to work state. Condensing history to prevent looping.")
                        if hasattr(agent, 'message_history') and agent.message_history:
                            sys_prompt = agent.message_history[0] if agent.message_history[0].get("role") == "system" else None
                            if sys_prompt:
                                agent.message_history = [sys_prompt]
                            else:
                                agent.message_history = []
                        agent._last_system_prompt_state = None  # Force fresh system prompt generation
                        # --- END DECOMPOSE TO WORK ---
                    # --- END DECOMPOSE TRANSITION VALIDATION ---

                    # --- FIX +44: SOFT NUDGE FOR SKIPPED TESTING ---
                    # When a worker goes directly work → report, check if they produced code files
                    # but never went through worker_test. If so, inject a nudge (but don't block).
                    if current_state == WORKER_STATE_WORK and requested_state == WORKER_STATE_REPORT:
                        CODE_EXTENSIONS = {'.js', '.py', '.html', '.css', '.ts', '.jsx', '.tsx', 
                                          '.sh', '.go', '.rs', '.java', '.c', '.cpp', '.rb', '.php'}
                        
                        # Check if the worker ever visited worker_test during this task
                        visited_test = False
                        if hasattr(agent, '_state_transition_history'):
                            for t in getattr(agent, '_state_transition_history', []):
                                if t.get('to_state') == WORKER_STATE_TEST:
                                    visited_test = True
                                    break
                        
                        if not visited_test:
                            # Scan recent message history for evidence of code file operations
                            produced_code = False
                            code_files_found = []
                            for msg in agent.message_history:
                                content = str(msg.get("content", ""))
                                # Check tool results for file_system write or code_editor operations
                                if msg.get("role") in ("assistant", "tool"):
                                    for ext in CODE_EXTENSIONS:
                                        if ext in content.lower():
                                            # Look for patterns like "File written: foo.js" or "path: game.py"
                                            import re as _re
                                            file_pattern = _re.findall(r'[\w/\\.-]+' + _re.escape(ext) + r'\b', content, _re.IGNORECASE)
                                            if file_pattern:
                                                code_files_found.extend(file_pattern[:3])  # Limit to avoid noise
                                                produced_code = True
                            
                            if produced_code:
                                # Deduplicate file list
                                code_files_found = list(set(code_files_found))[:5]
                                nudge_msg = (
                                    f"[Framework Testing Reminder]: You appear to have created/modified code files "
                                    f"({', '.join(code_files_found)}) but are going directly to REPORT without testing. "
                                    f"Consider transitioning to 'worker_test' state first to verify your code works. "
                                    f"If this is intentional (e.g., non-executable config files), you may proceed to report."
                                )
                                agent.message_history.append({"role": "system", "content": nudge_msg})
                                logger.info(
                                    f"WorkflowManager: Worker '{agent.agent_id}' skipping test state after producing "
                                    f"code files: {code_files_found}. Nudge injected."
                                )
                    # --- END SKIPPED TESTING NUDGE ---

                    # --- DELIVERY OF QUEUED ACTIVATION MESSAGES ---
                    # Only deliver new queued tasks when the worker is fully idle (WAIT state).
                    # Delivering them during WORK disrupts the worker's current focus.
                    if requested_state == WORKER_STATE_WAIT:
                        if hasattr(agent, 'message_inbox') and agent.message_inbox:
                            logger.info(f"WorkflowManager: Worker agent '{agent.agent_id}' entering safe state '{requested_state}'. Delivering {len(agent.message_inbox)} queued messages from inbox.")
                            
                            # Extract any deferred context variables before delivering the messages
                            for msg in agent.message_inbox:
                                if "_deferred_task_description" in msg:
                                    agent._injected_task_description = msg.pop("_deferred_task_description")
                                    agent._needs_initial_work_context = True
                                if "_deferred_task_id" in msg:
                                    agent.current_task_id = msg.pop("_deferred_task_id")
                                    
                            agent.message_history.extend(agent.message_inbox)
                            agent.message_inbox = []
                            agent._last_system_prompt_state = None  # Force fresh system prompt generation
                    # --- END DELIVERY ---

                if hasattr(agent, 'manager') and hasattr(agent.manager, 'send_to_ui'):
                    asyncio.create_task(agent.manager.send_to_ui({
                        "type": "agent_state_change", "agent_id": agent.agent_id,
                        "old_state": current_state, "new_state": requested_state,
                        "message": f"Agent '{agent.agent_id}' state changed from '{current_state}' to '{requested_state}'."
                    }))
                return True
            else:
                logger.debug(f"WorkflowManager: Agent '{agent.agent_id}' already in state '{requested_state}'. No change made.")
                return False # MODIFIED: Return False if no state change occurred
        else:
            logger.warning(f"WorkflowManager: Invalid state transition requested for agent '{agent.agent_id}' ({agent.agent_type}) to state '{requested_state}'. Allowed states: {self._valid_states.get(agent.agent_type, [])}")
            return False

    def _record_work_completion(self, agent: 'Agent', old_state: str, new_state: str) -> None:
        """Record work completion when transitioning out of work state."""
        try:
            work_summary = {
                'completed_at': time.time(),
                'transition_from': old_state,
                'transition_to': new_state,
                'total_cycles': getattr(agent, '_work_cycle_count', 0),
                'task_description': getattr(agent, 'current_task_description', 'N/A')
            }
            
            # Store work completion record
            if not hasattr(agent, '_work_completion_history'):
                agent._work_completion_history = []
            agent._work_completion_history.append(work_summary)
            
            # Keep only last 5 work sessions to avoid memory issues
            if len(agent._work_completion_history) > 5:
                agent._work_completion_history = agent._work_completion_history[-5:]
                
            logger.info(f"WorkflowManager: Recorded work completion for agent '{agent.agent_id}' - "
                       f"{work_summary['total_cycles']} cycles, transitioning from '{old_state}' to '{new_state}'")
                       
        except Exception as e:
            logger.error(f"WorkflowManager: Error recording work completion for '{agent.agent_id}': {e}")

    async def process_agent_output_for_workflow(
        self,
        manager: 'AgentManager',
        agent: 'Agent',
        llm_output: str
    ) -> Optional[WorkflowResult]:
        if not agent.agent_type or not agent.state:
            logger.debug("Agent type or state not set, cannot process for workflow.")
            return None

        content = llm_output.strip()

        # Collect all workflow triggers that match this agent's current type+state
        matching_triggers = []
        for (allowed_type, allowed_state, trigger_tag), workflow_instance in self._workflow_triggers.items():
            if agent.agent_type == allowed_type and agent.state == allowed_state:
                matching_triggers.append((trigger_tag, workflow_instance))

        if not matching_triggers:
            return None

        # --- Strategy 1: JSON Detection (Primary) ---
        json_result = await self._try_json_workflow_trigger(manager, agent, content, matching_triggers)
        if json_result is not None:
            return json_result

        # --- Strategy 2: Legacy XML Fallback (converts to dict for workflow.execute) ---
        xml_result = await self._try_xml_workflow_trigger_fallback(manager, agent, content, matching_triggers)
        if xml_result is not None:
            return xml_result

        return None

    async def _try_json_workflow_trigger(
        self,
        manager: 'AgentManager',
        agent: 'Agent',
        content: str,
        matching_triggers: list
    ) -> Optional[WorkflowResult]:
        """Try to detect and parse a JSON workflow trigger from the agent output."""
        
        # Strip <think> blocks before parsing so they don't interfere with surrounding text validation
        if hasattr(agent, 'think_pattern'):
            think_match = agent.think_pattern.search(content)
            if think_match:
                content = content.replace(think_match.group(0), '', 1).strip()

        json_data = None
        text_before = ""
        text_after = ""

        # Pattern 1: Markdown JSON fence (```json ... ``` or ``` ... ```)
        json_fence_pattern = r'```(?:json)?\s*(\{[\s\S]*?\})\s*```'
        json_fence_match = re.search(json_fence_pattern, content, re.DOTALL)
        if json_fence_match:
            try:
                json_data = json.loads(json_fence_match.group(1), strict=False)
                text_before = content[:json_fence_match.start()].strip()
                text_after = content[json_fence_match.end():].strip()
                logger.debug(f"WorkflowManager: Found JSON in markdown fence for agent '{agent.agent_id}'")
            except json.JSONDecodeError as e:
                logger.debug(f"WorkflowManager: JSON in markdown fence failed to parse: {e}")

        # Pattern 2: Raw JSON object (not in fence)
        if json_data is None:
            raw_json_pattern = r'(\{[\s\S]*\})'
            raw_json_match = re.search(raw_json_pattern, content, re.DOTALL)
            if raw_json_match:
                try:
                    json_data = json.loads(raw_json_match.group(1), strict=False)
                    text_before = content[:raw_json_match.start()].strip()
                    text_after = content[raw_json_match.end():].strip()
                    logger.debug(f"WorkflowManager: Found raw JSON object for agent '{agent.agent_id}'")
                except json.JSONDecodeError as e:
                    logger.debug(f"WorkflowManager: Raw JSON object failed to parse: {e}")

        if json_data is None or not isinstance(json_data, dict):
            return None  # No valid JSON found; caller should try XML fallback

        # Validate surrounding text
        if not self._validate_surrounding_text(agent, text_before, text_after, matching_triggers):
            return None

        # Determine which workflow to use based on JSON content
        trigger_tag, workflow_instance = self._match_json_to_workflow(json_data, matching_triggers)
        if workflow_instance is None:
            logger.debug(f"WorkflowManager: JSON found for agent '{agent.agent_id}' but could not match to any workflow trigger.")
            return None

        logger.info(f"Workflow trigger '{trigger_tag}' matched via JSON for agent '{agent.agent_id}' in state '{agent.state}'. Executing workflow '{workflow_instance.name}'.")

        # Special handling for ProjectCreationWorkflow: ensure _raw_plan_body_ is set
        if isinstance(workflow_instance, ProjectCreationWorkflow):
            if "_raw_plan_body_" not in json_data:
                # Use description/body/plan_body or serialize remaining keys
                json_data["_raw_plan_body_"] = json_data.get("description", json_data.get("body", json_data.get("plan_body", json.dumps(json_data, indent=2))))

        try:
            return await workflow_instance.execute(manager, agent, json_data)
        except Exception as e:
            logger.error(f"Error executing workflow '{workflow_instance.name}' for agent '{agent.agent_id}': {e}", exc_info=True)
            return WorkflowResult(success=False, message=f"Error executing workflow: {e}", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)

    def _match_json_to_workflow(self, json_data: dict, matching_triggers: list) -> tuple:
        """Given parsed JSON and a list of (trigger_tag, workflow_instance) pairs, pick the best match."""
        if len(matching_triggers) == 1:
            return matching_triggers[0]

        # Multiple triggers for this state — disambiguate based on JSON keys
        for trigger_tag, wf in matching_triggers:
            if trigger_tag == "kickoff_plan" and "roles" in json_data and "tasks" in json_data:
                return (trigger_tag, wf)
            elif trigger_tag == "task_list" and "tasks" in json_data and "roles" not in json_data:
                return (trigger_tag, wf)
            elif trigger_tag == "plan" and "title" in json_data:
                return (trigger_tag, wf)

        # Fallback: return the first matching trigger
        return matching_triggers[0]

    def _validate_surrounding_text(self, agent: 'Agent', text_before: str, text_after: str, matching_triggers: list) -> bool:
        """Validate that surrounding text around the JSON block is acceptable (not false positive)."""
        MAX_INSIGNIFICANT_TEXT_LEN = 15
        INSIGNIFICANT_PATTERN = r"^[A-Za-z0-9\s\.,;:!?'\"()\[\]{}`\-_]{0," + str(MAX_INSIGNIFICANT_TEXT_LEN) + r"}$"

        # Check trailing text
        if text_after:
            if not (len(text_after) <= MAX_INSIGNIFICANT_TEXT_LEN and re.fullmatch(INSIGNIFICANT_PATTERN, text_after, re.IGNORECASE)):
                logger.debug(f"WorkflowManager: JSON found for agent '{agent.agent_id}' but problematic trailing text: '{text_after[:50]}...'. Skipping.")
                return False

        # Check leading text
        if text_before:
            # Always allow <think> blocks before JSON
            think_block_pattern = r"^\s*<think>[\s\S]+?</think>\s*$"
            if re.fullmatch(think_block_pattern, text_before):
                logger.info(f"WorkflowManager: Allowed <think> block prefix for agent '{agent.agent_id}'")
                return True

            # Admin plan trigger has general leniency for prefix
            trigger_tags = [t[0] for t in matching_triggers]
            if agent.agent_type == AGENT_TYPE_ADMIN and "plan" in trigger_tags:
                return True
                
            # PM kickoff plan also has general leniency for prefix
            if agent.agent_type == AGENT_TYPE_PM and "kickoff_plan" in trigger_tags:
                return True

            # Otherwise check if it's insignificant
            if not (len(text_before) <= MAX_INSIGNIFICANT_TEXT_LEN and re.fullmatch(INSIGNIFICANT_PATTERN, text_before, re.IGNORECASE)):
                logger.debug(f"WorkflowManager: JSON found for agent '{agent.agent_id}' but problematic prefix: '{text_before[:50]}...'. Skipping.")
                return False

        return True

    async def _try_xml_workflow_trigger_fallback(
        self,
        manager: 'AgentManager',
        agent: 'Agent',
        content: str,
        matching_triggers: list
    ) -> Optional[WorkflowResult]:
        """Legacy XML fallback: detect XML workflow tags, convert to dict, pass to workflow.execute()."""
        
        # Strip <think> blocks before parsing
        if hasattr(agent, 'think_pattern'):
            think_match = agent.think_pattern.search(content)
            if think_match:
                content = content.replace(think_match.group(0), '', 1).strip()

        # Check for markdown fence wrapping
        was_fenced = False
        search_content = content
        fence_match = re.search(r"^```(?:xml|json)?\s*([\s\S]+?)\s*```$", content, re.DOTALL)
        if fence_match:
            was_fenced = True
            search_content = fence_match.group(1).strip()

        for trigger_tag, workflow_instance in matching_triggers:
            try:
                escaped_tag = re.escape(trigger_tag)
                pattern = rf"(<\s*{escaped_tag}(\s+[^>]*)?>)([\s\S]*?)(</\s*{escaped_tag}\s*>)"
                match = re.search(pattern, search_content, re.IGNORECASE | re.DOTALL)

                if not match:
                    continue

                inner_content = match.group(3).strip()
                text_before = search_content[:match.start()].strip()
                text_after = search_content[match.end():].strip()

                # Validate surrounding text using the same helper
                if not self._validate_surrounding_text(agent, text_before, text_after, matching_triggers):
                    continue

                logger.info(f"Workflow trigger '{trigger_tag}' matched via XML fallback for agent '{agent.agent_id}'. Converting to dict.")

                # Convert XML content to dict for the workflow
                data_dict: dict = {}

                if isinstance(workflow_instance, ProjectCreationWorkflow):
                    full_block = match.group(0)
                    title_m = re.search(r"<title>(.*?)</title>", full_block, re.IGNORECASE | re.DOTALL)
                    title_val = html.unescape(title_m.group(1).strip()) if title_m and title_m.group(1) and title_m.group(1).strip() else None
                    if not title_val:
                        logger.error(f"ProjectCreationWorkflow XML fallback: <title> not found in <{trigger_tag}> block.")
                        return WorkflowResult(success=False, message=f"Error: Project title not found in <{trigger_tag}> block.", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)
                    data_dict = {"title": title_val, "_raw_plan_body_": inner_content}

                elif isinstance(workflow_instance, PMKickoffWorkflow) and trigger_tag == "task_list":
                    tasks = []
                    for tm in re.finditer(r"<task>([\s\S]*?)</task>", inner_content, re.IGNORECASE | re.DOTALL):
                        t = html.unescape(tm.group(1).strip())
                        if t:
                            tasks.append({"description": t})
                    if not tasks:
                        return WorkflowResult(success=False, message="Error: No tasks found in <task_list>.", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)
                    data_dict = {"tasks": tasks}

                elif isinstance(workflow_instance, PMKickoffWorkflow) and trigger_tag == "kickoff_plan":
                    full_block = match.group(0)
                    # Extract roles
                    roles = [html.unescape(rm.group(1).strip()) for rm in re.finditer(r"<role>([\s\S]*?)</role>", full_block, re.IGNORECASE | re.DOTALL) if rm.group(1).strip()]
                    # Extract tasks with attributes
                    tasks = []
                    for tm in re.finditer(r"<task(\s+[^>]*)?>([^<]*)</task>", full_block, re.IGNORECASE | re.DOTALL):
                        attrs_str = tm.group(1) or ""
                        desc = html.unescape(tm.group(2).strip())
                        task_dict: dict = {"description": desc}
                        id_m = re.search(r'id="([^"]*)"', attrs_str)
                        dep_m = re.search(r'depends_on="([^"]*)"', attrs_str)
                        if id_m: task_dict["id"] = id_m.group(1)
                        if dep_m: task_dict["depends_on"] = dep_m.group(1)
                        if desc:
                            tasks.append(task_dict)
                    # Extract code_base_definitions
                    cbd_m = re.search(r"<code_base_definitions>([\s\S]*?)</code_base_definitions>", full_block, re.IGNORECASE | re.DOTALL)
                    cbd = cbd_m.group(1).strip() if cbd_m else None
                    # Extract project_structure dirs
                    dirs = []
                    for dm in re.finditer(r'<dir\s+name="([^"]*)"', full_block, re.IGNORECASE):
                        dirs.append({"dir": dm.group(1)})
                    data_dict = {"roles": roles, "tasks": tasks}
                    if cbd: data_dict["code_base_definitions"] = cbd
                    if dirs: data_dict["project_structure"] = dirs
                else:
                    # Generic: try to parse inner content as JSON, else wrap it
                    try:
                        data_dict = json.loads(inner_content)
                    except (json.JSONDecodeError, ValueError):
                        data_dict = {"_raw_content_": inner_content}

                try:
                    return await workflow_instance.execute(manager, agent, data_dict)
                except Exception as e:
                    logger.error(f"Error executing workflow '{workflow_instance.name}' (XML fallback) for agent '{agent.agent_id}': {e}", exc_info=True)
                    return WorkflowResult(success=False, message=f"Error executing workflow: {e}", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)

            except Exception as e:
                logger.error(f"Error during XML fallback workflow trigger check for tag '{trigger_tag}': {e}", exc_info=True)
        return None


    def _get_agent_project_name(self, agent: 'Agent', manager: 'AgentManager') -> str:
        if agent.agent_type == AGENT_TYPE_PM:
            if hasattr(agent, 'agent_config') and 'config' in agent.agent_config:
                if 'project_name_context' in agent.agent_config['config']:
                    return agent.agent_config['config']['project_name_context']
                if 'project_name' in agent.agent_config['config']: 
                    return agent.agent_config['config']['project_name']
            if manager.current_project:
                sanitized_current_project_for_regex = re.escape(re.sub(r'\W+', '_', manager.current_project))
                pm_id_match = re.match(rf"pm_{sanitized_current_project_for_regex}(?:_.*)?", agent.agent_id)
                if pm_id_match:
                    return manager.current_project
            if hasattr(agent, 'initial_plan_description') and isinstance(agent.initial_plan_description, str):
                title_match = re.search(r"<title>(.*?)</title>", agent.initial_plan_description, re.IGNORECASE | re.DOTALL)
                if title_match and title_match.group(1).strip():
                    return title_match.group(1).strip()
        if hasattr(agent, 'agent_config') and 'config' in agent.agent_config and 'project_name_context' in agent.agent_config['config']:
            return agent.agent_config['config']['project_name_context']
        return manager.current_project or "N/A"

    def _get_agent_task_titles(self, agent: 'Agent', manager: 'AgentManager') -> list:
        """Get a list of assigned task titles for an agent.
        Uses the agent's injected task description as the primary source.
        Returns a list of short task title strings."""
        titles = []
        # Check for injected task description (set when worker is activated with a task)
        task_desc = getattr(agent, '_injected_task_description', None)
        if task_desc and isinstance(task_desc, str) and task_desc.strip():
            # Use first line as title, truncate if needed
            first_line = task_desc.strip().split('\n')[0].strip()
            if len(first_line) > 60:
                first_line = first_line[:57] + "..."
            titles.append(first_line)
        return titles

    def _build_team_wip_updates(self, current_agent: 'Agent', manager: 'AgentManager') -> str:
        """Builds a summary of work in progress for all team members."""
        updates = []
        project_name = self._get_agent_project_name(current_agent, manager)
        team_id = manager.state_manager.get_agent_team(current_agent.agent_id)

        relevant_agents = []
        if hasattr(manager, 'agents'):
            for ag in manager.agents.values():
                if ag.agent_type == 'admin': continue # Skip Admin
                ag_proj = self._get_agent_project_name(ag, manager)
                ag_team = manager.state_manager.get_agent_team(ag.agent_id)
                if ag_proj == project_name or ag_team == team_id:
                    relevant_agents.append(ag)

        if not relevant_agents:
            return "No other team members are currently active."

        for ag in relevant_agents:
            # Task info
            tasks = "None"
            if ag.agent_type == "pm":
                tasks = "Managing Project State"
            else:
                task_titles = self._get_agent_task_titles(ag, manager)
                if task_titles:
                    tasks = ", ".join(task_titles)

            # Last action
            last_action_desc = "Idling or awaiting tasks."
            if hasattr(ag, 'state') and ag.state:
                last_action_desc = f"Currently in '{ag.state}' state."
                
            # Try to grab last thought
            last_thought = ""
            if hasattr(ag, 'message_history'):
                for msg in reversed(ag.message_history):
                    if msg.get("role") == "assistant":
                        content = msg.get("content", "")
                        if "<think>" in content:
                            import re
                            match = re.search(r"<think>\s*(.*?)\s*</think>", content, re.DOTALL)
                            if match:
                                thought = match.group(1).strip()
                                # truncate thought
                                if len(thought) > 100: thought = thought[:97] + "..."
                                last_thought = thought
                        if last_thought: break

            if last_thought:
                last_action_desc += f" Last thought: '{last_thought}'"
            
            # Format
            ag_str = f"{ag.agent_id} - {ag.persona}\nWorking on: {tasks}\nLast action(s): {last_action_desc}\n"
            updates.append(ag_str)

        if not updates:
            return "No updates available."
        
        return "\n".join(updates)

    def _build_address_book(self, agent: 'Agent', manager: 'AgentManager') -> str:
        content_lines = []
        agent_type = agent.agent_type
        agent_id = agent.agent_id
        agent_project_name = self._get_agent_project_name(agent, manager)

        if agent_type == AGENT_TYPE_ADMIN:
            content_lines.append(f"- Admin AI (Yourself): {agent_id}")
            # Filter: exclude bootstrapped PM agents when dynamic PMs (PM1, PM2, ...) exist
            all_pms = [ag for ag_id, ag in manager.agents.items() if ag.agent_type == AGENT_TYPE_PM and ag_id != agent_id]
            has_dynamic_pms = any(re.match(r'^PM\d+$', pm.agent_id, re.IGNORECASE) for pm in all_pms)
            pms = [pm for pm in all_pms if not (has_dynamic_pms and pm.agent_id in manager.bootstrap_agents)]
            if pms:
                content_lines.append("- Project Managers (PMs):")
                for pm in pms:
                    pm_proj_name = self._get_agent_project_name(pm, manager)
                    content_lines.append(f"  - PM for '{pm_proj_name}': {pm.agent_id} (Persona: {pm.persona})")
            else:
                content_lines.append("- Project Managers (PMs): (None active currently)")
        elif agent_type == AGENT_TYPE_PM:
            content_lines.append(f"- Project Manager (Yourself): {agent_id} for Project '{agent_project_name}'")
            content_lines.append(f"- Admin AI: {BOOTSTRAP_AGENT_ID}")
            # Filter: exclude bootstrapped PM agents from "other PMs" when dynamic PMs exist
            all_other_pms = [ag for ag_id, ag in manager.agents.items() if ag.agent_type == AGENT_TYPE_PM and ag_id != agent_id]
            has_dynamic_other_pms = any(re.match(r'^PM\d+$', pm.agent_id, re.IGNORECASE) for pm in all_other_pms)
            other_pms = [pm for pm in all_other_pms if not (has_dynamic_other_pms and pm.agent_id in manager.bootstrap_agents)]
            if other_pms:
                content_lines.append("- Other Project Managers:")
                for pm in other_pms:
                    other_pm_proj_name = self._get_agent_project_name(pm, manager)
                    content_lines.append(f"  - PM for '{other_pm_proj_name}': {pm.agent_id} (Persona: {pm.persona})")
            workers_in_my_project = []
            for worker_agent in manager.agents.values():
                if worker_agent.agent_type == AGENT_TYPE_WORKER:
                    # *** FIX: Exclude bootstrap agents unless they have a specific project context ***
                    # This prevents system-level agents (like constitutional_guardian_ai) from leaking
                    # into a project's contact list just because they are 'worker' type.
                    if worker_agent.agent_id in manager.bootstrap_agents:
                        # A bootstrap agent is only part of a project if explicitly assigned.
                        if 'project_name_context' not in worker_agent.agent_config.get('config', {}):
                            continue # Skip this bootstrap agent as it's a general system agent

                    worker_project_name = self._get_agent_project_name(worker_agent, manager)
                    if worker_project_name == agent_project_name:
                        if worker_agent not in workers_in_my_project:
                            workers_in_my_project.append(worker_agent)
            unique_workers = list({w.agent_id: w for w in workers_in_my_project}.values()) 
            if unique_workers:
                content_lines.append(f"- Your Worker Agents (Project '{agent_project_name}'):")
                for worker in unique_workers:
                    worker_role = getattr(worker, 'role', None) or getattr(worker, 'persona', 'General Worker')
                    worker_state = getattr(worker, 'state', 'unknown')
                    worker_tasks = self._get_agent_task_titles(worker, manager)
                    task_info = f" | Tasks: {', '.join(worker_tasks)}" if worker_tasks else ""
                    content_lines.append(f"  - {worker.agent_id} (Role: {worker_role}, State: {worker_state}{task_info})")
            else:
                content_lines.append(f"- Your Worker Agents (Project '{agent_project_name}'): (None created yet or in your project)")
        elif agent_type == AGENT_TYPE_WORKER:
            content_lines.append(f"- Worker (Yourself): {agent_id} for Project '{agent_project_name}'")
            content_lines.append(f"- Admin AI: {BOOTSTRAP_AGENT_ID}")
            # Prefer dynamic PMs (PM1, PM2, ...) over bootstrapped project_manager_agent
            my_pm: Optional['Agent'] = None
            fallback_pm: Optional['Agent'] = None
            for pm_candidate in manager.agents.values():
                if pm_candidate.agent_type == AGENT_TYPE_PM:
                    pm_candidate_project_name = self._get_agent_project_name(pm_candidate, manager)
                    if pm_candidate_project_name == agent_project_name:
                        if pm_candidate.agent_id not in manager.bootstrap_agents:
                            my_pm = pm_candidate; break  # Dynamic PM found, use it
                        elif fallback_pm is None:
                            fallback_pm = pm_candidate  # Keep as fallback
            if my_pm is None:
                my_pm = fallback_pm  # Fall back to bootstrapped PM if no dynamic PM exists
            if my_pm: content_lines.append(f"- Your Project Manager: {my_pm.agent_id} (Persona: {my_pm.persona})")
            else: content_lines.append("- Your Project Manager: (Not identified for this project)")
            team_id = manager.state_manager.get_agent_team(agent_id)
            if team_id:
                team_members = manager.state_manager.get_agents_in_team(team_id)
                other_team_members = [tm for tm in team_members if tm.agent_id != agent_id]
                if other_team_members:
                    content_lines.append(f"- Your Team Members (Team: {team_id}):")
                    for member in other_team_members:
                        member_role = getattr(member, 'role', None) or getattr(member, 'persona', 'Unknown')
                        member_tasks = self._get_agent_task_titles(member, manager)
                        task_info = f" | Tasks: {', '.join(member_tasks)}" if member_tasks else ""
                        content_lines.append(f"  - {member.agent_id} (Role: {member_role}{task_info})")
                else: content_lines.append(f"- Your Team Members (Team: {team_id}): (No other members)")
            else: content_lines.append("- Your Team Members: (Not currently in a team)")
        if not content_lines: return "(No specific contacts identified for your role in the current context)"
        return "\n".join(content_lines)

    def get_system_prompt(self, agent: 'Agent', manager: 'AgentManager') -> str:
        if not hasattr(agent, 'agent_type') or not agent.agent_type:
            logger.error(f"Cannot get prompt for agent '{agent.agent_id}': Missing 'agent_type'. Using default.")
            return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")

        standard_instructions_key = self._standard_instructions_map.get(agent.agent_type)
        standard_instructions_template = settings.PROMPTS.get(standard_instructions_key or "", "Error: Standard instructions template missing.")

        address_book_content = self._build_address_book(agent, manager)
        agent_project_name_for_context = self._get_agent_project_name(agent, manager)
        available_workflow_trigger_info = ""
        if agent.agent_type and agent.state:
            for (allowed_type, allowed_state, trigger_tag), wf_instance in self._workflow_triggers.items():
                if agent.agent_type == allowed_type and agent.state == allowed_state:
                    available_workflow_trigger_info = (
                        f"\n\n**Workflow Trigger:** To initiate the '{wf_instance.name}' process for your current state ('{agent.state}'), "
                        f"your response **MUST BE ONLY** the JSON structure described below (fill in necessary values):\n"
                        f"```json\n{wf_instance.expected_json_schema}\n```" 
                    )
                    break
        json_tool_instructions = """[TOOL USE - CRITICAL FORMATS]
- **Tool Discovery:** To know what tools are available, use `{"action": "list_tools"}`
- **Tool Details:** To know how a specific tool works, first perform the `list_tools` call, then use `{"action": "get_info", "tool_name": "TOOL_NAME"}`

[JSON FORMAT RULES - READ CAREFULLY]
1. Output raw JSON for your tool calls.
2. NEVER nest tool calls or use XML.
3. Each tool has its own JSON schema - get the format using list_tools first
4. Examples of CORRECT formats:
   - List tools: `{"action": "list_tools"}`
   - Get tool info: `{"action": "get_info", "tool_name": "code_editor"}`
   - Use code_editor: `{"action": "replace_chunks", "filepath": "src/main.py", "chunks": [{"search": "old_code", "replace": "new_code"}]}`
5. NEVER put XML parameters inside your response.
6. **IMPORTANT:** ALWAYS use `code_editor` for modifying existing code. Only use `file_system` (write action) for creating BRAND NEW files."""

        native_tool_instructions = """[TOOL USE]
- **Native JSON Tools:** You have access to native JSON tool calling capabilities.
- **Workflow:** Use the provided JSON tool functions to execute actions. Do NOT output XML `<tool_name>...</tool_name>` tags to call tools. Simply call the tool naturally! Your tool calls will be intercepted and executed by the system automatically.
- **IMPORTANT:** ALWAYS use `code_editor` for modifying existing code. Only use `file_system` (write action) for creating BRAND NEW files."""

        # All states now support native tools natively if enabled globally.
        use_native_instructions = settings.NATIVE_TOOL_CALLING_ENABLED

        if agent.state == "pm_startup":
            tool_instructions = "[TOOL USE - RESTRICTED]\nYou are in startup mode. DO NOT use general action tools like file_system or code_editor. Only use the tools required to transition your state, or output the required JSON plan if requested."
        else:
            tool_instructions = native_tool_instructions if use_native_instructions else json_tool_instructions

        json_tool_examples = """[EXAMPLE TOOL USE RESPONSE]
<think>I need to add a new function to the main application logic.</think>
```json
{
  "action": "replace_chunks",
  "filepath": "src/main.py",
  "chunks": [{"search": "def old_func():\\n    pass", "replace": "def old_func():\\n    pass\\n\\ndef new_func():\\n    print('Hello')"}]
}
```

[EXAMPLE: SWITCHING TO REPORT STATE]
<think>I have completed the first milestone. I need to report my progress to the PM.</think>
Call the 'request_state' tool with state='worker_report'"""

        native_tool_examples = """[EXAMPLE TOOL USE RESPONSE]
<think>I need to modify the main logic to add a new parameter.</think>
(Call the code_editor tool using native JSON. Include 'start_line' and 'end_line' keys to target lines, OR use the 'search' string. Do NOT just provide 'replace'.)

[EXAMPLE: SWITCHING TO REPORT STATE]
<think>I have completed the first milestone using my native tools. I need to report my progress to the PM.</think>
(Call the request_state tool here using native JSON)"""

        json_report_examples = """[EXAMPLE: MILESTONE REPORT]
<think>I completed the database schema and saved it. I still have more work to do.</think>
```json
{
  "action": "send_message",
  "target_agent_id": "PM1",
  "message_content": "Milestone complete: Database schema created and saved to db/schema.sql. Moving on to implementing the API endpoints next."
}
```
Call the 'request_state' tool with state='worker_work'

[EXAMPLE: FINAL REPORT]
<think>All sub-tasks are done and the main task is marked completed. Time for my final report.</think>
```json
{
  "action": "send_message",
  "target_agent_id": "PM1",
  "message_content": "Task complete: 'Implement User Authentication'. Created login page (ui/login.html), auth API (api/auth.py), and user model (models/user.py). All files saved to workspace."
}
```
Call the 'request_state' tool with state='worker_wait'"""

        native_report_examples = """[EXAMPLE: MILESTONE REPORT]
<think>I completed the database schema and saved it. I still have more work to do.</think>
(Call both the send_message AND request_state tools here using native JSON)

[EXAMPLE: FINAL REPORT]
<think>All sub-tasks are done and the main task is marked completed. Time for my final report.</think>
(Call both the send_message AND request_state tools here using native JSON)"""

        tool_examples = native_tool_examples if use_native_instructions else json_tool_examples
        report_examples = native_report_examples if use_native_instructions else json_report_examples

        fallback_kb_search = "`{\"action\": \"search_knowledge\", \"query_keywords\": \"architecture,api\"}`"
        json_kb_search = "using the `knowledge_base` tool (search_knowledge action)"
        kb_search_example = json_kb_search if use_native_instructions else fallback_kb_search

        fallback_workspace_list = "`{\"action\": \"list\"}`"
        json_workspace_list = "using the `file_system` tool (list action)"
        workspace_list_example = json_workspace_list if use_native_instructions else fallback_workspace_list

        standard_formatting_context = {
            "agent_id": agent.agent_id, "agent_type": agent.agent_type,
            "team_id": manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
            "project_name": agent_project_name_for_context, "session_name": manager.current_session or 'N/A',
            "current_time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(sep=' ', timespec='seconds'),
            "address_book": address_book_content,
            "available_workflow_trigger": available_workflow_trigger_info,
            "pm_provider": "N/A", # Default if not PM or not found
            "pm_model": "N/A",    # Default if not PM or not found
            "tool_instructions": tool_instructions,
            "team_wip_updates": self._build_team_wip_updates(agent, manager)
        }

        if agent.agent_type == AGENT_TYPE_PM:
            # agent.provider_name should hold the specific provider instance (e.g., ollama-local-...)
            # agent.model should hold the model suffix (e.g., llama3:latest)
            # agent.agent_config['config']['model'] should hold the canonical model (e.g., ollama/llama3:latest)

            # Use the specific provider instance name the agent is actually using
            standard_formatting_context["pm_provider"] = agent.provider_name or "N/A"

            # Use the canonical model name from its config, which includes the base provider prefix
            # This is what the PM should use when instructing other agents.
            if hasattr(agent, 'agent_config') and 'config' in agent.agent_config and 'model' in agent.agent_config['config']:
                standard_formatting_context["pm_model"] = agent.agent_config['config']['model'] or "N/A"
            elif agent.model: # Fallback to agent.model (suffix) if full config not available, less ideal
                standard_formatting_context["pm_model"] = f"{agent.provider_name.split('-local-')[0].split('-proxy')[0]}/{agent.model}" if agent.provider_name else agent.model

        try: formatted_standard_instructions = standard_instructions_template.format(**standard_formatting_context)
        except Exception as e: logger.error(f"Error formatting standard instructions: {e}"); formatted_standard_instructions = standard_instructions_template
        
        # Append Governance Principles to standard instructions
        if hasattr(settings, 'GOVERNANCE_PRINCIPLES') and settings.GOVERNANCE_PRINCIPLES:
            relevant_principles = []
            for gp in settings.GOVERNANCE_PRINCIPLES:
                if not gp.get('enabled', False):
                    continue
                applies_to = gp.get('applies_to', [])
                if "all_agents" in applies_to or agent.agent_type in applies_to:
                    relevant_principles.append(gp)
            
            if relevant_principles:
                gp_text = "\n\n--- Governance Principles ---\n"
                for gp in relevant_principles:
                    gp_text += f"Principle: {gp['name']} (ID: {gp.get('id', 'N/A')})\n{gp['text']}\n"
                gp_text += "--- End Governance Principles ---\n"
                formatted_standard_instructions += gp_text
        # Append Admin Memory Context if available
        if agent.agent_type == AGENT_TYPE_ADMIN:
            admin_memory = None
            if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict):
                config_dict = agent.agent_config.get('config', {})
                admin_memory = config_dict.get('admin_memory_context')
            if admin_memory:
                formatted_standard_instructions += f"\n{admin_memory}\n"
        
        state_prompt_key = self._prompt_map.get((agent.agent_type, agent.state or DEFAULT_STATE)) or self._prompt_map.get((agent.agent_type, DEFAULT_STATE))
        if not state_prompt_key: return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")
        state_prompt_template = settings.PROMPTS.get(state_prompt_key, "Error: State-specific prompt template missing.")
        
        if agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_WORK:
            task_desc_for_prompt = agent.current_task_description
            if not task_desc_for_prompt or task_desc_for_prompt.isspace():
                # Fallback to find the last user message in history
                logger.warning(f"Admin agent {agent.agent_id} in 'work' state has no task description. Searching history for last user message.")
                for msg in reversed(agent.message_history):
                    if msg.get("role") == "user":
                        task_desc_for_prompt = msg.get("content") or ""
                        logger.info(f"Found last user message for Admin work state task: '{task_desc_for_prompt[:100]}...'")
                        break
        else:
            task_desc_for_prompt = getattr(agent, 'initial_plan_description', None)

        # Use injected task description for newly activated workers, overriding other values
        if agent.agent_type == AGENT_TYPE_WORKER:
            if hasattr(agent, '_injected_task_description') and agent._injected_task_description is not None:
                task_desc_for_prompt = agent._injected_task_description

        # Fallback logic for task_description if it's still empty
        if not task_desc_for_prompt or task_desc_for_prompt.isspace():
            if agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_WORK:
                # This now serves as a fallback if the task description was not passed during state change for some reason.
                logger.error(f"CRITICAL: Admin agent {agent.agent_id} in 'work' state has an empty task description even after fallback. The prompt will be empty.")
                task_desc_for_prompt = "No task was provided. You must ask the user for a task."
            elif agent.agent_type == AGENT_TYPE_PM:
                # Check if this is a bootstrapped PM and a dynamic PM already exists
                if agent.agent_id in manager.bootstrap_agents:
                    agent_proj = self._get_agent_project_name(agent, manager)
                    has_dynamic_pm = any(
                        ag.agent_type == AGENT_TYPE_PM and ag.agent_id not in manager.bootstrap_agents
                        and self._get_agent_project_name(ag, manager) == agent_proj
                        for ag in manager.agents.values()
                    )
                    if has_dynamic_pm:
                        logger.info(f"Bootstrapped PM '{agent.agent_id}' has no plan and dynamic PM exists for project '{agent_proj}'. Auto-deactivating.")
                        agent.state = 'pm_idle'
                        agent.status = AGENT_STATUS_IDLE
                        task_desc_for_prompt = 'Standby - dynamic PM has taken over.'
                    else:
                        task_desc_for_prompt = '{task_description}'
                        logger.error(f"CRITICAL: PM agent {agent.agent_id} in state {agent.state} has no 'initial_plan_description' and no injected context. Startup prompt will use a placeholder.")
                else:
                    task_desc_for_prompt = '{task_description}'
                    logger.error(f"CRITICAL: PM agent {agent.agent_id} in state {agent.state} has no 'initial_plan_description' and no injected context. Startup prompt will use a placeholder.")
            elif agent.agent_type == AGENT_TYPE_WORKER:
                task_desc_for_prompt = "No task description provided." # Specific message for Worker
                logger.warning(f"Worker agent {agent.agent_id} in state {agent.state} has no 'initial_plan_description' and no injected context. Using default message.")
            else: # For other agents/states
                task_desc_for_prompt = '{task_description}' # Generic placeholder
                logger.warning(f"Agent {agent.agent_id} ({agent.agent_type}) in state {agent.state} has no task description. Using generic placeholder.")

        personality_text = agent._config_system_prompt.strip() if hasattr(agent, '_config_system_prompt') and agent._config_system_prompt else ""


        state_formatting_context = {
            "agent_id": agent.agent_id, "persona": agent.persona,
            "project_name": agent_project_name_for_context, "session_name": manager.current_session or 'N/A',
            "team_id": manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
            "current_time_utc": standard_formatting_context["current_time_utc"], 
            "pm_agent_id": getattr(agent, 'delegated_pm_id', '{pm_agent_id}'),
            "task_description": task_desc_for_prompt, 
            self._standard_instructions_map.get(agent.agent_type, "standard_framework_instructions"): formatted_standard_instructions,
            "personality_instructions": personality_text,
            "role": getattr(agent, 'role', 'General Worker'),
            "tool_instructions": tool_instructions,
            "tool_examples": tool_examples,
            "report_examples": report_examples,
            "kb_search_example": kb_search_example,
            "workspace_list_example": workspace_list_example,
            "team_wip_updates": standard_formatting_context["team_wip_updates"]
        }
        try:
            final_prompt = state_prompt_template.format(**state_formatting_context)
            logger.info(f"WorkflowManager: Generated prompt for agent '{agent.agent_id}' using state key '{state_prompt_key}'.")
            agent.final_system_prompt = final_prompt 
            return final_prompt
        except KeyError as fmt_err: 
            missing_key = str(fmt_err).strip("'")
            logger.error(f"WorkflowManager: Failed to format state prompt template '{state_prompt_key}'. Missing key: {missing_key}. Prompt before error: {state_prompt_template[:500]}... Context keys: {list(state_formatting_context.keys())}")
            try:
                 fallback_context = {
                     self._standard_instructions_map.get(agent.agent_type, "standard_framework_instructions"): formatted_standard_instructions, 
                     "personality_instructions": state_formatting_context.get("personality_instructions","") 
                 }
                 logger.warning(f"Attempting fallback formatting for '{state_prompt_key}' with minimal context (standard instructions + personality).")
                 final_prompt = (f"{state_formatting_context.get('personality_instructions', '')}\n\n"
                                 f"{state_formatting_context.get(self._standard_instructions_map.get(agent.agent_type, 'standard_framework_instructions'), '')}\n\n"
                                 f"[Warning: Full state-specific prompt formatting failed due to missing key: {missing_key}. You are '{agent.persona}'. Your current task context might be incomplete. Please proceed with caution or request clarification.]")

                 agent.final_system_prompt = final_prompt; return final_prompt
            except Exception as fallback_e:
                 logger.error(f"Fallback formatting also failed for '{state_prompt_key}': {fallback_e}")
                 final_prompt = settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing and state prompt formatting failed.")
                 agent.final_system_prompt = final_prompt; return final_prompt
        except Exception as e:
            logger.error(f"WorkflowManager: Unexpected error formatting state prompt template '{state_prompt_key}': {e}. Using absolute default.", exc_info=True)
            final_prompt = settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")
            agent.final_system_prompt = final_prompt; return final_prompt
