# START OF FILE src/workflows/pm_kickoff_workflow.py
import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, List, Optional

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
    Triggered by a PM agent in PM_STATE_STARTUP outputting a <task_list>.
    This workflow processes the list of tasks, creates them in TaskWarrior
    via the project_management tool, and then transitions the PM agent to
    the PM_STATE_BUILD_TEAM_TASKS state.
    """
    name: str = "pm_project_kickoff"
    trigger_tag_name: str = "task_list"  # PM directly outputs this tag
    allowed_agent_type: str = AGENT_TYPE_PM
    allowed_agent_state: str = PM_STATE_STARTUP
    description: str = (
        "Processes a PM's decomposed task list from <task_list> XML, "
        "creates these kick-off tasks in TaskWarrior, and transitions the PM "
        "to the team building state."
    )
    expected_xml_schema: str = (
        "<task_list>\n"
        "  <task>Description of high-level kick-off task 1</task>\n"
        "  <task>Description of high-level kick-off task 2</task>\n"
        "  <!-- ... more tasks ... -->\n"
        "</task_list>"
    )

    async def execute(
        self,
        manager: 'AgentManager',
        agent: 'Agent',  # This will be the PM agent
        xml_data: ET.Element  # This is the <task_list> element
    ) -> WorkflowResult:
        logger.info(f"Executing PMKickoffWorkflow for PM agent '{agent.agent_id}'.")

        task_descriptions: List[str] = []
        for task_element in xml_data.findall("task"):
            if task_element.text and task_element.text.strip():
                task_descriptions.append(task_element.text.strip())
            else:
                logger.warning(f"PMKickoffWorkflow: Found <task> element with no text content for agent '{agent.agent_id}'. Skipping.")
        
        if not task_descriptions:
            logger.warning(f"PMKickoffWorkflow: No tasks found in <task_list> from agent '{agent.agent_id}'.")
            return WorkflowResult(
                success=False,
                message="No tasks were provided in the <task_list>. Please provide a list of kick-off tasks.",
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

        for i, task_desc in enumerate(task_descriptions):
            task_tool_args = {
                "action": "add_task",
                "description": f"Kick-off Task {i+1}: {task_desc}",
                "priority": "H",
                "project_filter": project_context,
                "tags": ["kickoff", "pm_decomposed", f"task_order_{i+1}"],
                "assignee_agent_id": agent.agent_id
            }
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
            logger.info(f"PMKickoffWorkflow: Cleared history for PM agent '{agent.agent_id}' before transitioning to PM_STATE_BUILD_TEAM_TASKS.")

            # Store the count of kick-off tasks on the agent object
            agent.kick_off_task_count_for_build = len(task_descriptions)
            agent.successfully_created_agent_count_for_build = 0 # Ensure counter is reset
            logger.info(f"PMKickoffWorkflow: Stored kick_off_task_count_for_build={agent.kick_off_task_count_for_build} and reset successfully_created_agent_count_for_build on agent '{agent.agent_id}'.")

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
                    agent_id="framework_internal", # System action
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
                        agent_id="framework_internal", # System action
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

            # Format the task descriptions for inclusion in the message with explicit numbering
            formatted_task_list = "\n".join([f"{i+1}. {desc}" for i, desc in enumerate(task_descriptions)])
            if not formatted_task_list:
                formatted_task_list = "No specific kick-off tasks were itemized in the previous step."
            else:
                formatted_task_list = f"MASTER KICK-OFF TASK LIST:\n{formatted_task_list}"


            directive_message_content = (
                "[Framework System Message]\n"
                "Your previous state 'pm_startup' and its associated task decomposition are complete.\n"
                "The following {num_tasks} kick-off tasks have been identified. This is your **MASTER KICK-OFF TASK LIST** for creating worker agents. When the framework directs you to create worker #N, you MUST use task #N from THIS LIST to define its role and system_prompt:\n"
                "{formatted_task_list}\n\n"
                "You are NOW in state 'pm_build_team_tasks'.\n"
                "Your SOLE FOCUS now is to follow the instructions for 'pm_build_team_tasks' provided in your main system prompt. "
                "The framework will guide you through team creation, getting tool info, and then creating worker agents one by one based on THE KICK-OFF TASKS LISTED ABOVE.\n"
                "Await specific [Framework System Message] directives for each sub-step."
            )

            project_name_context = agent.agent_config.get("config", {}).get("project_name_context", "UnknownProject")

            final_directive_content = directive_message_content.format(
                num_tasks=len(task_descriptions),
                formatted_task_list=formatted_task_list, # This now includes the "MASTER KICK-OFF TASK LIST:" header if tasks exist
                project_name_context=project_name_context
            )

            agent.message_history.append({"role": "system", "content": final_directive_content})
            logger.info(f"PMKickoffWorkflow: Injected directive system message for PM agent '{agent.agent_id}' for state PM_STATE_BUILD_TEAM_TASKS, detailing {len(task_descriptions)} kick-off tasks with explicit numbering.")

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