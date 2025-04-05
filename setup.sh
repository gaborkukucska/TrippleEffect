#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e
# Treat unset variables as an error when substituting.
# set -u # Temporarily disable as read -p can lead to unbound variables if user hits Ctrl+C
# Pipeliens return status of the last command to exit with non-zero status.
set -o pipefail

# --- Configuration ---
PROJECT_DIR=$(pwd) # Assumes script is run from the project root
VENV_DIR=".venv"
REQUIREMENTS_FILE="requirements.txt"
ENV_EXAMPLE_FILE=".env.example"
ENV_FILE=".env"
PYTHON_CMD="python3" # Change if your python command is different

# --- Colors for Output ---
COLOR_RESET='\033[0m'
COLOR_GREEN='\033[0;32m'
COLOR_YELLOW='\033[0;33m'
COLOR_RED='\033[0;31m'
COLOR_BLUE='\033[0;34m'

# --- Helper Functions ---
info() {
  echo -e "${COLOR_BLUE}INFO: $1${COLOR_RESET}"
}

success() {
  echo -e "${COLOR_GREEN}SUCCESS: $1${COLOR_RESET}"
}

warning() {
  echo -e "${COLOR_YELLOW}WARNING: $1${COLOR_RESET}"
}

error() {
  echo -e "${COLOR_RED}ERROR: $1${COLOR_RESET}" >&2
  exit 1
}

check_command() {
  if ! command -v "$1" &> /dev/null; then
    error "Command '$1' not found. Please install it and try again."
  fi
}

# Function to update or add a variable in the .env file (using line-by-line processing)
# Usage: update_env_var "VAR_NAME" "new_value"
update_env_var() {
    local var_name="$1"
    local new_value="$2"
    local env_file_path="$ENV_FILE"
    local temp_env_file="${env_file_path}.tmp"
    local updated=false # Flag to track if we updated an existing var

    # --- Debug: Print original value ---
    # echo "Debug: Original value for $var_name: '$new_value'" # Keep commented out unless needed

    # Check if the .env file exists, create if necessary
    if [ ! -f "$env_file_path" ]; then
        info "$env_file_path not found. Creating from $ENV_EXAMPLE_FILE."
        if [ -f "$ENV_EXAMPLE_FILE" ]; then
            cp "$ENV_EXAMPLE_FILE" "$env_file_path" || error "Failed to copy $ENV_EXAMPLE_FILE"
        else
            warning "$ENV_EXAMPLE_FILE not found. Creating empty $env_file_path."
            touch "$env_file_path" || error "Failed to create $env_file_path"
        fi
    fi

    # Ensure temp file is clean before starting
    rm -f "$temp_env_file"

    # Process the file line by line
    # Use standard, safe read loop
    while IFS= read -r line || [ -n "$line" ]; do
        # Match lines starting with optional whitespace, optional #, optional whitespace, var_name=
        # Using Bash regex matching
        if [[ "$line" =~ ^[[:space:]]*#?[[:space:]]*${var_name}= ]]; then
            # Found the line, print the new value (uncommented) to the temp file
            printf '%s=%s\n' "$var_name" "$new_value" >> "$temp_env_file"
            # info "Updating ${var_name} line in temporary file." # Can be verbose, keep commented
            updated=true
        else
            # Keep the original line, print to the temp file
            printf '%s\n' "$line" >> "$temp_env_file"
        fi
    done < "$env_file_path"

    # If the variable was not found and updated during the loop, append it
    if [ "$updated" = false ]; then
        info "Adding ${var_name} to temporary file."
        # Add a newline before adding the var if temp file not empty
        if [ -s "$temp_env_file" ]; then
           # Check if temp file already ends with newline before adding another
           # This avoids double newlines if the original file ended cleanly
           if [ "$(tail -c1 "$temp_env_file" | wc -l)" -eq 0 ]; then
               printf '\n' >> "$temp_env_file"
           fi
        fi
         # Append the new variable line using printf for safety
        printf '%s=%s\n' "$var_name" "$new_value" >> "$temp_env_file"
    fi

    # Replace original file with the temporary file
    mv "$temp_env_file" "$env_file_path" || error "Failed to update $env_file_path from temporary file."
    info "Successfully updated ${env_file_path} for ${var_name}."

    # No .bak file cleanup needed with this method
}


# --- Main Script Logic ---

info "Starting TrippleEffect Setup..."
info "Project Directory: $PROJECT_DIR"

# 1. Check Dependencies
info "Checking prerequisites..."
check_command "git"
check_command "$PYTHON_CMD"
# Check Python version? (Requires parsing output) - Skip for now, assume 3.9+ as per README

# Termux Specific Checks
if uname -o | grep -q Android; then
  info "Termux environment detected. Checking build tools..."
  if ! command -v ar &> /dev/null || ! command -v gcc &> /dev/null; then
    warning "Build tools ('binutils', 'build-essential') might be missing."
    read -p "Attempt to install them using 'pkg'? [Y/n]: " install_build_tools
    if [[ "$install_build_tools" =~ ^[Yy]$ ]] || [ -z "$install_build_tools" ]; then
      info "Running 'pkg update && pkg upgrade'..."
      pkg update && pkg upgrade || error "Failed to update packages."
      info "Running 'pkg install binutils build-essential -y'..."
      pkg install binutils build-essential -y || error "Failed to install build tools. Please install manually and retry."
      success "Build tools installed/updated."
    else
      warning "Skipping build tool installation. Python package installation might fail later."
    fi
  else
    info "Build tools seem to be present."
  fi
fi
success "Prerequisites checked."

# 2. Setup Virtual Environment
info "Setting up Python virtual environment ($VENV_DIR)..."
if [ -d "$VENV_DIR" ]; then
  read -p "Directory '$VENV_DIR' already exists. Remove and recreate? [y/N]: " recreate_venv
  if [[ "$recreate_venv" =~ ^[Yy]$ ]]; then
    info "Removing existing $VENV_DIR..."
    rm -rf "$VENV_DIR" || error "Failed to remove $VENV_DIR."
    "$PYTHON_CMD" -m venv "$VENV_DIR" || error "Failed to create virtual environment."
    success "Virtual environment recreated."
  else
    info "Using existing virtual environment."
  fi
else
  "$PYTHON_CMD" -m venv "$VENV_DIR" || error "Failed to create virtual environment."
  success "Virtual environment created."
fi

# 3. Install Requirements
info "Installing Python requirements from $REQUIREMENTS_FILE..."
# Use pip from the created venv
"$VENV_DIR/bin/pip" install --upgrade pip || warning "Failed to upgrade pip. Continuing..."
"$VENV_DIR/bin/pip" install -r "$REQUIREMENTS_FILE" || error "Failed to install requirements. Check error messages above."
success "Python requirements installed."

# 4. Configure .env file
info "Configuring $ENV_FILE..."

# Ensure .env file exists, copy from example if needed
# update_env_var function handles creation now if needed, this check is slightly redundant but harmless
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE_FILE" ]; then
        info "Creating $ENV_FILE from $ENV_EXAMPLE_FILE."
        cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    else
        warning "$ENV_EXAMPLE_FILE not found. Creating empty $ENV_FILE."
        touch "$ENV_FILE"
    fi
fi

# --- OpenRouter Setup ---
info "--- OpenRouter Configuration ---"
OPENROUTER_KEY=""
while [ -z "$OPENROUTER_KEY" ]; do
  read -sp "Enter your OpenRouter API Key (leave blank to skip): " OPENROUTER_KEY
  echo # Newline after secure read
  if [ -z "$OPENROUTER_KEY" ]; then
      warning "Skipping OpenRouter API key setup."
      break # Exit the loop if skipped
  elif [[ "$OPENROUTER_KEY" != sk-or-v1-* ]]; then
       warning "Key doesn't look like a valid OpenRouter key (should start with 'sk-or-v1-'). Please try again or leave blank to skip."
       OPENROUTER_KEY="" # Reset to loop again
  else
      update_env_var "OPENROUTER_API_KEY" "$OPENROUTER_KEY"
      # Set a default Referer
      DEFAULT_REFERER="http://localhost:8000/TrippleEffect"
      read -p "Enter OpenRouter Referer [default: $DEFAULT_REFERER]: " REFERER_INPUT
      REFERER_VALUE=${REFERER_INPUT:-$DEFAULT_REFERER} # Use default if empty
      update_env_var "OPENROUTER_REFERER" "$REFERER_VALUE"

      success "OpenRouter basic configuration updated."

      # Ask about defaults
      read -p "Set OpenRouter + google/gemini-flash-1.5 as default provider/model? [Y/n]: " set_or_defaults
      if [[ "$set_or_defaults" =~ ^[Yy]$ ]] || [ -z "$set_or_defaults" ]; then
          update_env_var "DEFAULT_AGENT_PROVIDER" "openrouter"
          update_env_var "DEFAULT_AGENT_MODEL" "google/gemini-2.5-pro-exp-03-25:free"
          success "Set OpenRouter and Gemini Pro 2.5 as defaults."
      fi
      break # Exit loop after successful setup
  fi
done

# --- Ollama Setup ---
info "--- Ollama Configuration ---"
DEFAULT_OLLAMA_URL="http://localhost:11434"
read -p "Enter Ollama Base URL [default: $DEFAULT_OLLAMA_URL]: " OLLAMA_URL_INPUT
OLLAMA_URL=${OLLAMA_URL_INPUT:-$DEFAULT_OLLAMA_URL} # Use default if input is empty
update_env_var "OLLAMA_BASE_URL" "$OLLAMA_URL"
success "Ollama configuration updated."


# --- OpenAI Setup (Optional) ---
info "--- OpenAI Configuration (Optional) ---"
read -p "Do you want to configure OpenAI? [y/N]: " configure_openai
if [[ "$configure_openai" =~ ^[Yy]$ ]]; then
    OPENAI_KEY=""
    while [ -z "$OPENAI_KEY" ]; do
        read -sp "Enter your OpenAI API Key: " OPENAI_KEY
        echo
        if [ -z "$OPENAI_KEY" ]; then
            warning "OpenAI key cannot be empty if you choose to configure it."
        elif [[ "$OPENAI_KEY" != sk-* ]]; then
            warning "Key doesn't look like a valid OpenAI key (should start with 'sk-'). Please try again."
             OPENAI_KEY=""
        else
            update_env_var "OPENAI_API_KEY" "$OPENAI_KEY"
            success "OpenAI configuration updated."
            break
        fi
    done
else
    info "Skipping OpenAI configuration."
fi

# Final cleanup of sed backups - not needed with new function
# rm -f "${ENV_FILE}.bak"

success "$ENV_FILE configuration complete."

# --- Finish ---
success "TrippleEffect setup finished!"
echo -e "\n${COLOR_YELLOW}Next steps:${COLOR_RESET}"
echo -e "1. Activate the virtual environment: ${COLOR_GREEN}source $VENV_DIR/bin/activate${COLOR_RESET}"
echo -e "2. Edit ${COLOR_GREEN}config.yaml${COLOR_RESET} to define your agents (ensure providers match your .env setup)."
echo -e "3. Run the application from the project root (${PROJECT_DIR}) using: ${COLOR_GREEN}python -m src.main${COLOR_RESET}" # <-- UPDATED COMMAND
echo -e "4. Access the UI in your browser (usually http://localhost:8000)."

exit 0
