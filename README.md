<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**Version:** 2.15 (Phase 15: Prompt Centralization Completed)

*This framework is primarily developed and iterated upon by Large Language Models (LLMs) like Google's Gemini series, guided by human oversight provided by Gabby* 🤓👋

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI.

It features **dynamic discovery of reachable LLM providers** (local Ollama/LiteLLM first via env/localhost/network check, then configured public APIs), their **available models**, and management of **multiple API keys per provider**. It automatically attempts **key cycling** upon authentication/rate-limit style errors for remote providers. If a key encounters persistent errors, it's **temporarily quarantined** (state saved). It can **automatically select a suitable model for the core `Admin AI`** at startup and validates models used for dynamic agents against the discovered list, considering cost tiers (`MODEL_TIER`). Standardized agent instructions and default prompts are loaded from `prompts.json`. If an agent encounters persistent errors (like provider errors or rate limits *after* retries and key cycling), the framework **automatically attempts to failover** to other available models based on a preference hierarchy (Local -> External Free -> External Paid), up to a configurable limit, before marking the agent as errored for that task. Performance metrics (success/failure counts, duration) are tracked per model and saved.

## 🚀 Quick Start: Setup & Run

For a quick setup, use the provided scripts (requires bash environment, Python 3.9+, pip, and git):

1.  **Clone:** `git clone https://github.com/gaborkukucska/TrippleEffect.git`
2.  **Navigate:** `cd TrippleEffect`
3.  **Run Setup Script:**
    ```bash
    chmod +x setup.sh
    ./setup.sh
    ```
    *   This creates directories, sets up a Python virtual environment (`.venv/`), installs dependencies, and guides you through configuring API keys and settings in a `.env` file. Follow the prompts carefully.
4.  **Run Application Script:**
    ```bash
    chmod +x run.sh
    ./run.sh
    ```
    *   This activates the virtual environment and starts the FastAPI server.
    *   Access the web UI in your browser, typically at `http://localhost:8000` (or your machine's IP if running remotely/on Termux). Watch the terminal for logs.
    *   **Note on Ollama:** If using a local Ollama instance, especially over the network (e.g., from Termux), you might encounter `ClientConnectionError: Connection closed` during streaming. This seems environment-related. Check Ollama server logs and network configuration if issues persist.

*(See detailed manual steps below if needed)*

## ⚙️ Manual Installation

Follow these steps if you prefer manual setup or encounter issues with the setup script:

1.  **Prerequisites:** Python 3.9+ installed, Git. (Optional) Local Ollama or LiteLLM service running if you want to use local models.
2.  **Clone:** `git clone https://github.com/gaborkukucska/TrippleEffect.git`
3.  **Navigate:** `cd TrippleEffect`
4.  **Create Virtual Environment:** `python -m venv .venv`
5.  **Activate Virtual Environment:**
    *   Windows: `.venv\Scripts\activate`
    *   macOS/Linux: `source .venv/bin/activate`
6.  **Install Dependencies:** `pip install -r requirements.txt`
7.  **Configure Environment (`.env`):**
    *   Copy `.env.example` to a new file named `.env`: `cp .env.example .env`
    *   Edit the `.env` file:
        *   Add your required **API keys**.
        *   For providers like OpenRouter or OpenAI where you have multiple keys, you can add them using the format `PROVIDERNAME_API_KEY_1`, `PROVIDERNAME_API_KEY_2`, etc. (See `.env.example`).
        *   Set **Base URLs** only if you need to override defaults (e.g., for proxies or local instances not on default ports). For local Ollama/LiteLLM, leave blank to use auto-discovery (localhost/network check) or set explicitly (e.g., `OLLAMA_BASE_URL=http://192.168.1.X:11434`).
        *   Set `MODEL_TIER` (`FREE` or `ALL`) to filter models from remote providers.
        *   Set `GITHUB_ACCESS_TOKEN` if you need the GitHub tool.
8.  **Review Bootstrap Config (`config.yaml`):** Usually, leave as is. Admin AI model is auto-selected. Modify only to force Admin AI model or change its base persona. Create the file with default content if it doesn't exist (see `setup.sh` for default content).
9.  **Review Default Prompts (`prompts.json`):** Review and customize the standard framework instructions or default agent prompts if desired. Create the file with default content if it doesn't exist (see `setup.sh` for default content).
10. **Create Directories (if needed):** `mkdir logs data projects sandboxes`

## ▶️ Manual Running

If using manual installation or not using `run.sh`:

1.  Ensure your virtual environment is activated (`source .venv/bin/activate` or equivalent).
2.  Run the application:
    ```bash
    python -m src.main
    ```
*   The server starts. Watch the logs for provider discovery, model filtering, Admin AI model selection, prompt loading, and any errors.
*   Access the web UI in your browser, typically at `http://localhost:8000` (or your machine's IP if running remotely/on Termux).
*   **Note on Ollama:** As mentioned in the Quick Start, Ollama streaming might be unstable in some network configurations, potentially causing `ClientConnectionError: Connection closed`. Check external factors if this occurs.

## 🎯 Core Concept

The system orchestrates multiple LLM agents. A central `Admin AI` agent analyzes user requests and devises plans, creating specialized "worker" agents dynamically in memory. Standard framework instructions and default prompts are loaded from `prompts.json` at startup.

**Key Workflow:**
1.  **Startup:**
    *   Framework checks `.env` for API keys (including `PROVIDER_API_KEY_N` format), URLs and `MODEL_TIER`.
    *   Loads `prompts.json` containing standard instructions and default prompts.
    *   Initializes `ProviderKeyManager` (loads key quarantine state).
    *   Discovers reachable local (Ollama/LiteLLM via env/localhost/network checks) and configured remote providers.
    *   Fetches available models for reachable providers, filters by `MODEL_TIER`.
    *   Automatically selects the best available, non-depleted model for `Admin AI` if not set in `config.yaml`. Logs the selection.
    *   Loads basic performance metrics from previous runs (`data/model_performance_metrics.json`).
2.  **Task Submission:** User submits task via UI 📝.
3.  **Planning & Delegation:** `Admin AI` receives task, uses knowledge of available models and loaded prompts, plans team, defines roles/prompts.
4.  **Agent Creation:** `Admin AI` uses `ManageTeamTool`. Framework validates requested model against available list and creates the agent using an available API key via `ProviderKeyManager`.
5.  **Framework Context:** Standard instructions (from loaded `prompts.json`) injected into dynamic agents.
6.  **Task Execution & Failover:** `Admin AI` delegates tasks. Agents process using their assigned model and API key.
    *   If an agent's LLM call fails with a transient error (e.g., temporary network issue, 5xx, connection closed): The `AgentCycleHandler` attempts retries with delays (up to `MAX_STREAM_RETRIES`). <!-- Updated -->
    *   If an agent's LLM call fails with a potentially key-related error (e.g., 429 rate limit, 401/403 auth error):
        *   The `ProviderKeyManager` quarantines the specific API key for 24 hours.
        *   The `AgentManager` attempts to cycle to the *next available key* for the *same provider*.
        *   If a new key is found, the agent retries the *same task* with the *same model* using the new key.
    *   If an agent's LLM call fails fatally (non-retryable error, max retries reached, or all keys for the provider are quarantined):
        *   The framework automatically triggers model/provider failover.
        *   It attempts to switch the agent to the next best available model (Local -> Free -> Paid) that hasn't already failed *for this specific task attempt sequence* and whose provider has available keys.
        *   This repeats up to `MAX_FAILOVER_ATTEMPTS`.
        *   If all failover attempts fail, the agent enters an `ERROR` state for that task.
    *   Agents use tools, communicate, and report results back to `Admin AI`.
7.  **Metric Tracking:** Success/failure and duration of each LLM call attempt (including retries and failovers) are recorded by the `ModelPerformanceTracker`.
8.  **Coordination & Synthesis:** `Admin AI` monitors progress, coordinates, synthesizes results.
9.  **Cleanup:** `Admin AI` cleans up dynamic agents/teams.
10. **Shutdown:** Performance metrics and API key quarantine states are saved.

Configuration (`config.yaml`) primarily defines `Admin AI` persona/prompt (provider/model optional). `.env` manages secrets, URLs, `MODEL_TIER`, and potentially multiple API keys per provider. `prompts.json` defines standard agent instructions. Session state is saved/loaded.

## ✨ Key Features

*   **Dynamic Multi-Agent Architecture:** Admin AI orchestration.
*   **Dynamic Provider/Model Discovery:** Auto-detects reachable providers (local check priority) & models.
*   **Multi-API Key Management:** Supports multiple keys per provider (`PROVIDER_API_KEY_N` format in `.env`).
*   **API Key Cycling:** Automatically tries next available key on auth/rate-limit errors.
*   **API Key Quarantining:** Temporarily disables keys after persistent failure (24h default, state saved).
*   **Automatic Admin AI Model Selection:** Selects best available, non-depleted model at startup.
*   **Centralized Prompts:** Standard instructions and defaults loaded from `prompts.json`. <!-- NEW -->
*   **Model Availability Validation:** Ensures dynamic agents use valid models.
*   **Automatic Retry & Failover:** Agents attempt retries for transient errors (incl. connection closed), then key cycling (if applicable), then model/provider failover respecting tiers (Local -> Free -> Paid), up to `MAX_FAILOVER_ATTEMPTS`. <!-- Updated -->
*   **Performance Tracking:** Records success/failure counts and duration per model, saved to JSON.
*   **Structured Delegation & Framework Context.**
*   **Asynchronous Backend & Real-time UI Updates.**
*   **Multi-Provider LLM Support:** Connects to discovered/configured providers.
*   **Simplified Configuration:** `config.yaml` (Admin AI model optional), `.env` (secrets, URLs, tier, multiple keys), `prompts.json` (standard instructions). <!-- Updated -->
*   **Sandboxed & Shared Workspaces.**
*   **Sequential Multi-Tool Usage.**
*   **Agent Communication.**
*   **Session Persistence.**
*   **Timestamped File Logging.**
*   **Extensible Design.**
*   **Termux Friendly.**

## 🏗️ Architecture Overview (Conceptual - Post Phase 15)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_CHAT_VIEW["Chat & Agents View ✅"]
        UI_LOGS_VIEW["System Logs View ✅"]
        UI_SESSION_VIEW["Project/Session View ✅"]
        UI_CONFIG_VIEW["Static Config Info View ✅"] %% Simplified
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend ✅"]
        WS_MANAGER["🔌 WebSocket Manager ✅"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ Uses ModelRegistry ✅<br>+ **Uses ProviderKeyManager ✅**<br>+ Auto-Selects Admin AI Model ✅<br>+ **Handles Key/Model Failover** ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"] %% Updated
        PROVIDER_KEY_MGR["🔑 Provider Key Manager <br>+ Manages Keys ✅<br>+ Handles Quarantine ✅<br>+ Saves/Loads State ✅"] %% Added
        MODEL_REGISTRY["📚 Model Registry✅"]
        PERF_TRACKER["📊 Performance Tracker<br>+ Records Metrics ✅<br>+ Saves/Loads Metrics ✅"]
        CYCLE_HANDLER["🔄 Agent Cycle Handler<br>+ Handles Retries ✅<br>+ **Triggers Key/Model Failover** ✅<br>+ Reports Metrics ✅"] %% Updated
        INTERACTION_HANDLER["🤝 Interaction Handler ✅"]
        STATE_MANAGER["📝 AgentStateManager ✅"]
        SESSION_MANAGER["💾 SessionManager ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent ✅"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers"] %% Instantiated by AGENT_MANAGER
             PROVIDER_OR["🔌 OpenRouter"]
             PROVIDER_OLLAMA["🔌 Ollama"]
             PROVIDER_OPENAI["🔌 OpenAI"]
             PROVIDER_LITELLM["🔌 LiteLLM (TBD)"]
         end

         subgraph Tools ["🛠️ Tools"] %% Used by INTERACTION_HANDLER
             TOOL_EXECUTOR["Executor"]
             TOOL_FS["FileSystem"]
             TOOL_SENDMSG["SendMessage"]
             TOOL_MANAGE_TEAM["ManageTeam"]
             TOOL_GITHUB["GitHub"]
             TOOL_WEBSEARCH["WebSearch"]
         end

         SANDBOXES["📁 Sandboxes ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage ✅"]
         SHARED_WORKSPACE["🌐 Shared Workspace ✅"]
         LOG_FILES["📄 Log Files ✅"]
         METRICS_FILE["📄 Metrics File ✅"]
         QUARANTINE_FILE["📄 Key Quarantine File ✅"] %% Added
         DATA_DIR["📁 Data Dir ✅"]
    end

    subgraph External %% Status Implicit
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        OLLAMA_SVC["⚙️ Local Ollama Svc"]
        LITELLM_SVC["⚙️ Local LiteLLM Svc"]
        CONFIG_YAML["⚙️ config.yaml"]
        PROMPTS_JSON["📜 prompts.json"] %% Added
        DOT_ENV[".env File <br>(Multi-Key Support)"] %% Updated
    end

    %% --- Connections ---
    USER -- Interacts --> Frontend;
    Frontend -- HTTP/WebSocket --> Backend;

    FASTAPI -- Manages --> AGENT_MANAGER;
    FASTAPI -- Manages --> MODEL_REGISTRY;
    FASTAPI -- Manages --> PERF_TRACKER; # Via AgentManager init
    FASTAPI -- Manages --> PROVIDER_KEY_MGR; # Via AgentManager init

    AGENT_MANAGER -- Uses --> MODEL_REGISTRY;
    AGENT_MANAGER -- Uses --> PROVIDER_KEY_MGR; # To get keys, quarantine
    AGENT_MANAGER -- Uses --> PERF_TRACKER; # To trigger save
    AGENT_MANAGER -- Instantiates --> LLM_Providers; # With specific key config
    AGENT_MANAGER -- Manages --> Agents;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates --> SESSION_MANAGER;
    AGENT_MANAGER -- Handles Failover --> AGENT_MANAGER; # Calls self to switch model/key
    AGENT_MANAGER -- Loads Prompts via --> External; # Via settings -> prompts.json

    MODEL_REGISTRY -- Discovers --> External;
    PROVIDER_KEY_MGR -- Reads/Writes --> QUARANTINE_FILE; # Added
    PROVIDER_KEY_MGR -- Creates --> DATA_DIR; # Added
    PERF_TRACKER -- Reads/Writes --> METRICS_FILE;
    PERF_TRACKER -- Creates --> DATA_DIR;

    CYCLE_HANDLER -- Runs --> Agents;
    CYCLE_HANDLER -- Delegates --> INTERACTION_HANDLER;
    CYCLE_HANDLER -- Reports Metrics --> PERF_TRACKER;
    CYCLE_HANDLER -- Triggers Failover --> AGENT_MANAGER; # Via error propagation

    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    TOOL_EXECUTOR -- Executes --> Tools;

    Backend -- "Writes Logs" --> LOG_FILES;
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;
```

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:**
    *   YAML (`PyYAML`) for bootstrap agent definitions (`config.yaml`)
    *   `.env` files (`python-dotenv`) for secrets, URLs, settings (`MODEL_TIER`, multi-key).
    *   JSON (`prompts.json`) for standard framework/agent instructions. <!-- NEW -->
*   **State Management:** In-memory dictionaries (`AgentManager`, `AgentStateManager`).
*   **Model Availability:** `ModelRegistry` class handling discovery and filtering.
*   **API Key Management:** `ProviderKeyManager` class handling key cycling and quarantining.
*   **Performance Metrics:** `ModelPerformanceTracker` class saving to JSON.
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (`SessionManager`), performance metrics, and key quarantine state.
*   **XML Parsing:** Standard library `re`, `html`.
*   **Logging:** Standard library `logging`.

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── data/                   # Persisted application data 💾
│   ├── model_performance_metrics.json # Stored metrics
│   └── quarantine_state.json          # Quarantined API keys
├── config.yaml             # Bootstrap agents (AdminAI provider/model optional) ✅
├── prompts.json            # Standard framework instructions & default prompts 📜 ✨ NEW
├── setup.sh                # Easy setup script ✨ NEW
├── run.sh                  # Easy run script ✨ NEW
├── helperfiles/            # Project planning & tracking 📝 ✅
│   ├── PROJECT_PLAN.md
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md
├── logs/                   # Application log files (timestamped) 📝 ✅
│   └── app_YYYYMMDD_HHMMSS.log
├── projects/               # Saved project/session state 💾 ✅
│   └── [ProjectName]/
│       └── [SessionName]/
│           ├── agent_session_data.json
│           └── shared_workspace/ # Created by FileSystemTool (scope: shared)
├── sandboxes/              # Agent work directories 📁 ✅
│   └── agent_X/            # Private space for each agent
├── src/                    # Source code 🐍
│   ├── __init__.py
│   ├── agents/             # Agent-related logic
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class ✅
│   │   ├── cycle_handler.py # Handles agent cycle, retries, failover ✅ ✨ UPDATED
│   │   ├── interaction_handler.py # Processes tool signals ✅
│   │   ├── manager.py      # AgentManager (orchestration) 🧑‍💼 ✅
│   │   ├── performance_tracker.py # Tracks model performance metrics 📊 ✅
│   │   ├── provider_key_manager.py # Manages API Keys & Quarantine 🔑 ✅
│   │   ├── prompt_utils.py # Prompt update helper (constants removed) ✅ ✨ UPDATED
│   │   ├── agent_lifecycle.py # Handles agent creation/deletion/bootstrap ✅ ✨ UPDATED
│   │   ├── failover_handler.py # Handles failover logic ✅
│   │   ├── session_manager.py # Handles save/load state ✅
│   │   └── state_manager.py   # Handles team state ✅
│   ├── api/                # FastAPI endpoints
│   │   ├── __init__.py
│   │   ├── http_routes.py  # API endpoints ✅
│   │   └── websocket_manager.py # Handles WS connections ✅
│   ├── config/             # Configuration loading & management
│   │   ├── __init__.py
│   │   ├── config_manager.py # Handles config.yaml read ✅
│   │   ├── model_registry.py # Handles provider/model discovery & filtering 📚 ✅
│   │   └── settings.py     # Loads .env, prompts.json, instantiates registry ✅ ✨ UPDATED
│   ├── llm_providers/      # LLM provider implementations ✅
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base provider class
│   │   ├── ollama_provider.py # Ollama implementation ✅
│   │   ├── openai_provider.py # OpenAI implementation
│   │   └── openrouter_provider.py # OpenRouter implementation
│   ├── tools/              # Agent tools implementations 🛠️ ✅
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base tool class
│   │   ├── executor.py     # Tool discovery and execution logic
│   │   ├── file_system.py  # File system tool (private/shared scope)
│   │   ├── github_tool.py  # GitHub interaction tool
│   │   ├── manage_team.py  # Agent/Team management tool
│   │   ├── send_message.py # Inter-agent communication tool
│   │   └── web_search.py   # Web search tool (scraping)
│   ├── ui/                 # (Currently empty, potential future UI components)
│   │   └── __init__.py
│   ├── utils/              # Utility functions (if needed)
│   │   └── __init__.py
│   └── main.py             # Application entry point ✅
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css       # UI styles ✅
│   └── js/
│       └── app.js          # Frontend logic ✅
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # Main UI page ✅
├── .env.example            # Example environment variables (multi-key) ✅
├── .gitignore              # Ensure logs/, projects/, sandboxes/, data/, prompts.json are added
├── LICENSE                 # Project License (MIT) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies ✅
```
*(Note: Ensure `data/`, `projects/`, `sandboxes/`, `logs/`, `.venv/`, `prompts.json` are added to your `.gitignore` file)*

## 🖱️ Usage

1.  Open the UI. Check logs for startup details.
2.  Enter your task.
3.  `Admin AI` plans, creates agents using available models, API keys, and loaded prompts.
4.  Agents execute. Errors trigger retries, key cycling, or model/provider failover. Check agent statuses and logs.
5.  `Admin AI` coordinates results.
6.  Use the "Project/Session" view (💾 icon) to save/load state. Metrics and quarantine state are saved automatically on shutdown.

## 🛠️ Development

*   Follow standard Python development practices.
*   Keep helper files (`helperfiles/`) updated.
*   Configure API keys, URLs, and model tiers via `.env`.
*   Modify `config.yaml` primarily for Admin AI base prompt/persona override.
*   Modify `prompts.json` to change standard instructions or default agent prompts.

## 🙌 Contributing

Contributions are welcome! While this framework is primarily developed through AI interaction guided by human oversight, contributions from the community for bug fixes, feature suggestions, documentation improvements, testing, and adding new tools or LLM providers are highly appreciated.

**Reporting Issues:**

*   Check existing [GitHub Issues](https://github.com/gaborkukucska/TrippleEffect/issues).
*   Open a new issue with details (logs, steps, expected vs. actual).

**Contributing Code:**

1.  Fork the repository.
2.  Create a branch (e.g., `fix/github-tool`, `feature/anthropic-provider`).
3.  Make changes, adhering to style and `DEVELOPMENT_RULES.md`.
4.  Update documentation (`README.md`, `helperfiles/*`).
5.  Commit with clear messages.
6.  Push to your fork.
7.  Open a Pull Request to the `main` branch of `gaborkukucska/TrippleEffect`.

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for the full text.
