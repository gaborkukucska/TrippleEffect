<!-- START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface, optimized for environments like Termux. It allows multiple Language Model (LLM) agents to work together on complex tasks, coordinated through a central backend and managed via a web UI powered by multiple external API providers like Ollama, LiteLLM, Openrouter, Google, Anthropic and DeepSeek.

## Core Concept

The system orchestrates a configurable number of LLM agents (defaulting to three, hence the name "TrippleEffect"). Users interact with the system through a web interface to submit tasks via text, voice, camera, or file uploads. The agents are fully customisable (provider, model, system prompt, persona, temperature, top-p, etc., can collaborate, delegate sub-tasks, utilize tools within sandboxed environments, and stream their responses back to the user interface in real-time.

## Key Features

*   **Multi-Agent Collaboration:** Supports multiple LLM agents working concurrently and cooperatively.
*   **Asynchronous Architecture:** Built with FastAPI and asyncio for efficient handling of concurrent operations (LLM requests, WebSocket communication).
*   **Browser-Based UI:** Rich web interface for task submission, agent monitoring, configuration, and viewing results.
*   **Real-time Communication:** Uses WebSockets for instant updates between the backend and the UI.
*   **Configurable Agents:** Easily configure agent parameters (model, system prompt, tools) via settings.
*   **Sandboxed Environments:** Each agent operates within its own directory (`sandboxes/agent_<id>`) for file-based tasks, enhancing security and organization (implementation in progress).
*   **Tool Usage:** Agents can be equipped with tools (e.g., file system access, web search, database) to extend their capabilities (implementation in progress).
*   **Extensible Framework:** Designed to be modular for adding new agents, tools, or UI components.
*   **Termux Friendly:** Aims for compatibility and reasonable performance on resource-constrained environments like Termux.

## Architecture Overview (Conceptual)

```
+---------------------+      +-----------------------+      +---------------------+
|   Browser UI        |<---->|   FastAPI Backend     |<---->|   LLM APIs (OpenAI) |
| (HTML/CSS/JS)       |      | (main.py, api/)       |      +---------------------+
+---------------------+      +-----------------------+
       |                            | ▲ |
 (WebSocket /ws)                    | | | (Agent Core, Tools)
       |                            ▼ | ▼
+---------------------+      +-----------------------+      +---------------------+
| WebSocket Manager   |<---->|   Agent Coordinator   |<---->|     Agent Instances |
| (in main.py/api/)   |      | (src/agents/manager.py) |      | (src/agents/core.py)|
+---------------------+      +-----------------------+      +----------+----------+
                                     |                                |
                                     ▼ (Tool Execution)               ▼ (File I/O)
                                +-----------------+             +----------------------+
                                | Tools (src/tools) |             | Sandboxes (sandboxes/)|
                                +-----------------+             +----------------------+
```

*   **Browser UI:** Frontend interface for user interaction.
*   **FastAPI Backend:** Serves the UI, handles HTTP requests, manages WebSocket connections, and orchestrates agent actions.
*   **WebSocket Manager:** Handles real-time communication between UI and backend.
*   **Agent Coordinator:** Manages the lifecycle and interaction logic between agents.
*   **Agent Instances:** Individual agent objects, each potentially interacting with a separate LLM API.
*   **Tools:** Modules providing specific capabilities to agents, agents can be tasked to create more tools that user can then set up and activate.
*   **Sandboxes:** Isolated directories and conda environments for agent file operations and command executions.
*   **LLM APIs:** External providers.

## Technology Stack

*   **Backend:** Python 3.x, FastAPI, Uvicorn, WebSockets (fastapi.websockets, websockets library)
*   **LLM Interaction:** OpenAI Python Library
*   **Frontend:** HTML5, CSS3, JavaScript (Vanilla JS or potentially a lightweight framework later)
*   **Asynchronous Operations:** asyncio, aiohttp

## Directory Structure

```
TrippleEffect/
├── .venv/                  # Virtual environment (Recommended)
├── helperfiles/            # Project planning, function index, etc.
│   ├── PROJECT_PLAN.md
│   └── FUNCTIONS_INDEX.md
├── sandboxes/              # Agent-specific work directories (created at runtime)
│   └── agent_X/
├── src/                    # Source code
│   ├── agents/             # Agent core logic, coordinator
│   │   ├── __init__.py
│   │   ├── core.py
│   │   └── manager.py      # (Proposed)
│   ├── api/                # FastAPI routes and WebSocket logic
│   │   ├── __init__.py
│   │   └── routes.py       # (Proposed)
│   ├── tools/              # Agent tools implementations
│   │   └── __init__.py
│   ├── ui/                 # UI-related backend helpers (if needed)
│   │   └── __init__.py
│   ├── utils/              # Utility functions
│   │   └── __init__.py
│   ├── __init__.py
│   └── main.py             # Application entry point
├── static/                 # Frontend static files
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js          # Main frontend JavaScript
├── templates/              # HTML templates (if using Jinja2 or similar)
│   └── index.html
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

## Installation ( To Be Updated )

1.  **Prerequisites:**
    *   Python 3.8+
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

5.  **Configure API Keys:**
    *   Set your OpenAI API key as an environment variable:
        ```bash
        export OPENAI_API_KEY='your_api_key_here'
        ```
    *   Alternatively, create a `.env` file in the project root (ensure it's in `.gitignore`!) and the application will load it (requires `python-dotenv` package - to be added).
        ```.env
        OPENAI_API_KEY=your_api_key_here
        ```

## Running the Application ( To Be Updated )

```bash
python src/main.py
```

Access the UI in your web browser at `http://<your-ip-address>:8000` (or `http://localhost:8000` if running locally on a desktop). In Termux, find your IP using the `ifconfig` command.

## Usage ( To Be Updated )

1.  Open the web UI.
2.  (Planned) Navigate to the Settings page to configure agents (models, prompts, tools).
3.  Use the main interface to submit tasks to the agent system.
4.  Observe the agents' collaboration and results in the output area.

## Development ( To Be Updated )

*   **Code Style:** Follow PEP 8 guidelines. Use a formatter like Black (optional but recommended).
*   **Linting:** Use Flake8 or Pylint (optional but recommended).
*   **Helper Files:** Keep `helperfiles/PROJECT_PLAN.md` and `helperfiles/FUNCTIONS_INDEX.md` updated during development.
*   **Branching:** Use feature branches for new development (e.g., `feat/agent-communication`, `fix/ui-bug`).

## Contributing ( To Be Updated )

Contributions are welcome! Please follow the development guidelines and open a Pull Request. (Further details can be added later).

## License

(Specify License - e.g., MIT License)
