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

# --- Type Hinting & Direct Import for AgentManager ---
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
# Add direct import to resolve runtime NameError during FastAPI dependency evaluation
from src.agents.manager import AgentManager
# --- NEW: Import agent status constants ---
from src.agents.constants import AGENT_STATUS_IDLE
# --- END NEW ---

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


# --- HTTP Routes ---

@router.get("/", response_class=HTMLResponse)
async def get_index_page(request: Request):
    """ Serves the main index.html page. """
    try:
        template_path = TEMPLATE_DIR / "index.html"
        if not template_path.is_file():
             logger.error(f"Template file index.html not found at {template_path}")
             # Return a simple HTML error page if template is missing
             error_html = "<html><body><h1>Internal Server Error</h1><p>Could not load the main application page. Template file missing.</p></body></html>"
             return HTMLResponse(content=error_html, status_code=500)
        # Pass the request object to the template context
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception as e:
        # Catch potential errors during template rendering
        logger.error(f"Error rendering template index.html: {e}", exc_info=True)
        error_html = "<html><body><h1>Internal Server Error</h1><p>An unexpected error occurred while loading the page.</p></body></html>"
        return HTMLResponse(content=error_html, status_code=500)

# --- Agent Config CRUD API Endpoints (using ConfigManager, no AgentManager needed) ---

@router.get("/api/config/agents", response_model=List[AgentInfo])
async def get_agent_configurations():
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
async def create_agent_configuration(agent_data: AgentConfigCreate):
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
async def update_agent_configuration(agent_id: str, agent_config_data: AgentConfigInput):
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
async def delete_agent_configuration(agent_id: str):
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
async def list_projects():
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
async def list_sessions(project_name: str):
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

        # Schedule the agent's first cycle
        logger.info(f"Approving project start for PM '{pm_agent_id}'. Scheduling initial cycle...")
        await manager.schedule_cycle(agent_to_start)

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
