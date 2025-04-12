<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**Version:** 2.14 (Phase 13: Performance Tracking & Auto-Failover Completed)

*This framework is primarily developed and iterated upon by Large Language Models (LLMs) like Google's Gemini series, guided by human oversight.*

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI.

It features **dynamic discovery of reachable LLM providers** (local Ollama/LiteLLM first via env/localhost/network check, then configured public APIs), their **available models**, and management of **multiple API keys per provider**. It automatically attempts **key cycling** upon authentication/rate-limit style errors for remote providers. If a key encounters persistent errors, it's **temporarily quarantined** (state saved). It can **automatically select a suitable model for the core `Admin AI`** at startup and validates models used for dynamic agents against the discovered list, considering cost tiers (`MODEL_TIER`). If an agent encounters persistent errors (like provider errors or rate limits *after* retries and key cycling), the framework **automatically attempts to failover** to other available models based on a preference hierarchy (Local -> External Free -> External Paid), up to a configurable limit, before marking the agent as errored for that task. Performance metrics (success/failure counts, duration) are tracked per model and saved.

## 🎯 Core Concept

The system orchestrates multiple LLM agents. A central `Admin AI` agent analyzes user requests and devises plans, creating specialized "worker" agents dynamically in memory.

**Key Workflow:**
1.  **Startup:**
    *   Framework checks `.env` for API keys (including `PROVIDER_API_KEY_N` format), URLs and `MODEL_TIER`.
    *   Initializes `ProviderKeyManager` (loads key quarantine state).
    *   Discovers reachable local (Ollama/LiteLLM via env/localhost/network checks) and configured remote providers.
    *   Fetches available models for reachable providers, filters by `MODEL_TIER`.
    *   Automatically selects the best available, non-depleted model for `Admin AI` if not set in `config.yaml`. Logs the selection.
    *   Loads basic performance metrics from previous runs (`data/model_performance_metrics.json`).
2.  **Task Submission:** User submits task via UI 📝.
3.  **Planning & Delegation:** `Admin AI` receives task, uses knowledge of available models, plans team, defines roles/prompts.
4.  **Agent Creation:** `Admin AI` uses `ManageTeamTool`. Framework validates requested model against available list and creates the agent using an available API key via `ProviderKeyManager`.
5.  **Framework Context:** Standard instructions injected into dynamic agents.
6.  **Task Execution & Failover:** `Admin AI` delegates tasks. Agents process using their assigned model and API key.
    *   If an agent's LLM call fails with a transient error (e.g., temporary network issue, 5xx): The `AgentCycleHandler` attempts retries with delays (up to `MAX_STREAM_RETRIES`).
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

Configuration (`config.yaml`) primarily defines `Admin AI` persona/prompt (provider/model optional). `.env` manages secrets, URLs, `MODEL_TIER`, and potentially multiple API keys per provider. Session state is saved/loaded.

## ✨ Key Features

*   **Dynamic Multi-Agent Architecture:** Admin AI orchestration.
*   **Dynamic Provider/Model Discovery:** Auto-detects reachable providers (local check priority) & models.
*   **Multi-API Key Management:** Supports multiple keys per provider (`PROVIDER_API_KEY_N` format in `.env`). <!-- NEW -->
*   **API Key Cycling:** Automatically tries next available key on auth/rate-limit errors. <!-- NEW -->
*   **API Key Quarantining:** Temporarily disables keys after persistent failure (24h default, state saved). <!-- NEW -->
*   **Automatic Admin AI Model Selection:** Selects best available, non-depleted model at startup.
*   **Model Availability Validation:** Ensures dynamic agents use valid models.
*   **Automatic Retry & Failover:** Agents attempt retries for transient errors, then key cycling (if applicable), then model/provider failover respecting tiers (Local -> Free -> Paid), up to `MAX_FAILOVER_ATTEMPTS`. <!-- Updated -->
*   **Performance Tracking:** Records success/failure counts and duration per model, saved to JSON.
*   **Structured Delegation & Framework Context.**
*   **Asynchronous Backend & Real-time UI Updates.**
*   **Multi-Provider LLM Support:** Connects to discovered/configured providers.
*   **Simplified Configuration:** `config.yaml` (Admin AI model optional), `.env` (secrets, URLs, tier, multiple keys). <!-- Updated -->
*   **Sandboxed & Shared Workspaces.**
*   **Sequential Multi-Tool Usage.**
*   **Agent Communication.**
*   **Session Persistence.**
*   **Timestamped File Logging.**
*   **Extensible Design.**
*   **Termux Friendly.**

## 🏗️ Architecture Overview (Conceptual - Post Phase 13 + Key Mgmt)

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
*   **Configuration:** YAML (`PyYAML`) for bootstrap agents, `.env` (secrets, URLs, `MODEL_TIER`, multi-key). <!-- Updated -->
*   **State Management:** In-memory dictionaries (`AgentManager`, `AgentStateManager`).
*   **Model Availability:** `ModelRegistry` class handling discovery and filtering.
*   **API Key Management:** `ProviderKeyManager` class handling key cycling and quarantining. <!-- NEW -->
*   **Performance Metrics:** `ModelPerformanceTracker` class saving to JSON.
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (`SessionManager`), performance metrics, and key quarantine state. <!-- Updated -->
*   **XML Parsing:** Standard library `re`, `html`.
*   **Logging:** Standard library `logging`.

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── data/                   # Persisted application data 💾
│   ├── model_performance_metrics.json # Stored metrics
│   └── quarantine_state.json          # Quarantined API keys ✨ NEW
├── config.yaml             # Bootstrap agents (AdminAI provider/model optional) ✅
├── helperfiles/            # Project planning & tracking 📝 ✅
│   ├── PROJECT_PLAN.md
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md
├── logs/                   # Application log files (timestamped) 📝 ✅
│   └── app_YYYYMMDD_HHMMSS.log
├── projects/               # Saved project/session state 💾
│   └── [ProjectName]/
│       └── [SessionName]/
│           └── agent_session_data.json
├── sandboxes/              # Agent work directories 📁 ✅
│   └── agent_X/
├── src/                    # Source code 🐍
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class ✅
│   │   ├── cycle_handler.py # Handles agent cycle, retries, triggers failover, reports metrics ✅
│   │   ├── interaction_handler.py # Processes tool signals ✅
│   │   ├── manager.py      # AgentManager (orchestration, key/failover logic) 🧑‍💼 ✨ UPDATED
│   │   ├── performance_tracker.py # Tracks model performance metrics 📊 ✅
│   │   ├── provider_key_manager.py # Manages API Keys & Quarantine 🔑 ✨ NEW
│   │   ├── prompt_utils.py # Prompt templates ✅
│   │   ├── session_manager.py # Handles save/load state ✅
│   │   └── state_manager.py   # Handles team state ✅
│   ├── api/
│   │   ├── __init__.py
│   │   ├── http_routes.py  # API endpoints ✅
│   │   └── websocket_manager.py # Handles WS connections ✅
│   ├── config/
│   │   ├── __init__.py
│   │   ├── config_manager.py # Handles config.yaml read ✅
│   │   ├── model_registry.py # Handles provider/model discovery & filtering 📚 ✨ UPDATED
│   │   └── settings.py     # Loads .env (multi-key), instantiates registry ✅ ✨ UPDATED
│   ├── llm_providers/      # LLM provider implementations ✅
│   │   └── ...
│   ├── tools/              # Agent tools implementations 🛠️ ✅
│   │   └── ...
│   ├── ui/
│   │   └── __init__.py
│   ├── utils/
│   │   └── __init__.py
│   ├── __init__.py
│   └── main.py             # Application entry point (runs discovery) ✅
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css       # UI styles ✅
│   └── js/
│       └── app.js          # Frontend logic ✅
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # Main UI page ✅
├── .env.example            # Example environment variables (multi-key) ✅ ✨ UPDATED
├── .gitignore              # Ensure logs/, projects/, sandboxes/, data/ are added
├── LICENSE                 # Project License (MIT) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies ✅
```
*(Note: Ensure `data/`, `projects/`, `sandboxes/`, `logs/`, `.venv/` are added to your `.gitignore` file)*

## ⚙️ Installation

1.  **Prerequisites:** Python 3.9+ installed, Git. (Optional) Local Ollama or LiteLLM service running if you want to use local models.
2.  **Clone:** `git clone https://github.com/gaborkukucska/TrippleEffect.git`
3.  **Navigate:** `cd TrippleEffect`
4.  **Create Virtual Environment:**
    ```bash
    python -m venv .venv
    ```
5.  **Activate Virtual Environment:**
    *   Windows: `.venv\Scripts\activate`
    *   macOS/Linux: `source .venv/bin/activate`
6.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *(This installs FastAPI, Uvicorn, PyYAML, OpenAI library, etc.)*
7.  **Configure Environment (`.env`):**
    *   Copy `.env.example` to a new file named `.env`: `cp .env.example .env`
    *   Edit the `.env` file:
        *   Add your required **API keys**.
        *   For providers like OpenRouter or OpenAI where you have multiple keys, you can add them using the format `PROVIDERNAME_API_KEY_1`, `PROVIDERNAME_API_KEY_2`, etc. (See `.env.example`).
        *   Set **Base URLs** only if you need to override defaults (e.g., for proxies or local instances not on default ports). For local Ollama/LiteLLM, leave blank to use auto-discovery (localhost/network check) or set explicitly (e.g., `OLLAMA_BASE_URL=http://192.168.1.X:11434`).
        *   Set `MODEL_TIER` (`FREE` or `ALL`) to filter models from remote providers.
        *   Set `GITHUB_ACCESS_TOKEN` if you need the GitHub tool.
8.  **Review Bootstrap Config (`config.yaml`):** Usually, you don't need to change this. The Admin AI's model is auto-selected by default. Modify only if you need to force a specific bootstrap agent or change the Admin AI's base persona/instructions.
9.  **Create Directories (if they don't exist):**
    ```bash
    mkdir logs data projects sandboxes
    ```
    *(Note: `projects` and `sandboxes` might be created automatically on first run/save)*

## ▶️ Running the Application

Ensure your virtual environment is activated (`source .venv/bin/activate` or equivalent). Then run:

```bash
python -m src.main
```
*   The server starts. **Watch the logs** for provider discovery, model filtering, Admin AI model selection, and any errors.
*   Access the web UI in your browser, typically at `http://localhost:8000` (or your machine's IP if running remotely/on Termux).

## 🖱️ Usage

1.  Open the UI. Check initial logs in the "Logs" view for discovery/selection details.
2.  Enter your task in the chat input.
3.  `Admin AI` plans, creates agents using available models and API keys.
4.  Agents execute. If errors occur:
    *   Transient errors trigger retries.
    *   Key-related errors trigger key quarantining and cycling.
    *   Persistent errors trigger model/provider failover (Local -> Free -> Paid).
    *   Check agent statuses and logs for details. Agents enter `ERROR` state if all failover attempts fail.
5.  `Admin AI` coordinates results.
6.  Use the "Project/Session" view (💾 icon) to save/load state. Performance metrics and key quarantine state are saved automatically on shutdown.

## 🛠️ Development

*   Follow standard Python development practices.
*   Keep helper files (`helperfiles/`) updated.
*   Configure API keys, URLs, and model tiers via `.env`.
*   Modify `config.yaml` primarily for Admin AI base prompt/persona or to *override* auto-selection if necessary.

## 🙌 Contributing

Contributions are welcome! While this framework is primarily developed through AI interaction guided by human oversight, contributions from the community for bug fixes, feature suggestions, documentation improvements, testing, and adding new tools or LLM providers are highly appreciated.

**Reporting Issues:**

*   If you encounter a bug or have a suggestion for a new feature, please check the existing [GitHub Issues](https://github.com/gaborkukucska/TrippleEffect/issues) first.
*   If your issue isn't listed, please open a new issue, providing as much detail as possible (logs, steps to reproduce, expected vs. actual behavior).

**Contributing Code:**

1.  **Fork the Repository:** Create your own copy of the project on GitHub.
2.  **Create a Branch:** Make a new branch in your fork for your changes (e.g., `git checkout -b fix/fix-github-tool` or `git checkout -b feature/add-anthropic-provider`).
3.  **Make Changes:** Implement your fix or feature.
    *   Please adhere to the existing code style (PEP 8, use Black for formatting if possible).
    *   Follow the guidelines outlined in `helperfiles/DEVELOPMENT_RULES.md`.
4.  **Update Documentation:** If your changes impact the architecture, add new features, modify function signatures, or change configuration, please update:
    *   `README.md`
    *   `helperfiles/PROJECT_PLAN.md`
    *   `helperfiles/FUNCTIONS_INDEX.md`
5.  **Commit:** Commit your changes with clear and descriptive messages.
6.  **Push:** Push your branch to your fork on GitHub.
7.  **Open a Pull Request:** Create a Pull Request from your branch to the `main` branch of the original `gaborkukucska/TrippleEffect` repository. Describe your changes clearly in the PR description.

We will review contributions and provide feedback.

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for the full text.
