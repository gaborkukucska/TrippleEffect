// static/js/modules/modal.js

// Modal Handling Logic

// --- Module State / Dependencies (will be set by main app.js) ---
let agentModalElement = null;
let overrideModalElement = null;
let agentFormElement = null;
let overrideFormElement = null;
let ws = null; // WebSocket instance
let displayAgentConfigurationsCallback = null; // Callback to refresh config list
let addMessageCallback = null; // Callback to add messages to UI

/**
 * Initializes the modal module with necessary DOM elements and callbacks.
 * @param {WebSocket} websocketInstance - The active WebSocket instance.
 * @param {function} displayConfigCallback - Function to refresh the config display.
 * @param {function} addMsgCallback - Function to add messages to the UI.
 */
export function initModals(websocketInstance, displayConfigCallback, addMsgCallback) {
    agentModalElement = document.getElementById('agent-modal');
    overrideModalElement = document.getElementById('override-modal');
    agentFormElement = document.getElementById('agent-form');
    overrideFormElement = document.getElementById('override-form');
    ws = websocketInstance;
    displayAgentConfigurationsCallback = displayConfigCallback;
    addMessageCallback = addMsgCallback;

    if (!agentModalElement || !overrideModalElement || !agentFormElement || !overrideFormElement) {
        console.error("One or more modal elements not found!");
    } else {
         console.log("Modal module initialized.");
         setupModalListeners(); // Setup internal listeners
    }
}

/**
 * Sets up event listeners for modal forms and close buttons.
 */
function setupModalListeners() {
    // Form submission listeners
    agentFormElement?.addEventListener('submit', handleSaveAgent);
    overrideFormElement?.addEventListener('submit', handleSubmitOverride);

    // Close button listeners
    agentModalElement?.querySelector('.close-button')?.addEventListener('click', () => closeModal('agent-modal'));
    overrideModalElement?.querySelector('.close-button')?.addEventListener('click', () => closeModal('override-modal'));

    // Optional: Close on clicking background (handled globally in app.js for simplicity)
    // agentModalElement?.addEventListener('click', (e) => { if (e.target === agentModalElement) closeModal('agent-modal'); });
    // overrideModalElement?.addEventListener('click', (e) => { if (e.target === overrideModalElement) closeModal('override-modal'); });
}

/**
 * Opens a specific modal dialog. Handles pre-filling for agent edit.
 * @param {string} modalId - The ID of the modal to open ('agent-modal' or 'override-modal').
 * @param {string|null} [editId=null] - The agent ID if editing (for agent-modal).
 */
export async function openModal(modalId, editId = null) {
    const modal = document.getElementById(modalId);
    if (!modal) {
        console.error(`Modal with ID ${modalId} not found.`);
        return;
    }

    // --- Agent Add/Edit Modal Specific Logic ---
    if (modalId === 'agent-modal' && agentFormElement) {
        const titleEl = modal.querySelector('#modal-title');
        const agentIdInput = agentFormElement.querySelector('#agent-id');
        const editAgentIdInput = agentFormElement.querySelector('#edit-agent-id');
        if (!titleEl || !agentIdInput || !editAgentIdInput) {
            console.error("Agent modal internal elements missing.");
            return;
        }

        agentFormElement.reset(); // Clear previous values
        editAgentIdInput.value = ''; // Clear hidden edit ID
        agentIdInput.disabled = false; // Enable ID input by default

        if (editId) {
            // --- Editing Existing Agent ---
            titleEl.textContent = `Edit Agent: ${editId}`;
            editAgentIdInput.value = editId; // Store ID for submission logic
            agentIdInput.value = editId; // Display ID
            agentIdInput.disabled = true; // Prevent editing ID

            // Fetch existing config to pre-fill
            try {
                // Ideally, fetch specific agent details: GET /api/config/agents/{editId}
                // Workaround: Fetch list and find agent
                console.log(`Fetching config list to find details for agent: ${editId}`);
                const response = await fetch('/api/config/agents'); // Assumes this returns enough info
                if (!response.ok) throw new Error(`Failed to fetch agent list (HTTP ${response.status})`);
                const agents = await response.json();
                const agentData = agents.find(a => a.agent_id === editId);

                if (!agentData) throw new Error(`Agent config for ${editId} not found in list.`);

                // Pre-fill form based on available data (limited by list endpoint)
                agentFormElement.querySelector('#persona').value = agentData.persona || editId;
                agentFormElement.querySelector('#provider').value = agentData.provider || 'openrouter';
                agentFormElement.querySelector('#model').value = agentData.model || '';
                // Cannot reliably prefill temp/prompt without a dedicated GET endpoint for full config
                console.warn("Edit modal prefilled with limited data.");
                // Example if full config was available via agentData.config:
                // agentFormElement.querySelector('#temperature').value = agentData.config?.temperature || 0.7;
                // agentFormElement.querySelector('#system_prompt').value = agentData.config?.system_prompt || '';

            } catch (error) {
                console.error("Error fetching agent data for edit:", error);
                if (addMessageCallback) addMessageCallback('system-log-area', `[UI Error] Failed to load data for editing agent ${editId}: ${error.message}`, 'error');
                alert(`Error loading agent data: ${error.message}`);
                return; // Don't open modal if data fetch fails
            }
        } else {
            // --- Adding New Agent ---
            titleEl.textContent = 'Add New Static Agent';
            // Set default values
            agentFormElement.querySelector('#temperature').value = 0.7;
            agentFormElement.querySelector('#provider').value = 'openrouter'; // Or fetch default from settings if possible
        }
    }

    // --- Show the Modal ---
    modal.style.display = 'block';
}

/**
 * Closes a specific modal dialog.
 * @param {string} modalId - The ID of the modal to close.
 */
export function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
        // Reset forms inside modal when closed for next time
        const form = modal.querySelector('form');
        if (form) form.reset();
        // Reset specific fields like hidden edit ID
        if (modalId === 'agent-modal') {
             const editInput = document.getElementById('edit-agent-id');
             const idInput = document.getElementById('agent-id');
             if(editInput) editInput.value = '';
             if(idInput) idInput.disabled = false;
        }
    }
}

/**
 * Handles the submission of the Agent Add/Edit form.
 * @param {Event} event - The form submission event.
 */
async function handleSaveAgent(event) {
    event.preventDefault();
    const form = event.target; if (!form) return;
    const agentIdInput = form.querySelector('#agent-id');
    const editAgentIdInput = form.querySelector('#edit-agent-id');
    if (!agentIdInput || !editAgentIdInput) return;

    const agentId = agentIdInput.value.trim();
    const editAgentId = editAgentIdInput.value;
    const isEditing = !!editAgentId;

    // Validation
    if (!agentId || !/^[a-zA-Z0-9_-]+$/.test(agentId)) {
        alert("Valid Agent ID required (alphanumeric, _, -)."); return;
    }
    const agentConfig = {
        provider: form.querySelector('#provider')?.value,
        model: form.querySelector('#model')?.value.trim() ?? '',
        persona: form.querySelector('#persona')?.value.trim() || agentId,
        temperature: parseFloat(form.querySelector('#temperature')?.value) || 0.7,
        system_prompt: form.querySelector('#system_prompt')?.value.trim() || 'You are a helpful assistant.',
    };
    if (!agentConfig.provider || !agentConfig.model) {
        alert("Provider and Model are required."); return;
    }

    // API Call
    const url = isEditing ? `/api/config/agents/${editAgentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';
    const payload = isEditing ? agentConfig : { agent_id: agentId, config: agentConfig };
    console.log(`Sending ${method} to ${url}`);

    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (response.ok && result.success) {
            alert(result.message || `Agent ${isEditing ? 'updated' : 'added'}. Restart required.`);
            closeModal('agent-modal');
            if (displayAgentConfigurationsCallback) displayAgentConfigurationsCallback(); // Refresh list
        } else {
            throw new Error(result.detail || result.message || `Failed to ${isEditing ? 'update' : 'add'} agent.`);
        }
    } catch (error) {
        console.error(`Error saving agent:`, error);
        alert(`Error: ${error.message}`);
        if (addMessageCallback) addMessageCallback('system-log-area', `[UI Err] Save agent failed: ${error.message}`, 'error');
    }
}

/**
 * Displays the Agent Override modal with data from the backend.
 * @param {object} data - Data object containing agent_id, persona, message, etc.
 */
export function showOverrideModal(data) {
    if (!overrideModalElement || !overrideFormElement) return;
    const agentId = data.agent_id;
    const persona = data.persona || agentId;

    // Set hidden agent ID
    const agentIdInput = overrideFormElement.querySelector('#override-agent-id');
    if (agentIdInput) agentIdInput.value = agentId;

    // Set display text
    const titleEl = overrideModalElement.querySelector('#override-modal-title');
    const messageEl = overrideModalElement.querySelector('#override-message');
    const errorEl = overrideModalElement.querySelector('#override-last-error');
    if (titleEl) titleEl.textContent = `Override for Agent: ${persona}`;
    if (messageEl) messageEl.textContent = data.message || `Agent '${persona}' (${agentId}) failed. Provide alternative.`;
    if (errorEl) errorEl.textContent = data.last_error || "Unknown error";

    // Pre-fill form if possible
    const providerSelect = overrideFormElement.querySelector('#override-provider');
    const modelInput = overrideFormElement.querySelector('#override-model');
    if (providerSelect && data.current_provider) providerSelect.value = data.current_provider;
    if (modelInput) modelInput.value = data.current_model || '';

    openModal('override-modal'); // Use the exported openModal function
}

/**
 * Handles the submission of the Agent Override form.
 * @param {Event} event - The form submission event.
 */
function handleSubmitOverride(event) {
    event.preventDefault();
    if (!overrideFormElement) return;

    const agentId = overrideFormElement.querySelector('#override-agent-id')?.value;
    const newProvider = overrideFormElement.querySelector('#override-provider')?.value;
    const newModel = overrideFormElement.querySelector('#override-model')?.value.trim();

    if (!agentId || !newProvider || !newModel) {
        alert("Please fill all override fields.");
        return;
    }
    const overrideData = {
        type: "submit_user_override",
        agent_id: agentId,
        new_provider: newProvider,
        new_model: newModel
    };

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(overrideData));
        if (addMessageCallback) addMessageCallback('system-log-area', `[UI] Submitted override for ${agentId} (Prov: ${newProvider}, Model: ${nM}).`, 'status');
        closeModal('override-modal');
    } else {
        alert("WebSocket not connected. Cannot submit override.");
        if (addMessageCallback) addMessageCallback('system-log-area', `[UI Err] Override failed for ${agentId}: WS not connected.`, 'error');
    }
}

// Global click listener to close modals (can be kept here or moved to app.js)
// window.addEventListener('click', function(event) {
//     if (event.target.classList.contains('modal')) {
//         closeModal(event.target.id);
//     }
// });
