// START OF FILE static/js/app.js - Main Application Orchestrator (v3 - Ensuring Completeness)

// --- Import Modules ---
import { initConfigUI, displayAgentConfigurations } from './modules/config.js';
import { initWebSocket, sendMessageToServer, getWebSocketInstance } from './modules/websocket.js';
import {
    initUIUpdate, addMessage, appendAgentResponseChunk, finalizeAgentResponse,
    updateLogStatus, updateAgentStatusUI, addOrUpdateAgentStatusEntry, removeAgentStatusEntry,
    addRawLogEntry
} from './modules/uiUpdate.js';
import { initModals, openModal, closeModal, showOverrideModal } from './modules/modal.js';
import {
    initSwipe, handleTouchStart, handleTouchMove, handleTouchEnd,
    updateContentWrapperTransform, addKeyboardNavListeners // Removed getCurrent/setCurrent exports - use callbacks
} from './modules/swipe.js';

// --- Global State ---
let currentSectionIndex = 0; // Managed here, passed to/from swipe module via callbacks
let currentFile = null;      // File handling state
let currentFileContent = null;

// --- DOM Element References (for Initialization) ---
let contentWrapper = null;
let swipeSections = null;
let messageInput = null;
let sendButton = null;
let fileInput = null;
let attachFileButton = null;
let fileInfoArea = null;
let agentStatusContent = null;
let conversationArea = null;
let systemLogArea = null;
let configContent = null;

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded. Starting Initialization (Full App Code)...");

    // --- Get Core Elements ---
    console.log("Getting core DOM elements...");
    contentWrapper = document.querySelector('.content-wrapper');
    swipeSections = document.querySelectorAll('.swipe-section');
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    fileInfoArea = document.getElementById('file-info-area');
    agentStatusContent = document.getElementById('agent-status-content');
    conversationArea = document.getElementById('conversation-area');
    systemLogArea = document.getElementById('system-log-area');
    configContent = document.getElementById('config-content');

    // --- Basic Checks ---
    if (!contentWrapper || swipeSections.length === 0 || !conversationArea || !systemLogArea || !messageInput || !agentStatusContent || !configContent || !sendButton) {
        console.error("Essential UI elements missing! Aborting initialization. Check HTML IDs/Classes:", {
            contentWrapper: !!contentWrapper, swipeSections: swipeSections.length, conversationArea: !!conversationArea,
            systemLogArea: !!systemLogArea, messageInput: !!messageInput, agentStatusContent: !!agentStatusContent,
            configContent: !!configContent, sendButton: !!sendButton
        });
        document.body.innerHTML = '<h1 style="color: red; text-align: center;">UI Initialization Error: Core elements missing.</h1>';
        return;
    }
     console.log("Core DOM elements found.");

    const numSections = swipeSections.length;
    if (numSections > 0) {
         console.log(`Found ${numSections} swipe sections.`);

         // --- Initialize Modules (Order matters for dependencies) ---
         try {
             console.log("Initializing uiUpdate module...");
             initUIUpdate(
                 conversationArea,
                 systemLogArea,
                 agentStatusContent,
                 () => currentSectionIndex, // Pass getter for current index
                 swipeSections             // Pass NodeList of sections
             );
             console.log("uiUpdate module initialized.");

             console.log("Initializing Modals module...");
             initModals(
                 getWebSocketInstance, // Pass function that returns WS instance from websocket module
                 () => displayAgentConfigurations(), // Callback for config refresh (needs config module loaded)
                 addMessage // Callback for adding messages (from uiUpdate module)
             );
             console.log("Modals module initialized.");

             console.log("Initializing Config UI module...");
             initConfigUI(
                 openModal, // Function from modal module
                 addMessage // Function from uiUpdate module
             );
             console.log("Config UI module initialized.");

             console.log("Initializing WebSocket module...");
             initWebSocket(
                 handleWebSocketMessage, // Main message handler in this file
                 updateLogStatus,       // Function from uiUpdate module
                 addMessage             // Function from uiUpdate module
             );
             console.log("WebSocket module initialized.");

             console.log("Initializing Swipe module...");
             // Pass getter/setter for currentSectionIndex managed by this file
             initSwipe(
                 contentWrapper,
                 swipeSections,
                 () => currentSectionIndex, // Getter
                 (newIndex) => { currentSectionIndex = newIndex; } // Setter
             );
             console.log("Swipe module initialized.");

             console.log("Adding Keyboard Nav listeners...");
             // Pass getter/setter for keyboard nav too
             addKeyboardNavListeners(
                 () => currentSectionIndex, // Getter
                 (newIndex) => { currentSectionIndex = newIndex; } // Setter
             );
             console.log("Keyboard Nav listeners added.");

             console.log("Setting up Global Event Listeners...");
             setupGlobalEventListeners(); // This will attach swipe handlers from the swipe module
             console.log("Global event listeners setup complete.");

             // Initial transform is now set within initSwipe

             console.log("All modules initialized successfully.");

         } catch (error) {
             console.error("Error during module initialization:", error);
             document.body.innerHTML = `<h1 style="color: red; text-align: center;">UI Initialization Error: ${error.message}</h1><pre>${error.stack}</pre>`;
         }
    } else {
        console.error("Initialization Error: No elements with class 'swipe-section' found.");
    }
});

// --- Global Event Listeners ---
function setupGlobalEventListeners() {
    // Send button listener
    sendButton?.addEventListener('click', handleSendMessage);

    // Message input listener (Enter key)
    messageInput?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage();
        }
    });

    // File attachment listeners
    attachFileButton?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);

    // Swipe listeners on the wrapper - Attach the imported handlers
    if (contentWrapper) {
        contentWrapper.addEventListener('touchstart', handleTouchStart, { passive: false });
        contentWrapper.addEventListener('touchmove', handleTouchMove, { passive: false });
        contentWrapper.addEventListener('touchend', handleTouchEnd);
        contentWrapper.addEventListener('touchcancel', handleTouchEnd);
        console.log("Swipe event listeners attached to content wrapper.");
    } else { console.error("Content wrapper not found for swipe listeners!"); }

    // Global listener to close modals when clicking outside
    window.addEventListener('click', function(event) {
        if (event.target.classList.contains('modal')) {
            closeModal(event.target.id); // Use closeModal from modal module
        }
    });

    // Keyboard nav listeners are added via swipe module's addKeyboardNavListeners

    console.log("Global event listeners setup complete.");
}

// --- WebSocket Message Handling ---
function handleWebSocketMessage(data) {
    // Log raw data (using function from uiUpdate module)
    addRawLogEntry(data);

    switch (data.type) {
        case 'response_chunk':
            appendAgentResponseChunk(data.agent_id, data.content); // uiUpdate
            break;
        case 'status':
        case 'system_event':
            addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status.'}`, 'status'); // uiUpdate
             if (data.message === 'Connected to TrippleEffect backend!') {
                updateLogStatus('Connected!', false); // uiUpdate
             }
            break;
        case 'error':
            addMessage('system-log-area', `[${data.agent_id || 'Error'}] ${data.content}`, 'error'); // uiUpdate
            break;
        case 'final_response':
            finalizeAgentResponse(data.agent_id, data.content); // uiUpdate
            break;
        case 'agent_status_update':
            updateAgentStatusUI(data.agent_id, data.status); // uiUpdate
            break;
        case 'agent_added':
             addMessage('system-log-area', `[Sys] Agent Added: ${data.agent_id} (Team: ${data.team || 'N/A'})`, 'status'); // uiUpdate
             addOrUpdateAgentStatusEntry(data.agent_id, { // uiUpdate
                agent_id: data.agent_id,
                persona: data.config?.persona || data.agent_id,
                status: data.config?.status || 'idle',
                provider: data.config?.provider,
                model: data.config?.model,
                team: data.team
             });
            break;
        case 'agent_deleted':
            addMessage('system-log-area', `[Sys] Agent Deleted: ${data.agent_id}`, 'status'); // uiUpdate
            removeAgentStatusEntry(data.agent_id); // uiUpdate
            break;
        case 'team_created':
            addMessage('system-log-area', `[Sys] Team Created: ${data.team_id}`, 'status'); // uiUpdate
            break;
        case 'team_deleted':
             addMessage('system-log-area', `[Sys] Team Deleted: ${data.team_id}`, 'status'); // uiUpdate
            break;
        case 'agent_moved_team':
             addMessage('system-log-area', `[Sys] Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status'); // uiUpdate
             // Request status update for the specific agent to reflect team change in UI
             // requestAgentStatus(data.agent_id); // Needs backend support
            break;
        case 'request_user_override':
             showOverrideModal(data); // Use function from modal module
             addMessage('system-log-area', `[Sys] Override Required for Agent ${data.agent_id}`, 'error'); // uiUpdate
            break;
        default:
            console.warn('Received unknown message type:', data.type, data);
            addMessage('system-log-area', `[Sys] Unhandled msg type: ${data.type}`, 'status'); // uiUpdate
    }
}

// --- Action Handlers ---
function handleSendMessage() {
    const messageText = messageInput?.value.trim() ?? '';
    // Use the global currentFile state variable managed by file handling functions below
    if (!messageText && !currentFile) {
        console.log("Empty message and no file attached. Not sending.");
        return;
    }

    const messageToSend = {
        type: currentFile ? 'user_message_with_file' : 'user_message', // Adjust type as needed by backend
        text: messageText,
    };
    // Attach file details if present and content was read successfully
    if (currentFile && currentFileContent) {
        messageToSend.filename = currentFile.name;
        messageToSend.file_content = currentFileContent;
    } else if (currentFile && !currentFileContent) {
         console.error("File selected but content is missing. Cannot send.");
         addMessage('system-log-area', '[UI Error] File content missing. Please re-attach.', 'error');
         clearFileInput(); // Clear the broken selection
         return;
    }

    // Display user message in conversation area
    const displayMessage = currentFile ? `[File: ${currentFile.name}]\n${messageText}` : messageText;
    addMessage('conversation-area', displayMessage, 'user'); // From uiUpdate

    // Send to server via WebSocket module
    sendMessageToServer(messageToSend); // From websocket

    // Clear input and file selection
    if(messageInput) messageInput.value = '';
    clearFileInput(); // Use local clear function
}

// --- File Handling ---
function handleFileSelect(event) {
    const file = event.target.files[0]; if (!file) { clearFileInput(); return; }
    const allowedTypes = ['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml'];
    const allowedExtensions = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log'];
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();
    const isValidType = allowedTypes.includes(file.type) || allowedExtensions.includes(fileExtension);
    if (!isValidType) { alert(`Unsupported type: ${file.type || fileExtension}. Upload text files.`); clearFileInput(); return; }
    const maxSize = 1 * 1024 * 1024; if (file.size > maxSize) { alert(`File too large (${(file.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`); clearFileInput(); return; }
    const reader = new FileReader();
    reader.onload = (e) => { currentFile = { name: file.name, size: file.size, type: file.type }; currentFileContent = e.target.result; displayFileInfo(); console.log(`File "${file.name}" selected.`); };
    reader.onerror = (e) => { console.error("File read error:", e); alert("Error reading file."); clearFileInput(); };
    reader.readAsText(file);
}

function displayFileInfo() {
    if (!fileInfoArea) return;
    if (currentFile) {
        fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`;
    } else { fileInfoArea.innerHTML = ''; }
}

function clearFileInput() {
    currentFile = null; currentFileContent = null;
    if(fileInput) fileInput.value = '';
    displayFileInfo(); console.log("File input cleared.");
}

// --- Stubs for potentially needed functions (if backend requests them) ---
// function requestInitialAgentStatus() { console.log("Req init status (needs backend)..."); }
// function requestAgentStatus(agentId) { console.log(`Req status for ${agentId} (needs backend)...`); }

// --- Make functions globally accessible IF needed by inline HTML onclick (like modals) ---
// This is generally discouraged in module setups, prefer adding listeners in JS.
// However, for the simple modal close buttons, we can expose the function.
window.closeModal = closeModal; // Expose closeModal from modal module globally
window.clearFileInput = clearFileInput; // Expose clearFileInput globally for the button

console.log("Main app.js execution finished.");
