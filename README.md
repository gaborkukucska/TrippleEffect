<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI. It aims for extensibility to support various LLM API providers (like Ollama, LiteLLM, OpenRouter, Google, Anthropic, DeepSeek, etc.).

## 🎯 Core Concept

The system orchestrates multiple LLM agents, whose number and specific configurations (model, persona, system prompt, etc.) are defined in a central `config.yaml` file ⚙️. Users interact with the system through a web interface to submit tasks via text 📝 (voice 🎤, camera 📸, file uploads 📁 are planned future features).

The agents, loaded based on the configuration, can:
*   Work concurrently on the same task.
*   Collaborate and delegate sub-tasks (future phase 🤝).
*   Utilize tools within sandboxed environments 🛠️.
*   Stream their responses back to the user interface in real-time ⚡.

## ✨ Key Features

*   **Multi-Agent Architecture:** Supports multiple LLM agents working concurrently.
*   **Asynchronous Backend:** Built with FastAPI and `asyncio` for efficient handling of concurrent operations (LLM requests, WebSocket communication).
*   **Browser-Based UI:** Simple web interface for task submission, agent monitoring, and viewing results. Configuration viewing/editing planned.
*   **Real-time Updates:** Uses WebSockets (`/ws`) for instant communication between the backend and the UI.
*   **YAML Configuration:** Easily define and configure agents (ID, model, system prompt, persona, temperature) via `config.yaml`. Defaults can be set via `.env` file.
*   **Sandboxed Workspaces:** Each agent operates within its own dynamically created directory (`sandboxes/agent_<id>/`) for file-based tasks, enhancing security and organization 📁.
*   **Tool Usage (WIP):** Framework planned for agents to use tools (e.g., file system access 📄, web search 🔍) to extend their capabilities.
*   **Extensible Design:** Modular structure for adding new agents (via config), tools, or UI components.
*   **Termux Friendly:** Aims for compatibility and reasonable performance on resource-constrained environments.

## 🏗️ Architecture Overview (Conceptual)

```mermaid
graph LR
    subgraph Frontend
        UI[🌐 Browser UI <br>(HTML/CSS/JS)]
    end

    subgraph Backend
        FASTAPI[🚀 FastAPI Backend <br>(main.py, api/)]
        WS_MANAGER[🔌 WebSocket Manager <br>(api/websocket_manager.py)]
        AGENT_MANAGER[🧑‍💼 Agent Manager <br>(agents/manager.py)]
        subgraph Agents
            direction LR
            AGENT_INST_1[🤖 Agent Instance 1 <br>(agents/core.py)]
            AGENT_INST_2[🤖 Agent Instance 2]
            AGENT_INST_N[🤖 Agent Instance N]
        end
        subgraph Tools
            direction TB
            TOOL_EXECUTOR[🛠️ Tool Executor <br>(tools/executor.py)]
            TOOL_FS[📄 FileSystem Tool <br>(tools/file_system.py)]
            TOOL_WEB[🔍 Web Search Tool]
        end
        SANDBOXES[📁 Sandboxes <br>(sandboxes/agent_id/)]
    end

    subgraph External
        LLM_API[☁️ LLM APIs <br>(OpenAI, etc.)]
        CONFIG[⚙️ config.yaml]
    end

    UI -- HTTP --> FASTAPI;
    UI -- WebSocket /ws <--> WS_MANAGER;
    FASTAPI -- Manages --> AGENT_MANAGER;
    WS_MANAGER -- Forwards/Receives --> AGENT_MANAGER;
    AGENT_MANAGER -- Controls/Coordinates --> AGENT_INST_1;
    AGENT_MANAGER -- Controls/Coordinates --> AGENT_INST_2;
    AGENT_MANAGER -- Controls/Coordinates --> AGENT_INST_N;
    AGENT_MANAGER -- Reads Config --> CONFIG;
    AGENT_INST_1 -- Interacts --> LLM_API;
    AGENT_INST_2 -- Interacts --> LLM_API;
    AGENT_INST_N -- Interacts --> LLM_API;
    AGENT_MANAGER -- Routes Tool Request --> TOOL_EXECUTOR;
    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_WEB;
    AGENT_INST_1 -- File I/O --> SANDBOXES;
    AGENT_INST_2 -- File I/O --> SANDBOXES;
    AGENT_INST_N -- File I/O --> SANDBOXES;
    TOOL_FS -- Operates Within --> SANDBOXES;

```

*   **Browser UI:** Frontend interface (`static/`, `templates/`).
*   **FastAPI Backend:** Serves UI, handles HTTP/WebSocket, orchestrates via `AgentManager`.
*   **WebSocket Manager:** Manages real-time UI communication.
*   **Agent Manager:** Central coordinator; loads config, initializes agents & sandboxes, dispatches tasks.
*   **Agent Instances:** Individual agent objects; interact with LLMs, manage state, use sandbox.
*   **Tools:** Modules providing capabilities (filesystem, web search, etc.).
*   **Sandboxes:** Isolated agent directories (`sandboxes/agent_<id>/`).
*   **LLM APIs:** External services (e.g., OpenAI).
*   **`config.yaml`:** Defines agents and their settings.

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library (initially), `aiohttp` (for potential future HTTP APIs)
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous:** `asyncio`
*   **Configuration:** YAML (`PyYAML`), `.env` files (for secrets/defaults)
*   **Data Handling:** Pydantic (via FastAPI)

## 📁 Directory Structure

```
TrippleEffect/
├── .venv/                  # Virtual environment (Recommended)
├── config.yaml             # Agent configurations <--- ✨ NEW
├── helperfiles/            # Project planning & tracking 📝
│   ├── PROJECT_PLAN.md
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md
├── sandboxes/              # Agent work directories (created at runtime) 📁
│   └── agent_X/
├── src/                    # Source code 🐍
│   ├── agents/             # Agent core logic & manager
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class definition 🤖
│   │   ├── manager.py      # AgentManager class 🧑‍💼
│   │   └── prompts.py      # (Planned) For prompt templates
│   ├── api/                # FastAPI routes & WebSocket logic 🔌
│   │   ├── __init__.py
│   │   ├── http_routes.py  # HTTP endpoints (serving UI)
│   │   └── websocket_manager.py # WebSocket endpoint & broadcast
│   ├── config/             # Configuration loading ⚙️
│   │   ├── __init__.py
│   │   └── settings.py     # Loads .env and config.yaml
│   ├── tools/              # Agent tools implementations 🛠️ (Phase 5+)
│   │   ├── __init__.py
│   │   ├── base.py         # (Planned) Base tool definition
│   │   ├── executor.py     # (Planned) Tool execution logic
│   │   └── file_system.py  # (Planned) File system tool 📄
│   ├── ui/                 # UI backend helpers (if needed)
│   │   └── __init__.py
│   ├── utils/              # Utility functions
│   │   └── __init__.py
│   ├── __init__.py
│   └── main.py             # Application entry point 🚀
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css       # Stylesheets 🎨
│   └── js/
│       └── app.js          # Main frontend JavaScript 💡
├── templates/              # HTML templates (Jinja2)
│   └── index.html          # Main HTML page 📄
├── .env.example            # Example environment variables file <--- NEW
├── .gitignore              # Git ignore file
├── LICENSE                 # Project License (Specify one!) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies 📦
```

## ⚙️ Installation

1.  **Prerequisites:**
    *   Python 3.9+ 🐍
    *   Git
    *   (Termux specific) `pkg install python git openssl-tool libjpeg-turbo libwebp` (potentially more later)

2.  **Clone Repository:**
    ```bash
    git clone https://github.com/gaborkukucska/TrippleEffect.git
    cd TrippleEffect
    ```

3.  **Set up Virtual Environment:** (Recommended)
    ```bash
    python -m venv .venv
    # Activate it:
    # Linux/macOS:
    source .venv/bin/activate
    # Windows:
    # .venv\Scripts\activate
    ```

4.  **Install Dependencies:** 📦
    ```bash
    pip install -r requirements.txt
    ```

5.  **Configure Environment Variables:** 🔑
    *   Copy `.env.example` to `.env`:
        ```bash
        cp .env.example .env
        ```
    *   Edit the `.env` file and add your OpenAI API key:
        ```dotenv
        # .env
        OPENAI_API_KEY=your_secret_openai_api_key_here

        # Optional: Set default agent parameters if config.yaml is missing/incomplete
        # DEFAULT_AGENT_MODEL=gpt-3.5-turbo
        # DEFAULT_SYSTEM_PROMPT="You are a helpful assistant."
        # DEFAULT_TEMPERATURE=0.7
        # DEFAULT_PERSONA="General Assistant"
        ```
    *   **Important:** Ensure `.env` is listed in your `.gitignore` file to avoid committing secrets!

6.  **Configure Agents:** 🧑‍🔧🧑‍🏫🧑‍🚒
    *   Edit the `config.yaml` file in the project root.
    *   Define the `agent_id`, `model`, `system_prompt`, `temperature`, and `persona` for each agent you want to run. Refer to the example structure provided.

## ▶️ Running the Application

```bash
python src/main.py
```

*   The server will start (usually on port 8000).
*   It will load agents based on `config.yaml` during startup. Check the console output for details.
*   Access the UI in your web browser: `http://localhost:8000` (or `http://<your-termux-ip>:8000` if on Termux).

## 🖱️ Usage

1.  Open the web UI in your browser.
2.  The backend automatically loads the agents defined in `config.yaml`. You should see a "Connected" status message.
3.  Type your task or question into the input box ⌨️ and press Enter or click "Send".
4.  The task will be sent concurrently to all initialized and *available* agents.
5.  Observe the agents' responses streaming back into the message area, identified by their `agent_id` and styled differently based on the CSS 🎨.
6.  Agents operate within their respective `sandboxes/agent_<id>/` directories (this becomes relevant when file-system tools are used).

## 🛠️ Development

*   **Code Style:** Follow PEP 8. Consider using formatters like Black.
*   **Linting:** Use Flake8 or Pylint to catch errors.
*   **Helper Files:** Keep `helperfiles/PROJECT_PLAN.md` and `helperfiles/FUNCTIONS_INDEX.md` updated! ✍️
*   **Configuration:** Modify `config.yaml` to add/change agents. Set API keys and defaults in `.env`.
*   **Branching:** Use feature branches (e.g., `feat/filesystem-tool`, `fix/ui-streaming`).

## 🙌 Contributing

Contributions are welcome! Please follow the development guidelines and open a Pull Request on GitHub.

## 📜 License

(Please specify a license here, e.g., MIT License)
