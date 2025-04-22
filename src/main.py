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
from typing import Optional, TYPE_CHECKING # Added TYPE_CHECKING
import time # <<< --- IMPORT ADDED HERE ---

# --- Base Directory ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Load Environment Variables ---
dotenv_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=dotenv_path)
print(f"Attempted to load .env file from: {dotenv_path}") # Keep print for immediate feedback

# --- Configure Logging ---
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

timestamp = time.strftime("%Y%m%d_%H%M%S") # Now 'time' is defined
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

# Import ModelRegistry instance and settings
from src.config.settings import model_registry, settings
from src.config.model_registry import DEFAULT_OLLAMA_PORT

# --- Import Database Manager ---
from src.core.database_manager import db_manager, close_db_connection # Import manager and close function

# --- Type Hinting ---
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

# --- Global placeholder for the manager and proxy process ---
agent_manager_instance: Optional['AgentManager'] = None # Use forward reference string
ollama_proxy_process: Optional[subprocess.Popen] = None

# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown events.
    Initializes Database, Ollama Proxy (optional), Agent Manager, discovers models,
    and initializes bootstrap agents. Handles graceful shutdown.
    """
    # --- Moved Import Here ---
    from src.agents.manager import AgentManager
    # --- End Moved Import ---

    global agent_manager_instance, ollama_proxy_process
    logger.info("Application startup sequence initiated...")

    # --- Initialize Database Explicitly ---
    # Call the async initialization function here, within the running event loop
    logger.info("Lifespan: Initializing DatabaseManager...")
    await db_manager._initialize_db() # Await the init here
    if db_manager._session_local is None:
        # Make this more prominent as it prevents DB operations
        logger.critical("Lifespan: DatabaseManager initialization FAILED! Database operations will not work.")
        # Depending on requirements, might want to exit:
        # raise RuntimeError("Critical component DatabaseManager failed to initialize.")
    else:
        logger.info("Lifespan: DatabaseManager initialized successfully.")


    # --- Start Ollama Proxy (if enabled) ---
    if settings.USE_OLLAMA_PROXY:
        logger.info("Ollama proxy is enabled in settings. Attempting to start...")
        node_executable = shutil.which("node")
        proxy_script_path = BASE_DIR / "ollama-proxy" / "server.js"
        proxy_can_start = True # Flag to track if proxy should be started

        if not node_executable:
            logger.error("Ollama Proxy Error: 'node' command not found in PATH. Cannot start proxy.")
            proxy_can_start = False
        elif not proxy_script_path.is_file():
            logger.error(f"Ollama Proxy Error: Proxy script not found at {proxy_script_path}. Cannot start proxy.")
            proxy_can_start = False
        else:
            # Verify Proxy Script Content
            try:
                with open(proxy_script_path, 'r', encoding='utf-8') as f:
                    proxy_code = f.read()
                # Simple check for a known route handler
                if "app.get('/api/tags'" not in proxy_code and "app.post('/api/chat'" not in proxy_code:
                    logger.critical(f"Ollama Proxy Error: The script at {proxy_script_path} appears incomplete or incorrect. Missing expected route handlers. Aborting proxy start.")
                    proxy_can_start = False
                else:
                    logger.info("Proxy script content check passed.")
            except Exception as read_err:
                 logger.error(f"Ollama Proxy Error: Failed to read proxy script {proxy_script_path}: {read_err}", exc_info=True)
                 proxy_can_start = False

        if proxy_can_start:
            proxy_env = os.environ.copy()
            proxy_env["OLLAMA_PROXY_PORT"] = str(settings.OLLAMA_PROXY_PORT)
            # Determine target URL for the proxy
            proxy_target_url = settings.OLLAMA_BASE_URL or f"http://localhost:{DEFAULT_OLLAMA_PORT}"
            proxy_env["OLLAMA_PROXY_TARGET_URL"] = proxy_target_url

            logger.info(f"Starting Ollama proxy: {node_executable} {proxy_script_path}")
            logger.info(f"  Proxy Port: {proxy_env['OLLAMA_PROXY_PORT']}")
            logger.info(f"  Proxy Target URL: {proxy_env['OLLAMA_PROXY_TARGET_URL']}")

            try:
                # Start the Node.js process
                ollama_proxy_process = subprocess.Popen(
                    [node_executable, str(proxy_script_path)],
                    env=proxy_env,
                    start_new_session=True, # Important for clean termination on Linux/macOS
                    stdout=subprocess.PIPE, # Capture stdout
                    stderr=subprocess.PIPE, # Capture stderr
                    text=True, # Decode stdout/stderr as text
                    encoding='utf-8',
                    errors='replace' # Handle potential decoding errors
                )
                logger.info(f"Ollama proxy process started with PID: {ollama_proxy_process.pid}")
                app.state.ollama_proxy_process = ollama_proxy_process # Store for shutdown

                # --- Wait for proxy to become ready ---
                proxy_ready = False
                proxy_check_url = f"http://localhost:{proxy_env['OLLAMA_PROXY_PORT']}/" # Check root path
                max_wait_time = 15 # seconds
                check_interval = 0.5 # seconds
                start_time = time.monotonic()
                logger.info(f"Waiting up to {max_wait_time}s for proxy at {proxy_check_url} to become ready...")

                while time.monotonic() - start_time < max_wait_time:
                    # Check if the process exited prematurely
                    if ollama_proxy_process.poll() is not None:
                        logger.error(f"Ollama proxy process (PID: {ollama_proxy_process.pid}) exited prematurely while waiting for readiness. Code: {ollama_proxy_process.returncode}")
                        # Read stderr if available
                        try:
                            stderr_output = ollama_proxy_process.stderr.read() if ollama_proxy_process.stderr else "N/A"
                            logger.error(f"Proxy stderr: {stderr_output}")
                        except Exception as e:
                            logger.error(f"Error reading proxy stderr after premature exit: {e}")
                        break

                    # Attempt to connect
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(proxy_check_url, timeout=check_interval) as response:
                                # Check for a successful status code (e.g., 200 OK from proxy's root)
                                if response.status == 200:
                                    logger.info(f"Ollama proxy is ready at {proxy_check_url}.")
                                    proxy_ready = True
                                    break
                                else:
                                     logger.debug(f"Proxy check attempt failed with status {response.status}. Retrying...")
                    except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
                        logger.debug(f"Proxy not yet reachable at {proxy_check_url}. Retrying...")
                    except Exception as check_err:
                         logger.warning(f"Unexpected error during proxy readiness check: {check_err}")

                    await asyncio.sleep(check_interval) # Wait before next check

                if not proxy_ready:
                    logger.error(f"Ollama proxy did not become ready within {max_wait_time} seconds.")
                    # Attempt to terminate if it didn't become ready but is still running
                    if ollama_proxy_process and ollama_proxy_process.poll() is None:
                         logger.warning("Terminating non-ready proxy process...")
                         signal_to_send_kill = signal.SIGKILL
                         process_group_id = os.getpgid(ollama_proxy_process.pid) if os.name == 'posix' else None
                         try:
                             if process_group_id: os.killpg(process_group_id, signal.SIGTERM)
                             else: ollama_proxy_process.terminate()
                             ollama_proxy_process.wait(timeout=2)
                         except (subprocess.TimeoutExpired, ProcessLookupError):
                              logger.warning("Proxy did not terminate after SIGTERM, sending SIGKILL.")
                              if process_group_id: os.killpg(process_group_id, signal_to_send_kill)
                              else: ollama_proxy_process.kill()
                         except Exception as term_err:
                             logger.error(f"Error during proxy termination: {term_err}")
                    app.state.ollama_proxy_process = None # Clear from state
                    ollama_proxy_process = None # Clear global
                else:
                    logger.info("Ollama proxy appears to be running and ready.")
                # --- End Wait for proxy ---

            except Exception as e:
                logger.error(f"Failed during Ollama proxy startup or readiness check: {e}", exc_info=True)
                ollama_proxy_process = None
                app.state.ollama_proxy_process = None
        # --- End Proxy Startup Logic ---

    else:
        logger.info("Ollama proxy is disabled in settings.")
    # --- End Ollama Proxy Startup Section ---


    logger.info("Instantiating AgentManager...")
    agent_manager_instance = AgentManager() # AgentManager is now imported
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
    except Exception as e:
        logger.error(f"Lifespan: Error during provider/model discovery: {e}", exc_info=True)

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

    # 2. Stop Ollama Proxy (if running)
    proxy_process_to_stop = getattr(app.state, 'ollama_proxy_process', None)
    if proxy_process_to_stop and isinstance(proxy_process_to_stop, subprocess.Popen):
        logger.info(f"Attempting to stop Ollama proxy process (PID: {proxy_process_to_stop.pid})...")
        if proxy_process_to_stop.poll() is None: # Check if it's still running
            try:
                signal_to_send_kill = signal.SIGKILL
                process_group_id = os.getpgid(proxy_process_to_stop.pid) if os.name == 'posix' else None
                # Try graceful termination first
                if process_group_id:
                    os.killpg(process_group_id, signal.SIGTERM); logger.info(f"Sent SIGTERM to process group {process_group_id}")
                else:
                    proxy_process_to_stop.terminate(); logger.info(f"Sent terminate signal to process {proxy_process_to_stop.pid}")
                # Wait briefly
                try:
                    proxy_process_to_stop.wait(timeout=5)
                    logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} terminated gracefully.")
                except subprocess.TimeoutExpired:
                    logger.warning(f"Ollama proxy process {proxy_process_to_stop.pid} did not terminate gracefully after 5s. Sending SIGKILL.")
                    if process_group_id: os.killpg(process_group_id, signal_to_send_kill)
                    else: proxy_process_to_stop.kill(); logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} killed.")
            except ProcessLookupError:
                 logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} already terminated.")
            except Exception as e:
                logger.error(f"Error stopping Ollama proxy process {proxy_process_to_stop.pid}: {e}", exc_info=True)
        else:
            logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} was already stopped.")
        # Clear the reference from app state
        app.state.ollama_proxy_process = None

    # 3. Close Database Connection Pool (Important: Do this *after* AgentManager cleanup)
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
    version="2.22", # Update version for Phase 22
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
    logger.info("Starting Uvicorn server directly...")
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False, # Keep reload False for stability with background processes/state
        log_level=log_level_str.lower() # Use configured log level for Uvicorn too
    )
