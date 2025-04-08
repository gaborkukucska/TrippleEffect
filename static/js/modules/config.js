// static/js/modules/config.js

// Configuration UI Logic (Static Agents)

// --- Module State / Dependencies ---
let configContentElement = null;
let refreshConfigButtonElement = null;
let addAgentButtonElement = null;
let openModalCallback = null; // Function provided by app.js to open the agent modal
let addMessageCallback = null; // Function provided by app.js to add UI messages

/**
 * Initializes the Configuration UI module.
 * @param {function} openModalFunc - Callback function to open modals (from modal.js via app.js).
 * @param {function} addMsgFunc - Callback function to add UI messages (from uiUpdate.js via app.js).
 */
export function initConfigUI(openModalFunc, addMsgFunc) {
    configContentElement = document.getElementById('config-content');
    refreshConfigButtonElement = document.getElementById('refresh-config-button');
    addAgentButtonElement = document.getElementById('add-agent-button');
    openModalCallback = openModalFunc;
    addMessageCallback = addMsgFunc;

    if (!configContentElement || !refreshConfigButtonElement || !addAgentButtonElement) {
        console.error("One or more config section elements not found!");
        return;
    }

    // Setup Listeners
    refreshConfigButtonElement.addEventListener('click', displayAgentConfigurations);
    // The Add button needs to trigger the modal opening logic from the modal module
    addAgentButtonElement.addEventListener('click', () => {
        if (openModalCallback) {
            openModalCallback('agent-modal'); // Open modal for adding (no editId)
        } else {
            console.error("openModalCallback not provided to config module.");
        }
    });

    console.log("Config UI module initialized.");
    // Initial fetch and display
    displayAgentConfigurations();
}

/**
 * Fetches agent configurations from the API and displays them.
 */
export async function displayAgentConfigurations() {
    if (!configContentElement) {
        console.warn("Config content area not found. Cannot display configurations.");
        return;
    }
    configContentElement.innerHTML = '<span class="status-placeholder">Loading...</span>';

    try {
        const response = await fetch('/api/config/agents'); // Fetch static config list
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status} ${response.statusText}`);
        }
        const agents = await response.json();

        configContentElement.innerHTML = ''; // Clear previous/loading content
        if (!Array.isArray(agents)) {
             throw new Error("Invalid response format: Expected an array of agents.");
        }

        if (agents.length === 0) {
            configContentElement.innerHTML = '<span class="status-placeholder">No static agents configured.</span>';
            return;
        }

        // Sort agents alphabetically by ID for consistent display
        agents.sort((a, b) => (a.agent_id || "").localeCompare(b.agent_id || ""));

        agents.forEach(agent => {
            if (!agent || !agent.agent_id) {
                console.warn("Skipping invalid agent data:", agent);
                return; // Skip malformed entries
            }
            const item = document.createElement('div');
            item.classList.add('config-item');
            // Use textContent for safety, construct HTML carefully
            const detailsText = `- ${agent.provider || 'N/A'} / ${agent.model || 'N/A'}`;
            item.innerHTML = `
                <span>
                    <strong></strong> (${agent.agent_id})
                    <span class="agent-details"></span>
                </span>
                <div class="config-item-actions">
                    <button class="config-action-button edit-button" data-id="${agent.agent_id}" title="Edit">✏️</button>
                    <button class="config-action-button delete-button" data-id="${agent.agent_id}" title="Delete">🗑️</button>
                </div>`;
            // Set text content safely
            item.querySelector('strong').textContent = agent.persona || agent.agent_id;
            item.querySelector('.agent-details').textContent = detailsText;

            configContentElement.appendChild(item);

            // Add listeners only if buttons exist
            item.querySelector('.edit-button')?.addEventListener('click', () => {
                 if (openModalCallback) {
                     openModalCallback('agent-modal', agent.agent_id); // Open modal for editing
                 } else {
                     console.error("openModalCallback not provided for edit button.");
                 }
            });
            item.querySelector('.delete-button')?.addEventListener('click', () => handleDeleteAgent(agent.agent_id));
        });
    } catch (error) {
        console.error('Error fetching/displaying agent configurations:', error);
        if(configContentElement) configContentElement.innerHTML = '<span class="status-placeholder error">Error loading configuration.</span>';
        if(addMessageCallback) addMessageCallback('system-log-area', `[UI Error] Failed to display config: ${error.message}`, 'error');
    }
}

/**
 * Handles the deletion of a static agent configuration.
 * @param {string} agentId - The ID of the agent configuration to delete.
 */
async function handleDeleteAgent(agentId) {
    if (!confirm(`Delete static agent config '${agentId}'? Requires application restart.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' });
        const result = await response.json(); // Assuming backend sends JSON response

        if (response.ok && result.success) {
            alert(result.message || 'Agent config deleted successfully. Restart required.');
            displayAgentConfigurations(); // Refresh the list in the UI
        } else {
            // Throw error with details from backend if available
            throw new Error(result.detail || result.message || `Failed to delete agent config (HTTP ${response.status})`);
        }
    } catch (error) {
        console.error('Error deleting agent configuration:', error);
        alert(`Error deleting agent '${agentId}': ${error.message}`);
        if (addMessageCallback) addMessageCallback('system-log-area', `[UI Error] Failed to delete agent config ${agentId}: ${error.message}`, 'error');
    }
}
