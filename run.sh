#!/bin/bash

# Script to activate the virtual environment and run the TrippleEffect application.

echo "--- Attempting to run TrippleEffect ---"

# Define virtual environment directory
VENV_DIR=".venv"
PYTHON_CMD="python" # Default command

# Check if python3 exists, prefer it if it does
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
fi

# Check if the base Python command is available
if ! command -v $PYTHON_CMD &> /dev/null; then
    echo "Error: Python command ('$PYTHON_CMD') not found."
    echo "Please ensure Python 3.9+ is installed and accessible in your PATH."
    exit 1
fi

# --- Virtual Environment Activation ---
ACTIVATE_SCRIPT=""
# Check for Linux/macOS activate script
if [ -f "$VENV_DIR/bin/activate" ]; then
    ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"
# Check for Windows (Git Bash, Cygwin, etc.) activate script
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    ACTIVATE_SCRIPT="$VENV_DIR/Scripts/activate"
fi

if [ -z "$ACTIVATE_SCRIPT" ]; then
    echo "Error: Virtual environment activation script not found in '$VENV_DIR'."
    echo "Please run the setup script (./setup.sh) first to create the virtual environment."
    exit 1
fi

echo "Activating virtual environment: $ACTIVATE_SCRIPT"
# Use 'source' or '.' to run the activation script in the current shell context
source "$ACTIVATE_SCRIPT"
if [ $? -ne 0 ]; then
    echo "Error: Failed to activate virtual environment. Please try activating it manually:"
    echo "  source $ACTIVATE_SCRIPT"
    exit 1
fi
echo "Virtual environment activated."

# --- Run the Application ---
echo "Starting TrippleEffect application..."
# Use the python command found within the activated environment
$PYTHON_CMD -m src.main "$@" # Pass any command-line arguments through

# Check the exit code of the application
APP_EXIT_CODE=$?
if [ $APP_EXIT_CODE -ne 0 ]; then
    echo "Application exited with error code: $APP_EXIT_CODE"
fi

# Deactivation is usually handled when the script/shell exits, but can be added explicitly if needed
# deactivate
echo "--- TrippleEffect script finished ---"

exit $APP_EXIT_CODE
