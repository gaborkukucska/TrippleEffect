# START OF FILE src/agents/manager.py
import asyncio
# ... (other imports remain the same) ...
from pathlib import Path

logger = logging.getLogger(__name__)

# ... (PROVIDER_CLASS_MAP, BOOTSTRAP_AGENT_ID, STREAM_RETRY_DELAYS, MAX_STREAM_RETRIES remain the same) ...

# --- Generic Standard Instructions for ALL Dynamic Agents ---
# These instructions are injected by the framework (_create_agent_internal)
# into the system prompt of every dynamically created agent.
STANDARD_FRAMEWORK_INSTRUCTIONS = """

--- Standard Tool & Communication Protocol ---
Your Agent ID: `{agent_id}`
Your Assigned Team ID: `{team_id}`

**Context Awareness:** Before using tools (like web_search or asking teammates), carefully review the information already provided in your system prompt, the current conversation history, and any content included in the message assigning your task. Use the available information first.

**Tool Usage:** You have access to the following tools. Use the specified XML format precisely. Only use ONE tool call per response message, placed at the very end.

{tool_descriptions_xml}

**Communication:**
- Use the `<send_message>` tool to communicate ONLY with other agents *within your team* or the Admin AI (`admin_ai`).
- **CRITICAL:** Specify the exact `target_agent_id` (e.g., `agent_17..._xyz` or `admin_ai`). **DO NOT use agent personas (like 'Researcher') as the target_agent_id.** Use the IDs provided in team lists or feedback messages.
- Respond to messages directed to you ([From @...]).
- **MANDATORY FINAL STEP: Report Results:** After completing **ALL** parts of your assigned task (including any file writing), your **VERY LAST ACTION** in that turn **MUST** be to use the `<send_message>` tool to report your completion and results (e.g., summary, analysis, confirmation of file write including filename and scope) back to the **agent who assigned you the task** (this is usually `admin_ai`, check the initial task message). **Failure to send this final confirmation message will stall the entire process.** Do not just stop; explicitly report completion.

**File System:**
- Use the `<file_system>` tool with the appropriate `scope` ('private' or 'shared') as instructed by the Admin AI. The `scope` determines where the file operation takes place.
- **`scope: private`**: Your personal sandbox. Use this for temporary files or work specific only to you. Path is relative to your agent's private directory.
- **`scope: shared`**: The shared workspace for the current project/session. Use this if the file needs to be accessed by other agents or the user. Path is relative to the session's shared directory.
- All paths provided (e.g., in `filename` or `path`) MUST be relative within the specified scope.
- If you write a file, you **must** still perform the **MANDATORY FINAL STEP** described above (using `send_message`) to report completion, the filename/path, and **the scope used** (`private` or `shared`) back to the requester.

**Task Management:**
- If you receive a complex task, break it down logically. Execute the steps sequentially. Report progress clearly on significant sub-steps or if you encounter issues using `send_message`. Remember the **MANDATORY FINAL STEP** upon full task completion.
--- End Standard Protocol ---
"""

# --- Specific Operational Instructions for Admin AI (with ID/Team info) ---
# These instructions are injected by the framework (initialize_bootstrap_agents)
# into the system prompt of the Admin AI, combined with its config.yaml prompt.
ADMIN_AI_OPERATIONAL_INSTRUCTIONS = """

--- Admin AI Core Operational Workflow ---
**Your Identity:**
*   Your Agent ID: `admin_ai`
*   Your Assigned Team ID: `N/A` (You manage teams, you aren't assigned to one)

**Your core function is to ORCHESTRATE and DELEGATE, not perform tasks directly.**

**Mandatory Workflow:**

1.  **Analyze User Request:** (Handled by your primary persona prompt from config). Ask clarifying questions if needed.
1.5 **Answer Direct Questions:** (Handled by your primary persona prompt from config). Offer to create a team for complex tasks. Do not generate code examples yourself.
2.  **Plan Agent Team & Initial Tasks:** Determine roles, specific instructions, team structure. Define initial high-level tasks. **Delegate aggressively.**
    *   **File Saving Scope Planning:** When defining agent instructions (`system_prompt` for `create_agent`), explicitly decide if the final output file should be `private` or `shared`. Instruct the agent accordingly to use the correct `scope` parameter with the `file_system` tool. Shared scope is generally preferred for final deliverables.
3.  **Execute Structured Delegation Plan:** Follow precisely:
    *   **(a) Check State:** Use `ManageTeamTool` (`list_teams`, `list_agents`). Get existing agent IDs if needed.
    *   **(b) Create Team(s):** Use `ManageTeamTool` (`action: create_team`, providing `team_id`).
    *   **(c) Create Agents Sequentially:** Use `ManageTeamTool` (`action: create_agent`). Specify `provider`, `model`, `persona`, role-specific `system_prompt` (including file scope instructions), `team_id`. Ensure the agent's `system_prompt` instructs it to report back to you (`admin_ai`) via `send_message`. **Wait** for feedback with `created_agent_id`. Store IDs.
    *   **(d) Kick-off Tasks:** Use `send_message` targeting the correct `created_agent_id`. Reiterate the need to report back to `admin_ai` via `send_message`.
4.  **Coordinate & Monitor:**
    *   Monitor incoming messages. **WAIT** for an agent to report completion (via `send_message`) before assuming its task is done.
    *   Relay necessary information between agents *only if required by your plan* using `send_message`.
    *   Provide clarification if agents are stuck.
    *   **Do NOT perform agents' tasks.** If an agent reports saving a file, ask them for the content and the scope (`private` or `shared`) via `send_message`. Use *your* `file_system` tool only as a last resort, specifying the correct scope and exact path.
    *   **DO NOT proceed** to synthesis or cleanup until you have received confirmation messages (via `send_message`) from **ALL** agents that their assigned tasks are complete.
5.  **Synthesize & Report to User:** **ONLY AFTER** confirming all delegated tasks are complete, compile results. Clearly state where final files were saved.
6.  **Clean Up:** **ONLY AFTER** delivering the final result to the user:
    *   **(a) Identify Agents/Teams:** Use `ManageTeamTool` with `action: list_agents` **immediately before deletion** to get the **current list and exact `agent_id` values** (e.g., `agent_17..._xyz`) of all dynamic agents created for the completed task.
    *   **(b) Delete Agents:** Delete **each dynamic agent individually** using `ManageTeamTool` with `action: delete_agent` and the **specific `agent_id` obtained in step (a).** **CRITICAL: You MUST provide the specific ID (like `agent_17..._xyz`) in the `agent_id` parameter. DO NOT use the agent's persona name.**
    *   **(c) Delete Team(s):** **AFTER** confirming **ALL** agents in a team are deleted (verify with `list_agents` again if needed), delete the team using `ManageTeamTool` with `action: delete_team` and the correct `team_id`.

**Tool Usage Reminders:**
*   Use exact `agent_id`s (obtained from `list_agents` or creation feedback) for `send_message` and **especially for `delete_agent`**. Double-check IDs before use.
*   Instruct agents clearly on whether to use `scope: private` or `scope: shared`.
*   Check the standard tool descriptions provided separately.
--- End Admin AI Core Operational Workflow ---
"""

# ... (Rest of AgentManager class remains the same as the version you provided last) ...

class AgentManager:
    """
    Main coordinator for agents. Handles task distribution, agent lifecycle (creation/deletion),
    tool execution routing, and orchestrates state/session management via dedicated managers.
    Includes provider configuration checks and stream error retries with user override.
    Injects standard framework instructions into dynamic agents and Admin AI.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        # --- Initialize State ---
        self.bootstrap_agents: List[str] = [] # List of bootstrap agent IDs
        self.agents: Dict[str, Agent] = {}    # Dictionary to hold Agent instances {agent_id: Agent}
        self.send_to_ui_func = broadcast      # Function to broadcast messages to UI

        # --- Initialize Core Components ---
        logger.info("Instantiating ToolExecutor...")
        self.tool_executor = ToolExecutor()
        logger.info("ToolExecutor instantiated.")
        # Pre-generate tool descriptions for prompts
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        logger.info("Generated XML tool descriptions for prompts.")

        logger.info("Instantiating AgentStateManager...")
        self.state_manager = AgentStateManager(self) # Manages team/assignment state
        logger.info("AgentStateManager instantiated.")

        logger.info("Instantiating SessionManager...")
        self.session_manager = SessionManager(self, self.state_manager) # Handles save/load
        logger.info("SessionManager instantiated.")

        # --- Session Tracking ---
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None

        # --- Ensure Directories ---
        self._ensure_projects_dir() # Ensure base directory for project data exists

        logger.info("AgentManager initialized synchronously. Bootstrap agents will be loaded asynchronously.")

    # --- Directory Setup ---
    def _ensure_projects_dir(self):
        """Ensures the base directory for projects exists."""
        try:
             settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e:
             logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)
    
    # --- Agent Initialization ---
    async def initialize_bootstrap_agents(self):
        """Loads and initializes agents defined as 'bootstrap' in the configuration."""
        logger.info("Initializing bootstrap agents asynchronously...")
        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list:
             logger.warning("No bootstrap agent configurations found in settings."); return

        # Ensure main sandbox directory exists (used by individual agent sandbox checks)
        main_sandbox_dir = BASE_DIR / "sandboxes"
        try:
             # Use asyncio.to_thread for synchronous file operations
             await asyncio.to_thread(main_sandbox_dir.mkdir, parents=True, exist_ok=True)
             logger.info(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
             logger.error(f"Error creating main sandbox directory: {e}")

        tasks = []
        formatted_allowed_models = settings.get_formatted_allowed_models()

        # --- *** CORRECTED: Define generic_standard_info BEFORE the loop *** ---
        # Prepare generic instructions part (tools description) ONCE
        generic_standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(
                agent_id='{agent_id}', # Placeholders - will be removed below
                team_id='{team_id}',   # Placeholders - will be removed below
                tool_descriptions_xml=self.tool_descriptions_xml
            )
        # Remove the generic placeholder lines as they aren't needed for AdminAI context specifically
        generic_standard_info = generic_standard_info.replace("Your Agent ID: {agent_id}\n", "")
        generic_standard_info = generic_standard_info.replace("Your Assigned Team ID: {team_id}\n", "")
        # --- *** END CORRECTION *** ---

        # Iterate through agent configurations loaded from settings
        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id:
                 logger.warning("Skipping bootstrap agent configuration due to missing 'agent_id'.")
                 continue

            agent_config_data = agent_conf_entry.get("config", {})
            provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)

            # Check if the required provider is configured in .env
            if not settings.is_provider_configured(provider_name):
                logger.error(f"--- Cannot initialize bootstrap agent '{agent_id}': Provider '{provider_name}' is not configured in .env. Skipping. ---")
                continue

            # --- Assemble the FINAL system prompt ---
            # Create a mutable copy of the config data
            final_agent_config_data = agent_config_data.copy()

            # **Special Handling for Admin AI Prompt Construction**
            if agent_id == BOOTSTRAP_AGENT_ID:
                # Get the user-defined persona/goal part from config.yaml
                user_defined_prompt = final_agent_config_data.get("system_prompt", "")

                # *** CONSTRUCT ADMIN AI PROMPT ***
                # The final prompt combines:
                # 1. User-defined persona/goal (from config.yaml)
                # 2. Specific operational workflow (ADMIN_AI_OPERATIONAL_INSTRUCTIONS constant)
                # 3. Generic tool descriptions (derived from STANDARD_FRAMEWORK_INSTRUCTIONS constant)
                # 4. Allowed models list for dynamic agents (from settings)
                final_agent_config_data["system_prompt"] = (
                    f"--- Primary Goal/Persona ---\n{user_defined_prompt}\n\n"
                    f"{ADMIN_AI_OPERATIONAL_INSTRUCTIONS}\n\n" # Add the operational workflow
                    f"{generic_standard_info}\n\n" # Add the generic tool descriptions part
                    f"---\n{formatted_allowed_models}\n---" # Add allowed models separately at the end
                )
                logger.info(f"Assembled final prompt for '{BOOTSTRAP_AGENT_ID}': Combined config.yaml prompt + Operational Instructions + Tool Descriptions + Allowed Models.")
            else:
                # For other bootstrap agents (if any), use their config.yaml prompt directly
                # (No framework injection for non-admin bootstrap agents currently)
                logger.info(f"Using system prompt directly from config for bootstrap agent '{agent_id}'.")

            # Create an async task for agent creation with the potentially modified config data
            tasks.append(self._create_agent_internal(
                agent_id_requested=agent_id,
                agent_config_data=final_agent_config_data, # Use the modified data
                is_bootstrap=True
            ))

        # --- Gather results of agent creation tasks ---
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_ids = []
        # Process results, mapping back to agent IDs for logging
        processed_count = 0
        for i, result in enumerate(results):
            task_index = -1
            # Find the corresponding config entry (handling skipped configs)
            current_processed_count = 0
            for idx, conf in enumerate(agent_configs_list):
                 p_name = conf.get("config", {}).get("provider", settings.DEFAULT_AGENT_PROVIDER)
                 if settings.is_provider_configured(p_name):
                     if current_processed_count == i:
                         task_index = idx
                         break
                     current_processed_count += 1
            # Determine agent ID for logging
            if task_index == -1:
                 agent_id_log = f"unknown_{i}"; logger.error(f"Could not map result index {i} back to agent config.")
            else:
                 agent_id_log = agent_configs_list[task_index].get("agent_id", f"unknown_{i}")

            # Log success or failure
            if isinstance(result, Exception):
                 logger.error(f"--- Failed bootstrap init '{agent_id_log}': {result} ---", exc_info=result)
            elif isinstance(result, tuple) and len(result) == 3:
                 success, message, created_agent_id = result
                 if success and created_agent_id:
                     self.bootstrap_agents.append(created_agent_id)
                     successful_ids.append(created_agent_id)
                     logger.info(f"--- Bootstrap agent '{created_agent_id}' initialized. ---")
                 else:
                     logger.error(f"--- Failed bootstrap init '{agent_id_log}': {message} ---")
            else:
                 logger.error(f"--- Unexpected result type during bootstrap init for '{agent_id_log}': {result} ---")

        logger.info(f"Finished async bootstrap agent initialization. Active bootstrap agents: {successful_ids}")
        if BOOTSTRAP_AGENT_ID not in self.agents:
             logger.critical(f"CRITICAL: Admin AI ('{BOOTSTRAP_AGENT_ID}') failed to initialize!")


    # --- Agent Creation Core Logic ---
    async def _create_agent_internal(
        self,
        agent_id_requested: Optional[str],
        agent_config_data: Dict[str, Any],
        is_bootstrap: bool = False,
        team_id: Optional[str] = None,
        loading_from_session: bool = False # Flag to skip prompt injection when loading
        ) -> Tuple[bool, str, Optional[str]]:
        """
        Internal core logic for creating an agent instance (bootstrap or dynamic).
        Handles ID generation, validation, prompt construction, provider instantiation,
        agent object creation, sandbox setup, and state registration.

        **Prompt Construction for Dynamic Agents:**
        - Takes the role-specific prompt from `agent_config_data` (provided by Admin AI).
        - Prepends the `STANDARD_FRAMEWORK_INSTRUCTIONS` constant (formatted with agent ID, team ID, tool descriptions).
        - Uses this combined prompt when creating the Agent instance.

        Returns:
            Tuple[bool, str, Optional[str]]: (success_flag, message, created_agent_id)
        """
        # 1. Determine Agent ID (Code remains the same)
        agent_id: Optional[str] = None
        if agent_id_requested and agent_id_requested in self.agents:
             msg = f"Agent ID '{agent_id_requested}' already exists."
             logger.error(msg)
             return False, msg, None
        elif agent_id_requested:
             agent_id = agent_id_requested # Use the requested ID
        else:
             agent_id = self._generate_unique_agent_id() # Generate a unique ID

        if not agent_id:
             return False, "Failed to determine or generate Agent ID.", None

        logger.debug(f"Creating agent '{agent_id}' (Bootstrap: {is_bootstrap}, SessionLoad: {loading_from_session}, Team: {team_id})")

        # 2. Extract Config & Validate Provider Configuration (Code remains the same)
        provider_name = agent_config_data.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        model = agent_config_data.get("model", settings.DEFAULT_AGENT_MODEL)
        persona = agent_config_data.get("persona", settings.DEFAULT_PERSONA)

        if not settings.is_provider_configured(provider_name):
            msg = f"Validation Error creating '{agent_id}': Provider '{provider_name}' is not configured."; logger.error(msg); return False, msg, None

        # 3. Validate Model against allowed list (only for dynamic, non-session-load agents) (Code remains the same)
        if not is_bootstrap and not loading_from_session:
            allowed_models = settings.ALLOWED_SUB_AGENT_MODELS.get(provider_name)
            if allowed_models is None:
                msg = f"Validation Error creating '{agent_id}': Provider '{provider_name}' not found in allowed_sub_agent_models config."; logger.error(msg); return False, msg, None
            valid_allowed_models = [m for m in allowed_models if m and m.strip()]
            if not valid_allowed_models or model not in valid_allowed_models:
                allowed_list_str = ', '.join(valid_allowed_models) if valid_allowed_models else 'None configured'
                msg = f"Validation Error creating '{agent_id}': Model '{model}' not allowed for '{provider_name}'. Allowed: [{allowed_list_str}]"; logger.error(msg); return False, msg, None
            logger.info(f"Dynamic agent creation validated for '{agent_id}': Provider '{provider_name}', Model '{model}' is allowed.")

        # 4. Extract other config details (Code remains the same)
        role_specific_prompt = agent_config_data.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        temperature = agent_config_data.get("temperature", settings.DEFAULT_TEMPERATURE)
        allowed_provider_keys = ['api_key', 'base_url', 'referer']
        agent_config_keys_to_exclude = [
            'provider', 'model', 'system_prompt', 'temperature', 'persona',
            'project_name', 'session_name'
        ] + allowed_provider_keys
        provider_specific_kwargs = { k: v for k, v in agent_config_data.items() if k not in agent_config_keys_to_exclude }
        if agent_config_data.get("referer"): provider_specific_kwargs["referer"] = agent_config_data["referer"]

        # 5. Construct Final System Prompt
        final_system_prompt = role_specific_prompt # Start with the provided role-specific part

        # *** DYNAMIC AGENT PROMPT ASSEMBLY ***
        # Inject standard framework instructions ONLY for dynamic agents NOT being loaded from session
        if not loading_from_session and not is_bootstrap:
             logger.debug(f"Constructing final prompt for dynamic agent '{agent_id}'...")
             # Format the STANDARD_FRAMEWORK_INSTRUCTIONS with this agent's details
             standard_info = STANDARD_FRAMEWORK_INSTRUCTIONS.format(
                 agent_id=agent_id,
                 team_id=team_id or "N/A", # Show N/A if no team initially
                 tool_descriptions_xml=self.tool_descriptions_xml # Use pre-generated descriptions
             )
             # Prepend standard instructions to the role-specific prompt provided by Admin AI
             # The Admin AI provides the part describing the agent's specific task/role.
             # The framework provides the standard capabilities (tools, comms, file system scopes).
             final_system_prompt = standard_info + "\n\n--- Your Specific Role & Task ---\n" + role_specific_prompt
             logger.info(f"Injected standard framework instructions for dynamic agent '{agent_id}'.")
        # *** END DYNAMIC AGENT PROMPT ASSEMBLY ***
        elif loading_from_session:
             # Use the prompt exactly as loaded from session data
             final_system_prompt = agent_config_data.get("system_prompt", role_specific_prompt) # Should exist in loaded data
             logger.debug(f"Using existing prompt for agent '{agent_id}' (loading from session).")
        elif is_bootstrap:
             # Bootstrap agents (including Admin AI) have their prompts fully constructed
             # in initialize_bootstrap_agents or used directly from config if not Admin AI.
             # Use the prompt already present in agent_config_data.
             final_system_prompt = agent_config_data.get("system_prompt", final_system_prompt)
             logger.debug(f"Using pre-assembled prompt for bootstrap agent '{agent_id}'.")

        # 6. Store the final combined config entry (Code remains the same)
        final_agent_config_entry = {
            "agent_id": agent_id,
            "config": {
                "provider": provider_name,
                "model": model,
                "system_prompt": final_system_prompt, # Store the final prompt used
                "persona": persona,
                "temperature": temperature,
                **provider_specific_kwargs # Include any extra args
            }
        }

        # 7. Instantiate LLM Provider (Code remains the same)
        ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
        if not ProviderClass:
             msg = f"Unknown provider '{provider_name}' specified for agent '{agent_id}'."; logger.error(msg); return False, msg, None
        base_provider_config = settings.get_provider_config(provider_name)
        provider_config_overrides = {k: agent_config_data[k] for k in allowed_provider_keys if k in agent_config_data}
        final_provider_args = { **base_provider_config, **provider_specific_kwargs, **provider_config_overrides}
        final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
        try:
             llm_provider_instance = ProviderClass(**final_provider_args)
             logger.info(f"  Instantiated provider {ProviderClass.__name__} for '{agent_id}'.")
        except Exception as e:
             msg = f"Provider instantiation failed for '{agent_id}': {e}"; logger.error(msg, exc_info=True); return False, msg, None

        # 8. Instantiate Agent Object (Code remains the same)
        try:
            agent = Agent(
                agent_config=final_agent_config_entry, # Pass the full final entry
                llm_provider=llm_provider_instance,
                manager=self # Pass reference to self
            )
            logger.info(f"  Instantiated Agent object for '{agent_id}'.")
        except Exception as e:
            msg = f"Agent instantiation failed for '{agent_id}': {e}"; logger.error(msg, exc_info=True)
            await self._close_provider_safe(llm_provider_instance)
            return False, msg, None

        # 9. Ensure Agent Sandbox Directory Exists (Code remains the same)
        try:
            sandbox_ok = await asyncio.to_thread(agent.ensure_sandbox_exists)
            if not sandbox_ok:
                 logger.warning(f"  Failed to ensure sandbox for '{agent_id}'. Filesystem tool might fail.")
        except Exception as e:
            logger.error(f"  Error ensuring sandbox directory for '{agent_id}': {e}", exc_info=True)
            logger.warning(f"  Proceeding without guaranteed sandbox for '{agent_id}'. Filesystem tool might fail.")

        # 10. Add agent instance to the main registry (Code remains the same)
        self.agents[agent_id] = agent
        logger.debug(f"Agent '{agent_id}' added to self.agents dictionary.")

        # 11. Assign to Team State via StateManager (if team_id provided) (Code remains the same)
        team_add_msg_suffix = ""
        if team_id:
            team_add_success, team_add_msg = await self.state_manager.add_agent_to_team(agent_id, team_id)
            if not team_add_success:
                team_add_msg_suffix = f" (Warning adding to team state: {team_add_msg})"
            else:
                logger.info(f"Agent '{agent_id}' state added to team '{team_id}' via StateManager.")

        # 12. Return Success (Code remains the same)
        message = f"Agent '{agent_id}' created successfully." + team_add_msg_suffix
        return True, message, agent_id


    # --- Public Agent Creation Method (Called by ManageTeamTool Handler) ---
    # (No changes needed in this method's code)
    async def create_agent_instance(
        self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str,
        team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs
        ) -> Tuple[bool, str, Optional[str]]:
        """
        Public method for creating dynamic agents. Validates inputs and calls internal creation logic.
        Notifies UI on success.
        """
        if not provider or not model or not system_prompt or not persona:
             return False, "Missing required parameters for agent creation (provider, model, system_prompt, persona).", None

        agent_config_data = { "provider": provider, "model": model, "system_prompt": system_prompt, "persona": persona }
        if temperature is not None: agent_config_data["temperature"] = temperature
        known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
        extra_kwargs = {k: v for k, v in kwargs.items() if k not in known_args}
        agent_config_data.update(extra_kwargs)

        success, message, created_agent_id = await self._create_agent_internal(
            agent_id_requested=agent_id_requested,
            agent_config_data=agent_config_data,
            is_bootstrap=False,
            team_id=team_id,
            loading_from_session=False
        )

        if success and created_agent_id:
            created_agent = self.agents.get(created_agent_id)
            config_sent_to_ui = created_agent.agent_config.get("config", {}) if created_agent else {}
            current_team = self.state_manager.get_agent_team(created_agent_id)
            await self.send_to_ui({
                "type": "agent_added",
                "agent_id": created_agent_id,
                "config": config_sent_to_ui,
                "team": current_team
            })
            await self.push_agent_status_update(created_agent_id)

        return success, message, created_agent_id

    # --- Agent Deletion ---
    # (No changes needed in this method's code)
    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        """Removes a dynamic agent instance, cleans up resources, and updates state."""
        if not agent_id: return False, "Agent ID cannot be empty."
        if agent_id not in self.agents: return False, f"Agent '{agent_id}' not found."
        if agent_id in self.bootstrap_agents: return False, f"Cannot delete bootstrap agent '{agent_id}'."

        agent_instance = self.agents.pop(agent_id)
        self.state_manager.remove_agent_from_all_teams_state(agent_id)
        await self._close_provider_safe(agent_instance.llm_provider)
        # Optional sandbox cleanup omitted for safety

        message = f"Agent '{agent_id}' deleted successfully."
        logger.info(message)
        await self.send_to_ui({"type": "agent_deleted", "agent_id": agent_id})
        return True, message

    # --- Agent ID Generation ---
    # (No changes needed in this method's code)
    def _generate_unique_agent_id(self, prefix="agent") -> str:
        timestamp = int(time.time() * 1000)
        short_uuid = uuid.uuid4().hex[:4]
        while True:
            new_id = f"{prefix}_{timestamp}_{short_uuid}".replace(":", "_")
            if new_id not in self.agents:
                return new_id
            time.sleep(0.001)
            timestamp = int(time.time() * 1000)
            short_uuid = uuid.uuid4().hex[:4]


    # --- Message Handling ---
    # (No changes needed in this method's code)
    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        logger.info(f"AgentManager received user message for Admin AI: '{message[:100]}...'")
        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent:
            logger.error(f"Admin AI ('{BOOTSTRAP_AGENT_ID}') not found. Cannot process user message.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."})
            return
        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Delegating user message to '{BOOTSTRAP_AGENT_ID}'.")
            admin_agent.message_history.append({"role": "user", "content": message})
            asyncio.create_task(self._handle_agent_generator(admin_agent))
        elif admin_agent.status == AGENT_STATUS_AWAITING_USER_OVERRIDE:
             logger.warning(f"Admin AI ({admin_agent.status}) awaiting user override. New message ignored.")
             await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI is waiting for user input..." })
        else:
            logger.info(f"Admin AI busy ({admin_agent.status}). User message queued implicitly in history.")
            admin_agent.message_history.append({"role": "user", "content": message})
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Message queued." })

    # --- User Override Handling ---
    # (No changes needed in this method's code)
    async def handle_user_override(self, override_data: Dict[str, Any]):
        agent_id = override_data.get("agent_id")
        new_provider_name = override_data.get("new_provider")
        new_model = override_data.get("new_model")
        if not all([agent_id, new_provider_name, new_model]):
            logger.error(f"Received invalid user override data: {override_data}")
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": "Invalid override data."})
            return
        agent = self.agents.get(agent_id)
        if not agent:
            logger.error(f"Cannot apply user override: Agent '{agent_id}' not found.")
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"Agent {agent_id} not found."})
            return
        if agent.status != AGENT_STATUS_AWAITING_USER_OVERRIDE:
            logger.warning(f"Received override for agent '{agent_id}' but status is '{agent.status}'. Ignoring.")
            return

        logger.info(f"Applying user override for agent '{agent_id}'. New: {new_provider_name}/{new_model}")
        ProviderClass = PROVIDER_CLASS_MAP.get(new_provider_name)
        if not ProviderClass or not settings.is_provider_configured(new_provider_name):
            error_msg = f"Override failed: Provider '{new_provider_name}' unknown or not configured."
            logger.error(error_msg)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": error_msg})
            return

        old_provider_instance = agent.llm_provider
        old_provider_name = agent.provider_name
        old_model = agent.model
        try:
            agent.provider_name = new_provider_name
            agent.model = new_model
            if hasattr(agent, 'agent_config') and "config" in agent.agent_config:
                agent.agent_config["config"]["provider"] = new_provider_name
                agent.agent_config["config"]["model"] = new_model

            base_provider_config = settings.get_provider_config(new_provider_name)
            provider_kwargs = agent.agent_config.get("config", {}).copy()
            for key in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']: provider_kwargs.pop(key, None)
            final_provider_args = { **base_provider_config, **provider_kwargs }
            final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}
            new_provider_instance = ProviderClass(**final_provider_args)
            agent.llm_provider = new_provider_instance
            await self._close_provider_safe(old_provider_instance)

            logger.info(f"User override applied for '{agent_id}'. Restarting cycle.")
            agent.set_status(AGENT_STATUS_IDLE)
            await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Override applied. Retrying with {new_provider_name}/{new_model}."})
            asyncio.create_task(self._handle_agent_generator(agent, 0))
        except Exception as e:
            logger.error(f"Error applying override for '{agent_id}': {e}", exc_info=True)
            agent.provider_name = old_provider_name; agent.model = old_model; agent.llm_provider = old_provider_instance
            if hasattr(agent, 'agent_config') and "config" in agent.agent_config:
                 agent.agent_config["config"]["provider"] = old_provider_name; agent.agent_config["config"]["model"] = old_model
            agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"Failed to apply override: {e}. Try again."})

    # --- Agent Processing Cycle ---
    async def _handle_agent_generator(self, agent: Agent, retry_count: int = 0):
        """
        Manages the asynchronous generator returned by agent.process_message().
        Handles events yielded by the agent (chunks, tool calls, errors).
        Executes tools sequentially. Handles stream errors with retries/override.
        Processes feedback and manages agent reactivation.
        """
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}' (Retry Attempt: {retry_count})...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        reactivate_agent_after_feedback = False
        current_cycle_error = False
        is_stream_related_error = False
        last_error_content = ""
        history_len_before_processing = len(agent.message_history)
        logger.debug(f"Agent '{agent_id}' history length before cycle: {history_len_before_processing}")

        try:
            agent_generator = agent.process_message()
            while True:
                try: event = await agent_generator.asend(None)
                except StopAsyncIteration: logger.info(f"Agent '{agent_id}' generator finished normally."); break
                except Exception as gen_err:
                    logger.error(f"Generator error for '{agent_id}': {gen_err}", exc_info=True)
                    agent.set_status(AGENT_STATUS_ERROR)
                    await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Generator crashed - {gen_err}]"})
                    current_cycle_error = True; break

                event_type = event.get("type")
                if event_type in ["response_chunk", "status", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "final_response":
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content})
                            logger.debug(f"Appended final response for '{agent_id}'.")
                elif event_type == "error":
                    last_error_content = event.get("content", "[Unknown Agent Error]")
                    logger.error(f"Agent '{agent_id}' reported error: {last_error_content}")
                    is_stream_error = any(indicator in last_error_content for indicator in ["Error processing stream chunk", "APIError during stream", "Failed to decode stream chunk", "Stream connection error", "Provider returned error", "connection/timeout error", "Status 429", "RateLimitError", "Status 500", "Status 503"])
                    is_stream_related_error = is_stream_error
                    if not is_stream_error:
                        if "agent_id" not in event: event["agent_id"] = agent_id
                        await self.send_to_ui(event)
                        agent.set_status(AGENT_STATUS_ERROR)
                    else: logger.warning(f"Detected potentially temporary stream error for agent '{agent_id}'. Retries/Override will be handled.")
                    current_cycle_error = True; break
                elif event_type == "tool_requests":
                    all_tool_calls: List[Dict] = event.get("calls", [])
                    if not all_tool_calls: continue
                    logger.info(f"Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                    agent_last_response = event.get("raw_assistant_response")
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response})
                         logger.debug(f"Appended assistant response (with tools) for '{agent_id}'.")

                    management_calls = []; other_calls = []; invalid_call_results = []
                    for call in all_tool_calls:
                         call_id, tool_name, tool_args = call.get("id"), call.get("name"), call.get("arguments", {})
                         if call_id and tool_name and isinstance(tool_args, dict):
                            if tool_name == ManageTeamTool.name: management_calls.append(call)
                            else: other_calls.append(call)
                         else:
                            logger.warning(f"Skipping invalid tool request format from '{agent_id}': {call}")
                            fail_result = await self._failed_tool_result(call_id, tool_name);
                            if fail_result: invalid_call_results.append(fail_result)
                    if invalid_call_results:
                        for fail_res in invalid_call_results:
                            agent.message_history.append({"role": "tool", "tool_call_id": fail_res['call_id'], "content": str(fail_res['content']) })

                    calls_to_execute = management_calls + other_calls
                    activation_tasks = []
                    manager_action_feedback = []
                    if calls_to_execute:
                        logger.info(f"Executing {len(calls_to_execute)} tool call(s) sequentially for agent '{agent_id}'. Mgmt: {len(management_calls)}, Other: {len(other_calls)}")
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})

                        for call in calls_to_execute:
                            call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']
                            # *** FIX: Pass project/session context ***
                            result = await self._execute_single_tool(
                                agent, call_id, tool_name, tool_args,
                                project_name=self.current_project,
                                session_name=self.current_session
                            )
                            # *** END FIX ***
                            if result:
                                raw_content_for_hist = result.get("content", "[Tool Error: No content returned]")
                                tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_for_hist) }
                                if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id:
                                     agent.message_history.append(tool_msg)
                                     logger.debug(f"Appended raw tool result for {call_id} to '{agent_id}' history.")

                                raw_tool_output = result.get("_raw_result")
                                if tool_name == ManageTeamTool.name:
                                    if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                        action = raw_tool_output.get("action"); params = raw_tool_output.get("params", {})
                                        logger.info(f"Processing successful ManageTeamTool execution signal: Action='{action}' by '{agent_id}'.")
                                        action_success, action_message, action_data = await self._handle_manage_team_action(action, params)
                                        feedback = {"call_id": call_id, "action": action, "success": action_success, "message": action_message}
                                        if action_data: feedback["data"] = action_data
                                        manager_action_feedback.append(feedback)
                                    elif isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "error":
                                         logger.warning(f"ManageTeamTool call {call_id} failed validation/execution. Raw Result: {raw_tool_output}")
                                         manager_action_feedback.append({"call_id": call_id, "action": raw_tool_output.get("action"), "success": False, "message": raw_tool_output.get("message", "Tool execution failed.")})
                                    else:
                                         logger.warning(f"ManageTeamTool call {call_id} had unexpected result structure: {result}")
                                         manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result structure."})
                                elif tool_name == SendMessageTool.name:
                                    target_id = call['arguments'].get("target_agent_id")
                                    msg_content = call['arguments'].get("message_content")
                                    activation_task = None
                                    if target_id and msg_content is not None:
                                        activation_task = await self._route_and_activate_agent_message(agent_id, target_id, msg_content)
                                        if activation_task: activation_tasks.append(activation_task)
                                    else:
                                        logger.error(f"SendMessage args incomplete for call {call_id}. Args: {call['arguments']}")
                                        manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": "Validation Error: Missing target_agent_id or message_content."})
                            else:
                                 logger.error(f"Tool execution failed unexpectedly for call_id {call_id}, no result dictionary returned.")
                                 manager_action_feedback.append({"call_id": call_id, "action": tool_name, "success": False, "message": "Tool execution failed unexpectedly (no result)."})

                        logger.info(f"Finished executing {len(calls_to_execute)} tool calls for agent '{agent_id}'.")
                        if activation_tasks:
                            logger.info(f"Waiting for {len(activation_tasks)} activation tasks triggered by '{agent_id}'.")
                            await asyncio.gather(*activation_tasks)
                            logger.info(f"Completed gathering activation tasks triggered by '{agent_id}'.")

                        if manager_action_feedback:
                            feedback_appended = False
                            for feedback in manager_action_feedback:
                                feedback_content = f"[Manager Result for {feedback.get('action', 'N/A')} (Call ID: {feedback['call_id']})]: Success={feedback['success']}. Message: {feedback['message']}"
                                if feedback.get("data"):
                                     try:
                                         data_str = json.dumps(feedback['data'], indent=2)
                                         feedback_content += f"\nData:\n{data_str[:1500]}{'... (truncated)' if len(data_str) > 1500 else ''}"
                                     except TypeError: feedback_content += f"\nData: [Unserializable Data]"
                                feedback_message: MessageDict = { "role": "tool", "tool_call_id": feedback['call_id'], "content": feedback_content }
                                if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != feedback_content:
                                     agent.message_history.append(feedback_message)
                                     logger.debug(f"Appended manager feedback for call {feedback['call_id']} to '{agent_id}' history.")
                                     feedback_appended = True
                            if feedback_appended: reactivate_agent_after_feedback = True
                else: logger.warning(f"Unknown event type '{event_type}' received from agent '{agent_id}'.")
        except Exception as e:
            logger.error(f"Error occurred while handling generator for agent '{agent_id}': {e}", exc_info=True)
            agent.set_status(AGENT_STATUS_ERROR); current_cycle_error = True
            last_error_content = f"[Manager Error: Unexpected error in generator handler - {e}]"
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
        finally:
            if agent_generator:
                try: await agent_generator.aclose(); logger.debug(f"Closed generator for '{agent_id}'.")
                except Exception as close_err: logger.error(f"Error closing generator for '{agent_id}': {close_err}", exc_info=True)

            if current_cycle_error and is_stream_related_error and retry_count < MAX_STREAM_RETRIES:
                retry_delay = STREAM_RETRY_DELAYS[retry_count]
                logger.warning(f"Stream error for '{agent_id}'. Retrying in {retry_delay:.1f}s (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES})...")
                await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying (Attempt {retry_count + 1}/{MAX_STREAM_RETRIES}, delay {retry_delay}s)..."})
                await asyncio.sleep(retry_delay); agent.set_status(AGENT_STATUS_IDLE)
                asyncio.create_task(self._handle_agent_generator(agent, retry_count + 1))
                logger.info(f"Retry task scheduled for agent '{agent_id}'. Cycle ending."); return
            elif current_cycle_error and is_stream_related_error and retry_count >= MAX_STREAM_RETRIES:
                logger.error(f"Agent '{agent_id}' failed after {MAX_STREAM_RETRIES} retries. Requesting user override.")
                agent.set_status(AGENT_STATUS_AWAITING_USER_OVERRIDE)
                await self.send_to_ui({ "type": "request_user_override", "agent_id": agent_id, "persona": agent.persona, "current_provider": agent.provider_name, "current_model": agent.model, "last_error": last_error_content, "message": f"Agent '{agent.persona}' failed after retries. Provide alternative or try again." })
                logger.info(f"User override requested for '{agent_id}'. Cycle ending."); return
            elif reactivate_agent_after_feedback and not current_cycle_error:
                logger.info(f"Reactivating agent '{agent_id}' to process manager feedback.")
                agent.set_status(AGENT_STATUS_IDLE)
                asyncio.create_task(self._handle_agent_generator(agent, 0))
                logger.info(f"Reactivation task scheduled for '{agent_id}'. Cycle ending."); return
            elif not current_cycle_error:
                 history_len_after_processing = len(agent.message_history)
                 if history_len_after_processing > history_len_before_processing and agent.message_history and agent.message_history[-1].get("role") == "user":
                      logger.info(f"Agent '{agent_id}' has new message(s). Reactivating.")
                      agent.set_status(AGENT_STATUS_IDLE)
                      asyncio.create_task(self._handle_agent_generator(agent, 0))
                      logger.info(f"Reactivation task scheduled for '{agent_id}'. Cycle ending."); return
                 else: logger.debug(f"Agent '{agent_id}' cycle finished cleanly, no new incoming user messages detected.")

            final_status = agent.status
            if final_status not in [AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE, AGENT_STATUS_AWAITING_TOOL]:
                 logger.warning(f"Agent '{agent_id}' ended generator handling in unexpected state '{final_status}'. Setting to IDLE.")
                 agent.set_status(AGENT_STATUS_IDLE)
            await self.push_agent_status_update(agent_id)
            log_level = logging.ERROR if agent.status in [AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_OVERRIDE] else logging.INFO
            logger.log(log_level, f"Manager finished handling generator cycle for Agent '{agent_id}'. Final status: {agent.status}")

    # --- Tool Execution & Management ---
    async def _handle_manage_team_action(self, action: Optional[str], params: Dict[str, Any]) -> Tuple[bool, str, Optional[Any]]:
        """Dispatches ManageTeamTool actions to internal methods or StateManager."""
        if not action: return False, "No action specified.", None
        success, message, result_data = False, "Unknown action or error.", None
        try:
            logger.debug(f"Manager: Delegating ManageTeam action '{action}' with params: {params}")
            agent_id = params.get("agent_id"); team_id = params.get("team_id")
            provider = params.get("provider"); model = params.get("model")
            system_prompt = params.get("system_prompt"); persona = params.get("persona")
            temperature = params.get("temperature")
            # Collect any extra kwargs not explicitly handled
            known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
            extra_kwargs = {k: v for k, v in params.items() if k not in known_args}

            # --- Dispatch based on action ---
            if action == "create_agent":
                success, message, created_agent_id = await self.create_agent_instance(
                    agent_id, provider, model, system_prompt, persona, team_id, temperature, **extra_kwargs
                )
                if success and created_agent_id:
                    # Return data useful for Admin AI (e.g., the created ID)
                    result_data = { "created_agent_id": created_agent_id, "persona": persona, "provider": provider, "model": model, "team_id": team_id }
                    message = f"Agent '{persona}' created successfully with ID '{created_agent_id}'." # More informative message
            elif action == "delete_agent":
                 success, message = await self.delete_agent_instance(agent_id)
            elif action == "create_team":
                 success, message = await self.state_manager.create_new_team(team_id)
                 if success: result_data = {"created_team_id": team_id}
            elif action == "delete_team":
                 success, message = await self.state_manager.delete_existing_team(team_id)
            elif action == "add_agent_to_team":
                 success, message = await self.state_manager.add_agent_to_team(agent_id, team_id)
                 if success: await self._update_agent_prompt_team_id(agent_id, team_id) # Update live prompt state
            elif action == "remove_agent_from_team":
                 success, message = await self.state_manager.remove_agent_from_team(agent_id, team_id)
                 if success: await self._update_agent_prompt_team_id(agent_id, None) # Update live prompt state
            elif action == "list_agents":
                 filter_team_id = params.get("team_id"); # Optional filter
                 result_data = self.get_agent_info_list_sync(filter_team_id=filter_team_id)
                 success = True; count = len(result_data)
                 message = f"Found {count} agent(s)"
                 if filter_team_id: message += f" in team '{filter_team_id}'."
                 else: message += " in total."
            elif action == "list_teams":
                 result_data = self.state_manager.get_team_info_dict(); success = True; message = f"Found {len(result_data)} team(s)."
            else:
                 message = f"Unrecognized action: {action}"; logger.warning(message)

            logger.info(f"ManageTeamTool action '{action}' result: Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e:
             message = f"Error processing ManageTeamTool action '{action}': {e}"
             logger.error(message, exc_info=True)
             return False, message, None

    async def _update_agent_prompt_team_id(self, agent_id: str, new_team_id: Optional[str]):
        """Updates the team ID within an agent's live system prompt state (in memory)."""
        agent = self.agents.get(agent_id)
        # Only update dynamic agents, not bootstrap ones
        if agent and not (agent_id in self.bootstrap_agents):
            try:
                # Use regex to replace the team ID line safely, handling potential variations
                team_line_regex = r"(Your Assigned Team ID:).*"
                new_team_line = rf"\1 {new_team_id or 'N/A'}" # Use N/A if team ID is None

                # Update the agent's live system prompt attribute
                agent.final_system_prompt = re.sub(team_line_regex, new_team_line, agent.final_system_prompt)

                # Update the prompt within the stored agent_config dictionary (used for saving)
                if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config:
                     agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt

                # Update the prompt in the agent's active message history (if present)
                if agent.message_history and agent.message_history[0]["role"] == "system":
                    agent.message_history[0]["content"] = agent.final_system_prompt

                logger.info(f"Updated team ID ({new_team_id or 'N/A'}) in live prompt state for dynamic agent '{agent_id}'.")
            except Exception as e:
                 logger.error(f"Error updating system prompt state for agent '{agent_id}' after team change: {e}", exc_info=True)


    # --- *** MODIFIED Message Routing *** ---
    async def _route_and_activate_agent_message(self, sender_id: str, target_id: str, message_content: str) -> Optional[asyncio.Task]:
        """
        Routes a message between agents using SendMessageTool.
        Checks target existence and team membership rules.
        Appends message to target history and activates target if idle.
        Appends feedback to SENDER history if target does not exist.

        Args:
            sender_id: ID of the sending agent.
            target_id: ID of the target agent.
            message_content: The content of the message.

        Returns:
            Optional[asyncio.Task]: An asyncio Task if the target agent was activated, otherwise None.
        """
        sender_agent = self.agents.get(sender_id)
        target_agent = self.agents.get(target_id)

        # --- Check Sender Existence ---
        if not sender_agent:
            logger.error(f"SendMsg route error: Sender '{sender_id}' not found. Cannot route message or provide feedback.")
            return None # Cannot proceed

        # --- Check Target Existence ---
        if not target_agent:
            error_msg = f"Failed to send message: Target agent '{target_id}' not found."
            logger.error(f"SendMsg route error from '{sender_id}': {error_msg}")
            # --- Add Feedback to Sender History ---
            feedback_message: MessageDict = {
                "role": "tool",
                "tool_call_id": f"send_message_failed_{target_id}", # Use a descriptive pseudo-ID
                "content": f"[Manager Feedback for SendMessage]: {error_msg}"
            }
            sender_agent.message_history.append(feedback_message)
            logger.debug(f"Appended 'target not found' feedback to sender '{sender_id}' history.")
            # --- End Feedback ---
            return None # Indicate routing failed, no activation task

        # --- Communication Rules Check (Team Membership) ---
        sender_team = self.state_manager.get_agent_team(sender_id)
        target_team = self.state_manager.get_agent_team(target_id)
        allowed = False

        if sender_id == BOOTSTRAP_AGENT_ID: # Admin AI can send to anyone
            allowed = True
            logger.info(f"Admin AI ('{sender_id}') sending message to '{target_id}'.")
        elif target_id == BOOTSTRAP_AGENT_ID: # Anyone can send to Admin AI
             allowed = True
             logger.info(f"Agent '{sender_id}' sending message to Admin AI ('{target_id}').")
        elif sender_team and sender_team == target_team: # Agents in the same team can communicate
            allowed = True
            logger.info(f"Routing message from '{sender_id}' to '{target_id}' within team '{target_team}'.")

        if not allowed:
            # --- Add Feedback to Sender History ---
            error_msg = f"Message blocked: Sender '{sender_id}' (Team: {sender_team or 'N/A'}) cannot send to Target '{target_id}' (Team: {target_team or 'N/A'}). Communication restricted to teammates or Admin AI."
            logger.warning(error_msg)
            feedback_message: MessageDict = {
                "role": "tool",
                "tool_call_id": f"send_message_failed_{target_id}",
                "content": f"[Manager Feedback for SendMessage]: {error_msg}"
            }
            sender_agent.message_history.append(feedback_message)
            logger.debug(f"Appended 'communication blocked' feedback to sender '{sender_id}' history.")
            # --- End Feedback ---
            return None # Indicate routing failed

        # --- Proceed with Message Routing and Activation ---
        formatted_message: MessageDict = {
            "role": "user", # Treat incoming agent messages like user messages for the recipient
            "content": f"[From @{sender_id}]: {message_content}"
        }
        # Append to the target agent's history
        target_agent.message_history.append(formatted_message)
        logger.debug(f"Appended message from '{sender_id}' to history of '{target_id}'.")

        # Activate target agent only if it's currently idle
        if target_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Target '{target_id}' is IDLE. Activating...");
            # Return the task so the caller can potentially await it
            return asyncio.create_task(self._handle_agent_generator(target_agent, 0)) # Reset retry count
        # Handle case where target is awaiting override - queue message but don't activate
        elif target_agent.status == AGENT_STATUS_AWAITING_USER_OVERRIDE:
             logger.info(f"Target '{target_id}' is {AGENT_STATUS_AWAITING_USER_OVERRIDE}. Message queued, not activating.")
             await self.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued (awaiting user override)." })
             return None
        else: # Target is busy (processing, executing tool, etc.)
            logger.info(f"Target '{target_id}' not IDLE (Status: {target_agent.status}). Message queued in history.")
            await self.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued." })
            return None # No activation task created

    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any], project_name: Optional[str] = None, session_name: Optional[str] = None) -> Optional[Dict]:
        """Executes a single tool call via the ToolExecutor."""
        if not self.tool_executor:
            logger.error("ToolExecutor unavailable. Cannot execute tool.")
            return {"call_id": call_id, "content": "[ToolExec Error: ToolExecutor unavailable]", "_raw_result": None}

        # Set agent status to executing tool
        tool_info = {"name": tool_name, "call_id": call_id}
        agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)

        raw_result: Optional[Any] = None
        result_content: str = "[Tool Execution Error: Unknown]"
        try:
            # *** FIX: Use the passed context variables ***
            logger.debug(f"Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}' with context Project: {project_name}, Session: {session_name}")
            # --- Pass context arguments to executor ---
            raw_result = await self.tool_executor.execute_tool(
                agent.agent_id,
                agent.sandbox_path,
                tool_name,
                tool_args,
                project_name=project_name, # Pass correct context
                session_name=session_name  # Pass correct context
            )
            # *** END FIX ***
            logger.debug(f"Tool '{tool_name}' completed execution.")

            # Handle Result Formatting
            if tool_name == ManageTeamTool.name:
                 # Use message if available, otherwise stringify the dict
                 result_content = raw_result.get("message", str(raw_result)) if isinstance(raw_result, dict) else str(raw_result)
            elif isinstance(raw_result, str):
                 result_content = raw_result
            else: # Attempt to stringify other types
                 try: result_content = json.dumps(raw_result, indent=2)
                 except TypeError: result_content = str(raw_result)

        except Exception as e:
            # Capture the correct error message from the exception
            error_msg = f"Manager error during _execute_single_tool '{tool_name}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            result_content = f"[ToolExec Error: {error_msg}]" # Use the captured error message
            raw_result = None # Ensure raw_result is None on error
        finally:
            if agent.status == AGENT_STATUS_EXECUTING_TOOL and agent.current_tool_info and agent.current_tool_info.get("call_id") == call_id:
                agent.set_status(AGENT_STATUS_PROCESSING)

        return {"call_id": call_id, "content": result_content, "_raw_result": raw_result}


    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        """Creates a standard error dictionary for failed tool dispatch/parsing."""
        error_content = f"[ToolExec Error: Failed dispatch for '{tool_name or 'unknown'}'. Invalid format or arguments.]"
        final_call_id = call_id or f"invalid_call_{int(time.time())}"
        return {"call_id": final_call_id, "content": error_content, "_raw_result": {"status": "error", "message": error_content}}


    # --- UI Updates and Status ---
    async def push_agent_status_update(self, agent_id: str):
        """Sends the current status of a specific agent to the UI."""
        agent = self.agents.get(agent_id)
        if agent:
            state = agent.get_state() # Get base state from agent
            state["team"] = self.state_manager.get_agent_team(agent_id) # Add team info from StateManager
            await self.send_to_ui({
                "type": "agent_status_update",
                "agent_id": agent_id,
                "status": state # Send combined state
            })
        else:
            logger.warning(f"Cannot push status update for unknown agent: {agent_id}")

    async def send_to_ui(self, message_data: Dict[str, Any]):
        """Sends a JSON message to all connected UI clients via the broadcast function."""
        if not self.send_to_ui_func:
            logger.warning("UI broadcast function not configured. Cannot send message.")
            return
        try:
            await self.send_to_ui_func(json.dumps(message_data))
        except TypeError as e:
            logger.error(f"JSON serialization error sending to UI: {e}. Data: {message_data}", exc_info=True)
        except Exception as e:
            logger.error(f"Error sending message to UI: {e}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Synchronously gets a snapshot of all current agent statuses, including team info."""
        statuses = {}
        for agent_id, agent in self.agents.items():
             state = agent.get_state()
             state["team"] = self.state_manager.get_agent_team(agent_id) # Add team info
             statuses[agent_id] = state
        return statuses


    # --- Session Persistence (Delegated) ---
    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Delegates saving the current session state to the SessionManager."""
        logger.info(f"Delegating save_session call for project '{project_name}'...")
        return await self.session_manager.save_session(project_name, session_name)

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Delegates loading a session state to the SessionManager."""
        logger.info(f"Delegating load_session call for project '{project_name}', session '{session_name}'...")
        return await self.session_manager.load_session(project_name, session_name)


    # --- Cleanup ---
    async def cleanup_providers(self):
        """Cleans up resources used by LLM providers (e.g., closes network sessions)."""
        logger.info("Cleaning up LLM providers...");
        # Use a set to find unique provider instances currently in use
        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        logger.info(f"Found {len(active_providers)} unique provider instances to clean up.")
        # Create tasks to close sessions safely
        tasks = [
            asyncio.create_task(self._close_provider_safe(provider))
            for provider in active_providers
            # Check if provider has the async close_session method
            if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session)
        ]
        if tasks:
            await asyncio.gather(*tasks)
            logger.info("LLM Provider cleanup tasks completed.")
        else:
            logger.info("No provider cleanup tasks were necessary.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        """Safely attempts to call the close_session method on a provider instance."""
        try:
            logger.info(f"Attempting to close session for provider: {provider!r}")
            await provider.close_session()
            logger.info(f"Successfully closed session for provider: {provider!r}")
        except Exception as e:
            logger.error(f"Error closing session for provider {provider!r}: {e}", exc_info=True)

    # --- Sync Helper for Listing Agents (Used by ManageTeamTool Handler) ---
    def get_agent_info_list_sync(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Synchronously gets basic info list for agents, optionally filtered by team."""
        info_list = []
        for agent_id, agent in self.agents.items():
             current_team = self.state_manager.get_agent_team(agent_id)
             # Apply filter if provided
             if filter_team_id is not None and current_team != filter_team_id:
                 continue
             # Get basic state and add team info
             state = agent.get_state()
             info = {
                 "agent_id": agent_id,
                 "persona": state.get("persona"),
                 "provider": state.get("provider"),
                 "model": state.get("model"),
                 "status": state.get("status"),
                 "team": current_team
             }
             info_list.append(info)
        return info_list
