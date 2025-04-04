# START OF FILE src/api/http_routes.py
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path

# Import settings to access loaded configurations
from src.config.settings import settings

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
             print(f"Error: Template file index.html not found at {template_path}")
             raise FileNotFoundError("index.html template not found.")
        return templates.TemplateResponse("index.html", {"request": request})
    except FileNotFoundError as e:
         print(f"Error serving index page: {e}")
         error_html = """
         <html><head><title>Error 500</title></head>
         <body><h1>Internal Server Error</h1><p>Could not load the main application page. Template file missing.</p></body></html>
         """
         return HTMLResponse(content=error_html, status_code=500)
    except Exception as e:
        print(f"Error rendering template index.html: {e}")
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
    Reads from the centrally loaded settings.
    """
    agent_info_list: List[AgentInfo] = []
    try:
        raw_configs = settings.AGENT_CONFIGURATIONS
        if not raw_configs:
            # Return empty list if no agents are configured
            return []

        for agent_conf_entry in raw_configs:
            agent_id = agent_conf_entry.get("agent_id")
            config_dict = agent_conf_entry.get("config", {})

            # Extract only the necessary, non-sensitive info
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
        print(f"Error retrieving agent configurations: {e}")
        # Use HTTPException for API errors
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve agent configurations: {e}"
        )

# You can add more HTTP routes here later if needed, for example:
# - Health check endpoints
