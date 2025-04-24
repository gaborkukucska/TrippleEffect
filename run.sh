#!/bin/bash

# run.sh - Script to activate the virtual environment and run the TrippleEffect application

echo "--- Starting TrippleEffect Application ---"

# --- Define Virtual Environment Directory ---
VENV_DIR=".venv"

# --- Check if Virtual Environment Exists ---
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Virtual environment '$VENV_DIR' not found."
    echo "Please run the setup script first: ./setup.sh"
    exit 1
fi

# --- Activate Virtual Environment ---
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to activate virtual environment."
    echo "Try activating it manually: source $VENV_DIR/bin/activate"
    exit 1
fi
echo "Virtual environment activated."

# --- Run the Application ---
echo "Launching TrippleEffect (uvicorn src.main:app)..."
# Run uvicorn directly, omitting --log-level to use default/root logger config
# Pass any arguments passed to run.sh directly to the uvicorn command
uvicorn src.main:app --host 0.0.0.0 --port 8000 "$@"

# --- Check Exit Status (Optional) ---
EXIT_STATUS=$?
if [ $EXIT_STATUS -ne 0 ]; then
    echo "WARNING: Application exited with status $EXIT_STATUS."
fi

# --- Deactivate (Optional - Usually happens when shell exits anyway) ---
# echo "Deactivating virtual environment..."
# deactivate

echo "--- TrippleEffect Application Finished ---"

exit $EXIT_STATUS
