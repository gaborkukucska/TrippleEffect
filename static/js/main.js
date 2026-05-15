// START OF FILE static/js/main.js

// --- Core Module Imports ---
import * as config from './config.js'; // Load config first (though not strictly necessary unless used globally here)
import * as state from './state.js'; // Load state management
import * as DOM from './domElements.js'; // Import DOM element selectors and assigner
import * as ui from './ui.js'; // Import UI manipulation functions
import * as ws from './websocket.js'; // Import WebSocket connection logic
// --- Handler Imports ---
import * as handlers from './handlers.js'; // Import all handlers
// --- API Import (Modified) ---
import { approveProject, downloadProject } from './api.js'; // Import specific functions
import { restoreMessages } from './chatPersistence.js'; // Restore chat on refresh
// --- Auth Import ---
import { checkAuth, showAuthView, hideAuthView, initAuth } from './auth.js';

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

     if (DOM.shutdownServerButton) {
          console.log("Main: Attaching listener to Shutdown Server button...");
         DOM.shutdownServerButton.addEventListener('click', handlers.handleShutdownServer);
     } else { console.warn("Main: Shutdown server button not found during listener setup."); }

     // --- Project Lifecycle Controls ---
     if (DOM.projectStartButton) {
          console.log("Main: Attaching listener to Project Start button...");
         DOM.projectStartButton.addEventListener('click', handlers.handleProjectStart);
     } else { console.warn("Main: Project start button not found during listener setup."); }

     if (DOM.projectStopButton) {
          console.log("Main: Attaching listener to Project Stop button...");
         DOM.projectStopButton.addEventListener('click', handlers.handleProjectStop);
     } else { console.warn("Main: Project stop button not found during listener setup."); }

     if (DOM.projectDownloadButton) {
          console.log("Main: Attaching listener to Project Download button...");
         DOM.projectDownloadButton.addEventListener('click', handlers.handleProjectDownload);
     } else { console.warn("Main: Project download button not found during listener setup."); }

     // --- Download Scope Modal Buttons ---
     if (DOM.downloadWorkspaceBtn) {
         DOM.downloadWorkspaceBtn.addEventListener('click', () => handlers.handleDownloadScopeSelect('workspace'));
     }
     if (DOM.downloadFullBtn) {
         DOM.downloadFullBtn.addEventListener('click', () => handlers.handleDownloadScopeSelect('full'));
     }

     // --- Internal Comms Filter Setup ---
     const internalCommsArea = document.getElementById('internal-comms-area');
     const filterIds = ['filter-system', 'filter-agent', 'filter-tool', 'filter-cg', 'filter-error'];
     filterIds.forEach(id => {
         const checkbox = document.getElementById(id);
         if (checkbox && internalCommsArea) {
             checkbox.addEventListener('change', (e) => {
                 const filterKey = id.replace('filter-', '');
                 internalCommsArea.setAttribute(`data-filter-${filterKey}`, e.target.checked);
             });
         }
     });

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

// --- NEW: Define named handler function in module scope ---
/**
 * Handles clicks on the project approval button using event delegation.
 * @param {Event} event The click event.
 */
const handleApproveButtonClick = async (event) => {
    // Check if the clicked element is an approve button
    if (event.target && event.target.classList.contains('approve-project-btn')) {
        const button = event.target;
        const pmAgentId = button.dataset.pmId; // Get agent ID from data attribute
        if (!pmAgentId) {
            console.error("Approve button clicked, but pmAgentId not found in data attribute.");
            ui.displayMessage("Error: Could not identify project to approve.", 'error', 'internal-comms-area', 'frontend');
            return;
        }

        console.log(`Handler: Approve button clicked for PM Agent ID: ${pmAgentId}`);
        button.disabled = true; // Disable button immediately
        button.textContent = 'Approving...';

        // --- DIAGNOSTIC LOG ---
        console.log("Checking approveProject function reference:", typeof approveProject, approveProject);
        // --- END DIAGNOSTIC LOG ---

        try {
            // Use the directly imported approveProject function (available in this scope)
            const result = await approveProject(pmAgentId);
            console.log("Handler: Project approval API call successful.", result);
            // Backend sends 'project_approved' message via WebSocket,
            // which handleWebSocketMessage will process to show confirmation.
            button.textContent = 'Approved'; // Update button text
        } catch (error) {
            console.error(`Handler: Error approving project for PM ${pmAgentId}:`, error);
            // Error message should be displayed by makeApiCall, but add a fallback
            ui.displayMessage(`Error approving project: ${error.message || 'Unknown error'}`, 'error', 'internal-comms-area', 'frontend');
            button.disabled = false; // Re-enable button on error
            button.textContent = 'Approve Project Start';
        }
    }
};
// --- END NEW ---


/**
 * Performs the full app initialization (DOM, listeners, WebSocket).
 * Called after authentication is confirmed.
 */
const startApp = (username) => {
    console.log(`Main: Starting app for user '${username}'...`);

    console.log("Main: Attempting to assign DOM elements...");
    DOM.assignElements();
    console.log("Main: DOM element assignment function executed.");

    console.log("Main: Attempting to set up event listeners...");
    setupEventListeners();
    console.log("Main: Event listener setup function executed.");

    ui.switchView('chat-view'); // Set the initial view using the UI module
    restoreMessages(); // Restore chat and internal comms from sessionStorage
    ws.connectWebSocket(); // Start WebSocket connection using the WS module


    // --- Attach the named handler function ---
    document.body.addEventListener('click', handleApproveButtonClick);
    // --- END Attach ---

    // Add delegated event listener for message buttons in conversation area
    if (DOM.conversationArea) {
        console.log("Main: Attaching delegated click listener for message buttons to conversationArea...");
        DOM.conversationArea.addEventListener('click', handlers.handleMessageButtonClick);
    } else {
        console.error("Main: conversationArea not found, cannot attach message button click listener.");
    }

    // --- Draggable Divider between Chat and Agent Status ---
    if (DOM.chatAgentsDivider && DOM.statusPanelsContainer) {
        console.log("Main: Initializing draggable divider for Chat/Agents split.");
        const divider = DOM.chatAgentsDivider;
        const statusPanel = DOM.statusPanelsContainer;
        const chatLayout = divider.parentElement; // .chat-view-layout

        let isDragging = false;
        let startY = 0;
        let startHeight = 0;

        const onPointerDown = (e) => {
            isDragging = true;
            startY = e.clientY;
            startHeight = statusPanel.getBoundingClientRect().height;
            divider.setPointerCapture(e.pointerId);
            document.body.style.userSelect = 'none'; // Prevent text selection while dragging
            document.body.style.cursor = 'ns-resize';
        };

        const onPointerMove = (e) => {
            if (!isDragging) return;
            // Dragging up increases status panel height, dragging down decreases it
            const dy = startY - e.clientY;
            const parentHeight = chatLayout.getBoundingClientRect().height;
            const minHeight = 80;
            const maxHeight = parentHeight * 0.7;
            const newHeight = Math.min(maxHeight, Math.max(minHeight, startHeight + dy));
            statusPanel.style.height = `${newHeight}px`;
        };

        const onPointerUp = (e) => {
            if (!isDragging) return;
            isDragging = false;
            divider.releasePointerCapture(e.pointerId);
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        };

        divider.addEventListener('pointerdown', onPointerDown);
        divider.addEventListener('pointermove', onPointerMove);
        divider.addEventListener('pointerup', onPointerUp);
        // Also cancel on pointer leaving the window
        divider.addEventListener('lostpointercapture', () => {
            isDragging = false;
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        });
    } else {
        console.warn("Main: Chat/Agents divider or agent status container not found, skipping divider setup.");
    }
    // --- END Draggable Divider ---

    console.log("Main: Application initialization sequence complete.");
};

const checkProviderSetupAndStart = async (username) => {
    try {
        const response = await fetch('/api/config/providers/status');
        if (response.ok) {
            const data = await response.json();
            if (!data.has_providers) {
                // Show provider setup modal
                const modal = document.getElementById('provider-setup-modal');
                if (modal) modal.style.display = 'flex';
                return; // Do not start the app yet
            }
        }
    } catch (e) {
        console.error("Failed to check provider status:", e);
    }
    // If providers exist or check failed, proceed to start app
    startApp(username);
};

const setupProviderFormListeners = () => {
    const providerSelect = document.getElementById('setup-provider');
    const apiKeyContainer = document.getElementById('setup-api-key-container');
    const baseUrlContainer = document.getElementById('setup-base-url-container');
    const apiKeyInput = document.getElementById('setup-api-key');
    const baseUrlInput = document.getElementById('setup-base-url');
    const form = document.getElementById('provider-setup-form');
    const errorDiv = document.getElementById('setup-error');

    if (!form || !providerSelect) return;

    providerSelect.addEventListener('change', (e) => {
        const val = e.target.value;
        if (val === 'ollama') {
            apiKeyContainer.style.display = 'none';
            baseUrlContainer.style.display = 'block';
            baseUrlInput.required = true;
            apiKeyInput.required = false;
        } else if (val === 'vllm' || val === 'litellm') {
            apiKeyContainer.style.display = 'block';
            baseUrlContainer.style.display = 'block';
            baseUrlInput.required = true;
            apiKeyInput.required = false;
        } else {
            apiKeyContainer.style.display = 'block';
            apiKeyInput.required = true;
            baseUrlContainer.style.display = 'none';
            baseUrlInput.required = false;
        }
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorDiv.textContent = '';
        const provider = providerSelect.value;
        const apiKey = apiKeyInput.value;
        const baseUrl = baseUrlInput.value;
        const submitBtn = document.getElementById('submit-provider-setup');

        submitBtn.disabled = true;
        submitBtn.textContent = 'Saving...';

        try {
            const response = await fetch('/api/config/providers/setup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider, api_key: apiKey, base_url: baseUrl })
            });
            const data = await response.json();
            if (response.ok && data.success) {
                errorDiv.style.color = '#28a745'; // Success green
                errorDiv.textContent = data.message;
            } else {
                errorDiv.textContent = data.detail || data.message || 'Failed to setup provider.';
                submitBtn.disabled = false;
                submitBtn.textContent = 'Save & Restart Framework';
            }
        } catch (err) {
            console.error(err);
            errorDiv.textContent = 'Connection error.';
            submitBtn.disabled = false;
            submitBtn.textContent = 'Save & Restart Framework';
        }
    });
};

/**
 * Initialization function runs when the DOM is fully loaded.
 * Checks authentication state before starting the app.
 */
const initializeApp = async () => {
    console.log("Main: DOM fully loaded. Checking authentication...");

    setupProviderFormListeners();

    // Initialize auth UI (tab switching, form handlers, logout)
    initAuth(async (username) => {
        // This callback fires on successful login/register
        await checkProviderSetupAndStart(username);
    });

    // Check if already authenticated (e.g. valid cookie from previous session)
    const authResult = await checkAuth();
    if (authResult.authenticated) {
        console.log(`Main: Already authenticated as '${authResult.username}'. Starting app.`);
        hideAuthView();
        await checkProviderSetupAndStart(authResult.username);
    } else {
        console.log("Main: Not authenticated. Showing login view.");
        showAuthView();
    }
};

// --- Wait for the DOM to be ready before initializing ---
document.addEventListener('DOMContentLoaded', initializeApp);

console.log("Frontend main.js loaded and initializing...");

