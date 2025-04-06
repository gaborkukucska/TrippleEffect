# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import os
from dotenv import load_dotenv
import logging # Added logging
import asyncio # Added asyncio for gather

# Load environment variables from .env file FIRST
dotenv_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}")

# Configure logging early
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import the routers and the setup function for AgentManager injection
# Ensure these imports don't cause circular dependencies now
from src.api import http_routes, websocket_manager

# Import the AgentManager class
from src.agents.manager import AgentManager

# --- Instantiate Agent Manager (Synchronous Part) ---
# NOTE: Settings must be loaded before this if Manager uses settings in __init__
logger.info("Instantiating AgentManager (sync part)...")
agent_manager = AgentManager() # Initialization is now synchronous
logger.info("AgentManager instantiated.")

# --- Inject Agent Manager into WebSocket Manager ---
# This needs agent_manager to exist first
websocket_manager.set_agent_manager(agent_manager)
logger.info("AgentManager instance injected into WebSocketManager.")


# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code here runs on startup BEFORE requests are accepted
    logger.info("Application startup sequence initiated...")
    # --- Initialize bootstrap agents ASYNCHRONOUSLY ---
    logger.info("Lifespan: Initializing bootstrap agents...")
    try:
        # Run the async initialization method
        init_task = asyncio.create_task(agent_manager.initialize_bootstrap_agents())
        # We might want to wait for bootstrap agents to be ready before yielding
        # especially if admin_ai needs to handle the very first request.
        await init_task
        logger.info("Lifespan: Bootstrap agent initialization task completed.")
    except Exception as e:
        logger.critical(f"Lifespan: CRITICAL ERROR during bootstrap agent initialization: {e}", exc_info=True)
        # Decide if startup should halt - for now, log critical error and continue
        # raise RuntimeError("Bootstrap agent initialization failed.") from e
    # --- End bootstrap initialization ---

    logger.info("Application startup complete. Ready to accept requests.")
    yield # Application runs here

    # Code here runs on shutdown
    logger.info("Application shutdown sequence initiated...")
    try:
        await agent_manager.cleanup_providers() # Call the cleanup method
        logger.info("Lifespan: Provider cleanup finished.")
    except Exception as e:
        logger.error(f"Lifespan: Error during provider cleanup: {e}", exc_info=True)
    logger.info("Application shutdown complete.")


# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent

# Create the FastAPI application instance, including the lifespan context manager
logger.info("Creating FastAPI app instance...")
app = FastAPI(
    title="TrippleEffect",
    version="0.3.0", # Incremented version
    lifespan=lifespan # Register the lifespan handler
)
logger.info("FastAPI app instance created with lifespan handler.")

# Mount the static files directory
static_files_path = BASE_DIR / "static"
logger.info(f"Looking for static files directory at: {static_files_path}")
if static_files_path.exists() and static_files_path.is_dir():
    try:
        app.mount("/static", StaticFiles(directory=static_files_path), name="static")
        logger.info(f"Mounted static files directory: {static_files_path}")
    except Exception as e:
         logger.error(f"Error mounting static files directory {static_files_path}: {e}", exc_info=True)
else:
    logger.warning(f"Static files directory not found at {static_files_path}")


# Include the API routers
logger.info("Including API routers...")
try:
    app.include_router(http_routes.router)
    app.include_router(websocket_manager.router)
    logger.info("API routers included.")
except Exception as e:
     logger.error(f"Error including routers: {e}", exc_info=True)


# Configuration for running the app with uvicorn directly
if __name__ == "__main__":
    logger.info("Starting Uvicorn server...")
    # Use app_dir='.' if running 'python -m src.main' from root,
    # Use app_dir='src' if running 'python src/main.py' from root
    # Assuming running with 'python -m src.main' from root
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, app_dir="src", log_level="info")
