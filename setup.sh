#!/bin/bash

# TrippleEffect Setup Script
echo "--- TrippleEffect Setup ---"

# Define the Python virtual environment directory
VENV_DIR=".venv"

# --- OS Detection ---
OS_TYPE="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS_TYPE="linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS_TYPE="macos"
elif [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "windows" ]] || [[ "$OSTYPE" == "freebsd"* ]]; then
    OS_TYPE="windows_or_other"
fi
echo "Detected OS Type: $OS_TYPE"

# Check if Python 3 is available
if ! command -v python3 &> /dev/null
then
    echo "Python 3 could not be found. Please install Python 3.9+."
    exit 1
fi
echo "Python 3 found."

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment in $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "Failed to create virtual environment. Please check your Python 3 installation."
        exit 1
    fi
    echo "Virtual environment created."
else
    echo "Virtual environment '$VENV_DIR' already exists."
fi

# Activate virtual environment
echo "Activating virtual environment..."
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "Failed to activate virtual environment."
    exit 1
fi
echo "Virtual environment activated."

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip
if [ $? -ne 0 ]; then
    echo "Failed to upgrade pip."
fi

# Install dependencies from requirements.txt
if [ -f "requirements.txt" ]; then
    echo "Installing dependencies from requirements.txt..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "Failed to install dependencies. Please check requirements.txt and your internet connection."
        exit 1
    fi
    echo "Dependencies installed successfully."
else
    echo "requirements.txt not found. Skipping dependency installation."
fi

# --- Taskwarrior CLI Check and Installation Prompt ---
echo "Checking for Taskwarrior command-line tool ('task')..."
if ! command -v task &> /dev/null
then
    echo ""
    echo "---------------------------------------------------------------------"
    echo "🔴 WARNING: Taskwarrior command-line tool ('task') not found."
    echo "   The ProjectManagementTool in TrippleEffect relies on Taskwarrior."
    echo "---------------------------------------------------------------------"
    echo ""

    INSTALL_TASKWARRIOR=false
    INSTALL_CMD=""

    if [ "$OS_TYPE" == "linux" ]; then
        if command -v apt-get &> /dev/null; then
            INSTALL_CMD="sudo apt-get update && sudo apt-get install -y taskwarrior"
        elif command -v dnf &> /dev/null; then
            INSTALL_CMD="sudo dnf install -y task"
        elif command -v yum &> /dev/null; then
            INSTALL_CMD="sudo yum install -y task"
        elif command -v pacman &> /dev/null; then
            INSTALL_CMD="sudo pacman -S --noconfirm task"
        else
            echo "Could not detect common Linux package manager (apt, dnf, yum, pacman)."
            echo "Please install Taskwarrior manually. Visit: https://taskwarrior.org/download/"
        fi
    elif [ "$OS_TYPE" == "macos" ]; then
        if command -v brew &> /dev/null; then
            INSTALL_CMD="brew install taskwarrior"
        else
            echo "Homebrew (brew) not found. It's the recommended way to install Taskwarrior on macOS."
            echo "Please install Homebrew (see https://brew.sh/) and then run: brew install taskwarrior"
            echo "Alternatively, visit: https://taskwarrior.org/download/"
        fi
    elif [ "$OS_TYPE" == "windows_or_other" ]; then
        echo "Automatic installation of Taskwarrior is not supported on this OS ($OSTYPE) by this script."
        echo "If you are using Windows:"
        echo "  - Consider using WSL (Windows Subsystem for Linux) and installing Taskwarrior via its Linux distribution's package manager."
        echo "  - Or, find Windows installation instructions at: https://taskwarrior.org/download/"
        echo "For other systems, please visit: https://taskwarrior.org/download/"
    fi

    if [ -n "$INSTALL_CMD" ]; then
        read -r -p "Do you want this script to attempt to install Taskwarrior using: '$INSTALL_CMD'? (y/N): " response
        if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
            echo "Attempting to install Taskwarrior..."
            eval "$INSTALL_CMD"
            if [ $? -ne 0 ]; then
                echo "🔴 Taskwarrior installation attempt FAILED. Please install it manually."
                echo "Visit: https://taskwarrior.org/download/"
            else
                echo "✅ Taskwarrior installation attempt finished. Please verify it was successful."
                if command -v task &> /dev/null; then
                    echo "✅ Taskwarrior command ('task') is now available."
                else
                    echo "🔴 Taskwarrior command ('task') is still not available after installation attempt."
                    echo "   Please check the output above for errors or try manual installation."
                fi
            fi
        else
            echo "Skipping automatic Taskwarrior installation."
            echo "Please install Taskwarrior manually if you intend to use project management features."
            echo "Visit: https://taskwarrior.org/download/"
        fi
    fi
    echo "---------------------------------------------------------------------"
    echo ""
else
    echo "✅ Taskwarrior command-line tool found."
fi
# --- End Taskwarrior Check ---

# --- .env File and API Key Setup ---
ENV_FILE=".env"
ENV_EXAMPLE_FILE=".env.example"
ENV_CREATED_NOW=false

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE_FILE" ]; then
        echo "Creating $ENV_FILE file from $ENV_EXAMPLE_FILE..."
        cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
        echo "$ENV_FILE file created."
        ENV_CREATED_NOW=true
    else
        echo "Warning: $ENV_EXAMPLE_FILE not found. Cannot create $ENV_FILE file automatically."
        touch "$ENV_FILE"
        echo "Created an empty $ENV_FILE. You will need to populate it fully."
        ENV_CREATED_NOW=true
    fi
else
    echo "$ENV_FILE file already exists."
fi

echo ""
echo "--- API Key Configuration ---"
echo "This script will guide you through setting API keys in your $ENV_FILE."
echo "You can skip any prompt by pressing Enter if you don't have/use that key."
echo ""
echo "⚠️ IMPORTANT: API keys entered here will be visible on your screen and"
echo "   may be stored in your shell's history. For maximum security, you might"
echo "   prefer to edit the $ENV_FILE file directly in a text editor after this script."
echo ""

# Function to append or update/replace a key in .env
# If key exists, it comments out the old one(s) and adds the new one.
# If key does not exist, it appends it.
# Ensures the new key is not commented out.
update_env_var() {
    local key_name="$1"
    local key_value="$2"
    local env_file="$3"
    local tmp_file

    tmp_file=$(mktemp)
    if [ ! -f "$tmp_file" ]; then
        echo "Error: Failed to create temporary file for updating .env. Aborting update for $key_name."
        return 1
    fi

    # First, comment out any existing lines for this key (commented or not)
    # This regex matches lines starting with optional whitespace, optional #, optional whitespace, key_name=
    # We use a character class for sed's BRE/ERE differences with +, *
    # sed "s/^\([\t ]*\)\(#*\)\([\t ]*\)${key_name}=.*/#&/" "$env_file" > "$tmp_file"
    # A simpler approach: remove existing lines and append the new one.
    # However, to preserve comments and structure, commenting out is better.
    # Let's try with awk for more robust line matching and commenting
    awk -v key="$key_name" -v new_val="$key_name=\"$key_value\"" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*#?[[:space:]]*" key "=" {
        if (!found) {
            print "# Previous entry for " key " (commented out by setup.sh):"
        }
        print "#" $0
        found=1
        next
    }
    { print }
    END {
        if (!found) {
            print new_val
        }
    }
    ' "$env_file" > "$tmp_file"
    # If awk modified something or if the key was not found (and thus will be added by the END block if value is provided)
    # we still need to ensure the non-found key gets added if a value was provided.
    # The awk script above doesn't add the new value if 'found' was true.
    # Let's refine: awk will comment. Then we add the new line.

    # Comment out existing lines
    awk -v key="$key_name" '
    $0 ~ "^[[:space:]]*#?[[:space:]]*" key "=" {
        print "#" $0
        next
    }
    { print }
    ' "$env_file" > "$tmp_file"
    mv "$tmp_file" "$env_file"

    # Append the new (or updated) key if a value was provided
    if [ -n "$key_value" ]; then
        # Ensure value is quoted if it's not already and doesn't contain quotes
        if [[ "$key_value" != \"*\" && "$key_value" != \'*\' && "$key_value" != *[\"\']* ]]; then
            echo "${key_name}=\"${key_value}\"" >> "$env_file"
        else
            echo "${key_name}=${key_value}" >> "$env_file" # Assume user quoted correctly or key is simple
        fi
        echo "Set ${key_name} in $env_file."
    elif grep -q -E "^\s*#?\s*${key_name}=" "$env_file"; then
        # If value is empty but key was in file, ensure it's commented if user wants to clear it.
        # The awk above should have handled this. This is a fallback.
        awk -v key="$key_name" '
        $0 ~ "^[[:space:]]*" key "=" { print "#" $0; next }
        { print }
        ' "$env_file" > "$tmp_file" && mv "$tmp_file" "$env_file"
        echo "Cleared and commented out ${key_name} in $env_file as no new value was provided."
    fi
    # Cleanup tmp file just in case it was created by mktemp but not moved.
    [ -f "$tmp_file" ] && rm -f "$tmp_file"
}


# Function to handle keys for a specific provider
configure_provider_keys() {
    local provider_display_name="$1" # e.g., "OpenAI"
    local provider_env_prefix="$2"   # e.g., "OPENAI"
    local env_file="$3"
    local num_keys
    local key_val
    local key_name_base="${provider_env_prefix}_API_KEY"

    echo ""
    echo "Configuring $provider_display_name API Keys..."
    while true; do
        read -r -p "How many $provider_display_name API keys do you want to configure (0 to skip)? " num_keys
        if [[ "$num_keys" =~ ^[0-9]+$ ]]; then
            break
        else
            echo "Invalid input. Please enter a number (e.g., 0, 1, 2)."
        fi
    done

    if [ "$num_keys" -eq 0 ]; then
        echo "Skipping $provider_display_name API key configuration."
        return
    fi

    for i in $(seq 1 "$num_keys"); do
        local key_suffix_for_prompt="_${i}"
        local current_key_name="${key_name_base}${key_suffix_for_prompt}"
        
        # If only one key, prompt without the suffix for better UX for common case, but store with _1
        # This makes the first key effectively PROVIDER_API_KEY_1
        local prompt_key_name="${provider_env_prefix} API Key ${i}"

        read -r -p "Enter $prompt_key_name: " key_val
        if [ -n "$key_val" ]; then
            update_env_var "$current_key_name" "$key_val" "$env_file"
        else
            echo "Skipped $prompt_key_name."
            # If skipped, ensure any existing entry for this specific numbered key is commented
            update_env_var "$current_key_name" "" "$env_file"
        fi
    done
}

read -r -p "Do you want to configure API keys now? (y/N): " config_choice
if [[ "$config_choice" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo ""
    configure_provider_keys "OpenAI" "OPENAI" "$ENV_FILE"
    configure_provider_keys "OpenRouter" "OPENROUTER" "$ENV_FILE"

    echo ""
    echo "Configuring other API Keys..."
    # OpenRouter Referer
    read -r -p "Enter your OpenRouter HTTP Referer (optional, e.g., http://localhost:8000/YourAppName): " openrouter_referer
    if [ -n "$openrouter_referer" ]; then
        update_env_var "OPENROUTER_REFERER" "$openrouter_referer" "$ENV_FILE"
    else
        update_env_var "OPENROUTER_REFERER" "" "$ENV_FILE" # Comment out if empty
    fi

    # GitHub PAT
    read -r -p "Enter your GitHub Personal Access Token (GITHUB_ACCESS_TOKEN) (leave blank to skip): " github_pat
    if [ -n "$github_pat" ]; then
        update_env_var "GITHUB_ACCESS_TOKEN" "$github_pat" "$ENV_FILE"
    else
        update_env_var "GITHUB_ACCESS_TOKEN" "" "$ENV_FILE"
    fi

    # Tavily API Key
    read -r -p "Enter your Tavily API Key (TAVILY_API_KEY) (leave blank to skip): " tavily_key
    if [ -n "$tavily_key" ]; then
        update_env_var "TAVILY_API_KEY" "$tavily_key" "$ENV_FILE"
    else
        update_env_var "TAVILY_API_KEY" "" "$ENV_FILE"
    fi

    echo ""
    echo "API key configuration finished."
    echo "Please review the $ENV_FILE file for correctness and add any other settings manually."
else
    if [ "$ENV_CREATED_NOW" = true ]; then
        echo "Skipping interactive API key setup. Please edit the $ENV_FILE file manually."
    else
        echo "Skipping interactive API key setup. Remember to check your existing $ENV_FILE."
    fi
fi
echo "---------------------------------"
# --- End .env File and API Key Setup ---


# Create data and logs directories if they don't exist
echo "Ensuring data and logs directories exist..."
mkdir -p data
mkdir -p logs
echo "Data and logs directories ensured."

echo ""
echo "--- Setup Complete ---"
echo "To run the application:"
echo "1. (If not already active) Activate the virtual environment: source $VENV_DIR/bin/activate"
echo "2. CRITICAL: Ensure your $ENV_FILE file is correctly populated with API keys."
echo "3. Run the application: ./run.sh OR python -m src.main"
echo "4. Access the UI at http://localhost:8000"