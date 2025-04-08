// static/js/app.js - Main Application Orchestrator

// --- Import Modules ---
import { initConfigUI, displayAgentConfigurations } from './modules/config.js';
import { initWebSocket, sendMessageToServer } from './modules/websocket.js';
import {
    initUIUpdate, addMessage, appendAgentResponseChunk, finalizeAgentResponse,
    updateLogStatus, updateAgentStatusUI, addOrUpdateAgentStatusEntry, removeAgentStatusEntry,
    addRawLogEntry, getConversationArea, getSystemLogArea // Export elements if needed by WS handler
} from './modules/uiUpdate.js';
import { initModals, openModal, closeModal, showOverrideModal } from './modules/modal.js';
import {
    initSwipe, handleTouchStart, handleTouchMove, handleTouchEnd,
    updateContentWrapperTransform, addKeyboardNavListeners, getCurrentSectionIndex, setCurrentSectionIndex
} from './modules/swipe.js';

// --- Global State (Minimal) ---
let currentSectionIndex = 0; // Managed here, passed to/from swipe module

// --- DOM Element References (for Initialization) ---
let contentWrapper = null;
let swipeSections = null;
let messageInput = null;
let sendButton = null;
let fileInput = null;
let attachFileButton = null;
let fileInfoArea = null;
let agentStatusContent = null; // Needed by uiUpdate

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded. Initializing Modules...");

    // --- Get Core Elements ---
    contentWrapper = document.querySelector('.content-wrapper');
    swipeSections = document.querySelectorAll('.swipe-section');
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    fileInfoArea = document.getElementById('file-info-area');
    agentStatusContent = document.getElementById('agent-status-content'); // For uiUpdate

    // --- Basic Checks ---
    if (!contentWrapper || swipeSections.length === 0 || !messageInput || !sendButton || !agentStatusContent) {
        console.error("Essential UI elements missing! Cannot initialize application.");
        document.body.innerHTML = '<h1 style="color: red; text-align: center;">UI Initialization Error: Core elements missing.</h1>';
        return;
    }

    // --- Initialize Modules (Order might matter for dependencies) ---
    try {
        // UI Update needs elements first
        initUIUpdate(
            document.getElementById('conversation-area'),
            document.getElementById('system-log-area'),
            agentStatusContent // Pass the specific element it manages
        );

        // Modals need callbacks from UI update and config
        // Pass displayAgentConfigurations from config module later
        initModals(
            null, // WebSocket instance placeholder, set later
            () => displayAgentConfigurations(), // Callback to refresh config
            addMessage // Callback to add messages
        );

        // Config UI needs modal opener and message adder callbacks
        initConfigUI(
            openModal, // Function from modal module
            addMessage // Function from uiUpdate module
        );

        // WebSocket needs the message handler
        initWebSocket(handleWebSocketMessage); // Pass the main message handler

        // Swipe needs wrapper and sections
        initSwipe(contentWrapper, swipeSections);

        // Keyboard Nav needs getter/setter for index managed here
        addKeyboardNavListeners(
            () => currentSectionIndex, // Getter
            (newIndex) => { currentSectionIndex = newIndex; } // Setter
        );

        // Setup remaining general event listeners
        setupGlobalEventListeners();

        console.log("All modules initialized successfully.");

    } catch (error) {
        console.error("Error during module initialization:", error);
        document.body.innerHTML = `<h1 style="color: red; text-align: center;">UI Initialization Error: ${error.message}</h1>`;
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

    // Swipe listeners on the wrapper (initialized in initSwipe)
    contentWrapper?.addEventListener('touchstart', handleTouchStart, { passive: false });
    contentWrapper?.addEventListener('touchmove', handleTouchMove, { passive: false });
    contentWrapper?.addEventListener('touchend', handleTouchEnd);
    contentWrapper?.addEventListener('touchcancel', handleTouchEnd);

    // Global listener to close modals when clicking outside
    window.addEventListener('click', function(event) {
        if (event.target.classList.contains('modal')) {
            closeModal(event.target.id); // Use closeModal from modal module
        }
    });

    console.log("Global event listeners setup complete.");
}

// --- WebSocket Message Handling ---
/**
 * Main handler to process messages received via WebSocket.
 * Delegates UI updates to the uiUpdate module and modal actions to the modal module.
 * @param {object} data - The parsed message data from the server.
 */
function handleWebSocketMessage(data) {
    // Log raw data (using function from uiUpdate module)
    addRawLogEntry(data);

    switch (data.type) {
        case 'response_chunk':
            appendAgentResponseChunk(data.agent_id, data.content);
            break;
        case 'status':
        case 'system_event':
            // Add status to system log area
            addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status.'}`, 'status');
            // If it's the initial connection message, update the log status specifically
             if (data.message === 'Connected to TrippleEffect backend!') {
                updateLogStatus('Connected!', false);
             }
            break;
        case 'error':
            addMessage('system-log-area', `[${data.agent_id || 'Error'}] ${data.content}`, 'error');
            break;
        case 'final_response':
            finalizeAgentResponse(data.agent_id, data.content);
            break;
        case 'agent_status_update':
            updateAgentStatusUI(data.agent_id, data.status);
            break;
        case 'agent_added':
             addMessage('system-log-area', `[Sys] Agent Added: ${data.agent_id} (Team: ${data.team || 'N/A'})`, 'status');
             // Add/Update entry in the agent status list UI
             addOrUpdateAgentStatusEntry(data.agent_id, {
                agent_id: data.agent_id,
                persona: data.config?.persona || data.agent_id,
                status: data.config?.status || 'idle',
                provider: data.config?.provider,
                model: data.config?.model,
                team: data.team
             });
            break;
        case 'agent_deleted':
            addMessage('system-log-area', `[Sys] Agent Deleted: ${data.agent_id}`, 'status');
            removeAgentStatusEntry(data.agent_id); // Remove from agent status list UI
            break;
        // --- Team messages for logs ---
        case 'team_created':
            addMessage('system-log-area', `[Sys] Team Created: ${data.team_id}`, 'status');
            break;
        case 'team_deleted':
             addMessage('system-log-area', `[Sys] Team Deleted: ${data.team_id}`, 'status');
            break;
        case 'agent_moved_team':
             addMessage('system-log-area', `[Sys] Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status');
             // Request status update for the specific agent to reflect team change in UI
             // requestAgentStatus(data.agent_id); // Uncomment if backend supports this request
             // For now, rely on the next automatic status push or manual refresh
            break;
        // --- Modal Trigger ---
        case 'request_user_override':
             showOverrideModal(data); // Use function from modal module
             addMessage('system-log-area', `[Sys] Override Required for Agent ${data.agent_id}`, 'error');
            break;
        default:
            console.warn('Received unknown message type:', data.type, data);
            addMessage('system-log-area', `[Sys] Unhandled msg type: ${data.type}`, 'status');
    }
}

// --- Action Handlers ---
/**
 * Gathers message/file data and sends it via WebSocket.
 */
function handleSendMessage() {
    const messageText = messageInput?.value.trim() ?? '';
    // Use the global currentFile state variable managed by file handling functions
    if (!messageText && !currentFile) {
        console.log("Empty message and no file attached. Not sending.");
        return;
    }

    const messageToSend = {
        // Use different types if backend distinguishes them, otherwise use single type
        type: currentFile ? 'user_message_with_file' : 'user_message', // Or just 'user_message'
        text: messageText,
    };
    if (currentFile) {
        messageToSend.filename = currentFile.name;
        messageToSend.file_content = currentFile.content; // Content is already read
    }

    // Display user message in conversation area (using function from uiUpdate)
    const displayMessage = currentFile ? `[File: ${currentFile.name}]\n${messageText}` : messageText;
    addMessage('conversation-area', displayMessage, 'user');

    // Send to server (using function from websocket module)
    sendMessageToServer(messageToSend);

    // Clear input and file selection
    if(messageInput) messageInput.value = '';
    clearFileInput(); // Use local clear function
}

// --- File Handling ---
// State variable `currentFile` is global in this file
let currentFileContent = null; // Separate variable to hold read content

function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) { clearFileInput(); return; }

    // Validation (Type and Size)
    const allowedTypes = ['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml', 'application/log'];
    const allowedExtensions = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log'];
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();
    const isValidType = allowedTypes.includes(file.type) || allowedExtensions.includes(fileExtension);
    if (!isValidType) { alert(`Unsupported file type: ${file.type || fileExtension}. Upload text-based files.`); clearFileInput(); return; }
    const maxSize = 1 * 1024 * 1024; // 1MB
    if (file.size > maxSize) { alert(`File too large (${(file.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`); clearFileInput(); return; }

    // Read File Content
    const reader = new FileReader();
    reader.onload = (e) => {
        // Store file metadata and content separately
        currentFile = { name: file.name, size: file.size, type: file.type };
        currentFileContent = e.target.result; // Store content
        displayFileInfo(); // Update UI
        console.log(`File "${file.name}" selected and content stored.`);
    };
    reader.onerror = (e) => {
        console.error("File reading error:", e);
        alert("Error reading file.");
        clearFileInput();
    };
    reader.readAsText(file); // Read as text
}

function displayFileInfo() {
    if (!fileInfoArea) return;
    if (currentFile) {
        fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`;
    } else {
        fileInfoArea.innerHTML = ''; // Clear the display
    }
}

function clearFileInput() {
    currentFile = null; // Clear file metadata
    currentFileContent = null; // Clear stored content
    if(fileInput) fileInput.value = ''; // Clear the file input element itself
    displayFileInfo(); // Update UI
    console.log("File input cleared.");
}
