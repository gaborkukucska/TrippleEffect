// START OF FILE static/js/app.js - Main Application Orchestrator (v2 - Enhanced Logging)

// --- Import Modules ---
import { initConfigUI, displayAgentConfigurations } from './modules/config.js';
import { initWebSocket, sendMessageToServer, getWebSocketInstance } from './modules/websocket.js'; // Import getWebSocketInstance
import {
    initUIUpdate, addMessage, appendAgentResponseChunk, finalizeAgentResponse,
    updateLogStatus, updateAgentStatusUI, addOrUpdateAgentStatusEntry, removeAgentStatusEntry,
    addRawLogEntry
} from './modules/uiUpdate.js';
import { initModals, openModal, closeModal, showOverrideModal } from './modules/modal.js';
import {
    initSwipe, handleTouchStart, handleTouchMove, handleTouchEnd,
    updateContentWrapperTransform, addKeyboardNavListeners, getCurrentSectionIndex, setCurrentSectionIndex
} from './modules/swipe.js';

// --- Global State ---
let currentSectionIndex = 0; // Managed here, passed to/from swipe module
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
let conversationArea = null; // Add refs needed for initUIUpdate
let systemLogArea = null;    // Add refs needed for initUIUpdate
let configContent = null; // Add refs needed for initConfigUI

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded. Starting Initialization...");

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
    conversationArea = document.getElementById('conversation-area'); // Get element
    systemLogArea = document.getElementById('system-log-area');    // Get element
    configContent = document.getElementById('config-content');    // Get element

    // --- Basic Checks ---
    if (!contentWrapper || swipeSections.length === 0 || !conversationArea || !systemLogArea || !messageInput || !agentStatusContent || !configContent || !sendButton) {
        console.error("Essential UI elements missing! Aborting initialization. Check HTML IDs/Classes:", {
            contentWrapper: !!contentWrapper,
            swipeSections: swipeSections.length,
            conversationArea: !!conversationArea,
            systemLogArea: !!systemLogArea,
            messageInput: !!messageInput,
            agentStatusContent: !!agentStatusContent,
            configContent: !!configContent,
            sendButton: !!sendButton
        });
        document.body.innerHTML = '<h1 style="color: red; text-align: center;">UI Initialization Error: Core elements missing.</h1>';
        return;
    }
     console.log("Core DOM elements found.");

    const numSections = swipeSections.length;
    if (numSections > 0) {
         console.log(`Found ${numSections} swipe sections.`);

         // --- Initialize Modules (Order might matter for dependencies) ---
         try {
             console.log("Initializing uiUpdate module...");
             initUIUpdate(
                 conversationArea, // Pass the actual element
                 systemLogArea,    // Pass the actual element
                 agentStatusContent,
                 () => currentSectionIndex, // Pass getter for current index
                 swipeSections             // Pass NodeList of sections
             );
             console.log("uiUpdate module initialized.");

             console.log("Initializing Modals module...");
             initModals(
                 getWebSocketInstance, // Pass function that returns WS instance
                 () => displayAgentConfigurations(), // Callback for config refresh
                 addMessage // Callback for adding messages
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
             initSwipe(contentWrapper, swipeSections);
             console.log("Swipe module initialized.");

             console.log("Adding Keyboard Nav listeners...");
             addKeyboardNavListeners(
                 () => currentSectionIndex, // Getter
                 (newIndex) => { currentSectionIndex = newIndex; } // Setter
             );
             console.log("Keyboard Nav listeners added.");

             console.log("Setting up Global Event Listeners...");
             setupGlobalEventListeners();
             console.log("Global event listeners setup complete.");

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
// (handleWebSocketMessage remains unchanged)
function handleWebSocketMessage(data) { addRawLogEntry(data); switch (data.type) { case 'response_chunk': appendAgentResponseChunk(data.agent_id, data.content); break; case 'status': case 'system_event': addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status.'}`, 'status'); if (data.message === 'Connected to TrippleEffect backend!') { updateLogStatus('Connected!', false); } break; case 'error': addMessage('system-log-area', `[${data.agent_id || 'Error'}] ${data.content}`, 'error'); break; case 'final_response': finalizeAgentResponse(data.agent_id, data.content); break; case 'agent_status_update': updateAgentStatusUI(data.agent_id, data.status); break; case 'agent_added': addMessage('system-log-area', `[Sys] Agent Added: ${data.agent_id} (Team: ${data.team || 'N/A'})`, 'status'); addOrUpdateAgentStatusEntry(data.agent_id, { agent_id: data.agent_id, persona: data.config?.persona || data.agent_id, status: data.config?.status || 'idle', provider: data.config?.provider, model: data.config?.model, team: data.team }); break; case 'agent_deleted': addMessage('system-log-area', `[Sys] Agent Deleted: ${data.agent_id}`, 'status'); removeAgentStatusEntry(data.agent_id); break; case 'team_created': addMessage('system-log-area', `[Sys] Team Created: ${data.team_id}`, 'status'); break; case 'team_deleted': addMessage('system-log-area', `[Sys] Team Deleted: ${data.team_id}`, 'status'); break; case 'agent_moved_team': addMessage('system-log-area', `[Sys] Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status'); requestAgentStatus(data.agent_id); break; case 'request_user_override': showOverrideModal(data); addMessage('system-log-area', `[Sys] Override Required for Agent ${data.agent_id}`, 'error'); break; default: console.warn('Unknown WS msg type:', data.type, data); addMessage('system-log-area', `[Sys] Unhandled msg type: ${data.type}`, 'status'); } }
function requestInitialAgentStatus() { console.log("Req init status (needs backend)..."); }
function requestAgentStatus(agentId) { console.log(`Req status for ${agentId} (needs backend)...`); }


// --- Action Handlers ---
function handleSendMessage() {
    const messageText = messageInput?.value.trim() ?? '';
    // Use the global currentFile state variable managed by file handling functions
    if (!messageText && !currentFile) {
        console.log("Empty message and no file attached. Not sending.");
        return;
    }

    const messageToSend = {
        // Decide message type based on whether a file is attached
        type: currentFile ? 'user_message_with_file' : 'user_message',
        text: messageText,
    };
    if (currentFile && currentFileContent) { // Make sure content was read
        messageToSend.filename = currentFile.name;
        messageToSend.file_content = currentFileContent; // Send the stored content
    } else if (currentFile && !currentFileContent) {
         console.error("File selected but content is missing. Cannot send.");
         addMessage('system-log-area', '[UI Error] File content missing. Please re-attach.', 'error');
         clearFileInput(); // Clear the broken selection
         return;
    }

    // Display user message in conversation area
    const displayMessage = currentFile ? `[File: ${currentFile.name}]\n${messageText}` : messageText;
    addMessage('conversation-area', displayMessage, 'user');

    // Send to server
    sendMessageToServer(messageToSend);

    // Clear input and file selection
    if(messageInput) messageInput.value = '';
    clearFileInput(); // Use local clear function
}

// --- File Handling ---
// (handleFileSelect, displayFileInfo, clearFileInput remain unchanged)
function handleFileSelect(event) { const f = event.target.files[0]; if (!f) { clearFileInput(); return; } const t = ['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml']; const e = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log']; const x = '.' + f.name.split('.').pop().toLowerCase(); const v = t.includes(f.type) || e.includes(x); if (!v) { alert(`Unsupported type: ${f.type || x}. Upload text files.`); clearFileInput(); return; } const s = 1 * 1024 * 1024; if (f.size > s) { alert(`File too large (${(f.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`); clearFileInput(); return; } const r = new FileReader(); r.onload = (ev) => { currentFile = { name: f.name, size: f.size, type: f.type }; currentFileContent = ev.target.result; displayFileInfo(); console.log(`File "${f.name}" selected.`); }; r.onerror = (ev) => { console.error("File read error:", ev); alert("Error reading file."); clearFileInput(); }; r.readAsText(f); }
function displayFileInfo() { if (!fileInfoArea) return; if (currentFile) { fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`; } else { fileInfoArea.innerHTML = ''; } }
function clearFileInput() { currentFile = null; currentFileContent = null; if(fileInput) fileInput.value = ''; displayFileInfo(); console.log("File input cleared."); }

// --- The rest of the functions (UI updates, Modals, Config) are imported ---
// They are called either by the initialization sequence, event listeners, or handleWebSocketMessage.
