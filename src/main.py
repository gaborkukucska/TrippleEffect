# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI, Request # Added Request
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import os
from dotenv import load_dotenv
import logging
import asyncio

# Load environment variables from .env file FIRST
dotenv_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}")

# Configure logging early
# Use uvicorn's logging configuration by default when run via uvicorn
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("uvicorn.error") # Use uvicorn's logger for consistency

# Import the routers and the setup function for AgentManager injection
from src.api import http_routes, websocket_manager

# Import the AgentManager class
from src.agents.manager import AgentManager

# --- Global placeholder for the manager ---
# We create it synchronously but initialize agents async in lifespan
agent_manager_instance: Optional[AgentManager] = None

# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_manager_instance
    # Code here runs on startup BEFORE requests are accepted
    logger.info("Application startup sequence initiated...")

    # --- Instantiate Agent Manager (Synchronous Part) ---
    logger.info("Instantiating AgentManager...")
    agent_manager_instance = AgentManager() # Create the single instance
    logger.info("AgentManager instantiated.")

    # --- Store instance in app.state for dependency injection ---
    app.state.agent_manager = agent_manager_instance
    logger.info("AgentManager instance stored in app.state.")

    # --- Inject Agent Manager into WebSocket Manager ---
    websocket_manager.set_agent_manager(agent_manager_instance)
    logger.info("AgentManager instance injected into WebSocketManager.")

    # --- Initialize bootstrap agents ASYNCHRONOUSLY ---
    logger.info("Lifespan: Initializing bootstrap agents...")
    try:
        # Run the async initialization method on the created instance
        init_task = asyncio.create_task(agent_manager_instance.initialize_bootstrap_agents())
        # Wait for bootstrap agents to be ready before yielding
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
    if app.state.agent_manager: # Use the instance from app state
        try:
            await app.state.agent_manager.cleanup_providers() # Call the cleanup method
            logger.info("Lifespan: Provider cleanup finished.")
        except Exception as e:
            logger.error(f"Lifespan: Error during provider cleanup: {e}", exc_info=True)
    else:
        logger.warning("Lifespan: AgentManager instance not found in app.state during shutdown.")
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
    # This block is primarily for direct execution `python -m src.main`
    # When run by uvicorn command line, the lifespan handles initialization
    logger.info("Starting Uvicorn server directly...")
    # uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
    # Correct way to run with reload from script using uvicorn programmatic API:
    uvicorn.run(
        "src.main:app", # app object location
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(BASE_DIR / "src")], # Specify dirs to watch
        log_level="info"
    )
