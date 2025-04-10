// START OF FILE static/js/app.js

// --- Global Variables ---
let ws = null; // WebSocket connection instance
let currentView = 'chat-view'; // Default view
let attachedFile = { name: null, content: null, size: null }; // Store attached file info
const MAX_FILE_SIZE_MB = 5;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
const settings = { // Placeholder for defaults, replace if actual settings are passed
    DEFAULT_PERSONA: "Default Persona",
    DEFAULT_AGENT_PROVIDER: "openrouter",
    DEFAULT_AGENT_MODEL: "default-model",
    DEFAULT_TEMPERATURE: 0.7,
    DEFAULT_SYSTEM_PROMPT: "You are a helpful assistant."
};

// --- DOM Elements (Cached on Load) ---
let messageInput, sendButton, conversationArea, systemLogArea, logStatus,
    agentStatusContent, bottomNavButtons, views, fileInfoArea, fileInput,
    attachFileButton, agentModal, agentForm, modalTitle, editAgentIdInput,
    overrideModal, overrideForm, overrideAgentIdInput, overrideMessageP,
    overrideLastErrorCode, overrideProviderSelect, overrideModelInput,
    configContent, refreshConfigButton, addAgentButton,
    // Session Management Elements
    projectSelect, sessionSelect, loadSessionButton, saveProjectNameInput,
    saveSessionNameInput, saveSessionButton, sessionStatusMessage;


// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed");

    // Cache DOM elements
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    conversationArea = document.getElementById('conversation-area');
    systemLogArea = document.getElementById('system-log-area');
    logStatus = document.querySelector('#logs-view .message.status.initial-connecting');
    agentStatusContent = document.getElementById('agent-status-content');
    bottomNavButtons = document.querySelectorAll('.nav-button');
    views = document.querySelectorAll('.view-panel');
    fileInfoArea = document.getElementById('file-info-area');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    agentModal = document.getElementById('agent-modal');
    agentForm = document.getElementById('agent-form');
    modalTitle = document.getElementById('modal-title');
    editAgentIdInput = document.getElementById('edit-agent-id');
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
    // Session Management Elements
    projectSelect = document.getElementById('project-select');
    sessionSelect = document.getElementById('session-select');
    loadSessionButton = document.getElementById('load-session-button');
    saveProjectNameInput = document.getElementById('save-project-name');
    saveSessionNameInput = document.getElementById('save-session-name');
    saveSessionButton = document.getElementById('save-session-button');
    sessionStatusMessage = document.getElementById('session-status-message');


    // Check if essential elements are found
    const essentialElements = [
        messageInput, sendButton, conversationArea, systemLogArea, logStatus,
        agentStatusContent, projectSelect, sessionSelect, loadSessionButton,
        saveProjectNameInput, saveSessionButton, sessionStatusMessage
    ];
    if (essentialElements.some(el => !el)) {
        console.error("Initialization Error: One or more essential DOM elements are missing.");
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
    loadProjects(); // Load initial project list for session view

    console.log("Initialization complete.");
});

// --- WebSocket Management ---
// (setupWebSocket, handleWebSocketMessage - unchanged from previous version,
// includes agent_added/agent_deleted handlers)
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
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            addRawLogEntry(data); // Log raw data for debugging
            handleWebSocketMessage(data); // Process the message
        } catch (error) {
            console.error("Error parsing WebSocket message or handling data:", error);
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
        setTimeout(setupWebSocket, 5000);
    };
}

function handleWebSocketMessage(data) {
    const agentId = data.agent_id || 'system'; // Default to system if no agent_id
    let messageText = ''; // Define messageText outside switch for broader use

    console.log(`Handling WS message type: ${data.type}`); // Log message type

    switch (data.type) {
        case 'response_chunk':
            appendAgentResponseChunk(agentId, data.content);
            break;
        case 'final_response':
            finalizeAgentResponse(agentId, data.content);
            break;
        case 'status':
        case 'system_event': // Treat system events like status messages for logging
            messageText = data.content || data.message || 'Status update';
            addMessage('system-log-area', `[${agentId}] ${messageText}`, 'status');
            // Display session load/save messages in the session view status area too
            if (data.type === 'system_event' && (data.event === 'session_loaded' || data.event === 'session_saved')) {
                 displaySessionStatus(messageText, false);
            }
            break;
        case 'error':
            messageText = data.content || 'Unknown error occurred.';
            addMessage('system-log-area', `[ERROR - ${agentId}] ${messageText}`, 'error');
            if(data.agent_id) {
                updateAgentStatusUI(agentId, { status: 'error' });
            }
             if (messageText.toLowerCase().includes('session')) {
                 displaySessionStatus(messageText, true);
             }
            break;
        case 'agent_status_update':
            updateAgentStatusUI(data.agent_id, data.status);
            break;
        case 'agent_added':
            console.log("Agent Added Event:", data);
            messageText = `Agent Added: ${data.agent_id} (Persona: ${data.config?.persona || 'N/A'}, Model: ${data.config?.model || 'N/A'}, Team: ${data.team || 'N/A'})`;
            addMessage('system-log-area', `[System] ${messageText}`, 'status log-agent-message');
            addOrUpdateAgentStatusEntry(data.agent_id, {
                persona: data.config?.persona,
                model: data.config?.model,
                provider: data.config?.provider,
                status: 'idle',
                team: data.team || 'N/A'
            });
            break;
        case 'agent_deleted':
             console.log("Agent Deleted Event:", data);
            messageText = `Agent Deleted: ${data.agent_id}`;
            addMessage('system-log-area', `[System] ${messageText}`, 'status log-agent-message');
            removeAgentStatusEntry(data.agent_id);
            break;
        // --- *** Corrected Handlers *** ---
         case 'team_created':
             console.log("Team Created Event:", data);
             messageText = `Team Created: ${data.team_id || '(Unknown ID)'}`;
             addMessage('system-log-area', `[System] ${messageText}`, 'status log-agent-message');
             // No separate UI element for teams currently, just log it.
             break;
         case 'team_deleted':
             console.log("Team Deleted Event:", data);
             messageText = `Team Deleted: ${data.team_id || '(Unknown ID)'}`;
             addMessage('system-log-area', `[System] ${messageText}`, 'status log-agent-message');
             // No separate UI element for teams currently, just log it.
             break;
         case 'agent_moved_team':
             console.log("Agent Moved Team Event:", data);
             messageText = `Agent ${data.agent_id || '?'} moved to Team: ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`;
             addMessage('system-log-area', `[System] ${messageText}`, 'status log-agent-message');
             // The visual update happens via agent_status_update which should include the new team info
             break;
        // --- *** End Corrected Handlers *** ---
        case 'request_user_override':
            showOverrideModal(data);
            break;
        default:
            messageText = `Unknown message type received: ${data.type}`;
            addMessage('system-log-area', `[System WARN] ${messageText}`, 'status');
            console.warn("Received unknown WebSocket message type:", data);
            // Log the raw data for debugging unknown types
            console.debug("Raw Data for Unknown Type:", data);
    }
}
// --- End replacement function ---

// --- UI Update Functions ---
// (updateLogStatus, addMessage, appendAgentResponseChunk, finalizeAgentResponse - unchanged)
function updateLogStatus(message, isError = false) {
    if (logStatus) {
        logStatus.textContent = message;
        logStatus.style.color = isError ? 'var(--accent-color-red)' : 'var(--text-color-secondary)';
        logStatus.style.fontWeight = isError ? 'bold' : 'normal';
        logStatus.classList.remove("initial-placeholder");
    }
}

function addMessage(areaId, text, type = 'info', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) {
        console.error(`addMessage Error: Area with ID '${areaId}' not found.`);
        return;
    }
    const placeholder = area.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type);
    const contentSpan = document.createElement('span');
    if (areaId === 'system-log-area') {
        const timestampSpan = document.createElement('span');
        timestampSpan.classList.add('timestamp');
        timestampSpan.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ':';
        messageDiv.appendChild(timestampSpan);
    }
    if (text.includes("Executing tool")) contentSpan.classList.add('log-tool-use');
    if (text.includes("Received message from") || text.includes("Sending message to") || text.includes("Agent Added") || text.includes("Agent Deleted")) contentSpan.classList.add('log-agent-message');
    contentSpan.textContent = text;
    messageDiv.appendChild(contentSpan);
    if (agentId) {
        messageDiv.dataset.agentId = agentId;
         if (areaId === 'conversation-area') {
             if (type === 'user') messageDiv.classList.add('user');
             else messageDiv.classList.add('agent_response');
        }
    }
    if (type === 'status' && text.includes("Executing tool")) {
         messageDiv.classList.add('tool-execution');
    }
    area.appendChild(messageDiv);
    area.scrollTop = area.scrollHeight;
}


function appendAgentResponseChunk(agentId, chunk) {
    if (!conversationArea) return;
    const agentMessageId = `agent-msg-${agentId}`;
    let agentMsgDiv = conversationArea.querySelector(`#${agentMessageId}`);
    if (!agentMsgDiv) {
        agentMsgDiv = document.createElement('div');
        agentMsgDiv.classList.add('message', 'agent_response');
        agentMsgDiv.id = agentMessageId;
        agentMsgDiv.dataset.agentId = agentId;
        const labelSpan = document.createElement('span');
        labelSpan.classList.add('agent-label');
        labelSpan.textContent = `Agent @${agentId}:`;
        agentMsgDiv.appendChild(labelSpan);
        const contentSpan = document.createElement('span');
        contentSpan.classList.add('message-content');
        contentSpan.textContent = chunk;
        agentMsgDiv.appendChild(contentSpan);
        conversationArea.appendChild(agentMsgDiv);
        const statusEntry = agentStatusContent?.querySelector(`#agent-status-${agentId}`);
        if(statusEntry) {
            labelSpan.textContent = statusEntry.dataset.persona || `Agent @${agentId}:`;
        }
    } else {
        const contentSpan = agentMsgDiv.querySelector('.message-content');
        if (contentSpan) { contentSpan.textContent += chunk; }
    }
    conversationArea.scrollTop = conversationArea.scrollHeight;
}

function finalizeAgentResponse(agentId, finalContent) {
     if (!conversationArea) return;
     const agentMessageId = `agent-msg-${agentId}`;
     let agentMsgDiv = conversationArea.querySelector(`#${agentMessageId}`);
     if (agentMsgDiv) {
         agentMsgDiv.classList.add('finalized');
         const contentSpan = agentMsgDiv.querySelector('.message-content');
         // if (contentSpan && contentSpan.textContent !== finalContent) { } // Optional update
     } else {
         addMessage('conversation-area', finalContent, 'agent_response', agentId);
         const newMsgDiv = conversationArea.querySelector(`#agent-msg-${agentId}`);
         const statusEntry = agentStatusContent?.querySelector(`#agent-status-${agentId}`);
         const labelSpan = newMsgDiv?.querySelector('.agent-label');
         if(newMsgDiv && labelSpan && statusEntry) {
             labelSpan.textContent = statusEntry.dataset.persona || `Agent @${agentId}:`;
         }
     }
     conversationArea.scrollTop = conversationArea.scrollHeight;
 }

// (updateAgentStatusUI, addOrUpdateAgentStatusEntry, removeAgentStatusEntry, addRawLogEntry - unchanged from previous step)
function updateAgentStatusUI(agentId, statusData) {
    addOrUpdateAgentStatusEntry(agentId, statusData);
}
function addOrUpdateAgentStatusEntry(agentId, statusData) {
    if (!agentStatusContent) return;
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();
    let entry = agentStatusContent.querySelector(`#agent-status-${agentId}`);
    const status = statusData.status || 'unknown';
    const persona = statusData.persona || `Agent @${agentId}`;
    const modelInfo = statusData.model ? `(${statusData.provider || 'N/A'}/${statusData.model})` : '';
    const teamInfo = statusData.team ? `[Team: ${statusData.team}]` : '[Team: N/A]';
    if (!entry) {
        entry = document.createElement('div');
        entry.id = `agent-status-${agentId}`;
        entry.classList.add('agent-status-item');
        agentStatusContent.appendChild(entry);
        console.log(`Added status entry for ${agentId}`);
    }
    entry.className = `agent-status-item status-${status.toLowerCase().replace(/\s+/g, '_')}`;
    entry.dataset.persona = persona;
    entry.innerHTML = `
        <span>
            <strong>${persona}</strong>
            <span class="agent-model">${modelInfo}</span>
            <span class="agent-team">${teamInfo}</span>
        </span>
        <span class="agent-status">${status}</span>
    `;
}
function removeAgentStatusEntry(agentId) {
    if (!agentStatusContent) return;
    const entry = agentStatusContent.querySelector(`#agent-status-${agentId}`);
    if (entry) {
        entry.remove();
        console.log(`Removed status entry for ${agentId}`);
    }
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
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); handleSendMessage(); }
    });

    // Attach File Button & Input
    attachFileButton?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);

    // Config View Buttons
    refreshConfigButton?.addEventListener('click', displayAgentConfigurations);
    addAgentButton?.addEventListener('click', () => openModal('agent-modal'));

    // Modal Form Submits
    agentForm?.addEventListener('submit', handleSaveAgent);
    overrideForm?.addEventListener('submit', handleSubmitOverride);

    // --- Session Management Listeners ---
    projectSelect?.addEventListener('change', (event) => {
        const selectedProject = event.target.value;
        loadSessions(selectedProject); // Load sessions when project changes
    });

    loadSessionButton?.addEventListener('click', handleLoadSession);
    saveSessionButton?.addEventListener('click', handleSaveSession);
    // --- End Session Listeners ---

    // Bottom Navigation Buttons
    bottomNavButtons?.forEach(button => {
        button.addEventListener('click', () => {
            const viewId = button.dataset.view;
            if (viewId) {
                showView(viewId);
                // --- Refresh projects when navigating TO session view ---
                if (viewId === 'session-view') {
                    loadProjects();
                }
                // --- ---
            }
        });
    });

    // Global listener to close modals
     window.addEventListener('click', (event) => {
         if (event.target === agentModal) { closeModal('agent-modal'); }
         if (event.target === overrideModal) { closeModal('override-modal'); }
     });
}

// --- View Navigation ---
function showView(viewId) {
    views?.forEach(panel => {
        panel.classList.remove('active');
        if (panel.id === viewId) { panel.classList.add('active'); }
    });
    bottomNavButtons?.forEach(button => {
         button.classList.remove('active');
         if (button.dataset.view === viewId) { button.classList.add('active'); }
     });
    currentView = viewId;
    console.log(`Switched to view: ${viewId}`);
}

// --- Message Sending Logic ---
// (handleSendMessage - unchanged)
function handleSendMessage() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('system-log-area', 'WebSocket is not connected. Cannot send message.', 'error');
        return;
    }
    const messageText = messageInput?.value.trim();
    if (!messageText && !attachedFile.name) { return; }
    let messageToSend;
    if (attachedFile.name && attachedFile.content) {
        messageToSend = {
            type: "user_message_with_file", text: messageText,
            filename: attachedFile.name, file_content: attachedFile.content
        };
        addMessage('conversation-area', `You (with ${attachedFile.name}): ${messageText}`, 'user');
    } else {
        messageToSend = messageText;
        addMessage('conversation-area', `You: ${messageText}`, 'user');
    }
    try {
        ws.send(typeof messageToSend === 'string' ? messageToSend : JSON.stringify(messageToSend));
    } catch (error) {
        console.error("Error sending message via WebSocket:", error);
        addMessage('system-log-area', `Error sending message: ${error}`, 'error');
        return;
    }
    if (messageInput) messageInput.value = '';
    clearFileInput();
    if (messageInput) messageInput.style.height = 'auto';
     if (sendButton) sendButton.disabled = false;
}


// --- File Handling ---
// (handleFileSelect, displayFileInfo, clearFileInput - unchanged)
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) { clearFileInput(); return; }
    const allowedExtensions = ['.txt', '.py', '.js', '.html', '.css', '.md', '.json', '.yaml', '.yml', '.csv', '.log'];
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();
    if (file.size > MAX_FILE_SIZE_BYTES) {
        addMessage('system-log-area', `Error: File size exceeds ${MAX_FILE_SIZE_MB}MB limit.`, 'error');
        clearFileInput(); return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
        attachedFile.name = file.name; attachedFile.content = e.target.result; attachedFile.size = file.size; displayFileInfo();
    };
    reader.onerror = (e) => { console.error("FileReader Error:", e); addMessage('system-log-area', 'Error reading file.', 'error'); clearFileInput(); };
    reader.readAsText(file);
}
function displayFileInfo() {
    if (fileInfoArea) {
        if (attachedFile.name) {
            const fileSizeKB = attachedFile.size ? (attachedFile.size / 1024).toFixed(1) : 'N/A';
            fileInfoArea.innerHTML = `<span>📎 ${attachedFile.name} (${fileSizeKB} KB)</span> <button onclick="clearFileInput()" title="Remove File">✖</button>`;
            fileInfoArea.style.display = 'flex';
        } else {
            fileInfoArea.innerHTML = ''; fileInfoArea.style.display = 'none';
        }
    }
}
function clearFileInput() {
    attachedFile = { name: null, content: null, size: null };
    if (fileInput) fileInput.value = ''; displayFileInfo();
}


// --- Configuration Management UI ---
// (displayAgentConfigurations, handleSaveAgent, handleDeleteAgent - unchanged)
async function displayAgentConfigurations() {
    if (!configContent) return;
    configContent.innerHTML = '<span class="status-placeholder">Loading...</span>';
    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
        const agents = await response.json();
        configContent.innerHTML = '';
        if (agents.length === 0) {
            configContent.innerHTML = '<span class="status-placeholder">No static agents configured.</span>'; return;
        }
        agents.forEach(agent => {
            const itemDiv = document.createElement('div'); itemDiv.classList.add('config-item');
            itemDiv.innerHTML = `<span><strong>${agent.agent_id}</strong> <span class="agent-details">(${agent.persona || 'No Persona'} - ${agent.provider}/${agent.model})</span></span> <div class="config-item-actions"> <button class="config-action-button edit-button" data-agent-id="${agent.agent_id}" title="Edit Agent">✏️</button> <button class="config-action-button delete-button" data-agent-id="${agent.agent_id}" title="Delete Agent">🗑️</button> </div>`;
            configContent.appendChild(itemDiv);
            itemDiv.querySelector('.edit-button').addEventListener('click', (e) => { openModal('agent-modal', e.currentTarget.dataset.agentId); });
            itemDiv.querySelector('.delete-button').addEventListener('click', (e) => {
                const idToDelete = e.currentTarget.dataset.agentId;
                if (confirm(`Are you sure you want to delete agent configuration '${idToDelete}'? This requires an application restart.`)) { handleDeleteAgent(idToDelete); }
            });
        });
    } catch (error) {
        console.error("Error fetching agent configurations:", error);
        configContent.innerHTML = '<span class="status-placeholder">Error loading configuration.</span>';
        addMessage('system-log-area', `Error fetching config: ${error.message}`, 'error');
    }
}
async function handleSaveAgent(event) {
    event.preventDefault(); if (!agentForm) return;
    const agentId = agentForm.elements['agent-id'].value;
    const isEditing = !!agentForm.elements['edit-agent-id'].value;
    const agentConfigData = {
        provider: agentForm.elements['provider'].value, model: agentForm.elements['model'].value,
        persona: agentForm.elements['persona'].value || settings.DEFAULT_PERSONA,
        temperature: parseFloat(agentForm.elements['temperature'].value) || settings.DEFAULT_TEMPERATURE,
        system_prompt: agentForm.elements['system_prompt'].value || settings.DEFAULT_SYSTEM_PROMPT,
    };
    const url = isEditing ? `/api/config/agents/${agentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';
    const bodyPayload = isEditing ? agentConfigData : { agent_id: agentId, config: agentConfigData };
    try {
        const response = await fetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(bodyPayload), });
        const result = await response.json();
        if (response.ok) {
            addMessage('system-log-area', result.message || `Agent configuration ${isEditing ? 'updated' : 'added'} successfully. Restart required.`, 'status');
            closeModal('agent-modal'); displayAgentConfigurations();
        } else { throw new Error(result.detail || `Failed to ${isEditing ? 'update' : 'add'} agent configuration.`); }
    } catch (error) { console.error(`Error saving agent configuration for '${agentId}':`, error); addMessage('system-log-area', `Error saving agent config: ${error.message}`, 'error'); }
}
async function handleDeleteAgent(agentId) {
    try {
        const response = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE', });
        const result = await response.json();
        if (response.ok) { addMessage('system-log-area', result.message || `Agent configuration '${agentId}' deleted. Restart required.`, 'status'); displayAgentConfigurations(); }
        else { throw new Error(result.detail || `Failed to delete agent configuration '${agentId}'.`); }
    } catch (error) { console.error(`Error deleting agent configuration '${agentId}':`, error); addMessage('system-log-area', `Error deleting agent config: ${error.message}`, 'error'); }
}


// --- Modal Management ---
// (openModal, closeModal, showOverrideModal, handleSubmitOverride - unchanged)
function openModal(modalId, editId = null) {
    const modal = document.getElementById(modalId); if (!modal) return;
    if (modalId === 'agent-modal') {
        agentForm?.reset(); editAgentIdInput.value = ''; const agentIdInput = agentForm?.elements['agent-id'];
        if (editId) {
            modalTitle.textContent = 'Edit Agent'; editAgentIdInput.value = editId;
            if (agentIdInput) { agentIdInput.value = editId; agentIdInput.readOnly = true; }
             fetch(`/api/config/agents`)
                 .then(response => response.json())
                 .then(agents => {
                     const agentToEdit = agents.find(a => a.agent_id === editId);
                     if (agentToEdit && agentForm) {
                         agentForm.elements['persona'].value = agentToEdit.persona || ''; agentForm.elements['provider'].value = agentToEdit.provider || 'openrouter';
                         agentForm.elements['model'].value = agentToEdit.model || ''; agentForm.elements['temperature'].value = settings.DEFAULT_TEMPERATURE;
                         agentForm.elements['system_prompt'].value = settings.DEFAULT_SYSTEM_PROMPT; console.warn("Pre-filling edit modal with limited data.");
                     } else { console.error(`Agent config for ${editId} not found for editing.`); closeModal('agent-modal'); }
                 })
                 .catch(err => { console.error("Error fetching agent details for edit:", err); closeModal('agent-modal'); });
        } else {
            modalTitle.textContent = 'Add Agent'; if (agentIdInput) agentIdInput.readOnly = false;
             if(agentForm) {
                 agentForm.elements['persona'].value = settings.DEFAULT_PERSONA; agentForm.elements['provider'].value = settings.DEFAULT_AGENT_PROVIDER;
                 agentForm.elements['model'].value = settings.DEFAULT_AGENT_MODEL; agentForm.elements['temperature'].value = settings.DEFAULT_TEMPERATURE;
                 agentForm.elements['system_prompt'].value = settings.DEFAULT_SYSTEM_PROMPT;
             }
        }
    }
    modal.style.display = 'block';
}
function closeModal(modalId) {
    const modal = document.getElementById(modalId); if (modal) { modal.style.display = 'none';
        if (modalId === 'agent-modal' && agentForm) { agentForm.reset(); const agentIdInput = agentForm.elements['agent-id']; if(agentIdInput) agentIdInput.readOnly = false; }
        else if (modalId === 'override-modal' && overrideForm) { overrideForm.reset(); } }
}
function showOverrideModal(data) {
     if (!overrideModal || !overrideForm) return; console.log("Showing override modal for:", data);
     overrideAgentIdInput.value = data.agent_id || ''; overrideMessageP.textContent = data.message || `Agent '${data.persona || data.agent_id}' requires new configuration.`;
     overrideLastErrorCode.textContent = data.last_error || '[No error details provided]';
     if (data.current_provider) overrideProviderSelect.value = data.current_provider;
     if (data.current_model) overrideModelInput.value = data.current_model; else overrideModelInput.value = '';
     openModal('override-modal');
}
async function handleSubmitOverride(event) {
     event.preventDefault(); if (!ws || ws.readyState !== WebSocket.OPEN) { addMessage('system-log-area', 'WebSocket is not connected. Cannot submit override.', 'error'); return; }
     const overrideData = { type: "submit_user_override", agent_id: overrideAgentIdInput.value, new_provider: overrideProviderSelect.value, new_model: overrideModelInput.value.trim() };
     if (!overrideData.agent_id || !overrideData.new_provider || !overrideData.new_model) { addMessage('system-log-area', 'Override Error: Missing required fields.', 'error'); return; }
     console.log("Submitting user override:", overrideData); ws.send(JSON.stringify(overrideData)); closeModal('override-modal');
     addMessage('system-log-area', `Submitted configuration override for agent ${overrideData.agent_id}.`, 'status');
}


// --- *** NEW: Session Management Functions *** ---

async function loadProjects() {
    console.log("Loading projects...");
    if (!projectSelect) return;

    // Clear existing options (except the default)
    projectSelect.innerHTML = '<option value="">-- Select Project --</option>';
    // Clear session dropdown as well
    sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
    sessionSelect.disabled = true;
    loadSessionButton.disabled = true;
    displaySessionStatus("", false); // Clear status

    try {
        const response = await fetch('/api/projects');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const projects = await response.json();

        if (projects.length === 0) {
            projectSelect.innerHTML = '<option value="">-- No Projects Found --</option>';
        } else {
            projects.forEach(project => {
                const option = document.createElement('option');
                option.value = project.project_name;
                option.textContent = project.project_name;
                projectSelect.appendChild(option);
            });
        }
         console.log(`Loaded ${projects.length} projects.`);
    } catch (error) {
        console.error("Error loading projects:", error);
        displaySessionStatus(`Error loading projects: ${error.message}`, true);
        projectSelect.innerHTML = '<option value="">-- Error Loading --</option>';
    }
}

async function loadSessions(projectName) {
    console.log(`Loading sessions for project: ${projectName}`);
    if (!sessionSelect || !loadSessionButton) return;

    // Clear existing session options and disable
    sessionSelect.innerHTML = '<option value="">-- Loading Sessions --</option>';
    sessionSelect.disabled = true;
    loadSessionButton.disabled = true;
    displaySessionStatus("", false); // Clear status

    if (!projectName) {
        sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
        return; // Do nothing if no project is selected
    }

    try {
        const response = await fetch(`/api/projects/${projectName}/sessions`);
        if (!response.ok) {
            // Handle project not found specifically? The backend might already do this.
            if (response.status === 404) {
                 throw new Error(`Project '${projectName}' not found or has no sessions.`);
            }
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const sessions = await response.json();

        if (sessions.length === 0) {
            sessionSelect.innerHTML = '<option value="">-- No Sessions Found --</option>';
             // Keep sessionSelect disabled, button disabled
        } else {
            sessionSelect.innerHTML = '<option value="">-- Select Session --</option>'; // Reset default
            sessions.forEach(session => {
                const option = document.createElement('option');
                option.value = session.session_name;
                option.textContent = session.session_name;
                sessionSelect.appendChild(option);
            });
            sessionSelect.disabled = false; // Enable session selection
            // Enable load button ONLY if a valid session is selected (initially none)
             sessionSelect.onchange = () => { // Add listener to enable load button
                 loadSessionButton.disabled = !sessionSelect.value;
             };
        }
        console.log(`Loaded ${sessions.length} sessions for ${projectName}.`);
    } catch (error) {
        console.error(`Error loading sessions for ${projectName}:`, error);
        displaySessionStatus(`Error loading sessions: ${error.message}`, true);
        sessionSelect.innerHTML = '<option value="">-- Error Loading Sessions --</option>';
        sessionSelect.disabled = true;
        loadSessionButton.disabled = true;
    }
}

// --- *** Replace existing handleLoadSession function with this one *** ---
async function handleLoadSession() {
    // --- Add null checks for the select elements ---
    if (!projectSelect || !sessionSelect) {
         console.error("Load Session Error: Project or Session select element not found in DOM.");
         displaySessionStatus("Error: UI elements missing. Cannot load session.", true);
         return;
    }
    // --- End null checks ---

    const projectName = projectSelect.value; // Now safe to access .value
    const sessionName = sessionSelect.value; // Now safe to access .value

    if (!projectName || !sessionName) {
        displaySessionStatus("Error: Please select both a project and a session to load.", true);
        return;
    }

    console.log(`Requesting load for: ${projectName}/${sessionName}`);
    displaySessionStatus(`Loading session '${sessionName}'...`, false);
    if (loadSessionButton) loadSessionButton.disabled = true; // Disable button during load

    try {
        const response = await fetch(`/api/projects/${projectName}/sessions/${sessionName}/load`, {
            method: 'POST',
        });
        const result = await response.json();

        if (response.ok) {
            console.log("Load session successful:", result.message);
            // Backend sends success message via WebSocket ('system_event'),
            // so we just clear the local status or show the message briefly
            displaySessionStatus(result.message, false);
            // Add a system log message as well for clarity
            addMessage('system-log-area', `[System] Session '${sessionName}' from project '${projectName}' loaded successfully.`, 'status');
            // Optional: Switch back to chat view after successful load?
            // showView('chat-view');
        } else {
            // Use the detailed error message from the backend result if available
            throw new Error(result.detail || `Failed to load session '${sessionName}'. Status: ${response.status}`);
        }
    } catch (error) {
        console.error("Error loading session:", error);
        displaySessionStatus(`Error loading session: ${error.message}`, true);
        addMessage('system-log-area', `Error loading session: ${error.message}`, 'error'); // Log error
    } finally {
        // Re-enable button only if a session is still selected
        if (loadSessionButton && sessionSelect) {
             loadSessionButton.disabled = !sessionSelect.value;
        }
    }
}
// --- *** End replacement section *** ---

async function handleSaveSession() {
    const projectName = saveProjectNameInput?.value.trim();
    const sessionName = saveSessionNameInput?.value.trim() || null; // Send null if empty

    if (!projectName) {
        displaySessionStatus("Error: Project name is required to save.", true);
        return;
    }

    // Basic validation for project name (prevent slashes etc.)
    if (!/^[a-zA-Z0-9_-]+$/.test(projectName)) {
         displaySessionStatus("Error: Project name can only contain letters, numbers, underscores, and hyphens.", true);
         return;
    }
     // Basic validation for session name (if provided)
    if (sessionName && !/^[a-zA-Z0-9_-]+$/.test(sessionName)) {
        displaySessionStatus("Error: Session name (if provided) can only contain letters, numbers, underscores, and hyphens.", true);
        return;
    }


    console.log(`Requesting save for project: ${projectName}, session: ${sessionName || '(Auto-named)'}`);
    displaySessionStatus(`Saving session under project '${projectName}'...`, false);
    saveSessionButton.disabled = true; // Disable button during save

    try {
        const payload = {};
        if (sessionName) {
            payload.session_name = sessionName;
        }

        const response = await fetch(`/api/projects/${projectName}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: Object.keys(payload).length > 0 ? JSON.stringify(payload) : null, // Send body only if session name provided
        });

        const result = await response.json();

        if (response.ok || response.status === 201) { // Handle 201 Created
            console.log("Save session successful:", result.message);
            // Backend sends success message via WebSocket ('system_event')
            displaySessionStatus(result.message, false);
            // Refresh project list to show the new project/session if it's new
            loadProjects();
            // Clear the save inputs after successful save
            saveProjectNameInput.value = '';
            saveSessionNameInput.value = '';
        } else {
            throw new Error(result.detail || `Failed to save session.`);
        }
    } catch (error) {
        console.error("Error saving session:", error);
        displaySessionStatus(`Error saving session: ${error.message}`, true);
    } finally {
        saveSessionButton.disabled = false; // Re-enable button
    }
}

function displaySessionStatus(message, isError = false) {
     if (!sessionStatusMessage) return;
     sessionStatusMessage.textContent = message;
     sessionStatusMessage.className = 'session-status'; // Reset classes
     if (message) {
         sessionStatusMessage.classList.add(isError ? 'error' : 'success');
         sessionStatusMessage.style.display = 'block';
     } else {
         sessionStatusMessage.style.display = 'none';
     }
     // Auto-clear message after a delay?
     // setTimeout(() => {
     //     if (sessionStatusMessage.textContent === message) { // Only clear if it hasn't changed
     //         sessionStatusMessage.textContent = '';
     //         sessionStatusMessage.style.display = 'none';
     //         sessionStatusMessage.className = 'session-status';
     //     }
     // }, 5000); // Clear after 5 seconds
}

// --- END NEW SESSION FUNCTIONS ---


// --- Utility Functions (Placeholder) ---
// function requestInitialAgentStatus() { }
