<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.16 <!-- Updated Version -->
**Date:** 2025-04-18 <!-- Updated Date -->

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
*   Utilize **XML-based tool calling** with **sequential execution**. *(Reverted & Refined in P16)*
*   Allow tool use in sandboxed or shared workspaces. *(Completed in P11)*
*   Implement **automatic project/session context setting**. *(Partially Completed in P11)*
*   **(Future Goals)** Enhance Admin AI planning (few-shot examples P17), **use tracked performance metrics for ranking and automatic model selection** (ranking P17, auto-select P18+), implement new Admin AI tools, resource management, advanced collaboration patterns, database integration, formal project/task management.

## 2. Scope

**In Scope (Completed up to Phase 16):**

*   **Core Backend & Agent Core:** Base functionality. *(Completed)*
*   **Admin AI Agent:** Core logic. *(Completed)*
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover. *(Completed)*
*   **State & Session Management:** Team state, save/load. *(Completed)*
*   **Model Registry (`ModelRegistry`):** Provider/model discovery, filtering. *(Completed in P12)*
*   **Automatic Admin AI Model Selection:** Based on discovery/preferences. *(Completed in P12)*
*   **Performance Tracking (`ModelPerformanceTracker`):** Tracks success/failure/duration per model, saves to JSON. *(Completed in P13)*
*   **Automatic Agent Failover:** Agent switches provider/model on persistent errors based on tiers (Local->Free->Paid), up to `MAX_FAILOVER_ATTEMPTS`. *(Completed in P13)*
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI tool calls. *(Completed)*
*   **Tooling:** Core tools implemented, using **XML format**. *(Reverted & Refined in P16)*
*   **Configuration:** `config.yaml` (Admin AI optional), `.env` (keys, URLs, tier, proxy), `prompts.json` (XML tools). *(Updated in P16)*
*   **Session Persistence:** Save/Load state. *(Completed)*
*   **Human UI:** Dynamic updates, Session management, Conversation view. *(Simplified in P13)*
*   **WebSocket Communication:** Real-time updates. *(Completed)*
*   **Sandboxing & Shared Workspace:** Implemented. *(Completed)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover. *(Completed)*
*   **Helper Files & Logging:** Maintained. *(Ongoing)*
*   **Ollama Proxy Integration:** Optional, managed proxy. *(Completed in P15)*
*   **Bug Fixes & Prompt Refinements:** Addressed tool usage issues. *(Completed in P16)*

**Out of Scope (Deferred to Future Phases 17+):**

*   **Phase 17: Few-Shot Prompting & Performance Ranking.** (Add examples to prompts, implement ranking algorithm).
*   **Phase 18: Automatic Dynamic Agent Model Selection.** (Use rankings/role for selection).
*   **Phase 19+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource limiting, DB/Vector Store, GeUI, etc.

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:**
    *   `openai` library (for OpenAI & OpenRouter providers)
    *   `aiohttp` (for Ollama provider and internal HTTP requests)
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:**
    *   YAML (`PyYAML`) for bootstrap agent definitions (`config.yaml`)
    *   `.env` files (`python-dotenv`) for secrets, URLs, and settings like `MODEL_TIER`, proxy config.
    *   JSON (`prompts.json`) for standard framework/agent instructions (using XML tool format). <!-- Updated -->
*   **Model Discovery & Management:** Custom `ModelRegistry` class
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (saving to JSON)
*   **Data Handling/Validation:** Pydantic (primarily via FastAPI)
*   **File System Interaction:** Python's built-in `pathlib` and `os` modules
*   **XML Parsing:** Standard library `re` (Regex) and `html` (for unescaping). <!-- Updated -->
*   **Logging:** Standard library `logging` module
*   **HTTP Requests (Internal):** `aiohttp` (used within `ModelRegistry`, `GitHubTool`, `WebSearchTool`)
*   **HTML Parsing (Tools):** `BeautifulSoup4` (`bs4`) (used within `WebSearchTool`)
*   **File Persistence:** Standard library `json` module (for session state and performance metrics)
*   **Ollama Proxy:** Node.js, Express, node-fetch (managed via `subprocess`).

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 16)

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
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ Uses ModelRegistry ✅<br>+ Uses ProviderKeyManager ✅<br>+ Auto-Selects Admin AI Model ✅<br>+ Handles Key/Model Failover ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"] %% Updated
        PROVIDER_KEY_MGR["🔑 Provider Key Manager <br>+ Manages Keys ✅<br>+ Handles Quarantine ✅<br>+ Saves/Loads State ✅"] %% Added
        MODEL_REGISTRY["📚 Model Registry✅"]
        PERF_TRACKER["📊 Performance Tracker<br>+ Records Metrics ✅<br>+ Saves/Loads Metrics ✅"]
        CYCLE_HANDLER["🔄 Agent Cycle Handler<br>+ Handles Retries ✅<br>+ Triggers Key/Model Failover ✅<br>+ Reports Metrics ✅<br>+ Handles Tool Results ✅"] %% Updated
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
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;
```

## 5. Development Phases & Milestones

**Phase 1-12 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery, Admin AI Auto-Selection.

**Phase 13: Performance Tracking & Automatic Failover (Completed)**
*   [X] Track model success/failure/duration. Implement automatic model/provider failover on persistent errors.

**Phase 14: Provider Key Management & Failover Enhancement (Completed)**
*   [X] Multi-key support per provider (`PROVIDER_API_KEY_N`). API Key cycling on auth/rate errors. Temporary key quarantining. Refined failover to attempt key cycling before model switching.

**Phase 15: Prompt Centralization & Ollama Proxy (Completed)**
*   [X] Centralized standard prompts into `prompts.json`. Integrated optional Node.js Ollama proxy managed by the main app.

**Phase 16: XML Tooling Restoration & Prompt Refinement (Completed)**
*   **Goal:** Revert tool communication to XML, fix related bugs, and refine Admin AI prompts for better delegation and tool usage.
*   [X] **Tool Format Reversion:** Updated `prompts.json`, `agent_lifecycle.py`, `core.py`, `README.md`, `TOOL_MAKING.md` to use and expect XML format for tool calls. Removed JSON tool parsing/generation logic.
*   [X] **Bug Fix (`cycle_handler.py`):** Fixed `UnboundLocalError` that occurred during tool result processing.
*   [X] **Configuration Fix (`settings.py`):** Corrected `is_provider_configured` logic to properly recognize Ollama as configured when the proxy is enabled.
*   [X] **Validation Enhancement (`tools/manage_team.py`, `agents/agent_lifecycle.py`):** Added stricter checks to ensure Admin AI provides required provider/model parameters for agent creation and that the pair is valid/available. Improved error messages for Admin AI feedback.
*   [X] **Admin AI Prompt Refinement (`prompts.json`):** Significantly strengthened instructions for Admin AI, mandating delegation, sequential tool use, and correct specification of provider/model parameters from the available list.

**Phase 17: Few-Shot Prompting & Performance Ranking (Next)**
*   **Goal:** Improve LLM instruction following with few-shot examples in prompts. Implement basic model ranking based on collected performance data.
*   [ ] **Few-Shot Examples:** Add concrete examples of correct, sequential tool usage (especially `ManageTeamTool` sequence) to `prompts.json` for Admin AI. Add examples for standard agent tools.
*   [ ] **Ranking Algorithm:** Refine/implement scoring logic in `ModelPerformanceTracker._calculate_score` and `get_ranked_models`. Consider factors like success rate, latency, call volume threshold.
*   [ ] **(Display Only)** Add a (temporary or permanent) way to view the ranked models list (e.g., a hidden API endpoint or log output) to verify ranking logic.

**Future Phases (18+) (High-Level)**
*   **Phase 18:** Automatic Dynamic Agent Model Selection (Use rankings/role).
*   **Phase 19+:** New Admin AI Tools (Qualitative Feedback, Category Selection), LiteLLM Provider, Advanced Collaboration, Enhanced Admin AI, Resource Limits, DB/Vector Store, GeUI, etc.
