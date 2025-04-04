# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os
from dotenv import load_dotenv

# Load environment variables from .env file FIRST
# This ensures settings (like API keys) are available when AgentManager initializes agents
# Note: settings.py also loads .env, providing robustness
dotenv_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}")


# Import the routers and the setup function for AgentManager injection
from src.api import http_routes, websocket_manager

# Import the AgentManager class
from src.agents.manager import AgentManager

# --- Instantiate Agent Manager ---
# The AgentManager's __init__ will create and initialize agents,
# potentially using settings loaded from the .env file above.
# It also imports the `broadcast` function from websocket_manager.
print("Instantiating AgentManager...")
agent_manager = AgentManager()
print("AgentManager instantiated.")

# --- Inject Agent Manager into WebSocket Manager ---
# This makes the agent_manager instance available to the WebSocket endpoint handlers
# via the module-level variable set by set_agent_manager.
websocket_manager.set_agent_manager(agent_manager)
print("AgentManager instance injected into WebSocketManager.")


# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent # This should point to TrippleEffect-main/

# Create the FastAPI application instance
print("Creating FastAPI app instance...")
app = FastAPI(title="TrippleEffect", version="0.1.0")
print("FastAPI app instance created.")

# Mount the static files directory
# This allows serving CSS, JS, images etc.
static_files_path = BASE_DIR / "static"
print(f"Looking for static files directory at: {static_files_path}")
if static_files_path.exists() and static_files_path.is_dir():
    app.mount("/static", StaticFiles(directory=static_files_path), name="static")
    print(f"Mounted static files directory: {static_files_path}")
else:
    print(f"Warning: Static files directory not found at {static_files_path}")
    # Optional: Create the directory if it doesn't exist
    try:
        static_files_path.mkdir(parents=True, exist_ok=True)
        print(f"Created static files directory at {static_files_path}")
        app.mount("/static", StaticFiles(directory=static_files_path), name="static")
        print(f"Mounted newly created static files directory: {static_files_path}")
    except Exception as e:
        print(f"Error creating or mounting static files directory: {e}")


# Include the API routers
print("Including API routers...")
app.include_router(http_routes.router)
app.include_router(websocket_manager.router)
print("API routers included.")


# Configuration for running the app with uvicorn directly
if __name__ == "__main__":
    print("Starting Uvicorn server...")
    # Use 0.0.0.0 to make it accessible on the network (important for Termux)
    # Use reload=True for development convenience
    # Set log_level for more detailed uvicorn output during startup/requests
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, app_dir="src", log_level="info")
