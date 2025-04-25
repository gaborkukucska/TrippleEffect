<!-- # START OF FILE README.md -->
<!-- # IT IS CRITICAL THAT ALL AIs and LLMs FOLLOW THE DEVELOPMENT INSTRUCTIONS IN THE DEVELOPMENT_RULES.md FILE WHEN FURTER DEVELOPING THIS FRAMEWORK!!! -->
# TrippleEffect Multi-Agent Framework

**Version:** 2.24 <!-- Updated Version -->

**TrippleEffect** is an asynchronous, collaborative multi-agent framework built with Python, FastAPI, and WebSockets. It features a central **Admin AI** that initiates projects and a dedicated **Project Manager** agent per session that handles detailed task tracking and team coordination. This framework is predominantly developed by various LLMs guided by Gabby.

## Quick Start (using scripts)

For a faster setup, you can use the provided shell scripts (ensure they are executable: `chmod +x setup.sh run.sh`):

1.  **Run Setup:** `./setup.sh` (This usually creates the environment, installs dependencies, and copies `.env.example`).
2.  **Configure:** Edit the created `.env` file with your API keys (OpenAI, OpenRouter, GitHub PAT, Tavily).
3.  **Run:** `./run.sh` (This typically activates the environment and starts the application using `python -m src.main`).
4.  **Access UI:** Open your browser to `http://localhost:8000`.

*(See the detailed "Setup and Running" section below for manual steps and configuration options.)*

## Core Concepts

*   **Admin AI Initiator:** The central agent (`admin_ai`) interacts with the user, understands requests, initiates projects, creates initial plans, and delegates execution to a Project Manager agent.
*   **Project Manager Agent:** Automatically created per project/session (`pm_{project}_{session}`), this agent receives the plan from Admin AI, uses the `ProjectManagementTool` (backed by `tasklib`) to create and track tasks (including assignees), pings agents for updates, and reports progress/completion back to Admin AI.
*   **Dynamic Agent Management:** Admin AI uses `ManageTeamTool` to create teams and worker agents as per the plan. The Project Manager agent is added to the team by Admin AI.
*   **Intelligent Model Handling:**
    *   **Discovery:** Automatically finds reachable LLM providers (Ollama, OpenRouter, OpenAI) and available models at startup.
    *   **Filtering:** Filters discovered models based on the `MODEL_TIER` setting (`.env`).
    *   **Auto-Selection:** Automatically selects the best model for Admin AI (at startup) and dynamic agents (at creation if not specified) based on performance metrics and availability.
    *   **Failover:** Automatic API key cycling and model/provider failover (Local -> Free -> Paid tiers) on persistent errors during generation.
    *   **Performance Tracking:** Records success rate and latency per model, persisting data (`data/model_performance_metrics.json`).
*   **Tool-Based Interaction:** Agents use tools via an **XML format**. Tools include file system operations, inter-agent messaging, team management, web search, GitHub interaction, system information retrieval, and knowledge base operations.
    *   **Context Management:** Standardized instructions are injected, agents are guided to use file operations for large outputs. Admin AI receives current time context. **(Update v2.23):** Admin AI system prompt assembly streamlined to remove redundant static tool descriptions and model lists.
    *   **Persistence:** Session state (agents, teams, histories) can be saved/loaded (filesystem). Interactions and knowledge are logged to a database (`data/trippleeffect_memory.db`).
*   **Communication Layer Separation (UI):** The user interface visually separates direct User<->Admin AI interaction from internal Admin AI<->Agent communication and system events.

## Features

*   **Asynchronous Backend:** Built with FastAPI and `asyncio`.
*   **WebSocket Communication:** Real-time updates via WebSockets.
*   **Dynamic Agent/Team Creation:** Manage agents and teams on the fly using `ManageTeamTool`.
*   **Configurable Model Selection:**
    *   Dynamic discovery of providers/models (Ollama, OpenRouter, OpenAI).
    *   Filtering based on `MODEL_TIER` (.env: `FREE` or `ALL`).
    *   Automatic model selection for Admin AI and dynamic agents using performance data.
*   **Robust Error Handling:**
    *   Automatic retries for transient LLM API errors.
    *   Multi-key support and key cycling for providers (`PROVIDER_API_KEY_N` in `.env`).
    *   Automatic failover to different models/providers based on tiers (Local -> Free -> Paid).
    *   Key quarantining on persistent auth/rate limit errors.
*   **Performance Tracking:** Monitors success rate and latency per model, saved to `data/model_performance_metrics.json`.
*   **Structured Admin AI Workflow:** Mandatory planning phase (`<plan>` tag) before execution. Strong emphasis on **Knowledge Base search before planning**.
    *   **XML Tooling:** Agents request tool use via XML format. Available tools:
        *   `FileSystemTool`: Read, Write, List, Mkdir, Delete (File/Empty Dir), Find/Replace in private sandbox or shared workspace.
        *   `GitHubTool`: List Repos, List Files (Recursive), Read File content using PAT.
        *   `ManageTeamTool`: Create/Delete Agents/Teams, Assign Agents, List Agents/Teams, Get Agent Details.
        *   `SendMessageTool`: Communicate between agents within a team or with Admin AI (using exact agent IDs).
        *   `WebSearchTool`: Search the web (uses Tavily API if configured, falls back to DDG scraping).
            *   `SystemHelpTool`: Get current time (UTC), Search application logs, **Get detailed tool usage info (`get_tool_info`)**.
            *   `KnowledgeBaseTool`: Save/Search distilled knowledge in the database.
            *   `ProjectManagementTool`: Add, list, modify, and complete project tasks (uses `tasklib` backend per session). Used primarily by the Project Manager agent.
    *   **On-Demand Tool Help:** Implemented `get_detailed_usage()` in tools and `get_tool_info` action in `SystemHelpTool` for dynamic help retrieval (full transition planned for Phase 27+).
*   **Session Persistence:** Save and load agent states, histories, team structures, and **project task data** (filesystem, including `tasklib` data).
*   **Database Backend (SQLite):**
    *   Logs user, agent, tool, and system interactions.
    *   Stores long-term knowledge summaries via `KnowledgeBaseTool`.
*   **Refined Web UI (Phase 22):**
    *   Separated view for User <-> Admin AI chat (`Chat` view).
    *   Dedicated view for internal Admin AI <-> Agent communication, tool usage, and system status updates (`Internal Comms` view).
    *   Improved message chunk grouping for concurrent streams.
    *   Increased message history limit in Internal Comms view.
    *   Session management interface.
    *   Static configuration viewer.
*   **Sandboxing:** Agents operate within dedicated sandbox directories or a shared session workspace.
*   **Context Optimization:** Agents guided to use files for large outputs. Separate, concise system prompt variant for local Admin AI models.
*   **Admin AI Time Context:** Current UTC time is injected into Admin AI prompts.
*   **Ollama Proxy (Optional):** Integrates an optional Node.js proxy for Ollama to potentially stabilize streaming.
*   **Ollama Integration:** Fixed response streaming issues, improved network discovery (`LOCAL_API_DISCOVERY_SUBNETS="auto"`), addressed initialization errors.

## Architecture Overview

```mermaid
graph TD
    subgraph UserLayer [Layer 1: User Interface] %% Updated P24
        USER[👨‍💻 Human User]
        subgraph Frontend [🌐 Human UI (Web) - Refactored P22]
             direction LR
             UI_CHAT["**Chat View** <br> (User <-> Admin)✅"]
             UI_INTERNAL["**Internal Comms View** <br>(Admin <-> Agents, Tools, Status)✅"] %% MODIFIED View Name
             UI_SESSIONS["Session View✅"]
             UI_CONFIG["Config View✅"]
             %% Removed Logs View
        end
    end

    subgraph CoreInstance [Layer 2: Local TrippleEffect Instance]
        direction TB
        BackendApp["🚀 FastAPI Backend✅"]

        subgraph Managers ["Management & Orchestration"]
            direction LR
            AGENT_MANAGER["🧑‍💼 Agent Manager ✅"]
            DB_MANAGER["**📦 Database Manager ✅**"]
            STATE_MANAGER["📝 AgentStateManager ✅"]
            SESSION_MANAGER["💾 SessionManager (FS) ✅"]
            PROVIDER_KEY_MGR["🔑 ProviderKeyManager ✅"]
            MODEL_REGISTRY["📚 ModelRegistry ✅"]
            PERF_TRACKER["📊 PerformanceTracker ✅"]
        end

        subgraph Handlers ["Core Logic Handlers"]
            direction LR
            CYCLE_HANDLER["🔄 AgentCycleHandler ✅<br>(Known agent logic issues: looping, placeholder replacement, targeting - See Phase 24)"] %% ANNOTATION
            INTERACTION_HANDLER["🤝 InteractionHandler ✅"]
            FAILOVER_HANDLER["💥 FailoverHandler (Func) ✅"]
        end

        subgraph CoreAgents ["Core & Dynamic Agents"] %% Updated P24
             ADMIN_AI["🤖 Admin AI Agent <br>(Initiator/User Interface)✅"]
             PM_AGENT["🤖 Project Manager Agent <br>(Per Session, Auto-Created)✅"]
             subgraph DynamicTeam [Dynamic Team Example]
                direction LR
                 AGENT_DYN_1["🤖 Worker Agent 1"]
                 AGENT_DYN_N["🤖 ... Worker Agent N"]
             end
        end

        subgraph InstanceTools ["🛠️ Tools"]
            TOOL_EXECUTOR["Executor"]
            TOOL_FS["FileSystem ✅"]
            TOOL_SENDMSG["SendMessage (Local) ✅"]
            TOOL_MANAGE_TEAM["ManageTeam ✅"]
            TOOL_GITHUB["GitHub ✅"]
            TOOL_WEBSEARCH["WebSearch ✅"]
            TOOL_SYSTEMHELP["SystemHelp ✅"]
            TOOL_KNOWLEDGE["**KnowledgeBase ✅**"]
            TOOL_PROJECT_MGMT["**ProjectManagement (Tasklib) ✅**"] %% Added P24
        end

        subgraph InstanceData ["Local Instance Data"] %% Updated P24
            SANDBOXES["📁 Sandboxes"]
            SHARED_WORKSPACE["🌐 Shared Workspace"]
            PROJECT_SESSIONS["💾 Project/Session Files"]
            LOG_FILES["📄 Log Files <br>(Backend Only)"] %% ANNOTATION
            CONFIG_FILES["⚙️ Config (yaml, json, env)"]
            METRICS_FILE["📄 Metrics File"]
            QUARANTINE_FILE["📄 Key Quarantine File"]
            SQLITE_DB["**💾 SQLite DB <br>(Interactions, Knowledge)**"]
            TASKLIB_DATA["**📊 Tasklib Data <br>(Per Session)**"] %% Added P24
        end
    end

    subgraph ExternalServices [External Services]
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        TAVILY_API["☁️ Tavily API"]
        OLLAMA_SVC["⚙️ Local Ollama Svc"]
        OLLAMA_PROXY_SVC["🔌 Optional Ollama Proxy"]
        GITHUB_API["☁️ GitHub API"]
    end

    %% --- Layer 3 (Future) ---
    subgraph FederatedLayer [Layer 3: Federated Instances (Future - Phase 26+)]
         ExternalInstance["🏢 External TrippleEffect Instance"]
         ExternalAdminAI["🤖 External Admin AI"]
         ExternalDB["💾 External Instance DB"]
    end

    %% --- Connections ---
    USER -- HTTP/WS --> Frontend;
    Frontend -- HTTP/WS --> BackendApp;

    BackendApp -- Manages --> AGENT_MANAGER;
    BackendApp -- Manages --> DB_MANAGER; %% Via singleton / lifespan

    AGENT_MANAGER -- Uses --> DB_MANAGER;
    AGENT_MANAGER -- Uses --> STATE_MANAGER;
    AGENT_MANAGER -- Uses --> SESSION_MANAGER;
    AGENT_MANAGER -- Uses --> PROVIDER_KEY_MGR;
    AGENT_MANAGER -- Uses --> MODEL_REGISTRY;
    AGENT_MANAGER -- Uses --> PERF_TRACKER;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> INTERACTION_HANDLER;
    AGENT_MANAGER -- Triggers --> FAILOVER_HANDLER;
    AGENT_MANAGER -- Manages --> CoreAgents;
    AGENT_MANAGER -- Creates --> PM_AGENT; %% Added P24

    CYCLE_HANDLER -- Runs --> CoreAgents;
    CYCLE_HANDLER -- Logs to --> DB_MANAGER;
    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    INTERACTION_HANDLER -- Updates --> STATE_MANAGER;
    INTERACTION_HANDLER -- Routes Msg --> CoreAgents; %% Includes Admin <-> PM

    TOOL_EXECUTOR -- Executes --> InstanceTools;
    InstanceTools -- Access --> InstanceData;
    InstanceTools -- Interact With --> ExternalServices;
    TOOL_KNOWLEDGE -- Uses --> DB_MANAGER;

    %% Data Persistence %% Updated P24
    SESSION_MANAGER -- R/W --> PROJECT_SESSIONS;
    TOOL_PROJECT_MGMT -- R/W --> TASKLIB_DATA; %% Added P24
    DB_MANAGER -- R/W --> SQLITE_DB;
    PERF_TRACKER -- R/W --> METRICS_FILE;
    PROVIDER_KEY_MGR -- R/W --> QUARANTINE_FILE;
    BackendApp -- Writes --> LOG_FILES;


    %% External Services Connections
    MODEL_REGISTRY -- Discovers --> LLM_API_SVC;
    MODEL_REGISTRY -- Discovers --> OLLAMA_SVC;
    MODEL_REGISTRY -- Discovers --> OLLAMA_PROXY_SVC;
    CoreAgents -- via LLM Providers --> LLM_API_SVC;
    CoreAgents -- via LLM Providers --> OLLAMA_SVC;
    CoreAgents -- via LLM Providers --> OLLAMA_PROXY_SVC;
    TOOL_WEBSEARCH -- Calls --> TAVILY_API;
    TOOL_GITHUB -- Calls --> GITHUB_API;

    %% Federated Layer Connections (Dashed lines for future)
    BackendApp -.->|External API Calls| ExternalInstance;
    ExternalInstance -.->|Callbacks / API Calls| BackendApp;

```

## Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
*   **Task Management:** `tasklib` (Python Taskwarrior library) %% Added P24
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
*   **Model Discovery & Management:** Custom `ModelRegistry` class
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge), Taskwarrior files (project tasks via `tasklib`) %% Updated P24
*   **Optional Proxy:** Node.js, Express, node-fetch
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Logging:** Standard library `logging`

## Setup and Running (Detailed)

1.  **Prerequisites:**
    *   Python 3.9+
    *   Node.js and npm (only if using the optional Ollama proxy)
    *   Access to LLM APIs (OpenAI, OpenRouter) and/or a running local Ollama instance.

2.  **Clone the repository:**
    ```bash
    git clone https://github.com/gaborkukucska/TrippleEffect.git # Replace with actual repo URL
    cd TrippleEffect
    ```

3.  **Set up Python Environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

4.  **Configure Environment:**
    *   Copy `.env.example` to `.env`.
    *   Edit `.env` and add your API keys (OpenAI, OpenRouter, GitHub PAT, Tavily API Key).
    *   Set `MODEL_TIER` (`FREE` or `ALL`).
    *   Configure `OLLAMA_BASE_URL` if your Ollama instance is not at `http://localhost:11434`.
    *   Configure Ollama proxy settings (`USE_OLLAMA_PROXY`, `OLLAMA_PROXY_PORT`) if needed.
    *   **Note:** While local LLMs (via Ollama) are supported, smaller models may currently exhibit reliability issues with complex instructions or tool use. For more consistent results, using a robust external provider via OpenRouter (configured in `.env`) is recommended.

5.  **Configure Bootstrap Agents (Optional):**
    *   Edit `config.yaml` to define any bootstrap agents beyond the default Admin AI.
    *   You can optionally specify a provider/model for Admin AI here, otherwise it will be auto-selected.

6.  **Install Proxy Dependencies (Optional):**
    ```bash
    cd ollama-proxy
    npm install
    cd ..
    ```

7.  **Run the Application:**
    ```bash
    # Option 1: Use the run script (if available and configured)
    # chmod +x run.sh
    # ./run.sh

    # Option 2: Run directly using Python
    python -m src.main
    ```
    *(Alternatively, for development with auto-reload, use `uvicorn src.main:app --reload --port 8000`, but be aware reload might interfere with proxy management or agent state.)*

8.  **Access the UI:** Open your web browser to `http://localhost:8000`.

## Development Status

*   **Current Version:** 2.24 <!-- Updated Version -->
*   **Completed Phases:** 1-23 (Core, Dynamic Agents, Failover, Key Mgmt, Proxy, XML Tools, Auto-Selection, Planning Phase, Context Optimization, Tool Enhancements, System Help, Memory Foundation (DB), UI Layer Refactor & KB Prompt Refinement, **Project Manager Agent & Tasklib Integration**). **Recent Fixes/Enhancements:** Ollama integration fixes, enhanced network discovery, local model prompt variant, initial on-demand tool help mechanism, PM agent workflow, `tasklib` integration.
*   **Current Phase (24):** Advanced Memory & Learning (incl. fixing known agent logic issues - looping, placeholders, targeting).
*   **Future Plans:** Proactive Behavior (Phase 25), Federated Communication (Phase 26+), New Admin tools, LiteLLM provider, advanced collaboration, resource limits, DB/Vector Stores, **Full transition to on-demand tool help** (removing static descriptions from prompts - Phase 27+).

See `helperfiles/PROJECT_PLAN.md` for detailed phase information.

## Contributing

Contributions are welcome! Please follow standard fork-and-pull-request procedures. Adhere to the development rules outlined in `helperfiles/DEVELOPMENT_RULES.md`.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

## Acknowledgements

*   Inspired by AutoGen, CrewAI, and other multi-agent frameworks.
*   Uses the powerful libraries FastAPI, Pydantic, SQLAlchemy, and the OpenAI Python client.
<!-- # IT IS CRITICAL THAT ALL AIs and LLMs FOLLOW THE DEVELOPMENT INSTRUCTIONS IN THE DEVELOPMENT_RULES.md FILE WHEN FURTER DEVELOPING THIS FRAMEWORK!!! -->
