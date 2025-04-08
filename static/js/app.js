// START OF FILE static/js/app.js (Bottom Navigation Version)

// --- WebSocket Connection ---
let ws; // WebSocket instance
let reconnectInterval = 5000;
let currentFile = null; // To store the currently selected file object
let currentFileContent = null; // Store content separately

// --- DOM Element References ---
// View Panels
let chatView = null;
let logsView = null;
let configView = null;
let viewPanels = null; // NodeList

// Navigation
let bottomNav = null;
let navButtons = null; // NodeList

// Content Areas
let conversationArea = null;
let systemLogArea = null;
let messageInput = null;
let sendButton = null;
let agentStatusContent = null;
let configContent = null;
let fileInput = null;
let attachFileButton = null;
let fileInfoArea = null;

// Modals
let agentModal = null;
let overrideModal = null;
let agentForm = null;
let overrideForm = null;
let modalTitle = null;

// Config Buttons
let addAgentButton = null;
let refreshConfigButton = null;

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded. Initializing Bottom Nav UI...");

    // --- Get Core Elements ---
    console.log("Getting core DOM elements...");
    // Views
    chatView = document.getElementById('chat-view');
    logsView = document.getElementById('logs-view');
    configView = document.getElementById('config-view');
    viewPanels = document.querySelectorAll('.view-panel');

    // Navigation
    bottomNav = document.getElementById('bottom-nav');
    navButtons = bottomNav?.querySelectorAll('.nav-button');

    // Content / Input
    conversationArea = document.getElementById('conversation-area');
    systemLogArea = document.getElementById('system-log-area');
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    agentStatusContent = document.getElementById('agent-status-content');
    configContent = document.getElementById('config-content');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    fileInfoArea = document.getElementById('file-info-area');

    // Modals
    agentModal = document.getElementById('agent-modal');
    overrideModal = document.getElementById('override-modal');
    agentForm = document.getElementById('agent-form');
    overrideForm = document.getElementById('override-form');
    modalTitle = document.getElementById('modal-title');

    // Config Buttons
    addAgentButton = document.getElementById('add-agent-button');
    refreshConfigButton = document.getElementById('refresh-config-button');

    // --- Basic Checks ---
    const essentialElements = [ // List all essential elements for the core app
        chatView, logsView, configView, bottomNav, navButtons,
        conversationArea, systemLogArea, messageInput, sendButton, agentStatusContent,
        configContent, agentModal, overrideModal, agentForm, overrideForm
    ];
    if (viewPanels.length === 0 || !navButtons || navButtons.length === 0 || essentialElements.some(el => !el)) {
        console.error("Essential UI elements missing! Aborting initialization. Check HTML structure & IDs/Classes.", {
           viewPanels: viewPanels.length, navButtons: navButtons?.length, essentials: essentialElements.map(el => !!el)
        });
        document.body.innerHTML = '<h1 style="color: red; text-align: center;">UI Initialization Error: Core elements missing. Check Console.</h1>';
        return;
    }
     console.log("Core DOM elements found.");

     // --- Initialize ---
     try {
        console.log("Setting up WebSocket...");
        setupWebSocket(); // Setup WebSocket connection

        console.log("Setting up Event Listeners...");
        setupEventListeners(); // Setup all button clicks, input events, etc.

        console.log("Loading initial config display...");
        displayAgentConfigurations(); // Initial load for config view

        console.log("UI Initialized Successfully.");
     } catch(error) {
        console.error("Error during initialization:", error);
        document.body.innerHTML = `<h1 style="color: red; text-align: center;">UI Initialization Error: ${error.message}</h1><pre>${error.stack}</pre>`;
     }
});

// --- WebSocket Setup ---
function setupWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting WS connection: ${wsUrl}`);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WS connected.');
        updateLogStatus('Connected!', false); // Update status in log view
        requestInitialAgentStatus();
        reconnectInterval = 5000;
    };

    ws.onmessage = (event) => {
        try {
            const messageData = JSON.parse(event.data);
            handleWebSocketMessage(messageData); // Handle incoming messages
        } catch (error) {
            console.error('WS parse error:', error);
            addMessage('system-log-area', `[SysErr] Bad msg: ${event.data}`, 'error');
        }
    };

    ws.onerror = (event) => {
        console.error('WS error:', event);
        addMessage('system-log-area', '[WS Error] Connect error.', 'error');
        updateLogStatus('Connect Error. Retry...', true);
    };

    ws.onclose = (event) => {
        console.log('WS closed:', event.reason, `(${event.code})`);
        const message = event.wasClean ? `[WS Closed] Code: ${event.code}.` : `[WS Closed] Lost connection. Code: ${event.code}. Reconnecting...`;
        const type = event.wasClean ? 'status' : 'error';
        addMessage('system-log-area', message, type);
        updateLogStatus('Disconnected. Retry...', true);
        ws = null; // Clear instance
        setTimeout(setupWebSocket, reconnectInterval);
        reconnectInterval = Math.min(reconnectInterval * 1.5, 30000);
    };
}

// --- WebSocket Message Handling ---
function handleWebSocketMessage(data) {
    addRawLogEntry(data); // Log raw data for debugging

    switch (data.type) {
        case 'response_chunk': appendAgentResponseChunk(data.agent_id, data.content); break;
        case 'status': case 'system_event': addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status.'}`, 'status'); if (data.message === 'Connected to TrippleEffect backend!') { updateLogStatus('Connected!', false); } break;
        case 'error': addMessage('system-log-area', `[${data.agent_id || 'Error'}] ${data.content}`, 'error'); break;
        case 'final_response': finalizeAgentResponse(data.agent_id, data.content); break;
        case 'agent_status_update': updateAgentStatusUI(data.agent_id, data.status); break;
        case 'agent_added': addMessage('system-log-area', `[Sys] Agent Added: ${data.agent_id} (Team: ${data.team || 'N/A'})`, 'status'); addOrUpdateAgentStatusEntry(data.agent_id, { agent_id: data.agent_id, persona: data.config?.persona || data.agent_id, status: data.config?.status || 'idle', provider: data.config?.provider, model: data.config?.model, team: data.team }); break;
        case 'agent_deleted': addMessage('system-log-area', `[Sys] Agent Deleted: ${data.agent_id}`, 'status'); removeAgentStatusEntry(data.agent_id); break;
        case 'team_created': addMessage('system-log-area', `[Sys] Team Created: ${data.team_id}`, 'status'); break;
        case 'team_deleted': addMessage('system-log-area', `[Sys] Team Deleted: ${data.team_id}`, 'status'); break;
        case 'agent_moved_team': addMessage('system-log-area', `[Sys] Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status'); requestAgentStatus(data.agent_id); break;
        case 'request_user_override': showOverrideModal(data); addMessage('system-log-area', `[Sys] Override Required for Agent ${data.agent_id}`, 'error'); break;
        default: console.warn('Unknown WS msg type:', data.type, data); addMessage('system-log-area', `[Sys] Unhandled msg type: ${data.type}`, 'status');
    }
}
function requestInitialAgentStatus() { console.log("Req init status (needs backend)..."); }
function requestAgentStatus(agentId) { console.log(`Req status for ${agentId} (needs backend)...`); }


// --- UI Update Functions ---
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
    // Scroll the specific area
    setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
}
function appendAgentResponseChunk(agentId, chunk) { const area = conversationArea; if (!area) return; let d = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`); if (!d) { const p = area.querySelector('.initial-placeholder'); if (p) p.remove(); d = document.createElement('div'); d.classList.add('message', 'agent_response', 'incomplete'); d.dataset.agentId = agentId; const l = document.createElement('strong'); l.textContent = `Agent @${agentId}:\n`; d.appendChild(l); area.appendChild(d); } const n = document.createTextNode(chunk); d.appendChild(n); setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50); }
function finalizeAgentResponse(agentId, finalContent) { const area = conversationArea; if (!area) return; let d = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`); if (d) { d.classList.remove('incomplete'); } else if (finalContent) { addMessage('conversation-area', `Agent @${agentId}:\n${finalContent}`, 'agent_response', agentId); } setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50); }
function updateLogStatus(message, isError = false) { const area = systemLogArea; if (!area) return; let s = area.querySelector('.status.initial-connecting'); if (!s) s = area.querySelector('.message.status:last-child'); if (!s && message) { addMessage('system-log-area', message, isError ? 'error' : 'status'); } else if (s) { s.textContent = message; s.className = `message status ${isError ? 'error' : ''}`; if (message === 'Connected!' || message === 'Connected to backend!') s.classList.remove('initial-connecting'); } }
function updateAgentStatusUI(agentId, statusData) { if (!agentStatusContent) return; const p = agentStatusContent.querySelector('.status-placeholder'); if (p) p.remove(); addOrUpdateAgentStatusEntry(agentId, statusData); }
function addOrUpdateAgentStatusEntry(agentId, statusData) { if (!agentStatusContent) return; let i = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`); if (!i) { i = document.createElement('div'); i.classList.add('agent-status-item'); i.dataset.agentId = agentId; agentStatusContent.appendChild(i); } const p = statusData?.persona || agentId; const s = statusData?.status || 'unknown'; const v = statusData?.provider || 'N/A'; const m = statusData?.model || 'N/A'; const t = statusData?.team || 'None'; i.title = `ID: ${agentId}\nProvider: ${v}\nModel: ${m}\nTeam: ${t}\nStatus: ${s}`; i.innerHTML = `<strong>${p}</strong> <span class="agent-model">(${m})</span> <span>[Team: ${t}]</span> <span class="agent-status">${s.replace('_', ' ')}</span>`; i.className = `agent-status-item status-${s}`; }
function removeAgentStatusEntry(agentId) { if (!agentStatusContent) return; const i = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`); if (i) i.remove(); if (!agentStatusContent.hasChildNodes() || agentStatusContent.innerHTML.trim() === '') { agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>'; } }
function addRawLogEntry(data) { try { const t = JSON.stringify(data); console.debug("Raw:", t.substring(0, 300) + (t.length > 300 ? '...' : '')); } catch (e) { console.warn("Cannot stringify raw data:", data); } }


// --- Event Listeners ---
function setupEventListeners() {
    // Input listeners
    sendButton?.addEventListener('click', handleSendMessage);
    messageInput?.addEventListener('keypress', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSendMessage(); } });
    attachFileButton?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);

    // Config listeners
    addAgentButton?.addEventListener('click', () => openModal('agent-modal'));
    refreshConfigButton?.addEventListener('click', displayAgentConfigurations);

    // Modal form listeners
    agentForm?.addEventListener('submit', handleSaveAgent);
    overrideForm?.addEventListener('submit', handleSubmitOverride);

    // Bottom Navigation listeners
    navButtons?.forEach(button => {
        button.addEventListener('click', () => {
            const viewId = button.dataset.view;
            showView(viewId);
        });
    });

    // Global listener to close modals
    window.addEventListener('click', function(event) { if (event.target.classList.contains('modal')) closeModal(event.target.id); });

    console.log("Global event listeners setup complete.");
}

// --- Navigation Logic ---
/**
 * Shows the specified view panel and hides others. Updates nav button active state.
 * @param {string} viewId - The ID of the view panel to show (e.g., 'chat-view').
 */
function showView(viewId) {
    console.log(`Switching view to: ${viewId}`);
    // Hide all panels
    viewPanels?.forEach(panel => {
        panel.classList.remove('active');
    });
    // Deactivate all nav buttons
    navButtons?.forEach(button => {
        button.classList.remove('active');
    });

    // Show the target panel
    const targetPanel = document.getElementById(viewId);
    if (targetPanel) {
        targetPanel.classList.add('active');
    } else {
        console.error(`View panel with ID ${viewId} not found!`);
        // Optionally default back to chat view
        document.getElementById('chat-view')?.classList.add('active');
        viewId = 'chat-view'; // Update viewId for button activation
    }

    // Activate the corresponding nav button
    const targetButton = bottomNav?.querySelector(`button[data-view="${viewId}"]`);
    if (targetButton) {
        targetButton.classList.add('active');
    }
}

// --- Action Handlers ---
function handleSendMessage() { const messageText = messageInput?.value.trim() ?? ''; if (!messageText && !currentFile) { return; } if (!ws || ws.readyState !== WebSocket.OPEN) { addMessage('system-log-area', '[SysErr] WS not connected.', 'error'); return; } const messageToSend = { type: currentFile ? 'user_message_with_file' : 'user_message', text: messageText }; if (currentFile && currentFileContent) { messageToSend.filename = currentFile.name; messageToSend.file_content = currentFileContent; } else if (currentFile && !currentFileContent) { console.error("File selected but no content."); addMessage('system-log-area', '[UI Err] File content missing.', 'error'); clearFileInput(); return; } const displayMessage = currentFile ? `[File: ${currentFile.name}]\n${messageText}` : messageText; addMessage('conversation-area', displayMessage, 'user'); sendMessageToServer(messageToSend); if(messageInput) messageInput.value = ''; clearFileInput(); }

// --- File Handling ---
function handleFileSelect(event) { const f = event.target.files[0]; if (!f) { clearFileInput(); return; } const t=['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml']; const e=['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log']; const x='.' + f.name.split('.').pop().toLowerCase(); const v=t.includes(f.type) || e.includes(x); if (!v) { alert(`Unsupported type: ${f.type || x}. Upload text files.`); clearFileInput(); return; } const s=1*1024*1024; if (f.size > s) { alert(`File too large (${(f.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`); clearFileInput(); return; } const r=new FileReader(); r.onload = (ev) => { currentFile = { name: f.name, size: f.size, type: f.type }; currentFileContent = ev.target.result; displayFileInfo(); console.log(`File "${f.name}" selected.`); }; r.onerror = (ev) => { console.error("File read error:", ev); alert("Error reading file."); clearFileInput(); }; r.readAsText(f); }
function displayFileInfo() { if (!fileInfoArea) return; if (currentFile) { fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`; } else { fileInfoArea.innerHTML = ''; } }
function clearFileInput() { currentFile = null; currentFileContent = null; if(fileInput) fileInput.value = ''; displayFileInfo(); console.log("File input cleared."); }

// --- Configuration Management UI ---
// (displayAgentConfigurations, handleSaveAgent, handleDeleteAgent remain unchanged)
async function displayAgentConfigurations() { if (!configContent) { console.warn("Config area not found."); return; } configContent.innerHTML = '<span class="status-placeholder">Loading...</span>'; try { const r = await fetch('/api/config/agents'); if (!r.ok) throw new Error(`HTTP ${r.status}`); const a = await r.json(); configContent.innerHTML = ''; if (a.length === 0) { configContent.innerHTML = '<span class="status-placeholder">No static agents.</span>'; return; } a.sort((x, y) => (x.agent_id || "").localeCompare(y.agent_id || "")); a.forEach(g => { if (!g || !g.agent_id) return; const i = document.createElement('div'); i.classList.add('config-item'); const dT = `- ${g.provider || 'N/A'} / ${g.model || 'N/A'}`; i.innerHTML = `<span><strong></strong> (${g.agent_id}) <span class="agent-details"></span></span> <div class="config-item-actions"> <button class="config-action-button edit-button" data-id="${g.agent_id}" title="Edit">✏️</button> <button class="config-action-button delete-button" data-id="${g.agent_id}" title="Delete">🗑️</button> </div>`; i.querySelector('strong').textContent = g.persona || g.agent_id; i.querySelector('.agent-details').textContent = dT; configContent.appendChild(i); i.querySelector('.edit-button')?.addEventListener('click', () => openModal('agent-modal', g.agent_id)); i.querySelector('.delete-button')?.addEventListener('click', () => handleDeleteAgent(g.agent_id)); }); } catch (e) { console.error('Error fetching configs:', e); if(configContent) configContent.innerHTML = '<span class="status-placeholder error">Error loading.</span>'; addMessage('system-log-area', `[UI Error] Failed config load: ${e}`, 'error'); } }
async function handleSaveAgent(event) { event.preventDefault(); const f = event.target; if (!f) return; const iI = f.querySelector('#agent-id'); const eI = f.querySelector('#edit-agent-id'); if (!iI || !eI) return; const aId = iI.value.trim(); const eId = eI.value; const ed = !!eId; if (!aId || !/^[a-zA-Z0-9_-]+$/.test(aId)) { alert("Valid Agent ID required."); return; } const cfg = { provider: f.querySelector('#provider')?.value, model: f.querySelector('#model')?.value.trim() ?? '', persona: f.querySelector('#persona')?.value.trim() || aId, temperature: parseFloat(f.querySelector('#temperature')?.value) || 0.7, system_prompt: f.querySelector('#system_prompt')?.value.trim() || 'You are helpful.' }; if (!cfg.provider || !cfg.model) { alert("Provider/Model required."); return; } const u = ed ? `/api/config/agents/${eId}` : '/api/config/agents'; const m = ed ? 'PUT' : 'POST'; const p = ed ? cfg : { agent_id: aId, config: cfg }; console.log(`Sending ${m} to ${u}`); try { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) }); const x = await r.json(); if (r.ok && x.success) { alert(x.message || `Agent ${ed ? 'updated' : 'added'}. Restart.`); closeModal('agent-modal'); displayAgentConfigurations(); } else { throw new Error(x.detail || x.message || `Failed op.`); } } catch (e) { console.error(`Save agent err:`, e); alert(`Error: ${e.message}`); addMessage('system-log-area', `[UI Err] Save agent failed: ${e.message}`, 'error'); } }
async function handleDeleteAgent(agentId) { if (!confirm(`Delete static cfg '${agentId}'? Restart needed.`)) return; try { const r = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' }); const x = await r.json(); if (r.ok && x.success) { alert(x.message || 'Agent cfg deleted. Restart.'); displayAgentConfigurations(); } else { throw new Error(x.detail || x.message || 'Failed delete.'); } } catch (e) { console.error('Delete agent err:', e); alert(`Error: ${e.message}`); addMessage('system-log-area', `[UI Err] Delete agent failed: ${e.message}`, 'error'); } }


// --- Modal Handling ---
async function openModal(modalId, editId = null) { const m = document.getElementById(modalId); if (!m) { console.error(`Modal ${modalId} not found.`); return; } if (modalId === 'agent-modal') { const f = m.querySelector('#agent-form'); const t = m.querySelector('#modal-title'); const iI = f?.querySelector('#agent-id'); const eI = f?.querySelector('#edit-agent-id'); if (!f || !t || !iI || !eI) { console.error("Agent modal elements missing."); return; } f.reset(); eI.value = ''; iI.disabled = false; if (editId) { t.textContent = `Edit Agent: ${editId}`; eI.value = editId; iI.value = editId; iI.disabled = true; try { console.log(`Fetching list for edit: ${editId}`); const r = await fetch('/api/config/agents'); if (!r.ok) throw new Error('Fetch list failed.'); const a = await r.json(); const d = a.find(x => x.agent_id === editId); if (!d) throw new Error(`Agent ${editId} not found.`); f.querySelector('#persona').value = d.persona || editId; f.querySelector('#provider').value = d.provider || 'openrouter'; f.querySelector('#model').value = d.model || ''; console.warn("Edit modal prefilled limited data."); } catch (e) { console.error("Edit fetch err:", e); alert(`Load agent error: ${e.message}`); return; } } else { t.textContent = 'Add New Static Agent'; f.querySelector('#temperature').value = 0.7; f.querySelector('#provider').value = 'openrouter'; } } m.style.display = 'block'; }
function closeModal(modalId) { const m = document.getElementById(modalId); if (m) m.style.display = 'none'; const f = m?.querySelector('form'); if(f) f.reset(); if(modalId === 'agent-modal') { const eI = document.getElementById('edit-agent-id'); const iI = document.getElementById('agent-id'); if(eI) eI.value = ''; if(iI) iI.disabled = false; } }
function showOverrideModal(data) { if (!overrideModal) return; const aId = data.agent_id; const p = data.persona || aId; document.getElementById('override-agent-id').value = aId; document.getElementById('override-modal-title').textContent = `Override for: ${p}`; document.getElementById('override-message').textContent = data.message || `Agent '${p}' (${aId}) failed. Provide alternative.`; document.getElementById('override-last-error').textContent = data.last_error || "Unknown error"; const s = document.getElementById('override-provider'); const i = document.getElementById('override-model'); if (s && data.current_provider) s.value = data.current_provider; if (i) i.value = data.current_model || ''; openModal('override-modal'); }
function handleSubmitOverride(event) { event.preventDefault(); const aId = document.getElementById('override-agent-id')?.value; const nP = document.getElementById('override-provider')?.value; const nM = document.getElementById('override-model')?.value.trim(); if (!aId || !nP || !nM) { alert("Fill all override fields."); return; } const oD = { type: "submit_user_override", agent_id: aId, new_provider: nP, new_model: nM }; const wsInstance = getWebSocketInstance ? getWebSocketInstance() : ws; if (wsInstance && wsInstance.readyState === WebSocket.OPEN) { wsInstance.send(JSON.stringify(oD)); addMessage('system-log-area', `[UI] Submitted override for ${aId} (Prov: ${nP}, Model: ${nM}).`, 'status'); closeModal('override-modal'); } else { alert("WS not connected."); addMessage('system-log-area', `[UI Err] Override failed for ${aId}: WS not connected.`, 'error'); } }
// Expose functions needed by inline HTML onclick attributes globally
window.closeModal = closeModal;
window.clearFileInput = clearFileInput; // Ensure this is exposed if button uses onclick

console.log("Main app.js execution finished (Bottom Nav Version).");
