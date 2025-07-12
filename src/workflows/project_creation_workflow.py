# START OF FILE src/workflows/project_creation_workflow.py
import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Optional
import re # For sanitizing project title and extracting from raw body
import html # For unescaping title

from .base import BaseWorkflow, WorkflowResult
from src.agents.constants import (
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM,
    ADMIN_STATE_PLANNING, ADMIN_STATE_CONVERSATION,
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, 
    PM_STATE_STARTUP 
)
from src.config.settings import settings 

if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

logger = logging.getLogger(__name__)

class ProjectCreationWorkflow(BaseWorkflow):
    """
    Handles the automatic creation of a project (initial Taskwarrior task) and its dedicated
    Project Manager (PM) agent when the Admin AI submits a plan.
    Relies on AgentWorkflowManager to prepare a structured XML input with <title> and <_raw_plan_body_>.
    """
    name: str = "project_creation"
    trigger_tag_name: str = "plan" 
    allowed_agent_type: str = AGENT_TYPE_ADMIN
    allowed_agent_state: str = ADMIN_STATE_PLANNING
    description: str = "Orchestrates project initialization: creates the initial project task and a dedicated Project Manager agent from an Admin AI's plan."
    expected_xml_schema: str = "<plan><title>Project Title</title>\n  <_raw_plan_body_>(Raw plan description, Markdown is okay here)</_raw_plan_body_>\n</plan>"

    async def execute(
        self,
        manager: 'AgentManager',
        agent: 'Agent', 
        xml_data: ET.Element 
    ) -> WorkflowResult:
        logger.info(f"Executing ProjectCreationWorkflow for agent '{agent.agent_id}'. Received xml_data root tag: {xml_data.tag}")

        project_title: Optional[str] = None
        plan_description: str = ""

        title_element = xml_data.find("title")
        if title_element is not None and title_element.text:
            project_title = html.unescape(title_element.text.strip())
            logger.info(f"ProjectCreationWorkflow: Extracted title='{project_title}' from <title> element.")
        else:
            logger.error("ProjectCreationWorkflow: <title> sub-element not found or empty in provided XML data by AgentWorkflowManager.")
            return WorkflowResult(
                success=False, message="Error: Project title (<title>) could not be extracted from the processed plan data.",
                workflow_name=self.name, next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE
            )

        raw_body_element = xml_data.find("_raw_plan_body_")
        if raw_body_element is not None and raw_body_element.text:
            plan_description = raw_body_element.text.strip()
            logger.info(f"ProjectCreationWorkflow: Extracted plan description from <_raw_plan_body_> (length: {len(plan_description)}).")
        else:
            logger.warning("ProjectCreationWorkflow: <_raw_plan_body_> element not found or empty. Plan description might be missing.")
            plan_description = f"Initial project plan for '{project_title}' (Description body was missing from processed plan data)."


        logger.info(f"ProjectCreationWorkflow: Final Extracted Title='{project_title}'. Plan Description (excerpt): '{plan_description[:150]}...'")

        if manager.current_session_db_id:
            logged_plan_content = f"<plan>\n  <title>{html.escape(project_title)}</title>\n  <description_body_for_log>\n{html.escape(plan_description)}\n  </description_body_for_log>\n</plan>"
            await manager.db_manager.log_interaction(
                session_id=manager.current_session_db_id, agent_id=agent.agent_id,
                role="assistant_plan", content=logged_plan_content
            )

        pm_agent_id: Optional[str] = None
        pm_creation_success = False
        pm_creation_message = f"Failed to create PM agent for project '{project_title}'."

        try:
            if not manager.current_project or not manager.current_session:
                raise ValueError("Cannot create PM agent: AgentManager is missing active project/session context.")

            sanitized_project_title_for_id = re.sub(r'\W+', '_', project_title)
            sanitized_session_name_for_id = re.sub(r'\W+', '_', manager.current_session)
            pm_instance_id = f"pm_{sanitized_project_title_for_id}_{sanitized_session_name_for_id}"[:100] 
            pm_bootstrap_config_id = "project_manager_agent"

            if pm_instance_id in manager.agents:
                pm_agent_id = pm_instance_id
                pm_creation_success = True
                pm_creation_message = f"Project Manager agent '{pm_agent_id}' already exists for this project/session."
                logger.info(pm_creation_message)
                existing_pm_agent = manager.agents.get(pm_agent_id)
                if existing_pm_agent:
                    existing_pm_agent._awaiting_project_approval = True
            else:
                pm_config_base = settings.get_agent_config_by_id(pm_bootstrap_config_id)
                if not pm_config_base:
                    raise ValueError(f"Bootstrap config for PM agent ('{pm_bootstrap_config_id}') not found in settings.")

                pm_persona_base = pm_config_base.get("persona")
                if not pm_persona_base:
                    raise ValueError(f"Bootstrap config for PM agent ('{pm_bootstrap_config_id}') is missing 'persona'.")
                pm_persona = f"{pm_persona_base} ({project_title[:50]})"
                
                # System prompt for PM is set by WorkflowManager based on PM_STATE_STARTUP.
                # Pass the system_prompt from config if it exists, otherwise empty string.
                # AgentLifecycle's _create_agent_internal uses this; WorkflowManager then sets state-specific one.
                pm_system_prompt_for_creation = pm_config_base.get("system_prompt", "") 
                
                pm_temp = pm_config_base.get("temperature")
                pm_extra_kwargs = {
                    k: v for k, v in pm_config_base.items()
                    if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona']
                }
                # Pass project_title and plan_description as kwargs for the PM agent to use later
                pm_extra_kwargs['project_name_context'] = project_title 
                pm_extra_kwargs['initial_plan_description'] = plan_description

                logger.info(f"ProjectCreationWorkflow: Calling create_agent_instance for PM '{pm_instance_id}'. Provider from config: {pm_config_base.get('provider')}, Model from config: {pm_config_base.get('model')}")
                pm_creation_success, pm_creation_message, pm_agent_id = await manager.create_agent_instance(
                    agent_id_requested=pm_instance_id,
                    provider=pm_config_base.get("provider"), 
                    model=pm_config_base.get("model"),       
                    system_prompt=pm_system_prompt_for_creation, # This is the base prompt from config
                    persona=pm_persona, 
                    team_id=None, 
                    temperature=pm_temp,
                    **pm_extra_kwargs
                )
                if pm_creation_success and pm_agent_id:
                    new_pm_agent = manager.agents.get(pm_agent_id)
                    if new_pm_agent:
                        new_pm_agent._awaiting_project_approval = True 
                        # AgentWorkflowManager will be called to set the *actual* startup prompt during the PM's first cycle
                        # Here we just ensure its initial state is PM_STATE_STARTUP
                        if hasattr(manager, 'workflow_manager'):
                            manager.workflow_manager.change_state(new_pm_agent, PM_STATE_STARTUP)
                        else:
                            logger.error("WorkflowManager not available on AgentManager, cannot set initial PM state.")
                            new_pm_agent.set_state(PM_STATE_STARTUP) # Fallback
                        logger.info(f"ProjectCreationWorkflow: PM agent '{pm_agent_id}' created. Status: {new_pm_agent.status}, State: {new_pm_agent.state}. Marked for approval.")
                    else: 
                        pm_creation_success = False
                        pm_creation_message = "PM agent was reported as created but its instance was not found in the manager."
                elif not pm_creation_success:
                     logger.error(f"PM agent creation call failed: {pm_creation_message}")

        except Exception as e:
            logger.error(f"ProjectCreationWorkflow: Error during PM agent creation for '{project_title}': {e}", exc_info=True)
            pm_creation_message = f"Error creating PM agent: {str(e)}"
            pm_creation_success = False
        
        if not pm_creation_success or not pm_agent_id: 
            return WorkflowResult(
                success=False, message=f"PM agent creation failed: {pm_creation_message}", workflow_name=self.name,
                next_agent_state=agent.state, next_agent_status=AGENT_STATUS_IDLE 
            )

        task_creation_success = False
        task_creation_details = "PM created successfully, task creation initializing." 

        try:
            logger.info(f"ProjectCreationWorkflow: Attempting to add 'Project Kick-off' task via ToolExecutor for PM '{pm_agent_id}'...")
            tool_args = {
                "action": "add_task",
                "description": f"PROJECT KICK-OFF: {project_title}\n\nFull Plan Overview:\n{plan_description}",
                "priority": "H",
                "project_filter": project_title,
                "tags": ["project_kickoff", "auto_created_by_framework", f"+{pm_agent_id}"], # Ensure tag for assignee
                "assignee_agent_id": pm_agent_id
            }
            pm_agent_for_sandbox = manager.agents.get(pm_agent_id)
            if not pm_agent_for_sandbox:
                 raise ValueError(f"Critical: Could not find PM agent '{pm_agent_id}' instance for sandbox path during task creation.")

            task_result = await manager.tool_executor.execute_tool(
                agent_id="framework", 
                agent_sandbox_path=pm_agent_for_sandbox.sandbox_path, 
                tool_name="project_management",
                tool_args=tool_args,
                project_name=project_title, # Use the extracted project_title for this tool call
                session_name=manager.current_session
            )

            if isinstance(task_result, dict) and task_result.get("status") == "success":
                task_id_val = task_result.get("task_id", "N/A") 
                task_uuid_val = task_result.get("task_uuid", "N/A")
                task_creation_success = True
                task_creation_details = f"Initial 'Project Kick-off' task (ID: {task_id_val}, UUID: {task_uuid_val}) created and assigned to PM '{pm_agent_id}'."
                logger.info(f"ProjectCreationWorkflow Task Create (Success): {task_creation_details}")
            else:
                error_detail = task_result.get("message", "Unknown error from tool execution.") if isinstance(task_result, dict) else str(task_result)
                task_creation_success = False
                task_creation_details = f"Failed to create 'Project Kick-off' task: {error_detail}"
                logger.error(f"ProjectCreationWorkflow Task Create (Failure): {task_creation_details}. Full task_result: {task_result}")

        except Exception as task_err:
            task_creation_success = False
            task_creation_details = f"Exception during 'Project Kick-off' task creation: {str(task_err)}"
            logger.error(f"ProjectCreationWorkflow Task Create (Exception): {task_creation_details}", exc_info=True)
        
        final_message: str 
        ui_message_data: Optional[dict] = None
        overall_success = pm_creation_success and task_creation_success

        if overall_success:
            final_message = f"Project '{project_title}' (PM: {pm_agent_id}) initialized. {task_creation_details} Project is now awaiting user approval to start."
        elif pm_creation_success: 
            final_message = f"PM agent '{pm_agent_id}' created for project '{project_title}', but initial task creation failed: {task_creation_details}. Project awaiting user approval; PM may need to create tasks manually or retry."
        else: 
            final_message = f"Project creation failed for '{project_title}'. PM agent creation status: {pm_creation_message}. Task creation status: {task_creation_details}"


        ui_message_data = {
            "type": "project_pending_approval", "project_title": project_title,
            "plan_content": plan_description, "pm_agent_id": pm_agent_id, 
            "message": f"Project '{project_title}' (PM: {pm_agent_id}) has been planned. {task_creation_details} Please approve to start."
        }
        
        logger.info(f"ProjectCreationWorkflow result: Success={overall_success}, Message: {final_message}")
        
        return WorkflowResult(
            success=overall_success, 
            message=final_message,
            workflow_name=self.name,
            next_agent_state=ADMIN_STATE_CONVERSATION, 
            next_agent_status=AGENT_STATUS_IDLE,
            ui_message_data=ui_message_data
        )