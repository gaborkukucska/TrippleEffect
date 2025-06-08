# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import os
import subprocess
import signal
import shutil
import aiohttp # Required for proxy readiness check
from dotenv import load_dotenv
import logging
import logging.handlers
import asyncio
from typing import Optional
import time

# --- Import BASE_DIR from settings ---
from src.config.settings import BASE_DIR # Import BASE_DIR

# --- Load Environment Variables ---
dotenv_path = BASE_DIR / '.env'
# Force override of existing environment variables with values from .env
load_dotenv(dotenv_path=dotenv_path, override=True)
print(f"Attempted to load .env file from: {dotenv_path} (Override=True)") # Keep print for immediate feedback

# --- Configure Logging ---
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

timestamp = time.strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"app_{timestamp}.log"

log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)

# Configure root logger
logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True)

# File Handler
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(log_level)

# Add handlers to the root logger
root_logger = logging.getLogger()
# Clear existing handlers if necessary (especially in reload scenarios)
if root_logger.hasHandlers():
    # Be careful not to remove handlers added by libraries if needed,
    # but for basic setup, clearing default handlers is usually fine.
    # root_logger.handlers.clear() # Temporarily commented out, check if needed
    pass
root_logger.addHandler(file_handler)

# Add Console Handler (ensure logs also go to console)
# basicConfig should add a StreamHandler by default if no handlers are present.
# If running with uvicorn and its logging, this might duplicate. Check output.
# If needed:
# console_handler = logging.StreamHandler()
# console_handler.setFormatter(log_formatter)
# console_handler.setLevel(log_level)
# if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
#      root_logger.addHandler(console_handler)


logger = logging.getLogger(__name__) # Get logger for this module
logger.info(f"--- Application Logging Initialized (Level: {log_level_str}, Console & File: {LOG_FILE.name}) ---")


# Import the routers and the setup function for AgentManager injection
from src.api import http_routes, websocket_manager

# --- Import the AgentManager class ---
from src.agents.manager import AgentManager
# --- End Import ---

# Import ModelRegistry instance and settings
from src.config.settings import model_registry, settings
from src.config.model_registry import DEFAULT_OLLAMA_PORT

# --- Import Database Manager ---
from src.core.database_manager import db_manager, close_db_connection # Import manager and close function

# --- Global placeholder for the manager and proxy process ---
agent_manager_instance: Optional[AgentManager] = None
ollama_proxy_process: Optional[subprocess.Popen] = None

# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown events.
    Initializes Database, Ollama Proxy (optional), Agent Manager, discovers models,
    and initializes bootstrap agents. Handles graceful shutdown.
    """
    global agent_manager_instance, ollama_proxy_process
    logger.info("Application startup sequence initiated...")

    # --- Initialize Database Explicitly ---
    # Call the async initialization function here, within the running event loop
    logger.info("Lifespan: Initializing DatabaseManager...")
    await db_manager._initialize_db() # <<< *** FIX: Await the init here ***
    if db_manager._session_local is None:
        # Make this more prominent as it prevents DB operations
        logger.critical("Lifespan: DatabaseManager initialization FAILED! Database operations will not work.")
        # Depending on requirements, might want to exit:
        # raise RuntimeError("Critical component DatabaseManager failed to initialize.")
    else:
        logger.info("Lifespan: DatabaseManager initialized successfully.")

    logger.info("Instantiating AgentManager...")
    agent_manager_instance = AgentManager()
    logger.info("AgentManager instantiated.")

    # Store manager in app state for access in request handlers (dependencies)
    app.state.agent_manager = agent_manager_instance
    logger.info("AgentManager instance stored in app.state.")

    # Inject manager into WebSocket manager
    websocket_manager.set_agent_manager(agent_manager_instance)
    logger.info("AgentManager instance injected into WebSocketManager.")

    logger.info("Lifespan: Discovering reachable providers and available models...")
    try:
        await model_registry.discover_models_and_providers()
        logger.info("Lifespan: Provider and model discovery completed.")

        # Initialize local provider lists after model discovery
        if agent_manager_instance:
            logger.info("Lifespan: Initializing local provider lists in AgentManager...")
            await agent_manager_instance._initialize_local_provider_lists()
            logger.info("Lifespan: Local provider lists initialization completed.")
        else:
            logger.error("Lifespan: AgentManager instance not available for initializing local provider lists.")

    except Exception as e:
        logger.error(f"Lifespan: Error during provider/model discovery or local provider init: {e}", exc_info=True)

    logger.info("Lifespan: Initializing bootstrap agents...")
    try:
        # Run initialization in a task to avoid blocking startup if it takes time
        init_task = asyncio.create_task(agent_manager_instance.initialize_bootstrap_agents())
        # Await completion if bootstrap agents must be ready before yield
        await init_task
        logger.info("Lifespan: Bootstrap agent initialization task completed.")
    except Exception as e:
        logger.critical(f"Lifespan: CRITICAL ERROR during bootstrap agent initialization: {e}", exc_info=True)
        # Depending on severity, might want to prevent app from starting fully
        # raise SystemExit("Failed to initialize critical bootstrap agents.") from e

    logger.info("Application startup complete. Ready to accept requests.")
    yield # Application runs here

    # --- Shutdown Logic ---
    logger.info("Application shutdown sequence initiated...")
    # 1. Cleanup Agent Manager (saves metrics, quarantine, ends DB session)
    if app.state.agent_manager:
        try:
            await app.state.agent_manager.cleanup_providers()
            logger.info("Lifespan: Agent Manager cleanup finished (providers, metrics, quarantine, DB session ended).")
        except Exception as e:
            logger.error(f"Lifespan: Error during AgentManager cleanup: {e}", exc_info=True)
    else:
            logger.warning("Lifespan: AgentManager instance not found in app.state during shutdown.")

    # 2. Close Database Connection Pool (Important: Do this *after* AgentManager cleanup)
    logger.info("Lifespan: Closing database connection pool...")
    try:
        await close_db_connection() # Call the close function from database_manager
        logger.info("Lifespan: Database connection pool closed.")
    except Exception as e:
        logger.error(f"Lifespan: Error closing database connection: {e}", exc_info=True)

    logger.info("--- Application Shutdown Complete ---")


# Create the FastAPI application instance
logger.info("Creating FastAPI app instance...")
app = FastAPI(
    title="TrippleEffect",
    version="2.25", # Updated Version
    description="Asynchronous Multi-Agent Framework with Dynamic Orchestration",
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
    logger.warning(f"Static files directory not found at {static_files_path}, UI will not load.")


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
    # Use the log_level_str defined earlier (after initial load_dotenv)
    # Ensure it's lowercase for uvicorn
    uvicorn_log_level = log_level_str.lower()
    # Basic validation just in case log_level_str itself is bad
    valid_uvicorn_levels = ['critical', 'error', 'warning', 'info', 'debug', 'trace']
    if uvicorn_log_level not in valid_uvicorn_levels:
        logger.warning(f"Processed LOG_LEVEL '{log_level_str}' is invalid for Uvicorn. Defaulting Uvicorn log level to 'info'.")
        # uvicorn_log_level = 'info' # No longer needed

    logger.info(f"Starting Uvicorn server directly (using root logger level: {log_level_str})...")
    # Remove log_level parameter to let Uvicorn use the already configured root logger
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False # Keep reload False for stability with background processes/state
        # log_level parameter removed
    )
