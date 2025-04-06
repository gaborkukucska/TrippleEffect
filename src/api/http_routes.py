# START OF FILE src/api/http_routes.py
from fastapi import APIRouter, Request, HTTPException, status as http_status, Depends # Added Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any # Added Dict, Any
from pathlib import Path
import logging
import os # For listing directories
import time # For default session names

# Import settings to access loaded configurations (defaults mostly) and PROJECTS_BASE_DIR
from src.config.settings import settings

# Import the ConfigManager singleton instance for agent config CRUD
from src.config.config_manager import config_manager

# --- Import the global AgentManager instance ---
# Although FastAPI encourages dependency injection, for simplicity in the current structure,
# we'll access the globally instantiated AgentManager directly from where it's created (main.py scope).
# If issues arise, proper dependency injection should be implemented.
from src.main import agent_manager # Import the instance created in main.py


logger = logging.getLogger(__name__)

# Define the path to the templates directory relative to this file
TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

# Check if template directory exists
if not TEMPLATE_DIR.exists():
    print(f"Warning: Templates directory not found at {TEMPLATE_DIR}")

# Initialize Jinja2Templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Create an API router instance
router = APIRouter()

# --- Pydantic Models for API Responses ---

class AgentInfo(BaseModel):
    """Model to represent basic agent information for the UI."""
    agent_id: str
    provider: str
    model: str
    persona: str

class GeneralResponse(BaseModel):
    """ Simple success/error response model. """
    success: bool
    message: str
    details: Optional[str] = None

# --- Pydantic Models for Session/Project API ---

class SessionInfo(BaseModel):
    """ Model for session information. """
    project_name: str
    session_name: str
    # Add timestamp or other metadata later if needed from session file

class ProjectInfo(BaseModel):
    """ Model for project information. """
    project_name: str
    sessions: Optional[List[str]] = None # Optional list of session names

class SaveSessionInput(BaseModel):
    """ Optional input for saving session (allows specifying name). """
    session_name: Optional[str] = Field(None, description="Optional name for the session. If omitted, a timestamp-based name is generated.")


# --- HTTP Routes ---

@router.get("/", response_class=HTMLResponse)
async def get_index_page(request: Request):
    """
    Serves the main index.html page.
    """
    try:
        # Check if template exists before trying to render
        template_path = TEMPLATE_DIR / "index.html"
        if not template_path.is_file():
             logger.error(f"Template file index.html not found at {template_path}")
             raise FileNotFoundError("index.html template not found.")
        return templates.TemplateResponse("index.html", {"request": request})
    except FileNotFoundError as e:
         logger.error(f"Error serving index page: {e}")
         error_html = """
         <html><head><title>Error 500</title></head>
         <body><h1>Internal Server Error</h1><p>Could not load the main application page. Template file missing.</p></body></html>
         """
         return HTMLResponse(content=error_html, status_code=500)
    except Exception as e:
        logger.error(f"Error rendering template index.html: {e}", exc_info=True)
        # Provide a fallback response or raise an appropriate HTTP exception
        error_html = """
        <html><head><title>Error 500</title></head>
        <body><h1>Internal Server Error</h1><p>An unexpected error occurred while loading the page.</p></body></html>
        """
        return HTMLResponse(content=error_html, status_code=500)

# --- Agent Config CRUD API Endpoints ---

# Pydantic models for agent config
class AgentConfigInput(BaseModel):
    provider: str = Field(..., description="Provider name ('openai', 'ollama', 'openrouter', etc.)")
    model: str = Field(..., description="Model name specific to the provider.")
    system_prompt: Optional[str] = Field(settings.DEFAULT_SYSTEM_PROMPT, description="The system prompt for the agent.")
    temperature: Optional[float] = Field(settings.DEFAULT_TEMPERATURE, description="Sampling temperature (e.g., 0.7).")
    persona: Optional[str] = Field(settings.DEFAULT_PERSONA, description="Display name for the agent.")
    class Config: extra = "allow"

class AgentConfigCreate(BaseModel):
    agent_id: str = Field(..., description="Unique identifier for the agent (e.g., 'coder_v2'). Cannot contain spaces or special characters.", pattern=r"^[a-zA-Z0-9_-]+$") # Added pattern
    config: AgentConfigInput = Field(..., description="The configuration settings for the agent.")

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
             # Check if deletion failed because agent wasn't found or save failed
             all_configs = await config_manager.get_full_config() # Check full config state
             agent_still_exists = any(a.get("agent_id") == agent_id for a in all_configs.get("agents", []))
             if not agent_still_exists:
                 logger.error(f"Agent '{agent_id}' not found for deletion (or was already deleted but save failed?).")
                 raise HTTPException(status_code=404, detail=f"Agent with ID '{agent_id}' not found.")
             else: # Agent exists, but delete op failed (likely save error)
                 logger.error(f"Failed to delete agent '{agent_id}' using ConfigManager (likely save failed).")
                 raise HTTPException(status_code=500, detail=f"Failed to delete agent '{agent_id}'. Check server logs for save errors.")
    except HTTPException as http_exc: raise http_exc
    except Exception as e:
        logger.error(f"Error deleting agent configuration for '{agent_id}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent configuration: {e}")


# --- Project/Session Management API Endpoints ---

@router.get("/api/projects", response_model=List[ProjectInfo])
async def list_projects():
    """ Lists available projects by scanning the projects base directory. """
    projects = []
    base_dir = settings.PROJECTS_BASE_DIR
    if not base_dir.is_dir():
        logger.warning(f"Projects base directory not found or is not a directory: {base_dir}")
        return [] # Return empty list if base directory doesn't exist

    try:
        for item in base_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'): # List only directories, ignore hidden
                projects.append(ProjectInfo(project_name=item.name))
        return projects
    except Exception as e:
        logger.error(f"Error listing projects in {base_dir}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list projects: {e}")


@router.get("/api/projects/{project_name}/sessions", response_model=List[SessionInfo])
async def list_sessions(project_name: str):
    """ Lists available sessions within a specific project directory. """
    sessions = []
    project_dir = settings.PROJECTS_BASE_DIR / project_name

    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")

    try:
        for item in project_dir.iterdir():
            # Check if it's a directory AND contains the expected history file
            session_file = item / "agent_histories.json"
            if item.is_dir() and not item.name.startswith('.') and session_file.is_file():
                sessions.append(SessionInfo(project_name=project_name, session_name=item.name))
            elif item.is_dir() and not item.name.startswith('.'):
                 logger.warning(f"Directory '{item.name}' in project '{project_name}' exists but missing 'agent_histories.json', not listed as session.")

        return sessions
    except Exception as e:
        logger.error(f"Error listing sessions in {project_dir}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list sessions for project '{project_name}': {e}")

@router.post("/api/projects/{project_name}/sessions", response_model=GeneralResponse, status_code=http_status.HTTP_201_CREATED)
async def save_current_session(project_name: str, session_input: Optional[SaveSessionInput] = None):
    """ Saves the current state (agent histories) as a new session in the specified project. """
    session_name_to_save = session_input.session_name if session_input else None
    try:
        success, message = await agent_manager.save_session(project_name, session_name_to_save)
        if success:
            return GeneralResponse(success=True, message=message)
        else:
            # Determine appropriate status code based on message?
            # For now, use 400 for generic failure during save.
            raise HTTPException(status_code=400, detail=message)
    except Exception as e:
        logger.error(f"Unexpected error saving session for project '{project_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error saving session: {e}")

@router.post("/api/projects/{project_name}/sessions/{session_name}/load", response_model=GeneralResponse)
async def load_specific_session(project_name: str, session_name: str):
    """ Loads the specified session, replacing current agent histories. """
    try:
        success, message = await agent_manager.load_session(project_name, session_name)
        if success:
            # Send a status update via WebSocket to notify UI of the load
            await agent_manager._send_to_ui({
                "type": "system_event",
                "event": "session_loaded",
                "project": project_name,
                "session": session_name,
                "message": message
            })
            return GeneralResponse(success=True, message=message)
        else:
            # Use 404 if file not found, 400 otherwise
            if "not found" in message.lower():
                 raise HTTPException(status_code=404, detail=message)
            else:
                 raise HTTPException(status_code=400, detail=message)
    except HTTPException as http_exc: raise http_exc # Re-raise known exceptions
    except Exception as e:
        logger.error(f"Unexpected error loading session '{session_name}' for project '{project_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error loading session: {e}")
