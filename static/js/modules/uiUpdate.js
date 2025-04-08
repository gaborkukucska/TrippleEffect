// static/js/modules/uiUpdate.js

// UI Update Logic (Messages, Agent Status)

// --- Module State / Dependencies ---
let conversationAreaElement = null;
let systemLogAreaElement = null;
let agentStatusContentElement = null;
let currentSectionIndexGetter = () => 0; // Function provided by app.js to get current swipe index
let swipeSectionsNodeList = null; // NodeList of sections provided by app.js

/**
 * Initializes the UI Update module with necessary DOM elements.
 * @param {HTMLElement} convArea - The conversation area element.
 * @param {HTMLElement} logArea - The system log area element.
 * @param {HTMLElement} statusArea - The agent status content element.
 * @param {function} getIndexFunc - Function from app.js to get current swipe index.
 * @param {NodeList} sectionsNodeList - NodeList of swipe sections from app.js.
 */
export function initUIUpdate(convArea, logArea, statusArea, getIndexFunc, sectionsNodeList) {
    conversationAreaElement = convArea;
    systemLogAreaElement = logArea;
    agentStatusContentElement = statusArea;
    currentSectionIndexGetter = getIndexFunc;
    swipeSectionsNodeList = sectionsNodeList;

    if (!conversationAreaElement || !systemLogAreaElement || !agentStatusContentElement) {
        console.error("One or more UI update target elements not found!");
    } else {
        console.log("UI Update module initialized.");
    }
}

/**
 * Helper function to add a formatted message div to a specific area.
 * Handles scrolling only if the area is in the active section.
 * @param {string} areaId - 'conversation-area' or 'system-log-area'.
 * @param {string} text - The message text.
 * @param {string} [type='status'] - Message type ('user', 'agent_response', 'status', 'error', 'tool-execution').
 * @param {string|null} [agentId=null] - The agent ID associated with the message.
 */
export function addMessage(areaId, text, type = 'status', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) {
        console.error(`Message area #${areaId} not found.`);
        return;
    }
    const placeholder = area.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();

    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type);
    if (agentId) messageDiv.dataset.agentId = agentId;

    // Add timestamp to system log messages
    if (areaId === 'system-log-area') {
        const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const timestampSpan = document.createElement('span');
        timestampSpan.classList.add('timestamp');
        timestampSpan.textContent = `[${timestamp}] `;
        messageDiv.appendChild(timestampSpan);
    }

    // Handle content safely (escape HTML, convert newlines)
    const contentSpan = document.createElement('span');
    contentSpan.innerHTML = text
        .replace(/&/g, "&")
        .replace(/</g, "<")
        .replace(/>/g, ">")
        .replace(/\n/g, '<br>');
    messageDiv.appendChild(contentSpan);

    area.appendChild(messageDiv);

    // Scroll only if the area is within the currently active section
    const currentSectionIdx = currentSectionIndexGetter();
    if (swipeSectionsNodeList && area.closest('.swipe-section') === swipeSectionsNodeList[currentSectionIdx]) {
        // Use setTimeout to ensure scrolling happens after rendering
        setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
    }
}

/**
 * Appends a chunk of text to an ongoing agent response message.
 * Creates the message div if it doesn't exist.
 * Handles scrolling only if the area is active.
 * @param {string} agentId - The ID of the agent responding.
 * @param {string} chunk - The text chunk to append.
 */
export function appendAgentResponseChunk(agentId, chunk) {
    const area = conversationAreaElement; if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);

    if (!agentMsgDiv) {
        const placeholder = area.querySelector('.initial-placeholder'); if (placeholder) placeholder.remove();
        agentMsgDiv = document.createElement('div');
        agentMsgDiv.classList.add('message', 'agent_response', 'incomplete');
        agentMsgDiv.dataset.agentId = agentId;
        const label = document.createElement('strong');
        label.textContent = `Agent @${agentId}:\n`; // Add label and newline
        agentMsgDiv.appendChild(label);
        area.appendChild(agentMsgDiv);
    }
    // Append text chunk safely
    const chunkNode = document.createTextNode(chunk);
    agentMsgDiv.appendChild(chunkNode);

    // Scroll only if the area is within the currently active section
    const currentSectionIdx = currentSectionIndexGetter();
     if (swipeSectionsNodeList && area.closest('.swipe-section') === swipeSectionsNodeList[currentSectionIdx]) {
        setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
    }
}

/**
 * Finalizes an agent's response message by removing the 'incomplete' class.
 * If no streaming occurred, adds the full message.
 * Handles scrolling only if the area is active.
 * @param {string} agentId - The ID of the agent whose response is final.
 * @param {string} finalContent - The complete final content (used if no streaming occurred).
 */
export function finalizeAgentResponse(agentId, finalContent) {
    const area = conversationAreaElement; if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);

    if (agentMsgDiv) {
        agentMsgDiv.classList.remove('incomplete');
    } else if (finalContent) {
        // If no streaming chunks were received, add the complete message now
        addMessage('conversation-area', `Agent @${agentId}:\n${finalContent}`, 'agent_response', agentId);
    }

    // Scroll only if the area is within the currently active section
    const currentSectionIdx = currentSectionIndexGetter();
     if (swipeSectionsNodeList && area.closest('.swipe-section') === swipeSectionsNodeList[currentSectionIdx]) {
         setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
    }
}

/**
 * Updates the connection status message in the system log area.
 * @param {string} message - The status text to display.
 * @param {boolean} [isError=false] - Whether the status represents an error state.
 */
export function updateLogStatus(message, isError = false) {
    const area = systemLogAreaElement; if (!area) return;
    let statusDiv = area.querySelector('.status.initial-connecting'); // Find specific connecting message
    if (!statusDiv) {
        // If connecting message isn't found, target the last status message
        const statusMessages = area.querySelectorAll('.message.status');
        statusDiv = statusMessages.length > 0 ? statusMessages[statusMessages.length - 1] : null;
    }

    // If no status div exists, create one (e.g., for the very first status)
    if (!statusDiv && message) {
        addMessage('system-log-area', message, isError ? 'error' : 'status');
    } else if (statusDiv) {
        // Update existing status div
        statusDiv.textContent = message; // Use textContent for safety
        statusDiv.className = `message status ${isError ? 'error' : ''}`; // Reset classes
        // Remove specific class if connection is established
        if (message === 'Connected!' || message === 'Connected to backend!') {
            statusDiv.classList.remove('initial-connecting');
        }
    }
}

/**
 * Entry point to update the Agent Status list UI.
 * @param {string} agentId - The ID of the agent whose status changed.
 * @param {object} statusData - The new status data object.
 */
export function updateAgentStatusUI(agentId, statusData) {
    if (!agentStatusContentElement) {
        console.warn("Agent status content area not found. Cannot update UI.");
        return;
    }
    const placeholder = agentStatusContentElement.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();
    addOrUpdateAgentStatusEntry(agentId, statusData);
}

/**
 * Adds a new agent entry or updates an existing one in the status list.
 * @param {string} agentId - The agent's ID.
 * @param {object} statusData - Object containing persona, status, provider, model, team.
 */
export function addOrUpdateAgentStatusEntry(agentId, statusData) {
     if (!agentStatusContentElement) return;
     let itemDiv = agentStatusContentElement.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);

     if (!itemDiv) {
         itemDiv = document.createElement('div');
         itemDiv.classList.add('agent-status-item');
         itemDiv.dataset.agentId = agentId;
         // Simple append, could sort later if needed
         agentStatusContentElement.appendChild(itemDiv);
     }

     // Safely extract data
     const persona = statusData?.persona || agentId;
     const status = statusData?.status || 'unknown';
     const provider = statusData?.provider || 'N/A';
     const model = statusData?.model || 'N/A';
     const team = statusData?.team || 'None';

     // Set title attribute for tooltip info
     itemDiv.title = `ID: ${agentId}\nProvider: ${provider}\nModel: ${model}\nTeam: ${team}\nStatus: ${status}`;

     // Update innerHTML for display
     itemDiv.innerHTML = `
         <strong>${persona}</strong>
         <span class="agent-model">(${model})</span>
         <span>[Team: ${team}]</span>
         <span class="agent-status">${status.replace('_', ' ')}</span>
     `;
     // Update status class for dynamic styling
     itemDiv.className = `agent-status-item status-${status}`;
}

/**
 * Removes an agent's entry from the status list UI.
 * @param {string} agentId - The ID of the agent to remove.
 */
export function removeAgentStatusEntry(agentId) {
    if (!agentStatusContentElement) return;
    const itemDiv = agentStatusContentElement.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);
    if (itemDiv) itemDiv.remove();
    // Add placeholder back if the list becomes empty
    if (!agentStatusContentElement.hasChildNodes() || agentStatusContentElement.innerHTML.trim() === '') {
        agentStatusContentElement.innerHTML = '<span class="status-placeholder">No active agents.</span>';
    }
}

/**
 * Logs raw WebSocket data to the console for debugging.
 * @param {object} data - The raw data object received from WebSocket.
 */
export function addRawLogEntry(data) {
    try {
        const logText = JSON.stringify(data);
        // Limit log length in console to avoid flooding
        console.debug("Raw WS Data:", logText.substring(0, 500) + (logText.length > 500 ? '...' : ''));
    } catch (e) {
        // Handle potential circular references or other stringify errors
        console.warn("Could not stringify raw WS data:", data);
    }
}

// Optional: Export element getters if other modules absolutely need direct access
// export function getConversationArea() { return conversationAreaElement; }
// export function getSystemLogArea() { return systemLogAreaElement; }
