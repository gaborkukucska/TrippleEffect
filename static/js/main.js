// START OF FILE static/js/main.js

// --- Core Module Imports ---
import * as config from './config.js'; // Load config first (though not strictly necessary unless used globally here)
import * as state from './state.js'; // Load state management
import * as DOM from './domElements.js'; // Import DOM element selectors and assigner
import * as ui from './ui.js'; // Import UI manipulation functions
import * as ws from './websocket.js'; // Import WebSocket connection logic
// --- Handler Imports ---
import * as handlers from './handlers.js'; // Import all handlers

/**
 * Sets up primary event listeners after DOM elements are assigned.
 * Connects DOM elements to their corresponding handler functions.
 */
const setupEventListeners = () => {
    console.log("Main: Setting up primary event listeners..."); // Log start of setup

    // --- Input Area (Chat View) ---
    if (DOM.sendButton) {
        console.log("Main: Attaching listener to Send button...");
        DOM.sendButton.addEventListener('click', handlers.handleSendMessage);
    } else { console.error("Main: Send button not found during listener setup."); }

    if (DOM.messageInput) {
        console.log("Main: Attaching listeners to Message input...");
        DOM.messageInput.addEventListener('keypress', handlers.handleMessageInputKeypress);
        DOM.messageInput.addEventListener('input', handlers.handleMessageInput);
    } else { console.error("Main: Message input not found during listener setup."); }

    if (DOM.attachFileButton && DOM.fileInput) {
         console.log("Main: Attaching listener to Attach File button...");
        DOM.attachFileButton.addEventListener('click', () => {
             console.log("Main: Attach file button clicked, triggering file input.");
             DOM.fileInput.click();
         });
    } else { console.warn("Main: Attach file button or file input not found during listener setup."); }

    if (DOM.fileInput) {
         console.log("Main: Attaching listener to File input change...");
        DOM.fileInput.addEventListener('change', handlers.handleFileSelect);
    } else { console.warn("Main: File input not found during listener setup."); }

    // --- Navigation ---
    if (DOM.navButtons && DOM.navButtons.length > 0) {
        console.log(`Main: Attaching listeners to ${DOM.navButtons.length} Nav buttons...`);
        DOM.navButtons.forEach(button => {
            button.addEventListener('click', handlers.handleNavButtonClick);
        });
    } else { console.error("Main: Nav buttons not found or empty during listener setup."); }

    // --- Config View ---
     if (DOM.refreshConfigButton) {
          console.log("Main: Attaching listener to Refresh Config button...");
         DOM.refreshConfigButton.addEventListener('click', handlers.handleRefreshConfig);
     } else { console.warn("Main: Refresh config button not found during listener setup."); }

     if (DOM.addAgentButton) {
          console.log("Main: Attaching listener to Add Agent button...");
         DOM.addAgentButton.addEventListener('click', handlers.handleAddAgentClick);
     } else { console.warn("Main: Add agent button not found during listener setup."); }
     // Note: Edit/Delete listeners are added dynamically in configView.js render function

     // --- Agent Modal ---
     if (DOM.agentForm) {
          console.log("Main: Attaching listener to Agent Form submit...");
         DOM.agentForm.addEventListener('submit', handlers.handleAgentFormSubmit);
     } else { console.warn("Main: Agent form not found during listener setup."); }
     // Note: Modal close buttons use inline onclick or listeners set up elsewhere (e.g., in ui.js if needed)

     // --- Session Management ---
     if (DOM.projectSelect) {
          console.log("Main: Attaching listener to Project Select change...");
         DOM.projectSelect.addEventListener('change', handlers.handleProjectSelectionChange);
     } else { console.warn("Main: Project select not found during listener setup."); }

     if (DOM.loadSessionButton) {
          console.log("Main: Attaching listener to Load Session button...");
         DOM.loadSessionButton.addEventListener('click', handlers.handleLoadSession);
     } else { console.warn("Main: Load session button not found during listener setup."); }

     if (DOM.saveSessionButton) {
          console.log("Main: Attaching listener to Save Session button...");
         DOM.saveSessionButton.addEventListener('click', handlers.handleSaveSession);
     } else { console.warn("Main: Save session button not found during listener setup."); }


     // --- Global Accessibility for Inline Handlers (If Required) ---
     // Expose necessary UI functions globally if they MUST be called from inline HTML onclick
     // It's generally preferred to attach all listeners here or within specific modules.
     window.uiModule = {
         closeModal: ui.closeModal
         // Add other functions needed globally here
     };
     console.log("Main: Globally exposed functions (if any) under window.uiModule");


    console.log("Main: Primary event listeners setup complete.");
};


/**
 * Initialization function runs when the DOM is fully loaded.
 */
const initializeApp = () => {
    console.log("Main: DOM fully loaded and parsed. Initializing app..."); // Log 1: Start Init

    console.log("Main: Attempting to assign DOM elements..."); // Log 2: Before Assign
    DOM.assignElements(); // Crucial: Assign DOM elements FIRST
    console.log("Main: DOM element assignment function executed."); // Log 3: After Assign

    console.log("Main: Attempting to set up event listeners..."); // Log 4: Before Setup
    setupEventListeners(); // Setup listeners using assigned elements
    console.log("Main: Event listener setup function executed."); // Log 5: After Setup

    ui.switchView('chat-view'); // Set the initial view using the UI module
    ws.connectWebSocket(); // Start WebSocket connection using the WS module

    console.log("Main: Application initialization sequence complete."); // Log 6: End Init
};

// --- Wait for the DOM to be ready before initializing ---
document.addEventListener('DOMContentLoaded', initializeApp);

console.log("Frontend main.js loaded and initializing...");
