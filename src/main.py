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
import time # *** ADDED for timestamp ***

# --- Base Directory ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Load Environment Variables ---
dotenv_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}") # Keep initial print

# --- Configure Logging ---
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True) # Ensure logs directory exists

# *** Generate timestamp for log filename ***
timestamp = time.strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"app_{timestamp}.log"
# *** End timestamp generation ***

# Configure root logger
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_level = logging.INFO # Default level

# Basic config sets up default stream handler (console)
# Set force=True if using reload to potentially avoid issues with handlers persisting across reloads
logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True)

# Create unique file handler for this run
# Using basic FileHandler for now. Consider RotatingFileHandler for long-running processes.
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(log_level)

# Get the root logger and add the file handler
# Avoid adding duplicate handlers if basicConfig already added one implicitly
root_logger = logging.getLogger()
# Check if a FileHandler for the *same file* already exists (less likely now with timestamps)
# if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(LOG_FILE) for h in root_logger.handlers):
#     root_logger.addHandler(file_handler)
# Simpler approach: just add it. basicConfig usually just adds StreamHandler.
root_logger.addHandler(file_handler)


# Get logger for this module *after* setup
logger = logging.getLogger(__name__)
logger.info(f"--- Application Logging Initialized (Console & File: {LOG_FILE.name}) ---")
# logger.info(f"Log file: {LOG_FILE}") # Redundant with above
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
    logger.info("--- Application Shutdown Complete ---")


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
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(BASE_DIR / "src")],
        log_level="info"
    )
