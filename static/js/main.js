// START OF FILE static/js/main.js

import { assignElements } from './domElements.js';
import { connectWebSocket } from './websocket.js';
import { switchView } from './ui.js';
import {
    handleSendMessage,
    handleMessageInputKeypress,
    handleMessageInput,
    handleNavButtonClick,
    handleFileSelect,
    handleRefreshConfig,
    handleAddAgentClick,
    handleAgentFormSubmit,
    handleProjectSelectionChange,
    handleLoadSession,
    handleSaveSession
} from './handlers.js';
import * as DOM from './domElements.js'; // Import DOM elements after they are assigned

/**
 * Sets up primary event listeners after DOM elements are assigned.
 */
const setupEventListeners = () => {
    console.log("Main: Setting up primary event listeners...");

    // Check if elements exist before adding listeners
    if (DOM.sendButton) {
        DOM.sendButton.addEventListener('click', handleSendMessage);
    } else { console.error("Main: Send button not found for listener setup."); }

    if (DOM.messageInput) {
        DOM.messageInput.addEventListener('keypress', handleMessageInputKeypress);
        DOM.messageInput.addEventListener('input', handleMessageInput);
    } else { console.error("Main: Message input not found for listener setup."); }

    if (DOM.navButtons) {
        DOM.navButtons.forEach(button => {
            button.addEventListener('click', handleNavButtonClick);
        });
    } else { console.error("Main: Nav buttons not found for listener setup."); }

     if (DOM.attachFileButton) {
        DOM.attachFileButton.addEventListener('click', () => DOM.fileInput?.click());
     } else { console.warn("Main: Attach file button not found."); }

     if (DOM.fileInput) {
        DOM.fileInput.addEventListener('change', handleFileSelect);
     } else { console.warn("Main: File input not found."); }

     // Config View Buttons
     if (DOM.refreshConfigButton) {
         DOM.refreshConfigButton.addEventListener('click', handleRefreshConfig);
     } else { console.warn("Main: Refresh config button not found."); }

     if (DOM.addAgentButton) {
         DOM.addAgentButton.addEventListener('click', handleAddAgentClick);
     } else { console.warn("Main: Add agent button not found."); }

     // Agent Modal Form Submit
     if (DOM.agentForm) {
         DOM.agentForm.addEventListener('submit', handleAgentFormSubmit);
     } else { console.warn("Main: Agent form not found."); }

     // Session Management Event Listeners
     if (DOM.projectSelect) {
         DOM.projectSelect.addEventListener('change', handleProjectSelectionChange);
     } else { console.warn("Main: Project select not found."); }

     if (DOM.loadSessionButton) {
         DOM.loadSessionButton.addEventListener('click', handleLoadSession);
     } else { console.warn("Main: Load session button not found."); }

     if (DOM.saveSessionButton) {
         DOM.saveSessionButton.addEventListener('click', handleSaveSession);
     } else { console.warn("Main: Save session button not found."); }

     // Expose closeModal globally IF NEEDED for inline HTML onclick
     // It's generally better to attach listeners via JS like above
     // window.uiModule = { closeModal: ui.closeModal }; // Example if needed

    console.log("Main: Primary event listeners setup complete.");
};


/**
 * Initialization function runs when the DOM is fully loaded.
 */
const initializeApp = () => {
    console.log("Main: DOM fully loaded and parsed. Initializing app...");
    assignElements(); // Assign DOM elements now
    setupEventListeners(); // Set up event listeners using assigned elements
    switchView('chat-view'); // Set the initial view
    connectWebSocket(); // Start WebSocket connection
    console.log("Main: Application initialization complete.");
};

// --- Wait for the DOM to be ready before initializing ---
document.addEventListener('DOMContentLoaded', initializeApp);

console.log("Frontend main.js loaded.");
