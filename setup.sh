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

# Function to update or add a variable in the .env file
# Usage: update_env_var "VAR_NAME" "new_value"
update_env_var() {
    local var_name="$1"
    local new_value="$2"
    local env_file_path="$ENV_FILE" # Use global ENV_FILE definition

    # Escape backslashes, forward slashes, and ampersands in the value for sed
    local escaped_value
    escaped_value=$(echo "$new_value" | sed -e 's/\\/\\\\/g' -e 's/\//\\\//g' -e 's/\&/\\\&/g')

    # Check if the .env file exists
    if [ ! -f "$env_file_path" ]; then
        info "$env_file_path not found. Creating from $ENV_EXAMPLE_FILE."
        if [ -f "$ENV_EXAMPLE_FILE" ]; then
            cp "$ENV_EXAMPLE_FILE" "$env_file_path"
        else
            warning "$ENV_EXAMPLE_FILE not found. Creating empty $env_file_path."
            touch "$env_file_path"
        fi
    fi

    # Check if the variable exists (commented or uncommented)
    # Use grep -q to check quietly
    if grep -q -E "^\s*#?\s*${var_name}=" "$env_file_path"; then
        # Variable exists (commented or uncommented), replace the line
        # Use a different delimiter for sed (#) in case value contains /
        # This sed command replaces the line starting with optional whitespace/comment,
        # the variable name, and equals sign, with the new uncommented line.
        sed -i.bak "s#^\s*#?\s*${var_name}=.*#${var_name}=${escaped_value}#" "$env_file_path"
        info "Updated ${var_name} in ${env_file_path}."
    else
        # Variable doesn't exist, append it
        info "Adding ${var_name} to ${env_file_path}."
        # Add a newline before adding the var if file not empty
        if [ -s "$env_file_path" ]; then
           echo "" >> "$env_file_path"
        fi
        echo "${var_name}=${escaped_value}" >> "$env_file_path"
    fi

    # Clean up backup file potentially created by sed -i
    rm -f "${env_file_path}.bak"
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
      if [ -z "$REFERER_INPUT" ]; then
          update_env_var "OPENROUTER_REFERER" "$DEFAULT_REFERER"
      else
          update_env_var "OPENROUTER_REFERER" "$REFERER_INPUT"
      fi
      success "OpenRouter basic configuration updated."

      # Ask about defaults
      read -p "Set OpenRouter + google/gemini-flash-1.5 as default provider/model? [Y/n]: " set_or_defaults
      if [[ "$set_or_defaults" =~ ^[Yy]$ ]] || [ -z "$set_or_defaults" ]; then
          update_env_var "DEFAULT_AGENT_PROVIDER" "openrouter"
          update_env_var "DEFAULT_AGENT_MODEL" "google/gemini-flash-1.5"
          success "Set OpenRouter and Gemini Flash 1.5 as defaults."
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

# Final cleanup of sed backups just in case
rm -f "${ENV_FILE}.bak"

success "$ENV_FILE configuration complete."

# --- Finish ---
success "TrippleEffect setup finished!"
echo -e "\n${COLOR_YELLOW}Next steps:${COLOR_RESET}"
echo -e "1. Activate the virtual environment: ${COLOR_GREEN}source $VENV_DIR/bin/activate${COLOR_RESET}"
echo -e "2. Edit ${COLOR_GREEN}config.yaml${COLOR_RESET} to define your agents (ensure providers match your .env setup)."
echo -e "3. Run the application: ${COLOR_GREEN}python src/main.py${COLOR_RESET}"
echo -e "4. Access the UI in your browser (usually http://localhost:8000)."

exit 0
