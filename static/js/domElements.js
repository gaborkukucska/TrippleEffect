// START OF FILE static/js/domElements.js

/**
 * Selects and exports references to frequently used DOM elements.
 * Should be called only after DOMContentLoaded.
 */

export let messageInput, sendButton, conversationArea, internalCommsArea, agentStatusContent, viewPanels, navButtons, fileInput, attachFileButton, fileInfoArea, projectSelect, sessionSelect, loadSessionButton, saveProjectNameInput, saveSessionNameInput, saveSessionButton, sessionStatusMessage, configContent, refreshConfigButton, addAgentButton, agentModal, agentForm, modalTitle, editAgentIdInput;

export const assignElements = () => {
    console.log("DOM Assign: Starting element assignment..."); // Log start

    // --- Select Elements ---
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    conversationArea = document.getElementById('conversation-area');
    agentStatusContent = document.getElementById('agent-status-content');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    fileInfoArea = document.getElementById('file-info-area');
    internalCommsArea = document.getElementById('internal-comms-area');
    viewPanels = document.querySelectorAll('.view-panel');
    navButtons = document.querySelectorAll('.nav-button');
    projectSelect = document.getElementById('project-select');
    sessionSelect = document.getElementById('session-select');
    loadSessionButton = document.getElementById('load-session-button');
    saveProjectNameInput = document.getElementById('save-project-name');
    saveSessionNameInput = document.getElementById('save-session-name');
    saveSessionButton = document.getElementById('save-session-button');
    sessionStatusMessage = document.getElementById('session-status-message');
    configContent = document.getElementById('config-content');
    refreshConfigButton = document.getElementById('refresh-config-button');
    addAgentButton = document.getElementById('add-agent-button');
    agentModal = document.getElementById('agent-modal');
    agentForm = document.getElementById('agent-form');
    modalTitle = document.getElementById('modal-title');
    editAgentIdInput = document.getElementById('edit-agent-id');
    // --- End Select Elements ---

    console.log("DOM Assign: Element selection complete. Validating critical elements..."); // Log validation start

    // Basic validation check
    const criticalElements = {
        conversationArea, internalCommsArea, messageInput, sendButton, viewPanels, navButtons
    };
    let allCriticalFound = true;
    for (const [name, element] of Object.entries(criticalElements)) {
        let found = false;
        // For querySelectorAll, check length
        if (element instanceof NodeList && element.length > 0) {
             found = true;
        } else if (element && !(element instanceof NodeList)) { // Check if element exists and is not a nodelist
             found = true;
        }

        if (!found) {
             console.error(`DOM Assign: Critical Element Missing/Empty: ${name}`);
             allCriticalFound = false;
        } else {
             console.log(`DOM Assign: Critical Element Found: ${name}`); // Log found elements
        }
    }

    if (allCriticalFound) {
        console.log("DOM Assign: All critical elements validated successfully.");
    } else {
        console.error("DOM Assign: One or more critical DOM elements were NOT found. UI functionality will be limited.");
    }
    console.log("DOM element assignment complete.");
};

// Note: assignElements() must be called after DOM is loaded, typically in main.js.
