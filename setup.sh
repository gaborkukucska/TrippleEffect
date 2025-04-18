# --- START OF FILE TrippleEffect-main/setup.sh ---
#!/bin/bash

# Setup script for TrippleEffect Framework
# Creates directories, virtual environment, installs dependencies,
# and guides through .env configuration.

echo "--- Starting TrippleEffect Setup ---"

# --- Helper Functions ---
check_command() {
    if ! command -v $1 &> /dev/null; then
        echo "Error: Required command '$1' not found. Please install it."
        exit 1
    fi
}

create_default_config_yaml() {
    echo "Creating default config.yaml..."
    cat << EOF > config.yaml
# START OF FILE config.yaml
# Configuration for TrippleEffect Bootstrap Agents.

# This file defines essential bootstrap agents (like Admin AI).
# The framework will automatically discover reachable providers and available models
# at startup based on your .env configuration and the MODEL_TIER setting.

# --- Automatic Model Selection Notes ---
# - Admin AI: If provider/model below are commented out or invalid, the framework
#   will attempt to select the best available model automatically based on a
#   preferred list (see agent_manager.py) and discovered models. Check startup
#   logs to see which model was selected.
# - Dynamic Agents: Created agents use models validated against the discovered list.

# --- Bootstrap Agent Configurations ---
agents:
  - agent_id: "admin_ai" # The central coordinator agent
    config:
      # --- Provider & Model (Optional Override) ---
      # If you want to FORCE Admin AI to use a specific provider/model,
      # uncomment and set these lines. Ensure the provider is configured
      # in .env and the model is available/discoverable.
      # Otherwise, leave commented out for automatic selection.
      # provider: "openrouter"
      # model: "google/gemini-flash-1.5:free"

      # --- Admin AI Persona & Instructions ---
      # Define the high-level goal/persona here.
      # The detailed operational workflow, tool usage, and the list of *actually available*
      # models will be automatically injected into the prompt by the framework
      # (loading instructions from prompts.json).
      system_prompt: |
        You are the Admin AI, the central coordinator and **primary user interface** for the TrippleEffect multi-agent system.
        Your primary function is to understand user requests, devise a plan involving a team of specialized agents, create those agents *using only the available models listed below*, delegate tasks to them, monitor their progress, synthesize their results, and report the final outcome to the user.
        You are the orchestrator and project manager. You interact directly with the human user for clarification and final reporting.
        Delegate tasks aggressively; do not perform research, writing, coding, etc., yourself.
        Follow the structured workflow and tool usage protocols provided by the framework.
      temperature: 0.6 # Balanced temperature for planning and control
      persona: "Admin AI (@admin_ai)" # Display name

  # --- Add more bootstrap agents here if needed ---
EOF
}

create_default_prompts_json() {
    echo "Creating default prompts.json..."
    cat << 'EOF' > prompts.json
{
  "standard_framework_instructions": "\n\n--- Standard Tool & Communication Protocol ---\nYour Agent ID: `{agent_id}`\nYour Assigned Team ID: `{team_id}`\n\n**Context Awareness:** Before using tools (like web_search or asking teammates), carefully review the information already provided in your system prompt, the current conversation history, and any content included in the message assigning your task. Use the available information first.\n\n**Tool Usage:** You have access to the following tools. Use the specified XML format precisely. Only use ONE tool call per response message, placed at the very end.\n\n{tool_descriptions_xml}\n\n**Communication:**\n- Use the `<send_message>` tool to communicate ONLY with other agents *within your team* or the Admin AI (`admin_ai`).\n- **CRITICAL:** Specify the exact `target_agent_id` (e.g., `agent_17..._xyz` or `admin_ai`). **DO NOT use agent personas (like 'Researcher') as the target_agent_id.** Use the IDs provided in team lists or feedback messages.\n- Respond to messages directed to you ([From @...]).\n- **MANDATORY FINAL STEP & STOP:** After completing **ALL** parts of your assigned task (including any file writing), your **VERY LAST ACTION** in that turn **MUST** be to use the `<send_message>` tool to report your completion and results (e.g., summary, analysis, confirmation of file write including filename and scope) back to the **agent who assigned you the task** (this is usually `admin_ai`, check the initial task message). **CRITICAL: AFTER sending this final confirmation message, YOU MUST STOP. Do NOT output any further text, reasoning, or tool calls in that response or subsequent turns unless you receive a NEW instruction or question.**\n\n**File System:**\n- Use the `<file_system>` tool with the appropriate `scope` ('private' or 'shared') as instructed by the Admin AI. The `scope` determines where the file operation takes place.\n- **`scope: private`**: Your personal sandbox. Use this for temporary files or work specific only to you. Path is relative to your agent's private directory.\n- **`scope: shared`**: The shared workspace for the current project/session. Use this if the file needs to be accessed by other agents or the user. Path is relative to the session's shared directory.\n- All paths provided (e.g., in `filename` or `path`) MUST be relative within the specified scope.\n- If you write a file, you **must** still perform the **MANDATORY FINAL STEP & STOP** described above (using `send_message`) to report completion, the filename/path, and **the scope used** (`private` or `shared`) back to the requester.\n\n**Task Management:**\n- If you receive a complex task, break it down logically. Execute the steps sequentially. Report progress clearly on significant sub-steps or if you encounter issues using `send_message`. Remember the **MANDATORY FINAL STEP & STOP** upon full task completion.\n--- End Standard Protocol ---\n",
  "admin_ai_operational_instructions": "\n\n--- Admin AI Core Operational Workflow ---\n**Your Identity:**\n*   Your Agent ID: `admin_ai`\n*   Your Assigned Team ID: `N/A` (You manage teams, you aren't assigned to one)\n\n**Your CORE FUNCTION is to ORCHESTRATE and DELEGATE, not perform tasks directly.**\n**You should PRIMARILY use `ManageTeamTool` and `send_message`. Avoid using other tools like `github_tool`, `web_search`, or `file_system` yourself unless absolutely necessary.**\n\n**Mandatory Workflow:**\n\n1.  **Analyze User Request:** Understand the goal. Ask clarifying questions if needed.\n1.5 **Answer Direct Questions:** Offer to create a team for complex tasks. Do not perform these tasks yourself.\n2.  **Plan Agent Team & Initial Tasks:** Determine roles, specific instructions for each agent, and team structure. **Delegate aggressively.**\n    *   **File Saving Scope Planning:** Explicitly decide if final output files should be `private` or `shared`. Instruct the worker agent *in its system_prompt* to use the correct `scope` with the `file_system` tool.\n3.  **Execute Structured Delegation Plan:** Follow precisely:\n    *   **(a) Check State (Optional):** Use `ManageTeamTool` (`list_teams`, `list_agents`).\n    *   **(b) Create Team(s):** Use `ManageTeamTool` (`action: create_team`, providing `team_id`).\n    *   **(c) Create Agents Sequentially:** Use `ManageTeamTool` (`action: create_agent`). Specify `provider`, `model`, `persona`, a **detailed role-specific `system_prompt`**, and the `team_id`. **Wait** for the feedback message containing `created_agent_id`. **Store this exact ID.**\n    *   **(d) Kick-off Tasks:** Use `send_message` targeting the **exact `created_agent_id` you received in the feedback from step (c).** Reiterate the core task and the requirement to report back to `admin_ai` via `send_message` upon completion. **Do not guess or reuse IDs from previous steps.**\n4.  **Coordinate & Monitor:**\n    *   Monitor incoming messages for agent progress reports and final completion confirmations sent via `send_message`.\n    *   **DO NOT perform the agents' tasks yourself.** Wait for the designated agent to perform the action and report the results back to you via `send_message`.\n    *   If an agent reports saving a file, ask them for the content *and the scope* via `send_message`. Only use *your* `file_system` tool as a last resort.\n    *   Relay necessary information between agents *only if required* using `send_message`.\n    *   Provide clarification via `send_message` if agents get stuck.\n    *   **DO NOT proceed** to synthesis or cleanup until you have received confirmation messages (via `send_message`) from **ALL** required agents.\n5.  **Synthesize & Report to User:** **ONLY AFTER** confirming all tasks are complete, compile the results reported by the agents. Present the final answer, stating where files were saved.\n6.  **Wait User Feedback:** Wait for the user to check the quality of the finished work and give you their feedback.\n7.  **IF Clean Up is Requested:** IF the user requests a Clean Up and **ONLY AFTER** delivering the final result:\n    *   **(a) Identify Agents:** Use `ManageTeamTool` with `action: list_agents` **immediately before deletion** to get the **current list and exact `agent_id` values**.\n    *   **(b) Delete Agents:** Delete **each dynamic agent individually** using `ManageTeamTool` with `action: delete_agent` and the **specific `agent_id` obtained in step (a).**\n    *   **(c) Delete Team(s):** **AFTER** confirming **ALL** agents in a team are deleted, delete the team using `ManageTeamTool` with `action: delete_team` and the correct `team_id`.\n\n--- Available Tools (For YOUR Use as Admin AI) ---\nUse the specified XML format precisely. Only use ONE tool call per response message, placed at the very end.\nYour primary tools are `ManageTeamTool` and `send_message`.\n\n{tool_descriptions_xml}\n--- End Available Tools ---\n\n**Tool Usage Reminders:**\n*   Use exact `agent_id`s (obtained from `list_agents` or creation feedback) for `send_message` and **especially for `delete_agent`**. Double-check IDs before use.\n*   Instruct worker agents clearly on which tools *they* should use and what file `scope` (`private` or `shared`) to use.\n--- End Admin AI Core Operational Workflow ---\n",
  "default_system_prompt": "You are a helpful assistant.",
  "default_agent_persona": "Assistant Agent"
}
EOF
}

configure_env_file() {
    echo "--- Configuring .env file ---"
    # Clear existing file content or create new
    > .env
    echo "# TrippleEffect Environment Variables" >> .env
    echo "# Generated by setup.sh on $(date)" >> .env
    echo "" >> .env

    # --- Local Provider URLs ---
    echo "# --- Local LLM Provider URLs (Optional) ---" >> .env
    echo "# Leave blank to enable automatic discovery (localhost/network checks)" >> .env
    read -p "Do you want to specify a Base URL for Ollama? (e.g., http://192.168.1.X:11434) [y/N]: " specify_ollama_url
    if [[ "$specify_ollama_url" =~ ^[Yy]$ ]]; then
        read -p "Enter Ollama Base URL: " ollama_url
        echo "OLLAMA_BASE_URL=${ollama_url}" >> .env
    else
        echo "OLLAMA_BASE_URL=" >> .env
        echo "(Ollama URL left blank for auto-discovery)"
    fi
    read -p "Do you want to specify a Base URL for LiteLLM? (e.g., http://192.168.1.X:4000) [y/N]: " specify_litellm_url
    if [[ "$specify_litellm_url" =~ ^[Yy]$ ]]; then
        read -p "Enter LiteLLM Base URL: " litellm_url
        echo "LITELLM_BASE_URL=${litellm_url}" >> .env
    else
        echo "LITELLM_BASE_URL=" >> .env
        echo "(LiteLLM URL left blank for auto-discovery)"
    fi
    echo "" >> .env

    # --- Remote Provider API Keys (Multi-key support) ---
    echo "# --- Remote LLM Provider API Keys (Add multiple if needed) ---" >> .env
    PROVIDERS=("OPENROUTER" "OPENAI" "ANTHROPIC" "GOOGLE" "DEEPSEEK") # Add more as needed

    for provider in "${PROVIDERS[@]}"; do
        while true; do
            read -p "How many API keys do you want to add for ${provider}? (Enter 0 to skip): " num_keys
            if [[ "$num_keys" =~ ^[0-9]+$ ]]; then
                break
            else
                echo "Invalid input. Please enter a number (0 or more)."
            fi
        done

        if [ "$num_keys" -gt 0 ]; then
            echo "# ${provider} Keys" >> .env
            for i in $(seq 1 $num_keys); do
                 # Loop until a non-empty key is entered
                while true; do
                    read -s -p "Enter ${provider} API Key #${i}: " api_key # -s for silent input
                    echo # Add a newline after silent input
                    if [ -n "$api_key" ]; then
                        echo "${provider}_API_KEY_${i}=${api_key}" >> .env
                        break
                    else
                        echo "API key cannot be empty. Please try again."
                    fi
                done
            done
            echo "" >> .env
        else
             echo "# No keys configured for ${provider}" >> .env
             echo "" >> .env
        fi
    done

    # --- Other Optional URLs/Tokens ---
    echo "# --- Other Optional Settings ---" >> .env
    # OpenRouter specific (Referer is optional but recommended)
    read -p "Enter OpenRouter Referer URL (Optional, e.g., your project URL or leave blank): " openrouter_referer
    echo "OPENROUTER_REFERER=${openrouter_referer}" >> .env

    # GitHub Token
    read -p "Enter GitHub Access Token (Optional, needed for GitHub tool): " github_token
    echo "GITHUB_ACCESS_TOKEN=${github_token}" >> .env
    echo "" >> .env

    # --- Model Tier ---
    echo "# --- Model Filtering ---" >> .env
    while true; do
        read -p "Select MODEL_TIER (FREE / ALL) [Default: ALL]: " model_tier_input
        model_tier_upper=$(echo "$model_tier_input" | tr '[:lower:]' '[:upper:]') # Convert to uppercase
        if [ -z "$model_tier_upper" ]; then
            model_tier_upper="ALL" # Default if empty
        fi
        if [[ "$model_tier_upper" == "FREE" || "$model_tier_upper" == "ALL" ]]; then
            echo "MODEL_TIER=${model_tier_upper}" >> .env
            break
        else
            echo "Invalid input. Please enter FREE or ALL."
        fi
    done
    echo "" >> .env

    echo ".env file configured."
}


# --- Main Setup Logic ---

# 1. Check Prerequisites
echo "Checking prerequisites..."
check_command python3
check_command pip

# 2. Create Directories
echo "Creating required directories..."
mkdir -p logs data projects sandboxes helperfiles src/ui src/utils
echo "Directories created."

# 3. Setup Virtual Environment
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment in '$VENV_DIR'..."
    python3 -m venv $VENV_DIR
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment."
        exit 1
    fi
    echo "Virtual environment created."
else
    echo "Virtual environment '$VENV_DIR' already exists."
fi

# Determine activation command based on OS (best guess)
ACTIVATE_CMD="source $VENV_DIR/bin/activate"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    ACTIVATE_CMD="$VENV_DIR\\Scripts\\activate"
fi
echo "-----------------------------------------------------"
echo "IMPORTANT: Activate the virtual environment in your current shell:"
echo "  $ACTIVATE_CMD"
echo "Then run the application using: python -m src.main"
echo "-----------------------------------------------------"
# Cannot activate within the script for the parent shell, user must do it.

# 4. Install Dependencies (using the venv pip)
echo "Installing dependencies from requirements.txt..."
"$VENV_DIR/bin/pip" install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "Error: Failed to install dependencies. Make sure '$ACTIVATE_CMD' was run or try running pip install manually."
    # Don't exit, maybe user can fix manually
else
    echo "Dependencies installed successfully."
fi


# 5. Handle .env file
if [ -f ".env" ]; then
    read -p ".env file already exists. Overwrite with new configuration? (Choosing 'n' keeps the existing file) [y/N]: " overwrite_env
    if [[ "$overwrite_env" =~ ^[Yy]$ ]]; then
        configure_env_file
    else
        echo "Keeping existing .env file."
    fi
else
    if [ -f ".env.example" ]; then
        echo ".env file not found. Copying from .env.example and configuring..."
        cp .env.example .env
        configure_env_file
    else
        echo ".env file not found and no .env.example exists. Creating new .env file..."
        configure_env_file
    fi
fi

# 6. Handle config.yaml
if [ ! -f "config.yaml" ]; then
    create_default_config_yaml
else
    echo "config.yaml already exists."
fi

# 7. Handle prompts.json
if [ ! -f "prompts.json" ]; then
    create_default_prompts_json
else
    echo "prompts.json already exists."
fi

# --- Setup Ollama Proxy Dependencies ---
echo ""
echo "Setting up Ollama proxy dependencies (if directory exists)..."
if [ -d "ollama-proxy" ] && [ -f "ollama-proxy/package.json" ]; then
  echo "Running 'npm install' in ollama-proxy directory..."
  (cd ollama-proxy && npm install)
  if [ $? -ne 0 ]; then
      echo "Warning: 'npm install' for ollama-proxy failed. The proxy might not function correctly."
  else
      echo "Ollama proxy dependencies installed."
  fi
else
  echo "Skipping Ollama proxy setup: 'ollama-proxy' directory or 'package.json' not found."
fi
echo ""
# --- End Ollama Proxy Setup ---

echo "--- TrippleEffect Setup Complete ---"
echo "Remember to activate the virtual environment before running:"
echo "  $ACTIVATE_CMD"
echo "Then start the application:"
echo "  python -m src.main"

exit 0
