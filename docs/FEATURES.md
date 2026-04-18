# Features

*   **Asynchronous Backend:** Built with FastAPI and `asyncio`.
*   **WebSocket Communication:** Real-time updates via WebSockets.
*   **Dynamic Agent/Team Creation:** Manage agents and teams on the fly using `ManageTeamTool`.
*   **Advanced Agent Health System:** Comprehensive monitoring and recovery capabilities:
    *   **Loop Detection**: Automatically detects and resolves infinite loops, empty responses, and stuck patterns
    *   **Cross-Cycle Duplicate Prevention**: Detects and intercepts repeated identical tool calls across agent cycles, serving cached results and auto-advancing stalled workflows
    *   **XML Recovery**: Intelligent parsing and recovery of malformed XML tool calls
    *   **Context Optimization**: Automatic context summarization for improved performance
    *   **Workflow Continuation**: Smart reactivation logic for multi-step agent workflows
    *   **Health Analytics**: Detailed agent behavior analysis and intervention strategies
    *   **Team Work In Progress (WIP) Injection**: Dynamic injection of real-time team status updates into active agent prompts to ensure cross-agent situational awareness and minimize duplicated efforts.
*   **Configurable Model Selection:**
    *   Dynamic discovery of providers/models (Ollama, OpenRouter, OpenAI).
    *   Filtering based on `MODEL_TIER` (.env: `FREE` or `ALL`).
    *   Automatic model selection for Admin AI and dynamic agents, now prioritizing: Tier -> Model Size (parameter count, larger preferred) -> Performance Score -> ID. `num_parameters` are discovered for some providers (e.g., OpenRouter, Ollama).
*   **Robust Error Handling:**
    *   Automatic retries for transient LLM API errors.
    *   Multi-key support and key cycling for providers (`PROVIDER_API_KEY_N` in `.env`).
    *   Automatic failover to different models/providers based on tiers (Local -> Free -> Paid).
    *   Key quarantining on persistent auth/rate limit errors.
    *   Advanced XML validation and recovery mechanisms.
*   **Performance Tracking:** Monitors success rate and latency per model, saved to `data/model_performance_metrics.json`.
*   **State-Driven Admin AI Workflow:** Admin AI operates based on its current state (`conversation`, `planning`).
    *   **Conversation State:** Focuses on user interaction, KB search/save, monitoring PM updates, and identifying new tasks. Uses `<request_state state='planning'>` to signal task identification.
    *   **Planning State:** Focuses solely on creating a plan with a `<title>` tag. Framework handles project/PM creation upon plan submission.
*   **XML Tooling:** Agents request tool use via XML format. Available tools:
    *   `FileSystemTool`: Read, Write, List, Mkdir, Delete, Find/Replace, Fuzzy Search/Replace (`search_replace_block`), and Git operations (`git_commit`, `git_status`, `git_diff`) in sandbox or shared workspaces.
    *   `GitHubTool`: List Repos, List Files (Recursive), Read File content using PAT.
    *   `ManageTeamTool`: Create/Delete Agents/Teams, Assign Agents, List Agents/Teams, Get Agent Details.
    *   `SendMessageTool`: Communicate between agents within a team or with Admin AI (using exact agent IDs).
    *   `WebSearchTool`: Search the web (uses Tavily API if configured, falls back to DDG scraping).
    *   `SystemHelpTool`: Get current time (UTC), Search application logs.
    *   `KnowledgeBaseTool`: Save/Search distilled knowledge in the database. Now includes smarter keyword generation for saved thoughts and a `search_agent_thoughts` action.
    *   `ProjectManagementTool`: Add, list, modify, and complete project tasks (uses `tasklib` backend per session). **Assigns tasks via tags (`+agent_id`)** due to CLI UDA issues. Used primarily by the Project Manager agent.
    *   **Modular On-Demand Tool Help:** Implemented segmented `get_detailed_usage(sub_action=...)` in all major tools (`FileSystemTool`, `ProjectManagementTool`, `ManageTeamTool`). Agents can request action-specific help (e.g., just `read` or `add_task` docs) instead of the entire tool documentation. Error messages automatically include the relevant action's help context.
*   **Sequential Tool Execution:** Supports sequential execution of multiple tool calls from a single agent response.
*   **Constitutional Guardian (CG) System:**
    *   Dedicated CG agent (`constitutional_guardian_ai`) reviews agent outputs against `governance.yaml` principles.
    *   Uses a specific `cg_system_prompt`.
    *   `AgentCycleHandler` intercepts final responses for CG review via a direct LLM call.
    *   Original agents are paused (status `AGENT_STATUS_AWAITING_USER_REVIEW_CG`) if a concern is raised by CG.
    *   Backend methods in `AgentManager` (`resolve_cg_concern_approve`, `stop`, `retry`) are implemented to handle user decisions on concerns.
    *   Enhanced with comprehensive agent health monitoring and recovery capabilities.
*   **Session Persistence:** Save and load agent states, histories, team structures, and **project task data** (filesystem, including `tasklib` data with assignee tags).
*   **Database Backend (SQLite):**
    *   Logs user, agent, tool, and system interactions.
    *   Stores long-term knowledge summaries and agent thoughts via `KnowledgeBaseTool`.
*   **Governance Review via CG Agent:** System principles from `governance.yaml` are reviewed by the dedicated Constitutional Guardian (CG) agent, replacing the previous global prompt injection method.
*   **Refined Web UI:**
    *   Separated view for User <-> Admin AI chat (`Chat` view).
    *   Dedicated view for internal Admin AI <-> Agent communication, tool usage, and system status updates (`Internal Comms` view).
    *   Improved message chunk grouping for concurrent streams.
    *   Increased message history limit in Internal Comms view.
    *   Session management interface.
    *   Static configuration viewer.
    *   **Sandboxing:** Agents operate within dedicated sandbox directories or a shared session workspace.
*   **Context Optimization:** Agents guided to use files for large outputs. Admin AI prompts are now state-specific. Advanced context summarization for improved performance.
*   **Admin AI Time Context:** Current UTC time is injected into Admin AI prompts.
*   **Local Provider Integration:** Automatic network discovery (`LOCAL_API_DISCOVERY_SUBNETS="auto"`).
