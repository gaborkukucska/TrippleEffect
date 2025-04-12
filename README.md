<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**Version:** 2.14 (Phase 13: Performance Tracking & Auto-Failover Completed) <!-- Updated Version -->

*This framework is primarily developed and iterated upon by Large Language Models (LLMs) like Google's Gemini series, guided by human oversight.*

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI.

It features **dynamic discovery of reachable LLM providers** (local Ollama/LiteLLM, configured public APIs) and their **available models**. It can **automatically select a suitable model for the core `Admin AI`** at startup and validates models used for dynamic agents against the discovered list, considering cost tiers (`MODEL_TIER`). If an agent encounters persistent errors (like provider errors or rate limits) during a task, the framework **automatically attempts to failover** to other available models based on a preference hierarchy (Local -> Free Remote -> Paid Remote), up to a configurable limit, before marking the agent as errored for that task. Performance metrics (success/failure counts, duration) are tracked per model and saved.

## 🎯 Core Concept

The system orchestrates multiple LLM agents. A central `Admin AI` agent analyzes user requests and devises plans, creating specialized "worker" agents dynamically in memory.

**Key Workflow:**
1.  **Startup:**
    *   Framework checks `.env` for API keys/URLs and `MODEL_TIER`.
    *   Discovers reachable local (Ollama/LiteLLM) and configured remote providers.
    *   Fetches available models, filters by `MODEL_TIER`.
    *   Automatically selects the best available model for `Admin AI` if not set in `config.yaml`. Logs the selection.
    *   Loads basic performance metrics from previous runs (`data/model_performance_metrics.json`).
2.  **Task Submission:** User submits task via UI 📝.
3.  **Planning & Delegation:** `Admin AI` receives task, uses knowledge of available models, plans team, defines roles/prompts.
4.  **Agent Creation:** `Admin AI` uses `ManageTeamTool`. Framework validates requested model against available list and creates the agent.
5.  **Framework Context:** Standard instructions injected into dynamic agents.
6.  **Task Execution & Failover:** `Admin AI` delegates tasks. Agents process using their assigned model.
    *   If an agent's LLM call fails persistently (e.g., rate limit, provider error):
        *   The framework automatically triggers failover.
        *   It attempts to switch the agent to the next best available model (Local -> Free -> Paid) that hasn't already failed *for this specific task attempt*.
        *   This repeats up to `MAX_FAILOVER_ATTEMPTS`.
        *   If all failover attempts fail, the agent enters an `ERROR` state for that task.
    *   Agents use tools, communicate, and report results back to `Admin AI`.
7.  **Metric Tracking:** Success/failure and duration of each LLM call attempt (including failovers) are recorded by the `ModelPerformanceTracker`.
8.  **Coordination & Synthesis:** `Admin AI` monitors progress, coordinates, synthesizes results.
9.  **Cleanup:** `Admin AI` cleans up dynamic agents/teams.
10. **Shutdown:** Performance metrics are saved to `data/model_performance_metrics.json`.

Configuration (`config.yaml`) primarily defines `Admin AI` persona/prompt (provider/model optional). `.env` manages secrets, URLs, `MODEL_TIER`. Session state is saved/loaded. **User override for model errors is removed.**

## ✨ Key Features

*   **Dynamic Multi-Agent Architecture:** Admin AI orchestration.
*   **Dynamic Provider/Model Discovery:** Auto-detects reachable providers & models.
*   **Automatic Admin AI Model Selection:** Selects best available model at startup.
*   **Model Availability Validation:** Ensures dynamic agents use valid models.
*   **Automatic Model Failover:** Agents attempt to switch models/providers on persistent errors (up to `MAX_FAILOVER_ATTEMPTS`), respecting tiers (Local -> Free -> Paid). <!-- NEW -->
*   **Performance Tracking:** Records success/failure counts and duration per model, saved to JSON. <!-- NEW -->
*   **No User Override Required:** Fully automated error handling/failover loop. <!-- Updated -->
*   **Structured Delegation & Framework Context.**
*   **Asynchronous Backend & Real-time UI Updates.**
*   **Multi-Provider LLM Support:** Connects to discovered/configured providers.
*   **Simplified Configuration:** `config.yaml` (Admin AI model optional), `.env` (secrets, URLs, `MODEL_TIER`).
*   **Sandboxed & Shared Workspaces.**
*   **Sequential Multi-Tool Usage.**
*   **Agent Communication.**
*   **Session Persistence.**
*   **Timestamped File Logging.**
*   **Extensible Design.**
*   **Termux Friendly.**

## 🏗️ Architecture Overview (Conceptual - Post Phase 13)

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
        WS_MANAGER["🔌 WebSocket Manager ✅"] %% No Override Msgs
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ Uses ModelRegistry ✅<br>+ Auto-Selects Admin AI Model ✅<br>+ **Handles Model Failover** ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"]
        MODEL_REGISTRY["📚 Model Registry✅"]
        PERF_TRACKER["📊 Performance Tracker<br>+ Records Metrics ✅<br>+ Saves/Loads Metrics ✅"] %% Added
        CYCLE_HANDLER["🔄 Agent Cycle Handler<br>+ Handles Events/Retries ✅<br>+ **Triggers Failover** ✅<br>+ **Reports Metrics** ✅"] %% Updated
        INTERACTION_HANDLER["🤝 Interaction Handler ✅"]
        STATE_MANAGER["📝 AgentStateManager ✅"]
        SESSION_MANAGER["💾 SessionManager ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent ✅"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers"] %% Status Implicit
             PROVIDER_OR["🔌 OpenRouter"]
             PROVIDER_OLLAMA["🔌 Ollama"]
             PROVIDER_OPENAI["🔌 OpenAI"]
             PROVIDER_LITELLM["🔌 LiteLLM (TBD)"]
         end

         subgraph Tools ["🛠️ Tools"] %% Status Implicit
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
         METRICS_FILE["📄 Metrics File ✅"] %% Added
         DATA_DIR["📁 Data Dir (for metrics) ✅"] %% Added
    end

    subgraph External %% Status Implicit
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        OLLAMA_SVC["⚙️ Local Ollama Svc"]
        LITELLM_SVC["⚙️ Local LiteLLM Svc"]
        CONFIG_YAML["⚙️ config.yaml"]
        DOT_ENV[".env File"]
    end

    %% --- Connections ---
    USER -- Interacts --> Frontend;
    Frontend -- HTTP/WebSocket --> Backend;

    FASTAPI -- Manages --> AGENT_MANAGER;
    FASTAPI -- Manages --> MODEL_REGISTRY;
    FASTAPI -- Manages --> PERF_TRACKER; # Via AgentManager init

    AGENT_MANAGER -- Uses --> MODEL_REGISTRY;
    AGENT_MANAGER -- Uses --> PERF_TRACKER; # To trigger save
    AGENT_MANAGER -- Instantiates --> LLM_Providers;
    AGENT_MANAGER -- Manages --> Agents;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates --> SESSION_MANAGER;
    AGENT_MANAGER -- Handles Failover --> AGENT_MANAGER;

    MODEL_REGISTRY -- Discovers --> External;
    PERF_TRACKER -- Reads/Writes --> METRICS_FILE;
    PERF_TRACKER -- Creates --> DATA_DIR;


    CYCLE_HANDLER -- Runs --> Agents;
    CYCLE_HANDLER -- Delegates --> INTERACTION_HANDLER;
    CYCLE_HANDLER -- Reports Metrics --> PERF_TRACKER;
    CYCLE_HANDLER -- Triggers Failover --> AGENT_MANAGER;

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
*   **Configuration:** YAML (`PyYAML`) for bootstrap agents, `.env` for secrets/URLs/`MODEL_TIER`. <!-- Updated -->
*   **State Management:** In-memory dictionaries (`AgentManager`, `AgentStateManager`).
*   **Model Availability:** `ModelRegistry` class handling discovery and filtering. <!-- NEW -->
*   **Performance Metrics:** `ModelPerformanceTracker` class saving to JSON. <!-- NEW -->
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (`SessionManager`) and performance metrics. <!-- Updated -->
*   **XML Parsing:** Standard library `re`, `html`.
*   **Logging:** Standard library `logging`.

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── data/                   # Persisted application data 💾 NEW
│   └── model_performance_metrics.json # Stored metrics
├── config.yaml             # Bootstrap agents (AdminAI provider/model optional) ✅
├── helperfiles/            # Project planning & tracking 📝 ✅
│   ├── PROJECT_PLAN.md
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md
├── logs/                   # Application log files (timestamped) 📝 ✅
│   └── app_YYYYMMDD_HHMMSS.log
├── sandboxes/              # Agent work directories 📁 ✅
│   └── agent_X/
├── src/                    # Source code 🐍
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class ✅
│   │   ├── cycle_handler.py # Handles agent cycle, retries, triggers failover, reports metrics ✨ UPDATED
│   │   ├── interaction_handler.py # Processes tool signals ✅
│   │   ├── manager.py      # AgentManager (orchestration, Admin AI auto-select, failover logic) 🧑‍💼 ✨ UPDATED
│   │   ├── performance_tracker.py # Tracks model performance metrics 📊 NEW
│   │   ├── prompt_utils.py # Prompt templates ✅
│   │   ├── session_manager.py # Handles save/load state ✅
│   │   └── state_manager.py   # Handles team state ✅
│   ├── api/
│   │   ├── __init__.py
│   │   ├── http_routes.py  # API endpoints (Static config CRUD removed from UI scope) ✨ UPDATED (Implicitly)
│   │   └── websocket_manager.py # Handles WS connections (Override logic removed) ✨ UPDATED
│   ├── config/
│   │   ├── __init__.py
│   │   ├── config_manager.py # Handles config.yaml read ✅
│   │   ├── model_registry.py # Handles provider/model discovery & filtering 📚 ✅
│   │   └── settings.py     # Loads .env, instantiates registry ✅
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
│       └── app.js          # Frontend logic (Override/Static Config UI logic removed) ✨ UPDATED
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # Main UI page (Override/Static Config UI elements removed) ✨ UPDATED
├── .env.example            # Example environment variables ✅
├── .gitignore              # Ensure logs/, projects/, sandboxes/, data/ are added
├── LICENSE                 # Project License (MIT) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies ✅```
*(Note: Ensure `data/` is added to your `.gitignore` file)*

## ⚙️ Installation

1.  Prerequisites: Python 3.9+, Git, (Optional) Local Ollama/LiteLLM.
2.  Clone & `cd TrippleEffect`.
3.  Setup Venv & `pip install -r requirements.txt`.
4.  Configure Environment (`.env`): <!-- Updated -->
    *   Copy `.env.example` to `.env`.
    *   Add required **API keys**.
    *   Set **Base URLs** *only* if needed (defaults/discovery used otherwise).
    *   Set `MODEL_TIER` (`FREE` or `ALL`).
    *   Set `GITHUB_ACCESS_TOKEN` if needed.
5.  Review `config.yaml` for Admin AI persona/prompt (provider/model usually left commented for auto-selection).
6.  Create Directories: `mkdir logs data`

## ▶️ Running the Application

```bash
python -m src.main
```
*   Server starts. **Checks provider reachability, discovers models, filters, selects Admin AI model, loads/creates metrics file.** Check logs.
*   Access UI: `http://localhost:8000`.

## 🖱️ Usage

1.  Open UI. Check logs for discovery/selection details.
2.  Enter task.
3.  `Admin AI` plans, creates agents using available models.
4.  Agents execute. If errors occur, **automatic failover** attempts model switches (check logs/status). If failover limit reached, agent enters `ERROR` state.
5.  `Admin AI` coordinates results.
6.  Use Session view to save/load state. Performance metrics saved on shutdown.

## 🛠️ Development

*   Follow standard practices. Keep helper files updated.
*   Configure system via `.env`. Modify `config.yaml` mainly for Admin AI base prompt/persona or to *override* auto-selection.

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
