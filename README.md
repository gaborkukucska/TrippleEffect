<!-- # START OF FILE README.md - AI Contributor: Please review helperfiles/DEVELOPMENT_RULES.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**Version:** 2.9 (Phase 9 Refinements Completed) <!-- Updated Version -->

*This framework is primarily developed and iterated upon by Large Language Models (LLMs) like Google's Gemini series, guided by human oversight.*

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI. It aims for extensibility and supports various LLM API providers, including **OpenRouter**, **Ollama**, and **OpenAI** (and can be extended to others like LiteLLM, Google, Anthropic, etc.).

## 🎯 Core Concept

The system orchestrates multiple LLM agents. A central `Admin AI` agent (bootstrapped from `config.yaml`) analyzes user requests and devises plans, creating specialized "worker" agents dynamically in memory.

**Key Workflow:**
1.  User submits a complex task via the web UI 📝 (voice 🎤, camera 📸, file uploads 📁 planned future features).
2.  `Admin AI` receives the task, plans a team structure, and defines agent roles/prompts.
3.  `Admin AI` uses the `ManageTeamTool` to sequentially create the necessary dynamic agents.
4.  The **Framework** automatically injects standard instructions (tool usage, communication protocols, agent ID, team ID, task breakdown encouragement) into the system prompt of each dynamic agent. `Admin AI`'s prompts focus on the *specific* role and task.
5.  `Admin AI` uses the `SendMessageTool` to delegate initial tasks to the created agents (using their correct IDs).
6.  Agents process tasks, potentially using tools like `file_system` (within their sandboxed directory `sandboxes/agent_<id>/`) or communicating with teammates/Admin AI via `send_message`. **Multiple tool calls per turn are supported and executed sequentially.**
7.  Agents **report results back** to the requesting agent (usually `Admin AI`) using `send_message`.
8.  `Admin AI` monitors progress, coordinates further steps if needed, and synthesizes the final result for the user.
9.  `Admin AI` uses `ManageTeamTool` to clean up dynamic agents/teams upon task completion.

Configuration (`config.yaml`) primarily defines the `Admin AI` and constraints (allowed providers/models) for dynamic agent creation ⚙️. Secrets and default settings are managed via `.env`. The system includes error handling with **automatic retries** for provider issues and a **user override** mechanism via the UI if retries fail.

## ✨ Key Features

*   **Dynamic Multi-Agent Architecture:** `Admin AI` orchestrates dynamically created worker agents.
*   **Structured Delegation:** `Admin AI` follows a guided plan (Team -> Agents -> Tasks -> Kickoff).
*   **Framework-Injected Context:** Standard instructions (tools, comms, ID, team, task breakdown) automatically added to dynamic agent prompts for consistency.
*   **Asynchronous Backend:** Built with FastAPI and `asyncio` for efficient handling of concurrent operations.
*   **Browser-Based UI:** Simple web interface for task submission, agent monitoring, viewing configurations, and results. Includes modal for agent configuration override on persistent errors. <!-- Updated -->
*   **Real-time Updates:** Uses WebSockets (`/ws`) for instant communication.
*   **Multi-Provider LLM Support:** Connect agents to different LLM backends (**OpenRouter**, local **Ollama**, **OpenAI**, easily extensible).
*   **Robust Error Handling:** Includes automatic retries with specific delays for stream errors and a UI-driven user override for persistent failures. <!-- Updated -->
*   **YAML Configuration:** Defines bootstrap agents (`Admin AI`) and constraints (`allowed_sub_agent_models`). Defaults and API keys/URLs set via `.env`.
*   **Sandboxed Workspaces:** Each agent operates within its own directory (`sandboxes/agent_<id>/`) for file-based tasks 📁.
*   **Sequential Multi-Tool Usage:** Agents can request multiple tools (using XML format) in a single turn; the framework parses and executes them sequentially. <!-- Updated -->
*   **Agent Communication:** Agents can communicate with `Admin AI` and teammates (within the same team) using the `send_message` tool.
*   **Session Persistence:** Save/Load the state of dynamic agents, teams, and message histories via the `SessionManager`.
*   **Extensible Design:** Modular structure (`src/llm_providers`, `src/tools`, `src/agents`) for adding new LLM providers, tools, or agent logic.
*   **Termux Friendly:** Aims for compatibility and reasonable performance on resource-constrained environments.

## 🏗️ Architecture Overview (Conceptual - Post Phase 9 Refinements)

```mermaid
graph TD
    Changed to Top-Down for better layer visualization
    USER[🤓Human User]
    subgraph Frontend [Human UI Layer]
        direction LR
        UI_SESSION_VIEW["Session View <br>(Agent Status/Comms)<br>Log Stream Filter<br>Adv I/O: P11+<br>**Dynamic Updates: P10**"]
        UI_MGMT["Project/Session Mgmt Page <br>(Save/Load UI - P10)<br>**Override Modal ✅**<br>Auth UI: P10"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Session API ✅<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Dynamic State Updates (P9/10)<br>+ Log Categories (P10)<br>**+ Override Handling ✅**"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Orchestrator)<br>+ Agent Create/Delete ✅<br>+ Routes Admin/User Msgs ✅<br>+ Routes Tool Calls (Multi)✅<br>+ Handles Agent Generators ✅<br>+ Stream Error Retries/Override ✅<br>+ **Injects Standard Prompts ✅**<br>+ **Handles Queued Messages ✅**<br>+ Uses State/Session Mgrs ✅<br>Controls All Agents"]
        %% REFINED ROLE -- Moved comment to its own line
        STATE_MANAGER["📝 AgentStateManager <br>(Manages Teams State) P9 ✅"]
        SESSION_MANAGER["💾 SessionManager <br>(Handles Save/Load Logic) P9 ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>(Loaded from Config)<br>Receives Allowed Models ✅<br>Receives Standard Instr ✅<br>Uses ManageTeamTool<br>Uses SendMessageTool"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1<br>(Created by Manager)<br>Receives Injected Prompt ✅<br>Uses Tools, Reports Back ✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N<br>(Created by Manager)"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(Instantiated by Manager)"]
             PROVIDER_OR["🔌 OpenRouter Provider(s)"]
             PROVIDER_OLLAMA["🔌 Ollama Provider(s)"]
             PROVIDER_OPENAI["🔌 OpenAI Provider(s)"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor<br>+ XML Desc Gen ✅<br>**+ Correct Kwarg Handling ✅**"]
             TOOL_FS["📄 FileSystem Tool ✅"]
             TOOL_SENDMSG["🗣️ SendMessageTool ✅"]
             TOOL_MANAGE_TEAM["🛠️ ManageTeamTool ✅<br>Signals AgentManager"]
         end

         SANDBOXES["📁 Sandboxes <br>(Created Dynamically) ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage <br>(JSON via SessionManager) ✅"]
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(AdminAI + Allowed Models) ✅"]
        DOT_ENV[".env File <br>(Secrets/Config) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth, Session Mgmt) --> FASTAPI;
    Frontend -- WebSocket (Receives updates, Sends Overrides) --> WS_MANAGER;

    FASTAPI -- Calls AgentManager Ops --> AGENT_MANAGER; %% Simplified view
    FASTAPI -- Manages --> AGENT_MANAGER; %% App startup context

    WS_MANAGER -- Forwards Msgs / Sends Logs & Updates / Requests Override --> Frontend;
    WS_MANAGER -- Forwards User Msgs & Overrides --> AGENT_MANAGER; %% Routes to AdminAI or Handler

    AGENT_MANAGER -- "Loads Bootstrap Agent(s)" --> CONFIG_YAML;
    AGENT_MANAGER -- "Uses Settings For Checks" --> DOT_ENV; %% Via Settings
    AGENT_MANAGER -- "Instantiates/Reuses/Cleans" --> LLM_Providers;
    AGENT_MANAGER -- "Creates/Deletes/Manages Instances" --> Agents;
    AGENT_MANAGER -- "**Injects Standard Context into Prompts**" --> Agents; %% Updated
    AGENT_MANAGER -- "Handles Tool Call Signals" --> Tools; %% Handles ManageTeamTool signal
    AGENT_MANAGER -- Routes Tool Results Back --> Agents; %% Handles SendMessage activation
    AGENT_MANAGER -- Delegates State Ops --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates Session Ops --> SESSION_MANAGER;
    AGENT_MANAGER -- Handles User Override --> Agents; %% Updates config/provider

    STATE_MANAGER -- Manages --> "[Team State Dictionaries]"; %% Conceptual State
    SESSION_MANAGER -- Uses --> STATE_MANAGER; %% To get/set state
    SESSION_MANAGER -- Uses --> AGENT_MANAGER; %% To get agent configs/histories
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;

    ADMIN_AI -- "Uses Tools" --> TOOL_EXECUTOR;
    ADMIN_AI -- "Uses Provider" --> LLM_Providers;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;

    DYNAMIC_AGENT_1 -- "**Uses Tools based on Injected Info**" --> TOOL_EXECUTOR; %% Updated
    DYNAMIC_AGENT_1 -- "Uses Provider" --> LLM_Providers;
    DYNAMIC_AGENT_1 -- "Streams Text" --> AGENT_MANAGER;
    DYNAMIC_AGENT_1 -- "**Sends Result Message**" --> TOOL_SENDMSG; %% Updated

    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_SENDMSG;
    TOOL_EXECUTOR -- Executes --> TOOL_MANAGE_TEAM;

    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`) for bootstrap & allowed models, `.env`.
*   **State Management:** In-memory dictionaries in `AgentManager` and `AgentStateManager`.
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (via `SessionManager`).
*   **XML Parsing:** Standard library `re`, `html`.

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── config.yaml             # Bootstrap agents (AdminAI) & Dynamic Agent Constraints ✨ UPDATED
├── helperfiles/            # Project planning & tracking 📝
│   ├── PROJECT_PLAN.md     # <-- High-level plan and phase tracking ✨ UPDATED
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md  # <-- Function/Method index ✨ UPDATED
├── sandboxes/              # Agent work directories (created at runtime) 📁
│   └── agent_X/
├── src/                    # Source code 🐍
│   ├── agents/             # Agent core logic, managers, state
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class (parses multiple XML tools) 🤖 ✨ UPDATED
│   │   ├── manager.py      # AgentManager (orchestration, prompt injection, retry/override) 🧑‍💼 ✨ UPDATED
│   │   ├── session_manager.py # Handles save/load state 💾 ✨ NEW (P9)
│   │   └── state_manager.py   # Handles team/assignment state 📝 ✨ NEW (P9)
│   ├── api/                # FastAPI routes & WebSocket logic 🔌
│   │   ├── __init__.py
│   │   ├── http_routes.py  # Session API added ✨ UPDATED
│   │   └── websocket_manager.py # Handles override messages ✨ UPDATED
│   ├── config/             # Configuration loading & management ⚙️
│   │   ├── __init__.py
│   │   ├── config_manager.py # Handles read-only loading of config.yaml ✨ UPDATED
│   │   └── settings.py     # Loads .env and initial config ✨ UPDATED
│   ├── llm_providers/      # LLM provider implementations
│   │   ├── __init__.py
│   │   ├── base.py         # BaseLLMProvider ABC
│   │   ├── ollama_provider.py # Includes stream error handling ✨ UPDATED
│   │   ├── openai_provider.py # Includes stream error handling ✨ UPDATED
│   │   └── openrouter_provider.py # Includes stream error handling ✨ UPDATED
│   ├── tools/              # Agent tools implementations 🛠️
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── executor.py     # Executes tools, fixed arg passing ✨ UPDATED
│   │   ├── file_system.py  # Filesystem tool with path validation & async fix ✨ UPDATED
│   │   ├── manage_team.py  # Signals manager for agent/team ops ✨ UPDATED
│   │   └── send_message.py # Signals manager for inter-agent comms ✨ UPDATED
│   ├── ui/                 # UI backend helpers (if needed)
│   │   └── __init__.py
│   ├── utils/              # Utility functions
│   │   └── __init__.py
│   ├── __init__.py
│   └── main.py             # Application entry point 🚀
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css       # Override Modal styles added ✨ UPDATED
│   └── js/
│       └── app.js          # Override Modal logic added ✨ UPDATED
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # Override Modal added ✨ UPDATED
├── .env.example            # Example environment variables
├── .gitignore
├── LICENSE                 # Project License (MIT) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies
```
*(Note: Removed reference to Config CRUD UI/API as it's no longer used)*

## ⚙️ Installation

1.  **Prerequisites:**
    *   Python 3.9+ 🐍
    *   Git
    *   (Optional) Local Ollama instance running if using Ollama agents.
    *   **Termux specific:** `pkg update && pkg upgrade && pkg install binutils build-essential -y`

2.  **Clone Repository:**
    ```bash
    git clone https://github.com/gaborkukucska/TrippleEffect.git
    cd TrippleEffect
    ```

3.  **Set up Virtual Environment:** (Recommended)
    ```bash
    python -m venv .venv
    source .venv/bin/activate # Linux/macOS/Termux
    # .venv\Scripts\activate # Windows
    ```

4.  **Install Dependencies:** 📦
    ```bash
    pip install --upgrade pip
    pip install -r requirements.txt
    ```

5.  **Configure Environment Variables:** 🔑
    *   Copy `.env.example` to `.env`: `cp .env.example .env`
    *   **Edit `.env`:** Add your API keys (`OPENROUTER_API_KEY`, `OPENAI_API_KEY` if used) and set `OLLAMA_BASE_URL` if your Ollama instance isn't at the default `http://localhost:11434`. You can also set default providers/models here.

6.  **Review Agent Configuration (`config.yaml`):** 🧑‍🔧
    *   This file now primarily defines the bootstrap `admin_ai` agent and the `allowed_sub_agent_models`.
    *   Ensure the `admin_ai` configuration (provider, model, persona, prompt) is suitable for orchestration. The prompt should guide it to delegate tasks and use the structured workflow.
    *   Define the list of models dynamic agents are allowed to use under `allowed_sub_agent_models` for each provider you intend to use dynamically.
    *   **Static agent configurations are no longer added here.**

## ▶️ Running the Application

```bash
python -m src.main
```

*   The server will start (usually on `http://0.0.0.0:8000`).
*   It reads `.env` for secrets/defaults and loads `config.yaml` to initialize the `admin_ai`. Check console output.
*   Access the UI in your web browser: `http://localhost:8000` (or your machine's IP).

## 🖱️ Usage

1.  Open the web UI. You should see "Connected" status.
2.  Type your complex task into the input box ⌨️.
3.  Send the message. The task goes to the `Admin AI`.
4.  `Admin AI` will analyze, plan, create a team and dynamic agents, and delegate tasks sequentially. Observe the process in the "Conversation Area" and "System Logs & Status" area. Agent statuses update in the "Agent Status" section.
5.  Dynamic agents perform tasks, use tools (e.g., `file_system` within their `sandboxes/agent_<id>/` directory), and report back to `Admin AI` using `send_message`.
6.  If an agent encounters persistent LLM provider errors after retries, a modal window will appear asking you to provide an alternative provider/model for that agent.
7.  `Admin AI` coordinates, synthesizes results, and presents the final output to you.
8.  Use the (upcoming Phase 10) Project/Session management UI to save or load the state of dynamic agents and conversations.

## 🛠️ Development

*   **Code Style:** Follow PEP 8. Use formatters like Black.
*   **Linting:** Use Flake8 or Pylint.
*   **Helper Files:** Keep `helperfiles/PROJECT_PLAN.md` (tracking phases, goals, scope) and `helperfiles/FUNCTIONS_INDEX.md` updated! ✍️
*   **Configuration:** Modify `config.yaml` ONLY for bootstrap agents and allowed models. Set API keys/URLs/defaults in `.env`.
*   **Branching:** Use feature branches.

## 🙌 Contributing

Contributions welcome! Follow guidelines, open Pull Requests.

## 📜 License

MIT License - See `LICENSE` file for details.
