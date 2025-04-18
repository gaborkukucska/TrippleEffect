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

# Import ModelRegistry instance and settings
# Need DEFAULT_OLLAMA_PORT from model_registry for fallback
from src.config.settings import model_registry, settings
from src.config.model_registry import DEFAULT_OLLAMA_PORT

# --- Global placeholder for the manager and proxy process ---
agent_manager_instance: Optional[AgentManager] = None
ollama_proxy_process: Optional[subprocess.Popen] = None

# --- Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_manager_instance, ollama_proxy_process
    logger.info("Application startup sequence initiated...")

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
            # --- Verify Proxy Script Content ---
            try:
                with open(proxy_script_path, 'r', encoding='utf-8') as f:
                    proxy_code = f.read()
                if "app.get('/api/tags'" not in proxy_code:
                    logger.critical(f"Ollama Proxy Error: The script at {proxy_script_path} is missing the required '/api/tags' route handler. Please ensure the file content is correct. Aborting proxy start.")
                    proxy_can_start = False
                else:
                    logger.info("Proxy script content check passed ('/api/tags' route found).")
            except Exception as read_err:
                 logger.error(f"Ollama Proxy Error: Failed to read proxy script {proxy_script_path}: {read_err}", exc_info=True)
                 proxy_can_start = False
            # --- End Proxy Script Content Check ---

        # --- Proceed with Proxy Startup if checks passed ---
        if proxy_can_start:
            proxy_env = os.environ.copy()
            proxy_env["OLLAMA_PROXY_PORT"] = str(settings.OLLAMA_PROXY_PORT)
            proxy_target_url = settings.OLLAMA_BASE_URL or f"http://localhost:{DEFAULT_OLLAMA_PORT}"
            proxy_env["OLLAMA_PROXY_TARGET_URL"] = proxy_target_url

            logger.info(f"Starting Ollama proxy: {node_executable} {proxy_script_path}")
            logger.info(f"  Proxy Port: {proxy_env['OLLAMA_PROXY_PORT']}")
            logger.info(f"  Proxy Target URL: {proxy_env['OLLAMA_PROXY_TARGET_URL']}")

            try:
                ollama_proxy_process = subprocess.Popen(
                    [node_executable, str(proxy_script_path)],
                    env=proxy_env,
                    start_new_session=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                logger.info(f"Ollama proxy process started with PID: {ollama_proxy_process.pid}")
                app.state.ollama_proxy_process = ollama_proxy_process

                # --- Wait for proxy to become ready ---
                proxy_ready = False
                proxy_check_url = f"http://localhost:{proxy_env['OLLAMA_PROXY_PORT']}/"
                max_wait_time = 15
                check_interval = 0.5
                start_time = time.monotonic()
                logger.info(f"Waiting up to {max_wait_time}s for proxy at {proxy_check_url} to become ready...")

                while time.monotonic() - start_time < max_wait_time:
                    if ollama_proxy_process.poll() is not None:
                        logger.error(f"Ollama proxy process (PID: {ollama_proxy_process.pid}) exited prematurely while waiting for readiness. Code: {ollama_proxy_process.returncode}")
                        break

                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(proxy_check_url, timeout=check_interval) as response:
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

                    await asyncio.sleep(check_interval)

                if not proxy_ready:
                    logger.error(f"Ollama proxy did not become ready within {max_wait_time} seconds.")
                    # Attempt to terminate if it didn't become ready but is still running
                    if ollama_proxy_process and ollama_proxy_process.poll() is None:
                         logger.warning("Terminating non-ready proxy process...")
                         if os.name == 'posix': os.killpg(os.getpgid(ollama_proxy_process.pid), signal.SIGTERM)
                         else: ollama_proxy_process.terminate()
                         try: ollama_proxy_process.wait(timeout=2)
                         except subprocess.TimeoutExpired:
                              if os.name == 'posix': os.killpg(os.getpgid(ollama_proxy_process.pid), signal.SIGKILL)
                              else: ollama_proxy_process.kill()
                    app.state.ollama_proxy_process = None
                    ollama_proxy_process = None
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
    agent_manager_instance = AgentManager()
    logger.info("AgentManager instantiated.")

    app.state.agent_manager = agent_manager_instance
    logger.info("AgentManager instance stored in app.state.")

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

    # --- Stop Ollama Proxy (if running) ---
    proxy_process_to_stop = getattr(app.state, 'ollama_proxy_process', None)
    if proxy_process_to_stop and isinstance(proxy_process_to_stop, subprocess.Popen):
        logger.info(f"Attempting to stop Ollama proxy process (PID: {proxy_process_to_stop.pid})...")
        if proxy_process_to_stop.poll() is None:
            try:
                if os.name == 'posix':
                    os.killpg(os.getpgid(proxy_process_to_stop.pid), signal.SIGTERM)
                    logger.info(f"Sent SIGTERM to process group {os.getpgid(proxy_process_to_stop.pid)}")
                else:
                    proxy_process_to_stop.terminate()
                    logger.info(f"Sent terminate signal to process {proxy_process_to_stop.pid}")

                try:
                    proxy_process_to_stop.wait(timeout=5)
                    logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} terminated gracefully.")
                except subprocess.TimeoutExpired:
                    logger.warning(f"Ollama proxy process {proxy_process_to_stop.pid} did not terminate gracefully after 5s. Sending SIGKILL.")
                    if os.name == 'posix':
                         os.killpg(os.getpgid(proxy_process_to_stop.pid), signal.SIGKILL)
                    else:
                         proxy_process_to_stop.kill()
                    logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} killed.")
            except ProcessLookupError:
                 logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} already terminated.")
            except Exception as e:
                logger.error(f"Error stopping Ollama proxy process {proxy_process_to_stop.pid}: {e}", exc_info=True)
        else:
            logger.info(f"Ollama proxy process {proxy_process_to_stop.pid} was already stopped.")
        app.state.ollama_proxy_process = None
    # --- End Ollama Proxy Shutdown ---


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
        reload=False, # Disable reload to prevent double startup issues
        # reload_dirs=[str(BASE_DIR / "src")], # No longer needed if reload=False
        log_level="info"
    )
