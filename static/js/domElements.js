// START OF FILE static/js/domElements.js

/**
 * Selects and exports references to frequently used DOM elements.
 * Should be called only after DOMContentLoaded.
 */

export let messageInput, sendButton, conversationArea, internalCommsArea, agentStatusContent, viewPanels, navButtons, fileInput, attachFileButton, fileInfoArea, projectSelect, sessionSelect, loadSessionButton, saveProjectNameInput, saveSessionNameInput, saveSessionButton, sessionStatusMessage, configContent, refreshConfigButton, addAgentButton, agentModal, agentForm, modalTitle, editAgentIdInput;

export const assignElements = () => {
    console.log("Assigning DOM elements...");

    // Main Chat View
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    conversationArea = document.getElementById('conversation-area');
    agentStatusContent = document.getElementById('agent-status-content');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    fileInfoArea = document.getElementById('file-info-area');

    // Internal Comms View
    internalCommsArea = document.getElementById('internal-comms-area'); // Added this

    // General UI
    viewPanels = document.querySelectorAll('.view-panel');
    navButtons = document.querySelectorAll('.nav-button');

    // Session View
    projectSelect = document.getElementById('project-select');
    sessionSelect = document.getElementById('session-select');
    loadSessionButton = document.getElementById('load-session-button');
    saveProjectNameInput = document.getElementById('save-project-name');
    saveSessionNameInput = document.getElementById('save-session-name');
    saveSessionButton = document.getElementById('save-session-button');
    sessionStatusMessage = document.getElementById('session-status-message');

    // Config View
    configContent = document.getElementById('config-content');
    refreshConfigButton = document.getElementById('refresh-config-button');
    addAgentButton = document.getElementById('add-agent-button');

    // Agent Modal
    agentModal = document.getElementById('agent-modal');
    agentForm = document.getElementById('agent-form');
    modalTitle = document.getElementById('modal-title');
    editAgentIdInput = document.getElementById('edit-agent-id');

    // Basic validation check
    const criticalElements = {
        conversationArea,
        internalCommsArea, // Added this
        messageInput,
        sendButton,
        viewPanels,
        navButtons
    };
    for (const [name, element] of Object.entries(criticalElements)) {
        // For querySelectorAll, check length
        if (element instanceof NodeList && element.length === 0) {
             console.error(`Critical Element Missing/Empty: ${name}`);
        } else if (!element && !(element instanceof NodeList)) {
             console.error(`Critical Element Missing: ${name}`);
        }
    }
    console.log("DOM element assignment complete.");
};

// Note: assignElements() must be called after DOM is loaded, typically in main.js.
