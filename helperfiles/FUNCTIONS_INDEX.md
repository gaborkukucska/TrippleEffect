<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**Version:** 2.13 (Phase 12: Dynamic Discovery & Auto-Selection Completed) <!-- Updated Version -->

*This framework is primarily developed and iterated upon by Large Language Models (LLMs) like Google's Gemini series, guided by human oversight.*

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI.

It features **dynamic discovery of reachable LLM providers** (local Ollama/LiteLLM, configured public APIs) and their **available models**. It can **automatically select a suitable model for the core `Admin AI`** at startup and validates models used for dynamic agents against the discovered list, considering cost tiers (`MODEL_TIER` environment variable). It aims for extensibility and supports various LLM API providers like **OpenRouter**, **Ollama**, **OpenAI**, and can be extended easily.

## 🎯 Core Concept

The system orchestrates multiple LLM agents. A central `Admin AI` agent analyzes user requests and devises plans, creating specialized "worker" agents dynamically in memory.

**Key Workflow:**
1.  **Startup:**
    *   The framework checks `.env` for configured API keys/URLs.
    *   It attempts to discover reachable local providers (Ollama, LiteLLM) on default ports or specified URLs.
    *   It fetches available models from reachable local providers and configured remote providers (e.g., OpenRouter).
    *   Models are filtered based on the `MODEL_TIER` environment variable (`FREE` or `ALL`).
    *   If `Admin AI` provider/model aren't explicitly set (or valid) in `config.yaml`, the framework automatically selects the best available model based on internal preferences (e.g., favouring capable models like Claude Opus, GPT-4o, Gemini Pro, large local models). The chosen model is logged.
2.  **Task Submission:** User submits a complex task via the web UI 📝.
3.  **Planning & Delegation:** `Admin AI` receives the task, uses its knowledge of *available* models (injected into its prompt), plans a team structure, and defines agent roles/prompts.
4.  **Agent Creation:** `Admin AI` uses the `ManageTeamTool` to sequentially request dynamic agent creation. The framework validates the requested model against the *discovered available models list* before creating the agent.
5.  **Framework Context:** The Framework automatically injects standard instructions (tool usage, communication, ID, team, task breakdown) into each dynamic agent's prompt.
6.  **Task Execution:** `Admin AI` uses `SendMessageTool` to delegate tasks. Agents process tasks, use tools (e.g., `file_system` in sandboxes or shared workspace), communicate (`send_message`), and report results back to `Admin AI`. Multiple tool calls per turn are supported sequentially.
7.  **Coordination & Synthesis:** `Admin AI` monitors progress, coordinates, and synthesizes the final result for the user.
8.  **Cleanup:** `Admin AI` uses `ManageTeamTool` to clean up dynamic agents/teams.

Configuration (`config.yaml`) primarily defines the `Admin AI`'s base persona/prompt (provider/model are optional overrides). Secrets, provider URLs, and filtering (`MODEL_TIER`) are managed via `.env`. The system includes error handling with **automatic retries** and **user override**. Session state can be saved/loaded.

## ✨ Key Features

*   **Dynamic Multi-Agent Architecture:** `Admin AI` orchestrates dynamically created worker agents.
*   **Dynamic Provider/Model Discovery:** Automatically finds reachable local providers (Ollama, LiteLLM) and fetches available models from them and configured remote providers (OpenRouter, OpenAI) at startup. <!-- NEW -->
*   **Automatic Admin AI Model Selection:** Selects the best *available* provider/model for Admin AI at startup based on preferences if not explicitly set in `config.yaml`. <!-- NEW -->
*   **Model Availability Validation:** Ensures dynamic agents use models confirmed as available during discovery and respecting the `MODEL_TIER` (`FREE`/`ALL`) setting from `.env`. <!-- NEW -->
*   **Structured Delegation:** `Admin AI` follows a guided plan (Team -> Agents -> Tasks -> Kickoff).
*   **Framework-Injected Context:** Standard instructions automatically added to dynamic agent prompts.
*   **Asynchronous Backend:** FastAPI & `asyncio`.
*   **Browser-Based UI:** Task submission, agent/log monitoring, session management, config view, override modal.
*   **Real-time Updates:** WebSockets for status and messages.
*   **Multi-Provider LLM Support:** Connect to discovered/configured providers (**OpenRouter**, **Ollama**, **OpenAI**, extensible).
*   **Robust Error Handling:** Automatic retries, UI-driven user override.
*   **Simplified Configuration:** `config.yaml` for bootstrap agents (provider/model optional). `.env` for secrets, URLs, `MODEL_TIER`. <!-- Updated -->
*   **Sandboxed & Shared Workspaces:** Private agent directories and shared session workspace via `FileSystemTool`.
*   **Sequential Multi-Tool Usage:** Agents can request multiple tools per turn.
*   **Agent Communication:** Via `send_message` tool.
*   **Session Persistence:** Save/Load state via `SessionManager`.
*   **Timestamped File Logging:** Logs saved to `/logs`.
*   **Extensible Design:** Modular structure.
*   **Termux Friendly:** Aims for compatibility.

## 🏗️ Architecture Overview (Conceptual - Post Phase 12)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_CHAT_VIEW["Chat & Agents View ✅"]
        UI_LOGS_VIEW["System Logs View ✅"]
        UI_SESSION_VIEW["Project/Session View ✅"]
        UI_CONFIG_VIEW["Static Config View ✅<br>(Provider/Model Optional)"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend ✅"]
        WS_MANAGER["🔌 WebSocket Manager ✅"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ **Uses ModelRegistry** ✅<br>+ **Auto-Selects Admin AI Model** ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"]
        MODEL_REGISTRY["📚 Model Registry<br>(Singleton)<br>+ Discovers Providers ✅<br>+ Discovers Models ✅<br>+ Filters by Tier ✅<br>+ Stores Reachable/Available ✅"] %% Added
        CYCLE_HANDLER["🔄 Agent Cycle Handler ✅"]
        INTERACTION_HANDLER["🤝 Interaction Handler ✅"]
        STATE_MANAGER["📝 AgentStateManager ✅"]
        SESSION_MANAGER["💾 SessionManager ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>(Model Auto-Selected?)✅<br>Receives Available Models ✅"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1<br>(Uses Available Models) ✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(Instantiated by Manager)"]
             PROVIDER_OR["🔌 OpenRouter Provider(s)"]
             PROVIDER_OLLAMA["🔌 Ollama Provider(s)"]
             PROVIDER_OPENAI["🔌 OpenAI Provider(s)"]
             PROVIDER_LITELLM["🔌 LiteLLM Provider(s)<br>(Class TBD)"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor✅"]
             TOOL_FS["📄 FileSystem Tool ✅"]
             TOOL_SENDMSG["🗣️ SendMessageTool ✅"]
             TOOL_MANAGE_TEAM["🛠️ ManageTeamTool ✅"]
             TOOL_GITHUB["🐙 GitHub Tool ✅"]
             TOOL_WEBSEARCH["🌐 Web Search Tool ✅"]
         end

         SANDBOXES["📁 Sandboxes ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage ✅"]
         SHARED_WORKSPACE["🌐 Shared Workspace ✅"]
         LOG_FILES["📄 Log Files ✅"]
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        LITELLM_SVC["⚙️ Local LiteLLM Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(AdminAI Optional) ✅"]
        DOT_ENV[".env File <br>(Secrets/URLs/Tier) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP/WebSocket --> Backend;

    FASTAPI -- Manages/Uses --> AGENT_MANAGER;
    FASTAPI -- Creates/Manages --> MODEL_REGISTRY; # Via app startup lifespan

    AGENT_MANAGER -- Uses --> MODEL_REGISTRY; # For validation & selection
    AGENT_MANAGER -- Instantiates/Uses --> LLM_Providers;
    AGENT_MANAGER -- Creates/Deletes/Manages --> Agents;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates --> SESSION_MANAGER;
    AGENT_MANAGER -- Instantiates --> INTERACTION_HANDLER;
    AGENT_MANAGER -- Instantiates --> CYCLE_HANDLER;

    MODEL_REGISTRY -- Discovers Providers --> OLLAMA_SVC;
    MODEL_REGISTRY -- Discovers Providers --> LITELLM_SVC;
    MODEL_REGISTRY -- Discovers Models --> LLM_API_SVC; # e.g., OpenRouter

    CYCLE_HANDLER -- Runs --> Agents;
    CYCLE_HANDLER -- Delegates --> INTERACTION_HANDLER;
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
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (`SessionManager`).
*   **XML Parsing:** Standard library `re`, `html`.
*   **Logging:** Standard library `logging`.

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── config.yaml             # Bootstrap agents (AdminAI provider/model optional) ✨ UPDATED
├── helperfiles/            # Project planning & tracking 📝
│   ├── PROJECT_PLAN.md     # <-- High-level plan and phase tracking ✨ UPDATED
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md  # <-- Function/Method index ✨ UPDATED
├── logs/                   # Application log files (timestamped) 📝 NEW
│   └── app_YYYYMMDD_HHMMSS.log
├── sandboxes/              # Agent work directories (created at runtime) 📁
│   └── agent_X/
├── src/                    # Source code 🐍
│   ├── agents/             # Agent core logic, managers, state
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class ✅
│   │   ├── cycle_handler.py # Handles agent execution cycle, retries ✅
│   │   ├── interaction_handler.py # Processes tool signals, routes messages ✅
│   │   ├── manager.py      # AgentManager (orchestration, Admin AI auto-select, uses registry) 🧑‍💼 ✨ UPDATED
│   │   ├── prompt_utils.py # Prompt templates ✅
│   │   ├── session_manager.py # Handles save/load state ✅
│   │   └── state_manager.py   # Handles team/assignment state ✅
│   ├── api/                # FastAPI routes & WebSocket logic 🔌
│   │   ├── __init__.py
│   │   ├── http_routes.py  # Session API uses dependency injection ✅
│   │   └── websocket_manager.py # Handles WS connections, forwards messages ✅
│   ├── config/             # Configuration loading & management ⚙️
│   │   ├── __init__.py
│   │   ├── config_manager.py # Handles read-only loading of config.yaml ✅
│   │   ├── model_registry.py # Handles provider/model discovery & filtering 📚 NEW
│   │   └── settings.py     # Loads .env, initial config, MODEL_TIER, uses registry ✅ ✨ UPDATED
│   ├── llm_providers/      # LLM provider implementations
│   │   ├── __init__.py
│   │   ├── base.py         # BaseLLMProvider ABC
│   │   ├── ollama_provider.py # Includes stream error handling & timeout fix ✅
│   │   ├── openai_provider.py # Includes stream error handling ✅
│   │   └── openrouter_provider.py # Includes stream error handling ✅
│   │   └── # (litellm_provider.py - Future)
│   ├── tools/              # Agent tools implementations 🛠️
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── executor.py     # Executes tools ✅
│   │   ├── file_system.py  # Filesystem tool ✅
│   │   ├── github_tool.py  # GitHub interaction tool ✅
│   │   ├── manage_team.py  # Signals manager for agent/team ops ✅
│   │   ├── send_message.py # Signals manager for inter-agent comms ✅
│   │   └── web_search.py   # Web scraping search tool ✅
│   ├── ui/                 # UI backend helpers (if needed)
│   │   └── __init__.py
│   ├── utils/              # Utility functions
│   │   └── __init__.py
│   ├── __init__.py
│   └── main.py             # Application entry point (runs discovery) ✨ UPDATED
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css       # UI styles ✅
│   └── js/
│       └── app.js          # Frontend logic ✅
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # Main UI page ✅
├── .env.example            # Example environment variables (MODEL_TIER added) ✨ UPDATED
├── .gitignore              # Ensure logs/, projects/, sandboxes/ are added
├── LICENSE                 # Project License (MIT) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies (fnmatch should be built-in)
```
*(Note: Ensure `logs/`, `projects/`, `sandboxes/` are added to your `.gitignore` file)*

## ⚙️ Installation

1.  **Prerequisites:** Python 3.9+, Git, (Optional) Local Ollama/LiteLLM instance. Termux: `pkg update && pkg upgrade && pkg install binutils build-essential -y`
2.  **Clone:** `git clone https://github.com/gaborkukucska/TrippleEffect.git && cd TrippleEffect`
3.  **Venv:** `python -m venv .venv && source .venv/bin/activate`
4.  **Install:** `pip install --upgrade pip && pip install -r requirements.txt`
5.  **Configure Environment Variables (`.env`):** <!-- Updated -->
    *   Copy `.env.example` to `.env`.
    *   **Edit `.env`:**
        *   Add **API keys** for providers you want to use (e.g., `OPENROUTER_API_KEY`).
        *   Set **Base URLs** ONLY if your local providers (Ollama, LiteLLM) are *not* on `localhost` default ports OR if you need specific remote endpoints. Provider discovery will attempt localhost otherwise.
        *   Set `MODEL_TIER` to `FREE` or `ALL` (default). `FREE` restricts dynamic agents mainly to OpenRouter free models and local models.
        *   Set `GITHUB_ACCESS_TOKEN` if using the GitHub tool.
6.  **Review Bootstrap Agent Config (`config.yaml`):** <!-- Updated -->
    *   This file primarily defines the bootstrap `admin_ai` agent.
    *   Setting `provider` and `model` here is now **optional**. If commented out or invalid, the framework will attempt to auto-select a suitable model for Admin AI at startup.
    *   Ensure the `system_prompt` and `persona` are suitable for orchestration.
7.  **Create Logs Directory:** `mkdir logs`

## ▶️ Running the Application

```bash
python -m src.main
```

*   The server starts (usually on `http://0.0.0.0:8000`).
*   **Startup Sequence:** Reads `.env`, discovers reachable providers & available models (check logs), applies `MODEL_TIER` filter, selects Admin AI model (auto or from config), loads `config.yaml` for Admin AI prompt/persona, initializes Admin AI.
*   Access the UI in your web browser: `http://localhost:8000`.

## 🖱️ Usage

1.  Open the web UI. Check logs/console for provider/model discovery results and the selected Admin AI model.
2.  Type your complex task.
3.  Send the message. The task goes to the `Admin AI`.
4.  `Admin AI` will analyze, plan (using knowledge of *available* models), create dynamic agents (using available models), and delegate tasks sequentially.
5.  Observe the process in the UI.
6.  Handle user overrides for persistent LLM errors if they occur.
7.  `Admin AI` coordinates and presents the final output.
8.  Use the **Project & Session** view to save/load state.

## 🛠️ Development

*   **Code Style:** PEP 8, Black.
*   **Linting:** Flake8/Pylint.
*   **Helper Files:** Keep `helperfiles/PROJECT_PLAN.md` and `helperfiles/FUNCTIONS_INDEX.md` updated.
*   **Configuration:** Set keys/URLs/`MODEL_TIER` in `.env`. Edit `config.yaml` only for Admin AI's base prompt/persona or to *override* auto-selection.
*   **Branching:** Use feature branches.

## 🙌 Contributing

Contributions welcome! Follow guidelines, open Pull Requests.

## 📜 License

MIT License - See `LICENSE` file for details.
