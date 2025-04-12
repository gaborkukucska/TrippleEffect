# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import os
from dotenv import load_dotenv
import logging
import logging.handlers
import asyncio
from typing import Optional
import time

# --- Base Directory ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Load Environment Variables ---
dotenv_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}")

# --- Configure Logging ---
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

timestamp = time.strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"app_{timestamp}.log"

log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_level = logging.INFO # Default level

logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True)

file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(log_level)

root_logger = logging.getLogger()
root_logger.addHandler(file_handler)

logger = logging.getLogger(__name__)
logger.info(f"--- Application Logging Initialized (Console & File: {LOG_FILE.name}) ---")


# Import the routers and the setup function for AgentManager injection
from src.api import http_routes, websocket_manager

# --- Import the refactored AgentManager class ---
from src.agents.manager import AgentManager
# --- End Import ---

# Import ModelRegistry instance
from src.config.settings import model_registry, settings

# --- Global placeholder for the manager ---
agent_manager_instance: Optional[AgentManager] = None

# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_manager_instance
    logger.info("Application startup sequence initiated...")

    logger.info("Instantiating AgentManager...")
    agent_manager_instance = AgentManager() # Initialization logic is now within AgentManager.__init__
    logger.info("AgentManager instantiated.")

    app.state.agent_manager = agent_manager_instance
    logger.info("AgentManager instance stored in app.state.")

    websocket_manager.set_agent_manager(agent_manager_instance)
    logger.info("AgentManager instance injected into WebSocketManager.")

    logger.info("Lifespan: Discovering reachable providers and available models...")
    try:
        # Discovery is handled by ModelRegistry instance
        await model_registry.discover_models_and_providers()
        logger.info("Lifespan: Provider and model discovery completed.")
    except Exception as e:
        logger.error(f"Lifespan: Error during provider/model discovery: {e}", exc_info=True)

    # Use the initialized manager instance to initialize bootstrap agents
    logger.info("Lifespan: Initializing bootstrap agents...")
    try:
        # Delegate to the manager instance's method
        init_task = asyncio.create_task(agent_manager_instance.initialize_bootstrap_agents())
        await init_task
        logger.info("Lifespan: Bootstrap agent initialization task completed.")
    except Exception as e:
        logger.critical(f"Lifespan: CRITICAL ERROR during bootstrap agent initialization: {e}", exc_info=True)

    logger.info("Application startup complete. Ready to accept requests.")
    yield # Application runs here

    logger.info("Application shutdown sequence initiated...")
    if app.state.agent_manager:
        try:
            await app.state.agent_manager.cleanup_providers()
            logger.info("Lifespan: Provider cleanup finished.")
        except Exception as e:
            logger.error(f"Lifespan: Error during provider cleanup: {e}", exc_info=True)
    else:
        logger.warning("Lifespan: AgentManager instance not found in app.state during shutdown.")
    logger.info("--- Application Shutdown Complete ---")


# Create the FastAPI application instance
logger.info("Creating FastAPI app instance...")
app = FastAPI(
    title="TrippleEffect",
    version="2.14", # Update version
    lifespan=lifespan
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
    logger.info("Starting Uvicorn server directly...")
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True, # Keep reload enabled for development
        reload_dirs=[str(BASE_DIR / "src")],
        log_level="info"
    )
