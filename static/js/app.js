// START OF FILE static/js/app.js

// --- Global Variables ---
let ws = null; // WebSocket connection instance
let currentView = 'chat-view'; // Default view
let attachedFile = { name: null, content: null, size: null }; // Store attached file info
const MAX_FILE_SIZE_MB = 5;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

// --- DOM Elements (Cached on Load) ---
let messageInput, sendButton, conversationArea, systemLogArea, logStatus,
    agentStatusContent, bottomNavButtons, views, fileInfoArea, fileInput,
    attachFileButton, agentModal, agentForm, modalTitle, editAgentIdInput,
    overrideModal, overrideForm, overrideAgentIdInput, overrideMessageP,
    overrideLastErrorCode, overrideProviderSelect, overrideModelInput,
    configContent, refreshConfigButton, addAgentButton;

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed");

    // Cache DOM elements
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    conversationArea = document.getElementById('conversation-area');
    systemLogArea = document.getElementById('system-log-area');
    logStatus = document.querySelector('#logs-view .message.status.initial-connecting'); // Target specific status line
    agentStatusContent = document.getElementById('agent-status-content');
    bottomNavButtons = document.querySelectorAll('.nav-button');
    views = document.querySelectorAll('.view-panel');
    fileInfoArea = document.getElementById('file-info-area');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    agentModal = document.getElementById('agent-modal');
    agentForm = document.getElementById('agent-form');
    modalTitle = document.getElementById('modal-title');
    editAgentIdInput = document.getElementById('edit-agent-id'); // Hidden input for editing
    overrideModal = document.getElementById('override-modal');
    overrideForm = document.getElementById('override-form');
    overrideAgentIdInput = document.getElementById('override-agent-id');
    overrideMessageP = document.getElementById('override-message');
    overrideLastErrorCode = document.getElementById('override-last-error');
    overrideProviderSelect = document.getElementById('override-provider');
    overrideModelInput = document.getElementById('override-model');
    configContent = document.getElementById('config-content');
    refreshConfigButton = document.getElementById('refresh-config-button');
    addAgentButton = document.getElementById('add-agent-button');


    // Check if essential elements are found
    if (!messageInput || !sendButton || !conversationArea || !systemLogArea || !logStatus || !agentStatusContent) {
        console.error("Initialization Error: One or more essential DOM elements are missing.");
        // Display error to user?
        document.body.innerHTML = "<h1>Initialization Error</h1><p>Could not find essential page elements. Please reload or contact support.</p>";
        return;
    }

    // Setup WebSocket
    setupWebSocket();

    // Setup Event Listeners
    setupEventListeners();

    // Initial Setup
    showView(currentView); // Show the default view
    clearFileInput(); // Ensure file input is clear on load
    displayAgentConfigurations(); // Load initial config view

    console.log("Initialization complete.");
});

// --- WebSocket Management ---
function setupWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting to connect WebSocket to: ${wsUrl}`);
    updateLogStatus("Connecting...", false);

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("WebSocket connection established.");
        updateLogStatus("Connected", false);
        active_connections = []; // Reset active connections (client-side tracking if needed)
        // Optional: Request initial agent status after connection
        // requestInitialAgentStatus();
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            addRawLogEntry(data); // Log raw data for debugging
            handleWebSocketMessage(data); // Process the message
        } catch (error) {
            console.error("Error parsing WebSocket message or handling data:", error);
            // Handle non-JSON messages or errors
            addMessage('system-log-area', `Raw/Error Data: ${event.data}`, 'error');
        }
    };

    ws.onerror = (error) => {
        console.error("WebSocket Error:", error);
        updateLogStatus("Connection Error", true);
        addMessage('system-log-area', 'WebSocket connection error occurred.', 'error');
    };

    ws.onclose = (event) => {
        console.log(`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`);
        updateLogStatus(`Disconnected (Code: ${event.code})`, true);
        addMessage('system-log-area', `WebSocket connection closed. Trying to reconnect in 5 seconds...`, 'error');
        // Implement reconnection logic
        setTimeout(setupWebSocket, 5000);
    };
}

function handleWebSocketMessage(data) {
    const agentId = data.agent_id || 'system'; // Default to system if no agent_id

    switch (data.type) {
        case 'response_chunk':
            appendAgentResponseChunk(agentId, data.content);
            break;
        case 'final_response':
            finalizeAgentResponse(agentId, data.content);
            break;
        case 'status':
        case 'system_event': // Treat system events like status messages for logging
            addMessage('system-log-area', `[${agentId}] ${data.content || data.message || 'Status update'}`, 'status');
            // Also update agent status list if it's an agent-specific status message
             if (data.type === 'status' && data.agent_id && data.content?.startsWith("Configuration updated")) {
                 // Trigger a full status refresh if config changed
                 updateAgentStatusUI(agentId, { /* Need full status data here */ }); // Might need backend to send full status after override success
             }
            break;
        case 'error':
            addMessage('system-log-area', `[ERROR - ${agentId}] ${data.content}`, 'error');
            // If the error is agent-specific, reflect it in the status list
            if(data.agent_id) {
                updateAgentStatusUI(agentId, { status: 'error' }); // Update status to error
            }
            break;
        case 'agent_status_update':
            // Handles updates for a specific agent's status, potentially including team changes
            updateAgentStatusUI(data.agent_id, data.status); // Pass the whole status object
            break;
        case 'agent_added':
            // *** NEW: Handle agent addition ***
            console.log("Agent Added Event:", data);
            addMessage('system-log-area', `[System] Agent Added: ${data.agent_id} (Persona: ${data.config?.persona || 'N/A'}, Team: ${data.team || 'N/A'})`, 'status log-agent-message');
            // Create/Update the status entry using the provided config and team
            addOrUpdateAgentStatusEntry(data.agent_id, {
                persona: data.config?.persona,
                model: data.config?.model,
                provider: data.config?.provider,
                status: 'idle', // Assume idle initially
                team: data.team || 'N/A'
            });
            break;
        case 'agent_deleted':
            // *** NEW: Handle agent deletion ***
             console.log("Agent Deleted Event:", data);
            addMessage('system-log-area', `[System] Agent Deleted: ${data.agent_id}`, 'status log-agent-message');
            removeAgentStatusEntry(data.agent_id);
            break;
         case 'team_created':
             addMessage('system-log-area', `[System] Team Created: ${data.team_id}`, 'status log-agent-message');
             // Potentially update UI elements related to teams if added later
             break;
         case 'team_deleted':
             addMessage('system-log-area', `[System] Team Deleted: ${data.team_id}`, 'status log-agent-message');
             // Potentially update UI elements related to teams
             break;
         case 'agent_moved_team': // Could be handled by agent_status_update if it includes team
             addMessage('system-log-area', `[System] Agent ${data.agent_id} moved to Team: ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status log-agent-message');
             // Status update should handle the visual change via addOrUpdateAgentStatusEntry
             break;
        case 'request_user_override':
            showOverrideModal(data);
            break;
        default:
            addMessage('system-log-area', `Unknown message type: ${data.type} from ${agentId}`, 'status');
            console.warn("Received unknown WebSocket message type:", data);
    }
}

// --- UI Update Functions ---

function updateLogStatus(message, isError = false) {
    if (logStatus) {
        logStatus.textContent = message;
        logStatus.style.color = isError ? 'var(--accent-color-red)' : 'var(--text-color-secondary)';
        logStatus.style.fontWeight = isError ? 'bold' : 'normal';
        // Make sure it's visible if it was hidden
        logStatus.classList.remove("initial-placeholder");
    }
}

function addMessage(areaId, text, type = 'info', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) {
        console.error(`addMessage Error: Area with ID '${areaId}' not found.`);
        return;
    }

    // Remove placeholder if it exists
    const placeholder = area.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();

    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type); // Add base 'message' class and specific type class

    const contentSpan = document.createElement('span');

    // Add timestamp for system logs only
    if (areaId === 'system-log-area') {
        const timestampSpan = document.createElement('span');
        timestampSpan.classList.add('timestamp');
        timestampSpan.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ':';
        messageDiv.appendChild(timestampSpan);
    }

    // Specific class based on log type for potential styling
    if (text.includes("Executing tool")) contentSpan.classList.add('log-tool-use');
    if (text.includes("Received message from") || text.includes("Sending message to") || text.includes("Agent Added") || text.includes("Agent Deleted")) contentSpan.classList.add('log-agent-message');

    contentSpan.textContent = text;
    messageDiv.appendChild(contentSpan);

    // Add data attribute if agentId is provided
    if (agentId) {
        messageDiv.dataset.agentId = agentId;
         // Add specific styling for user/agent in conversation
         if (areaId === 'conversation-area') {
             if (type === 'user') messageDiv.classList.add('user');
             else messageDiv.classList.add('agent_response'); // Default to agent styling if not user
        }
    }

    // Add class for tool execution status messages
    if (type === 'status' && text.includes("Executing tool")) {
         messageDiv.classList.add('tool-execution');
    }

    area.appendChild(messageDiv);
    area.scrollTop = area.scrollHeight; // Scroll to bottom
}


function appendAgentResponseChunk(agentId, chunk) {
    if (!conversationArea) return;
    const agentMessageId = `agent-msg-${agentId}`;
    let agentMsgDiv = conversationArea.querySelector(`#${agentMessageId}`);

    if (!agentMsgDiv) {
        // Create the message container if it doesn't exist
        agentMsgDiv = document.createElement('div');
        agentMsgDiv.classList.add('message', 'agent_response'); // Base + agent styling
        agentMsgDiv.id = agentMessageId;
        agentMsgDiv.dataset.agentId = agentId; // Add data attribute

        const labelSpan = document.createElement('span');
        labelSpan.classList.add('agent-label');
        // Use agentId as fallback label if persona not yet known
        labelSpan.textContent = `Agent @${agentId}:`;
        agentMsgDiv.appendChild(labelSpan);

        const contentSpan = document.createElement('span');
        contentSpan.classList.add('message-content');
        contentSpan.textContent = chunk; // Start with the first chunk
        agentMsgDiv.appendChild(contentSpan);

        conversationArea.appendChild(agentMsgDiv);
        // Update label if persona is available from status updates
        const statusEntry = agentStatusContent?.querySelector(`#agent-status-${agentId}`);
        if(statusEntry) {
            labelSpan.textContent = statusEntry.dataset.persona || `Agent @${agentId}:`;
        }

    } else {
        // Append chunk to existing content span
        const contentSpan = agentMsgDiv.querySelector('.message-content');
        if (contentSpan) {
            contentSpan.textContent += chunk;
        }
    }
    conversationArea.scrollTop = conversationArea.scrollHeight;
}

function finalizeAgentResponse(agentId, finalContent) {
     if (!conversationArea) return;
     const agentMessageId = `agent-msg-${agentId}`;
     let agentMsgDiv = conversationArea.querySelector(`#${agentMessageId}`);

     if (agentMsgDiv) {
         // Message div exists (streamed chunks received) - mark as complete? (Optional)
         agentMsgDiv.classList.add('finalized');
         // Optionally update content if finalContent differs significantly (rare)
         const contentSpan = agentMsgDiv.querySelector('.message-content');
         if (contentSpan && contentSpan.textContent !== finalContent) {
             // console.warn(`Final content for ${agentId} differs from streamed content. Updating.`);
             // contentSpan.textContent = finalContent; // Uncomment to force update
         }
     } else {
         // No chunks were received, add the whole message now
         addMessage('conversation-area', finalContent, 'agent_response', agentId);
         // Try to set the label correctly if status is available
         const newMsgDiv = conversationArea.querySelector(`#agent-msg-${agentId}`); // Should exist now
         const statusEntry = agentStatusContent?.querySelector(`#agent-status-${agentId}`);
         const labelSpan = newMsgDiv?.querySelector('.agent-label');
         if(newMsgDiv && labelSpan && statusEntry) {
             labelSpan.textContent = statusEntry.dataset.persona || `Agent @${agentId}:`;
         }
     }
     conversationArea.scrollTop = conversationArea.scrollHeight;
 }


function updateAgentStatusUI(agentId, statusData) {
    // Central function to call the update/add logic
    addOrUpdateAgentStatusEntry(agentId, statusData);
}

// --- *** MODIFIED to include team *** ---
function addOrUpdateAgentStatusEntry(agentId, statusData) {
    if (!agentStatusContent) return;

    // Remove placeholder if it's the first agent being added
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();

    let entry = agentStatusContent.querySelector(`#agent-status-${agentId}`);
    const status = statusData.status || 'unknown';
    const persona = statusData.persona || `Agent @${agentId}`; // Use persona if available
    const modelInfo = statusData.model ? `(${statusData.provider || 'N/A'}/${statusData.model})` : '';
    // --- Get team info ---
    const teamInfo = statusData.team ? `[Team: ${statusData.team}]` : '[Team: N/A]';

    if (!entry) {
        // Create new entry
        entry = document.createElement('div');
        entry.id = `agent-status-${agentId}`;
        entry.classList.add('agent-status-item');
        agentStatusContent.appendChild(entry);
        console.log(`Added status entry for ${agentId}`);
    }

    // Update content and classes
    entry.className = `agent-status-item status-${status.toLowerCase().replace(/\s+/g, '_')}`; // Update classes based on status
    // Store persona for potential use in conversation label update
    entry.dataset.persona = persona;

    // --- Updated innerHTML to include team ---
    entry.innerHTML = `
        <span>
            <strong>${persona}</strong>
            <span class="agent-model">${modelInfo}</span>
            <span class="agent-team">${teamInfo}</span>
        </span>
        <span class="agent-status">${status}</span>
    `;
}
// --- *** END MODIFICATION *** ---

function removeAgentStatusEntry(agentId) {
    if (!agentStatusContent) return;
    const entry = agentStatusContent.querySelector(`#agent-status-${agentId}`);
    if (entry) {
        entry.remove();
        console.log(`Removed status entry for ${agentId}`);
    }
    // Add placeholder back if list becomes empty
    if (agentStatusContent.children.length === 0) {
        const placeholder = document.createElement('span');
        placeholder.classList.add('status-placeholder');
        placeholder.textContent = 'No active agents.';
        agentStatusContent.appendChild(placeholder);
    }
}


function addRawLogEntry(data) {
    console.debug("WebSocket Received:", data);
}

// --- Event Listeners ---
function setupEventListeners() {
    // Send Button
    sendButton?.addEventListener('click', handleSendMessage);

    // Message Input (Enter Key)
    messageInput?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault(); // Prevent newline
            handleSendMessage();
        }
    });

    // Attach File Button
    attachFileButton?.addEventListener('click', () => fileInput?.click());

    // File Input Change
    fileInput?.addEventListener('change', handleFileSelect);

    // Config View Buttons
    refreshConfigButton?.addEventListener('click', displayAgentConfigurations);
    addAgentButton?.addEventListener('click', () => openModal('agent-modal'));

    // Agent Modal Form Submit
    agentForm?.addEventListener('submit', handleSaveAgent);

    // Override Modal Form Submit
    overrideForm?.addEventListener('submit', handleSubmitOverride);

    // Bottom Navigation Buttons
    bottomNavButtons?.forEach(button => {
        button.addEventListener('click', () => {
            const viewId = button.dataset.view;
            if (viewId) {
                showView(viewId);
            }
        });
    });

    // Global listener to close modals when clicking outside
     window.addEventListener('click', (event) => {
         if (event.target === agentModal) {
             closeModal('agent-modal');
         }
         if (event.target === overrideModal) {
              closeModal('override-modal');
         }
     });
}

// --- View Navigation ---
function showView(viewId) {
    views?.forEach(panel => {
        panel.classList.remove('active');
        if (panel.id === viewId) {
            panel.classList.add('active');
        }
    });
    bottomNavButtons?.forEach(button => {
         button.classList.remove('active');
         if (button.dataset.view === viewId) {
             button.classList.add('active');
         }
     });
    currentView = viewId;
    console.log(`Switched to view: ${viewId}`);
}

// --- Message Sending Logic ---
function handleSendMessage() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('system-log-area', 'WebSocket is not connected. Cannot send message.', 'error');
        return;
    }

    const messageText = messageInput?.value.trim();

    // Check if there's either text or an attached file
    if (!messageText && !attachedFile.name) {
        return; // Do nothing if input and file are both empty
    }

    let messageToSend;

    if (attachedFile.name && attachedFile.content) {
        // Send structured message with file content
        messageToSend = {
            type: "user_message_with_file", // Specific type for messages with files
            text: messageText,
            filename: attachedFile.name,
            file_content: attachedFile.content
        };
        addMessage('conversation-area', `You (with ${attachedFile.name}): ${messageText}`, 'user');
    } else {
        // Send plain text message (as string)
        messageToSend = messageText; // Send directly as string
        addMessage('conversation-area', `You: ${messageText}`, 'user');
    }

    // Send the message (string or JSON stringified object)
    try {
        ws.send(typeof messageToSend === 'string' ? messageToSend : JSON.stringify(messageToSend));
    } catch (error) {
        console.error("Error sending message via WebSocket:", error);
        addMessage('system-log-area', `Error sending message: ${error}`, 'error');
        return; // Don't clear input if send failed
    }


    // Clear input and file attachment after sending
    if (messageInput) messageInput.value = '';
    clearFileInput();
    if (messageInput) messageInput.style.height = 'auto'; // Reset height potentially

     // Re-enable send button (it might be disabled elsewhere)
     if (sendButton) sendButton.disabled = false;
}

// --- File Handling ---
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) {
        clearFileInput();
        return;
    }

    // Basic file type check (adjust mimetypes as needed)
    const allowedTypes = [
        'text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css',
        'application/json', 'application/yaml', 'application/x-yaml',
        'application/javascript', 'text/javascript', 'application/x-python-code', 'text/x-python'
    ];
    // Looser check by extension for common types if MIME type isn't perfect
    const allowedExtensions = ['.txt', '.py', '.js', '.html', '.css', '.md', '.json', '.yaml', '.yml', '.csv', '.log'];
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();

    // if (!allowedTypes.includes(file.type) && !allowedExtensions.includes(fileExtension)) {
    //      addMessage('system-log-area', `Error: Invalid file type. Allowed types: ${allowedExtensions.join(', ')}`, 'error');
    //      clearFileInput();
    //      return;
    // }
    // ^^^ Relaxed file type check for now ^^^

    // File size check
    if (file.size > MAX_FILE_SIZE_BYTES) {
        addMessage('system-log-area', `Error: File size exceeds ${MAX_FILE_SIZE_MB}MB limit.`, 'error');
        clearFileInput();
        return;
    }


    const reader = new FileReader();
    reader.onload = (e) => {
        attachedFile.name = file.name;
        attachedFile.content = e.target.result;
        attachedFile.size = file.size;
        displayFileInfo();
    };
    reader.onerror = (e) => {
        console.error("FileReader Error:", e);
        addMessage('system-log-area', 'Error reading file.', 'error');
        clearFileInput();
    };
    reader.readAsText(file); // Read as text
}

function displayFileInfo() {
    if (fileInfoArea) {
        if (attachedFile.name) {
            const fileSizeKB = attachedFile.size ? (attachedFile.size / 1024).toFixed(1) : 'N/A';
            fileInfoArea.innerHTML = `
                <span>📎 ${attachedFile.name} (${fileSizeKB} KB)</span>
                <button onclick="clearFileInput()" title="Remove File">✖</button>
            `;
            fileInfoArea.style.display = 'flex'; // Show the area
        } else {
            fileInfoArea.innerHTML = '';
            fileInfoArea.style.display = 'none'; // Hide the area
        }
    }
}

function clearFileInput() {
    attachedFile = { name: null, content: null, size: null };
    if (fileInput) fileInput.value = ''; // Clear the input element
    displayFileInfo(); // Update the UI
}


// --- Configuration Management UI ---
async function displayAgentConfigurations() {
    if (!configContent) return;
    configContent.innerHTML = '<span class="status-placeholder">Loading...</span>'; // Show loading state

    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const agents = await response.json();

        configContent.innerHTML = ''; // Clear loading/previous content

        if (agents.length === 0) {
            configContent.innerHTML = '<span class="status-placeholder">No static agents configured.</span>';
            return;
        }

        agents.forEach(agent => {
            const itemDiv = document.createElement('div');
            itemDiv.classList.add('config-item');
            itemDiv.innerHTML = `
                <span>
                    <strong>${agent.agent_id}</strong>
                    <span class="agent-details">(${agent.persona || 'No Persona'} - ${agent.provider}/${agent.model})</span>
                </span>
                <div class="config-item-actions">
                    <button class="config-action-button edit-button" data-agent-id="${agent.agent_id}" title="Edit Agent">✏️</button>
                    <button class="config-action-button delete-button" data-agent-id="${agent.agent_id}" title="Delete Agent">🗑️</button>
                </div>
            `;
            configContent.appendChild(itemDiv);

            // Add event listeners for edit/delete buttons
            itemDiv.querySelector('.edit-button').addEventListener('click', (e) => {
                const idToEdit = e.currentTarget.dataset.agentId;
                openModal('agent-modal', idToEdit);
            });
            itemDiv.querySelector('.delete-button').addEventListener('click', (e) => {
                const idToDelete = e.currentTarget.dataset.agentId;
                if (confirm(`Are you sure you want to delete agent configuration '${idToDelete}'? This requires an application restart.`)) {
                    handleDeleteAgent(idToDelete);
                }
            });
        });

    } catch (error) {
        console.error("Error fetching agent configurations:", error);
        configContent.innerHTML = '<span class="status-placeholder">Error loading configuration.</span>';
        addMessage('system-log-area', `Error fetching config: ${error.message}`, 'error');
    }
}


async function handleSaveAgent(event) {
    event.preventDefault();
    if (!agentForm) return;

    const agentId = agentForm.elements['agent-id'].value;
    const isEditing = !!agentForm.elements['edit-agent-id'].value; // Check hidden input

    const agentConfigData = {
        provider: agentForm.elements['provider'].value,
        model: agentForm.elements['model'].value,
        persona: agentForm.elements['persona'].value || settings.DEFAULT_PERSONA, // Use default if empty
        temperature: parseFloat(agentForm.elements['temperature'].value) || settings.DEFAULT_TEMPERATURE,
        system_prompt: agentForm.elements['system_prompt'].value || settings.DEFAULT_SYSTEM_PROMPT,
    };

    const url = isEditing ? `/api/config/agents/${agentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';

    const bodyPayload = isEditing
        ? agentConfigData // PUT expects only the config part
        : { agent_id: agentId, config: agentConfigData }; // POST expects agent_id + config

    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(bodyPayload),
        });

        const result = await response.json();

        if (response.ok) {
            addMessage('system-log-area', result.message || `Agent configuration ${isEditing ? 'updated' : 'added'} successfully. Restart required.`, 'status');
            closeModal('agent-modal');
            displayAgentConfigurations(); // Refresh the list
        } else {
            throw new Error(result.detail || `Failed to ${isEditing ? 'update' : 'add'} agent configuration.`);
        }
    } catch (error) {
        console.error(`Error saving agent configuration for '${agentId}':`, error);
        addMessage('system-log-area', `Error saving agent config: ${error.message}`, 'error');
        // Optionally display error within the modal
    }
}

async function handleDeleteAgent(agentId) {
    try {
        const response = await fetch(`/api/config/agents/${agentId}`, {
            method: 'DELETE',
        });
        const result = await response.json();

        if (response.ok) {
            addMessage('system-log-area', result.message || `Agent configuration '${agentId}' deleted. Restart required.`, 'status');
            displayAgentConfigurations(); // Refresh the list
        } else {
            throw new Error(result.detail || `Failed to delete agent configuration '${agentId}'.`);
        }
    } catch (error) {
        console.error(`Error deleting agent configuration '${agentId}':`, error);
        addMessage('system-log-area', `Error deleting agent config: ${error.message}`, 'error');
    }
}

// --- Modal Management ---

function openModal(modalId, editId = null) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    if (modalId === 'agent-modal') {
        agentForm?.reset(); // Reset form fields
        editAgentIdInput.value = ''; // Clear hidden edit ID
        const agentIdInput = agentForm?.elements['agent-id'];

        if (editId) {
            // --- Pre-fill form for editing ---
            modalTitle.textContent = 'Edit Agent';
            editAgentIdInput.value = editId; // Set hidden input
            if (agentIdInput) {
                agentIdInput.value = editId;
                agentIdInput.readOnly = true; // Prevent changing ID during edit
            }
            // Fetch existing config to pre-fill (replace with actual API call if needed)
             fetch(`/api/config/agents`) // Re-fetch all to find the one easily
                 .then(response => response.json())
                 .then(agents => {
                     const agentToEdit = agents.find(a => a.agent_id === editId);
                     // Need full config detail, not just AgentInfo. Assuming backend would provide it.
                     // This part needs adjustment based on backend sending full config for edit.
                     // For now, we only have basic info. Let's fetch it (if an endpoint existed)
                     // fetch(`/api/config/agents/${editId}/details`) ...
                     // --- SIMULATED PREFILL based on list view data ---
                     if (agentToEdit && agentForm) {
                         agentForm.elements['persona'].value = agentToEdit.persona || '';
                         agentForm.elements['provider'].value = agentToEdit.provider || 'openrouter';
                         agentForm.elements['model'].value = agentToEdit.model || '';
                         // Defaults for others as full config isn't fetched here
                         agentForm.elements['temperature'].value = settings.DEFAULT_TEMPERATURE;
                         agentForm.elements['system_prompt'].value = settings.DEFAULT_SYSTEM_PROMPT;
                         console.warn("Pre-filling edit modal with limited data. Temperature/Prompt use defaults.");

                     } else {
                          console.error(`Agent config for ${editId} not found for editing.`);
                          closeModal('agent-modal'); // Close if agent not found
                     }
                 })
                 .catch(err => {
                      console.error("Error fetching agent details for edit:", err);
                      closeModal('agent-modal');
                 });


        } else {
            // --- Setup for adding new agent ---
            modalTitle.textContent = 'Add Agent';
            if (agentIdInput) agentIdInput.readOnly = false;
             // Set defaults for add mode
             if(agentForm) {
                 agentForm.elements['persona'].value = settings.DEFAULT_PERSONA;
                 agentForm.elements['provider'].value = settings.DEFAULT_AGENT_PROVIDER;
                 agentForm.elements['model'].value = settings.DEFAULT_AGENT_MODEL;
                 agentForm.elements['temperature'].value = settings.DEFAULT_TEMPERATURE;
                 agentForm.elements['system_prompt'].value = settings.DEFAULT_SYSTEM_PROMPT;
             }
        }
    }
    modal.style.display = 'block';
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
        // Optional: Reset form specific to the modal being closed
        if (modalId === 'agent-modal' && agentForm) {
             agentForm.reset();
             const agentIdInput = agentForm.elements['agent-id'];
             if(agentIdInput) agentIdInput.readOnly = false; // Ensure ID is editable for next add
        } else if (modalId === 'override-modal' && overrideForm) {
             overrideForm.reset();
        }
    }
}

function showOverrideModal(data) {
     if (!overrideModal || !overrideForm) return;
     console.log("Showing override modal for:", data);

     overrideAgentIdInput.value = data.agent_id || '';
     overrideMessageP.textContent = data.message || `Agent '${data.persona || data.agent_id}' requires new configuration.`;
     overrideLastErrorCode.textContent = data.last_error || '[No error details provided]';

     // Pre-select current provider/model if possible
     if (data.current_provider) overrideProviderSelect.value = data.current_provider;
     if (data.current_model) overrideModelInput.value = data.current_model;
     else overrideModelInput.value = ''; // Clear model if not provided

     openModal('override-modal');
}

async function handleSubmitOverride(event) {
     event.preventDefault();
     if (!ws || ws.readyState !== WebSocket.OPEN) {
         addMessage('system-log-area', 'WebSocket is not connected. Cannot submit override.', 'error');
         return;
     }
     const overrideData = {
         type: "submit_user_override",
         agent_id: overrideAgentIdInput.value,
         new_provider: overrideProviderSelect.value,
         new_model: overrideModelInput.value.trim()
     };

     if (!overrideData.agent_id || !overrideData.new_provider || !overrideData.new_model) {
          addMessage('system-log-area', 'Override Error: Missing required fields in override form.', 'error');
          return;
     }

     console.log("Submitting user override:", overrideData);
     ws.send(JSON.stringify(overrideData));
     closeModal('override-modal');
     addMessage('system-log-area', `Submitted configuration override for agent ${overrideData.agent_id}.`, 'status');
}


// --- Utility Functions (Example: Placeholder for initial status request) ---
// function requestInitialAgentStatus() {
//     if (ws && ws.readyState === WebSocket.OPEN) {
//         ws.send(JSON.stringify({ type: 'request_all_status' }));
//     }
// }
