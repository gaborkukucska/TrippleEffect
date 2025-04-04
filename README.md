<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface, optimized for environments like Termux. It allows multiple Language Model (LLM) agents to work together on complex tasks, coordinated through a central backend and managed via a web UI powered by multiple external API providers like Ollama, LiteLLM, Openrouter, Google, Anthropic and DeepSeek (Extensibility for various providers planned).

## Core Concept

The system orchestrates multiple LLM agents, whose number and specific configurations (model, persona, system prompt, etc.) are defined in a central `config.yaml` file. Users interact with the system through a web interface to submit tasks via text (voice, camera, file uploads are planned). The agents, loaded based on the configuration, can collaborate (future phase), delegate sub-tasks (future phase), utilize tools within sandboxed environments, and stream their responses back to the user interface in real-time.

## Key Features

*   **Multi-Agent Collaboration:** Supports multiple LLM agents working concurrently (collaboration logic planned).
*   **Asynchronous Architecture:** Built with FastAPI and asyncio for efficient handling of concurrent operations (LLM requests, WebSocket communication).
*   **Browser-Based UI:** Rich web interface for task submission, agent monitoring, and viewing results. Configuration viewing/editing planned.
*   **Real-time Communication:** Uses WebSockets for instant updates between the backend and the UI.
*   **Configurable Agents:** Easily define and configure agent parameters (ID, model, system prompt, persona, temperature) via `config.yaml`. Defaults can be set via environment variables.
*   **Sandboxed Environments:** Each agent operates within its own dynamically created directory (`sandboxes/agent_<id>/`) for file-based tasks, enhancing security and organization.
*   **Tool Usage:** Agents can be equipped with tools (e.g., file system access, web search) to extend their capabilities (implementation in progress).
*   **Extensible Framework:** Designed to be modular for adding new agents (via config), tools, or UI components.
*   **Termux Friendly:** Aims for compatibility and reasonable performance on resource-constrained environments like Termux.

## Architecture Overview (Conceptual)


+---------------------+ +-----------------------+ +---------------------+
| Browser UI |<---->| FastAPI Backend |<---->| LLM APIs (OpenAI) |
| (HTML/CSS/JS) | | (main.py, api/) | +---------------------+
+---------------------+ +-----------------------+
| | ▲ | (Loads Config)
(WebSocket /ws) | | | (Agent Core, Tools) |
| ▼ | ▼ ▼
+---------------------+ +-----------------------+ +---------------------+ +-------------+
| WebSocket Manager |<---->| Agent Coordinator |<---->| Agent Instances |<--| config.yaml |
| (in main.py/api/) | | (src/agents/manager.py) | | (src/agents/core.py)| +-------------+
+---------------------+ +-----------------------+ +----------+----------+
| |
▼ (Tool Execution) ▼ (File I/O)
+-----------------+ +----------------------+
| Tools (src/tools) | | Sandboxes (sandboxes/)|
+-----------------+ +----------------------+

*   **Browser UI:** Frontend interface for user interaction.
*   **FastAPI Backend:** Serves the UI, handles HTTP requests, manages WebSocket connections, and orchestrates agent actions via the `AgentManager`.
*   **WebSocket Manager:** Handles real-time communication between UI and backend, forwarding messages to/from the `AgentManager`.
*   **Agent Coordinator (`AgentManager`):** Reads `config.yaml` via `settings`, initializes agents, ensures sandboxes exist, manages the lifecycle and interaction logic between agents, handles task dispatching.
*   **Agent Instances (`Agent`):** Individual agent objects, each configured via `config.yaml`, interacting with an LLM API, managing state, and operating within its sandbox.
*   **Tools:** Modules providing specific capabilities to agents (implementation starting). Agents can be tasked to create more tools that user can then set up and activate (future goal).
*   **Sandboxes:** Isolated directories (`sandboxes/agent_<id>/`) automatically created for agent file operations.
*   **LLM APIs:** External providers (currently OpenAI via `openai` library).
*   **`config.yaml`:** File defining the agents to be loaded and their specific configurations.

## Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library initially, `aiohttp` for other potential HTTP-based APIs.
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (using `PyYAML`), `.env` files (for API keys, defaults).
*   **Data Handling:** Pydantic (via FastAPI)

## Directory Structure
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
IGNORE_WHEN_COPYING_END

TrippleEffect/
├── .venv/ # Virtual environment (Recommended)
├── config.yaml # Agent configurations <--- NEW
├── helperfiles/ # Project rules, planning, function index, etc.
│ ├── PROJECT_PLAN.md
│ ├── DEVELOPMENT_RULES.md
│ └── FUNCTIONS_INDEX.md
├── sandboxes/ # Agent-specific work directories (created at runtime)
│ └── agent_X/
├── src/ # Source code
│ ├── agents/ # Agent core logic, coordinator
│ │ ├── init.py
│ │ ├── core.py # Agent class definition
│ │ ├── manager.py # AgentManager class
│ │ └── prompts.py # (Planned) For storing prompt templates/fragments
│ ├── api/ # FastAPI routes and WebSocket logic
│ │ ├── init.py
│ │ ├── http_routes.py # HTTP endpoints (e.g., serving UI)
│ │ └── websocket_manager.py # WebSocket endpoint & broadcasting
│ ├── config/ # Configuration loading logic
│ │ ├── init.py
│ │ └── settings.py # Loads .env and config.yaml
│ ├── tools/ # Agent tools implementations (Phase 5+)
│ │ ├── init.py
│ │ ├── base.py # (Planned) Base tool definition
│ │ ├── executor.py # (Planned) Tool execution logic
│ │ └── file_system.py # (Planned) File system tool
│ ├── ui/ # UI-related backend helpers (if needed)
│ │ └── init.py
│ ├── utils/ # Utility functions
│ │ └── init.py
│ ├── init.py
│ └── main.py # Application entry point
├── static/ # Frontend static files
│ ├── css/
│ │ └── style.css
│ └── js/
│ └── app.js # Main frontend JavaScript
├── templates/ # HTML templates (if using Jinja2 or similar)
│ └── index.html
├── .env.example # Example environment variables file <--- (Recommended addition)
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt

*(Added `config.yaml`, `.env.example`, updated descriptions)*

## Installation

1.  **Prerequisites:**
    *   Python 3.9+
    *   Git
    *   (Termux specific) `pkg install python git openssl-tool libjpeg-turbo libwebp` (may need more depending on specific tools later)

2.  **Clone Repository:**
    ```bash
    git clone https://github.com/gaborkukucska/TrippleEffect.git
    cd TrippleEffect
    ```

3.  **Set up Virtual Environment:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
    ```

4.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **Configure Environment Variables:**
    *   Create a `.env` file in the project root (copy from `.env.example` if provided).
    *   Add your OpenAI API key to the `.env` file:
        ```.env
        OPENAI_API_KEY=your_api_key_here
        # Optional: Set default agent parameters if config.yaml is missing/incomplete
        # DEFAULT_AGENT_MODEL=gpt-3.5-turbo
        # DEFAULT_SYSTEM_PROMPT=You are a helpful assistant.
        # DEFAULT_TEMPERATURE=0.7
        ```
    *   Ensure `.env` is listed in your `.gitignore` file!

6.  **Configure Agents:**
    *   Edit the `config.yaml` file in the project root.
    *   Define the `agent_id`, `model`, `system_prompt`, `temperature`, and `persona` for each agent you want to run. Refer to the example `config.yaml` provided in the repository (or create one based on the structure).

## Running the Application

```bash
python src/main.py
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
IGNORE_WHEN_COPYING_END

Access the UI in your web browser at http://<your-ip-address>:8000 (or http://localhost:8000 if running locally on a desktop). In Termux, find your IP using the ifconfig command. The application will load agents based on config.yaml on startup.

Usage

Open the web UI.

The backend automatically loads agents defined in config.yaml. Status messages during initialization might appear in the console where you ran python src/main.py.

Use the main interface to submit tasks (text input) to the agent system.

The task will be sent concurrently to all initialized and available agents.

Observe the agents' responses streamed back to the UI, identified by their agent_id.

Agents operate within their respective sandboxes/agent_<id>/ directories (relevant for future tool usage).

Development

Code Style: Follow PEP 8 guidelines. Use a formatter like Black (optional but recommended).

Linting: Use Flake8 or Pylint (optional but recommended).

Helper Files: Keep helperfiles/PROJECT_PLAN.md and helperfiles/FUNCTIONS_INDEX.md updated during development.

Configuration: Modify config.yaml to add/change agents. Set API keys and defaults in .env.

Branching: Use feature branches for new development (e.g., feat/filesystem-tool, fix/ui-streaming).

Contributing

Contributions are welcome! Please follow the development guidelines and open a Pull Request. (Further details can be added later).

License

(Specify License - e.g., MIT License - Still needs specification)
