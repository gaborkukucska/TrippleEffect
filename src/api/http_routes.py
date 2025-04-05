# START OF FILE src/api/http_routes.py
from fastapi import APIRouter, Request, HTTPException, status as http_status # Added status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any # Added Dict, Any
from pathlib import Path
import logging # Added logging

# Import settings to access loaded configurations (for GET endpoint)
from src.config.settings import settings

# Import the ConfigManager singleton instance for CRUD operations
from src.config.config_manager import config_manager

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

# --- Pydantic Models for API Input/Output (Phase 8) ---

# Represents the 'config' part of an agent entry in config.yaml
# IMPORTANT: Define fields that can be SET via the API.
# Avoid including 'api_key' here if you don't want it settable via API.
# For now, let's allow setting most fields, but API keys should ideally remain in .env
class AgentConfigInput(BaseModel):
    provider: str = Field(..., description="Provider name ('openai', 'ollama', 'openrouter', etc.)")
    model: str = Field(..., description="Model name specific to the provider.")
    system_prompt: Optional[str] = Field(settings.DEFAULT_SYSTEM_PROMPT, description="The system prompt for the agent.")
    temperature: Optional[float] = Field(settings.DEFAULT_TEMPERATURE, description="Sampling temperature (e.g., 0.7).")
    persona: Optional[str] = Field(settings.DEFAULT_PERSONA, description="Display name for the agent.")
    # Allow arbitrary other key-value pairs (like provider-specific args)
    # Be cautious with this, ensure validation or sanitization if needed downstream
    class Config:
        extra = "allow"

# Model for creating a new agent config entry (includes agent_id)
class AgentConfigCreate(BaseModel):
    agent_id: str = Field(..., description="Unique identifier for the agent (e.g., 'coder_v2'). Cannot contain spaces or special characters.", pattern=r"^[a-zA-Z0-9_-]+$") # Added pattern
    config: AgentConfigInput = Field(..., description="The configuration settings for the agent.")

# General success/error response model
class GeneralResponse(BaseModel):
    success: bool
    message: str
    details: Optional[str] = None

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


@router.get("/api/config/agents", response_model=List[AgentInfo])
async def get_agent_configurations():
    """
    API endpoint to retrieve a list of configured agents (basic info only).
    Reads from the centrally loaded settings (which uses ConfigManager initially).
    """
    agent_info_list: List[AgentInfo] = []
    try:
        # Get current config from settings (which was loaded via ConfigManager)
        # For consistency in the UI after edits, maybe read directly from config_manager?
        # Let's read directly from config_manager to reflect saved state.
        raw_configs = config_manager.get_config() # Use config_manager's current state
        if not raw_configs:
            return [] # Return empty list if no agents are configured

        for agent_conf_entry in raw_configs:
            agent_id = agent_conf_entry.get("agent_id")
            config_dict = agent_conf_entry.get("config", {})

            # Extract only the necessary, non-sensitive info for display
            provider = config_dict.get("provider", settings.DEFAULT_AGENT_PROVIDER)
            model = config_dict.get("model", settings.DEFAULT_AGENT_MODEL)
            persona = config_dict.get("persona", settings.DEFAULT_PERSONA)

            if agent_id: # Only add if agent_id is present
                agent_info_list.append(
                    AgentInfo(
                        agent_id=agent_id,
                        provider=provider,
                        model=model,
                        persona=persona
                    )
                )
        return agent_info_list

    except Exception as e:
        logger.error(f"Error retrieving agent configurations: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve agent configurations: {e}"
        )

# --- Phase 8: CRUD API Endpoints ---

@router.post("/api/config/agents", response_model=GeneralResponse, status_code=http_status.HTTP_201_CREATED)
async def create_agent_configuration(agent_data: AgentConfigCreate):
    """
    API endpoint to add a new agent configuration to config.yaml.
    Requires application restart to take effect.
    """
    try:
        logger.info(f"Received request to create agent: {agent_data.agent_id}")
        # Convert Pydantic model back to dictionary for ConfigManager
        # agent_config_entry = {"agent_id": agent_data.agent_id, "config": agent_data.config.dict(exclude_unset=True)}
        # Using model_dump instead of dict for newer Pydantic versions
        agent_config_entry = {"agent_id": agent_data.agent_id, "config": agent_data.config.model_dump(exclude_unset=True)}


        success = config_manager.add_agent(agent_config_entry)
        if success:
            logger.info(f"Agent '{agent_data.agent_id}' added successfully.")
            return GeneralResponse(success=True, message=f"Agent '{agent_data.agent_id}' added. Restart application for changes to take effect.")
        else:
            logger.error(f"Failed to add agent '{agent_data.agent_id}' using ConfigManager (e.g., duplicate ID).")
            # ConfigManager logs specifics, return a general failure or check internal state
            # Check if agent exists now (maybe add failed but config was reloaded?)
            if config_manager.find_agent_index(agent_data.agent_id) is not None:
                 raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail=f"Agent with ID '{agent_data.agent_id}' already exists."
                )
            else:
                 raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to add agent '{agent_data.agent_id}'. Check server logs."
                )

    except HTTPException as http_exc:
        # Re-raise known HTTP exceptions
        raise http_exc
    except Exception as e:
        logger.error(f"Error creating agent configuration for '{agent_data.agent_id}': {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create agent configuration: {e}"
        )

@router.put("/api/config/agents/{agent_id}", response_model=GeneralResponse)
async def update_agent_configuration(agent_id: str, agent_config_data: AgentConfigInput):
    """
    API endpoint to update an existing agent's configuration in config.yaml.
    Requires application restart to take effect.
    """
    try:
        logger.info(f"Received request to update agent: {agent_id}")
        # Convert Pydantic model to dictionary
        # updated_config_dict = agent_config_data.dict(exclude_unset=True)
        # Using model_dump instead of dict for newer Pydantic versions
        updated_config_dict = agent_config_data.model_dump(exclude_unset=True)

        success = config_manager.update_agent(agent_id, updated_config_dict)

        if success:
            logger.info(f"Agent '{agent_id}' updated successfully.")
            return GeneralResponse(success=True, message=f"Agent '{agent_id}' updated. Restart application for changes to take effect.")
        else:
            # Check if agent exists before assuming other error
            if config_manager.find_agent_index(agent_id) is None:
                 logger.error(f"Agent '{agent_id}' not found for update.")
                 raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail=f"Agent with ID '{agent_id}' not found."
                )
            else:
                logger.error(f"Failed to update agent '{agent_id}' using ConfigManager.")
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST, # Or 500 if save failed
                    detail=f"Failed to update agent '{agent_id}'. Check server logs."
                )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error updating agent configuration for '{agent_id}': {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update agent configuration: {e}"
        )

@router.delete("/api/config/agents/{agent_id}", response_model=GeneralResponse)
async def delete_agent_configuration(agent_id: str):
    """
    API endpoint to remove an agent configuration from config.yaml.
    Requires application restart to take effect.
    """
    try:
        logger.info(f"Received request to delete agent: {agent_id}")
        success = config_manager.delete_agent(agent_id)

        if success:
            logger.info(f"Agent '{agent_id}' deleted successfully.")
            return GeneralResponse(success=True, message=f"Agent '{agent_id}' deleted. Restart application for changes to take effect.")
        else:
             # Check if agent exists before assuming other error
            if config_manager.find_agent_index(agent_id) is None:
                 logger.error(f"Agent '{agent_id}' not found for deletion.")
                 raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail=f"Agent with ID '{agent_id}' not found."
                )
            else:
                logger.error(f"Failed to delete agent '{agent_id}' using ConfigManager.")
                raise HTTPException(
                    status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, # Save likely failed
                    detail=f"Failed to delete agent '{agent_id}'. Check server logs."
                )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error deleting agent configuration for '{agent_id}': {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete agent configuration: {e}"
        )
