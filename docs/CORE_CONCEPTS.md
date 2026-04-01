# Core Concepts

*   **Stateful Admin AI:** The central agent (`admin_ai`) operates using a state machine (`conversation`, `planning`, etc.). In the `conversation` state, it interacts with the user and monitors ongoing projects via their PMs. When an actionable request is identified, it transitions to the `planning` state.
*   **Framework-Driven Project Initiation:** When Admin AI submits a plan (including a `<title>`) in the `planning` state, the framework automatically:
    *   Creates a project task in Taskwarrior using the title and plan.
    *   Creates a dedicated Project Manager agent (`pm_{project_title}_{session_id}`).
    *   Assigns the new PM to the initial project task.
    *   Notifies Admin AI and transitions it back to the `conversation` state.
*   **Project Manager Agent:** Automatically created per project/session by the framework, this agent uses the `ProjectManagementTool` (backed by `tasklib`) to decompose the initial plan, create a team, specialised worker agents, create/assign sub-tasks to worker agents, monitor progress via `send_message` tool, and report status/completion back to Admin AI.
*   **Dynamic Worker Agent Management:** The Project Manager agent (or Admin AI, depending on workflow evolution) uses `ManageTeamTool` to create worker agents as needed for specific sub-tasks.
*   **Constitutional Guardian (CG) Agent:** A specialized agent (`constitutional_guardian_ai`) reviews final textual outputs of other agents against predefined governance principles (from `governance.yaml`). If a concern is raised, the original agent's output is paused, and a UI notification is generated, allowing for user intervention (approve, stop agent, or provide feedback for retry). This feature's backend logic is implemented; UI/API for full user interaction is pending.
*   **Advanced Agent Health Monitoring:** Enhanced Constitutional Guardian system with comprehensive agent health monitoring capabilities:
    *   **Agent Health Monitor**: Tracks agent behavior patterns, detects infinite loops, empty responses, and problematic patterns
    *   **Cross-Cycle Duplicate Detection**: Detects when PM agents repeat identical tool calls across consecutive cycles. Serves cached results, injects escalated directives, and auto-advances the workflow after persistent duplicates.
    *   **XML Validator**: Automatic detection and recovery of malformed XML tool calls
    *   **Context Summarizer**: Manages conversation context for optimal performance with smaller models
    *   **Next Step Scheduler**: Intelligent agent reactivation and workflow continuation logic
    *   **Cycle Components Architecture**: Modular, extensible system for agent cycle management
*   **Intelligent Model Handling:**
    *   **Discovery:** Automatically finds reachable LLM providers (Ollama, OpenRouter, OpenAI) and available models at startup.
    *   **Filtering:** Filters discovered models based on the `MODEL_TIER` setting (`.env`).
    *   **Auto-Selection:** Automatically selects the best model for Admin AI (at startup) and dynamic agents (at creation if not specified). Selection priority is Tier -> Model Size (parameter count, larger preferred) -> Performance Score -> ID. `num_parameters` are discovered for providers like OpenRouter and Ollama where available.
    *   **Failover:** Automatic API key cycling and model/provider failover (Local -> Free -> Paid tiers) on persistent errors during generation. Model selection during failover also respects the new Size/Performance priority.
    *   **Performance Tracking:** Records success rate and latency per model, persisting data (`data/model_performance_metrics.json`).
*   **Tool-Based Interaction:** Agents use tools via an **XML format**. The framework can now process multiple distinct tool calls found in a single agent response; these are executed sequentially, and all results are then fed back to the agent in the next turn.
*   **Modular Tool Help System:** Tool documentation is segmented into action-specific help sections. Agents receive a concise summary by default and can request detailed help for specific actions via `sub_action`. When tool errors occur, the relevant action's documentation is automatically injected into the error message, enabling context-aware self-correction.
*   **Context Management:** Standardized instructions are injected, agents are guided to use file operations for large outputs. Admin AI receives current time context.
*   **Communication Layer Separation (UI):** The user interface visually separates direct User<->Admin AI interaction from internal Admin AI<->PM<->Worker communication and system events.
*   **Persistence:** Session state (agents, teams, histories) can be saved/loaded (filesystem). Interactions and knowledge are logged to a database (`data/trippleeffect_memory.db`).
*   **KnowledgeBaseTool Enhancements:** Agent thoughts are saved with automatically generated keywords. A new `search_agent_thoughts` action allows targeted retrieval of past agent reasoning.
*   **Governance Layer:** System principles defined in `governance.yaml` are now primarily used by the Constitutional Guardian (CG) agent for its review process. Global injection of these principles into all agent prompts has been removed.
