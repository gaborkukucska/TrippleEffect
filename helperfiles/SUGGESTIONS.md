# Framework Improvement Suggestions

Based on the code analysis performed on 2025-04-27, here are suggestions for improvement categorized by estimated complexity.

## First of all

Phase 1: Refactoring, Agent Typing, and State Change Mechanism

Define Agent Types & Constants:

Add agent type constants (AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER) to src/agents/constants.py.
Keep existing state constants (ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED). We won't add AGENT_STATE_WORK yet.
Implement Agent Typing:

Modify src/agents/core.py: Add an agent_type: str attribute to the Agent class.
Modify src/agents/agent_lifecycle.py (_create_agent_internal):
Determine agent_type based on agent_id (admin_ai -> ADMIN, pm_... -> PM, others -> WORKER).
Assign the determined agent_type to the new agent.agent_type attribute during instantiation.
Ensure agent_type is included in the final_agent_config_entry dictionary.
Modify src/agents/session_manager.py:
Ensure agent_type is saved within the dynamic_agents_config section during save_session.
Ensure agent_type is read and passed correctly when recreating agents in load_session (it should be part of the agent_cfg dictionary passed to _create_agent_internal).
Create AgentWorkflowManager:

Create src/agents/workflow_manager.py.
Define the AgentWorkflowManager class.
Initial Responsibilities:
Store valid states per agent type. Initially:
admin: startup, conversation, planning, work_delegated
pm: conversation
worker: conversation
Implement get_system_prompt(agent: Agent) -> str:
Takes an Agent instance.
Checks agent.agent_type and agent.state.
Determines the correct prompt key (e.g., admin_ai_conversation_prompt, pm_conversation_prompt, agent_conversation_prompt).
Loads template from settings.PROMPTS.
Formats template with context (agent_id, team_id, time, etc.).
Returns the final system prompt string.
Implement change_state(agent: Agent, requested_state: str) -> bool:
Checks if requested_state is valid for the agent.agent_type.
If valid, updates agent.state and returns True.
If invalid, logs a warning and returns False.
Integrate AgentWorkflowManager:

In AgentManager.__init__: Instantiate AgentWorkflowManager and store it (e.g., self.workflow_manager).
In src/agents/cycle_handler.py (run_cycle):
Replace the existing prompt injection logic with a call to self._manager.workflow_manager.get_system_prompt(agent).
In src/agents/cycle_handler.py (run_cycle - event loop):
When the agent_state_change_requested event is received, call self._manager.workflow_manager.change_state(agent, requested_state) instead of directly setting agent.state. Ensure the agent is reactivated afterwards.
In src/agents/agent_lifecycle.py (_create_agent_internal):
Set the initial state based on agent_type using the new workflow_manager.change_state method (or directly setting agent.state initially is also acceptable here). Admin starts in startup, others in conversation.
Implement State Change Trigger (Tag):

Modify src/agents/core.py (process_message):
Ensure the logic that detects <request_state state='...'/> works for all agent types, not just Admin AI.
When the tag is detected, yield the agent_state_change_requested event (as it does now for Admin AI).
Update Prompts (prompts.json):

Add new prompt templates:
pm_conversation_prompt: Instructions for PM when idle.
agent_conversation_prompt: Instructions for Worker when idle.
Review/update existing Admin AI prompts (admin_ai_startup_prompt, admin_ai_conversation_prompt, admin_ai_planning_prompt) to ensure they align with the new context injection via AgentWorkflowManager and instruct the Admin AI on how to request state changes (e.g., <request_state state='planning'/>).
Crucially: Update all relevant conversation prompts (admin_ai_conversation_prompt, pm_conversation_prompt, agent_conversation_prompt) to explicitly instruct the LLM: "If you need to use tools to fulfill a request or complete a task, first respond only with the tag <request_state state='work'/>." (We will add the work state and its prompt in Phase 2).
Phase 2: Implementing the work State (To be done after testing Phase 1)

Define AGENT_STATE_WORK constant.
Update AgentWorkflowManager to include work as a valid state for PM/Worker and define valid transitions (e.g., conversation -> work, work -> conversation).
Add agent_work_prompt to prompts.json with the 3-step tool usage instructions.
Implement the work -> conversation state transition logic in CycleHandler after successful tool execution.
This revised plan focuses on the structural changes first. We'll implement the AgentWorkflowManager, agent typing, the state change tag mechanism, and update the conversation prompts. We will not implement the work state itself or its specific prompt/workflow in this phase.

## Easy Fixes

*   **Version Synchronization:** Update the FastAPI app version in `src/main.py` (currently `2.21`) to match the version stated in `README.md` (currently `2.25`).
*   **Centralize Constants:** Move shared constants like `BOOTSTRAP_AGENT_ID`, `RETRYABLE_EXCEPTIONS`, retry configuration values, and regex patterns (`REQUEST_STATE_TAG_PATTERN`, etc.) from various modules (`agent_lifecycle.py`, `cycle_handler.py`, `openai_provider.py`, `openrouter_provider.py`, `failover_handler.py`) into `src/agents/constants.py` for better organization and easier updates.
*   **Consolidate `BASE_DIR` Definition:** Define `BASE_DIR` only once (e.g., in `src/config/settings.py`) and import it where needed (`src/main.py`, etc.) to avoid duplication.
*   **Consolidate Formatting Logic:** Refactor the duplicated model list formatting logic between `ModelRegistry._log_available_models` and `ModelRegistry.get_formatted_available_models` into a single helper method.
*   **Refactor Task Fetching:** Extract the duplicated logic for fetching tasks by UUID or ID from `ProjectManagementTool.modify_task` and `ProjectManagementTool.complete_task` into a private helper method within the tool.

## Medium Complexity

*   **Refactor `AgentCycleHandler.run_cycle`:** Break down the large `run_cycle` method in `src/agents/cycle_handler.py` into smaller, more focused helper methods (e.g., for prompt injection, event processing, tool execution handling, state change handling, final action logic) to improve readability, testability, and maintainability.
*   **Refactor Provider Retry Logic:** Abstract the duplicated retry/error handling logic from `src/llm_providers/openai_provider.py` and `src/llm_providers/openrouter_provider.py` into a shared utility function or potentially a base class method suitable for OpenAI-compatible APIs. Use retry constants from `settings.py`.
*   **Refactor PM Agent Creation:** Consolidate the duplicated Project Manager agent creation logic found in `AgentManager.save_session` and `AgentManager.create_project_and_pm_agent` into a single, reusable private method within `AgentManager`.
*   **Improve Prompt Team ID Update:** Replace the brittle regex-based team ID update in `src/agents/prompt_utils.py` with a more robust mechanism, perhaps using dedicated placeholders or structured prompt templates.
*   **Improve Knowledge Base Linking:** Modify the framework (likely `ToolExecutor` and `AgentManager`) to reliably pass the `session_db_id` and potentially the source `interaction_id` to `KnowledgeBaseTool.execute` so that saved knowledge can be correctly linked to its context.
*   **Improve Health Report Generation:** Make the `_generate_system_health_report` in `src/agents/cycle_handler.py` less reliant on string parsing of previous messages. Consider using structured event data if possible.
*   **Optimize Log Search:** Refactor `SystemHelpTool._search_logs_safe` to avoid reading the entire log file into memory, especially for large files. Use techniques like reading in chunks or using `collections.deque` with a fixed size.
*   **Improve Failover State Handling:** Consider passing failover state explicitly in `failover_handler.py` instead of attaching it directly to the agent object (`_failover_state`) for cleaner state management.
*   **Improve Failover Switch Error Handling:** Ensure `failover_handler._try_switch_agent` correctly reverts agent state if the provider instantiation fails mid-switch.

## Hard Complexity

*   **Resolve Tasklib UDA Issues:** Investigate and fix the underlying issues preventing reliable use of Tasklib User Defined Attributes (UDAs) for assigning agents to tasks. This would allow removing the complex workarounds in `ProjectManagementTool` (using `execute_command` for `add_task`, parsing tags for assignees in `list_tasks`) and enable using Tasklib's native methods (`tw.add_task`, direct attribute access) for cleaner, more robust project management.
*   **Implement True OpenAI Model Discovery:** Replace the hardcoded list in `ModelRegistry._discover_openai_models` with actual API calls to the OpenAI `/v1/models` endpoint to dynamically discover all models accessible by the configured API key.
*   **Implement LiteLLM Provider:** Complete the `LiteLLMProvider` implementation (marked as TODO in `agent_lifecycle.py`) and integrate it fully into the `PROVIDER_CLASS_MAP`, discovery, and failover logic.
*   **Improve Knowledge Base Search:** Replace the basic keyword `contains` search in `DatabaseManager.search_knowledge` with a more advanced mechanism like SQLite's FTS5 (Full-Text Search) or integrate a vector database for semantic search capabilities.
*   **Refine Failover Model Selection:** Implement the TODO in `failover_handler._select_alternate_models` to choose alternate models based on performance metrics or model characteristics instead of random selection.
