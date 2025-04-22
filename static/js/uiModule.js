// START OF FILE static/js/uiModule.js

/**
 * @module uiModule
 * @description Manages all direct DOM manipulation and UI updates for the application.
 */

import { apiClient } from './apiClient.js'; // Import apiClient for refreshing lists
import { clearAttachedFile } from './fileManager.js'; // Import for clearing file on modal close

// --- DOM Element References ---
const conversationArea = document.getElementById('conversation-area');
const internalCommsArea = document.getElementById('internal-comms-area'); // New reference
const messageInput = document.getElementById('message-input');
const agentStatusContent = document.getElementById('agent-status-content');
const fileInfoArea = document.getElementById('file-info-area');
const viewPanels = document.querySelectorAll('.view-panel');
const navButtons = document.querySelectorAll('.nav-button');
const agentModal = document.getElementById('agent-modal');
const agentForm = document.getElementById('agent-form');
const modalTitle = document.getElementById('modal-title');
const editAgentIdInput = document.getElementById('edit-agent-id');
const agentIdInput = document.getElementById('agent-id'); // For validation styling maybe
const configContent = document.getElementById('config-content');
const projectSelect = document.getElementById('project-select');
const sessionSelect = document.getElementById('session-select');
const loadSessionButton = document.getElementById('load-session-button');
const saveSessionButton = document.getElementById('save-session-button');
const sessionStatusMessage = document.getElementById('session-status-message');
const saveProjectNameInput = document.getElementById('save-project-name');
const saveSessionNameInput = document.getElementById('save-session-name');

// Helper to store the last message element per agent for chunk appending
const lastMessageElements = {};

/**
 * Clears placeholders from a container if they exist.
 * @param {HTMLElement} container - The container element.
 */
function clearPlaceholders(container) {
    if (!container) return;
    const placeholder = container.querySelector('.initial-placeholder');
    if (placeholder) {
        placeholder.remove();
    }
}

/**
 * Creates a message element (for conversation or internal logs).
 * @param {string} agentId - The ID of the agent or 'user'/'system'.
 * @param {string} content - The message content.
 * @param {string} type - The message type (e.g., 'user', 'agent_response', 'status', 'error').
 * @param {string} [logClass] - Optional CSS class for log entry types.
 * @param {boolean} [useCodeBlock=false] - Wrap content in <pre><code> for formatting.
 * @param {string} [timestamp] - Optional ISO timestamp string.
 * @returns {HTMLElement} The created message element.
 */
function createMessageElement(agentId, content, type, logClass = '', useCodeBlock = false, timestamp = null) {
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type); // e.g., message agent_response, message user, message status
    if (logClass) {
        messageDiv.classList.add(logClass); // e.g., log-tool-use
    }
    if (agentId) {
        messageDiv.dataset.agentId = agentId; // Add data attribute for potential styling
    }

    const timestampSpan = document.createElement('span');
    timestampSpan.classList.add('timestamp');
    // Format timestamp nicely if available
    timestampSpan.textContent = timestamp
        ? new Date(timestamp).toLocaleTimeString() + ' ' // Add space
        : new Date().toLocaleTimeString() + ' '; // Default to now, add space
    messageDiv.appendChild(timestampSpan);

    // Add agent label if it's an agent message (not user, status, error)
    if (type === 'agent_response' || (logClass && logClass !== 'status' && logClass !== 'error')) {
        const agentLabel = document.createElement('span');
        agentLabel.classList.add('agent-label');
        agentLabel.textContent = `@${agentId || 'Unknown'}:`;
        messageDiv.appendChild(agentLabel);
        // messageDiv.style.flexDirection = 'column'; // Stack label and content for agent messages
    }


    const contentSpan = document.createElement('span');
    contentSpan.classList.add('message-content');

    if (useCodeBlock) {
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = content;
        pre.appendChild(code);
        contentSpan.appendChild(pre);
    } else {
        contentSpan.textContent = content;
    }

    messageDiv.appendChild(contentSpan);

    return messageDiv;
}


/**
 * Scrolls a container element to the bottom.
 * @param {HTMLElement} element - The DOM element to scroll.
 */
function scrollToBottom(element) {
    if (element) {
        element.scrollTop = element.scrollHeight;
    }
}

// --- Functions for Conversation Area ---

/**
 * Adds a complete message (user or admin_ai) to the main conversation area.
 * @param {string} agentId - 'user' or 'admin_ai'.
 * @param {string} text - The message text.
 * @param {string} type - Message type ('user' or 'agent_response').
 */
function addMessageToConversation(agentId, text, type = 'agent_response') {
    clearPlaceholders(conversationArea); // Clear placeholder on first message
    const messageClass = agentId === 'user' ? 'user' : type; // Use 'user' class for user messages
    const messageElement = createMessageElement(agentId, text, messageClass);

    // Reset last message element tracking for this agent if it's a full message
    if (agentId) {
        lastMessageElements[agentId] = null;
    }

    conversationArea.appendChild(messageElement);
    scrollToBottom(conversationArea);
}

/**
 * Appends a streaming chunk to the last message of a specific agent in the conversation area.
 * @param {string} agentId - The ID of the agent (expected to be 'admin_ai').
 * @param {string} chunk - The text chunk to append.
 */
function appendAgentResponseChunk(agentId, chunk) {
    if (!agentId) return; // Should have agentId

    // Find or create the last message element for this agent
    let agentLastMessage = lastMessageElements[agentId];
    if (!agentLastMessage || !conversationArea.contains(agentLastMessage)) {
        clearPlaceholders(conversationArea); // Clear placeholder if this is the first chunk
        // Create a new message container if none exists or the last one is gone
        agentLastMessage = createMessageElement(agentId, '', 'agent_response');
        conversationArea.appendChild(agentLastMessage);
        lastMessageElements[agentId] = agentLastMessage;
    }

    // Append the chunk to the content span within the message element
    const contentSpan = agentLastMessage.querySelector('.message-content');
    if (contentSpan) {
        contentSpan.textContent += chunk;
        scrollToBottom(conversationArea); // Scroll as content is added
    } else {
        console.error("Could not find content span in agent message element:", agentLastMessage);
    }
}


// --- Functions for Internal Comms Area ---

/**
 * Adds a log entry to the internal communications area.
 * @param {object} data - The data object containing message details (type, content, agent_id, etc.).
 * @param {string} logClass - CSS class for styling (e.g., 'status', 'error', 'log-tool-use').
 * @param {boolean} [useCodeBlock=false] - Wrap content in <pre><code>.
 */
function addLogEntry(data, logClass, useCodeBlock = false) {
    clearPlaceholders(internalCommsArea); // Clear placeholder on first entry

    // Extract relevant info, provide defaults
    const agentId = data.agent_id || 'System';
    let content = data.content || data.message || JSON.stringify(data); // Fallback content
    const type = data.type || 'log'; // Fallback type
    const timestamp = data.timestamp; // Use provided timestamp if available

    // Specific formatting for tool interactions
    if (type === 'tool_requests' && data.calls) {
        content = `[Tool Request by @${agentId}]:\n`;
        data.calls.forEach(call => {
            content += `- ID: ${call.id}\n  Tool: ${call.name}\n  Args: ${JSON.stringify(call.arguments)}\n`;
        });
        useCodeBlock = true; // Use code block for tool requests/results
    } else if (type === 'tool_results' && data.results) {
        content = `[Tool Result for @${agentId}]:\n`;
         data.results.forEach(result => {
             content += `- Call ID: ${result.call_id}\n  Content: ${result.content}\n`;
         });
        useCodeBlock = true;
    }

    const logElement = createMessageElement(agentId, content, type, logClass, useCodeBlock, timestamp);
    internalCommsArea.appendChild(logElement);
    scrollToBottom(internalCommsArea);
}

/**
 * Updates the initial "Connecting..." status in the internal comms view.
 * @param {boolean} isConnected - Whether the WebSocket is connected.
 * @param {string} [message] - Optional message to display (e.g., "Connecting...", "Disconnected").
 */
function updateInitialConnectionStatus(isConnected, message = "Connected") {
    const connectingPlaceholder = internalCommsArea?.querySelector('.initial-connecting');
    if (connectingPlaceholder) {
        if (isConnected) {
            connectingPlaceholder.textContent = message; // Show "Connected"
            connectingPlaceholder.style.color = 'var(--accent-color-green)';
            // Optionally remove it after a short delay
            setTimeout(() => connectingPlaceholder.remove(), 2000);
        } else {
            connectingPlaceholder.textContent = message; // Show "Connecting..." or "Disconnected..."
             connectingPlaceholder.style.color = 'var(--accent-color-orange)';
        }
    } else if (!isConnected && message) {
        // If placeholder was removed but we disconnect, add an error message
        addLogEntry({ content: message }, 'error');
    }
}


// --- Functions for Agent Status ---

/**
 * Updates the entire agent status list in the UI.
 * @param {object} agentStatuses - An object where keys are agent IDs and values are status objects.
 */
function updateAgentStatusList(agentStatuses) {
    if (!agentStatusContent) return;
    agentStatusContent.innerHTML = ''; // Clear existing statuses

    const agentIds = Object.keys(agentStatuses);

    if (agentIds.length === 0) {
        agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>';
        return;
    }

    // Sort agents, maybe admin first, then by ID?
    agentIds.sort((a, b) => {
        if (a === 'admin_ai') return -1;
        if (b === 'admin_ai') return 1;
        return a.localeCompare(b);
    });

    agentIds.forEach(agentId => {
        const agentInfo = agentStatuses[agentId];
        if (!agentInfo) return; // Skip if info is missing

        const statusItem = document.createElement('div');
        statusItem.classList.add('agent-status-item');
        statusItem.classList.add(`status-${agentInfo.status || 'unknown'}`); // Add status class

        // Combine Name, Model, Team
        const nameModelTeamSpan = document.createElement('span');
        nameModelTeamSpan.innerHTML = `
            <strong>${agentInfo.persona || agentId}</strong>
            <span class="agent-model">(${agentInfo.model || 'N/A'})</span>
            ${agentInfo.team ? `<span class="agent-team">[Team: ${agentInfo.team}]</span>` : ''}
        `;

        // Status Badge
        const statusBadge = document.createElement('span');
        statusBadge.classList.add('agent-status');
        statusBadge.textContent = agentInfo.status || 'unknown';

        // Append elements
        statusItem.appendChild(nameModelTeamSpan);
        statusItem.appendChild(statusBadge);

        agentStatusContent.appendChild(statusItem);
    });
}


// --- Functions for View Switching ---

/**
 * Switches the active view panel.
 * @param {string} viewId - The ID of the view panel to activate.
 */
function switchView(viewId) {
    viewPanels.forEach(panel => {
        panel.classList.remove('active');
    });
    navButtons.forEach(button => {
        button.classList.remove('active');
    });

    const targetPanel = document.getElementById(viewId);
    const targetButton = document.querySelector(`.nav-button[data-view="${viewId}"]`);

    if (targetPanel) {
        targetPanel.classList.add('active');
        // Special actions when switching views
        if (viewId === 'session-view') {
            populateProjectDropdown(); // Refresh projects when switching to session view
        } else if (viewId === 'config-view') {
            refreshConfigList(); // Refresh config when switching to config view
        }
    } else {
        console.warn(`Switch View: Panel with ID ${viewId} not found.`);
        // Default to chat view if target not found
        document.getElementById('chat-view')?.classList.add('active');
        document.querySelector('.nav-button[data-view="chat-view"]')?.classList.add('active');
    }

    if (targetButton) {
        targetButton.classList.add('active');
    }
}


// --- Functions for Modals ---

/**
 * Opens the agent creation/editing modal.
 * @param {object} [agentData=null] - Optional agent data for editing.
 */
function openAgentModal(agentData = null) {
    agentForm.reset(); // Clear previous form data
    editAgentIdInput.value = ''; // Clear hidden editing ID
    agentIdInput.disabled = false; // Enable ID input for adding

    if (agentData && agentData.agent_id) {
        // Populate form for editing
        modalTitle.textContent = 'Edit Agent Config';
        editAgentIdInput.value = agentData.agent_id; // Set hidden field
        document.getElementById('agent-id').value = agentData.agent_id;
        agentIdInput.disabled = true; // Disable ID editing
        document.getElementById('persona').value = agentData.config?.persona || '';
        document.getElementById('provider').value = agentData.config?.provider || 'openrouter';
        document.getElementById('model').value = agentData.config?.model || '';
        document.getElementById('temperature').value = agentData.config?.temperature ?? 0.7;
        document.getElementById('system_prompt').value = agentData.config?.system_prompt || '';
    } else {
        // Setup for adding new agent
        modalTitle.textContent = 'Add New Static Agent';
    }

    agentModal.style.display = 'block';
}

/**
 * Closes a specified modal.
 * @param {string} modalId - The ID of the modal to close.
 */
function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
    // Clear attached file if agent modal is closed
    if (modalId === 'agent-modal') {
        clearAttachedFile();
        updateFileInfoArea(null);
    }
}

// --- Functions for Config View ---

/** Refreshes the static configuration list in the UI. */
async function refreshConfigList() {
    if (!configContent) return;
    configContent.innerHTML = '<span class="status-placeholder">Refreshing...</span>'; // Show loading state
    const configs = await apiClient.getAgentConfigs(); // Fetch fresh data
    configContent.innerHTML = ''; // Clear loading/previous state

    if (!configs || configs.length === 0) {
        configContent.innerHTML = '<span class="status-placeholder">No static agents configured.</span>';
        return;
    }

    configs.forEach(agent => {
        const itemDiv = document.createElement('div');
        itemDiv.classList.add('config-item');
        itemDiv.innerHTML = `
            <span>
                <strong>${agent.agent_id}</strong>
                <span class="agent-details">(${agent.persona || 'N/A'} | ${agent.provider}/${agent.model})</span>
            </span>
            <span class="config-item-actions">
                <button class="config-action-button edit-button" data-agent-id="${agent.agent_id}" title="Edit">✏️</button>
                <button class="config-action-button delete-button" data-agent-id="${agent.agent_id}" title="Delete">🗑️</button>
            </span>
        `;
        configContent.appendChild(itemDiv);

        // Add event listeners for edit/delete buttons
        itemDiv.querySelector('.edit-button').addEventListener('click', async (e) => {
            const agentIdToEdit = e.currentTarget.getAttribute('data-agent-id');
            // Need to fetch full config for editing, as list view is simplified
            const allConfigs = await apiClient.getAgentConfigs(true); // Assume this fetches full config
            const agentToEdit = allConfigs.find(c => c.agent_id === agentIdToEdit);
            if (agentToEdit) {
                openAgentModal(agentToEdit);
            } else {
                alert(`Could not find full config for agent ${agentIdToEdit}. Please refresh.`);
            }
        });
        itemDiv.querySelector('.delete-button').addEventListener('click', async (e) => {
             const agentIdToDelete = e.currentTarget.getAttribute('data-agent-id');
             if (confirm(`Are you sure you want to delete the static configuration for agent '${agentIdToDelete}'? Restart required.`)) {
                const success = await apiClient.deleteAgentConfig(agentIdToDelete);
                if (success) {
                    await refreshConfigList(); // Refresh list after deleting
                } else {
                     alert(`Failed to delete agent configuration for ${agentIdToDelete}. Check console.`);
                }
            }
        });
    });
}


// --- Functions for Session Management View ---

/** Populates the project selection dropdown. */
async function populateProjectDropdown() {
    if (!projectSelect) return;
    projectSelect.disabled = true;
    projectSelect.innerHTML = '<option value="">Loading Projects...</option>'; // Loading state
    try {
        const projects = await apiClient.listProjects();
        projectSelect.innerHTML = '<option value="">-- Select Project --</option>'; // Reset
        if (projects && projects.length > 0) {
            projects.forEach(project => {
                const option = document.createElement('option');
                option.value = project.project_name;
                option.textContent = project.project_name;
                projectSelect.appendChild(option);
            });
        } else {
             projectSelect.innerHTML = '<option value="">-- No Projects Found --</option>';
        }
    } catch (error) {
        console.error("Failed to fetch projects:", error);
        projectSelect.innerHTML = '<option value="">-- Error Loading Projects --</option>';
        displaySessionStatus("Error loading projects.", false);
    } finally {
        projectSelect.disabled = false;
    }
}

/**
 * Populates the session selection dropdown for a given project.
 * @param {string} projectName - The name of the project.
 */
async function populateSessionDropdown(projectName) {
    if (!sessionSelect || !projectName) return;
    sessionSelect.disabled = true;
    sessionSelect.innerHTML = '<option value="">Loading Sessions...</option>'; // Loading state
    loadSessionButton.disabled = true;
    try {
        const sessions = await apiClient.listSessions(projectName);
        sessionSelect.innerHTML = '<option value="">-- Select Session --</option>'; // Reset
        if (sessions && sessions.length > 0) {
             // Sort sessions, maybe newest first if names are timestamps? Basic sort for now.
             sessions.sort((a, b) => b.session_name.localeCompare(a.session_name));
            sessions.forEach(session => {
                const option = document.createElement('option');
                option.value = session.session_name;
                option.textContent = session.session_name; // Display session name
                sessionSelect.appendChild(option);
            });
            sessionSelect.disabled = false;
        } else {
             sessionSelect.innerHTML = '<option value="">-- No Sessions Found --</option>';
        }
    } catch (error) {
        console.error(`Failed to fetch sessions for project ${projectName}:`, error);
        sessionSelect.innerHTML = '<option value="">-- Error Loading Sessions --</option>';
        displaySessionStatus(`Error loading sessions for ${projectName}.`, false);
    }
    // Keep load button disabled until selection
}

/**
 * Displays a status message in the session management view.
 * @param {string} message - The message to display.
 * @param {boolean} isSuccess - True for success styling, false for error.
 */
function displaySessionStatus(message, isSuccess) {
    if (!sessionStatusMessage) return;
    sessionStatusMessage.textContent = message;
    sessionStatusMessage.className = 'session-status ' + (isSuccess ? 'success' : 'error');
    sessionStatusMessage.style.display = 'block';
    // Optionally hide after a delay
    // setTimeout(clearSessionStatus, 5000);
}

/** Clears the session status message. */
function clearSessionStatus() {
    if (sessionStatusMessage) {
        sessionStatusMessage.style.display = 'none';
        sessionStatusMessage.textContent = '';
        sessionStatusMessage.className = 'session-status';
    }
}

/** Sets the loading state for session buttons. */
function setSessionLoadingState(isLoading) {
    if (loadSessionButton) loadSessionButton.disabled = isLoading;
    if (saveSessionButton) saveSessionButton.disabled = isLoading;
    // Show spinner or change text? For now, just disable.
}


// --- File Attachment UI ---

/**
 * Updates the file info display area.
 * @param {object | null} fileInfo - The file info object { name, size } or null to clear.
 */
function updateFileInfoArea(fileInfo) {
    if (!fileInfoArea) return;
    if (fileInfo) {
        fileInfoArea.innerHTML = `
            <span>📎 ${fileInfo.name} (${(fileInfo.size / 1024).toFixed(1)} KB)</span>
            <button id="clear-file-button" title="Remove file">×</button>
        `;
        fileInfoArea.style.display = 'flex';
        // Add listener to the clear button dynamically
        document.getElementById('clear-file-button')?.addEventListener('click', () => {
            clearAttachedFile();
            updateFileInfoArea(null); // Clear UI
        });
    } else {
        fileInfoArea.innerHTML = '';
        fileInfoArea.style.display = 'none';
    }
}


// --- Input Textarea Height Adjustment ---

/** Adjusts the height of the chat textarea based on content. */
function adjustChatInputHeight() {
    if (!messageInput) return;
    // Temporarily reset height to auto to get the natural scrollHeight
    messageInput.style.height = 'auto';
    const scrollHeight = messageInput.scrollHeight;
    // Set the height based on scrollHeight, respecting min/max defined in CSS
    messageInput.style.height = `${scrollHeight}px`;
}

/** Resets the chat input textarea height to its default. */
function resetChatInputHeight() {
    if (messageInput) {
        messageInput.style.height = ''; // Remove inline style to revert to CSS default
    }
}

// --- Export UI Module Functions ---
export const uiModule = {
    addMessageToConversation,
    appendAgentResponseChunk,
    addLogEntry,
    updateAgentStatusList,
    switchView,
    openAgentModal,
    closeModal,
    refreshConfigList,
    populateProjectDropdown,
    populateSessionDropdown,
    displaySessionStatus,
    clearSessionStatus,
    setSessionLoadingState,
    updateFileInfoArea,
    adjustChatInputHeight,
    resetChatInputHeight,
    updateInitialConnectionStatus
};
