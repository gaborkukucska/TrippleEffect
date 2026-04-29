# TrippleEffect Framework: A Technical Explanation

## 1. Introduction

The TrippleEffect framework is an asynchronous, multi-agent system designed for collaborative task execution. Built on Python's `asyncio`, it facilitates complex workflows through a collection of specialized, state-driven AI agents. The architecture is centered around a main orchestrator, the `AgentManager`, which manages the lifecycle, communication, and execution of all agents within a session.

Agents operate in a semi-autonomous manner, driven by a state machine and interacting with their environment and each other through a structured tool-based system. Communication is primarily handled via an event-driven mechanism, where agents yield events (such as tool requests or state change proposals) that are processed by the framework's core handlers. This design allows for a clear separation of concerns between the agent's "thinking" process and the execution of actions, enabling robust error handling, workflow management, and observability.

The framework's core philosophy is to provide a structured yet flexible environment for agent collaboration, where high-level objectives are broken down and delegated through a hierarchy of specialized agents, from project management to individual task execution.

## 2. Core Concepts

### 2.1. The `AgentManager`: Central Orchestrator

The `AgentManager` is the singleton heart of the framework. It is instantiated once at application startup and holds the master state of the entire system. Its primary responsibilities include:

- **Agent Lifecycle Management**: Instantiating, deleting, and tracking all `Agent` objects. It holds the definitive dictionary of all active agents.
- **Session and State Management**: Managing the current project and session context, including database interactions for persistence and logging. It orchestrates the saving and loading of sessions.
- **Input Handling**: Acting as the primary entry point for user input, receiving messages and delegating them to the appropriate agent (typically the Admin AI).
- **Scheduling**: Initiating agent execution cycles by placing `AgentCycleHandler.run_cycle` tasks onto the `asyncio` event loop.
- **Component Hub**: Serving as a central service locator, providing agents with access to other core components like the `ToolExecutor`, `WorkflowManager`, and various handlers.

### 2.2. The `Agent`: A State-Driven Entity

The `Agent` class (`src/agents/core.py`) represents a single, independent actor within the framework. It is not just a conversational entity; it is a state-driven worker with a defined lifecycle and capabilities.

- **State Machine**: Each agent's behavior is dictated by its `agent_type` (e.g., `Admin`, `PM`, `Worker`) and its current `state` (e.g., `planning`, `work`, `manage`). State transitions are fundamental to the framework's workflow and are either requested by the agent itself or triggered by the `WorkflowManager`.
- **Asynchronous Processing**: The core of an agent's operation is the `process_message` async generator method. This method does not return a single, final response. Instead, it `yield`s a series of events representing its thought process and desired actions.
- **Event-Driven Communication**: Agents communicate their intentions by yielding structured dictionary events. Key event types include:
    - `tool_requests`: Indicates the agent wishes to execute one or more tools.
    - `agent_state_change_requested`: A proposal to transition to a new state.
    - `final_response`: A terminal text-based response intended for a user or another agent.
    - `agent_thought`: A meta-cognitive thought process, logged for observability but not part of the primary conversational history.
- **Encapsulation**: Each agent encapsulates its own configuration, message history, and a reference to its LLM provider, allowing for heterogeneous agents (using different models or providers) to coexist within the same session.

### 2.3. Asynchronous, Event-Driven Execution Flow

The framework's execution model is non-blocking and event-driven, orchestrated by the `AgentCycleHandler`. This handler mediates the interaction between an `Agent` and the framework.

1.  **Cycle Initiation**: The `AgentManager` schedules an agent's execution cycle by calling `AgentCycleHandler.run_cycle(agent)`.
2.  **Prompt Assembly**: The `AgentCycleHandler` prepares the necessary data for the LLM call, primarily by assembling the agent's current message history and injecting relevant system prompts or context.
3.  **Event Generation**: The handler calls the agent's `process_message` method, which begins yielding events.
4.  **Event Processing**: The `AgentCycleHandler` consumes events from the `process_message` generator and acts upon them.
    - If a `tool_requests` event is yielded, the handler passes the request to the `InteractionHandler` and `ToolExecutor` to perform the action. The results are then formatted and appended to the agent's message history.
    - If a `agent_state_change_requested` event is yielded, the `WorkflowManager` is invoked to validate and apply the state transition.
    - Other events (`final_response`, `agent_thought`) are logged and broadcast to the UI.
5.  **Cycle Continuation/Completion**: Based on the events processed, the `NextStepScheduler` component determines the agent's subsequent status. The agent might be set to `IDLE` if its turn is complete, or it might be immediately rescheduled for another cycle if the workflow requires it (e.g., after a tool call).

This decoupled, event-yielding architecture is a cornerstone of the TrippleEffect framework, enabling complex, multi-step agent actions and ensuring the system remains responsive.

## 3. The Agent Execution Loop: A Step-by-Step Guide

The agent execution loop is the core process that drives all agent activity. It is managed by the `AgentCycleHandler` and follows a precise, asynchronous sequence. The following steps detail a single, complete execution cycle, from scheduling to completion.

**Pre-condition**: An agent is in the `IDLE` state and an external event (e.g., a user message, a periodic timer, or another agent's action) has triggered the need for it to act.

---

**Step 1: Cycle Scheduling (`AgentManager`)**

- The `AgentManager.schedule_cycle(agent)` method is called.
- This creates an `asyncio.Task` for the `AgentCycleHandler.run_cycle(agent)` coroutine, placing it on the event loop for execution. The agent's status is immediately set to `PROCESSING`.

**Step 2: Context Preparation (`AgentCycleHandler` -> `PromptAssembler`)**

- Inside `run_cycle`, the `PromptAssembler.prepare_llm_call_data` method is invoked.
- It assembles the final list of messages to be sent to the LLM. This involves:
    1.  Retrieving the agent's persistent `message_history`.
    2.  Injecting the appropriate system prompt based on the agent's `type` and current `state` (retrieved from `WorkflowManager`).
    3.  Adding any dynamic context, such as the current time for the Admin AI or a specific task description for a Worker agent.

**Step 3: Event Generation (`Agent` -> `process_message`)**

- The `AgentCycleHandler` calls `agent.process_message()`, which returns an asynchronous generator.
- The `agent.llm_provider.stream_completion()` method is called, which begins streaming the LLM's response.
- As the LLM generates text, the `process_message` method parses it in real-time and `yield`s events. This is the critical step where the agent's raw output is translated into structured, actionable events for the framework.

**Step 4: Event Consumption and Handling (`AgentCycleHandler`)**

- The `AgentCycleHandler` iterates through the events yielded by `agent.process_message`. The handling logic branches based on the `type` of each event:

    - **`type: agent_thought`**: The thought content is logged to the database and broadcast to the UI for observability. Processing continues.

    - **`type: tool_requests`**:
        - The agent's turn is considered to be action-oriented.
        - The `AgentCycleHandler` loops through each tool call in the `calls` list.
        - For each call, it invokes `InteractionHandler.execute_single_tool`, which in turn finds the correct tool in the `ToolExecutor` and executes it.
        - The result from the tool (a dictionary) is formatted into a `role: "tool"` message and appended to the agent's `message_history`.
        - The `NextStepScheduler` is later informed that a tool was executed successfully, which typically results in the agent being immediately re-scheduled for another cycle to process the tool results.

    - **`type: agent_state_change_requested`**:
        - The handler calls `WorkflowManager.change_state(agent, requested_state)`.
        - The `WorkflowManager` validates if the transition is legal for the agent's type and current state.
        - If valid, the agent's `state` attribute is updated, and its status is reset to `IDLE`.
        - The agent is typically re-scheduled to begin work in its new state.

    - **`type: final_response`**:
        - This indicates a terminal text response.
        - The response is first passed to the `ConstitutionalGuardian` for a safety and compliance review.
        - If the review passes, the content is logged, broadcast to the UI, and appended to the agent's history. The agent's status is set to `IDLE`. The cycle is complete.
        - If the review fails, the agent's status is set to `AWAITING_USER_REVIEW_CG`, and the user is prompted for a decision.

    - **`type: error` or `type: malformed_tool_call`**:
        - The cycle is aborted.
        - An error message is constructed and added to the agent's history as a `role: "system"` message to provide context for a retry.
        - The `AgentHealthMonitor` logs the failure. If failure patterns are detected (e.g., repeated empty responses, looping), it may trigger an intervention.
        - The `FailoverHandler` may be invoked to attempt a retry with a different model or provider.

**Step 5: Cycle Outcome and Next Step Scheduling (`AgentCycleHandler` -> `OutcomeDeterminer` & `NextStepScheduler`)**

- After the event loop finishes (either by completion or by being broken by an event), the `OutcomeDeterminer` analyzes the context of the completed cycle (e.g., were there errors? was an action taken?).
- Based on this outcome, the `NextStepScheduler.schedule_next_step` method makes the final decision on the agent's fate:
    - **Re-schedule**: If the agent needs to continue its work (e.g., after a tool call), `AgentManager.schedule_cycle` is called again.
    - **Do Nothing**: If the agent's turn is complete and it should wait for a new trigger, its status is left as `IDLE`.

This structured, multi-component loop ensures that the agent's execution is predictable, observable, and resilient to errors, forming the backbone of the TrippleEffect framework's operational stability.

## 4. Key Components and Handlers

The framework's functionality is modularized into several key components and handlers. Understanding their individual roles is crucial to understanding the system as a whole.

### 4.1. `AgentManager` (`src/agents/manager.py`)

As the central orchestrator, the `AgentManager` is more than just a container for agents. It is an active component that manages the application's core logic.

- **Implementation Details**: It is instantiated as a singleton within the FastAPI application's `lifespan` context. It holds references to all other major handlers (`SessionManager`, `AgentCycleHandler`, `ToolExecutor`, etc.), making it the primary service locator.
- **Key Methods**:
    - `schedule_cycle()`: The entry point for starting an agent's execution.
    - `handle_user_message()`: The ingress point for all user interactions, responsible for queuing messages for the Admin AI.
    - `create_agent_instance()` / `delete_agent_instance()`: Methods that abstract the agent creation/deletion logic (defined in `agent_lifecycle.py`), including database record creation.
    - `save_session()` / `load_session()`: Coordinates with the `SessionManager` to persist and retrieve the entire framework state.
    - `_universal_framework_watchdog()`: An internal `asyncio` task (formerly `_periodic_pm_manage_check`) that periodically monitors all agents across the system, acting as a central pacemaker for autonomous oversight and triggering proactive status checks without user intervention.

### 4.2. `Agent` (`src/agents/core.py`)

The `Agent` class is the blueprint for all intelligent actors in the system.

- **Implementation Details**: It is initialized with a configuration dictionary, a reference to an LLM provider instance, and a reference back to the `AgentManager`. Its `message_history` is a simple list of dictionaries, adhering to the standard required by most LLM APIs.
- **Key Attributes**:
    - `agent_id`, `persona`, `agent_type`, `state`: The core identifiers and state machine attributes.
    - `llm_provider`: An instance of a class that inherits from `BaseLLMProvider`, ensuring a consistent interface for making LLM calls.
    - `raw_xml_tool_call_pattern`: A dynamically generated regex pattern, created at initialization based on the available tools in the `ToolExecutor`. This allows the agent to efficiently parse its own output for tool calls.

### 4.3. `AgentCycleHandler` (`src/agents/cycle_handler.py`)

This is the engine that drives the agent execution loop. It is stateless itself, operating purely on the `agent` and `context` it is given for each `run_cycle` call.

- **Implementation Details**: It is composed of several sub-components that handle specific parts of the cycle (`PromptAssembler`, `OutcomeDeterminer`, `NextStepScheduler`, etc.). This compositional design makes the cycle logic easier to manage and extend.
- **Cycle Components**:
    - **`PromptAssembler`**: Responsible for constructing the exact list of messages for the LLM, injecting system prompts and context. It actively queries the file system to inject a detailed `[SHARED WORKSPACE TREE]` structure directly into PM and Worker agents' history, guaranteeing deep ambient awareness of project deliverables without requiring costly CLI-probe turns.
    - **`XMLValidator`**: A utility used to validate and attempt recovery of malformed XML tool calls from an agent's response.
    - **`ContextSummarizer`**: An intelligent component that monitors the token count of an agent's history and, if it exceeds a threshold, uses an LLM to summarize the context, preventing token overflow errors with smaller models.
    - **`AgentHealthMonitor`**: Tracks agent behavior over multiple cycles to detect problematic patterns like loops, empty responses, or repetitive actions, and can trigger recovery plans.
    - **Cross-Cycle Duplicate Detection**: A specialized mechanism that detects when PM agents repeat identical tool calls across consecutive cycles. When detected, tool execution is skipped (cached results are served), escalated directives are injected, and after 3+ consecutive duplicates the framework auto-advances the workflow by executing the next expected tool call on the agent's behalf.
    - **`OutcomeDeterminer` & `NextStepScheduler`**: These work together at the end of the cycle to analyze what happened and decide whether the agent should be set to `IDLE` or be immediately re-scheduled. The `NextStepScheduler` strictly enforces **State Resilience**, specifically ensuring that Project Managers (e.g., in `pm_report_check`) persist in their current specific interaction state after successfully executing tools, rather than being aggressively reset to baseline states, preventing premature workflow cutoffs. The scheduler maintains a `persistent_states` set that includes `pm_manage`, `pm_audit`, `worker_work`, `worker_test`, `worker_report`, and `admin_work` — agents in these states are always re-scheduled after completing a cycle unless they explicitly request a state change or are caught in a loop.

### 4.4. `InteractionHandler` & `ToolExecutor` (`src/agents/interaction_handler.py`, `src/tools/executor.py`)

This pair of components manages all agent interactions with the "outside world" via tools.

- **`VllmProvider`**: Connects to native, high-performance local vLLM instances using standard OpenAI API structures for flawless JSON tool calling and prompt caching without XML hacking.
- **`OllamaProvider` & XML Hallucination Guard**: Handles translations between the framework and local Ollama models. To prevent deeply autoregressive models (like Qwen) from getting stuck into XML-generation loops, this provider intentionally avoids wrapping execution results in native XML `<tool_response>` tags within the agent message history, converting them instead to plain ASCII markdown `--- Tool Response ---` wrappers to stabilize local model text generation.
- **`ToolExecutor`**: A simple but crucial component. It auto-discovers all available tool classes, instantiates them, and stores them in a dictionary keyed by the tool's name. It provides a single `execute_tool` method that takes the tool name and arguments. When a tool execution fails, the `ToolExecutor` automatically fetches action-specific documentation from the tool's `get_detailed_usage(sub_action=...)` method and includes it in the error message sent back to the agent via the `ToolErrorHandler`, enabling context-aware self-correction.
- **`InteractionHandler`**: Acts as a mediator between the `AgentCycleHandler` and the `ToolExecutor`. Its `execute_single_tool` method contains the boilerplate logic for calling the executor, handling exceptions, logging the result to the database, and formatting the output into the `role: "tool"` message structure that the agent expects in its history. Crucially, it manages **Synchronous Message Queuing**: All cross-agent communication is completely asynchronous and non-interruptive. When an agent receives a message, it is silently placed in their `message_inbox`. The inbox is exclusively flushed into their active history when the agent transitions into a safe idle state (`worker_wait`, `pm_standby`) OR immediately after the agent themselves successfully dispatches an outbound message, guaranteeing unbroken thought loops. **Duplicate Agent Prevention**: The `_handle_create_agent` method performs a strict `role`-based duplicate check before allowing agent creation. If a `team_id` is available, only the team is checked; if it is unavailable (a common LLM omission), the check falls back to scanning *all* agents in the manager registry, ensuring no duplicate roles slip through regardless of parameter completeness.
- **Modular Tool Help System**: Major tools (`FileSystemTool`, `ProjectManagementTool`, `ManageTeamTool`, `WebSearchTool`) implement segmented `get_detailed_usage(sub_action=...)` methods. When called without a `sub_action`, they return a concise summary of all available actions. When called with a specific `sub_action` (e.g., `"read"`, `"add_task"`, `"create_agent"`), they return only the relevant action's detailed parameter documentation.
- **ProjectManagement Tool Safety Filters**: The PM's core tool monitors parameter requests to aggressively reduce token bloat. If a generic `list_tasks` command is issued without any constraints, the framework injects an automated `assignee_filter="unassigned"`. This protects local models by preventing full-dump outputs of hundreds of already delegated tasks.
- **Strict Path Sandboxing**: The `FileSystemTool` inherently enforces a hard partition at the `shared_workspace` directory. Regardless of what path the agent provides (even if they erroneously prepend session strings or project IDs), the `_resolve_and_validate_path` engine aggressively sanitizes and strips anomalous prefixes to ensure perfectly flat, consistent folder topologies across the entire session team.
- **Web Search Integration**: The `WebSearchTool` integrates deeply with **SearXNG** (configured via `.env`) as the primary open-source intelligence gathering mechanism, falling back to a custom DuckDuckGo scraper if the self-hosted engine is unavailable.

### 4.5. `WorkflowManager` (`src/agents/workflow_manager.py`)

The `WorkflowManager` handles high-level, framework-defined processes that are triggered by an agent's output, rather than direct tool calls.

- **Implementation Details**: It uses a dictionary of trigger patterns (e.g., a specific XML tag like `<plan>`) mapped to `Workflow` objects. The `process_agent_output_for_workflow` method checks an agent's response against these triggers.
- **Example Workflow (`project_creation`)**: When the Admin AI outputs a plan enclosed in `<plan>` tags, the `WorkflowManager` intercepts this. The `ProjectCreationWorkflow` is executed, which performs the framework-level actions of creating a project task, creating a new Project Manager agent, and assigning the task to it. When the project is officially approved to start by the user via the `http_routes` endpoint, the framework automatically serializes the initial `<plan>` description and writes it permanently to the file system as `PROJECT_PLAN.md` acting as the architectural bedrock constraint for the PM. This entire process is transparent to the Admin AI, which simply sees its plan being accepted.
- **State Management**: The `WorkflowManager` is also the authority on state transitions. Its `change_state` method contains the logic defining all valid state transitions for each agent type, ensuring the integrity of the state machine.
- **Smart Decomposition Detection** (`worker_decompose` → `worker_work`): When a worker requests the `worker_work` transition, the `WorkflowManager` inspects the task database. It filters `depends`-linked tasks by **assignee** (`st.get('assignee') == agent.agent_id`) to determine if the worker actually created sub-tasks. If worker-owned sub-tasks are found, the parent task is marked `decomposed` and reassigned to the PM. If no worker-owned sub-tasks exist (the worker skipped decomposition for a simple task), the transition is approved **without** modifying the parent task — the worker retains it and works directly. This prevents the false-positive task-loss bug where unrelated kick-off tasks that happened to depend on the parent were mistaken for worker-created sub-tasks.

### 4.6. `ConstitutionalGuardian` & Safety Layers

The framework includes a dedicated safety and reliability layer, embodied by the `ConstitutionalGuardian` (CG) agent and the `AgentHealthMonitor`.

- **`ConstitutionalGuardian`**: A specialized, non-interactive agent (`constitutional_guardian_ai`). Before any `final_response` from another agent is committed, the text is sent to the CG. The CG uses a dedicated system prompt and the principles defined in `governance.yaml` to check for violations. It returns either `<OK/>` or `<CONCERN>...</CONCERN>`. If a concern is raised, the original agent is paused, and user intervention is required. The CG's verdict generation uses a configurable token limit (`CG_MAX_TOKENS`, default `4000`) to ensure models with internal reasoning processes (e.g., `<think>` blocks) have sufficient output space to complete their verdicts.
- **`AgentHealthMonitor`**: As described in the `AgentCycleHandler` section, this component acts as an automated check against common agent failure modes. It is a key part of the framework's resilience, capable of intervening with corrective feedback or forcing an agent into an error state to prevent infinite loops. The monitor tracks `cycle_count_in_current_state` for each agent. **Proactive Escalation:** If an agent stalls relentlessly (e.g., empty response loops), the CG steps in, intercepts the loop, and dynamically constructs a diagnostic report containing recent errors and tool calls. It then dispatches this report directly into the `message_inbox` of the stalled agent's supervisor (Admin AI or Project Manager) via the `InteractionHandler`, instructing them to deploy targeted Human-in-the-Loop style recovery via their own `send_message` tools.

### 4.7. Failover Handler & Tool Support Blacklist

The `failover_handler.py` module manages automatic model/provider failover when agents encounter persistent errors. It follows a preference order: (1) same model on alternate local APIs, (2) alternative models on local providers, (3) external providers with API keys.

- **Tool Support Blacklist**: A module-level `_models_without_tool_support` set tracks `(provider, model)` pairs that have returned "does not support tools" errors at runtime. Before attempting any failover switch, both Pass 1 (preferred model on alternate APIs) and Pass 2 (alternative model candidates) check this blacklist, preventing cascade failures where agents cycle through multiple models that all lack native tool calling capability.
- **Health Checks**: Before switching to a local provider, the handler performs HTTP health checks on the target API endpoint.

### 4.8. Cross-Cycle Duplicate Detection & Result Truncation

The `AgentCycleHandler` maintains a per-agent cache of recent tool calls and their results. When an agent calls the exact same tool with identical arguments across consecutive cycles, the framework:

1. Returns a **truncated** cached result (max 200 characters) to save context tokens, since the agent already received the full result previously.
2. Injects a `[Framework System Message - DUPLICATE BLOCKED]` directive instructing the agent to proceed to the next workflow step.
3. After 3+ consecutive duplicates, escalates with `[AUTO-ADVANCE]` — for PMs this forces a state transition; for workers it injects a strong corrective directive.
4. Calls `_deduplicate_pm_framework_messages()` before each injection to remove old framework messages, preventing context bloat.

### 4.9. Tool Action Alias Auto-Correction

Both `ProjectManagementTool` and `FileSystemTool` include `action_suggestions` dictionaries that automatically correct common LLM action hallucinations (e.g., `mark_completed` → `complete_task`, `create_directory` → `mkdir`). When an invalid action is detected and a mapping exists, the tool auto-corrects and continues execution, appending a correction note to the successful result so the agent learns the correct action name.

### 4.10. Persistent State Loop Overrides

Agents in persistent states (like `worker_work`, `worker_test`, `worker_report`, `pm_manage`) automatically reactivate to process results or wait for new input. However, smaller models occasionally enter autoregressive generation loops, repeating the identical tool call (e.g. `modify_task` or `send_message`) infinitely despite the `CycleHandler`'s duplicate block and `AUTO-ADVANCE` directives.
To prevent this death spiral, `NextStepScheduler.schedule_next_step` includes a hard framework intervention block. If an agent hits 4 consecutive duplicate cross-cycle failures while in a persistent state, the scheduler intercepts the cycle, skips the LLM generation entirely, and forces a hard state transition (e.g., `worker_work` -> `worker_report`, `worker_test` -> `worker_report`, `worker_report` -> `worker_wait`). This forces the agent into a completely different operational context, breaking the loop.

## 5. State and Communication Model

The framework's operations are governed by a strict state machine and a well-defined communication model, which together ensure predictable and organized agent behavior.

### 5.1. The Agent State Machine

An agent's behavior, system prompt, and expected output are determined by the combination of its `agent_type` and its current `state`. The `WorkflowManager` enforces valid transitions between these states.

-   **Admin Agent (`admin_ai`) States**:
    -   `conversation`: The default state for interacting with the user. The Admin AI acts as the "Ultimate Orchestrator" and will seamlessly transition to the `work` state to run autonomous backend audits if queried about project progress.
    -   `planning`: A focused state where the agent's sole objective is to produce a detailed, structured plan in response to a user request. The output is expected to be wrapped in `<plan>` tags.
    -   `work`: The Proactive Orchestration & Investigation hub. The Admin AI employs `manage_team` and `project_management` tools autonomously to gather metrics across the framework and verify progress without interrupting active agents.
    -   `delegated`: A dormant oversight state where the Admin AI waits for the PM to declare a project finished. It performs periodic proactive sweeps (invoked by the framework watchdog) to ensure the PM hasn't stalled.

-   **Project Manager (PM) Agent States**:
    -   `startup`: The initial state for a new PM. Its goal is to take the initial plan from the Admin AI and break it down into a list of specific, actionable tasks for worker agents, outputting them in a `<task_list>` XML block.
    -   `build_team_tasks`: A guided state where the PM creates a team and then creates the necessary worker agents one by one. The framework injects system messages to guide the PM through this sequential process.
    -   `activate_workers`: A state where the PM assigns the previously defined tasks to the newly created worker agents.
    -   `manage`: The PM's main operational loop. It periodically activates to monitor task progress, review completed work, and assign new tasks.
    -   `report_check`: A focused, temporary state the PM automatically enters when receiving a message from a worker. After reading and replying, the PM auto-resolves back to its previous state (e.g., `manage` or `standby`).
    -   `audit`: A verification phase entered when the PM believes all assigned tasks are complete. The PM reviews documents, scans the codebase, and runs tests to ensure the overall project goals were met before formally declaring success.
    -   `standby`: A dormant state entered after a project is considered fully complete and the audit report has been filed to the Admin AI.

-   **Worker Agent States**:
    -   `decompose`: The initial state when a task is assigned. Workers evaluate task complexity here; for simple tasks they may skip decomposition and transition directly to `work`. If a worker does decompose, they must create sub-tasks via the `project_management` tool (`add_task` with a `depends` parameter pointing to their assigned task). Tool access is strictly limited in this state.
    -   `work`: The primary state where the agent executes a specific task or sub-task it has been assigned. **Note**: When transitioning into this state, the framework uses an active context condensation mechanism (clearing previous conversation history) to prevent the LLM from getting stuck in autoregressive state-request loops. The framework injects a `[Framework Note]` message informing the worker whether it is working on the original task directly (skipped decomposition) or on sub-tasks it created.
    -   `report`: A dedicated state for workers to compile progress reports or ask questions to the PM without mixing communication and tool workloads.
    -   `wait`: A state where the agent has completed its task and is waiting for the PM to review its work and provide a new task.

### 5.2. Communication Protocols

Communication within the framework is handled through several distinct channels.

-   **User <-> Framework**: All user messages are sent to the `AgentManager.handle_user_message` endpoint. These messages are placed in the Admin AI's message history, and a cycle is scheduled for the Admin AI to process them. This is the primary ingress point for new work.

-   **Agent -> Framework (Event Yielding)**: This is the main internal communication method. As detailed in the Execution Loop section, an agent's `process_message` generator yields events to the `AgentCycleHandler`. This is how an agent signals its intent to do anything other than send a simple text response.

-   **Framework -> Agent (System Messages)**: The framework, primarily through the `AgentCycleHandler` or `WorkflowManager`, can inject messages with `role: "system"` into an agent's history. This is a powerful mechanism used to:
    -   Provide feedback on errors (e.g., malformed tool calls).
    -   Give context for a new state (e.g., "You are now in the 'work' state. Your task is...").
    -   Intervene to break loops (e.g., "You appear to be stuck. Please try a different tool.").

-   **Agent <-> Tools (Native JSON / XML)**: The definitive method for an agent to request a tool execution is automatically handled depending on the `NATIVE_TOOL_CALLING_ENABLED` setting. 
    - **Native JSON (Default):** For modern models (OpenAI, Anthropic, Qwen3), `BaseTool` automatically converts its Python properties into rich JSON schemas. The application orchestrator parses the returned `tool_calls` array, bypassing text parsing altogether.
    - **XML Fallback:** If native capability is turned off, the agent outputs a structured XML block in its textural response. The framework uses regex (`raw_xml_tool_call_pattern`) to parse these blocks. The format is always:
    ```xml
    <tool_name>
        <action>action_to_perform</action>
        <param1>value1</param1>
        <param2>value2</param2>
    </tool_name>
    ```
    This hybrid system ensures backward compatibility while capitalizing on modern deterministic JSON tool execution.

-   **Agent <-> Agent (`SendMessageTool`) & Message Inboxing**: Agents can communicate directly with one another using the `SendMessageTool`. The tool takes a `target_agent_id` and `message_content` as parameters. 
    - **Safe State Delivery:** If the target agent is in a safe, interruptible state (like `wait`, `manage`, or `standby`), the framework appends the message directly to its history and schedules a cycle.
    - **Message Inboxing (Deferred Delivery):** If the target agent is in a high-focus or busy phase (like a PM in `startup` or a Worker in `report`), the `InteractionHandler` routes the message to the agent's `message_inbox`. The `WorkflowManager` later flushes this inbox securely when the agent naturally transitions to a safe state, preventing context pollution and infinite loops.

## 6. Conclusion and Intended Potential

The TrippleEffect framework is a robust system designed for building sophisticated multi-agent applications. Its core strengths lie in its asynchronous nature, its clear separation of concerns between agent logic and framework orchestration, and its resilient, state-driven execution model.

The intended potential of this architecture is to enable the creation of highly specialized agent teams that can tackle complex, long-running tasks with minimal human intervention. The hierarchical structure (Admin -> PM -> Workers) allows for a natural decomposition of problems. By leveraging the state machine, agents can be guided through complex workflows, ensuring that tasks like project planning, team building, task assignment, and execution occur in a reliable and predictable order.

An AI or developer using this documentation as a guide should be able to replicate the key architectural patterns: the central `AgentManager`, the event-yielding `Agent` core, the `AgentCycleHandler` as the execution engine, and the use of distinct handlers for tools, workflows, and state management. This modular design is the key to the framework's power and extensibility.