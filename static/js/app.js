// START OF FILE static/js/app.js

// --- WebSocket Connection ---
let ws; // WebSocket instance
let reconnectInterval = 5000; // Reconnect attempt interval in ms
let currentFile = null; // To store the currently selected file object

// --- DOM Elements (Original Single-Page Structure) ---
const container = document.querySelector('.container'); // Main container
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const agentStatusContent = document.getElementById('agent-status-content'); // In its original place
const configContent = document.getElementById('config-content');       // In its original place
const addAgentButton = document.getElementById('add-agent-button');       // In config area
const refreshConfigButton = document.getElementById('refresh-config-button'); // In config area
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');
const agentModal = document.getElementById('agent-modal');
const overrideModal = document.getElementById('override-modal');
const agentForm = document.getElementById('agent-form');
const overrideForm = document.getElementById('override-form');
const modalTitle = document.getElementById('modal-title');

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded (Baseline Single-Page).");
    // Check for essential baseline elements
    if (!container || !conversationArea || !systemLogArea || !messageInput || !agentStatusContent || !configContent) {
        console.error("Essential UI elements for baseline layout not found! Check HTML IDs/Classes.");
        // Display error more gracefully if possible
        document.body.innerHTML = '<h1 style="color: red; text-align: center;">UI Initialization Error: Essential elements missing.</h1>';
        return;
    }
    setupWebSocket();
    setupEventListeners();
    displayAgentConfigurations(); // Initial load for config area
});

// --- WebSocket Setup ---
// (WebSocket setup function remains unchanged - uses addMessage/updateLogStatus)
function setupWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting to connect WebSocket to: ${wsUrl}`);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connection established.');
        updateLogStatus('Connected to backend!', false);
        requestInitialAgentStatus();
        reconnectInterval = 5000; // Reset reconnect interval
    };

    ws.onmessage = (event) => {
        try {
            const messageData = JSON.parse(event.data);
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
        const message = event.wasClean
            ? `[WebSocket Closed] Code: ${event.code}.`
            : `[WebSocket Closed] Connection lost unexpectedly. Code: ${event.code}. Trying to reconnect...`;
        const type = event.wasClean ? 'status' : 'error';
        addMessage('system-log-area', message, type);
        updateLogStatus('Disconnected. Retrying...', true);
        setTimeout(setupWebSocket, reconnectInterval);
        reconnectInterval = Math.min(reconnectInterval * 1.5, 30000);
    };
}

// --- WebSocket Message Handling ---
// (handleWebSocketMessage function remains unchanged - uses correct addMessage/UI update functions)
function handleWebSocketMessage(data) {
    addRawLogEntry(data); // Log raw data for debugging

    switch (data.type) {
        case 'response_chunk':
            appendAgentResponseChunk(data.agent_id, data.content);
            break;
        case 'status':
        case 'system_event':
            addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status update.'}`, 'status');
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
             addMessage('system-log-area', `[System] Agent Added: ${data.agent_id} (Team: ${data.team || 'N/A'})`, 'status');
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
            addMessage('system-log-area', `[System] Agent Deleted: ${data.agent_id}`, 'status');
            removeAgentStatusEntry(data.agent_id);
            break;
        case 'team_created':
            addMessage('system-log-area', `[System] Team Created: ${data.team_id}`, 'status');
            break;
        case 'team_deleted':
             addMessage('system-log-area', `[System] Team Deleted: ${data.team_id}`, 'status');
            break;
        case 'agent_moved_team':
             addMessage('system-log-area', `[System] Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status');
             requestAgentStatus(data.agent_id);
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
    console.log("Requesting initial agent status (needs backend implementation)...");
}
function requestAgentStatus(agentId) {
    console.log(`Requesting status update for agent ${agentId} (needs backend implementation)...`);
}

// --- UI Update Functions ---
// (All UI update functions remain largely the same, just ensuring they target the correct baseline elements)
function addMessage(areaId, text, type = 'status', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) return;
    const placeholder = area.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();

    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type);
    if (agentId) messageDiv.dataset.agentId = agentId;

    if (areaId === 'system-log-area') {
        const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const timestampSpan = document.createElement('span');
        timestampSpan.classList.add('timestamp');
        timestampSpan.textContent = `[${timestamp}] `;
        messageDiv.appendChild(timestampSpan);
    }

    const contentSpan = document.createElement('span');
    contentSpan.innerHTML = text.replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">").replace(/\n/g, '<br>');
    messageDiv.appendChild(contentSpan);

    area.appendChild(messageDiv);
    area.scrollTop = area.scrollHeight; // Scroll the specific area
}

function appendAgentResponseChunk(agentId, chunk) {
    const area = conversationArea;
    if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);

    if (!agentMsgDiv) {
        const placeholder = area.querySelector('.initial-placeholder');
        if (placeholder) placeholder.remove();
        agentMsgDiv = document.createElement('div');
        agentMsgDiv.classList.add('message', 'agent_response', 'incomplete');
        agentMsgDiv.dataset.agentId = agentId;
        const label = document.createElement('strong');
        label.textContent = `Agent @${agentId}:\n`;
        agentMsgDiv.appendChild(label);
        area.appendChild(agentMsgDiv);
    }
    const chunkNode = document.createTextNode(chunk);
    agentMsgDiv.appendChild(chunkNode);
    area.scrollTop = area.scrollHeight;
}

function finalizeAgentResponse(agentId, finalContent) {
    const area = conversationArea;
    if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);

    if (agentMsgDiv) {
        agentMsgDiv.classList.remove('incomplete');
    } else if (finalContent) {
        addMessage('conversation-area', `Agent @${agentId}:\n${finalContent}`, 'agent_response', agentId);
    }
    area.scrollTop = area.scrollHeight;
}

function updateLogStatus(message, isError = false) {
    const area = systemLogArea;
    if (!area) return;
    let statusDiv = area.querySelector('.status.initial-connecting');
    if (!statusDiv) statusDiv = area.querySelector('.message.status:last-child');

     if (!statusDiv && message) {
          addMessage('system-log-area', message, isError ? 'error' : 'status');
     } else if (statusDiv) {
        statusDiv.textContent = message;
        statusDiv.className = `message status ${isError ? 'error' : ''}`;
        if (message === 'Connected to backend!') {
            statusDiv.classList.remove('initial-connecting');
        }
    }
}

function updateAgentStatusUI(agentId, statusData) {
    if (!agentStatusContent) return;
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

     const persona = statusData?.persona || agentId;
     const status = statusData?.status || 'unknown';
     const provider = statusData?.provider || 'N/A';
     const model = statusData?.model || 'N/A';
     const team = statusData?.team || 'None';

     itemDiv.title = `ID: ${agentId}\nProvider: ${provider}\nModel: ${model}\nTeam: ${team}\nStatus: ${status}`;
     itemDiv.innerHTML = `
         <strong>${persona}</strong>
         <span class="agent-model">(${model})</span>
         <span>[Team: ${team}]</span>
         <span class="agent-status">${status.replace('_', ' ')}</span>
     `;
     itemDiv.className = `agent-status-item status-${status}`;
}

function removeAgentStatusEntry(agentId) {
    if (!agentStatusContent) return;
    const itemDiv = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);
    if (itemDiv) itemDiv.remove();
    if (!agentStatusContent.hasChildNodes() || agentStatusContent.innerHTML.trim() === '') {
        agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>';
    }
}

function addRawLogEntry(data) {
    try {
        const logText = JSON.stringify(data);
        console.debug("Raw WS Data:", logText.substring(0, 500) + (logText.length > 500 ? '...' : ''));
    } catch (e) { console.warn("Could not stringify raw WS data:", data); }
}


// --- Event Listeners (Ensure correct selectors for baseline) ---
function setupEventListeners() {
    sendButton?.addEventListener('click', sendMessage);
    messageInput?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    attachFileButton?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);
    addAgentButton?.addEventListener('click', () => openModal('agent-modal'));
    refreshConfigButton?.addEventListener('click', displayAgentConfigurations); // Refresh list
    agentForm?.addEventListener('submit', handleSaveAgent);
    overrideForm?.addEventListener('submit', handleSubmitOverride);
    // --- NO SWIPE LISTENERS IN BASELINE ---
}

// --- Send Message Functionality (Unchanged) ---
function sendMessage() {
    const messageText = messageInput?.value.trim() ?? '';
    if (!messageText && !currentFile) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('system-log-area', '[System Error] WebSocket not connected.', 'error'); return;
    }
    const messageToSend = { type: currentFile ? 'user_message_with_file' : 'user_message', text: messageText };
    if (currentFile) { messageToSend.filename = currentFile.name; messageToSend.file_content = currentFile.content; }
    const displayMessage = currentFile ? `[File: ${currentFile.name}]\n${messageText}` : messageText;
    addMessage('conversation-area', displayMessage, 'user');
    ws.send(JSON.stringify(messageToSend));
    if(messageInput) messageInput.value = '';
    clearFileInput();
}

// --- File Handling (Unchanged) ---
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) { clearFileInput(); return; }
    const allowedTypes = ['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml'];
    const allowedExtensions = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log'];
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowedTypes.includes(file.type) && !allowedExtensions.includes(fileExtension)) {
        alert(`Unsupported file type: ${file.type || fileExtension}. Upload text-based files.`);
        clearFileInput(); return;
    }
    const maxSize = 1 * 1024 * 1024;
    if (file.size > maxSize) {
        alert(`File too large (${(file.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`);
        clearFileInput(); return;
    }
    const reader = new FileReader();
    reader.onload = (e) => { currentFile = { name: file.name, content: e.target.result, size: file.size, type: file.type }; displayFileInfo(); }
    reader.onerror = (e) => { console.error("File reading error:", e); alert("Error reading file."); clearFileInput(); }
    reader.readAsText(file);
}

function displayFileInfo() {
    if (!fileInfoArea) return;
    if (currentFile) {
        fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`;
    } else { fileInfoArea.innerHTML = ''; }
}

function clearFileInput() {
    currentFile = null;
    if(fileInput) fileInput.value = '';
    displayFileInfo();
}

// --- Configuration Management UI (Unchanged) ---
async function displayAgentConfigurations() {
    if (!configContent) { console.warn("Config content area not found."); return; }
    configContent.innerHTML = '<span class="status-placeholder">Loading...</span>';
    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const agents = await response.json();
        configContent.innerHTML = '';
        if (agents.length === 0) {
            configContent.innerHTML = '<span class="status-placeholder">No static agents configured.</span>'; return;
        }
        agents.sort((a, b) => a.agent_id.localeCompare(b.agent_id));
        agents.forEach(agent => {
            const item = document.createElement('div');
            item.classList.add('config-item');
            item.innerHTML = `
                <span><strong>${agent.persona || agent.agent_id}</strong> (${agent.agent_id}) <span class="agent-details">- ${agent.provider} / ${agent.model}</span></span>
                <div class="config-item-actions">
                    <button class="config-action-button edit-button" data-id="${agent.agent_id}" title="Edit">✏️</button>
                    <button class="config-action-button delete-button" data-id="${agent.agent_id}" title="Delete">🗑️</button>
                </div>`;
            configContent.appendChild(item);
            item.querySelector('.edit-button')?.addEventListener('click', () => openModal('agent-modal', agent.agent_id));
            item.querySelector('.delete-button')?.addEventListener('click', () => handleDeleteAgent(agent.agent_id));
        });
    } catch (error) {
        console.error('Error fetching/displaying agent configurations:', error);
        if(configContent) configContent.innerHTML = '<span class="status-placeholder error">Error loading config.</span>';
        addMessage('system-log-area', `[UI Error] Failed to display config: ${error}`, 'error');
    }
}

async function handleSaveAgent(event) {
    event.preventDefault();
    const form = event.target; if (!form) return;
    const agentIdInput = form.querySelector('#agent-id');
    const editAgentIdInput = form.querySelector('#edit-agent-id');
    if (!agentIdInput || !editAgentIdInput) return;

    const agentId = agentIdInput.value.trim();
    const editAgentId = editAgentIdInput.value;
    const isEditing = !!editAgentId;

    if (!agentId || !/^[a-zA-Z0-9_-]+$/.test(agentId)) { alert("Valid Agent ID required (alphanumeric, _, -)."); return; }
    const agentConfig = { provider: form.querySelector('#provider')?.value, model: form.querySelector('#model')?.value.trim() ?? '', persona: form.querySelector('#persona')?.value.trim() || agentId, temperature: parseFloat(form.querySelector('#temperature')?.value) || 0.7, system_prompt: form.querySelector('#system_prompt')?.value.trim() || 'You are a helpful assistant.' };
    if (!agentConfig.provider || !agentConfig.model) { alert("Provider and Model are required."); return; }

    const url = isEditing ? `/api/config/agents/${editAgentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';
    const payload = isEditing ? agentConfig : { agent_id: agentId, config: agentConfig };
    console.log(`Sending ${method} to ${url}`);

    try {
        const response = await fetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const result = await response.json();
        if (response.ok && result.success) { alert(result.message || `Agent ${isEditing ? 'updated' : 'added'}. Restart required.`); closeModal('agent-modal'); displayAgentConfigurations(); }
        else { throw new Error(result.detail || result.message || `Failed to ${isEditing ? 'update' : 'add'} agent.`); }
    } catch (error) { console.error(`Error saving agent:`, error); alert(`Error: ${error.message}`); addMessage('system-log-area', `[UI Error] Failed to save agent config: ${error.message}`, 'error'); }
}

async function handleDeleteAgent(agentId) {
    if (!confirm(`Delete static agent config '${agentId}'? Requires restart.`)) return;
    try {
        const response = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' });
        const result = await response.json();
        if (response.ok && result.success) { alert(result.message || 'Agent config deleted. Restart required.'); displayAgentConfigurations(); }
        else { throw new Error(result.detail || result.message || 'Failed to delete agent.'); }
    } catch (error) { console.error('Error deleting agent:', error); alert(`Error: ${error.message}`); addMessage('system-log-area', `[UI Error] Failed to delete agent config: ${error.message}`, 'error'); }
}

// --- Modal Handling (Unchanged) ---
async function openModal(modalId, editId = null) {
    const modal = document.getElementById(modalId);
    if (!modal) { console.error(`Modal with ID ${modalId} not found.`); return; }

    if (modalId === 'agent-modal') {
        const form = modal.querySelector('#agent-form');
        const titleEl = modal.querySelector('#modal-title');
        const agentIdInput = form?.querySelector('#agent-id');
        const editAgentIdInput = form?.querySelector('#edit-agent-id');
        if (!form || !titleEl || !agentIdInput || !editAgentIdInput) { console.error("Agent modal elements missing."); return; }
        form.reset(); editAgentIdInput.value = ''; agentIdInput.disabled = false;
        if (editId) {
            titleEl.textContent = `Edit Agent: ${editId}`; editAgentIdInput.value = editId; agentIdInput.value = editId; agentIdInput.disabled = true;
            try {
                 console.log(`Fetching config list to find details for agent: ${editId}`);
                 const response = await fetch('/api/config/agents'); if (!response.ok) throw new Error('Failed to fetch agent list for editing.');
                 const agents = await response.json(); const agentData = agents.find(a => a.agent_id === editId);
                 if (!agentData) throw new Error(`Agent config for ${editId} not found in list.`);
                 form.querySelector('#persona').value = agentData.persona || editId;
                 form.querySelector('#provider').value = agentData.provider || 'openrouter';
                 form.querySelector('#model').value = agentData.model || '';
                 console.warn("Edit modal prefilled with limited data from list.");
            } catch (error) { console.error("Error fetching agent data for edit:", error); alert(`Error loading agent data: ${error.message}`); return; }
        } else { titleEl.textContent = 'Add New Static Agent'; form.querySelector('#temperature').value = 0.7; form.querySelector('#provider').value = 'openrouter'; }
    }
    modal.style.display = 'block';
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.style.display = 'none';
    const form = modal?.querySelector('form'); if(form) form.reset();
    if(modalId === 'agent-modal') {
         const editInput = document.getElementById('edit-agent-id'); const idInput = document.getElementById('agent-id');
         if(editInput) editInput.value = ''; if(idInput) idInput.disabled = false;
    }
}
window.onclick = function(event) { if (event.target.classList.contains('modal')) closeModal(event.target.id); }

// --- Override Modal Specific (Unchanged) ---
function showOverrideModal(data) {
    if (!overrideModal) return; const agentId = data.agent_id; const persona = data.persona || agentId;
    document.getElementById('override-agent-id').value = agentId; document.getElementById('override-modal-title').textContent = `Override for Agent: ${persona}`;
    document.getElementById('override-message').textContent = data.message || `Agent '${persona}' (${agentId}) failed. Provide alternative.`;
    document.getElementById('override-last-error').textContent = data.last_error || "Unknown error";
    const providerSelect = document.getElementById('override-provider'); const modelInput = document.getElementById('override-model');
    if (providerSelect && data.current_provider) providerSelect.value = data.current_provider;
    if (modelInput) modelInput.value = data.current_model || '';
    openModal('override-modal');
}

function handleSubmitOverride(event) {
    event.preventDefault(); const agentId = document.getElementById('override-agent-id')?.value;
    const newProvider = document.getElementById('override-provider')?.value; const newModel = document.getElementById('override-model')?.value.trim();
    if (!agentId || !newProvider || !newModel) { alert("Please fill all override fields."); return; }
    const overrideData = { type: "submit_user_override", agent_id: agentId, new_provider: newProvider, new_model: newModel };
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(overrideData)); addMessage('system-log-area', `[UI Action] Submitted override for Agent ${agentId} (Provider: ${newProvider}, Model: ${newModel}).`, 'status'); closeModal('override-modal'); }
    else { alert("WebSocket not connected. Cannot submit override."); addMessage('system-log-area', `[UI Error] Failed override for ${agentId}: WebSocket not connected.`, 'error'); }
}
