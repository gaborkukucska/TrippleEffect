# Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
*   **Task Management:** `tasklib` (Python Taskwarrior library)
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`), `governance.yaml` (`PyYAML`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
*   **Model Discovery & Management:** Custom `ModelRegistry` class (includes model parameter size discovery).
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge, thoughts), Taskwarrior files (project tasks via `tasklib`)
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Local Auto Discovery:** Nmap
*   **Logging:** Standard library `logging`
