# START OF FILE src/workflows/pm_kickoff_workflow.py
import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any, List, Optional

from .base import BaseWorkflow, WorkflowResult
from src.agents.constants import (
    AGENT_TYPE_PM,
    PM_STATE_STARTUP, PM_STATE_BUILD_TEAM_TASKS,
    AGENT_STATUS_IDLE
)
from src.config.settings import settings # May not be needed directly here

if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

logger = logging.getLogger(__name__)

class PMKickoffWorkflow(BaseWorkflow):
    """
    Triggered by a PM agent in PM_STATE_STARTUP outputting a <kickoff_plan>.
    This workflow processes the plan, creates tasks in TaskWarrior, stores the
    required roles, and transitions the PM to the team building state.
    """
    name: str = "pm_project_kickoff"
    trigger_tag_name: str = "kickoff_plan"  # PM directly outputs this new tag
    allowed_agent_type: Optional[str] = AGENT_TYPE_PM
    allowed_agent_state: Optional[str] = PM_STATE_STARTUP
    description: str = (
        "Processes a PM's kickoff plan from <kickoff_plan> XML, creating tasks "
        "in TaskWarrior, storing the identified unique roles, and transitioning "
        "the PM to the team building state."
    )
    expected_xml_schema: str = (
        "<kickoff_plan>\n"
        "  <roles>\n"
        "    <role>First Unique Role (e.g., Coder)</role>\n"
        "    <role>Second Unique Role (e.g., Technical_Writer)</role>\n"
        "  </roles>\n"
        "  <tasks>\n"
        "    <task id=\"task_1\">High-level kick-off task 1 description</task>\n"
        "    <task id=\"task_2\" depends_on=\"task_1\">High-level kick-off task 2 description</task>\n"
        "  </tasks>\n"
        "</kickoff_plan>"
    )

    async def execute(
        self,
        manager: 'AgentManager',
        agent: 'Agent',  # This will be the PM agent
        data_input: Any  # This is the <kickoff_plan> element (ET.Element)
    ) -> WorkflowResult:
        logger.info(f"Executing PMKickoffWorkflow for PM agent '{agent.agent_id}'.")

        # --- Extract Roles ---
        roles_element = data_input.find("roles")
        role_names: List[str] = []
        if roles_element is not None:
            for role_element in roles_element.findall("role"):
                if role_element.text and role_element.text.strip():
                    role_names.append(role_element.text.strip())

        if not role_names:
            logger.warning(f"PMKickoffWorkflow: No roles found in <kickoff_plan> from agent '{agent.agent_id}'.")
            return WorkflowResult(
                success=False,
                message="No roles were provided in the <kickoff_plan>. Please provide a list of required roles.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )
        logger.info(f"PMKickoffWorkflow: Extracted {len(role_names)} unique roles for agent '{agent.agent_id}': {role_names}")

        # --- Extract Tasks ---
        tasks_element = data_input.find("tasks")
        task_descriptions: List[str] = []
        task_info_list: List[dict] = []
        if tasks_element is not None:
            for task_element in tasks_element.findall("task"):
                if task_element.text and task_element.text.strip():
                    task_descriptions.append(task_element.text.strip())
                    task_info_list.append({
                        "id": task_element.get("id"),
                        "depends_on": task_element.get("depends_on"),
                        "description": task_element.text.strip()
                    })
        
        if not task_descriptions:
            logger.warning(f"PMKickoffWorkflow: No tasks found in <kickoff_plan> from agent '{agent.agent_id}'.")
            return WorkflowResult(
                success=False,
                message="No tasks were provided in the <kickoff_plan>. Please provide a list of kick-off tasks.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP,
                next_agent_status=AGENT_STATUS_IDLE,
                tasks_to_schedule=[(agent, 0)]
            )
        logger.info(f"PMKickoffWorkflow: Extracted {len(task_descriptions)} tasks for agent '{agent.agent_id}': {task_descriptions}")

        created_tasks_info = []
        failed_tasks_info = []
        all_tasks_created_successfully = True

        project_context = agent.agent_config.get("config", {}).get("project_name_context")
        if not project_context:
            project_context = manager.current_project
            logger.warning(f"PMKickoffWorkflow: project_name_context not found in PM agent config. Using manager.current_project: {project_context}")
        
        if not project_context:
            logger.error(f"PMKickoffWorkflow: Critical error - project context is missing for PM '{agent.agent_id}'. Cannot create tasks.")
            return WorkflowResult(
                success=False,
                message="Critical error: Project context is missing for the PM agent. Cannot create tasks.",
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP, 
                next_agent_status=AGENT_STATUS_IDLE
            )

        for i, task_info in enumerate(task_info_list):
            task_desc = task_info["description"]
            task_tool_args = {
                "action": "add_task",
                "description": f"Kick-off Task {i+1}: {task_desc}",
                "priority": "H",
                "project_filter": project_context,
                "tags": ["kickoff", "pm_decomposed", f"task_order_{i+1}"]
            }
            if task_info.get("id"):
                task_tool_args["task_id"] = task_info["id"]
            if task_info.get("depends_on"):
                task_tool_args["depends"] = task_info["depends_on"]
            try:
                logger.debug(f"PMKickoffWorkflow: Attempting to create task via ToolExecutor: {task_tool_args}")
                task_result = await manager.tool_executor.execute_tool(
                    agent_id=agent.agent_id, 
                    agent_sandbox_path=agent.sandbox_path,
                    tool_name="project_management",
                    tool_args=task_tool_args,
                    project_name=project_context, 
                    session_name=manager.current_session,
                    manager=manager 
                )

                if isinstance(task_result, dict) and task_result.get("status") == "success":
                    task_id = task_result.get("task_id", "N/A")
                    task_uuid = task_result.get("task_uuid", "N/A")
                    created_tasks_info.append(f"Task '{task_desc[:30]}...' (ID: {task_id}, UUID: {task_uuid})")
                    logger.info(f"PMKickoffWorkflow: Successfully created task (ID: {task_id}): {task_desc}")
                else:
                    all_tasks_created_successfully = False
                    error_detail = task_result.get("message", "Unknown error") if isinstance(task_result, dict) else str(task_result)
                    failed_tasks_info.append(f"Task '{task_desc[:30]}...': {error_detail}")
                    logger.error(f"PMKickoffWorkflow: Failed to create task '{task_desc}': {error_detail}. Full result: {task_result}")

            except Exception as e:
                all_tasks_created_successfully = False
                failed_tasks_info.append(f"Task '{task_desc[:30]}...': Exception - {str(e)}")
                logger.error(f"PMKickoffWorkflow: Exception creating task '{task_desc}': {e}", exc_info=True)
        
        if all_tasks_created_successfully:
            logger.info(f"PMKickoffWorkflow: All tasks created for PM '{agent.agent_id}'. Preparing successful result with reschedule.")
            agent.clear_history()
            agent._last_system_prompt_state = None  # Force fresh prompt generation after history clear
            logger.info(f"PMKickoffWorkflow: Cleared history for PM agent '{agent.agent_id}' before transitioning to PM_STATE_BUILD_TEAM_TASKS.")

            # Store the identified roles and tasks on the agent for the next state
            agent.kick_off_roles = role_names
            agent.kick_off_tasks = task_descriptions
            agent.target_worker_agents_for_build = len(role_names)  # *** FIX: Set the target number of agents to build ***
            agent.successfully_created_agent_count_for_build = 0 # Reset counter
            logger.info(f"PMKickoffWorkflow: Stored {len(role_names)} roles, {len(task_descriptions)} tasks, and set target agent count to {len(role_names)} on agent '{agent.agent_id}'.")

            # --- BEGIN MODIFICATION: Mark initial Admin AI task as done ---
            logger.info(f"PMKickoffWorkflow: Attempting to find and complete the initial project plan task for project '{project_context}' assigned to PM '{agent.agent_id}'.")
            initial_project_task_uuid_to_complete = None
            try:
                list_tasks_args = {
                    "action": "list_tasks",
                    "project_filter": project_context,
                    "tags": ["project_kickoff", "auto_created_by_framework"],
                    "assignee_agent_id": agent.agent_id
                }
                logger.debug(f"PMKickoffWorkflow: Listing tasks with args: {list_tasks_args}")
                list_result = await manager.tool_executor.execute_tool(
                    agent_id="framework", # System action
                    agent_sandbox_path=agent.sandbox_path, # PM's sandbox for context
                    tool_name="project_management",
                    tool_args=list_tasks_args,
                    project_name=project_context,
                    session_name=manager.current_session,
                    manager=manager
                )

                if isinstance(list_result, dict) and list_result.get("status") == "success":
                    tasks = list_result.get("tasks", [])
                    logger.debug(f"PMKickoffWorkflow: Found {len(tasks)} candidate tasks for initial project plan task.")
                    found_matching_tasks = []
                    for task_item in tasks:
                        # Check description prefix and ensure it's not already completed
                        if task_item.get("description", "").startswith("PROJECT KICK-OFF:") and task_item.get("status") != "completed":
                            found_matching_tasks.append(task_item)

                    if len(found_matching_tasks) > 1:
                        logger.warning(f"PMKickoffWorkflow: Multiple ({len(found_matching_tasks)}) non-completed 'PROJECT KICK-OFF:' tasks found for PM '{agent.agent_id}' in project '{project_context}'. Will attempt to complete the first one found: {found_matching_tasks[0].get('uuid')}")

                    if found_matching_tasks:
                        initial_project_task_uuid_to_complete = found_matching_tasks[0].get("uuid")
                        logger.info(f"PMKickoffWorkflow: Identified initial project plan task to complete. UUID: {initial_project_task_uuid_to_complete}, Description: '{found_matching_tasks[0].get('description', '')[:50]}...'")
                    else:
                        logger.warning(f"PMKickoffWorkflow: No suitable non-completed 'PROJECT KICK-OFF:' task found for PM '{agent.agent_id}' in project '{project_context}'. It might have been completed manually or does not exist.")
                else:
                    error_msg = list_result.get("message", "Unknown error") if isinstance(list_result, dict) else str(list_result)
                    logger.error(f"PMKickoffWorkflow: Failed to list tasks to find initial project plan task: {error_msg}")

            except Exception as e_list:
                logger.error(f"PMKickoffWorkflow: Exception while trying to list tasks for initial project plan task: {e_list}", exc_info=True)

            if initial_project_task_uuid_to_complete:
                try:
                    complete_task_args = {
                        "action": "complete_task",
                        "task_id": initial_project_task_uuid_to_complete
                    }
                    logger.debug(f"PMKickoffWorkflow: Attempting to complete task with args: {complete_task_args}")
                    complete_result = await manager.tool_executor.execute_tool(
                        agent_id="framework", # System action
                        agent_sandbox_path=agent.sandbox_path, # PM's sandbox for context
                        tool_name="project_management",
                        tool_args=complete_task_args,
                        project_name=project_context,
                        session_name=manager.current_session,
                        manager=manager
                    )
                    if isinstance(complete_result, dict) and complete_result.get("status") == "success":
                        logger.info(f"PMKickoffWorkflow: Successfully marked initial project plan task '{initial_project_task_uuid_to_complete}' as completed.")
                    else:
                        error_msg = complete_result.get("message", "Unknown error") if isinstance(complete_result, dict) else str(complete_result)
                        logger.error(f"PMKickoffWorkflow: Failed to complete initial project plan task '{initial_project_task_uuid_to_complete}': {error_msg}")
                except Exception as e_complete:
                    logger.error(f"PMKickoffWorkflow: Exception while trying to complete initial project plan task '{initial_project_task_uuid_to_complete}': {e_complete}", exc_info=True)
            # --- END MODIFICATION ---

            # NOTE: create_team and tool_information auto-execution has been REMOVED
            # from this workflow. The cycle_handler.py handles these operations when
            # the PM naturally calls those tools, preventing double-execution that
            # caused confusion and duplicate actions.
            team_id = f"team_{project_context}"

            # Format the roles and tasks for inclusion in the message
            formatted_role_xml = "\n".join([f"    <role>{role}</role>" for role in role_names])
            formatted_task_xml = "\n".join([
                f"    <task id=\"{ti.get('id', '')}\" depends_on=\"{ti.get('depends_on', '')}\">{ti['description']}</task>" 
                if ti.get('id') or ti.get('depends_on') 
                else f"    <task>{ti['description']}</task>"
                for ti in task_info_list
            ])

            first_role_to_create = role_names[0] if role_names else "Worker"
            
            directive_message_content = (
                "[Framework System Message]\n"
                "Your previous state 'pm_startup' and its associated plan are complete.\n\n"
                "**MASTER KICKOFF PLAN SUMMARY**\n"
                "<kickoff_plan>\n"
                "  <roles>\n"
                "{formatted_role_xml}\n"
                "  </roles>\n"
                "  <tasks>\n"
                "{formatted_task_xml}\n"
                "  </tasks>\n"
                "</kickoff_plan>\n\n"
                "You are NOW in state 'pm_build_team_tasks'.\n\n"
                "**YOUR WORKFLOW:**\n"
                "Step 1: Create a team using '<manage_team><action>create_team</action><team_id>{team_id}</team_id></manage_team>'\n"
                "Step 2: The framework will automatically retrieve create_agent tool info for you.\n"
                "Step 3: Create one worker agent per role listed above. Do NOT create duplicate roles.\n"
                "Step 4: Once all roles are filled, request state change to 'pm_activate_workers'.\n\n"
                "Your MANDATORY FIRST ACTION is Step 1: Create the team '{team_id}'.\n"
                f"Your FIRST role to create after the team is formed will be: **'{first_role_to_create}'**."
            )

            final_directive_content = directive_message_content.format(
                formatted_role_xml=formatted_role_xml,
                formatted_task_xml=formatted_task_xml,
                team_id=team_id
            )

            agent.message_history.append({"role": "user", "content": final_directive_content})
            logger.info(f"PMKickoffWorkflow: Injected directive user message for PM agent '{agent.agent_id}' for state PM_STATE_BUILD_TEAM_TASKS, detailing {len(task_descriptions)} kick-off tasks with explicit numbering.")

            return WorkflowResult(
                success=True,
                message=f"All {len(task_descriptions)} kick-off tasks created successfully by PM '{agent.agent_id}'. Details: {'; '.join(created_tasks_info)}",
                workflow_name=self.name,
                next_agent_state=PM_STATE_BUILD_TEAM_TASKS,
                next_agent_status=AGENT_STATUS_IDLE,
                ui_message_data={
                    "type": "info", 
                    "agent_id": agent.agent_id, 
                    "content": f"PM '{agent.agent_id}' successfully decomposed plan and created {len(task_descriptions)} kick-off tasks. Stored task count for build phase. Moving to build team."
                },
                tasks_to_schedule=[(agent, 0)]
            )
        else:
            error_summary = f"PM '{agent.agent_id}' failed to create some kick-off tasks. Successfully created: {len(created_tasks_info)}. Failed: {len(failed_tasks_info)} - Details: {'; '.join(failed_tasks_info)}"
            logger.warning(f"PMKickoffWorkflow: Some tasks failed for PM '{agent.agent_id}'. Preparing failure result with reschedule. Summary: {error_summary}")
            return WorkflowResult(
                success=False,
                message=error_summary,
                workflow_name=self.name,
                next_agent_state=PM_STATE_STARTUP, 
                next_agent_status=AGENT_STATUS_IDLE,
                ui_message_data={"type": "error", "agent_id": agent.agent_id, "content": error_summary},
                tasks_to_schedule=[(agent, 0)] 
            )