#!/bin/bash

# setup.sh - Setup script for TrippleEffect
# Detects environment, installs dependencies, and helps configure the .env file interactively.

echo "--- Starting TrippleEffect Setup ---"

# --- Define Base Directory and .env Path ---
BASE_DIR=$(pwd)
ENV_FILE="$BASE_DIR/.env"
ENV_EXAMPLE_FILE="$BASE_DIR/.env.example"

# --- Function to Prompt for and Update/Add .env Variable ---
# Usage: prompt_and_update KEY_NAME "Description" "Default/Current Value" IS_SECRET(true/false)
prompt_and_update() {
    local key="$1"
    local description="$2"
    local current_value="$3" # Pass current value if found
    local is_secret="$4"
    local input_value=""
    local prompt_text=""
    local read_opts=""

    # Prepare prompt text
    prompt_text="Enter value for $key ($description)"
    if [ -n "$current_value" ]; then
        if [ "$is_secret" = true ]; then
            prompt_text="$prompt_text (current: ****${current_value: -4}, press Enter to keep): "
        else
            prompt_text="$prompt_text (current: '$current_value', press Enter to keep): "
        fi
    else
         prompt_text="$prompt_text (press Enter to skip/leave default): "
    fi

    # Set read options for secrets
    if [ "$is_secret" = true ]; then
        read_opts="-s" # Silent mode for passwords/keys
    fi

    # Prompt user
    read $read_opts -p "$prompt_text" input_value
    # Add a newline after secret input for cleaner output
    if [ "$is_secret" = true ]; then echo; fi

    # Use user input if provided, otherwise keep current value
    local final_value=""
    if [ -n "$input_value" ]; then
        final_value="$input_value"
    else
        final_value="$current_value" # Keep existing if user pressed Enter
    fi

    # Update or add the line in .env file if a value is set
    if [ -n "$final_value" ]; then
        # Escape potential special characters for sed (basic escaping for common chars)
        local escaped_value=$(echo "$final_value" | sed -e 's/[\/&]/\\&/g')
        local escaped_key=$(echo "$key" | sed -e 's/[\/&]/\\&/g')

        # Check if key exists (commented or uncommented)
        if grep -qE "^(#\s*)?${escaped_key}=" "$ENV_FILE"; then
            # Key exists, replace the line (handles commented/uncommented)
            # Use a temporary file for safer sed operation, especially across OSes
            local temp_env_file=$(mktemp)
            sed "s|^#*\s*${escaped_key}=.*|${escaped_key}=${escaped_value}|" "$ENV_FILE" > "$temp_env_file" && mv "$temp_env_file" "$ENV_FILE"
            echo "  Updated $key in $ENV_FILE"
        else
            # Key doesn't exist, append it
            echo "${key}=${final_value}" >> "$ENV_FILE"
            echo "  Added $key to $ENV_FILE"
        fi
    elif [ -n "$current_value" ]; then
         echo "  Kept existing value for $key."
    else
         echo "  Skipped $key (no value provided or kept)."
    fi
}

# --- Function to Prompt for Multi-Keys ---
# Usage: prompt_multi_key PROVIDER_PREFIX "Description"
prompt_multi_key() {
    local provider_prefix="$1"
    local description="$2"
    local base_key_name="${provider_prefix}_API_KEY"
    local current_keys=()
    local key_index=0
    local input_value=""
    local prompt_text=""

    echo "" # Newline for clarity
    echo "--- Configuring $provider_prefix Keys ---"

    # Find existing keys (base and indexed)
    # Need to be careful with grep patterns if using shell arrays across different shells
    # Simple approach: grep and process line by line
    current_keys_map=() # Use simple array to store key=value
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^${base_key_name}(_[0-9]+)?= ]]; then
            current_keys_map+=("$line")
        fi
    done < <(grep -E "^${base_key_name}(_[0-9]+)?=" "$ENV_FILE" 2>/dev/null || true)

    echo "Existing $provider_prefix keys found in .env:"
    if [ ${#current_keys_map[@]} -eq 0 ]; then
        echo "  None"
    else
        for kv in "${current_keys_map[@]}"; do
            local k="${kv%%=*}"
            local v="${kv#*=}"
             # Check if key ends with number (indexed) or is base key
            if [[ "$k" =~ _[0-9]+$ ]]; then
                local idx="${k##*_}"
                echo "  - ${provider_prefix}_API_KEY_${idx} = ****${v: -4}"
                # Keep track of highest index
                if [[ "$idx" -gt "$key_index" ]]; then key_index=$idx; fi
            elif [ "$k" == "$base_key_name" ]; then
                echo "  - ${base_key_name} = ****${v: -4}"
            fi
        done
    fi

    # Loop to add keys
    while true; do
        local next_index=$((key_index + 1))
        local key_to_add=""
        # Use base key name if no keys exist yet, otherwise use next index
        if [ ${#current_keys_map[@]} -eq 0 ] && [ "$key_index" -eq 0 ]; then
             key_to_add="$base_key_name"
             prompt_text="Enter value for $key_to_add ($description) (or press Enter to skip): "
        else
             key_to_add="${base_key_name}_${next_index}"
             prompt_text="Add another $provider_prefix key? Enter value for ${key_to_add} (or press Enter to finish): "
        fi

        read -s -p "$prompt_text" input_value
        echo # Newline after secret input

        if [ -z "$input_value" ]; then
            # If it was the *first* key prompt and they skipped, say so
            if [ ${#current_keys_map[@]} -eq 0 ] && [ "$key_index" -eq 0 ]; then
                 echo "  Skipped adding initial $provider_prefix key."
            fi
            echo "Finished adding $provider_prefix keys."
            break # Exit loop if user presses Enter
        fi

        # Add/Update the key
        local escaped_value=$(echo "$input_value" | sed -e 's/[\/&]/\\&/g')
        local escaped_key=$(echo "$key_to_add" | sed -e 's/[\/&]/\\&/g')
        local temp_env_file=$(mktemp)

        # Remove old entry if exists (commented or not)
        if grep -qE "^(#\s*)?${escaped_key}=" "$ENV_FILE"; then
            sed "/^#*\s*${escaped_key}=/d" "$ENV_FILE" > "$temp_env_file" && mv "$temp_env_file" "$ENV_FILE"
        fi
        # Append the new key
        echo "${key_to_add}=${input_value}" >> "$ENV_FILE"
        echo "  Added/Updated ${key_to_add}."

        # Update map and index for next iteration
        current_keys_map+=("${key_to_add}=${input_value}")
        key_index=$next_index
    done
}


# --- Environment Detection ---
# (Same detection logic as before)
echo "Detecting operating system and package manager..."
OS_TYPE="unknown"; PKG_MANAGER=""; UPDATE_CMD=""; INSTALL_CMD=""; BUILD_DEPS_LIST=(); PYTHON_DEV_PKG=""; LIBSODIUM_DEV_PKG=""; RUST_PKGS=()
if [[ -n "$PREFIX" ]] && command -v pkg &> /dev/null; then OS_TYPE="Termux"; PKG_MANAGER="pkg"; UPDATE_CMD="pkg update && pkg upgrade -y"; INSTALL_CMD="pkg install -y"; BUILD_DEPS_LIST=(clang make binutils pkg-config); PYTHON_DEV_PKG=""; LIBSODIUM_DEV_PKG="libsodium"; RUST_PKGS=(rust); echo "Detected Termux.";
elif command -v apt-get &> /dev/null && [[ -f /etc/debian_version ]]; then OS_TYPE="Debian/Ubuntu"; PKG_MANAGER="apt-get"; UPDATE_CMD="sudo apt-get update && sudo apt-get upgrade -y"; INSTALL_CMD="sudo apt-get install -y"; BUILD_DEPS_LIST=(build-essential pkg-config); PYTHON_DEV_PKG="python3-dev"; LIBSODIUM_DEV_PKG="libsodium-dev"; RUST_PKGS=(rustc cargo); echo "Detected Debian/Ubuntu based system.";
elif command -v dnf &> /dev/null || command -v yum &> /dev/null; then OS_TYPE="Fedora/RHEL"; if command -v dnf &> /dev/null; then PKG_MANAGER="dnf"; UPDATE_CMD="sudo dnf check-update && sudo dnf upgrade -y"; INSTALL_CMD="sudo dnf install -y"; BUILD_DEPS_LIST=(make gcc gcc-c++ kernel-devel pkg-config); else PKG_MANAGER="yum"; UPDATE_CMD="sudo yum check-update && sudo yum upgrade -y"; INSTALL_CMD="sudo yum install -y"; BUILD_DEPS_LIST=(make gcc gcc-c++ kernel-devel pkgconfig); fi; PYTHON_DEV_PKG="python3-devel"; LIBSODIUM_DEV_PKG="libsodium-devel"; RUST_PKGS=(rust cargo); echo "Detected Fedora/RHEL based system (using $PKG_MANAGER).";
elif [[ "$(uname -s)" == "Darwin" ]]; then OS_TYPE="macOS"; if command -v brew &> /dev/null; then PKG_MANAGER="brew"; UPDATE_CMD="brew update && brew upgrade"; INSTALL_CMD="brew install"; BUILD_DEPS_LIST=(pkg-config); PYTHON_DEV_PKG=""; LIBSODIUM_DEV_PKG="libsodium"; RUST_PKGS=(rust); echo "Detected macOS. Using Homebrew."; else echo "Detected macOS, but Homebrew (brew) not found."; PKG_MANAGER="manual"; fi;
else echo "Could not reliably determine OS/Package Manager."; OS_TYPE="unknown Linux/Other"; PKG_MANAGER="manual"; fi

# --- Install System Dependencies ---
# (Same installation logic as before)
if [[ "$PKG_MANAGER" != "manual" && "$PKG_MANAGER" != "" ]]; then
    echo ""; echo "--- Installing System Dependencies ---";
    echo "Updating package lists..."
    eval "$UPDATE_CMD" || echo "WARNING: Failed to update package lists."
    PACKAGES_TO_INSTALL=()
    PACKAGES_TO_INSTALL+=("${BUILD_DEPS_LIST[@]}")
    if [[ -n "$PYTHON_DEV_PKG" ]]; then PACKAGES_TO_INSTALL+=("$PYTHON_DEV_PKG"); fi
    if [[ -n "$LIBSODIUM_DEV_PKG" ]]; then PACKAGES_TO_INSTALL+=("$LIBSODIUM_DEV_PKG"); fi
    PACKAGES_TO_INSTALL+=("${RUST_PKGS[@]}")
    PACKAGES_TO_INSTALL=($(echo "${PACKAGES_TO_INSTALL[@]}" | tr ' ' '\n' | sort -u | tr '\n' ' '))
    if [ ${#PACKAGES_TO_INSTALL[@]} -gt 0 ]; then
        echo "Installing: ${PACKAGES_TO_INSTALL[@]}"
        if ! $INSTALL_CMD "${PACKAGES_TO_INSTALL[@]}"; then echo "ERROR: Failed to install one or more system dependencies."; exit 1; fi
        echo "System dependencies installed via $PKG_MANAGER."
    else echo "No specific system dependencies identified for automatic installation for $OS_TYPE."; fi
    if ! command -v rustc &> /dev/null; then echo "WARNING: Rust compiler (rustc) still not found."; fi
    if command -v pkg-config &> /dev/null && [[ -n "$LIBSODIUM_DEV_PKG" ]]; then if ! pkg-config --exists libsodium; then echo "WARNING: pkg-config cannot find libsodium."; fi; fi
elif [[ "$PKG_MANAGER" == "manual" ]]; then
    echo ""; echo "--- Manual System Dependencies Required ---";
    echo "Skipping automatic system dependency installation."
    echo "Please ensure required build tools, headers, libsodium, and Rust are installed manually."
fi

# --- Setup/Update .env File ---
echo ""
echo "--- Configuring .env File ---"
if [ ! -f "$ENV_FILE" ]; then
    echo "'.env' file not found. Copying from '$ENV_EXAMPLE_FILE'..."
    if [ -f "$ENV_EXAMPLE_FILE" ]; then
        cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
        echo "Created '.env' file."
    else
        echo "ERROR: '$ENV_EXAMPLE_FILE' not found. Cannot create '.env'."
        echo "Please create '.env' manually and add necessary API keys."
        exit 1
    fi
fi

echo "Reading current values from '$ENV_FILE' and prompting for updates..."
echo "(Press Enter to keep the current value or skip optional settings)."

# Use associative array to store current values (requires Bash 4+)
declare -A current_env_vars
while IFS='=' read -r key value || [[ -n "$key" ]]; do
    # Trim potential leading/trailing whitespace from key and value
    key=$(echo "$key" | xargs)
    # Skip empty lines or comment lines
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    # Handle values that might contain spaces or quotes (remove potential quotes for simple values)
    value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
    current_env_vars["$key"]="$value"
done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=.*$' "$ENV_FILE" 2>/dev/null || true) # Get uncommented KEY=VALUE lines

# --- Prompt for Keys and Settings ---
# Provider Keys (Multi-Key Support)
prompt_multi_key "OPENAI" "OpenAI API Key"
prompt_multi_key "OPENROUTER" "OpenRouter API Key"
# Add calls to prompt_multi_key for other providers like ANTHROPIC if needed

# Tool Keys (Single Keys)
prompt_and_update "TAVILY_API_KEY" "Tavily Search API Key (optional, for WebSearchTool)" "${current_env_vars[TAVILY_API_KEY]}" true
prompt_and_update "GITHUB_ACCESS_TOKEN" "GitHub PAT (optional, for GitHubTool)" "${current_env_vars[GITHUB_ACCESS_TOKEN]}" true

# Optional Settings with Defaults
prompt_and_update "MODEL_TIER" "Model filtering ('FREE' or 'ALL')" "${current_env_vars[MODEL_TIER]:-FREE}" false
prompt_and_update "OLLAMA_BASE_URL" "Ollama Base URL (optional, e.g., http://host:port)" "${current_env_vars[OLLAMA_BASE_URL]}" false
prompt_and_update "LITELLM_BASE_URL" "LiteLLM Base URL (optional, e.g., http://host:port)" "${current_env_vars[LITELLM_BASE_URL]}" false
prompt_and_update "OPENROUTER_REFERER" "OpenRouter Referer URL (optional)" "${current_env_vars[OPENROUTER_REFERER]}" false
prompt_and_update "USE_OLLAMA_PROXY" "Use Ollama Proxy? ('true' or 'false')" "${current_env_vars[USE_OLLAMA_PROXY]:-false}" false
prompt_and_update "OLLAMA_PROXY_PORT" "Ollama Proxy Port (if used)" "${current_env_vars[OLLAMA_PROXY_PORT]:-3000}" false
prompt_and_update "PROJECTS_BASE_DIR" "Directory for projects/sessions (optional)" "${current_env_vars[PROJECTS_BASE_DIR]}" false

echo "--- .env Configuration Updated ---"


# --- Python Virtual Environment Setup ---
echo ""
echo "--- Setting up Python Virtual Environment ---"
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment '$VENV_DIR' already exists."
else
    echo "Creating Python virtual environment in '$VENV_DIR'..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then echo "ERROR: Failed to create virtual environment."; exit 1; fi
    echo "Virtual environment created."
fi

# --- Activate Virtual Environment ---
echo "Activating virtual environment for dependency installation..."
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then echo "ERROR: Failed to activate virtual environment."; exit 1; fi

# --- Install Python Dependencies ---
echo "Installing/Upgrading pip..."
pip install --upgrade pip
echo "Installing Python dependencies from requirements.txt..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then echo "ERROR: Failed to install Python dependencies."; exit 1; fi
echo "Python dependencies installed successfully."

# --- Setup Ollama Proxy Dependencies (Optional) ---
PROXY_DIR="ollama-proxy"
if [ -d "$PROXY_DIR" ]; then
    if command -v npm &> /dev/null; then
        echo "Found ollama-proxy directory and npm. Installing proxy dependencies..."
        (cd "$PROXY_DIR" && npm install)
        if [ $? -ne 0 ]; then echo "WARNING: Failed to install ollama-proxy dependencies."; else echo "Ollama proxy dependencies installed."; fi
    else echo "INFO: ollama-proxy directory found, but 'npm' command not found."; fi
else echo "INFO: ollama-proxy directory not found."; fi

# --- Final Instructions ---
echo ""
echo "--- Setup Complete ---"
echo "To run the application:"
echo "1. Activate the virtual environment: source $VENV_DIR/bin/activate"
echo "2. Run the main script: python -m src.main"
echo "----------------------"

exit 0
