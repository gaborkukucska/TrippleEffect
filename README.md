<!-- # START OF FILE README.md -->
<!-- # IT IS CRITICAL THAT ALL AIs and LLMs FOLLOW THE DEVELOPMENT INSTRUCTIONS IN THE DEVELOPMENT_RULES.md FILE WHEN FURTHER DEVELOPING THIS FRAMEWORK!!! -->
# TrippleEffect Multi-Agent Framework

**Version:** 2.25 <!-- Updated Version -->

**TrippleEffect** is an asynchronous, collaborative multi-agent framework built with Python, FastAPI, and WebSockets. It features a central **Admin AI** that initiates projects and a dedicated **Project Manager** agent per session that handles detailed task creation/tracking and agent/team creation/coordination. This framework is predominantly developed by various LLMs guided by Gabby.

## Quick Start (using scripts)

For a faster setup, you can use the provided shell scripts but make sure they are executable:
```bash
chmod +x setup.sh run.sh
```

1.  **Run Setup:** `./setup.sh` (This usually creates the environment, installs dependencies, and copies `.env.example`).
2.  **Configure:** Edit the created `.env` file with your API keys (OpenAI, OpenRouter, GitHub PAT, Tavily).
3.  **Run:** `./run.sh` (This typically activates the environment and starts the application using `python -m src.main`).
4.  **Access UI:** Open your browser to `http://localhost:8000`.

*(See the detailed "Setup and Running" section below for manual steps and configuration options.)*

## Core Concepts

*   **Stateful Admin AI:** The central agent (`admin_ai`) operates using a state machine (`conversation`, `planning`, etc.). In the `conversation` state, it interacts with the user and monitors ongoing projects via their PMs. When an actionable request is identified, it transitions to the `planning` state.
*   **Framework-Driven Project Initiation:** When Admin AI submits a plan (including a `<title>`) in the `planning` state, the framework automatically:
    *   Creates a project task in Taskwarrior using the title and plan.
    *   Creates a dedicated Project Manager agent (`pm_{project_title}_{session_id}`).
    *   Assigns the new PM to the initial project task.
    *   Notifies Admin AI and transitions it back to the `conversation` state.
*   **Project Manager Agent:** Automatically created per project/session by the framework, this agent uses the `ProjectManagementTool` (backed by `tasklib`) to decompose the initial plan, create a team, specialised worker agents, create/assign sub-tasks to worker agents, monitor progress via `send_message` tool, and report status/completion back to Admin AI.
*   **Dynamic Worker Agent Management:** The Project Manager agent (or Admin AI, depending on workflow evolution) uses `ManageTeamTool` to create worker agents as needed for specific sub-tasks.
*   **Intelligent Model Handling:**
    *   **Discovery:** Automatically finds reachable LLM providers (Ollama, OpenRouter, OpenAI) and available models at startup.
    *   **Filtering:** Filters discovered models based on the `MODEL_TIER` setting (`.env`).
    *   **Auto-Selection:** Automatically selects the best model for Admin AI (at startup) and dynamic agents (at creation if not specified). Selection priority is now Tier -> Model Size (parameter count, larger preferred) -> Performance Score -> ID. `num_parameters` are discovered for providers like OpenRouter and Ollama where available.
    *   **Failover:** Automatic API key cycling and model/provider failover (Local -> Free -> Paid tiers) on persistent errors during generation. Model selection during failover also respects the new Size/Performance priority.
    *   **Performance Tracking:** Records success rate and latency per model, persisting data (`data/model_performance_metrics.json`).
*   **Tool-Based Interaction:** Agents use tools via an **XML format**. The framework can now process multiple distinct tool calls found in a single agent response; these are executed sequentially, and all results are then fed back to the agent in the next turn.
*   **Context Management:** Standardized instructions are injected, agents are guided to use file operations for large outputs. Admin AI receives current time context.
*   **Communication Layer Separation (UI):** The user interface visually separates direct User<->Admin AI interaction from internal Admin AI<->PM<->Worker communication and system events.
*   **Persistence:** Session state (agents, teams, histories) can be saved/loaded (filesystem). Interactions and knowledge are logged to a database (`data/trippleeffect_memory.db`).
*   **KnowledgeBaseTool Enhancements:** Agent thoughts are saved with automatically generated keywords. A new `search_agent_thoughts` action allows targeted retrieval of past agent reasoning.
*   **Basic Governance Layer:** System principles can be defined in `governance.yaml` and are automatically injected into relevant agent prompts to guide behavior.

## Features

*   **Asynchronous Backend:** Built with FastAPI and `asyncio`.
*   **WebSocket Communication:** Real-time updates via WebSockets.
*   **Dynamic Agent/Team Creation:** Manage agents and teams on the fly using `ManageTeamTool`.
*   **Configurable Model Selection:**
    *   Dynamic discovery of providers/models (Ollama, OpenRouter, OpenAI).
    *   Filtering based on `MODEL_TIER` (.env: `FREE` or `ALL`).
    *   Automatic model selection for Admin AI and dynamic agents, now prioritizing Tier -> Size -> Performance Score -> ID. `num_parameters` are discovered for some providers (e.g., OpenRouter, Ollama).
*   **Robust Error Handling:**
    *   Automatic retries for transient LLM API errors.
    *   Multi-key support and key cycling for providers (`PROVIDER_API_KEY_N` in `.env`).
    *   Automatic failover to different models/providers based on tiers (Local -> Free -> Paid).
    *   Key quarantining on persistent auth/rate limit errors.
*   **Performance Tracking:** Monitors success rate and latency per model, saved to `data/model_performance_metrics.json`.
*   **State-Driven Admin AI Workflow:** Admin AI operates based on its current state (`conversation`, `planning`).
    *   **Conversation State:** Focuses on user interaction, KB search/save, monitoring PM updates, and identifying new tasks. Uses `<request_state state='planning'>` to signal task identification.
    *   **Planning State:** Focuses solely on creating a plan with a `<title>` tag. Framework handles project/PM creation upon plan submission.
*   **XML Tooling:** Agents request tool use via XML format. Available tools:
    *   `FileSystemTool`: Read, Write, List, Mkdir, Delete (File/Empty Dir), Find/Replace in private sandbox or shared workspace.
    *   `GitHubTool`: List Repos, List Files (Recursive), Read File content using PAT.
    *   `ManageTeamTool`: Create/Delete Agents/Teams, Assign Agents, List Agents/Teams, Get Agent Details.
    *   `SendMessageTool`: Communicate between agents within a team or with Admin AI (using exact agent IDs).
    *   `WebSearchTool`: Search the web (uses Tavily API if configured, falls back to DDG scraping).
    *   `SystemHelpTool`: Get current time (UTC), Search application logs, **Get detailed tool usage info (`get_tool_info`)**.
    *   `KnowledgeBaseTool`: Save/Search distilled knowledge in the database. Now includes smarter keyword generation for saved thoughts and a `search_agent_thoughts` action.
    *   `ProjectManagementTool`: Add, list, modify, and complete project tasks (uses `tasklib` backend per session). **Assigns tasks via tags (`+agent_id`)** due to CLI UDA issues. Used primarily by the Project Manager agent.
    *   **On-Demand Tool Help:** Implemented `get_detailed_usage()` in tools and `get_tool_info` action in `SystemHelpTool` for dynamic help retrieval (full transition planned for Phase 27+).
*   **Sequential Tool Execution:** The framework can now process multiple tool calls from a single agent response, executing them sequentially and returning all results.
*   **Session Persistence:** Save and load agent states, histories, team structures, and **project task data** (filesystem, including `tasklib` data with assignee tags).
*   **Database Backend (SQLite):**
    *   Logs user, agent, tool, and system interactions.
    *   Stores long-term knowledge summaries and agent thoughts via `KnowledgeBaseTool`.
*   **Governance Layer (Foundation):** System principles from `governance.yaml` are injected into agent prompts.
*   **Refined Web UI (Phase 22):**
    *   Separated view for User <-> Admin AI chat (`Chat` view).
    *   Dedicated view for internal Admin AI <-> Agent communication, tool usage, and system status updates (`Internal Comms` view).
    *   Improved message chunk grouping for concurrent streams.
    *   Increased message history limit in Internal Comms view.
    *   Session management interface.
    *   Static configuration viewer.
*   **Sandboxing:** Agents operate within dedicated sandbox directories or a shared session workspace.
*   **Context Optimization:** Agents guided to use files for large outputs. Admin AI prompts are now state-specific.
*   **Admin AI Time Context:** Current UTC time is injected into Admin AI prompts.
*   **Local Provider Integration:** Automatic network discovery (`LOCAL_API_DISCOVERY_SUBNETS="auto"`).

## Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
*   **Task Management:** `tasklib` (Python Taskwarrior library) %% Added P24
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
    *   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`), `governance.yaml` (`PyYAML`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
    *   **Model Discovery & Management:** Custom `ModelRegistry` class (now includes model parameter size discovery).
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
    *   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge, thoughts), Taskwarrior files (project tasks via `tasklib`)
*   **Data Handling/Validation:** Pydantic (via FastAPI)
    *   **Local Auto Discovery:** Nmap
*   **Logging:** Standard library `logging`

## Setup and Running (Detailed)

1.  **Prerequisites:**
    *   Termux app if used on Android mobile devices.
    *   Python 3.9+
    *   Node.js and npm (only if using the optional Ollama proxy)
    *   Access to LLM APIs (OpenAI, OpenRouter) and/or a running local Ollama instance.
    *   Nmap to enable automatic local API provider discovery.
 
2.  **Clone the repository:**
    ```bash
    git clone https://github.com/gaborkukucska/TrippleEffect.git
    cd TrippleEffect
    ```

3.  **Set up Python Environment:**

    On Linux/MacOS
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```
    
    on Windows
    ```bash
    .venv\Scripts\activate
    pip install -r requirements.txt
    ```

4.  **Configure Environment:**
    *   Copy `.env.example` to `.env`.
    *   Edit `.env` and add your API keys (OpenAI, OpenRouter, GitHub PAT, Tavily API Key).
    *   Set `MODEL_TIER` (`LOCAL`, `FREE` or `ALL`).
    *   **Note:** While local LLMs (via Ollama & LiteLLM) are supported, smaller models may currently exhibit reliability issues with long and complex instructions or tool use especially if hosted weak hardware. For more consistent results, using a robust external provider like OpenRouter (configured in `.env`) might yield better results at this stage.

5.  **Configure Bootstrap Agents (Optional):**
    *   Edit `config.yaml` to define the behavior and conversation style of the Admin AI.
    *   Add any bootstrap agents beyond the default Admin AI & PM Agent.
    *   You can optionally specify a provider/model for Admin AI & PM Agent here, otherwise it will be auto-selected.

6.  **Run the Application:**
    # Option 1: Use the run script (could be error some at times).
    ```bash
    chmod +x run.sh
    ./run.sh
    ```
    # Option 2: (Recommended) Run directly using Python
    ```bash
    python -m src.main
    ```
    *(Alternatively, for development with auto-reload, use `uvicorn src.main:app --reload --port 8000`, but be aware reload might interfere with agent state.)*

7.  **Access the UI:** Open your web browser to `http://localhost:8000`.

## Development Status

*   **Current Version:** 2.25
*   **Completed Phases:** 1-24. 
*   **Recent Fixes/Enhancements (v2.25):** 
    *   Refined model selection logic (Tier, Size, Performance).
    *   Enabled sequential execution of multiple tools per agent turn.
    *   Improved thought saving (smarter keywords) and retrieval (`search_agent_thoughts` action).
    *   Added foundational Governance Layer (principles injected from `governance.yaml`).
    *   Expanded unit test coverage for new features.
    *   Corrected `project_management` tool's Taskwarrior CLI usage.
    *   Fixed `AgentManager` initial task creation check logic.
    *   Ollama integration fixes, enhanced network discovery.
*   **Current Phase (25 Target Completion):** Address remaining agent logic issues (looping, placeholders, targeting), investigate Taskwarrior CLI UDA issues, address external API rate limits.
*   **Future Plans:** Advanced Memory & Learning (Phase 26), Proactive Behavior (Phase 27), Federated Communication (Phase 28+), New Admin tools, LiteLLM provider, advanced collaboration, resource limits, DB/Vector Stores, **Full transition to on-demand tool help** (removing static descriptions from prompts - Phase 28+).

See `helperfiles/PROJECT_PLAN.md` for detailed phase information.

## Contributing

Contributions are welcome! Please follow standard fork-and-pull-request procedures. Adhere to the development rules outlined in `helperfiles/DEVELOPMENT_RULES.md`.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

## Acknowledgements

*   Inspired by AutoGen, CrewAI, and other multi-agent frameworks.
*   Uses the powerful libraries FastAPI, Pydantic, SQLAlchemy, and the OpenAI Python client.
*   Built with various LLMs like Google Gemini 2.5 Pro, Meta Llama 4, DeepSeek R1 and others.
*   Special THANKS to Openrouter, Huggigface, and Google AI Studio
<!-- # IT IS CRITICAL THAT ALL AIs and LLMs FOLLOW THE DEVELOPMENT INSTRUCTIONS IN THE DEVELOPMENT_RULES.md FILE WHEN FURTER DEVELOPING THIS FRAMEWORK!!! -->