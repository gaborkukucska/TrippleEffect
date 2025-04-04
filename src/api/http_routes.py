# START OF FILE src/api/http_routes.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

# Define the path to the templates directory relative to this file
# Adjust if your structure is different, but based on the plan:
# src/api/http_routes.py -> needs to go up two levels to TrippleEffect-main, then down to templates
TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

# Check if template directory exists
if not TEMPLATE_DIR.exists():
    print(f"Warning: Templates directory not found at {TEMPLATE_DIR}")
    # Optionally create it
    # TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    # print(f"Created templates directory at {TEMPLATE_DIR}")


# Initialize Jinja2Templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Create an API router instance
router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def get_index_page(request: Request):
    """
    Serves the main index.html page.
    """
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception as e:
        print(f"Error rendering template index.html: {e}")
        # Provide a fallback response or raise an appropriate HTTP exception
        # For now, just returning a simple HTML error message
        error_html = """
        <html><head><title>Error</title></head>
        <body><h1>Error loading page</h1><p>Could not find or render index.html.</p></body></html>
        """
        return HTMLResponse(content=error_html, status_code=500)

# You can add more HTTP routes here later if needed, for example:
# - API endpoints to get/set configuration
# - Health check endpoints
