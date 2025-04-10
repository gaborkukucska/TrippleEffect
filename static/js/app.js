// START OF FILE static/js/app.js

// --- Global Variables ---
let ws = null; // WebSocket connection
const conversationArea = document.getElementById('conversation-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const systemLogArea = document.getElementById('system-log-area');
const agentStatusContent = document.getElementById('agent-status-content');
const configContent = document.getElementById('config-content');
const agentModal = document.getElementById('agent-modal');
const overrideModal = document.getElementById('override-modal');
const agentForm = document.getElementById('agent-form');
const overrideForm = document.getElementById('override-form');
const modalTitle = document.getElementById('modal-title');
const editAgentIdField = document.getElementById('edit-agent-id');
const agentIdField = document.getElementById('agent-id'); // For create/edit modal
const overrideAgentIdField = document.getElementById('override-agent-id'); // For override modal
const overrideMessage = document.getElementById('override-message');
const overrideLastError = document.getElementById('override-last-error');

// File Attachment Elements
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');
let attachedFileContent = null;
let attachedFileName = null;
let attachedFileSize = null;

// Session Management Elements
const projectSelect = document.getElementById('project-select');
const sessionSelect = document.getElementById('session-select');
const loadSessionButton = document.getElementById('load-session-button');
const saveProjectNameInput = document.getElementById('save-project-name');
const saveSessionNameInput = document.getElementById('save-session-name');
const saveSessionButton = document.getElementById('save-session-button');
const sessionStatusMessage = document.getElementById('session-status-message');

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed");
    updateLogStatus("Initializing...", false);

    if (!conversationArea || !messageInput || !sendButton || !systemLogArea || !agentStatusContent || !configContent || !agentModal || !overrideModal || !agentForm || !overrideForm || !modalTitle || !editAgentIdField || !agentIdField || !overrideAgentIdField || !overrideMessage || !overrideLastError || !fileInput || !attachFileButton || !fileInfoArea || !projectSelect || !sessionSelect || !loadSessionButton || !saveProjectNameInput || !saveSessionNameInput || !saveSessionButton || !sessionStatusMessage) {
        console.error("CRITICAL: One or more required DOM elements are missing!");
        updateLogStatus("Initialization Error: UI elements missing.", true);
        // Optionally display a user-facing error message here
        alert("Error initializing the application UI. Some elements are missing. Please check the HTML structure or contact support.");
        return; // Stop further execution if essential elements are missing
    }

    try {
        setupWebSocket();
        setupEventListeners();
        // Load initial data
        displayAgentConfigurations(); // Load static agent config
        loadProjects(); // Load project list for session management
        showView('chat-view'); // Start on chat view
        updateLogStatus("Initialization complete.", false);
    } catch (error) {
        console.error("Error during initialization:", error);
        updateLogStatus(`Initialization Error: ${error.message}`, true);
        alert(`An error occurred during initialization: ${error.message}`);
    }
});


// --- WebSocket Management ---
function setupWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting WebSocket connection to: ${wsUrl}`);
    updateLogStatus("Connecting...", false);

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("WebSocket connection established.");
        updateLogStatus("Connected", false);
        addMessage('system-log-area', '[system] Connected to TrippleEffect backend!', 'status');
        // Request current agent statuses upon connection
        // ws.send(JSON.stringify({ type: "get_status" })); // If backend supports this
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data); // Centralized handler
        } catch (error) {
            console.error("Error parsing WebSocket message or handling it:", error);
            addMessage('system-log-area', `[Error] Failed to process message: ${event.data}`, 'error');
        }
    };

    ws.onerror = (event) => {
        console.error("WebSocket error:", event);
        updateLogStatus("Connection Error", true);
        addMessage('system-log-area', '[system] WebSocket error occurred.', 'error');
    };

    ws.onclose = (event) => {
        console.log(`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`);
        updateLogStatus(`Disconnected (Code: ${event.code})`, true);
        addMessage('system-log-area', `[system] WebSocket closed (Code: ${event.code}). Attempting to reconnect...`, 'status');
        ws = null; // Clear the instance
        // Implement reconnection logic
        setTimeout(setupWebSocket, 5000); // Try to reconnect after 5 seconds
    };
}

// --- Centralized Message Handler ---
function handleWebSocketMessage(data) {
    // addRawLogEntry(data); // Keep for debugging if needed, but comment out for cleaner logs

    const messageType = data.type;
    const agentId = data.agent_id || 'system'; // Default to system if no agent ID

    switch (messageType) {
        case 'response_chunk':
            // *** FIX: Use addMessage to create bubble if needed, then append ***
            appendAgentResponseChunk(agentId, data.content);
            break;
        case 'final_response':
             // *** FIX: Use finalizeAgentResponse to update last bubble ***
             finalizeAgentResponse(agentId, data.content);
            break;
        case 'status':
             // Add general status messages to system log (can filter later if too noisy)
             addMessage('system-log-area', `[${agentId}] ${data.content || data.message || 'Status update'}`, 'status');
             break;
        case 'error':
            addMessage('conversation-area', `[${agentId} Error] ${data.content}`, 'error', agentId);
            addMessage('system-log-area', `[${agentId} Error] ${data.content}`, 'error'); // Also log errors in system log
            break;
        case 'request_user_override':
            showOverrideModal(data);
            break;
        case 'agent_status_update':
            updateAgentStatusUI(agentId, data.status);
            break;
        // --- Handle specific system events for cleaner logs ---
        case 'agent_added':
            addMessage('system-log-area', `[system] Agent Added: ${data.agent_id} (Persona: ${data.config?.persona || 'N/A'}, Team: ${data.team || 'N/A'})`, 'status');
            // Status UI update is handled by agent_status_update which should follow
            break;
        case 'agent_deleted':
            addMessage('system-log-area', `[system] Agent Deleted: ${data.agent_id}`, 'status');
            removeAgentStatusEntry(data.agent_id); // Remove from UI list
            break;
        case 'team_created':
            addMessage('system-log-area', `[system] Team Created: ${data.team_id}`, 'status');
            break;
        case 'team_deleted':
            addMessage('system-log-area', `[system] Team Deleted: ${data.team_id}`, 'status');
            break;
        case 'agent_moved_team':
             addMessage('system-log-area', `[system] Agent ${data.agent_id} moved from team ${data.old_team_id || 'N/A'} to ${data.new_team_id || 'N/A'}`, 'status');
             // Agent status UI update will handle the visual change
            break;
        case 'session_saved':
             addMessage('system-log-area', `[system] Session '${data.session}' saved in project '${data.project}'.`, 'status');
             // Update session management UI if needed (e.g., refresh lists)
             if (document.getElementById('session-view').classList.contains('active')) {
                 loadProjects(); // Refresh project/session lists
             }
             break;
         case 'session_loaded':
             addMessage('system-log-area', `[system] ${data.message || `Session '${data.session}' loaded from project '${data.project}'.`}`, 'status');
             // Clear conversation area? Or let agent status updates handle it?
             clearConversationArea(); // Clear old messages on load
             // Status UI updates happen via agent_status_update messages
             break;
         case 'system_event': // Generic catch-all for other system messages
             addMessage('system-log-area', `[system] ${data.message || data.event || 'System event received.'}`, 'status');
             break;
        default:
            console.warn("Received unhandled message type:", messageType, data);
            // Optionally log unknown types to system log for debugging
            addMessage('system-log-area', `[Unknown Type: ${messageType}] ${JSON.stringify(data)}`, 'status');
            // addRawLogEntry(data); // Fallback to raw log for truly unknown
    }
}

// --- UI Update Functions ---

/**
 * Adds a message bubble to the specified area.
 * Handles user prompts, agent responses (initial creation), status, and errors.
 * Manages scrolling behavior.
 */
function addMessage(areaId, text, type = 'status', agentId = 'system') {
    const area = document.getElementById(areaId);
    if (!area) return;

    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type); // e.g., 'message', 'user' or 'message', 'agent_response'
    messageDiv.dataset.agentId = agentId; // Store agent ID for potential styling/grouping

    // --- Refined Structure for Agent/User messages ---
    if (type === 'user' || type === 'agent_response') {
         // Add agent label for agent responses
        if (type === 'agent_response') {
             const labelSpan = document.createElement('span');
             labelSpan.classList.add('agent-label');
             labelSpan.textContent = agentId; // Display agent ID or persona
             messageDiv.appendChild(labelSpan);
        }
        // Add content span
        const contentSpan = document.createElement('span');
        contentSpan.classList.add('message-content');
        contentSpan.textContent = text; // Use textContent to prevent HTML injection
        messageDiv.appendChild(contentSpan);
    } else { // For status, error, log messages
        const contentSpan = document.createElement('span');
        // Add timestamp for system log messages
        if (areaId === 'system-log-area') {
            const timestampSpan = document.createElement('span');
            timestampSpan.classList.add('timestamp');
            timestampSpan.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ": ";
            messageDiv.appendChild(timestampSpan);
        }
        contentSpan.textContent = text; // Direct text for status/error
        messageDiv.appendChild(contentSpan);
    }


    // --- Scroll Control ---
    const isScrolledToBottom = area.scrollHeight - area.clientHeight <= area.scrollTop + 30; // Tolerance of 30px

    area.appendChild(messageDiv);

    // Auto-scroll only if the user was already near the bottom
    if (isScrolledToBottom) {
        area.scrollTop = area.scrollHeight;
    }
}


/**
 * Appends streaming text chunks to the *last* message bubble for a given agent.
 * Creates the initial message bubble via addMessage if it doesn't exist.
 */
function appendAgentResponseChunk(agentId, chunk) {
    if (!chunk) return; // Ignore empty chunks

    const area = conversationArea; // Always append to conversation area
    // Find the *last* message bubble belonging to this agent
    let lastAgentMessage = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);

    // If no message exists for this agent yet in this turn, create it
    if (!lastAgentMessage) {
        addMessage('conversation-area', '', 'agent_response', agentId); // Create with empty content first
        lastAgentMessage = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);
        if (!lastAgentMessage) { // Should not happen, but safeguard
             console.error(`Failed to create initial message bubble for agent ${agentId}`);
             return;
        }
    }

    const contentSpan = lastAgentMessage.querySelector('.message-content');
    if (contentSpan) {
        // --- Scroll Control ---
        const isScrolledToBottom = area.scrollHeight - area.clientHeight <= area.scrollTop + 30;

        contentSpan.textContent += chunk; // Append text content

        // Auto-scroll only if the user was already near the bottom
        if (isScrolledToBottom) {
            area.scrollTop = area.scrollHeight;
        }
    } else {
        console.error(`Could not find content span in message bubble for agent ${agentId}`);
    }
}

/**
 * Updates the content of the *last* message bubble for an agent when its response is complete.
 */
function finalizeAgentResponse(agentId, finalContent) {
    const area = conversationArea;
    // Find the *last* message bubble belonging to this agent
    const lastAgentMessage = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);

    if (lastAgentMessage) {
        const contentSpan = lastAgentMessage.querySelector('.message-content');
        if (contentSpan) {
            // If no chunks were received, set the final content directly
            if (contentSpan.textContent.length === 0 && finalContent) {
                 contentSpan.textContent = finalContent;
            }
             // Add a subtle visual cue that the response is finished (optional)
             // lastAgentMessage.style.borderRight = "3px solid var(--accent-color-green)";
        } else {
             console.error(`Could not find content span to finalize for agent ${agentId}`);
        }

        // --- Scroll Control (Optional: scroll one last time if needed) ---
         const isScrolledToBottom = area.scrollHeight - area.clientHeight <= area.scrollTop + 30;
         if (isScrolledToBottom) {
             area.scrollTop = area.scrollHeight;
         }

    } else {
        // If no message bubble exists at all, add the final response as a new message
        console.warn(`No existing message bubble found for agent ${agentId} during finalize. Adding as new message.`);
        addMessage('conversation-area', finalContent || "[Agent finished with empty response]", 'agent_response', agentId);
    }
}


function updateLogStatus(message, isError = false) {
    const statusElement = systemLogArea.querySelector('.initial-connecting');
    if (statusElement) {
        statusElement.textContent = message;
        statusElement.style.color = isError ? 'var(--accent-color-red)' : 'var(--text-color-secondary)';
        statusElement.style.fontWeight = isError ? 'bold' : 'normal';
    } else {
        // If the initial element is gone, add a new status message
        addMessage('system-log-area', message, isError ? 'error' : 'status');
    }
}

function updateAgentStatusUI(agentId, statusData) {
    addOrUpdateAgentStatusEntry(agentId, statusData);
}

function addOrUpdateAgentStatusEntry(agentId, statusData) {
    const existingEntry = document.getElementById(`agent-status-${agentId}`);
    const statusClass = `status-${statusData.status?.toLowerCase().replace(/\s+/g, '_') || 'unknown'}`;
    const teamDisplay = statusData.team ? ` [Team: ${statusData.team}]` : '';

    const persona = statusData.persona || agentId; // Use persona if available
    const modelInfo = statusData.model ? ` (${statusData.model})` : '';
    const toolInfo = statusData.current_tool ? ` | Tool: ${statusData.current_tool.name}` : '';

    // Clear placeholder if it exists
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();

    if (existingEntry) {
        // Update existing entry
        existingEntry.className = `agent-status-item ${statusClass}`; // Update status class
        existingEntry.innerHTML = `
            <span>
                <strong>${persona}</strong>
                <span class="agent-model">${modelInfo}</span>
                <span class="agent-team">${teamDisplay}</span>
                 <span>${toolInfo}</span>
             </span>
            <span class="agent-status">${statusData.status || 'Unknown'}</span>
        `;
    } else {
        // Create new entry
        const entry = document.createElement('div');
        entry.id = `agent-status-${agentId}`;
        entry.classList.add('agent-status-item', statusClass);
        entry.dataset.agentId = agentId; // Add agent ID as data attribute
        entry.innerHTML = `
             <span>
                 <strong>${persona}</strong>
                 <span class="agent-model">${modelInfo}</span>
                 <span class="agent-team">${teamDisplay}</span>
                 <span>${toolInfo}</span>
             </span>
            <span class="agent-status">${statusData.status || 'Unknown'}</span>
        `;
        agentStatusContent.appendChild(entry);
    }
}

function removeAgentStatusEntry(agentId) {
    const entry = document.getElementById(`agent-status-${agentId}`);
    if (entry) {
        entry.remove();
    }
    // Show placeholder if no agents are left
    if (agentStatusContent.children.length === 0) {
        const placeholder = document.createElement('span');
        placeholder.classList.add('status-placeholder');
        placeholder.textContent = "No active agents.";
        agentStatusContent.appendChild(placeholder);
    }
}

function clearConversationArea() {
     conversationArea.innerHTML = '<div class="message status initial-placeholder"><span>Conversation Area Cleared</span></div>';
}

// Debugging function (keep commented unless needed)
// function addRawLogEntry(data) {
//     console.log("Raw WS Data:", data);
//     const logEntry = document.createElement('div');
//     logEntry.classList.add('message', 'status'); // Style as status for now
//     logEntry.style.fontSize = '0.8em';
//     logEntry.style.color = 'grey';
//     logEntry.style.whiteSpace = 'pre-wrap'; // Keep formatting
//     logEntry.style.wordBreak = 'break-all'; // Break long strings
//     logEntry.textContent = `Raw/Error Data: ${JSON.stringify(data, null, 2)}`; // Pretty print JSON

//     // --- Scroll Control ---
//     const isScrolledToBottom = systemLogArea.scrollHeight - systemLogArea.clientHeight <= systemLogArea.scrollTop + 30;
//     systemLogArea.appendChild(logEntry);
//     if (isScrolledToBottom) {
//         systemLogArea.scrollTop = systemLogArea.scrollHeight;
//     }
// }

// --- Event Listeners ---
function setupEventListeners() {
    sendButton.addEventListener('click', handleSendMessage);
    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault(); // Prevent newline in textarea
            handleSendMessage();
        }
    });

    // File Attachment Listeners
    attachFileButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', handleFileSelect);

    // Config Button Listeners
    document.getElementById('add-agent-button')?.addEventListener('click', () => openModal('agent-modal'));
    document.getElementById('refresh-config-button')?.addEventListener('click', displayAgentConfigurations);

    // Modal Form Listeners
    agentForm.addEventListener('submit', handleSaveAgent);
    overrideForm.addEventListener('submit', handleSubmitOverride);

    // Close modals when clicking outside the content
    window.addEventListener('click', (event) => {
        if (event.target == agentModal) closeModal('agent-modal');
        if (event.target == overrideModal) closeModal('override-modal');
    });

    // Bottom Navigation Listener
    document.querySelectorAll('.nav-button').forEach(button => {
        button.addEventListener('click', () => {
            const viewId = button.getAttribute('data-view');
            showView(viewId);
            // Update active state
            document.querySelectorAll('.nav-button').forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
        });
    });

    // Session Management Listeners
    projectSelect?.addEventListener('change', () => {
        const selectedProject = projectSelect.value;
        if (selectedProject) {
            loadSessions(selectedProject);
            saveProjectNameInput.value = selectedProject; // Pre-fill save input
        } else {
            sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
            sessionSelect.disabled = true;
            loadSessionButton.disabled = true;
            saveProjectNameInput.value = '';
        }
    });

    sessionSelect?.addEventListener('change', () => {
        loadSessionButton.disabled = !sessionSelect.value; // Enable load button only if a session is selected
    });

    loadSessionButton?.addEventListener('click', handleLoadSession);
    saveSessionButton?.addEventListener('click', handleSaveSession);

}


// --- View Switching ---
function showView(viewId) {
    console.log(`Switching to view: ${viewId}`);
    document.querySelectorAll('.view-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    const activePanel = document.getElementById(viewId);
    if (activePanel) {
        activePanel.classList.add('active');
         // Refresh projects when switching to session view
         if (viewId === 'session-view') {
             loadProjects();
         }
    } else {
        console.error(`View panel with ID '${viewId}' not found.`);
    }
}

// --- Message Sending ---
function handleSendMessage() {
    const messageText = messageInput.value.trim();

    if (!messageText && !attachedFileContent) {
        console.log("No message or file to send.");
        return; // Don't send empty messages unless a file is attached
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('conversation-area', "[System] WebSocket not connected. Cannot send message.", 'error');
        console.error("WebSocket not connected.");
        return;
    }

    let messageToSend;

    if (attachedFileContent) {
         // Send structured message with file data
         messageToSend = JSON.stringify({
             type: "user_message_with_file",
             text: messageText,
             filename: attachedFileName,
             file_content: attachedFileContent // Send file content directly
         });
         addMessage('conversation-area', `[You - File: ${attachedFileName}]\n${messageText}`, 'user'); // Display user message with file indication
    } else {
         // Send plain text message
         messageToSend = messageText;
         addMessage('conversation-area', messageText, 'user'); // Display user message
    }

    ws.send(messageToSend);
    console.log("Message sent:", messageToSend.substring(0, 100) + "...");

    // Clear input and file attachment
    messageInput.value = '';
    clearFileInput();
}


// --- File Handling ---
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) {
        clearFileInput();
        return;
    }

    // Simple validation (e.g., size limit 1MB)
    const maxSize = 1 * 1024 * 1024; // 1 MB
    const allowedTypes = [
        'text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css',
        'application/json', 'application/x-yaml', 'text/x-python',
        'application/javascript', 'text/x-log' // Add more as needed
    ];

    // Check type (allow if no type is set, common in Termux/mobile)
    if (file.type && !allowedTypes.includes(file.type) && !file.name.match(/\.(txt|py|js|html|css|md|json|yaml|csv|log)$/i)) {
         alert(`File type not allowed: ${file.type || 'unknown'}. Allowed extensions: .txt, .py, .js, .html, .css, .md, .json, .yaml, .csv, .log`);
         clearFileInput();
         return;
    }
    if (file.size > maxSize) {
        alert(`File is too large (${(file.size / 1024 / 1024).toFixed(2)} MB). Maximum size is 1 MB.`);
        clearFileInput();
        return;
    }

    const reader = new FileReader();
    reader.onload = function(e) {
        attachedFileContent = e.target.result;
        attachedFileName = file.name;
        attachedFileSize = file.size;
        displayFileInfo();
        console.log(`File attached: ${attachedFileName}, Size: ${attachedFileSize} bytes`);
    };
    reader.onerror = function(e) {
        console.error("Error reading file:", e);
        alert("Error reading file.");
        clearFileInput();
    };
    reader.readAsText(file); // Read as text
}

function displayFileInfo() {
    if (attachedFileName) {
        fileInfoArea.innerHTML = `
            <span>📎 ${attachedFileName} (${(attachedFileSize / 1024).toFixed(1)} KB)</span>
            <button onclick="clearFileInput()" title="Remove file">×</button>
        `;
        fileInfoArea.style.display = 'flex';
    } else {
        fileInfoArea.style.display = 'none';
        fileInfoArea.innerHTML = '';
    }
}

function clearFileInput() {
    fileInput.value = null; // Clear the file input element
    attachedFileContent = null;
    attachedFileName = null;
    attachedFileSize = null;
    displayFileInfo();
    console.log("File attachment cleared.");
}


// --- Agent Configuration Management ---
async function displayAgentConfigurations() {
    configContent.innerHTML = '<span class="status-placeholder">Loading...</span>'; // Show loading state
    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) {
            throw new Error(`Failed to fetch agent configs: ${response.status} ${response.statusText}`);
        }
        const agents = await response.json();
        configContent.innerHTML = ''; // Clear loading/previous content

        if (agents.length === 0) {
            configContent.innerHTML = '<span class="status-placeholder">No static agents configured.</span>';
            return;
        }

        agents.forEach(agent => {
            const div = document.createElement('div');
            div.classList.add('config-item');
            div.innerHTML = `
                <span>
                    <strong>${agent.persona || agent.agent_id}</strong>
                    <span class="agent-details"> (${agent.agent_id} | ${agent.provider} | ${agent.model})</span>
                </span>
                <div class="config-item-actions">
                    <button class="config-action-button edit-button" data-id="${agent.agent_id}" title="Edit Agent">✏️</button>
                    <button class="config-action-button delete-button" data-id="${agent.agent_id}" title="Delete Agent">🗑️</button>
                </div>
            `;
            configContent.appendChild(div);
        });

        // Add event listeners after creating elements
        document.querySelectorAll('.edit-button').forEach(button => {
            button.addEventListener('click', (e) => openModal('agent-modal', e.target.closest('button').dataset.id));
        });
        document.querySelectorAll('.delete-button').forEach(button => {
            button.addEventListener('click', (e) => handleDeleteAgent(e.target.closest('button').dataset.id));
        });

    } catch (error) {
        console.error('Error fetching agent configurations:', error);
        configContent.innerHTML = `<span class="status-placeholder">Error loading config: ${error.message}</span>`;
    }
}

async function handleSaveAgent(event) {
    event.preventDefault();
    const agentId = editAgentIdField.value; // Check if we are editing
    const isEditing = !!agentId;
    const url = isEditing ? `/api/config/agents/${agentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';

    const formData = new FormData(agentForm);
    // Construct the nested structure API expects
    const agentData = {
        agent_id: formData.get('agent_id'), // Only needed for POST
        config: {
            persona: formData.get('persona'),
            provider: formData.get('provider'),
            model: formData.get('model'),
            temperature: parseFloat(formData.get('temperature')),
            system_prompt: formData.get('system_prompt')
        }
    };
    // Remove agent_id from top level for PUT
    if (isEditing) delete agentData.agent_id;

    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(isEditing ? agentData.config : agentData) // Send config directly for PUT
        });
        const result = await response.json();
        if (response.ok) {
            closeModal('agent-modal');
            displayAgentConfigurations(); // Refresh list
            alert(result.message || (isEditing ? 'Agent updated successfully!' : 'Agent created successfully! Restart required.'));
        } else {
            throw new Error(result.detail || `Failed to ${isEditing ? 'update' : 'create'} agent.`);
        }
    } catch (error) {
        console.error('Error saving agent:', error);
        alert(`Error: ${error.message}`);
    }
}

async function handleDeleteAgent(agentId) {
    if (!confirm(`Are you sure you want to delete static agent configuration '${agentId}'? This requires an application restart.`)) {
        return;
    }
    try {
        const response = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' });
        const result = await response.json();
        if (response.ok) {
            displayAgentConfigurations(); // Refresh list
            alert(result.message || 'Agent configuration deleted successfully! Restart required.');
        } else {
            throw new Error(result.detail || 'Failed to delete agent configuration.');
        }
    } catch (error) {
        console.error('Error deleting agent:', error);
        alert(`Error: ${error.message}`);
    }
}


// --- Modal Handling ---
function openModal(modalId, editId = null) {
    const modal = document.getElementById(modalId);
    if (!modal) {
         console.error(`Modal with ID ${modalId} not found.`);
         return;
    }

    if (modalId === 'agent-modal') {
        agentForm.reset(); // Clear previous data
        editAgentIdField.value = editId || ''; // Set hidden field
        const agentIdInput = agentForm.querySelector('#agent-id');
        const title = modal.querySelector('#modal-title');

        if (editId) {
            title.textContent = 'Edit Agent';
            agentIdInput.value = editId;
            agentIdInput.readOnly = true; // Prevent editing ID
            // Fetch existing config to pre-fill (optional but good UX)
            // This requires an endpoint like /api/config/agents/{agent_id}
            // For now, just set the ID and clear other fields
            agentForm.querySelector('#persona').value = '';
            agentForm.querySelector('#provider').value = 'openrouter';
            agentForm.querySelector('#model').value = '';
            agentForm.querySelector('#temperature').value = '0.7';
            agentForm.querySelector('#system_prompt').value = '';
            // TODO: Fetch and pre-fill data for edit
        } else {
            title.textContent = 'Add Agent';
            agentIdInput.readOnly = false;
        }
    }
    // Pre-fill override modal in showOverrideModal function

    modal.style.display = "block";
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.style.display = "none";
}

function showOverrideModal(data) {
    overrideAgentIdField.value = data.agent_id || '';
    overrideMessage.textContent = data.message || `Agent '${data.persona || data.agent_id}' encountered a persistent error.`;
    overrideLastError.textContent = data.last_error || '[No error details provided]';
    // Set default provider/model based on current agent config?
    overrideForm.querySelector('#override-provider').value = data.current_provider || 'openrouter';
    overrideForm.querySelector('#override-model').value = data.current_model || '';
    openModal('override-modal');
}

function handleSubmitOverride(event) {
    event.preventDefault();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        alert("WebSocket not connected. Cannot submit override.");
        return;
    }
    const overrideData = {
        type: "submit_user_override",
        agent_id: overrideAgentIdField.value,
        new_provider: overrideForm.querySelector('#override-provider').value,
        new_model: overrideForm.querySelector('#override-model').value.trim()
    };
    if (!overrideData.new_model) {
         alert("Please enter a new model name.");
         return;
    }

    ws.send(JSON.stringify(overrideData));
    console.log("User override submitted:", overrideData);
    closeModal('override-modal');
}

// --- Session Management API Calls ---
async function loadProjects() {
    if (!projectSelect) return;
    try {
        const response = await fetch('/api/projects');
        if (!response.ok) throw new Error('Failed to fetch projects');
        const projects = await response.json();

        projectSelect.innerHTML = '<option value="">-- Select Project --</option>'; // Clear existing options
        projects.forEach(project => {
            const option = document.createElement('option');
            option.value = project.project_name;
            option.textContent = project.project_name;
            projectSelect.appendChild(option);
        });
        // Reset session dropdown as well
        sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
        sessionSelect.disabled = true;
        loadSessionButton.disabled = true;
    } catch (error) {
        console.error('Error loading projects:', error);
        displaySessionStatus(`Error loading projects: ${error.message}`, true);
    }
}

async function loadSessions(projectName) {
     if (!sessionSelect || !loadSessionButton) return;
     sessionSelect.innerHTML = '<option value="">Loading sessions...</option>'; // Show loading state
     sessionSelect.disabled = true;
     loadSessionButton.disabled = true;

    try {
        const response = await fetch(`/api/projects/${projectName}/sessions`);
        if (!response.ok) throw new Error('Failed to fetch sessions');
        const sessions = await response.json();

        sessionSelect.innerHTML = '<option value="">-- Select Session --</option>'; // Clear loading/previous options
        if (sessions.length > 0) {
            sessions.forEach(session => {
                const option = document.createElement('option');
                option.value = session.session_name;
                option.textContent = session.session_name;
                sessionSelect.appendChild(option);
            });
             sessionSelect.disabled = false; // Enable dropdown
        } else {
             sessionSelect.innerHTML = '<option value="">-- No Sessions Found --</option>';
        }
    } catch (error) {
        console.error(`Error loading sessions for ${projectName}:`, error);
        sessionSelect.innerHTML = '<option value="">-- Error Loading --</option>';
        displaySessionStatus(`Error loading sessions: ${error.message}`, true);
    }
}

async function handleLoadSession() {
     if (!projectSelect || !sessionSelect) {
         console.error("Project or session select element missing.");
         return;
     }
    const projectName = projectSelect.value;
    const sessionName = sessionSelect.value;

    if (!projectName || !sessionName) {
        displaySessionStatus("Please select both a project and a session to load.", true);
        return;
    }

    displaySessionStatus("Loading session...", false); // Show loading indicator
    loadSessionButton.disabled = true; // Disable button during load

    try {
        const response = await fetch(`/api/projects/${projectName}/sessions/${sessionName}/load`, { method: 'POST' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Failed to load session');

        displaySessionStatus(result.message || `Session '${sessionName}' loaded successfully.`, false);
        // Optionally switch back to chat view after successful load?
        // showView('chat-view');
    } catch (error) {
        console.error('Error loading session:', error);
        displaySessionStatus(`Error loading session: ${error.message}`, true);
    } finally {
         // Re-enable button only if a session is still selected
         loadSessionButton.disabled = !sessionSelect.value;
    }
}

async function handleSaveSession() {
     if (!saveProjectNameInput || !saveSessionNameInput || !saveSessionButton) {
         console.error("Save session input elements missing.");
         return;
     }
    const projectName = saveProjectNameInput.value.trim();
    const sessionName = saveSessionNameInput.value.trim() || null; // Send null if empty

    if (!projectName) {
        displaySessionStatus("Project name is required to save.", true);
        return;
    }

    displaySessionStatus("Saving session...", false); // Show saving indicator
    saveSessionButton.disabled = true; // Disable button during save

    try {
         const payload = { session_name: sessionName }; // Wrap in object expected by API
        const response = await fetch(`/api/projects/${projectName}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Failed to save session');

        displaySessionStatus(result.message || `Session saved successfully in project '${projectName}'.`, false);
        // Refresh project/session lists if the current view is session view
        if (document.getElementById('session-view').classList.contains('active')) {
             loadProjects();
        }
        // Maybe clear the session name input after successful save?
        // saveSessionNameInput.value = '';

    } catch (error) {
        console.error('Error saving session:', error);
        displaySessionStatus(`Error saving session: ${error.message}`, true);
    } finally {
         saveSessionButton.disabled = false; // Re-enable button
    }
}

function displaySessionStatus(message, isError = false) {
     if (!sessionStatusMessage) return;
    sessionStatusMessage.textContent = message;
    sessionStatusMessage.className = `session-status ${isError ? 'error' : 'success'}`;
    sessionStatusMessage.style.display = 'block';

    // Optional: Hide message after a few seconds
    // setTimeout(() => {
    //     sessionStatusMessage.style.display = 'none';
    // }, 5000);
                                          }
