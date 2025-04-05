<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**Version:** 2.2 (Phase 8 Completed) <!-- Updated Version -->

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI. It aims for extensibility and supports various LLM API providers, including **OpenRouter**, **Ollama**, and **OpenAI** (and can be extended to others like LiteLLM, Google, Anthropic, etc.).

## 🎯 Core Concept

The system orchestrates multiple LLM agents, whose number and specific configurations (**provider**, model, persona, system prompt, etc.) are defined in a central `config.yaml` file ⚙️. Users interact with the system through a web interface to submit tasks via text 📝 (voice 🎤, camera 📸, file uploads 📁 are planned future features). Agents can also be added, edited, or deleted via the web UI, although a **backend restart is currently required** for these changes to take effect.

The agents, loaded based on the configuration:
*   Interact with their configured LLM provider (**OpenRouter**, **Ollama**, **OpenAI**, etc.).
*   Can work concurrently on the same task.
*   Collaborate and delegate sub-tasks (future phase 🤝).
*   Utilize tools within sandboxed environments 🛠️ using an **XML-based tool calling format** for broad compatibility.
*   Stream their responses back to the user interface in real-time ⚡.

## ✨ Key Features

*   **Multi-Agent Architecture:** Supports multiple LLM agents working concurrently.
*   **Asynchronous Backend:** Built with FastAPI and `asyncio` for efficient handling of concurrent operations.
*   **Browser-Based UI:** Simple web interface for task submission, agent monitoring, viewing configurations, basic agent config CRUD (Create/Read/Update/Delete - requires restart), and results. <!-- Updated -->
*   **Real-time Updates:** Uses WebSockets (`/ws`) for instant communication.
*   **Multi-Provider LLM Support:** Connect agents to different LLM backends (**OpenRouter**, local **Ollama**, **OpenAI**, easily extensible).
*   **YAML Configuration:** Easily define agents (ID, **provider**, model, system prompt, persona, temperature, provider-specific args) via `config.yaml`. Defaults and API keys/URLs set via `.env`.
*   **Config Management UI:** Add, Edit, Delete agent configurations directly from the web UI (requires backend restart). <!-- New -->
*   **Safe Config Handling:** Uses a `ConfigManager` for atomic writes to `config.yaml` with backups. <!-- New -->
*   **Sandboxed Workspaces:** Each agent operates within its own directory (`sandboxes/agent_<id>/`) for file-based tasks 📁.
*   **XML Tool Usage:** Agents request tools using an XML format within their text responses. The framework parses and executes these requests (e.g., file system access 📄, web search 🔍 planned).
*   **Extensible Design:** Modular structure (`src/llm_providers`, `src/tools`, `src/config`) for adding new LLM providers, agents, tools, or configuration management logic.
*   **Termux Friendly:** Aims for compatibility and reasonable performance on resource-constrained environments. Requires specific build tools.

## 🏗️ Architecture Overview (Conceptual - Phase 8)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_COMS["Coms Page <br>(Log Stream Filter - P10)<br>Adv I/O: P11+"]
        UI_ADMIN["Admin Page <br>(Config CRUD UI - P8 ✅)<br>Settings View<br>Auth UI: P10<br>Refresh Button ✅"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Config CRUD API (P8 ✅)<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Log Categories (P10)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Reload Signal (Future)<br>+ Tool History Handling ✅<br>Controls All Agents"]
        CONFIG_MANAGER["📝 Config Manager <br>(Safe R/W config.yaml) P8 ✅"] %% Added P8 Config Manager

        subgraph Agents
            ADMIN_AI["🤖 Admin AI Agent <br>(Google Provider - P9)<br>Uses ConfigTool"]
            AGENT_INST_1["🤖 Worker Agent 1 <br>+ XML Tool Parsing ✅"]
            AGENT_INST_N["🤖 Worker Agent N <br>+ XML Tool Parsing ✅"]
            GEUI_AGENT["🤖 GeUI Agent(s) (P11+)"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(src/llm_providers/)"]
             PROVIDER_GOOGLE["🔌 Google Provider (P9)"]
             PROVIDER_OR["🔌 OpenRouter Provider ✅"]
             PROVIDER_OLLAMA["🔌 Ollama Provider ✅"]
             PROVIDER_OPENAI["🔌 OpenAI Provider ✅"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor<br>+ XML Desc Gen ✅"]
             TOOL_CONFIG["📝 ConfigTool (P9)<br>Uses Config Manager"]
             TOOL_FS["📄 FileSystem Tool ✅"]
             %% Other tools...
         end

         SANDBOXES["📁 Sandboxes ✅"]
    end

    subgraph External
        GOOGLE_API["☁️ Google AI APIs"]
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(Read/Write via Config Manager) ✅"]
        DOT_ENV[".env File <br>(Secrets - Read Only) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (Config CRUD API - P8) --> FASTAPI; %% Updated label
    Frontend -- WebSocket --> WS_MANAGER;

    FASTAPI -- Calls CRUD Ops --> CONFIG_MANAGER;
    FASTAPI -- Manages --> AGENT_MANAGER;
    WS_MANAGER -- Forwards Msgs / Sends Logs --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER;

    AGENT_MANAGER -- Controls --> ADMIN_AI;
    AGENT_MANAGER -- Controls --> AGENT_INST_1;
    AGENT_MANAGER -- Controls --> AGENT_INST_N;
    AGENT_MANAGER -- Controls --> GEUI_AGENT;
    AGENT_MANAGER -- "Reads Initial Config Via Settings Module" --> CONFIG_YAML;
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- Injects --> LLM_Providers;
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;
    AGENT_MANAGER -- "Generates & Injects XML Prompts ✅" --> Agents;
    AGENT_MANAGER -- "Appends Tool Results to Agent History ✅" --> Agents;

    ADMIN_AI -- Uses --> PROVIDER_GOOGLE;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;
    ADMIN_AI -- "Parses Own XML" --> ADMIN_AI;
    ADMIN_AI -- "Yields Tool Request (ConfigTool)" --> AGENT_MANAGER;

    AGENT_INST_1 -- Uses --> LLM_Providers;
    AGENT_INST_1 -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_1 -- "Parses Own XML ✅" --> AGENT_INST_1;
    AGENT_INST_1 -- "Yields Tool Request (e.g., FileSystemTool)" --> AGENT_MANAGER;

    AGENT_INST_N -- Uses --> LLM_Providers;
    AGENT_INST_N -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_N -- "Parses Own XML ✅" --> AGENT_INST_N;
    AGENT_INST_N -- "Yields Tool Request" --> AGENT_MANAGER;


    TOOL_EXECUTOR -- Executes --> TOOL_CONFIG;
    TOOL_EXECUTOR -- Executes --> TOOL_FS;

    TOOL_CONFIG -- Uses --> CONFIG_MANAGER;
    CONFIG_MANAGER -- Reads/Writes --> CONFIG_YAML;

    PROVIDER_GOOGLE -- Interacts --> GOOGLE_API;
    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library (used by OpenAI & OpenRouter providers), `aiohttp` (used by Ollama provider)
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous:** `asyncio`
*   **Configuration:** YAML (`PyYAML`), `.env` files (`python-dotenv`). **Safe read/write via `ConfigManager`**. <!-- Updated -->
*   **Data Handling:** Pydantic (via FastAPI)

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── config.yaml             # Agent configurations (editable via UI + restart) ✨ UPDATED
├── helperfiles/            # Project planning & tracking 📝
│   ├── PROJECT_PLAN.md
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md
├── sandboxes/              # Agent work directories (created at runtime) 📁
│   └── agent_X/
├── src/                    # Source code 🐍
│   ├── agents/             # Agent core logic & manager
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class (uses provider, parses XML tools) 🤖 ✨ UPDATED
│   │   └── manager.py      # AgentManager (instantiates providers, handles tool exec) 🧑‍💼 ✨ UPDATED
│   ├── api/                # FastAPI routes & WebSocket logic 🔌
│   │   ├── __init__.py
│   │   ├── http_routes.py  # Includes Agent Config CRUD endpoints ✨ UPDATED
│   │   └── websocket_manager.py
│   ├── config/             # Configuration loading & management ⚙️
│   │   ├── __init__.py
│   │   ├── config_manager.py # Handles safe R/W to config.yaml <--- ✨ NEW (Phase 8)
│   │   └── settings.py     # Loads .env and uses ConfigManager ✨ UPDATED
│   ├── llm_providers/      # LLM provider implementations
│   │   ├── __init__.py
│   │   ├── base.py         # BaseLLMProvider ABC
│   │   ├── ollama_provider.py
│   │   ├── openai_provider.py
│   │   └── openrouter_provider.py
│   ├── tools/              # Agent tools implementations 🛠️
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── executor.py     # Executes tools, generates XML descriptions ✨ UPDATED
│   │   └── file_system.py
│   ├── ui/                 # UI backend helpers (if needed)
│   │   └── __init__.py
│   ├── utils/              # Utility functions
│   │   └── __init__.py
│   ├── __init__.py
│   └── main.py             # Application entry point 🚀
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css       # ✨ UPDATED (Config UI styles)
│   └── js/
│       └── app.js          # ✨ UPDATED (Config CRUD logic, Refresh Button)
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # ✨ UPDATED (Config UI elements, Modals)
├── .env.example            # Example environment variables
├── .gitignore
├── LICENSE                 # Project License (MIT) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies
```

## ⚙️ Installation

1.  **Prerequisites:**
    *   Python 3.9+ 🐍
    *   Git
    *   (Required for Ollama) Local Ollama instance running. Download and install from [ollama.com](https://ollama.com/). Ensure it's running before starting TrippleEffect if you plan to use Ollama agents. Pull desired models (e.g., `ollama pull llama3`).
    *   **Termux specific:** Some Python packages require compilation. Install necessary build tools using `pkg`:
        ```bash
        pkg update && pkg upgrade
        pkg install binutils build-essential -y
        ```

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
    # Optional: Upgrade pip
    pip install --upgrade pip
    # Install project requirements
    pip install -r requirements.txt
    ```

5.  **Configure Environment Variables:** 🔑
    *   Copy `.env.example` to `.env`:
        ```bash
        cp .env.example .env
        ```
    *   **Edit the `.env` file:** This is crucial for connecting to LLM providers. Prioritize setting up OpenRouter and Ollama if you plan to use them. See `.env.example` for details on keys (`OPENROUTER_API_KEY`, `OLLAMA_BASE_URL`, `OPENAI_API_KEY`) and optional defaults (`DEFAULT_AGENT_PROVIDER`, `DEFAULT_AGENT_MODEL`, etc.).
    *   **Important:** Ensure `.env` is listed in your `.gitignore` file (it should be by default). **Never commit your API keys!**

6.  **Configure Agents:** 🧑‍🔧🧑‍🏫🧑‍🚒
    *   You can **initially** edit the `config.yaml` file in the project root to define your starting agents.
    *   For each agent, define:
        *   `agent_id`: Unique identifier (e.g., "coder", "researcher"). Use alphanumeric, underscores, hyphens only.
        *   `config`: A nested dictionary containing:
            *   `provider`: **Crucial.** Must be `"openrouter"`, `"ollama"`, or `"openai"`. Ensure the corresponding API key/URL is set in `.env`.
            *   `model`: The model name specific to the chosen provider.
            *   `system_prompt`: Instructions for the agent's persona and task focus.
            *   `temperature`: Controls creativity vs. determinism (0.0 to ~1.0+).
            *   `persona`: A name for the agent displayed in the UI.
            *   *(Optional)*: You can add other provider-specific arguments here (e.g., `top_p`). Overriding `base_url` per-agent is possible but API keys should stay in `.env`.
    *   **Alternatively, use the Web UI:** After starting the application, you can Add, Edit, and Delete agent configurations directly via the UI in the "Configuration" section. **Note:** A backend restart (or page refresh if using `reload=True`) is required for these UI-driven changes to take effect.
    *   **Example `config.yaml` snippet:**
        ```yaml
        # config.yaml
        agents:
          - agent_id: "analyst_or"
            config:
              provider: "openrouter" # Using OpenRouter
              model: "mistralai/mistral-7b-instruct" # Check OpenRouter for available models
              system_prompt: "You analyze text and data. Use tools if needed to read files."
              temperature: 0.6
              persona: "Data Analyst (OR)"

          - agent_id: "creative_local"
            config:
              provider: "ollama" # Using local Ollama
              model: "llama3" # Ensure 'llama3' is pulled in Ollama
              system_prompt: "You are a creative writer."
              temperature: 0.9
              persona: "Creative Writer (Ollama)"
        ```

## ▶️ Running the Application

```bash
python -m src.main
```

*   The server will start (usually on `http://0.0.0.0:8000`).
*   It reads `.env` for secrets/defaults and loads the current `config.yaml` to initialize agents. Check console output for details.
*   Access the UI in your web browser: `http://localhost:8000` (or your machine's IP if accessing from another device).

## 🖱️ Usage

1.  Open the web UI.
2.  The backend loads agents based on `config.yaml`. You should see a "Connected" status and agent configurations/statuses loaded in their respective UI sections.
3.  **Manage Agents (Optional):** Use the "+", "Edit", "Delete" buttons in the "Configuration" section to modify agent setups. Click the Refresh button (🔄) and restart the backend (`Ctrl+C` and run `python -m src.main` again) to apply changes.
4.  Type your task into the input box ⌨️ (optionally attach a text file using the 📎 button).
5.  Send the message. The task goes concurrently to all initialized and *available* (idle) agents.
6.  Observe responses streaming back in the "Conversation Area", identified by agent persona/ID. System messages and errors appear in the "System Logs & Status" area.
7.  Agent behavior (including XML tool use like file access) depends on the configured provider and model. Check agent status updates in the "Agent Status" section.
8.  Agents operate within `sandboxes/agent_<id>/` for file system tool operations.

## 🛠️ Development

*   **Code Style:** Follow PEP 8. Use formatters like Black.
*   **Linting:** Use Flake8 or Pylint.
*   **Helper Files:** Keep `helperfiles/PROJECT_PLAN.md` and `helperfiles/FUNCTIONS_INDEX.md` updated! ✍️
*   **Configuration:** Modify `config.yaml` manually OR use the UI (requires restart). Set API keys/URLs/defaults in `.env`.
*   **Branching:** Use feature branches.

## 🙌 Contributing

Contributions welcome! Follow guidelines, open Pull Requests.

## 📜 License

MIT License - See `LICENSE` file for details.
