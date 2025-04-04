<!-- # START OF FILE README.md -->
# TrippleEffect 🧑‍🚒🧑‍🏫👩‍🔧

**TrippleEffect** is an asynchronous, collaborative multi-agent framework designed with a browser-based user interface 🌐, optimized for environments like Termux 📱. It allows multiple Language Model (LLM) agents 🤖🤖🤖 to work together on complex tasks, coordinated through a central backend and managed via a web UI. It aims for extensibility and supports various LLM API providers, including **OpenRouter**, **Ollama**, and **OpenAI** (and can be extended to others like LiteLLM, Google, Anthropic, etc.).

## 🎯 Core Concept

The system orchestrates multiple LLM agents, whose number and specific configurations (**provider**, model, persona, system prompt, etc.) are defined in a central `config.yaml` file ⚙️. Users interact with the system through a web interface to submit tasks via text 📝 (voice 🎤, camera 📸, file uploads 📁 are planned future features).

The agents, loaded based on the configuration:
*   Interact with their configured LLM provider (**OpenRouter**, **Ollama**, **OpenAI**, etc.).
*   Can work concurrently on the same task.
*   Collaborate and delegate sub-tasks (future phase 🤝).
*   Utilize tools within sandboxed environments 🛠️ (supported across providers where models allow).
*   Stream their responses back to the user interface in real-time ⚡.

## ✨ Key Features

*   **Multi-Agent Architecture:** Supports multiple LLM agents working concurrently.
*   **Asynchronous Backend:** Built with FastAPI and `asyncio` for efficient handling of concurrent operations.
*   **Browser-Based UI:** Simple web interface for task submission, agent monitoring, viewing configurations, and results.
*   **Real-time Updates:** Uses WebSockets (`/ws`) for instant communication.
*   **Multi-Provider LLM Support:** Connect agents to different LLM backends (**OpenRouter**, local **Ollama**, **OpenAI**, easily extensible).
*   **YAML Configuration:** Easily define agents (ID, **provider**, model, system prompt, persona, temperature, provider-specific args) via `config.yaml`. Defaults and API keys/URLs set via `.env`.
*   **Sandboxed Workspaces:** Each agent operates within its own directory (`sandboxes/agent_<id>/`) for file-based tasks 📁.
*   **Tool Usage:** Framework allows agents to use tools (e.g., file system access 📄, web search 🔍 planned). Tool support relies on the capabilities of the chosen LLM model and provider implementation.
*   **Extensible Design:** Modular structure (`src/llm_providers`, `src/tools`) for adding new LLM providers, agents, or tools.
*   **Termux Friendly:** Aims for compatibility and reasonable performance on resource-constrained environments. Requires specific build tools.

## 🏗️ Architecture Overview (Conceptual - Corrected Mermaid)

```mermaid
graph LR
    subgraph Frontend
        UI["🌐 Browser UI <br>(HTML/CSS/JS)<br>Includes Config View"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>(main.py, api/)<br>+ Config Read Route"]
        WS_MANAGER["🔌 WebSocket Manager <br>(api/websocket_manager.py)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(agents/manager.py)"]
        subgraph Agents
            direction LR
            AGENT_INST_1["🤖 Agent Instance 1 <br>(agents/core.py)<br>Uses Provider A"]
            AGENT_INST_2["🤖 Agent Instance 2 <br>Uses Provider B"]
            AGENT_INST_N["🤖 Agent Instance N <br>Uses Provider C"]
        end
        subgraph LLM_Providers ["☁️ LLM Providers <br>(src/llm_providers/)"]
            PROVIDER_A["🔌 Provider A <br>(e.g., OpenAI)"]
            PROVIDER_B["🔌 Provider B <br>(e.g., Ollama)"]
            PROVIDER_C["🔌 Provider C <br>(e.g., OpenRouter)"]
        end
        subgraph Tools
            direction TB
            TOOL_EXECUTOR["🛠️ Tool Executor <br>(tools/executor.py)"]
            TOOL_FS["📄 FileSystem Tool <br>(tools/file_system.py)"]
            TOOL_WEB["🔍 Web Search Tool (Planned)"]
        end
        SANDBOXES["📁 Sandboxes <br>(sandboxes/agent_id/)"]
    end

    subgraph External
        LLM_API_SVC["☁️ External LLM APIs <br>(OpenAI, OpenRouter)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(Read Only by App)"]
        DOT_ENV[".env File <br>(API Keys, URLs - Read Only by App)"]
    end

    %% --- Connections ---
    UI -- HTTP --> FASTAPI;
    UI -- "WebSocket /ws" <--> WS_MANAGER;
    FASTAPI -- Manages --> AGENT_MANAGER;
    FASTAPI -- "Config Read API /api/config/agents" --> CONFIG_YAML; %% Reads config via settings
    WS_MANAGER -- "Forwards/Receives" --> AGENT_MANAGER;
    AGENT_MANAGER -- "Controls/Coordinates" --> AGENT_INST_1;
    AGENT_MANAGER -- "Controls/Coordinates" --> AGENT_INST_2;
    AGENT_MANAGER -- "Controls/Coordinates" --> AGENT_INST_N;
    AGENT_MANAGER -- "Reads Config" --> CONFIG_YAML; %% Via settings
    AGENT_MANAGER -- "Reads Defaults/Secrets" --> DOT_ENV; %% Via settings
    AGENT_MANAGER -- "Instantiates & Injects" --> LLM_Providers;
    AGENT_INST_1 -- Uses --> PROVIDER_A;
    AGENT_INST_2 -- Uses --> PROVIDER_B;
    AGENT_INST_N -- Uses --> PROVIDER_C;
    PROVIDER_A -- Interacts --> LLM_API_SVC;
    PROVIDER_B -- Interacts --> OLLAMA_SVC;
    PROVIDER_C -- Interacts --> LLM_API_SVC;
    AGENT_MANAGER -- "Routes Tool Request" --> TOOL_EXECUTOR;
    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_WEB;
    %% Tool requests flow through provider
    AGENT_INST_1 -- "Requests Tools Via Provider" --> LLM_Providers;
    AGENT_INST_1 -- "File I/O Via Tool" --> SANDBOXES;
    TOOL_FS -- "Operates Within" --> SANDBOXES;

```

*   **LLM Providers (`src/llm_providers/`):** New layer abstracting interaction with different LLM APIs (OpenAI, Ollama, OpenRouter).
*   **Agent Instances:** Use an injected LLM provider instance.
*   **`.env`:** Crucial for storing API keys and provider base URLs.

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library (used by OpenAI & OpenRouter providers), `aiohttp` (used by Ollama provider)
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous:** `asyncio`
*   **Configuration:** YAML (`PyYAML`), `.env` files (`python-dotenv`)
*   **Data Handling:** Pydantic (via FastAPI)

## 📁 Directory Structure

```TrippleEffect/
├── .venv/
├── config.yaml             # Agent configurations (provider, model, etc.) ✨ UPDATED
├── helperfiles/            # Project planning & tracking 📝
│   ├── PROJECT_PLAN.md
│   ├── DEVELOPMENT_RULES.md
│   └── FUNCTIONS_INDEX.md
├── sandboxes/              # Agent work directories (created at runtime) 📁
│   └── agent_X/
├── src/                    # Source code 🐍
│   ├── agents/             # Agent core logic & manager
│   │   ├── __init__.py
│   │   ├── core.py         # Agent class (uses injected provider) 🤖
│   │   └── manager.py      # AgentManager (instantiates providers) 🧑‍💼
│   ├── api/                # FastAPI routes & WebSocket logic 🔌
│   │   ├── __init__.py
│   │   ├── http_routes.py
│   │   └── websocket_manager.py
│   ├── config/             # Configuration loading ⚙️
│   │   ├── __init__.py
│   │   └── settings.py     # Loads .env and config.yaml (provider keys/URLs) ✨ UPDATED
│   ├── llm_providers/      # LLM provider implementations <--- ✨ NEW
│   │   ├── __init__.py
│   │   ├── base.py         # BaseLLMProvider ABC
│   │   ├── ollama_provider.py
│   │   ├── openai_provider.py
│   │   └── openrouter_provider.py
│   ├── tools/              # Agent tools implementations 🛠️
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── executor.py
│   │   └── file_system.py
│   ├── ui/                 # UI backend helpers (if needed)
│   │   └── __init__.py
│   ├── utils/              # Utility functions
│   │   └── __init__.py
│   ├── __init__.py
│   └── main.py             # Application entry point 🚀
├── static/                 # Frontend static files 🌐
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js          # ✨ UPDATED for Config View
├── templates/              # HTML templates (Jinja2)
│   └── index.html
├── .env.example            # Example environment variables ✨ UPDATED
├── .gitignore
├── LICENSE                 # Project License (Specify one!) 📜
├── README.md               # This file! 📖
└── requirements.txt        # Python dependencies 📦 ✨ UPDATED (uvicorn)
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
    *   **Edit the `.env` file:** This is crucial for connecting to LLM providers. Prioritize setting up OpenRouter and Ollama if you plan to use them.
        ```dotenv
        # .env (Fill with your actual values)

        # --- OpenRouter (Recommended for various models) ---
        # Get your key from https://openrouter.ai/keys
        OPENROUTER_API_KEY=sk-or-v1-your-openrouter-key...
        # Optional: Define a site URL or app name for OpenRouter stats
        # See https://openrouter.ai/docs#headers
        OPENROUTER_REFERER=http://localhost:8000/ # Or your app name/URL
        # OPENROUTER_BASE_URL= # Usually not needed unless using a proxy

        # --- Ollama (For local models) ---
        # Default assumes Ollama runs locally on port 11434. Adjust if needed.
        OLLAMA_BASE_URL=http://localhost:11434

        # --- OpenAI (Optional) ---
        # Only needed if you specifically configure an agent to use 'openai' provider
        OPENAI_API_KEY=sk-your-openai-key...
        # OPENAI_BASE_URL= # e.g., for Azure endpoints

        # --- Default agent params (Optional fallbacks) ---
        # If an agent in config.yaml doesn't specify these, these values are used.
        # DEFAULT_AGENT_PROVIDER="openrouter" # Example: Make OpenRouter the default
        # DEFAULT_AGENT_MODEL="mistralai/mistral-7b-instruct" # Example model
        # DEFAULT_SYSTEM_PROMPT="You are a helpful assistant."
        # DEFAULT_TEMPERATURE=0.7
        # DEFAULT_PERSONA="General Assistant"
        ```
    *   **Important:** Ensure `.env` is listed in your `.gitignore` file (it should be by default). **Never commit your API keys!**

6.  **Configure Agents:** 🧑‍🔧🧑‍🏫🧑‍🚒
    *   Edit the `config.yaml` file in the project root. This defines *which* agents run and *how* they behave.
    *   For each agent, define:
        *   `agent_id`: Unique identifier (e.g., "coder", "researcher").
        *   `provider`: **Crucial.** Must be `"openrouter"`, `"ollama"`, or `"openai"`. Ensure the corresponding API key/URL is set in `.env`.
        *   `model`: The model name specific to the chosen provider (e.g., `"mistralai/mistral-7b-instruct"` for OpenRouter, `"llama3"` for Ollama, `"gpt-4-turbo"` for OpenAI). Make sure the model is available on the provider (or pulled in Ollama).
        *   `system_prompt`: Instructions for the agent's persona and task focus.
        *   `temperature`: Controls creativity vs. determinism (0.0 to ~1.0+).
        *   `persona`: A name for the agent displayed in the UI.
        *   *(Optional)*: You can override provider settings per-agent (e.g., `base_url`), but API keys should stay in `.env`.
    *   **Example `config.yaml` snippet:**
        ```yaml
        agents:
          - agent_id: "analyst_or"
            config:
              provider: "openrouter" # Using OpenRouter
              model: "mistralai/mistral-7b-instruct" # Check OpenRouter for available models
              system_prompt: "You analyze text and data. Use tools if needed to read files."
              temperature: 0.6
              persona: "Data Analyst (OpenRouter)"

          - agent_id: "creative_local"
            config:
              provider: "ollama" # Using local Ollama
              model: "llama3" # Ensure 'llama3' is pulled in Ollama
              system_prompt: "You are a creative writer."
              temperature: 0.9
              persona: "Creative Writer (Ollama)"

          - agent_id: "coder_openai" # Optional OpenAI example
            config:
              provider: "openai"
              model: "gpt-4-turbo-preview"
              system_prompt: "You are an expert Python programmer."
              temperature: 0.2
              persona: "Python Expert (OpenAI)"
        ```

## ▶️ Running the Application

```bash
python src/main.py
```

*   The server will start (usually on `http://0.0.0.0:8000`).
*   It loads agents based on `config.yaml` and uses API keys/URLs from `.env` to initialize providers. Check console output for details and potential configuration warnings (e.g., missing keys for configured providers).
*   Access the UI in your web browser: `http://localhost:8000` (or your machine's IP if accessing from another device).

## 🖱️ Usage

1.  Open the web UI.
2.  The backend loads agents. You should see a "Connected" status and agent configurations/statuses loaded in their respective UI sections.
3.  Type your task into the input box ⌨️ (optionally attach a text file using the 📎 button).
4.  Send the message. The task goes concurrently to all initialized and *available* (idle) agents.
5.  Observe responses streaming back in the "Conversation Area", identified by `agent_id`. System messages and errors appear in the "System Logs & Status" area.
6.  Agent behavior (including tool use) depends on the configured provider and model. Check agent status updates in the "Agent Status" section.
7.  Agents operate within `sandboxes/agent_<id>/` for file system tool operations.

## 🛠️ Development

*   **Code Style:** Follow PEP 8. Use formatters like Black.
*   **Linting:** Use Flake8 or Pylint.
*   **Helper Files:** Keep `helperfiles/PROJECT_PLAN.md` and `helperfiles/FUNCTIONS_INDEX.md` updated! ✍️
*   **Configuration:** Modify `config.yaml` to add/change agents/providers. Set API keys/URLs/defaults in `.env`. (UI config editing planned for Phase 8).
*   **Branching:** Use feature branches.

## 🙌 Contributing

Contributions welcome! Follow guidelines, open Pull Requests.

## 📜 License

(Specify MIT License, Apache 2.0, etc.)
