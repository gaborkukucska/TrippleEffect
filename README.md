<!-- # START OF FILE README.md -->
# TrippleEffect Multi-Agent Framework

**Version:** 2.20 <!-- Updated Version -->

**TrippleEffect** is an asynchronous, collaborative multi-agent framework built with Python, FastAPI, and WebSockets. It features a central **Admin AI** that orchestrates tasks by dynamically creating and managing specialized agents.

![TrippleEffect UI Screenshot Placeholder](https://via.placeholder.com/800x400.png?text=TrippleEffect+UI+Screenshot)
*(Replace with actual screenshot)*

## Core Concepts

*   **Admin AI Orchestrator:** The central agent (`admin_ai`) coordinates tasks, manages agent teams, and interacts with the human user. It follows a structured **Planning -> Execution -> Coordination** workflow.
*   **Dynamic Agent Management:** Create, delete, and manage agents and teams *in memory* via Admin AI tool calls (`ManageTeamTool`). No restarts needed for dynamic changes.
*   **Intelligent Model Handling:**
    *   **Discovery:** Automatically finds reachable LLM providers (Ollama, OpenRouter, OpenAI) and available models at startup.
    *   **Filtering:** Filters discovered models based on the `MODEL_TIER` setting (`.env`).
    *   **Auto-Selection:** Automatically selects the best model for Admin AI (at startup) and dynamic agents (at creation if not specified) based on performance metrics and availability.
    *   **Failover:** Automatic API key cycling and model/provider failover (Local -> Free -> Paid tiers) on persistent errors during generation.
    *   **Performance Tracking:** Records success rate and latency per model, persisting data.
*   **Tool-Based Interaction:** Agents use tools via an **XML format**. Tools include file system operations, inter-agent messaging, team management, web search, GitHub interaction, and system information retrieval.
*   **Context Management:** Standardized instructions are injected, agents are guided to use file operations for large outputs, and Admin AI receives current time context.
*   **Persistence:** Session state (agents, teams, histories) can be saved and loaded.

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
*   **Structured Admin AI Workflow:** Mandatory planning phase (`<plan>` tag) before execution.
*   **XML Tooling:** Agents request tool use via XML format. Available tools:
    *   `FileSystemTool`: Read, Write, List, **Mkdir**, **Delete** (File/Empty Dir), Find/Replace in private sandbox or shared workspace.
    *   `GitHubTool`: List Repos, List Files (**Recursive**), Read File content using PAT.
    *   `ManageTeamTool`: Create/Delete Agents/Teams, Assign Agents, List Agents/Teams, **Get Agent Details**.
    *   `SendMessageTool`: Communicate between agents within a team or with Admin AI.
    *   `WebSearchTool`: Search the web (**uses Tavily API if configured, falls back to DDG scraping**).
    *   `SystemHelpTool`: (**NEW**) Get current time (UTC), Search application logs.
*   **Session Persistence:** Save and load agent states, histories, and team structures.
*   **Basic Web UI:** Interface to interact with Admin AI, view agent status, logs, manage static config, and manage sessions.
*   **Sandboxing:** Agents operate within dedicated sandbox directories or a shared session workspace.
*   **Context Optimization:** Agents guided to use files for large outputs.
*   **Admin AI Time Context:** Current UTC time is injected into Admin AI prompts.
*   **Ollama Proxy (Optional):** Integrates an optional Node.js proxy for Ollama to potentially stabilize streaming.

## Architecture Overview

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
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ Uses ModelRegistry ✅<br>+ Uses ProviderKeyManager ✅<br>+ Auto-Selects Admin AI Model ✅<br>+ **Handles Auto Model Selection (Dyn) ✅**<br>+ Handles Key/Model Failover ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"] %% Updated
        PROVIDER_KEY_MGR["🔑 Provider Key Manager <br>+ Manages Keys ✅<br>+ Handles Quarantine ✅<br>+ Saves/Loads State ✅"]
        MODEL_REGISTRY["📚 Model Registry✅"]
        PERF_TRACKER["📊 Performance Tracker<br>+ Records Metrics ✅<br>+ Saves/Loads Metrics ✅"]
        CYCLE_HANDLER["🔄 Agent Cycle Handler<br>+ Handles Retries ✅<br>+ Triggers Key/Model Failover ✅<br>+ Reports Metrics ✅<br>+ Handles Tool Results ✅<br>+ **Handles Plan Approval ✅**<br>+ **Injects Time Context (Admin) ✅**"] %% Updated
        INTERACTION_HANDLER["🤝 Interaction Handler <br>+ **Robust SendMessage Target ✅**<br>+ **Handles Get Agent Details ✅**"] %% Updated
        STATE_MANAGER["📝 AgentStateManager <br>+ **Idempotent Create Team ✅**"] %% Updated
        SESSION_MANAGER["💾 SessionManager ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>+ **Planning Phase Logic** ✅"] %% Updated
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers"] %% Instantiated by AGENT_MANAGER
             PROVIDER_OR["🔌 OpenRouter"]
             PROVIDER_OLLAMA["🔌 Ollama"]
             PROVIDER_OPENAI["🔌 OpenAI"]
             PROVIDER_LITELLM["🔌 LiteLLM (TBD)"]
         end

         subgraph Tools ["🛠️ Tools (XML Format)"]
             TOOL_EXECUTOR["Executor"]
             TOOL_FS["FileSystem <br>+ Find/Replace ✅<br>+ **Mkdir/Delete ✅**"] %% Updated
             TOOL_SENDMSG["SendMessage"]
             TOOL_MANAGE_TEAM["ManageTeam <br>+ Optional Provider/Model ✅<br>+ **Get Details ✅**"] %% Updated
             TOOL_GITHUB["GitHub<br>+ **Recursive List ✅**"] %% Updated
             TOOL_WEBSEARCH["WebSearch<br>+ **Tavily API ✅**<br>+ DDG Fallback ✅"] %% Updated
             TOOL_SYSTEMHELP["**SystemHelp ✅**<br>+ Get Time<br>+ Search Logs"] %% Added
         end

         SANDBOXES["📁 Sandboxes ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage ✅"]
         SHARED_WORKSPACE["🌐 Shared Workspace ✅"]
         LOG_FILES["📄 Log Files ✅"]
         METRICS_FILE["📄 Metrics File ✅"]
         QUARANTINE_FILE["📄 Key Quarantine File ✅"]
         DATA_DIR["📁 Data Dir ✅"]
    end

    subgraph External %% Status Implicit
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        TAVILY_API["☁️ Tavily API"] %% Added
        OLLAMA_SVC["⚙️ Local Ollama Svc"]
        OLLAMA_PROXY_SVC["🔌 Node.js Ollama Proxy (Optional)"]
        LITELLM_SVC["⚙️ Local LiteLLM Svc"]
        CONFIG_YAML["⚙️ config.yaml"]
        PROMPTS_JSON["📜 prompts.json <br>(XML Format)<br>(Planning Phase)<br>(File Usage Guidance)<br>+ **SystemHelpTool Info**"] %% Updated
        DOT_ENV[".env File <br>(Multi-Key Support)<br>(Proxy Config)<br>+ **Tavily Key**"] %% Updated
    end

    %% --- Connections ---
    USER -- Interacts --> Frontend;
    Frontend -- HTTP/WebSocket --> Backend;

    FASTAPI -- Manages --> AGENT_MANAGER;
    FASTAPI -- Manages --> MODEL_REGISTRY;
    FASTAPI -- Manages --> PERF_TRACKER;
    FASTAPI -- Manages --> PROVIDER_KEY_MGR;
    FASTAPI -- Manages --> OLLAMA_PROXY_SVC;

    AGENT_MANAGER -- Uses --> MODEL_REGISTRY;
    AGENT_MANAGER -- Uses --> PROVIDER_KEY_MGR;
    AGENT_MANAGER -- Uses --> PERF_TRACKER;
    AGENT_MANAGER -- Instantiates --> LLM_Providers;
    AGENT_MANAGER -- Manages --> Agents;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates --> SESSION_MANAGER;
    AGENT_MANAGER -- Handles Failover --> AGENT_MANAGER;
    AGENT_MANAGER -- Loads Prompts via --> External;

    MODEL_REGISTRY -- Discovers --> External;
    PROVIDER_KEY_MGR -- Reads/Writes --> QUARANTINE_FILE;
    PROVIDER_KEY_MGR -- Creates --> DATA_DIR;
    PERF_TRACKER -- Reads/Writes --> METRICS_FILE;
    PERF_TRACKER -- Creates --> DATA_DIR;

    CYCLE_HANDLER -- Runs --> Agents;
    CYCLE_HANDLER -- Delegates --> INTERACTION_HANDLER;
    CYCLE_HANDLER -- Reports Metrics --> PERF_TRACKER;
    CYCLE_HANDLER -- Triggers Failover --> AGENT_MANAGER;

    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    TOOL_EXECUTOR -- Executes --> Tools;

    LLM_Providers -- Calls --> OLLAMA_PROXY_SVC;
    LLM_Providers -- Calls --> LLM_API_SVC;
    LLM_Providers -- Calls --> OLLAMA_SVC;
    OLLAMA_PROXY_SVC -- Forwards to --> OLLAMA_SVC;

    %% Tool connections
    TOOL_WEBSEARCH -- Uses --> TAVILY_API; %% Added
    TOOL_WEBSEARCH -- Uses --> External; %% DDG Fallback
    TOOL_GITHUB -- Uses --> LLM_API_SVC; %% GitHub API
    TOOL_SYSTEMHELP -- Reads --> LOG_FILES;

    Backend -- "Writes Logs" --> LOG_FILES;
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;
```

## Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`)
*   **Tooling:** `BeautifulSoup4` (Web Search fallback), `tavily-python` (Web Search API) <!-- Added -->
*   **Persistence:** JSON, File System
*   **Optional Proxy:** Node.js, Express, node-fetch

## Setup and Running

1.  **Prerequisites:**
    *   Python 3.9+
    *   Node.js and npm (only if using the optional Ollama proxy)
    *   Access to LLM APIs (OpenAI, OpenRouter) and/or a running local Ollama instance.

2.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/TrippleEffect.git # Replace with actual repo URL
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
    *   Edit `.env` and add your API keys (OpenAI, OpenRouter, GitHub PAT, **Tavily API Key**).
    *   Set `MODEL_TIER` (`FREE` or `ALL`).
    *   Configure `OLLAMA_BASE_URL` if your Ollama instance is not at `http://localhost:11434`.
    *   Configure Ollama proxy settings (`USE_OLLAMA_PROXY`, `OLLAMA_PROXY_PORT`) if needed.

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
    # Make setup script executable (if needed)
    # chmod +x setup.sh
    # ./setup.sh # Or run the command directly:
    python -m src.main
    ```
    *(Alternatively, for development with auto-reload, use `uvicorn src.main:app --reload --port 8000`, but be aware reload might interfere with proxy management or agent state.)*

8.  **Access the UI:** Open your web browser to `http://localhost:8000`.

## Development Status

*   **Current Version:** 2.20
*   **Completed Phases:** 1-20 (Core, Dynamic Agents, Failover, Key Mgmt, Proxy, XML Tools, Auto-Selection, Planning Phase, Context Optimization, **Tool Enhancements, System Help**)
*   **Next Phase (21):** Few-Shot Prompting & Performance Ranking.
*   **Future Plans:** New Admin tools, LiteLLM provider, advanced collaboration, resource limits, DB/Vector Stores.

See `helperfiles/PROJECT_PLAN.md` for detailed phase information.

## Contributing

Contributions are welcome! Please follow standard fork-and-pull-request procedures. Adhere to the development rules outlined in `helperfiles/DEVELOPMENT_RULES.md`.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

## Acknowledgements

*   Inspired by AutoGen, CrewAI, and other multi-agent frameworks.
*   Uses the powerful libraries FastAPI, Pydantic, and the OpenAI Python client.
