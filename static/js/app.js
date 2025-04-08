// START OF FILE static/js/app.js

// --- WebSocket Connection ---
let ws; // WebSocket instance
let reconnectInterval = 5000; // Reconnect attempt interval in ms
let currentFile = null; // To store the currently selected file object

// --- Swipe Navigation State ---
const pageContainer = document.getElementById('page-container');
const pages = document.querySelectorAll('.page');
const numPages = pages.length;
let currentPageIndex = 0; // Start at the main page (index 0)
let touchStartX = 0;
let touchCurrentX = 0;
let isSwiping = false;
let swipeThreshold = 50; // Minimum pixels to register as a swipe

// --- DOM Elements (Update selectors based on new structure) ---
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const agentStatusContent = document.getElementById('agent-status-content'); // Now on main page
const configContent = document.getElementById('config-content'); // Now on config page
const addAgentButton = document.getElementById('add-agent-button'); // On config page
const refreshConfigButton = document.getElementById('refresh-config-button'); // On config page

// File Attachment Elements
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');

// Modal Elements
const agentModal = document.getElementById('agent-modal');
const overrideModal = document.getElementById('override-modal');
const agentForm = document.getElementById('agent-form');
const overrideForm = document.getElementById('override-form');
const modalTitle = document.getElementById('modal-title');

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed");
    setupWebSocket();
    setupEventListeners();
    displayAgentConfigurations(); // Initial load attempt for config page
    updatePageTransform(); // Set initial page position
});

// --- WebSocket Setup ---
function setupWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connection established.');
        updateLogStatus('Connected to backend!', false); // Clear connecting message
        requestInitialAgentStatus(); // Request status on connect
    };

    ws.onmessage = (event) => {
        try {
            const messageData = JSON.parse(event.data);
            // console.log('Message from server:', messageData); // DEBUG: Log raw message
            handleWebSocketMessage(messageData);
        } catch (error) {
            console.error('Failed to parse WebSocket message:', error);
            addMessage('system-log-area', `[System Error] Received unparseable message: ${event.data}`, 'error');
        }
    };

    ws.onerror = (event) => {
        console.error('WebSocket error:', event);
        addMessage('system-log-area', '[WebSocket Error] Connection error occurred.', 'error');
        updateLogStatus('Connection Error. Retrying...', true);
    };

    ws.onclose = (event) => {
        console.log('WebSocket connection closed:', event.reason, `(Code: ${event.code})`);
        if (!event.wasClean) {
            addMessage('system-log-area', `[WebSocket Closed] Connection lost unexpectedly. Code: ${event.code}. Trying to reconnect...`, 'error');
        } else {
             addMessage('system-log-area', `[WebSocket Closed] Connection closed. Code: ${event.code}.`, 'status');
        }
        updateLogStatus('Disconnected. Retrying...', true);
        // Simple backoff mechanism
        setTimeout(setupWebSocket, reconnectInterval);
        reconnectInterval = Math.min(reconnectInterval * 1.5, 30000); // Increase delay up to 30s
    };
}

// --- WebSocket Message Handling ---
function handleWebSocketMessage(data) {
    // Always add raw log for debugging/transparency
    addRawLogEntry(data);

    switch (data.type) {
        case 'response_chunk':
            appendAgentResponseChunk(data.agent_id, data.content);
            break;
        case 'status':
        case 'system_event': // Handle general system events like save/load
            addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status update.'}`, 'status');
             // Also show initial connect message in conversation briefly? Maybe not needed.
            if (data.message === 'Connected to TrippleEffect backend!') {
                 // Do nothing specific here for now, status updated via updateLogStatus
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
        case 'agent_added': // Handle dynamic agent additions
             addMessage('system-log-area', `[System] Agent Added: ${data.agent_id} (Team: ${data.team || 'N/A'})`, 'status');
             addOrUpdateAgentStatusEntry(data.agent_id, { // Use the initial status provided
                agent_id: data.agent_id,
                persona: data.config?.persona || data.agent_id,
                status: data.config?.status || 'idle', // Assuming initial status is idle
                provider: data.config?.provider,
                model: data.config?.model,
                team: data.team
             });
            break;
        case 'agent_deleted': // Handle dynamic agent deletions
            addMessage('system-log-area', `[System] Agent Deleted: ${data.agent_id}`, 'status');
            removeAgentStatusEntry(data.agent_id);
            break;
        // Add cases for team_created, team_deleted, agent_moved_team if needed for UI updates
        case 'team_created':
            addMessage('system-log-area', `[System] Team Created: ${data.team_id}`, 'status');
            // Could update a team list UI element if one existed
            break;
        case 'team_deleted':
             addMessage('system-log-area', `[System] Team Deleted: ${data.team_id}`, 'status');
             // Could update a team list UI element if one existed
            break;
        case 'agent_moved_team':
             addMessage('system-log-area', `[System] Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status');
             // Trigger status update to reflect the change in the agent list
             requestAgentStatus(data.agent_id); // Request update for the specific agent
            break;
        case 'request_user_override':
             showOverrideModal(data);
             addMessage('system-log-area', `[System] User Override Required for Agent ${data.agent_id}`, 'error');
            break;
        default:
            console.warn('Received unknown message type:', data.type, data);
            addMessage('system-log-area', `[System] Received unhandled message type: ${data.type}`, 'status');
    }
}

function requestInitialAgentStatus() {
    // Can potentially send a message to backend asking for current status of all agents
    // ws.send(JSON.stringify({ type: "get_initial_status" }));
    console.log("Requesting initial agent status (not implemented in backend yet)");
}
function requestAgentStatus(agentId) {
    // Request update for a specific agent (e.g., after team move)
    // ws.send(JSON.stringify({ type: "get_agent_status", agent_id: agentId }));
    console.log(`Requesting status for agent ${agentId} (not implemented)`);
    // As a fallback, we rely on the regular status updates pushed by the manager
}

// --- UI Update Functions ---

/** Helper to add a message div to a specific area */
function addMessage(areaId, text, type = 'status', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) {
        console.error(`Cannot find message area with ID: ${areaId}`);
        return;
    }

    // Clear placeholder if it exists
    const placeholder = area.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();

    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type); // type = 'user', 'agent_response', 'status', 'error', 'tool-execution'
    if (agentId) {
        messageDiv.dataset.agentId = agentId; // Add data attribute for styling
        // Use agent ID directly for persona if needed, or fetch persona mapping
        // For simplicity, just show agent ID for now in system logs
        // messageDiv.textContent = `[@${agentId}] ${text}`;
    }

    // Add timestamp to system log messages
    if (areaId === 'system-log-area') {
        const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const timestampSpan = document.createElement('span');
        timestampSpan.classList.add('timestamp');
        timestampSpan.textContent = `[${timestamp}]`;
        messageDiv.appendChild(timestampSpan);
    }

     // Process text for basic formatting (newlines)
    const contentSpan = document.createElement('span');
    contentSpan.textContent = text; // Use textContent to prevent HTML injection
    messageDiv.appendChild(contentSpan);
    // Simple newline handling (replace \n with <br>)
    // messageDiv.innerHTML = messageDiv.innerHTML.replace(/\n/g, '<br>');

    area.appendChild(messageDiv);
    // Auto-scroll to the bottom
    area.scrollTop = area.scrollHeight;
}

/** Handles streaming chunks for agent responses */
function appendAgentResponseChunk(agentId, chunk) {
    const area = conversationArea; // Agent responses always go to conversation area
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);

    if (!agentMsgDiv) {
        // Clear placeholder if it exists
        const placeholder = area.querySelector('.initial-placeholder');
        if (placeholder) placeholder.remove();

        agentMsgDiv = document.createElement('div');
        agentMsgDiv.classList.add('message', 'agent_response', 'incomplete');
        agentMsgDiv.dataset.agentId = agentId;
        // Maybe add a small label?
        const label = document.createElement('strong');
        label.textContent = `Agent @${agentId}:\n`; // Add newline for clarity
        agentMsgDiv.appendChild(label);
        area.appendChild(agentMsgDiv);
    }

    // Append chunk content - use textContent to avoid XSS
    // Create a text node for the chunk and append it
    const chunkNode = document.createTextNode(chunk);
    agentMsgDiv.appendChild(chunkNode);

    area.scrollTop = area.scrollHeight;
}

/** Marks an agent's response as complete */
function finalizeAgentResponse(agentId, finalContent) {
    const area = conversationArea;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);

    if (agentMsgDiv) {
        // If streaming occurred, just remove the incomplete class
        agentMsgDiv.classList.remove('incomplete');
    } else if (finalContent) {
        // If no streaming happened (e.g., error or immediate response), add the full message
        addMessage('conversation-area', `Agent @${agentId}:\n${finalContent}`, 'agent_response', agentId);
    }
     area.scrollTop = area.scrollHeight;
}

/** Updates the status text in the system log area */
function updateLogStatus(message, isError = false) {
    const area = systemLogArea;
    if (!area) return;

    let statusDiv = area.querySelector('.status.initial-connecting');
    if (!statusDiv) { // If connecting message removed, look for any status message
        statusDiv = area.querySelector('.message.status:last-child');
    }
    // If still no status div, create one (shouldn't happen often)
     if (!statusDiv && message) {
          addMessage('system-log-area', message, isError ? 'error' : 'status');
     } else if (statusDiv) {
        statusDiv.textContent = message;
        statusDiv.className = `message status ${isError ? 'error' : ''}`; // Update class for styling
        if (message === 'Connected to backend!') {
            statusDiv.classList.remove('initial-connecting'); // Remove class if connected
        }
    }
}

/** Updates the dedicated Agent Status list UI */
function updateAgentStatusUI(agentId, statusData) {
    // agentStatusContent is the container in the main page
    if (!agentStatusContent) return;

    // Clear placeholder if present
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();

    addOrUpdateAgentStatusEntry(agentId, statusData);
}

function addOrUpdateAgentStatusEntry(agentId, statusData) {
     if (!agentStatusContent) return;
     let itemDiv = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);

     if (!itemDiv) {
         itemDiv = document.createElement('div');
         itemDiv.classList.add('agent-status-item');
         itemDiv.dataset.agentId = agentId;
         agentStatusContent.appendChild(itemDiv);
     }

     const persona = statusData.persona || agentId;
     const status = statusData.status || 'unknown';
     const provider = statusData.provider || 'N/A';
     const model = statusData.model || 'N/A';
     const team = statusData.team || 'None'; // Add team info

     // Add descriptive title attribute
     itemDiv.title = `ID: ${agentId}\nProvider: ${provider}\nModel: ${model}\nTeam: ${team}\nStatus: ${status}`;

     itemDiv.innerHTML = `
         <strong>${persona}</strong>
         <span class="agent-model">(${model})</span>
         <span>[Team: ${team}]</span>
         <span class="agent-status">${status.replace('_', ' ')}</span>
     `;

     // Update status class for styling
     itemDiv.className = `agent-status-item status-${status}`; // Use status for class name
}

function removeAgentStatusEntry(agentId) {
    if (!agentStatusContent) return;
    const itemDiv = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);
    if (itemDiv) {
        itemDiv.remove();
    }
     // If list becomes empty, add placeholder back?
    if (!agentStatusContent.hasChildNodes()) {
        agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>';
    }
}

/** Adds raw log entry to system log */
function addRawLogEntry(data) {
    // This is mostly for debugging - keep it minimal
    try {
        const logText = `[RAW] ${JSON.stringify(data)}`;
        // console.debug("Raw WS Data:", logText); // Log to console instead of UI spam
        // Optionally add to UI if needed for intense debugging:
        // addMessage('system-log-area', logText, 'status', 'RAW_DEBUG');
    } catch (e) {
        // Ignore if stringify fails
    }
}


// --- Event Listeners ---
function setupEventListeners() {
    // Send message on button click or Enter key
    sendButton.addEventListener('click', sendMessage);
    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault(); // Prevent default Enter behavior (newline)
            sendMessage();
        }
    });

    // File attachment listeners
    attachFileButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', handleFileSelect);

    // Config Page Listeners
    if(addAgentButton) addAgentButton.addEventListener('click', () => openModal('agent-modal'));
    if(refreshConfigButton) refreshConfigButton.addEventListener('click', () => window.location.reload()); // Simple refresh

    // Modal Form Listeners
    if(agentForm) agentForm.addEventListener('submit', handleSaveAgent);
    if(overrideForm) overrideForm.addEventListener('submit', handleSubmitOverride);

    // Swipe Navigation Listeners
    if (pageContainer) {
        pageContainer.addEventListener('touchstart', handleTouchStart, { passive: false });
        pageContainer.addEventListener('touchmove', handleTouchMove, { passive: false });
        pageContainer.addEventListener('touchend', handleTouchEnd);
    } else {
         console.error("Page container not found!");
    }
}

// --- Send Message Functionality ---
function sendMessage() {
    const messageText = messageInput.value.trim();

    if (!messageText && !currentFile) {
        return; // Don't send empty messages unless a file is attached
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('system-log-area', '[System Error] WebSocket is not connected. Cannot send message.', 'error');
        return;
    }

    const messageToSend = {
        type: currentFile ? 'user_message_with_file' : 'user_message',
        text: messageText,
    };

    if (currentFile) {
        messageToSend.filename = currentFile.name;
        messageToSend.file_content = currentFile.content; // Content was read in handleFileSelect
    }

    // Display user message in conversation area
    // Show file info if attached
    const displayMessage = currentFile
        ? `[File: ${currentFile.name}]\n${messageText}`
        : messageText;
    addMessage('conversation-area', displayMessage, 'user'); // Add user message to conversation

    // Send the message object
    ws.send(JSON.stringify(messageToSend));

    // Clear input and file selection
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

    // Basic validation (allow common text-based types)
    const allowedTypes = ['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml'];
    // Allow common code extensions even if MIME type isn't perfect
    const allowedExtensions = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv'];
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();

    if (!allowedTypes.includes(file.type) && !allowedExtensions.includes(fileExtension)) {
        alert(`Unsupported file type: ${file.type || fileExtension}. Please upload a text-based file.`);
        clearFileInput();
        return;
    }

    // Limit file size (e.g., 1MB)
    const maxSize = 1 * 1024 * 1024;
    if (file.size > maxSize) {
        alert(`File is too large (${(file.size / 1024 / 1024).toFixed(2)} MB). Maximum size is 1 MB.`);
        clearFileInput();
        return;
    }

    const reader = new FileReader();
    reader.onload = function(e) {
        currentFile = {
            name: file.name,
            content: e.target.result,
            size: file.size,
            type: file.type
        };
        displayFileInfo();
    }
    reader.onerror = function(e) {
        console.error("File reading error:", e);
        alert("Error reading file.");
        clearFileInput();
    }
    reader.readAsText(file); // Read as text
}

function displayFileInfo() {
    if (currentFile) {
        fileInfoArea.innerHTML = `
            <span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span>
            <button onclick="clearFileInput()" title="Remove file">×</button>
        `;
    } else {
        fileInfoArea.innerHTML = '';
    }
}

function clearFileInput() {
    currentFile = null;
    fileInput.value = ''; // Clear the actual file input element
    displayFileInfo();
}

// --- Swipe Navigation Handlers ---
function handleTouchStart(event) {
    // Don't swipe if interacting with input elements or modals are open
    const targetTagName = event.target.tagName.toLowerCase();
    if (targetTagName === 'textarea' || targetTagName === 'input' || targetTagName === 'button' || targetTagName === 'select' || agentModal.style.display !== 'none' || overrideModal.style.display !== 'none') {
        return;
    }
     // Prevent default behavior like text selection or page scroll only if swiping starts
    // event.preventDefault(); // Be careful with this, might disable scrolling inside pages

    touchStartX = event.touches[0].clientX;
    touchCurrentX = touchStartX; // Initialize current position
    isSwiping = true;
    // Optionally, disable transition during swipe for direct feedback
    pageContainer.style.transition = 'none';
}

function handleTouchMove(event) {
    if (!isSwiping) return;

    touchCurrentX = event.touches[0].clientX;
    const diffX = touchCurrentX - touchStartX;

    // Check if horizontal movement is significant compared to vertical
    // (Simple check, more complex logic might be needed for robustness)
     if (Math.abs(diffX) > 10) { // Only prevent vertical scroll if moving horizontally
         event.preventDefault(); // Prevent vertical scroll WHILE swiping
     }


    // Apply visual feedback by moving the container (relative to current page)
    const baseTranslateX = -currentPageIndex * 100; // Base percentage offset
    pageContainer.style.transform = `translateX(calc(${baseTranslateX}vw + ${diffX}px))`;
}

function handleTouchEnd(event) {
    if (!isSwiping) return;
    isSwiping = false;

    const diffX = touchCurrentX - touchStartX;

    // Re-enable transition for smooth snap-back or page change
    pageContainer.style.transition = 'transform 0.3s ease-in-out';

    if (Math.abs(diffX) > swipeThreshold) {
        // Swipe detected - change page index
        if (diffX < 0 && currentPageIndex < numPages - 1) {
            // Swipe Left
            currentPageIndex++;
        } else if (diffX > 0 && currentPageIndex > 0) {
            // Swipe Right
            currentPageIndex--;
        }
    }
    // Else: Snap back to current page (handled by updatePageTransform)

    updatePageTransform(); // Update transform based on final currentPageIndex
}

function updatePageTransform() {
    if (pageContainer) {
        pageContainer.style.transform = `translateX(-${currentPageIndex * 100}vw)`;
    }
}

// Allow navigation via keyboard (for testing/accessibility)
document.addEventListener('keydown', (e) => {
    // Ignore key presses if modals are open or focus is on input fields
    const targetTagName = document.activeElement.tagName.toLowerCase();
     if (agentModal.style.display !== 'none' || overrideModal.style.display !== 'none' || targetTagName === 'textarea' || targetTagName === 'input' || targetTagName === 'select') {
         return;
     }

    if (e.key === 'ArrowLeft' && currentPageIndex > 0) {
        currentPageIndex--;
        pageContainer.style.transition = 'transform 0.3s ease-in-out'; // Ensure transition
        updatePageTransform();
    } else if (e.key === 'ArrowRight' && currentPageIndex < numPages - 1) {
        currentPageIndex++;
        pageContainer.style.transition = 'transform 0.3s ease-in-out'; // Ensure transition
        updatePageTransform();
    }
});


// --- Configuration Management UI ---

async function displayAgentConfigurations() {
    // Config content is now on the config page
    if (!configContent) {
         console.warn("Config content area not found (likely not on current page view, but trying to fetch).");
         // Continue fetching, but updates won't be visible until page is viewed
    } else {
         configContent.innerHTML = '<span class="status-placeholder">Loading...</span>'; // Show loading state
    }

    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const agents = await response.json();

        // Update config content even if not currently visible
        if(configContent) {
            configContent.innerHTML = ''; // Clear previous content
            if (agents.length === 0) {
                configContent.innerHTML = '<span class="status-placeholder">No static agents configured.</span>';
                return;
            }
            agents.forEach(agent => {
                const item = document.createElement('div');
                item.classList.add('config-item');
                item.innerHTML = `
                    <span>
                        <strong>${agent.persona || agent.agent_id}</strong> (${agent.agent_id})
                        <span class="agent-details">- ${agent.provider} / ${agent.model}</span>
                    </span>
                    <div class="config-item-actions">
                        <button class="config-action-button edit-button" data-id="${agent.agent_id}" title="Edit">✏️</button>
                        <button class="config-action-button delete-button" data-id="${agent.agent_id}" title="Delete">🗑️</button>
                    </div>
                `;
                configContent.appendChild(item);

                // Add event listeners for edit/delete
                item.querySelector('.edit-button').addEventListener('click', () => openModal('agent-modal', agent.agent_id));
                item.querySelector('.delete-button').addEventListener('click', () => handleDeleteAgent(agent.agent_id));
            });
        } else {
             console.warn("Config content area not found to display fetched configurations.");
        }

    } catch (error) {
        console.error('Error fetching agent configurations:', error);
        if(configContent) configContent.innerHTML = '<span class="status-placeholder error">Error loading configuration.</span>';
        addMessage('system-log-area', `[UI Error] Failed to fetch config: ${error}`, 'error');
    }
}

async function handleSaveAgent(event) {
    event.preventDefault();
    const form = event.target;
    const agentId = form.querySelector('#agent-id').value.trim();
    const editAgentId = form.querySelector('#edit-agent-id').value; // Get the hidden ID
    const isEditing = !!editAgentId; // Check if we are editing

    if (!agentId) {
        alert("Agent ID is required.");
        return;
    }
    if (!/^[a-zA-Z0-9_-]+$/.test(agentId)) {
        alert("Agent ID can only contain letters, numbers, underscores, and hyphens.");
        return;
    }

    const agentConfig = {
        provider: form.querySelector('#provider').value,
        model: form.querySelector('#model').value.trim(),
        persona: form.querySelector('#persona').value.trim() || agentId, // Default persona to agentId
        temperature: parseFloat(form.querySelector('#temperature').value) || 0.7,
        system_prompt: form.querySelector('#system_prompt').value.trim() || 'You are a helpful assistant.',
        // Add logic to parse extra args if implemented
    };

    // --- Determine API endpoint and method ---
    const url = isEditing ? `/api/config/agents/${editAgentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';

    // --- Prepare data payload ---
    const payload = isEditing
        ? agentConfig // For PUT, just send the config object
        : { agent_id: agentId, config: agentConfig }; // For POST, wrap config

    console.log(`Sending ${method} request to ${url} with payload:`, payload);

    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (response.ok && result.success) {
            alert(result.message || `Agent ${isEditing ? 'updated' : 'added'} successfully. Restart required.`);
            closeModal('agent-modal');
            displayAgentConfigurations(); // Refresh the list
        } else {
            throw new Error(result.detail || result.message || `Failed to ${isEditing ? 'update' : 'add'} agent.`);
        }
    } catch (error) {
        console.error(`Error ${isEditing ? 'updating' : 'adding'} agent:`, error);
        alert(`Error: ${error.message}`);
        addMessage('system-log-area', `[UI Error] Failed to save agent config: ${error.message}`, 'error');
    }
}


async function handleDeleteAgent(agentId) {
    if (!confirm(`Are you sure you want to delete agent '${agentId}'? This requires an application restart.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' });
        const result = await response.json();

        if (response.ok && result.success) {
            alert(result.message || 'Agent deleted successfully. Restart required.');
            displayAgentConfigurations(); // Refresh the list
        } else {
             throw new Error(result.detail || result.message || 'Failed to delete agent.');
        }
    } catch (error) {
        console.error('Error deleting agent:', error);
        alert(`Error deleting agent '${agentId}': ${error.message}`);
        addMessage('system-log-area', `[UI Error] Failed to delete agent config: ${error.message}`, 'error');
    }
}


// --- Modal Handling ---

async function openModal(modalId, editId = null) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    if (modalId === 'agent-modal') {
        const form = modal.querySelector('#agent-form');
        const title = modal.querySelector('#modal-title');
        const agentIdInput = form.querySelector('#agent-id');
        const editAgentIdInput = form.querySelector('#edit-agent-id');

        form.reset(); // Clear previous values
        editAgentIdInput.value = ''; // Clear edit ID
        agentIdInput.disabled = false; // Enable ID input by default

        if (editId) {
            title.textContent = `Edit Agent: ${editId}`;
            editAgentIdInput.value = editId;
            agentIdInput.value = editId;
            agentIdInput.disabled = true; // Disable ID editing

            // Fetch existing config to pre-fill the form
            try {
                const response = await fetch('/api/config/agents'); // Fetch all configs
                if (!response.ok) throw new Error('Failed to fetch agent list');
                const agents = await response.json();
                const agentData = agents.find(a => a.agent_id === editId);

                if (!agentData) throw new Error(`Agent config for ${editId} not found.`);

                // Need to fetch the full config for the specific agent (API might need enhancement)
                // For now, we only have basic info from the list endpoint. Let's prefill what we can.
                // We'll assume the structure returned by /api/config/agents includes basic config keys
                // Or ideally, enhance the list endpoint or add a GET /api/config/agents/{agent_id} endpoint

                 // --- TEMPORARY: Prefill only basic fields available from list ---
                 // This needs backend support for GET /api/config/agents/{id} to be robust
                 const configResponse = await fetch('/api/config/agents');
                 const allAgents = await configResponse.json();
                 const agentFullConfigEntry = allAgents.find(a => a.agent_id === editId);

                 if (agentFullConfigEntry) {
                    // Assuming the list endpoint includes necessary details or fetch details separately
                    // const detailsResponse = await fetch(`/api/config/agents/${editId}`); // Needs backend endpoint
                    // const details = await detailsResponse.json();
                    // const config = details.config; // Assuming endpoint returns full config

                    // Using only list data for now:
                    form.querySelector('#persona').value = agentFullConfigEntry.persona || editId;
                    form.querySelector('#provider').value = agentFullConfigEntry.provider || 'openrouter';
                    form.querySelector('#model').value = agentFullConfigEntry.model || '';
                    // Cannot prefill temp or prompt reliably from list endpoint
                    // form.querySelector('#temperature').value = config.temperature || 0.7;
                    // form.querySelector('#system_prompt').value = config.system_prompt || '';
                     console.warn("Prefilling edit form with limited data from list endpoint.");

                 } else {
                     alert(`Could not find config for agent ${editId}.`)
                     return; // Don't open modal if data fetch fails
                 }


            } catch (error) {
                console.error("Error fetching agent data for edit:", error);
                alert(`Error loading agent data: ${error.message}`);
                return; // Don't open modal if data fetch fails
            }

        } else {
            title.textContent = 'Add New Agent';
             // Set defaults for adding
            form.querySelector('#temperature').value = 0.7;
            form.querySelector('#persona').value = '';
            form.querySelector('#system_prompt').value = 'You are a helpful assistant.';
            form.querySelector('#provider').value = 'openrouter'; // Default provider
            form.querySelector('#model').value = ''; // Clear model
        }
    }

    modal.style.display = 'block';
}


function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
        // Optional: Reset forms inside modal when closed
        const form = modal.querySelector('form');
        if(form) form.reset();
         if(modalId === 'agent-modal') {
             document.getElementById('edit-agent-id').value = '';
             document.getElementById('agent-id').disabled = false;
         }
    }
}

// Close modal if clicked outside the content area
window.onclick = function(event) {
    if (event.target.classList.contains('modal')) {
        closeModal(event.target.id);
    }
}

// --- Override Modal Specific ---
function showOverrideModal(data) {
    const agentId = data.agent_id;
    const persona = data.persona || agentId;
    const currentProvider = data.current_provider;
    const currentModel = data.current_model;
    const lastError = data.last_error || "Unknown error";

    document.getElementById('override-agent-id').value = agentId;
    document.getElementById('override-modal-title').textContent = `Override for Agent: ${persona}`;
    document.getElementById('override-message').textContent = data.message || `Agent '${persona}' (${agentId}) failed. Please provide an alternative.`;
    document.getElementById('override-last-error').textContent = lastError;

    // Pre-select current provider/model if possible
    const providerSelect = document.getElementById('override-provider');
    const modelInput = document.getElementById('override-model');
    if (currentProvider) providerSelect.value = currentProvider;
    if (currentModel) modelInput.value = currentModel;

    openModal('override-modal');
}

function handleSubmitOverride(event) {
    event.preventDefault();
    const agentId = document.getElementById('override-agent-id').value;
    const newProvider = document.getElementById('override-provider').value;
    const newModel = document.getElementById('override-model').value.trim();

    if (!agentId || !newProvider || !newModel) {
        alert("Please fill in all override fields.");
        return;
    }

    const overrideData = {
        type: "submit_user_override",
        agent_id: agentId,
        new_provider: newProvider,
        new_model: newModel
    };

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(overrideData));
        addMessage('system-log-area', `[UI Action] Submitted override for Agent ${agentId} (Provider: ${newProvider}, Model: ${newModel}).`, 'status');
        closeModal('override-modal');
    } else {
        alert("WebSocket is not connected. Cannot submit override.");
        addMessage('system-log-area', `[UI Error] Failed to submit override for ${agentId}: WebSocket not connected.`, 'error');
    }
}
