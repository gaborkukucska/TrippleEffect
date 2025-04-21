// START OF FILE static/js/configView.js

import * as api from './api.js';
import * as ui from './ui.js';
import * as DOM from './domElements.js';
import { escapeHTML } from './utils.js';

/**
 * Fetches the static agent configurations from the API and renders them.
 */
export const loadStaticAgentConfig = async () => {
    if (!DOM.configContent) {
        console.warn("ConfigView: Config content area not found.");
        return;
    }
    console.log("ConfigView: Loading static agent configurations...");
    DOM.configContent.innerHTML = '<span class="status-placeholder">Loading config...</span>';
    try {
        // Assuming the API returns the list of AgentInfo objects
        const agentConfigs = await api.makeApiCall('/api/config/agents');
        renderStaticAgentConfig(agentConfigs);
        console.log("ConfigView: Static agent configurations loaded and rendered.");
    } catch (error) {
        console.error("ConfigView: Error loading static agent config:", error);
        DOM.configContent.innerHTML = '<span class="status-placeholder">Error loading config.</span>';
        // Further error display is handled by makeApiCall in ui.js
    }
};

/**
 * Renders the list of static agent configurations in the UI.
 * @param {Array<object>} agentConfigs - Array of agent configuration info objects.
 */
const renderStaticAgentConfig = (agentConfigs) => {
    if (!DOM.configContent) return;
    DOM.configContent.innerHTML = ''; // Clear previous content

    if (!agentConfigs || agentConfigs.length === 0) {
        DOM.configContent.innerHTML = '<span class="status-placeholder">No static agent configurations found.</span>';
        return;
    }

    agentConfigs.forEach(agent => {
        const item = document.createElement('div');
        item.classList.add('config-item');
        item.innerHTML = `
            <span>
                <strong>${escapeHTML(agent.agent_id)}</strong>
                <small class="agent-details">(${escapeHTML(agent.provider)} / ${escapeHTML(agent.model)}) - ${escapeHTML(agent.persona)}</small>
            </span>
            <span class="config-item-actions">
                <!-- Edit button disabled until backend provides full config details -->
                <button class="config-action-button edit-button" data-agent-id="${escapeHTML(agent.agent_id)}" title="Edit Agent (Requires Backend Update)" disabled>✏️</button>
                <button class="config-action-button delete-button" data-agent-id="${escapeHTML(agent.agent_id)}" title="Delete Static Agent Config">🗑️</button>
            </span>
        `;
        DOM.configContent.appendChild(item);
    });

     // Add event listeners for edit/delete buttons after rendering
     DOM.configContent.querySelectorAll('.edit-button:not([disabled])').forEach(button => {
         button.addEventListener('click', handleEditAgentConfigClick);
     });
     DOM.configContent.querySelectorAll('.delete-button').forEach(button => {
         button.addEventListener('click', handleDeleteAgentConfigClick);
     });
};

/**
 * Handles the click event for the Edit button on a static agent config item.
 * (Currently alerts user about limitations).
 * @param {Event} event - The click event.
 */
const handleEditAgentConfigClick = async (event) => {
    const agentId = event.currentTarget.getAttribute('data-agent-id');
    console.log(`ConfigView: Edit button clicked for static agent: ${agentId}`);
    // Placeholder: Needs backend endpoint to fetch full details for editing.
    alert(`Editing static agent '${agentId}' requires a backend update to fetch full configuration details. This feature is not yet fully implemented.`);
    // --- Example future implementation ---
    // try {
    //     // Assume an endpoint like '/api/config/agents/{agentId}/details' exists
    //     const fullAgentData = await api.makeApiCall(`/api/config/agents/${agentId}/details`);
    //     if (fullAgentData && fullAgentData.config) {
    //          openAgentModal(agentId, fullAgentData.config); // Pass the inner config object
    //     } else {
    //          throw new Error("Invalid data received from backend for agent details.");
    //     }
    // } catch (error) {
    //     console.error(`ConfigView: Error fetching full details for agent ${agentId}:`, error);
    //     alert(`Failed to fetch full details for editing agent ${agentId}. ${error.message}`);
    // }
    // --- End Example ---
};

/**
 * Handles the click event for the Delete button on a static agent config item.
 * @param {Event} event - The click event.
 */
const handleDeleteAgentConfigClick = async (event) => {
    const agentId = event.currentTarget.getAttribute('data-agent-id');
    console.log(`ConfigView: Delete button clicked for static agent: ${agentId}`);
    if (!confirm(`Are you sure you want to delete the static configuration for agent '${agentId}'? This requires an application restart.`)) {
        return;
    }
    try {
        const result = await api.makeApiCall(`/api/config/agents/${agentId}`, 'DELETE');
        console.log(`ConfigView: Static agent delete successful.`, result);
        // Display feedback in the internal comms view
        ui.displayMessage(escapeHTML(result.message), 'system_event', 'internal-comms-area', 'system');
        loadStaticAgentConfig(); // Refresh the list in the config view
    } catch (error) {
        console.error(`ConfigView: Error deleting static agent config for ${agentId}:`, error);
        // Error message should have been displayed by makeApiCall in the internal comms view
        alert(`Failed to delete static agent config: ${error.responseBody?.detail || error.message}`);
    }
};


/**
 * Opens the agent modal, optionally populating it for editing.
 * @param {string | null} agentIdToEdit - The ID of the agent to edit, or null for adding.
 * @param {object | null} agentData - The configuration data for the agent being edited.
 */
export const openAgentModal = (agentIdToEdit = null, agentData = null) => {
     // Ensure DOM elements are available
    if (!DOM.agentModal || !DOM.agentForm || !DOM.modalTitle || !DOM.editAgentIdInput) {
        console.error("ConfigView Error: Agent modal elements not found.");
        return;
    }
    DOM.agentForm.reset(); // Clear form
    DOM.editAgentIdInput.value = agentIdToEdit || ''; // Set hidden field if editing

    const agentIdField = document.getElementById('agent-id'); // Get field inside function

    if (agentIdToEdit && agentData) {
        DOM.modalTitle.textContent = `Edit Agent: ${agentIdToEdit}`;
        if (agentIdField) {
            agentIdField.value = agentIdToEdit;
            agentIdField.readOnly = true; // Prevent editing ID
        }
        // Safely access potentially missing fields
        document.getElementById('persona').value = agentData.persona || '';
        document.getElementById('provider').value = agentData.provider || 'openrouter';
        document.getElementById('model').value = agentData.model || '';
        // Use nullish coalescing for default temperature
        document.getElementById('temperature').value = agentData.temperature ?? 0.7;
        document.getElementById('system_prompt').value = agentData.system_prompt || '';
        // TODO: Handle loading/displaying extra kwargs if implemented
    } else {
        DOM.modalTitle.textContent = 'Add New Static Agent';
        if (agentIdField) agentIdField.readOnly = false;
        document.getElementById('temperature').value = 0.7; // Default for add
        // Clear other fields explicitly for 'Add' mode
        document.getElementById('persona').value = '';
        document.getElementById('provider').value = 'openrouter'; // Default provider
        document.getElementById('model').value = '';
        document.getElementById('system_prompt').value = '';
    }

    ui.openModal('agent-modal'); // Use UI module function
};


console.log("Frontend configView module loaded.");
