<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.20 <!-- Updated Version -->
**Date:** 2025-04-19 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator. *(Completed)*
*   Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
*   Inject standardized context into dynamic agents' system prompts. *(Completed)*
*   Empower agents to communicate and collaborate autonomously. *(Completed)*
*   Implement **session persistence**. *(Completed)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI`. *(Completed)*
*   **Dynamically discover reachable providers** and **available models**. *(Completed in P12)*
*   **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed in P12)*
*   **Automatically select the Admin AI's provider/model at startup**. *(Completed in P12)*
*   Implement provider availability checks and **automatic retries** for transient errors. *(Completed)*
*   **Implement automatic model/provider failover** for agents experiencing persistent errors during generation, following preference tiers (Local -> Free -> Paid). *(Completed in P13)*
*   **Implement basic performance metric tracking** (success rate, latency) per model, persisting data. *(Completed in P13)*
*   Implement a **Human User Interface** reflecting system state. *(Simplified in P13)*
*   Utilize **XML-based tool calling** with **sequential execution**. *(Restored & Refined in P16)*
*   Allow tool use in sandboxed or **shared workspaces**. *(Completed in P11, Enhanced in P18)*
*   Implement **automatic project/session context setting**. *(Partially Completed in P11)*
*   Implement **automatic model selection** for dynamic agents if not specified by Admin AI. *(Completed in P17)*
*   Implement **robust agent ID/persona handling** for `send_message`. *(Completed in P17)*
*   Implement **structured planning phase** for Admin AI. *(Completed in P17b)*
*   Enhance `FileSystemTool` with **find/replace** capability. *(Completed in P18)*
*   Optimize for **context length** by encouraging file usage for large outputs. *(Completed in P18)*
*   Enhance `FileSystemTool` with **directory creation/deletion**. *(Completed in P20)*
*   Enhance `GitHubTool` with **recursive listing**. *(Completed in P20)*
*   Enhance `ManageTeamTool` with **agent detail retrieval**. *(Completed in P20)*
*   Make `WebSearchTool` more robust with **API fallback**. *(Completed in P20)*
*   Implement `SystemHelpTool` for Admin AI **time awareness and log searching**. *(Completed in P20)*
*   Inject **current time context** into Admin AI LLM calls. *(Completed in P20)*
*   **(Future Goals)** Enhance Admin AI planning (few-shot examples P21), **use tracked performance metrics for ranking** (P21), implement new Admin AI tools, resource management, advanced collaboration patterns, database integration, formal project/task management.

## 2. Scope

**In Scope (Completed up to Phase 20):**

*   **Core Backend & Agent Core:** Base functionality. *(Completed)*
*   **Admin AI Agent:** Core logic. *(Completed)*
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover. *(Completed)*
*   **State & Session Management:** Team state, save/load. *(Completed)*
*   **Model Registry (`ModelRegistry`):** Provider/model discovery, filtering. *(Completed in P12)*
*   **Automatic Admin AI Model Selection:** Based on discovery/preferences. *(Completed in P12)*
*   **Performance Tracking (`ModelPerformanceTracker`):** Tracks success/failure/duration per model, saves to JSON. *(Completed in P13)*
*   **Automatic Agent Failover:** Agent switches provider/model on persistent errors based on tiers (Local->Free->Paid), up to `MAX_FAILOVER_ATTEMPTS`. *(Completed in P13)*
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI tool calls. *(Completed)*
*   **Tooling:** Core tools implemented, using **XML format**. *(Updated in P20)*
    *   `FileSystemTool`: Read/Write/List/FindReplace/**Mkdir**/**Delete**(File/EmptyDir).
    *   `GitHubTool`: List Repos/Files (**Recursive List**), Read File.
    *   `ManageTeamTool`: Agent/Team CRUD, Assign, List, **Get Details**. (Create Agent model/provider optional).
    *   `WebSearchTool`: Search web (**Tavily API w/ DDG Scraping Fallback**).
    *   `SendMessageTool`: Send message to agent (ID/Persona).
    *   **`SystemHelpTool`:** Get current time, Search logs.
*   **Configuration:** `config.yaml` (Admin AI optional), `.env` (keys, URLs, tier, proxy, **Tavily Key**), `prompts.json` (XML tools, plan phase, file usage guidance, **SystemHelpTool info**). *(Updated in P20)*
*   **Session Persistence:** Save/Load state. *(Completed)*
*   **Human UI:** Dynamic updates, Session management, Conversation view. *(Simplified in P13)*
*   **WebSocket Communication:** Real-time updates. *(Completed)*
*   **Sandboxing & Shared Workspace:** Implemented. *(Completed in P11, Usage refined in P18)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover. *(Completed)*
*   **Helper Files & Logging:** Maintained. *(Ongoing)*
*   **Ollama Proxy Integration:** Optional, managed proxy. *(Completed in P15)*
*   **XML Tooling Restoration & Prompt Refinements:** Addressed tool usage issues. *(Completed in P16)*
*   **Automatic Dynamic Agent Model Selection:** Framework selects model if Admin AI omits. *(Completed in P17)*
*   **Robust `send_message` Targeting:** Handles persona as fallback for target ID. *(Completed in P17)*
*   **Structured Planning Phase:** Admin AI outputs plan before execution. *(Completed in P17b)*
*   **Context Optimization:** Prompts encourage file usage for large outputs. *(Completed in P18)*
*   **Admin AI Time Context:** Current time injected into Admin AI LLM calls. *(Completed in P20)*

**Out of Scope (Deferred to Future Phases 21+):**

*   **Phase 21: Few-Shot Prompting & Performance Ranking.** (Add examples to prompts, implement ranking algorithm).
*   **Phase 22+:** New Admin AI Tools (Get Logs, Qualitative Feedback), LiteLLM Provider, Advanced Collaboration, Resource limiting, DB/Vector Store, GeUI, etc.

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:**
    *   `openai` library (used by multiple providers)
    *   `aiohttp` (used internally by Ollama provider, GitHub tool, Web Search tool fallback)
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:**
    *   YAML (`PyYAML`) for bootstrap agent definitions (`config.yaml`)
    *   `.env` files (`python-dotenv`) for secrets, URLs, and settings like `MODEL_TIER`, proxy config, **TAVILY_API_KEY**.
    *   JSON (`prompts.json`) for standard framework/agent instructions (using XML tool format, planning phase, **SystemHelpTool**). <!-- Updated -->
*   **Model Discovery & Management:** Custom `ModelRegistry` class
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (saving to JSON)
*   **Data Handling/Validation:** Pydantic (primarily via FastAPI)
*   **File System Interaction:** Python's built-in `pathlib` and `os` modules
*   **XML Parsing:** Standard library `re` (Regex) and `html` (for unescaping).
*   **Logging:** Standard library `logging` module
*   **HTTP Requests (Internal):** `aiohttp` (used within `ModelRegistry`, `GitHubTool`, `WebSearchTool` fallback)
*   **HTML Parsing (Tools):** `BeautifulSoup4` (`bs4`) (used within `WebSearchTool` fallback)
*   **Search API (Tools):** `tavily-python` (used within `WebSearchTool`) <!-- Added -->
*   **File Persistence:** Standard library `json` module (for session state and performance metrics)
*   **Ollama Proxy:** Node.js, Express, node-fetch (managed via `subprocess`).

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 20)

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

## 5. Development Phases & Milestones

**Phase 1-18 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery, Admin AI Auto-Selection, Performance Tracking, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling Restoration, Bug Fixes & Prompt Refinements, Auto Model Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Find/Replace.

**Phase 19: Few-Shot Prompting & Performance Ranking (Postponed to Phase 21)**
*   *(Moved content to Phase 21 below)*

**Phase 20: Tooling Enhancements & System Help (Completed)**
*   **Goal:** Enhance existing tools for more utility and robustness, and provide the Admin AI with system-level context and debugging capabilities.
*   [X] **`FileSystemTool` Enhancement:** Added `mkdir` and `delete` (file/empty dir) actions. Updated description and parameters. Implemented logic.
*   [X] **`GitHubTool` Enhancement:** Added optional `recursive` parameter to `list_files` action. Implemented recursive listing logic.
*   [X] **`ManageTeamTool` Enhancement:** Added `get_agent_details` action. Updated tool description and `execute` method. Implemented handling logic in `InteractionHandler`.
*   [X] **`WebSearchTool` Enhancement:** Integrated Tavily API as primary search method if `TAVILY_API_KEY` is set. Kept DDG HTML scraping as fallback. Added `tavily-python` requirement. Updated settings and tool logic.
*   [X] **`SystemHelpTool` Implementation:** Created new tool `SystemHelpTool` with `get_time` (UTC) and `search_logs` (latest log file) actions. Implemented logic and safe log searching.
*   [X] **Admin AI Time Context:** Modified `CycleHandler` to inject current UTC time into Admin AI's prompt context before LLM call. **(Requires Agent Core modification to fully utilize)**
*   [X] **Prompt Updates (`prompts.json`):** Updated Admin AI instructions to include `SystemHelpTool` and mention enhanced capabilities of other tools.
*   [X] **Documentation Updates (`FUNCTIONS_INDEX.md`, `PROJECT_PLAN.md`, `README.md`, `TOOL_MAKING.md`):** Updated descriptions and function lists to reflect changes.

**Phase 21: Few-Shot Prompting & Performance Ranking (Next)**
*   **Goal:** Improve LLM instruction following with few-shot examples in prompts. Implement basic model ranking based on collected performance data.
*   [ ] **Few-Shot Examples:** Add concrete examples of correct, sequential tool usage (especially `ManageTeamTool` sequence, planning phase, file usage, system help) to `prompts.json` for Admin AI. Add examples for standard agent tools.
*   [ ] **Ranking Algorithm:** Refine/implement scoring logic in `ModelPerformanceTracker._calculate_score` and `get_ranked_models`. Consider factors like success rate, latency, call volume threshold.
*   [ ] **(Display Only)** Add a way to view the ranked models list (e.g., hidden API endpoint or log output) to verify ranking logic.

**Future Phases (22+) (High-Level)**
*   **Phase 22+:** New Admin AI Tools (Qualitative Feedback), LiteLLM Provider, Advanced Collaboration, Resource Limiting, DB/Vector Store, GeUI, etc.
