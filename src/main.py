# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import os
from dotenv import load_dotenv
import logging
import logging.handlers # Added for FileHandler
import asyncio
from typing import Optional

# --- Base Directory ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Load Environment Variables ---
dotenv_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}") # Keep initial print

# --- Configure Logging ---
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True) # Ensure logs directory exists
LOG_FILE = LOG_DIR / "app.log"

# Configure root logger
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_level = logging.INFO # Default level

# Basic config sets up default stream handler (console)
logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Create file handler
# Use RotatingFileHandler for production to manage log size
# file_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5) # 10MB per file, 5 backups
# For simplicity now, use basic FileHandler
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(log_level)

# Add file handler to the root logger
logging.getLogger().addHandler(file_handler)

# Get logger for this module *after* basicConfig
logger = logging.getLogger(__name__)
logger.info("--- Application Logging Initialized (Console & File) ---")
logger.info(f"Log file: {LOG_FILE}")
# --- End Logging Configuration ---


# Import the routers and the setup function for AgentManager injection
from src.api import http_routes, websocket_manager

# Import the AgentManager class
from src.agents.manager import AgentManager

# --- Global placeholder for the manager ---
agent_manager_instance: Optional[AgentManager] = None

# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_manager_instance
    logger.info("Application startup sequence initiated...")

    # --- Instantiate Agent Manager (Synchronous Part) ---
    logger.info("Instantiating AgentManager...")
    agent_manager_instance = AgentManager()
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
        init_task = asyncio.create_task(agent_manager_instance.initialize_bootstrap_agents())
        await init_task
        logger.info("Lifespan: Bootstrap agent initialization task completed.")
    except Exception as e:
        logger.critical(f"Lifespan: CRITICAL ERROR during bootstrap agent initialization: {e}", exc_info=True)
    # --- End bootstrap initialization ---

    logger.info("Application startup complete. Ready to accept requests.")
    yield # Application runs here

    # Code here runs on shutdown
    logger.info("Application shutdown sequence initiated...")
    if app.state.agent_manager:
        try:
            await app.state.agent_manager.cleanup_providers()
            logger.info("Lifespan: Provider cleanup finished.")
        except Exception as e:
            logger.error(f"Lifespan: Error during provider cleanup: {e}", exc_info=True)
    else:
        logger.warning("Lifespan: AgentManager instance not found in app.state during shutdown.")
    logger.info("--- Application Shutdown Complete ---") # Added separator


# Create the FastAPI application instance, including the lifespan context manager
logger.info("Creating FastAPI app instance...")
app = FastAPI(
    title="TrippleEffect",
    version="0.3.0",
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
    # Note: Uvicorn might slightly alter logging format when run this way,
    # but basicConfig ensures handlers are set up.
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(BASE_DIR / "src")],
        log_level="info" # Uvicorn's log level setting
    )
