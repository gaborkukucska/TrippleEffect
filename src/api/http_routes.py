# START OF FILE src/api/http_routes.py
from fastapi import APIRouter, Request, HTTPException, status as http_status, Depends # Added Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from pathlib import Path
import logging
import os # For listing directories
import time # For default session names

# Import settings to access loaded configurations (defaults mostly) and PROJECTS_BASE_DIR
from src.config.settings import settings

# Import the ConfigManager singleton instance for agent config CRUD
from src.config.config_manager import config_manager

# Import auth dependency
from src.api.auth import get_current_user
from src.core.database_manager import User

# --- Type Hinting & Direct Import for AgentManager ---
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
# Add direct import to resolve runtime NameError during FastAPI dependency evaluation
from src.agents.manager import AgentManager
# --- NEW: Import relevant agent status and state constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_ERROR,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK,
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_STANDBY,
    WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT,
    DEFAULT_STATE,
    BOOTSTRAP_AGENT_ID, AGENT_TYPE_PM, AGENT_TYPE_WORKER
)
# --- END NEW ---
import asyncio # Import asyncio

logger = logging.getLogger(__name__)

# Define the path to the templates directory relative to this file
TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

# Check if template directory exists
if not TEMPLATE_DIR.exists():
    logger.warning(f"Templates directory not found at {TEMPLATE_DIR}") # Use logger

# Initialize Jinja2Templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Create an API router instance
router = APIRouter()

# --- *** MODIFIED Dependency Function to get AgentManager *** ---
def get_agent_manager_dependency(request: Request) -> 'AgentManager':
    """FastAPI dependency to get the AgentManager instance from app.state."""
    # Retrieve the manager instance stored in app.state by the lifespan startup event
    manager = getattr(request.app.state, 'agent_manager', None)
    if manager is None:
        # This should not happen if lifespan completes successfully
        logger.critical("CRITICAL ERROR: AgentManager instance not found in request.app.state!")
        raise HTTPException(status_code=500, detail="Internal Server Error: Agent Manager not initialized")
    # logger.debug(f"Dependency: Returning AgentManager instance from app.state: {id(manager)}") # Optional: Debug instance ID
    return manager
# --- *** END MODIFICATION *** ---


# --- Pydantic Models ---
class AgentInfo(BaseModel):
    agent_id: str
    provider: str
    model: str
    persona: str

class GeneralResponse(BaseModel):
    success: bool
    message: str
    details: Optional[str] = None

class SessionInfo(BaseModel):
    project_name: str
    session_name: str

class ProjectInfo(BaseModel):
    project_name: str
    sessions: Optional[List[str]] = None

class SaveSessionInput(BaseModel):
    session_name: Optional[str] = Field(None, description="Optional name for the session. If omitted, a timestamp-based name is generated.")

class AgentConfigInput(BaseModel):
    provider: str = Field(..., description="Provider name ('openai', 'ollama', 'openrouter', etc.)")
    model: str = Field(..., description="Model name specific to the provider.")
    system_prompt: Optional[str] = Field(settings.DEFAULT_SYSTEM_PROMPT, description="The system prompt for the agent.")
    temperature: Optional[float] = Field(settings.DEFAULT_TEMPERATURE, description="Sampling temperature (e.g., 0.7).")
    persona: Optional[str] = Field(settings.DEFAULT_PERSONA, description="Display name for the agent.")
    class Config: extra = "allow"

class AgentConfigCreate(BaseModel):
    agent_id: str = Field(..., description="Unique identifier for the agent (e.g., 'coder_v2'). Cannot contain spaces or special characters.", pattern=r"^[a-zA-Z0-9_-]+$")
    config: AgentConfigInput = Field(..., description="The configuration settings for the agent.")

class ProviderSetupInput(BaseModel):
    provider: str = Field(..., description="The name of the provider to setup (e.g., 'openai', 'openrouter', 'vllm', 'ollama')")
    api_key: Optional[str] = Field(None, description="The API key for the provider")
    base_url: Optional[str] = Field(None, description="The base URL for the provider if applicable")

class ProviderStatusResponse(BaseModel):
    has_providers: bool
    message: str


# --- HTTP Routes ---

@router.get("/", response_class=HTMLResponse)
async def get_index_page(request: Request):
    """ Serves the main index.html page. """
    try:
        template_path = TEMPLATE_DIR / "index.html"
        if not template_path.is_file():
             logger.error(f"Template file index.html not found at {template_path}")
             # Return a simple HTML error page if template is missing
             error_html = f"<html><body><h1>Template Missing</h1><p>Could not load the main application page. Expected template at:</p><pre>{template_path}</pre></body></html>"
             return HTMLResponse(content=error_html, status_code=500)
        # Pass the request object to the template context
        return templates.TemplateResponse("index.html", {"request": request})  # type: ignore
    except Exception as e:
        # Catch potential errors during template rendering
        import traceback
        trace = traceback.format_exc()
        logger.error(f"Error rendering template index.html: {e}\n{trace}")
        error_html = f"<html><body><h1>Internal Server Error</h1><p>An unexpected error occurred while loading the page:</p><pre>{trace}</pre></body></html>"
        return HTMLResponse(content=error_html, status_code=500)

# --- Provider Setup API Endpoints ---

@router.get("/api/config/providers/status", response_model=ProviderStatusResponse)
async def get_provider_status(manager: AgentManager = Depends(get_agent_manager_dependency), current_user: User = Depends(get_current_user)):
    """ API endpoint to check if any LLM providers are available in the system. """
    try:
        # Checking if available_models is populated by ModelRegistry
        has_providers = len(manager.model_registry.available_models) > 0
        return ProviderStatusResponse(has_providers=has_providers, message="Providers found." if has_providers else "No providers available.")
    except Exception as e:
        logger.error(f"Error checking provider status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to check provider status: {e}")

@router.post("/api/config/providers/setup", response_model=GeneralResponse)
async def setup_initial_provider(setup_data: ProviderSetupInput, current_user: User = Depends(get_current_user)):
    """ API endpoint to set up an initial LLM provider by appending to .env. """
    try:
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        
        provider = setup_data.provider.upper()
        api_key = setup_data.api_key
        base_url = setup_data.base_url
        
        env_lines = []
        if api_key:
            if provider == "OLLAMA" or provider == "VLLM":
                # For local providers, we might not strictly need an API key, but VLLM can use one
                if provider == "VLLM":
                    env_lines.append(f"{provider}_API_KEY=\"{api_key}\"\n")
            else:
                env_lines.append(f"{provider}_API_KEY=\"{api_key}\"\n")
                
        if base_url:
            env_lines.append(f"{provider}_BASE_URL=\"{base_url}\"\n")
            if provider == "OLLAMA":
                env_lines.append(f"OLLAMA_API_URLS=\"{base_url}\"\n")
            elif provider == "VLLM":
                env_lines.append(f"VLLM_API_URLS=\"{base_url}\"\n")
                
        # Also ensure LOCAL_API_SCAN_ENABLED might be helpful to keep true, or configure default models
        # Let's write the append string
        append_str = "\n# --- Auto-configured via UI ---\n" + "".join(env_lines)
        
        if env_lines:
            with open(env_path, "a") as f:
                f.write(append_str)
            logger.info(f"Appended initial provider configuration for {provider} to .env file.")
            
            # Start shutdown sequence async so the response goes through
            async def _send_signal():
                import signal
                await asyncio.sleep(1) # delay to let response complete
                logger.info("Sending SIGINT to trigger graceful shutdown after provider setup...")
                os.kill(os.getpid(), signal.SIGINT)
            
            asyncio.create_task(_send_signal())
            
            return GeneralResponse(success=True, message="Provider settings saved. The system is restarting. Please wait a moment and refresh the page.")
        else:
             return GeneralResponse(success=False, message="No configuration details provided.")
             
    except Exception as e:
        logger.error(f"Error setting up initial provider: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to setup initial provider: {e}")

# --- Agent Config CRUD API Endpoints (using ConfigManager, no AgentManager needed) ---

@router.get("/api/config/agents", response_model=List[AgentInfo])
async def get_agent_configurations(current_user: User = Depends(get_current_user)):
    """ API endpoint to retrieve a list of configured agents (basic info only). """
    agent_info_list: List[AgentInfo] = []
    try:
        raw_configs = await config_manager.get_config() # Gets only the 'agents' list
        if not raw_configs: return []
        if not isinstance(raw_configs, list):
             logger.error(f"ConfigManager.get_config() did not return a list, type: {type(raw_configs)}")
             raise HTTPException(status_code=500, detail="Internal error reading configuration structure.")
        for agent_conf_entry in raw_configs:
            agent_id = agent_conf_entry.get("agent_id")
            config_dict = agent_conf_entry.get("config", {})
            provider = config_dict.get("provider", settings.DEFAULT_AGENT_PROVIDER)
            model = config_dict.get("model", settings.DEFAULT_AGENT_MODEL)
            persona = config_dict.get("persona", settings.DEFAULT_PERSONA)
            if agent_id:
                agent_info_list.append(AgentInfo(agent_id=agent_id, provider=provider, model=model, persona=persona))
        return agent_info_list
    except Exception as e:
        logger.error(f"Error retrieving agent configurations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agent configurations: {e}")

@router.post("/api/config/agents", response_model=GeneralResponse, status_code=http_status.HTTP_201_CREATED)
async def create_agent_configuration(agent_data: AgentConfigCreate, current_user: User = Depends(get_current_user)):
    """ API endpoint to add a new agent configuration to config.yaml. Requires restart. """
    try:
        logger.info(f"Received request to create agent: {agent_data.agent_id}")
        agent_config_entry = {"agent_id": agent_data.agent_id, "config": agent_data.config.model_dump(exclude_unset=True)}
        success = await config_manager.add_agent(agent_config_entry)
        if success:
            return GeneralResponse(success=True, message=f"Agent '{agent_data.agent_id}' added. Restart application for changes to take effect.")
        else:
            current_config = await config_manager.get_config()
            if any(agent.get("agent_id") == agent_data.agent_id for agent in current_config):
                 raise HTTPException(status_code=409, detail=f"Agent with ID '{agent_data.agent_id}' already exists.")
            else:
                 raise HTTPException(status_code=400, detail=f"Failed to add agent '{agent_data.agent_id}'. Check server logs.")
    except HTTPException as http_exc: raise http_exc
    except Exception as e:
        logger.error(f"Error creating agent configuration for '{agent_data.agent_id}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent configuration: {e}")

@router.put("/api/config/agents/{agent_id}", response_model=GeneralResponse)
async def update_agent_configuration(agent_id: str, agent_config_data: AgentConfigInput, current_user: User = Depends(get_current_user)):
    """ API endpoint to update an existing agent's configuration in config.yaml. Requires restart. """
    try:
        logger.info(f"Received request to update agent: {agent_id}")
        updated_config_dict = agent_config_data.model_dump(exclude_unset=True)
        success = await config_manager.update_agent(agent_id, updated_config_dict)
        if success:
            return GeneralResponse(success=True, message=f"Agent '{agent_id}' updated. Restart application for changes to take effect.")
        else:
            current_config = await config_manager.get_config()
            if not any(agent.get("agent_id") == agent_id for agent in current_config):
                 raise HTTPException(status_code=404, detail=f"Agent with ID '{agent_id}' not found.")
            else:
                raise HTTPException(status_code=400, detail=f"Failed to update agent '{agent_id}'. Check server logs.")
    except HTTPException as http_exc: raise http_exc
    except Exception as e:
        logger.error(f"Error updating agent configuration for '{agent_id}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update agent configuration: {e}")

@router.delete("/api/config/agents/{agent_id}", response_model=GeneralResponse)
async def delete_agent_configuration(agent_id: str, current_user: User = Depends(get_current_user)):
    """ API endpoint to remove an agent configuration from config.yaml. Requires restart. """
    try:
        logger.info(f"Received request to delete agent: {agent_id}")
        success = await config_manager.delete_agent(agent_id)
        if success:
            return GeneralResponse(success=True, message=f"Agent '{agent_id}' deleted. Restart application for changes to take effect.")
        else:
             all_configs = await config_manager.get_full_config()
             agent_still_exists = any(a.get("agent_id") == agent_id for a in all_configs.get("agents", []))
             if not agent_still_exists:
                 logger.error(f"Agent '{agent_id}' not found for deletion (or already deleted but save failed?).")
                 raise HTTPException(status_code=404, detail=f"Agent with ID '{agent_id}' not found.")
             else:
                 logger.error(f"Failed to delete agent '{agent_id}' using ConfigManager (likely save failed).")
                 raise HTTPException(status_code=500, detail=f"Failed to delete agent '{agent_id}'. Check server logs for save errors.")
    except HTTPException as http_exc: raise http_exc
    except Exception as e:
        logger.error(f"Error deleting agent configuration for '{agent_id}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent configuration: {e}")


# --- Project/Session Management API Endpoints (Requires AgentManager) ---

@router.get("/api/projects", response_model=List[ProjectInfo])
async def list_projects(current_user: User = Depends(get_current_user)):
    """ Lists available projects by scanning the projects base directory. """
    projects = []
    base_dir = settings.PROJECTS_BASE_DIR
    if not base_dir.is_dir():
        logger.warning(f"Projects base directory not found or is not a directory: {base_dir}")
        return []
    try:
        for item in base_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                projects.append(ProjectInfo(project_name=item.name))
        return projects
    except Exception as e:
        logger.error(f"Error listing projects in {base_dir}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list projects: {e}")


@router.get("/api/projects/{project_name}/sessions", response_model=List[SessionInfo])
async def list_sessions(project_name: str, current_user: User = Depends(get_current_user)):
    """ Lists available sessions within a specific project directory by checking for agent_session_data.json. """
    sessions = []
    project_dir = settings.PROJECTS_BASE_DIR / project_name
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")
    try:
        for item in project_dir.iterdir():
            # Check if it's a directory and not hidden
            if item.is_dir() and not item.name.startswith('.'):
                # Look for the correct session data file name
                session_file = item / "agent_session_data.json" # Correct filename
                if session_file.is_file():
                    # If the correct session file exists, list it
                    sessions.append(SessionInfo(project_name=project_name, session_name=item.name))
                else:
                    # Log if a directory exists but doesn't contain the expected file
                    logger.warning(f"Directory '{item.name}' in project '{project_name}' exists but missing '{session_file.name}', not listed as session.")
        return sessions
    except Exception as e:
        logger.error(f"Error listing sessions in {project_dir}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list sessions for project '{project_name}': {e}")

@router.get("/api/projects/{project_name}/sessions/{session_name}/tasks")
async def get_project_tasks(project_name: str, session_name: str, current_user: User = Depends(get_current_user)):
    """ Fetches the tasks for the specific project and session. """
    try:
        from src.tools.project_management import ProjectManagementTool
        tool = ProjectManagementTool(project_name, session_name)
        result = await tool.execute(
            agent_id="system",
            agent_sandbox_path=Path("."),
            action="list_tasks",
            project_name=project_name,
            session_name=session_name,
            status_filter="all",
            include_decomposed=True
        )
        if result.get("status") == "success":
            return JSONResponse(content={"tasks": result.get("tasks", [])})
        else:
            raise HTTPException(status_code=500, detail=result.get("message", "Unknown error fetching tasks"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing tasks for {project_name}/{session_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list tasks: {e}")

@router.post("/api/projects/{project_name}/sessions", response_model=GeneralResponse, status_code=http_status.HTTP_201_CREATED)
async def save_current_session(
    project_name: str,
    session_input: Optional[SaveSessionInput] = None,
    # --- Inject AgentManager using the modified dependency ---
    manager: AgentManager = Depends(get_agent_manager_dependency) # Use direct type hint now
):
    """ Saves the current state (agents, histories, teams) as a new session. """
    session_name_to_save = session_input.session_name if session_input else None
    try:
        # Use the injected manager instance (retrieved from app.state)
        success, message = await manager.save_session(project_name, session_name_to_save)
        if success:
            return GeneralResponse(success=True, message=message)
        else:
            # Use 400 Bad Request for save failures reported by the manager
            raise HTTPException(status_code=400, detail=message)
    except Exception as e:
        logger.error(f"Unexpected error saving session for project '{project_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error saving session: {e}")

@router.post("/api/projects/{project_name}/sessions/{session_name}/load", response_model=GeneralResponse)
async def load_specific_session(
    project_name: str,
    session_name: str,
    # --- Inject AgentManager using the modified dependency ---
    manager: AgentManager = Depends(get_agent_manager_dependency) # Use direct type hint now
):
    """ Loads the specified session, replacing current dynamic agents and histories. """
    try:
        # Use the injected manager instance (retrieved from app.state)
        success, message = await manager.load_session(project_name, session_name)
        if success:
            # UI notification is handled by SessionManager sending a WebSocket message
            return GeneralResponse(success=True, message=message)
        else:
            if "not found" in message.lower():
                 raise HTTPException(status_code=404, detail=message)
            else: # Use 400 for other load failures reported by manager
                 raise HTTPException(status_code=400, detail=message)
    except HTTPException as http_exc:
         # Re-raise HTTPExceptions directly
         raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error loading session '{session_name}' for project '{project_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error loading session: {e}")


# --- NEW: Project Approval Endpoint ---
@router.post("/api/projects/approve/{pm_agent_id}", response_model=GeneralResponse)
async def approve_project_start(
    pm_agent_id: str,
    manager: AgentManager = Depends(get_agent_manager_dependency)
):
    """ Approves the start of a project managed by the specified PM agent. """
    logger.info(f"Received approval request for project managed by PM: {pm_agent_id}")
    try:
        agent_to_start = manager.agents.get(pm_agent_id)
        if not agent_to_start:
            logger.error(f"Approval failed: PM Agent '{pm_agent_id}' not found.")
            raise HTTPException(status_code=404, detail=f"Project Manager agent '{pm_agent_id}' not found.")

        # Check if the agent is idle (ready to start)
        if agent_to_start.status != AGENT_STATUS_IDLE:
            logger.warning(f"Approval failed: PM Agent '{pm_agent_id}' is not idle (Status: {agent_to_start.status}). Cannot start.")
            raise HTTPException(status_code=409, detail=f"Project Manager agent '{pm_agent_id}' is currently busy (Status: {agent_to_start.status}) and cannot be started.")

        # Verify the agent was awaiting approval
        if not getattr(agent_to_start, '_awaiting_project_approval', False):
            logger.warning(f"Approval failed: PM Agent '{pm_agent_id}' was not awaiting project approval.")
            raise HTTPException(status_code=409, detail=f"Project Manager agent '{pm_agent_id}' was not awaiting approval.")

        # Clear the approval flag before scheduling
        agent_to_start._awaiting_project_approval = False
        logger.info(f"Cleared _awaiting_project_approval flag for PM agent '{pm_agent_id}'.")

        # Set AgentManager's current project/session context for the PM
        pm_project_name = agent_to_start.agent_config.get("config", {}).get("project_name_context")
        pm_session_name = agent_to_start.agent_config.get("config", {}).get("session_name", manager.current_session)

        if pm_project_name and pm_session_name:
            logger.info(f"Setting AgentManager context to Project: '{pm_project_name}', Session: '{pm_session_name}' for PM '{pm_agent_id}' activation.")
            await manager.set_project_session_context(pm_project_name, pm_session_name, loading=False)
            
            # --- NEW: Save Initial Project Plan to shared_workspace ---
            initial_plan_description = agent_to_start.agent_config.get("config", {}).get("initial_plan_description")
            if initial_plan_description:
                try:
                    import re
                    safe_project_name = re.sub(r'[^\w\-. ]', '_', pm_project_name)
                    workspace_path = settings.PROJECTS_BASE_DIR / safe_project_name / pm_session_name / "shared_workspace"
                    workspace_path.mkdir(parents=True, exist_ok=True)
                    plan_file_path = workspace_path / "PROJECT_PLAN.md"
                    with open(plan_file_path, "w") as f:
                        f.write(f"# Project Plan: {pm_project_name}\n\n{initial_plan_description}")
                    logger.info(f"Successfully saved original project plan to {plan_file_path}")
                except Exception as e:
                    logger.error(f"Failed to save initial project plan to shared_workspace: {e}", exc_info=True)
            # --- END NEW ---
        else:
            logger.warning(f"PM Agent '{pm_agent_id}' missing 'project_name_context' or 'session_name' in config. AgentManager context not explicitly set for this PM activation. Using manager's current context: {manager.current_project}/{manager.current_session}")

        # Ensure the agent is in PM_STATE_STARTUP. It should be, from when it was created.
        # If it's somehow not, log a warning but proceed with scheduling as the primary goal is activation.
        if agent_to_start.state != PM_STATE_STARTUP:
            logger.warning(f"PM Agent '{pm_agent_id}' is in state '{agent_to_start.state}' instead of expected '{PM_STATE_STARTUP}' during approval. Proceeding with cycle scheduling.")
            # Optionally, force state back if deemed critical, but for now, focus on scheduling.
            # manager.workflow_manager.change_state(agent_to_start, PM_STATE_STARTUP) # This might re-introduce the original problem if not handled carefully.

        logger.info(f"PM agent '{pm_agent_id}' approved. Current state: '{agent_to_start.state}'. Scheduling initial cycle...")
        asyncio.create_task(manager.schedule_cycle(agent_to_start))

        # Send confirmation to UI
        await manager.send_to_ui({
            "type": "project_approved",
            "pm_agent_id": pm_agent_id,
            "message": f"Project managed by '{pm_agent_id}' approved and started."
        })

        return GeneralResponse(success=True, message=f"Project managed by '{pm_agent_id}' approved and started.")

    except HTTPException as http_exc:
        raise http_exc # Re-raise known HTTP exceptions
    except Exception as e:
        logger.error(f"Unexpected error approving project for PM '{pm_agent_id}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error approving project: {e}")
# --- END NEW ---
# --- NEW: Graceful Shutdown Endpoint ---
import signal

@router.post("/api/shutdown", response_model=GeneralResponse)
async def shutdown_framework(current_user: User = Depends(get_current_user)):
    """ Triggers a graceful shutdown of the TrippleEffect framework. """
    logger.info(f"Shutdown requested via API endpoint by user '{current_user.username}'.")
    
    # Run the kill signal asynchronously to allow the response to return
    async def _send_signal():
        await asyncio.sleep(1) # tiny delay to let the response complete
        logger.info("Sending SIGINT to self to trigger graceful shutdown...")
        os.kill(os.getpid(), signal.SIGINT)

    asyncio.create_task(_send_signal())
    
    return GeneralResponse(success=True, message="Graceful shutdown initiated. The framework will exit shortly.")
# --- END NEW ---


# --- NEW: Project Lifecycle Endpoints (Stop / Start / Download) ---

@router.post("/api/projects/active/stop", response_model=GeneralResponse)
async def stop_active_project(
    manager: AgentManager = Depends(get_agent_manager_dependency),
    current_user: User = Depends(get_current_user)
):
    """
    Stops/pauses the currently active project by transitioning all PM agents
    to pm_standby and all worker agents to worker_wait.
    """
    project_name = manager.current_project
    session_name = manager.current_session
    
    if not project_name or not session_name:
        raise HTTPException(status_code=400, detail="No active project/session to stop.")
    
    logger.info(f"Stop requested for active project '{project_name}/{session_name}' by user '{current_user.username}'.")
    
    stopped_agents = []
    try:
        agents_snapshot = list(manager.agents.values())
        for agent in agents_snapshot:
            # Skip bootstrap admin_ai — it should stay responsive
            if agent.agent_id == BOOTSTRAP_AGENT_ID:
                continue
            
            if agent.agent_type == AGENT_TYPE_PM:
                if agent.state != PM_STATE_STANDBY:
                    manager.workflow_manager.change_state(agent, PM_STATE_STANDBY)
                agent.set_status(AGENT_STATUS_IDLE)
                # Clear any pending scheduling state
                agent._awaiting_project_approval = False
                stopped_agents.append(agent.agent_id)
                logger.info(f"Stopped PM agent '{agent.agent_id}' -> pm_standby/idle")
                
            elif agent.agent_type == AGENT_TYPE_WORKER:
                from src.agents.constants import WORKER_STATE_WAIT as _WW
                if agent.state != _WW:
                    manager.workflow_manager.change_state(agent, _WW)
                agent.set_status(AGENT_STATUS_IDLE)
                stopped_agents.append(agent.agent_id)
                logger.info(f"Stopped Worker agent '{agent.agent_id}' -> worker_wait/idle")
            
            # Push status update to UI for each stopped agent
            await manager.push_agent_status_update(agent.agent_id)
        
        # Broadcast project_stopped event to UI
        await manager.send_to_ui({
            "type": "project_stopped",
            "project_name": project_name,
            "session_name": session_name,
            "stopped_agents": stopped_agents,
            "message": f"Project '{project_name}' has been paused. {len(stopped_agents)} agent(s) stopped."
        })
        
        msg = f"Project '{project_name}' stopped. {len(stopped_agents)} agent(s) paused."
        logger.info(msg)
        return GeneralResponse(success=True, message=msg)
    
    except Exception as e:
        logger.error(f"Error stopping project '{project_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stop project: {e}")


@router.post("/api/projects/active/start", response_model=GeneralResponse)
async def start_active_project(
    manager: AgentManager = Depends(get_agent_manager_dependency),
    current_user: User = Depends(get_current_user)
):
    """
    Resumes the currently active project by transitioning PM agents back to
    pm_manage and scheduling a new cycle. Injects awareness messages into
    PM and worker histories so they know the session was paused and resumed.
    """
    project_name = manager.current_project
    session_name = manager.current_session
    
    if not project_name or not session_name:
        raise HTTPException(status_code=400, detail="No active project/session to start.")
    
    logger.info(f"Start/resume requested for active project '{project_name}/{session_name}' by user '{current_user.username}'.")
    
    resumed_agents = []
    awareness_msg = (
        "[Framework System Message]: This project session was PAUSED by the user and has now been RESUMED. "
        "Review the current state of tasks and continue your work. Any work in progress before the pause "
        "should be picked up where it left off."
    )
    
    try:
        agents_snapshot = list(manager.agents.values())
        
        for agent in agents_snapshot:
            if agent.agent_id == BOOTSTRAP_AGENT_ID:
                continue
            
            if agent.agent_type == AGENT_TYPE_PM:
                if agent.state == PM_STATE_STANDBY:
                    # Inject awareness message
                    agent.message_history.append({"role": "system", "content": awareness_msg})
                    
                    # Transition to pm_manage and schedule a cycle
                    manager.workflow_manager.change_state(agent, PM_STATE_MANAGE)
                    agent.set_status(AGENT_STATUS_IDLE)
                    asyncio.create_task(manager.schedule_cycle(agent, 0))
                    resumed_agents.append(agent.agent_id)
                    logger.info(f"Resumed PM agent '{agent.agent_id}' -> pm_manage + scheduled cycle")
                    
            elif agent.agent_type == AGENT_TYPE_WORKER:
                # Inject awareness message into all workers so they know about the pause
                agent.message_history.append({"role": "system", "content": awareness_msg})
                resumed_agents.append(agent.agent_id)
                logger.info(f"Injected resume awareness into Worker agent '{agent.agent_id}'")
            
            await manager.push_agent_status_update(agent.agent_id)
        
        # Ensure the PM manage timer is running
        await manager.start_pm_manage_timer()
        
        # Broadcast project_started event to UI
        await manager.send_to_ui({
            "type": "project_started",
            "project_name": project_name,
            "session_name": session_name,
            "resumed_agents": resumed_agents,
            "message": f"Project '{project_name}' has been resumed. {len(resumed_agents)} agent(s) reactivated."
        })
        
        msg = f"Project '{project_name}' resumed. {len(resumed_agents)} agent(s) reactivated."
        logger.info(msg)
        return GeneralResponse(success=True, message=msg)
    
    except Exception as e:
        logger.error(f"Error starting project '{project_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start project: {e}")


def _build_exclusion_patterns(project_session_dir: Path) -> list:
    """
    Builds a list of exclusion patterns from hardcoded defaults and .gitignore.
    Returns a list of patterns (strings) to exclude.
    """
    # Hard-coded exclusion patterns — always excluded
    default_exclusions = [
        '.venv', 'venv', '__pycache__', '.git', 'node_modules',
        '.env', '.task', '.pytest_cache', '.mypy_cache',
        '*.pyc', '*.pyo', '.DS_Store', 'Thumbs.db'
    ]
    
    patterns = list(default_exclusions)
    
    # Read .gitignore if present
    gitignore_path = project_session_dir / '.gitignore'
    if not gitignore_path.is_file():
        # Also check one level up (project root)
        gitignore_path = project_session_dir.parent / '.gitignore'
    
    if gitignore_path.is_file():
        try:
            with open(gitignore_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Normalize: remove trailing slashes for directory matching
                        clean = line.rstrip('/')
                        if clean and clean not in patterns:
                            patterns.append(clean)
            logger.info(f"Loaded {len(patterns)} exclusion patterns (including .gitignore from {gitignore_path})")
        except Exception as e:
            logger.warning(f"Failed to read .gitignore at {gitignore_path}: {e}")
    
    return patterns


def _should_exclude(path: Path, base_dir: Path, patterns: list) -> bool:
    """
    Check if a file/directory path should be excluded based on the patterns list.
    """
    import fnmatch as fnm
    
    rel_path = path.relative_to(base_dir)
    rel_str = str(rel_path)
    name = path.name
    
    for pattern in patterns:
        # Match against the filename
        if fnm.fnmatch(name, pattern):
            return True
        # Match against any path component
        for part in rel_path.parts:
            if fnm.fnmatch(part, pattern):
                return True
        # Match against the full relative path
        if fnm.fnmatch(rel_str, pattern):
            return True
    
    return False


@router.get("/api/projects/active/download")
async def download_active_project(
    scope: str = "full",
    manager: AgentManager = Depends(get_agent_manager_dependency),
    current_user: User = Depends(get_current_user)
):
    """
    Creates a zip archive of the active project/session directory and returns it
    as a download. Excludes .venv, __pycache__, .git, and .gitignore patterns.
    
    Query params:
        scope: 'full' (entire session folder) or 'workspace' (shared_workspace only)
    """
    from fastapi.responses import StreamingResponse
    import zipfile
    import io
    import re as re_module
    
    project_name = manager.current_project
    session_name = manager.current_session
    
    if not project_name or not session_name:
        raise HTTPException(status_code=400, detail="No active project/session to download.")
    
    safe_project_name = re_module.sub(r'[^\w\-. ]', '_', project_name)
    session_dir = settings.PROJECTS_BASE_DIR / safe_project_name / session_name
    
    if scope == "workspace":
        target_dir = session_dir / "shared_workspace"
        zip_filename = f"{safe_project_name}_{session_name}_workspace.zip"
    else:
        target_dir = session_dir
        zip_filename = f"{safe_project_name}_{session_name}_full.zip"
    
    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {target_dir.name}")
    
    exclusion_patterns = _build_exclusion_patterns(session_dir)
    logger.info(f"Creating zip archive of '{target_dir}' (scope={scope}) with {len(exclusion_patterns)} exclusion patterns.")
    
    # Build the zip in memory
    zip_buffer = io.BytesIO()
    file_count = 0
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(target_dir.rglob('*')):
                if file_path.is_file() and not _should_exclude(file_path, target_dir, exclusion_patterns):
                    arcname = str(file_path.relative_to(target_dir))
                    zf.write(file_path, arcname)
                    file_count += 1
        
        zip_buffer.seek(0)
        logger.info(f"Zip archive created: {zip_filename} with {file_count} files.")
        
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
        )
    except Exception as e:
        logger.error(f"Error creating zip archive: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create archive: {e}")

# --- END: Project Lifecycle Endpoints ---

