# START OF FILE src/main.py
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os # Import os for environment variables later if needed
from dotenv import load_dotenv # Import load_dotenv

# Import the routers
from src.api import http_routes # Import http_routes
# Placeholder for websocket_manager import

# Load environment variables from .env file
# Useful for API keys later. Make sure .env is in .gitignore
load_dotenv()

# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent # This should point to TrippleEffect-main/

# Create the FastAPI application instance
app = FastAPI(title="TrippleEffect", version="0.1.0")

# Mount the static files directory
# This allows serving CSS, JS, images etc.
# It needs to point to the 'static' directory at the project root
static_files_path = BASE_DIR / "static"
if static_files_path.exists() and static_files_path.is_dir():
    app.mount("/static", StaticFiles(directory=static_files_path), name="static")
else:
    print(f"Warning: Static files directory not found at {static_files_path}")
    # Optionally, create the directory if it doesn't exist
    # static_files_path.mkdir(parents=True, exist_ok=True)
    # print(f"Created static files directory at {static_files_path}")
    # app.mount("/static", StaticFiles(directory=static_files_path), name="static")

# Include the API routers
app.include_router(http_routes.router) # Include the HTTP router
# Placeholder for including websocket_manager router


# Basic root endpoint removed, as it's now handled by http_routes.router


# Configuration for running the app with uvicorn directly
if __name__ == "__main__":
    print("Starting Uvicorn server...")
    # Use 0.0.0.0 to make it accessible on the network (important for Termux)
    # Use reload=True for development convenience
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, app_dir="src")
