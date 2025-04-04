# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager # Import asynccontextmanager
from pathlib import Path
import os
from dotenv import load_dotenv

# Load environment variables from .env file FIRST
dotenv_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}")

# Import the routers and the setup function for AgentManager injection
from src.api import http_routes, websocket_manager

# Import the AgentManager class
from src.agents.manager import AgentManager

# --- Instantiate Agent Manager ---
print("Instantiating AgentManager...")
agent_manager = AgentManager()
print("AgentManager instantiated.")

# --- Inject Agent Manager into WebSocket Manager ---
websocket_manager.set_agent_manager(agent_manager)
print("AgentManager instance injected into WebSocketManager.")


# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code here runs on startup
    print("Application startup...")
    # You could add other startup logic here if needed
    yield
    # Code here runs on shutdown
    print("Application shutdown...")
    await agent_manager.cleanup_providers() # Call the cleanup method
    print("Provider cleanup finished.")


# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent # This should point to TrippleEffect-main/

# Create the FastAPI application instance, including the lifespan context manager
print("Creating FastAPI app instance...")
app = FastAPI(
    title="TrippleEffect",
    version="0.2.0", # Incremented version slightly
    lifespan=lifespan # Register the lifespan handler
)
print("FastAPI app instance created with lifespan handler.")

# Mount the static files directory
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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, app_dir="src", log_level="info")
