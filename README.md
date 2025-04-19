<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**Version:** 2.16 (Phase 16: XML Tooling Reverted & Prompt Refinement)

*This framework is primarily developed and iterated upon by Large Language Models (LLMs) like Google's Gemini series, guided by human oversight provided by Gabby* 🤓👋

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI.

It features **dynamic discovery of reachable LLM providers** (local Ollama/LiteLLM first via env/localhost/network check, then configured public APIs), their **available models**, and management of **multiple API keys per provider**. It automatically attempts **key cycling** upon authentication/rate-limit style errors for remote providers. If a key encounters persistent errors, it's **temporarily quarantined** (state saved). It can **automatically select a suitable model for the core `Admin AI`** at startup and **validates models requested by the Admin AI** for dynamic agents against the discovered list, considering cost tiers (`MODEL_TIER`) and provider correctness. Standard agent instructions and default prompts are loaded from `prompts.json`. If an agent encounters persistent errors (like provider errors or rate limits *after* retries and key cycling), the framework **automatically attempts to failover** to other available models based on a preference hierarchy (Local -> External Free -> External Paid), up to a configurable limit, before marking the agent as errored for that task. Performance metrics (success/failure counts, duration) are tracked per model and saved. Tool interaction now uses **XML format**.

## 🚀 Quick Start: Setup & Run

For a quick setup, use the provided scripts (requires bash environment, Python 3.9+, pip, and git):

1.  **Clone:** `git clone https://github.com/gaborkukucska/TrippleEffect.git`
2.  **Navigate:** `cd TrippleEffect`
3.  **Run Setup Script:**
    ```bash
    chmod +x setup.sh
    ./setup.sh
    ```
    *   This creates directories, sets up a Python virtual environment (`.venv/`), installs Python dependencies (`requirements.txt`), installs Node.js dependencies for the optional Ollama proxy (`ollama-proxy/package.json` if Node.js/npm are found), and guides you through configuring API keys and settings in a `.env` file. Follow the prompts carefully.
4.  **Run Application Script:**
    ```bash
    chmod +x run.sh
    ./run.sh
    ```
    *   This activates the virtual environment and starts the FastAPI server.
    *   Access the web UI in your browser, typically at `http://localhost:8000` (or your machine's IP if running remotely/on Termux). Watch the terminal for logs from both the Python backend and potentially the Ollama proxy (if enabled).
    *   **Note on Ollama Connection Issues:** If using Ollama, especially over certain networks or Docker setups, you might encounter `ClientConnectionError: Connection closed` during streaming. The framework now includes an **integrated Node.js proxy** to mitigate this. Enable it by setting `USE_OLLAMA_PROXY=true` in your `.env` file. The `run.sh` script will automatically start/stop it. See configuration details below.

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
        *   Set **Base URLs** only if you need to override defaults (e.g., for proxies or local instances not on default ports). For local Ollama/LiteLLM, leave blank to use auto-discovery (localhost/network check) or set explicitly (e.g., `OLLAMA_BASE_URL=http://192.168.1.X:11434`). **Note:** `OLLAMA_BASE_URL` is only used if `USE_OLLAMA_PROXY=false`.
        *   Set `MODEL_TIER` (`FREE` or `ALL`) to filter models from remote providers. Default is `FREE`.
        *   Set `GITHUB_ACCESS_TOKEN` if you need the GitHub tool.
        *   **Configure Ollama Proxy (Optional):**
            *   Set `USE_OLLAMA_PROXY=true` to enable the integrated Node.js proxy (recommended if you experience Ollama connection issues). Requires Node.js/npm installed. If true, `OLLAMA_BASE_URL` is ignored by the Python code (but still used by the proxy itself via `OLLAMA_PROXY_TARGET_URL`). Ollama is considered 'configured' if this is true OR `OLLAMA_BASE_URL` is set.
            *   Set `OLLAMA_PROXY_PORT` (default: 3000) for the proxy's listening port.
            *   Set `OLLAMA_PROXY_TARGET_URL` (default: http://localhost:11434) to the actual address of your Ollama service that the proxy should connect to.
8.  **(Optional) Install Ollama Proxy Dependencies:** If you plan to use the integrated proxy (`USE_OLLAMA_PROXY=true`), ensure Node.js and npm are installed, then run:
    ```bash
    cd ollama-proxy
    npm install
    cd ..
    ```
    *(Note: `setup.sh` attempts this automatically if Node.js/npm are found).*
9.  **Review Bootstrap Config (`config.yaml`):** Usually, leave as is. Admin AI model is auto-selected. Modify only to force Admin AI model or change its base persona. Create the file with default content if it doesn't exist (see `setup.sh` for default content).
10. **Review Default Prompts (`prompts.json`):** Review and customize the standard framework instructions (now expecting XML tool calls) or default agent prompts if desired. Ensure the Admin AI instructions clearly state the requirement to provide valid provider/model pairs for agent creation. Create the file with default content if it doesn't exist (see `setup.sh` for default content).
11. **Create Directories (if needed):** `mkdir logs data projects sandboxes`

## ▶️ Manual Running

If using manual installation or not using `run.sh`:

1.  **Activate Python Virtual Environment:**
    ```bash
    source .venv/bin/activate
    # Or equivalent for your OS
    ```
2.  **Run the Python Application:**
    ```bash
    python -m src.main
    ```
*   The Python server starts. Watch the logs for startup details.
*   **Proxy Management:** If `USE_OLLAMA_PROXY=true` is set in your `.env` file, the Python application will automatically attempt to start the Node.js proxy process in the background during startup and terminate it during shutdown. Check the application logs for messages related to the proxy status.
*   Access the web UI in your browser, typically at `http://localhost:8000` (or your machine's IP if running remotely/on Termux).
*   **Note on Ollama:** As mentioned, the integrated proxy (enabled via `USE_OLLAMA_PROXY=true` and now managed by the main application) is the recommended way to handle potential `ClientConnectionError` issues.

## 🎯 Core Concept

The system orchestrates multiple LLM agents using **XML-based tool communication**. A central `Admin AI` agent analyzes user requests and devises plans, creating specialized "worker" agents dynamically in memory. Standard framework instructions and default prompts are loaded from `prompts.json` at startup.

**Key Workflow:**
1.  **Startup:**
    *   Framework checks `.env` for API keys (including `PROVIDER_API_KEY_N` format), URLs, `USE_OLLAMA_PROXY`, `OLLAMA_PROXY_PORT`, and `MODEL_TIER`.
    *   Starts Ollama proxy if enabled.
    *   Loads `prompts.json` containing standard instructions (expecting XML) and default prompts.
    *   Initializes `ProviderKeyManager` (loads key quarantine state).
    *   Discovers reachable local (Ollama/LiteLLM via env/localhost/network checks or proxy) and configured remote providers.
    *   Fetches available models for reachable providers, filters by `MODEL_TIER`.
    *   Automatically selects the best available, non-depleted model for `Admin AI` if not set in `config.yaml`. Logs the selection.
    *   Loads basic performance metrics from previous runs (`data/model_performance_metrics.json`).
2.  **Task Submission:** User submits task via UI 📝.
3.  **Planning & Delegation:** `Admin AI` receives task, uses knowledge of available models (from its prompt) and loaded prompts, plans team, defines roles/prompts.
4.  **Agent Creation:** `Admin AI` uses `<ManageTeamTool>` with `action="create_agent"`.
    *   **CRITICAL:** Admin AI **MUST specify valid `<provider>` and `<model>` parameters** from the available list provided in its system prompt context. The format must match the list (e.g., `<model>ollama/llama3...</model>` or `<model>google/gemma...</model>`). The framework does **not** automatically select models for dynamic agents.
    *   Framework validates the requested provider/model against the available list and provider format.
    *   Framework creates the agent using an available API key via `ProviderKeyManager`.
5.  **Framework Context:** Standard instructions (from loaded `prompts.json`, expecting XML) injected into dynamic agents.
6.  **Task Execution & Failover:** `Admin AI` delegates tasks via `<send_message>`. Agents process using their assigned model and API key.
    *   If an agent's LLM call fails with a transient error (e.g., temporary network issue, 5xx, connection closed): The `AgentCycleHandler` attempts retries with delays (up to `MAX_STREAM_RETRIES`).
    *   If an agent's LLM call fails with a potentially key-related error (e.g., 429 rate limit, 401/403 auth error):
        *   The `ProviderKeyManager` quarantines the specific API key for 24 hours.
        *   The `AgentManager` attempts to cycle to the *next available key* for the *same provider*.
        *   If a new key is found, the agent retries the *same task* with the *same model* using the new key.
    *   If an agent's LLM call fails fatally (non-retryable error, max retries reached, or all keys for the provider are quarantined):
        *   The framework automatically triggers model/provider failover.
        *   It attempts to switch the agent to the next best available model (Local -> Free -> Paid) that hasn't already failed *for this specific task attempt sequence* and whose provider has available keys.
        *   This repeats up to `MAX_FAILOVER_ATTEMPTS`.
        *   If all failover attempts fail, the agent enters an `ERROR` state for that task.
    *   Agents use tools (via XML), communicate, and report results back to `Admin AI` using `<send_message>`.
7.  **Metric Tracking:** Success/failure and duration of each LLM call attempt (including retries and failovers) are recorded by the `ModelPerformanceTracker`.
8.  **Coordination & Synthesis:** `Admin AI` monitors progress, coordinates, synthesizes results.
9.  **Cleanup:** `Admin AI` cleans up dynamic agents/teams via `<ManageTeamTool>`.
10. **Shutdown:** Performance metrics and API key quarantine states are saved. Ollama proxy (if started) is terminated.

Configuration (`config.yaml`) primarily defines `Admin AI` persona/prompt (provider/model optional). `.env` manages secrets, URLs, `MODEL_TIER`, proxy settings, and potentially multiple API keys per provider. `prompts.json` defines standard agent instructions (using XML tool format). Session state is saved/loaded.

## ✨ Key Features

*   **Dynamic Multi-Agent Architecture:** Admin AI orchestration.
*   **Dynamic Provider/Model Discovery:** Auto-detects reachable providers (local check priority) & models.
*   **Multi-API Key Management:** Supports multiple keys per provider (`PROVIDER_API_KEY_N` format in `.env`).
*   **API Key Cycling:** Automatically tries next available key on auth/rate-limit errors.
*   **API Key Quarantining:** Temporarily disables keys after persistent failure (24h default, state saved).
*   **Automatic Admin AI Model Selection:** Selects best available, non-depleted model at startup.
*   **Centralized Prompts:** Standard instructions and defaults loaded from `prompts.json`.
*   **Model Availability Validation:** Ensures dynamic agents use valid, available models specified by Admin AI.
*   **Provider/Model Correctness Check:** Validates that the requested provider matches the model format (e.g., `ollama` provider for `ollama/...` models).
*   **XML Tool Communication:** Agents use XML format to request tool execution. <!-- Updated -->
*   **Automatic Retry & Failover:** Agents attempt retries for transient errors, then key cycling, then model/provider failover respecting tiers (Local -> Free -> Paid), up to `MAX_FAILOVER_ATTEMPTS`.
*   **Performance Tracking:** Records success/failure counts and duration per model, saved to JSON.
*   **Structured Delegation & Framework Context.**
*   **Asynchronous Backend & Real-time UI Updates.**
*   **Multi-Provider LLM Support:** Connects to discovered/configured providers.
*   **Simplified Configuration:** `config.yaml` (Admin AI model optional), `.env` (secrets, URLs, tier, multiple keys, proxy), `prompts.json` (standard instructions - XML). <!-- Updated -->
*   **Integrated Ollama Proxy (Optional):** Mitigates connection issues, managed by the application lifecycle.
*   **Sandboxed & Shared Workspaces.**
*   **Sequential Multi-Tool Usage.**
*   **Agent Communication.**
*   **Session Persistence.**
*   **Timestamped File Logging.**
*   **Extensible Design.**
*   **Termux Friendly.**

## 🏗️ Architecture Overview (Conceptual - Post Phase 16)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_CHAT_VIEW["Chat & Agents View ✅"]
        UI_LOGS_VIEW["System Logs View ✅"]
        UI_SESSION_VIEW["Project/Session View ✅"]
        UI_CONFIG_VIEW["Static Config Info View ✅"]
    %% Simplified
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend ✅"]
        WS_MANAGER["🔌 WebSocket Manager ✅"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ Uses ModelRegistry ✅<br>+ Uses ProviderKeyManager ✅<br>+ Auto-Selects Admin AI Model ✅<br>+ Handles Key/Model Failover ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"]
        %% Updated
        PROVIDER_KEY_MGR["🔑 Provider Key Manager <br>+ Manages Keys ✅<br>+ Handles Quarantine ✅<br>+ Saves/Loads State ✅"]
        %% Added
        MODEL_REGISTRY["📚 Model Registry✅"]
        PERF_TRACKER["📊 Performance Tracker<br>+ Records Metrics ✅<br>+ Saves/Loads Metrics ✅"]
        CYCLE_HANDLER["🔄 Agent Cycle Handler<br>+ Handles Retries ✅<br>+ Triggers Key/Model Failover ✅<br>+ Reports Metrics ✅"]
        %% Updated
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

         subgraph Tools ["🛠️ Tools (XML Format)"] %% Updated
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
        OLLAMA_PROXY_SVC["🔌 Node.js Ollama Proxy (Optional)"] %% Added
        LITELLM_SVC["⚙️ Local LiteLLM Svc"]
        CONFIG_YAML["⚙️ config.yaml"]
        PROMPTS_JSON["📜 prompts.json (XML Format)"] %% Updated
        DOT_ENV[".env File <br>(Multi-Key Support)<br>(Proxy Config)"] %% Updated
    end

    %% --- Connections ---
    USER -- Interacts --> Frontend;
    Frontend -- HTTP/WebSocket --> Backend;

    FASTAPI -- Manages --> AGENT_MANAGER;
    FASTAPI -- Manages --> MODEL_REGISTRY;
    FASTAPI -- Manages --> PERF_TRACKER; # Via AgentManager init
    FASTAPI -- Manages --> PROVIDER_KEY_MGR; # Via AgentManager init
    FASTAPI -- Manages --> OLLAMA_PROXY_SVC; # Lifespan: Starts/Stops Proxy Process

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

    MODEL_REGISTRY -- Discovers --> External; # Checks Ollama/LiteLLM/Proxy/APIs
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

    LLM_Providers -- Calls --> OLLAMA_PROXY_SVC; # OllamaProvider uses proxy URL if enabled
    LLM_Providers -- Calls --> LLM_API_SVC; # Other providers
    LLM_Providers -- Calls --> OLLAMA_SVC; # OllamaProvider uses direct URL if proxy disabled
    OLLAMA_PROXY_SVC -- Forwards to --> OLLAMA_SVC;

    Backend -- "Writes Logs" --> LOG_FILES;
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;```

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library (used by multiple providers), `aiohttp` (used internally by Ollama provider, GitHub tool, Web Search tool)
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:**
    *   YAML (`PyYAML`) for bootstrap agent definitions (`config.yaml`)
    *   `.env` files (`python-dotenv`) for secrets, URLs, settings (`MODEL_TIER`, multi-key, proxy config).
    *   JSON (`prompts.json`) for standard framework/agent instructions (using XML tool format). <!-- Updated -->
*   **State Management:** In-memory dictionaries (`AgentManager`, `AgentStateManager`).
*   **Model Availability:** `ModelRegistry` class handling discovery and filtering.
*   **API Key Management:** `ProviderKeyManager` class handling key cycling and quarantining.
*   **Performance Metrics:** `ModelPerformanceTracker` class saving to JSON.
*   **Tool Communication:** XML format parsed via standard `re` library. <!-- Updated -->
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (`SessionManager`), performance metrics, and key quarantine state.
*   **XML Parsing:** Standard library `re`, `html`.
*   **Logging:** Standard library `logging`.
*   **Ollama Proxy:** Node.js, Express, node-fetch (managed via `subprocess`).

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── data/                   # Persisted application data 💾
│   ├── model_performance_metrics.json # Stored metrics
│   └── quarantine_state.json          # Quarantined API keys
├── ollama-proxy/           # Optional Node.js proxy for Ollama ✨ NEW
│   ├── server.js
│   ├── package.json
│   └── package-lock.json
├── config.yaml             # Bootstrap agents (AdminAI provider/model optional) ✅
├── prompts.json            # Standard framework instructions & default prompts (XML tool format) 📜 ✨ UPDATED
├── setup.sh                # Easy setup script ✨ NEW
├── run.sh                  # Easy run script ✨ NEW
├── helperfiles/            # Project planning & tracking 📝 ✅
│   ├── PROJECT_PLAN.md
│   ├── DEVELOPMENT_RULES.md
│   ├── FUNCTIONS_INDEX.md
│   └── TOOL_MAKING.md # Updated
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
│   │   ├── core.py         # Agent class (Parses XML) ✅ ✨ UPDATED
│   │   ├── cycle_handler.py # Handles agent cycle, retries, failover, tool processing ✅ ✨ UPDATED
│   │   ├── failover_handler.py # Handles key cycling & model failover ✅ ✨ NEW
│   │   ├── interaction_handler.py # Processes tool signals ✅
│   │   ├── manager.py      # AgentManager (orchestration) 🧑‍💼 ✅
│   │   ├── performance_tracker.py # Tracks model performance metrics 📊 ✅
│   │   ├── provider_key_manager.py # Manages API Keys & Quarantine 🔑 ✅
│   │   ├── prompt_utils.py # Prompt update helper ✅
│   │   ├── agent_lifecycle.py # Handles agent creation/deletion/bootstrap (Injects XML tools, Validates model) ✅ ✨ UPDATED
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
│   │   └── settings.py     # Loads .env, prompts.json, instantiates registry (Ollama config fix) ✅ ✨ UPDATED
│   ├── llm_providers/      # LLM provider implementations ✅
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base provider class
│   │   ├── ollama_provider.py # Ollama implementation ✅
│   │   ├── openai_provider.py # OpenAI implementation
│   │   └── openrouter_provider.py # OpenRouter implementation
│   ├── tools/              # Agent tools implementations 🛠️ ✅
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base tool class
│   │   ├── executor.py     # Tool discovery and execution logic (Generates XML descriptions) ✨ UPDATED
│   │   ├── file_system.py  # File system tool (private/shared scope)
│   │   ├── github_tool.py  # GitHub interaction tool
│   │   ├── manage_team.py  # Agent/Team management tool (Validates create_agent params) ✨ UPDATED
│   │   ├── send_message.py # Inter-agent communication tool
│   │   └── web_search.py   # Web search tool (scraping)
│   ├── ui/                 # (Currently empty, potential future UI components)
│   │   └── __init__.py
│   ├── utils/              # Utility functions (if needed)
│   │   └── __init__.py
│   └── main.py             # Application entry point (manages proxy process) ✅ ✨ UPDATED
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css       # UI styles ✅
│   └── js/
│       └── app.js          # Frontend logic ✅
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # Main UI page ✅
├── .env.example            # Example environment variables (multi-key, proxy) ✅ ✨ UPDATED
├── .gitignore              # Ensure logs/, projects/, sandboxes/, data/, prompts.json, node_modules/ are added
├── LICENSE                 # Project License (MIT) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies ✅
```
*(Note: Ensure `data/`, `projects/`, `sandboxes/`, `logs/`, `.venv/`, `prompts.json`, `ollama-proxy/node_modules/` are added to your `.gitignore` file)*

## 🖱️ Usage

1.  Open the UI. Check logs for startup details (including proxy status and discovered models).
2.  Enter your task.
3.  `Admin AI` plans, then uses `<ManageTeamTool>` to create teams and agents. **Crucially, it must provide valid `<provider>` and `<model>` parameters** within the tool call, selecting from the list shown in its context.
4.  Agents execute. Errors trigger retries, key cycling, or model/provider failover. Check agent statuses and logs.
5.  `Admin AI` coordinates results based on agent messages.
6.  Use the "Project/Session" view (💾 icon) to save/load state. Metrics and quarantine state are saved automatically on shutdown.

## 🛠️ Development

*   Follow standard Python development practices.
*   Keep helper files (`helperfiles/`) updated.
*   Configure API keys, URLs, model tiers, and Ollama proxy settings via `.env`.
*   Modify `config.yaml` primarily for Admin AI base prompt/persona override or forcing a specific Admin AI model.
*   Modify `prompts.json` to change standard instructions (expecting XML) or default agent prompts. Ensure Admin AI instructions emphasize the need to provide valid provider/model pairs.
*   Develop new tools following the XML convention (see `helperfiles/TOOL_MAKING.md`).

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
