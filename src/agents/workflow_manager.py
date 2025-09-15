# START OF FILE src/agents/workflow_manager.py
import logging
import datetime
import asyncio
import time
from typing import Dict, List, Optional, TYPE_CHECKING, Tuple, Any
import re
import importlib
import inspect
from pathlib import Path
import xml.etree.ElementTree as ET
import html # For unescaping title if extracted via regex

from src.agents.constants import (
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK, ADMIN_STATE_STANDBY,
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_STANDBY,
    PM_STATE_PLAN_DECOMPOSITION, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS,
    WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT,
    DEFAULT_STATE, BOOTSTRAP_AGENT_ID, AGENT_STATUS_IDLE
)
from src.config.settings import settings, BASE_DIR
from src.workflows.base import BaseWorkflow, WorkflowResult
from src.workflows.project_creation_workflow import ProjectCreationWorkflow
from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow


if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

class AgentWorkflowManager:
    def __init__(self):
        self._valid_states: Dict[str, List[str]] = {
            AGENT_TYPE_ADMIN: [ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK, ADMIN_STATE_STANDBY, DEFAULT_STATE],
            AGENT_TYPE_PM: [PM_STATE_STARTUP, PM_STATE_PLAN_DECOMPOSITION, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_STANDBY, DEFAULT_STATE],
            AGENT_TYPE_WORKER: [WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT, DEFAULT_STATE]
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
            (AGENT_TYPE_PM, PM_STATE_PLAN_DECOMPOSITION): "pm_plan_decomposition_prompt", 
            (AGENT_TYPE_PM, PM_STATE_BUILD_TEAM_TASKS): "pm_build_team_tasks_prompt",       
            (AGENT_TYPE_PM, PM_STATE_ACTIVATE_WORKERS): "pm_activate_workers_prompt",     
            (AGENT_TYPE_PM, PM_STATE_WORK): "pm_work_prompt",
            (AGENT_TYPE_PM, PM_STATE_MANAGE): "pm_manage_prompt",
            (AGENT_TYPE_PM, PM_STATE_STANDBY): "pm_standby_prompt",
            (AGENT_TYPE_PM, DEFAULT_STATE): "default_system_prompt",

            (AGENT_TYPE_WORKER, WORKER_STATE_STARTUP): "worker_startup_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_WORK): "worker_work_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_WAIT): "worker_wait_prompt",
            (AGENT_TYPE_WORKER, DEFAULT_STATE): "default_system_prompt"
        }
        self._standard_instructions_map: Dict[str, str] = {
            AGENT_TYPE_ADMIN: "admin_standard_framework_instructions",
            AGENT_TYPE_PM: "pm_standard_framework_instructions",
            AGENT_TYPE_WORKER: "worker_standard_framework_instructions",
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

    def is_valid_state(self, agent_type: str, state: str) -> bool:
        return state in self._valid_states.get(agent_type, [])

    def change_state(self, agent: 'Agent', requested_state: str) -> bool:
        if not hasattr(agent, 'agent_type') or not agent.agent_type:
            logger.error(f"Cannot change state for agent '{agent.agent_id}': Missing 'agent_type'.")
            return False
        if self.is_valid_state(agent.agent_type, requested_state):
            current_state = agent.state
            if current_state != requested_state:
                logger.info(f"WorkflowManager: Changing state for agent '{agent.agent_id}' ({agent.agent_type}) from '{current_state}' to '{requested_state}'.")
                
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
                            
                        # Clear any completion-related flags
                        if hasattr(agent, '_manage_cycle_cooldown_until'):
                            delattr(agent, '_manage_cycle_cooldown_until')

                agent.state = requested_state

                # PM State-specific logic
                if agent.agent_type == AGENT_TYPE_PM:
                    if requested_state == PM_STATE_MANAGE:
                        agent._pm_needs_initial_list_tools = True
                        agent.clear_history()
                        logger.info(f"WorkflowManager: Cleared history for PM agent '{agent.agent_id}' upon entering state 'PM_STATE_MANAGE' to ensure a clean start for the management loop.")
                    elif hasattr(agent, '_pm_needs_initial_list_tools'):
                        agent._pm_needs_initial_list_tools = False

                    # Per user request, clear history when entering activate_workers state
                    if requested_state == PM_STATE_ACTIVATE_WORKERS:
                        agent.clear_history()
                        logger.info(f"WorkflowManager: Cleared history for PM agent '{agent.agent_id}' upon entering state '{requested_state}'.")

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

    async def process_agent_output_for_workflow(
        self,
        manager: 'AgentManager',
        agent: 'Agent',
        llm_output: str
    ) -> Optional[WorkflowResult]:
        if not agent.agent_type or not agent.state:
            logger.debug("Agent type or state not set, cannot process for workflow.")
            return None

        initial_content_to_process = llm_output.strip()
        was_fenced = False
        content_to_process_for_xml_tag_search = initial_content_to_process

        # Robust Fence Detection: Use regex to find a markdown fence *anywhere*
        # Non-greedy `+?` is important for `([\s\S]+?)`
        markdown_fence_pattern = r"```(?:xml)?\s*([\s\S]+?)\s*```"
        fence_search_match = re.search(markdown_fence_pattern, initial_content_to_process, re.DOTALL)

        if fence_search_match:
            was_fenced = True
            # The new content_to_process_for_xml_tag_search becomes the *inner content* of this first detected fence
            content_to_process_for_xml_tag_search = fence_search_match.group(1).strip()
            logger.debug(f"WorkflowManager: Found Markdown fence. Inner content for XML tag search: '{content_to_process_for_xml_tag_search[:200]}...'")
        else:
            # No fence found, content_to_process_for_xml_tag_search remains the original stripped input
            logger.debug(f"WorkflowManager: No Markdown fence found. Processing original stripped input for XML tags: '{content_to_process_for_xml_tag_search[:200]}...'")

        for (allowed_type, allowed_state, trigger_tag), workflow_instance in self._workflow_triggers.items():
            if agent.agent_type == allowed_type and agent.state == allowed_state:
                try:
                    escaped_trigger_tag = re.escape(trigger_tag)
                    pattern_str = rf"(<\s*{escaped_trigger_tag}(\s+[^>]*)?>([\s\S]*?)</\s*{escaped_trigger_tag}\s*>)"
                    
                    # Search for the XML pattern within the (potentially unfenced) content_to_process_for_xml_tag_search
                    match = re.search(pattern_str, content_to_process_for_xml_tag_search, re.IGNORECASE | re.DOTALL)

                    if match:
                        xml_full_trigger_block = match.group(1).strip()
                        xml_inner_content = match.group(3).strip()
                        
                        text_before_xml_tag = content_to_process_for_xml_tag_search[:match.start()].strip()
                        text_after_xml_tag = content_to_process_for_xml_tag_search[match.end():].strip()
                        
                        # Define what is considered "insignificant" surrounding text.
                        MAX_INSIGNIFICANT_TEXT_LEN_DEFAULT = 15 # For non-fenced content
                        INSIGNIFICANT_TEXT_PATTERN_DEFAULT = r"^[A-Za-z0-9\s\.,;:!?'\"\(\)\[\]\{\}]{0," + str(MAX_INSIGNIFICANT_TEXT_LEN_DEFAULT) + r"}$"

                        MAX_INSIGNIFICANT_TEXT_LEN_FENCED = 1 # Allow potential single newline or space if strip() didn't catch it
                        INSIGNIFICANT_TEXT_PATTERN_FENCED = r"^\s?$" # Allows empty or a single whitespace char

                        if was_fenced:
                            # Inside a fence, text before/after the XML tag must be very minimal (ideally empty)
                            is_problematic_before_fenced = not (len(text_before_xml_tag) <= MAX_INSIGNIFICANT_TEXT_LEN_FENCED and \
                                                               re.fullmatch(INSIGNIFICANT_TEXT_PATTERN_FENCED, text_before_xml_tag))
                            is_problematic_after_fenced = not (len(text_after_xml_tag) <= MAX_INSIGNIFICANT_TEXT_LEN_FENCED and \
                                                              re.fullmatch(INSIGNIFICANT_TEXT_PATTERN_FENCED, text_after_xml_tag))
                            if is_problematic_before_fenced or is_problematic_after_fenced:
                                logger.warning(f"Workflow trigger '{trigger_tag}' found within fenced content for agent '{agent.agent_id}', but has non-minimal surrounding text *inside* the fence. Before: '{text_before_xml_tag}', After: '{text_after_xml_tag}'. Skipping.")
                                continue
                        # Logic for non-fenced content or content where the fence itself might contain the surrounding text
                        else:
                            # Default problematic flags
                            is_problematic_before = False
                            is_problematic_after = False

                            # General check for text_after_xml_tag (applies to all non-fenced)
                            if text_after_xml_tag: # Only check if there's actually text after
                                # Enhanced pattern to allow backticks and other common formatting artifacts
                                ENHANCED_INSIGNIFICANT_TEXT_PATTERN = r"^[A-Za-z0-9\s\.,;:!?'\"\(\)\[\]\{\}`\-_]{0," + str(MAX_INSIGNIFICANT_TEXT_LEN_DEFAULT) + r"}$"
                                
                                if not (len(text_after_xml_tag) <= MAX_INSIGNIFICANT_TEXT_LEN_DEFAULT and \
                                        re.fullmatch(ENHANCED_INSIGNIFICANT_TEXT_PATTERN, text_after_xml_tag, re.IGNORECASE)):
                                    is_problematic_after = True

                            if is_problematic_after:
                                logger.debug(f"Workflow trigger '{trigger_tag}' for agent '{agent.agent_id}' found, but problematic trailing text detected: '{text_after_xml_tag[:50]}...'. Skipping.")
                                continue

                            # Specific check for PM in startup state with "task_list" trigger
                            if agent.agent_type == AGENT_TYPE_PM and \
                               agent.state == PM_STATE_STARTUP and \
                               trigger_tag == "task_list":

                                if text_before_xml_tag: # Only check if there's actually text before
                                    think_block_pattern = r"^\s*<think>[\s\S]+?</think>\s*$" # Allows surrounding whitespace around think block itself
                                    is_just_a_think_block = bool(re.fullmatch(think_block_pattern, text_before_xml_tag))

                                    if not is_just_a_think_block:
                                        # If it's not just a think block, check if it's insignificant text
                                        if not (len(text_before_xml_tag) <= MAX_INSIGNIFICANT_TEXT_LEN_DEFAULT and \
                                                re.fullmatch(INSIGNIFICANT_TEXT_PATTERN_DEFAULT, text_before_xml_tag, re.IGNORECASE)):
                                            is_problematic_before = True
                                    # If it is_just_a_think_block, is_problematic_before remains False (it's allowed)
                                    else:
                                        logger.info(f"PM in startup with '{trigger_tag}': Allowed <think> block prefix: '{text_before_xml_tag[:100]}...'")

                            # General checks for text_before_xml_tag for other cases
                            elif not (agent.agent_type == AGENT_TYPE_ADMIN and trigger_tag == "plan"): # Admin plan has general leniency for prefix
                                if text_before_xml_tag: # Only check if there's actually text before
                                    if not (len(text_before_xml_tag) <= MAX_INSIGNIFICANT_TEXT_LEN_DEFAULT and \
                                            re.fullmatch(INSIGNIFICANT_TEXT_PATTERN_DEFAULT, text_before_xml_tag, re.IGNORECASE)):
                                        is_problematic_before = True

                            if is_problematic_before:
                                logger.debug(f"Workflow trigger '{trigger_tag}' for agent '{agent.agent_id}' found, but problematic prefix detected: '{text_before_xml_tag[:50]}...'. Skipping.")
                                continue
                        
                        logger.info(f"Workflow trigger '{trigger_tag}' matched cleanly for agent '{agent.agent_id}' in state '{agent.state}'. Executing workflow '{workflow_instance.name}'.")
                        xml_element_for_workflow: Optional[ET.Element] = None
                        
                        if isinstance(workflow_instance, ProjectCreationWorkflow):
                            title_match_in_block = re.search(r"<title>(.*?)</title>", xml_full_trigger_block, re.IGNORECASE | re.DOTALL)
                            project_title_from_regex = html.unescape(title_match_in_block.group(1).strip()) if title_match_in_block and title_match_in_block.group(1) and title_match_in_block.group(1).strip() else None
                            if not project_title_from_regex:
                                logger.error(f"ProjectCreationWorkflow: <title> could not be extracted via regex from the <{trigger_tag}> block. Block: {xml_full_trigger_block[:300]}...")
                                return WorkflowResult(success=False, message=f"Error: Project title (<title>) could not be extracted from the <{trigger_tag}> block via regex.", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)
                            plan_root_element = ET.Element(trigger_tag); title_el = ET.SubElement(plan_root_element, "title"); title_el.text = project_title_from_regex
                            raw_body_el = ET.SubElement(plan_root_element, "_raw_plan_body_"); raw_body_el.text = xml_inner_content
                            xml_element_for_workflow = plan_root_element
                        
                        elif isinstance(workflow_instance, PMKickoffWorkflow) and trigger_tag == "task_list":
                            task_list_root = ET.Element("task_list")
                            task_tag_matches = re.finditer(r"<task>([\s\S]*?)</task>", xml_inner_content, re.IGNORECASE | re.DOTALL)
                            tasks_found_count = 0
                            for task_match in task_tag_matches:
                                task_text = html.unescape(task_match.group(1).strip())
                                if task_text:
                                    task_el = ET.SubElement(task_list_root, "task")
                                    task_el.text = task_text
                                    tasks_found_count += 1
                            if tasks_found_count == 0:
                                logger.warning(f"PMKickoffWorkflow: No <task> elements found via regex within the <task_list> inner content: {xml_inner_content[:200]}")
                                return WorkflowResult(success=False, message="Error: No valid <task> elements found inside <task_list>.", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)
                            xml_element_for_workflow = task_list_root
                            logger.debug(f"PMKickoffWorkflow: Reconstructed <task_list> with {tasks_found_count} tasks.")

                        else: 
                            try:
                                xml_element_for_workflow = ET.fromstring(xml_full_trigger_block)
                            except ET.ParseError as e:
                                logger.error(f"Failed to parse XML for workflow trigger '{trigger_tag}': {e}. Content: {xml_full_trigger_block[:200]}...")
                                return WorkflowResult(success=False, message=f"Error: XML for workflow trigger '{trigger_tag}' is not well-formed. Problem: {e}", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)
                        
                        if xml_element_for_workflow is not None:
                            return await workflow_instance.execute(manager, agent, xml_element_for_workflow)
                        else: 
                            logger.error(f"Internal error: xml_element_for_workflow is None after processing for trigger '{trigger_tag}'.")
                            return WorkflowResult(success=False, message=f"Internal error processing workflow '{trigger_tag}'.", workflow_name=workflow_instance.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE)

                except Exception as e:
                    logger.error(f"Error during workflow trigger check for tag '{trigger_tag}': {e}", exc_info=True)
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

    def _build_address_book(self, agent: 'Agent', manager: 'AgentManager') -> str:
        content_lines = []
        agent_type = agent.agent_type
        agent_id = agent.agent_id
        agent_project_name = self._get_agent_project_name(agent, manager)

        if agent_type == AGENT_TYPE_ADMIN:
            content_lines.append(f"- Admin AI (Yourself): {agent_id}")
            pms = [ag for ag_id, ag in manager.agents.items() if ag.agent_type == AGENT_TYPE_PM and ag_id != agent_id]
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
            other_pms = [ag for ag_id, ag in manager.agents.items() if ag.agent_type == AGENT_TYPE_PM and ag_id != agent_id]
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
                    worker_team = manager.state_manager.get_agent_team(worker.agent_id) or "N/A"
                    content_lines.append(f"  - {worker.agent_id} (Persona: {worker.persona}, Team: {worker_team})")
            else:
                content_lines.append(f"- Your Worker Agents (Project '{agent_project_name}'): (None created yet or in your project)")
        elif agent_type == AGENT_TYPE_WORKER:
            content_lines.append(f"- Worker (Yourself): {agent_id} for Project '{agent_project_name}'")
            content_lines.append(f"- Admin AI: {BOOTSTRAP_AGENT_ID}")
            my_pm: Optional['Agent'] = None
            for pm_candidate in manager.agents.values():
                if pm_candidate.agent_type == AGENT_TYPE_PM:
                    pm_candidate_project_name = self._get_agent_project_name(pm_candidate, manager)
                    if pm_candidate_project_name == agent_project_name:
                        my_pm = pm_candidate; break 
            if my_pm: content_lines.append(f"- Your Project Manager: {my_pm.agent_id} (Persona: {my_pm.persona})")
            else: content_lines.append("- Your Project Manager: (Not identified for this project)")
            team_id = manager.state_manager.get_agent_team(agent_id)
            if team_id:
                team_members = manager.state_manager.get_agents_in_team(team_id)
                other_team_members = [tm for tm in team_members if tm.agent_id != agent_id]
                if other_team_members:
                    content_lines.append(f"- Your Team Members (Team: {team_id}):")
                    for member in other_team_members: content_lines.append(f"  - {member.agent_id} (Persona: {member.persona}, Type: {member.agent_type})")
                else: content_lines.append(f"- Your Team Members (Team: {team_id}): (No other members)")
            else: content_lines.append("- Your Team Members: (Not currently in a team)")
        if not content_lines: return "(No specific contacts identified for your role in the current context)"
        return "\n".join(content_lines)

    def get_system_prompt(self, agent: 'Agent', manager: 'AgentManager') -> str:
        if not hasattr(agent, 'agent_type') or not agent.agent_type:
            logger.error(f"Cannot get prompt for agent '{agent.agent_id}': Missing 'agent_type'. Using default.")
            return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")

        standard_instructions_key = self._standard_instructions_map.get(agent.agent_type)
        standard_instructions_template = settings.PROMPTS.get(standard_instructions_key, "Error: Standard instructions template missing.")

        address_book_content = self._build_address_book(agent, manager)
        agent_project_name_for_context = self._get_agent_project_name(agent, manager)
        available_workflow_trigger_info = ""
        if agent.agent_type and agent.state:
            for (allowed_type, allowed_state, trigger_tag), wf_instance in self._workflow_triggers.items():
                if agent.agent_type == allowed_type and agent.state == allowed_state:
                    available_workflow_trigger_info = (
                        f"\n\n**Workflow Trigger:** To initiate the '{wf_instance.name}' process for your current state ('{agent.state}'), "
                        f"your response **MUST BE ONLY** the XML structure described below (fill in necessary values):\n"
                        f"```xml\n{html.escape(wf_instance.expected_xml_schema)}\n```" 
                    )
                    break
        standard_formatting_context = {
            "agent_id": agent.agent_id, "agent_type": agent.agent_type,
            "team_id": manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
            "project_name": agent_project_name_for_context, "session_name": manager.current_session or 'N/A',
            "current_time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(sep=' ', timespec='seconds'),
            "address_book": address_book_content,
            "available_workflow_trigger": available_workflow_trigger_info,
            "pm_provider": "N/A", # Default if not PM or not found
            "pm_model": "N/A"    # Default if not PM or not found
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
        
        state_prompt_key = self._prompt_map.get((agent.agent_type, agent.state)) or self._prompt_map.get((agent.agent_type, DEFAULT_STATE))
        if not state_prompt_key: return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")
        state_prompt_template = settings.PROMPTS.get(state_prompt_key, "Error: State-specific prompt template missing.")
        
        task_desc_for_prompt = getattr(agent, 'initial_plan_description', None)

        # === MODIFICATION START ===
        # Use injected task description for newly activated workers
        if agent.agent_type == AGENT_TYPE_WORKER and agent.state == WORKER_STATE_WORK:
            if hasattr(agent, '_needs_initial_work_context') and agent._needs_initial_work_context and \
               hasattr(agent, '_injected_task_description') and agent._injected_task_description is not None:
                task_desc_for_prompt = agent._injected_task_description
                logger.info(f"WorkflowManager: Using injected task description for worker {agent.agent_id} for initial work context: '{str(task_desc_for_prompt)[:100]}...'")
                agent._needs_initial_work_context = False

        # Refined fallback logic for task_description
        if task_desc_for_prompt is None:
            if agent.agent_type == AGENT_TYPE_PM:
                task_desc_for_prompt = '{task_description}' # Placeholder for PM, critical error
                logger.error(f"CRITICAL: PM agent {agent.agent_id} in state {agent.state} has no 'initial_plan_description' and no injected context. Startup prompt will use a placeholder.")
            elif agent.agent_type == AGENT_TYPE_WORKER:
                task_desc_for_prompt = "No task description provided." # Specific message for Worker
                logger.warning(f"Worker agent {agent.agent_id} in state {agent.state} has no 'initial_plan_description' and no injected context. Using default message.")
            elif agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_WORK and not getattr(agent, 'default_task_assigned', False):
                # This is the fix: Provide a specific, actionable default task for Admin AI when it's in the work state without one.
                logger.warning(f"Admin agent {agent.agent_id} in 'work' state has no task description. Injecting default tool-testing task.")
                task_desc_for_prompt = (
                    "Your current task is to systematically test your available tools. "
                    "You have already listed them. Now, you MUST process the list of tools one by one. "
                    "Pick the first tool from the list that you have not already tested, and get more information about it using the 'get_info' action of the 'tool_information' tool. "
                    "Then, in a subsequent turn, attempt to use one of its actions. Do not list the tools again."
                )
                agent.default_task_assigned = True # Set the flag to prevent re-injection
            else: # For Admin or other types in other states
                task_desc_for_prompt = '{task_description}' # Generic placeholder
                logger.warning(f"Agent {agent.agent_id} ({agent.agent_type}) in state {agent.state} has no task description. Using generic placeholder.")
        # === MODIFICATION END ===

        state_formatting_context = {
            "agent_id": agent.agent_id, "persona": agent.persona,
            "project_name": agent_project_name_for_context, "session_name": manager.current_session or 'N/A',
            "team_id": manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
            "current_time_utc": standard_formatting_context["current_time_utc"], 
            "pm_agent_id": getattr(agent, 'delegated_pm_id', '{pm_agent_id}'),
            "task_description": task_desc_for_prompt, 
            self._standard_instructions_map.get(agent.agent_type, "standard_framework_instructions"): formatted_standard_instructions,
            "personality_instructions": agent._config_system_prompt.strip() if agent.agent_type == AGENT_TYPE_ADMIN and hasattr(agent, '_config_system_prompt') else ""
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
