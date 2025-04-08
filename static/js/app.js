// START OF FILE static/js/app.js (Bottom Navigation Version - Agent Label Fix)

// --- WebSocket Connection ---
let ws; // WebSocket instance
let reconnectInterval = 5000;
let currentFile = null; // To store the currently selected file object
let currentFileContent = null; // Store content separately

// --- DOM Element References ---
let chatView, logsView, configView, viewPanels, bottomNav, navButtons,
    conversationArea, systemLogArea, messageInput, sendButton, agentStatusContent,
    configContent, fileInput, attachFileButton, fileInfoArea, agentModal,
    overrideModal, agentForm, overrideForm, modalTitle, addAgentButton, refreshConfigButton;

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded. Initializing Bottom Nav UI...");
    // Get Elements
    chatView = document.getElementById('chat-view');
    logsView = document.getElementById('logs-view');
    configView = document.getElementById('config-view');
    viewPanels = document.querySelectorAll('.view-panel');
    bottomNav = document.getElementById('bottom-nav');
    navButtons = bottomNav?.querySelectorAll('.nav-button');
    conversationArea = document.getElementById('conversation-area');
    systemLogArea = document.getElementById('system-log-area');
    messageInput = document.getElementById('message-input');
    sendButton = document.getElementById('send-button');
    agentStatusContent = document.getElementById('agent-status-content');
    configContent = document.getElementById('config-content');
    fileInput = document.getElementById('file-input');
    attachFileButton = document.getElementById('attach-file-button');
    fileInfoArea = document.getElementById('file-info-area');
    agentModal = document.getElementById('agent-modal');
    overrideModal = document.getElementById('override-modal');
    agentForm = document.getElementById('agent-form');
    overrideForm = document.getElementById('override-form');
    modalTitle = document.getElementById('modal-title');
    addAgentButton = document.getElementById('add-agent-button');
    refreshConfigButton = document.getElementById('refresh-config-button');

    // Checks
    const essentialElements = [ chatView, logsView, configView, bottomNav, navButtons, conversationArea, systemLogArea, messageInput, sendButton, agentStatusContent, configContent, agentModal, overrideModal, agentForm, overrideForm ];
    if (viewPanels.length === 0 || !navButtons || navButtons.length === 0 || essentialElements.some(el => !el)) { console.error("Essential UI elements missing!"); document.body.innerHTML = '<h1 style="color: red;">UI Init Error</h1>'; return; }
    console.log("Core DOM elements found.");

    // Init
     try {
        console.log("Setting up WebSocket...");
        setupWebSocket();
        console.log("Setting up Event Listeners...");
        setupEventListeners();
        console.log("Loading initial config display...");
        displayAgentConfigurations();
        console.log("UI Initialized Successfully.");
     } catch(error) { console.error("Error during initialization:", error); document.body.innerHTML = `<h1 style="color: red;">UI Init Error: ${error.message}</h1><pre>${error.stack}</pre>`; }
});


// --- WebSocket Setup ---
function setupWebSocket() { const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'; const wsUrl = `${wsProtocol}//${window.location.host}/ws`; console.log(`Attempting WS: ${wsUrl}`); ws = new WebSocket(wsUrl); ws.onopen = () => { console.log('WS connected.'); updateLogStatus('Connected!', false); requestInitialAgentStatus(); reconnectInterval = 5000; }; ws.onmessage = (event) => { try { const d = JSON.parse(event.data); handleWebSocketMessage(d); } catch (e) { console.error('WS parse error:', e); addMessage('system-log-area', `[SysErr] Bad msg: ${event.data}`, 'error'); } }; ws.onerror = (e) => { console.error('WS error:', e); addMessage('system-log-area', '[WS Error] Connect error.', 'error'); updateLogStatus('Connect Error. Retry...', true); }; ws.onclose = (e) => { console.log('WS closed:', e.reason, `(${e.code})`); const m = e.wasClean ? `[WS Closed] Code: ${e.code}.` : `[WS Closed] Lost connection. Code: ${e.code}. Reconnecting...`; const t = e.wasClean ? 'status' : 'error'; addMessage('system-log-area', m, t); updateLogStatus('Disconnected. Retry...', true); ws = null; setTimeout(setupWebSocket, reconnectInterval); reconnectInterval = Math.min(reconnectInterval * 1.5, 30000); }; }

// --- WebSocket Message Handling ---
function handleWebSocketMessage(data) { addRawLogEntry(data); switch (data.type) { case 'response_chunk': appendAgentResponseChunk(data.agent_id, data.content); break; case 'status': case 'system_event': addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status.'}`, 'status'); if (data.message === 'Connected to TrippleEffect backend!') { updateLogStatus('Connected!', false); } break; case 'error': addMessage('system-log-area', `[${data.agent_id || 'Error'}] ${data.content}`, 'error'); break; case 'final_response': finalizeAgentResponse(data.agent_id, data.content); break; case 'agent_status_update': updateAgentStatusUI(data.agent_id, data.status); break; case 'agent_added': addMessage('system-log-area', `[Sys] Agent Added: ${data.agent_id} (Team: ${data.team || 'N/A'})`, 'status'); addOrUpdateAgentStatusEntry(data.agent_id, { agent_id: data.agent_id, persona: data.config?.persona || data.agent_id, status: data.config?.status || 'idle', provider: data.config?.provider, model: data.config?.model, team: data.team }); break; case 'agent_deleted': addMessage('system-log-area', `[Sys] Agent Deleted: ${data.agent_id}`, 'status'); removeAgentStatusEntry(data.agent_id); break; case 'team_created': addMessage('system-log-area', `[Sys] Team Created: ${data.team_id}`, 'status'); break; case 'team_deleted': addMessage('system-log-area', `[Sys] Team Deleted: ${data.team_id}`, 'status'); break; case 'agent_moved_team': addMessage('system-log-area', `[Sys] Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status'); requestAgentStatus(data.agent_id); break; case 'request_user_override': showOverrideModal(data); addMessage('system-log-area', `[Sys] Override Required for Agent ${data.agent_id}`, 'error'); break; default: console.warn('Unknown WS msg type:', data.type, data); addMessage('system-log-area', `[Sys] Unhandled msg type: ${data.type}`, 'status'); } }
function requestInitialAgentStatus() { console.log("Req init status (needs backend)..."); }
function requestAgentStatus(agentId) { console.log(`Req status for ${agentId} (needs backend)...`); }

// --- UI Update Functions ---
function addMessage(areaId, text, type = 'status', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) { console.error(`Message area #${areaId} not found.`); return; }
    const placeholder = area.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();

    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', type);
    if (agentId) messageDiv.dataset.agentId = agentId;

    // Add timestamp for system logs
    if (areaId === 'system-log-area') {
        const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const timestampSpan = document.createElement('span');
        timestampSpan.classList.add('timestamp');
        timestampSpan.textContent = `[${timestamp}] `;
        messageDiv.appendChild(timestampSpan);

        // Apply color classes based on content (only if not an error)
        if (type !== 'error') {
            if (text.includes("Executing tool") || text.includes("Tool result") || text.includes("Manager Result for") || text.includes("validated. Signaling manager")) {
                messageDiv.classList.add('log-tool-use');
            } else if (text.startsWith("[From @")) {
                messageDiv.classList.add('log-agent-message');
            }
        }
    }

    // *** MODIFICATION for Agent Responses in Conversation Area ***
    if (areaId === 'conversation-area' && type === 'agent_response' && agentId) {
        // Add Agent Label Element
        const label = document.createElement('div'); // Use div for block display
        label.classList.add('agent-label');
        label.textContent = `Agent @${agentId}:`;
        messageDiv.appendChild(label);
        // Message content will be added to a separate container below
        const contentContainer = document.createElement('div');
        contentContainer.classList.add('message-content');
        // Use innerHTML for content to render <br> tags
        contentContainer.innerHTML = text.replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">").replace(/\n/g, '<br>');
        messageDiv.appendChild(contentContainer);
    } else {
        // For user messages or system logs, add content directly
        const contentSpan = document.createElement('span');
        contentSpan.innerHTML = text.replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">").replace(/\n/g, '<br>');
        messageDiv.appendChild(contentSpan);
    }
    // *** END MODIFICATION ***

    area.appendChild(messageDiv);
    setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50); // Scroll after append
}

function appendAgentResponseChunk(agentId, chunk) {
    const area = conversationArea; if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);
    let contentContainer;

    if (!agentMsgDiv) {
        // Create the main message div
        const placeholder = area.querySelector('.initial-placeholder'); if (placeholder) placeholder.remove();
        agentMsgDiv = document.createElement('div');
        agentMsgDiv.classList.add('message', 'agent_response', 'incomplete');
        agentMsgDiv.dataset.agentId = agentId;

        // Add Agent Label Element
        const label = document.createElement('div');
        label.classList.add('agent-label');
        label.textContent = `Agent @${agentId}:`;
        agentMsgDiv.appendChild(label);

        // Add Container for the content chunks
        contentContainer = document.createElement('div');
        contentContainer.classList.add('message-content');
        agentMsgDiv.appendChild(contentContainer);

        area.appendChild(agentMsgDiv);
    } else {
        // Find existing content container
        contentContainer = agentMsgDiv.querySelector('.message-content');
        if (!contentContainer) { // Should not happen, but safety check
            console.error("Could not find message-content container for chunk append!");
            contentContainer = agentMsgDiv; // Append to main div as fallback
        }
    }
    // Append text chunk safely
    const chunkNode = document.createTextNode(chunk);
    contentContainer.appendChild(chunkNode);

    setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
}

function finalizeAgentResponse(agentId, finalContent) {
    const area = conversationArea; if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);

    if (agentMsgDiv) {
        agentMsgDiv.classList.remove('incomplete');
        // Content should already be in the .message-content div from chunks
    } else if (finalContent) {
        // If no streaming occurred, add the full message using the new structure
        addMessage('conversation-area', finalContent, 'agent_response', agentId);
    }
    setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
}

function updateLogStatus(message, isError = false) { const area = systemLogArea; if (!area) return; let s = area.querySelector('.status.initial-connecting'); if (!s) s = area.querySelector('.message.status:last-child'); if (!s && message) { addMessage('system-log-area', message, isError ? 'error' : 'status'); } else if (s) { s.textContent = message; s.className = `message status ${isError ? 'error' : ''}`; if (message === 'Connected!' || message === 'Connected to backend!') s.classList.remove('initial-connecting'); } }
function updateAgentStatusUI(agentId, statusData) { if (!agentStatusContent) return; const p = agentStatusContent.querySelector('.status-placeholder'); if (p) p.remove(); addOrUpdateAgentStatusEntry(agentId, statusData); }
function addOrUpdateAgentStatusEntry(agentId, statusData) { if (!agentStatusContent) return; let i = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`); if (!i) { i = document.createElement('div'); i.classList.add('agent-status-item'); i.dataset.agentId = agentId; agentStatusContent.appendChild(i); } const p = statusData?.persona || agentId; const s = statusData?.status || 'unknown'; const v = statusData?.provider || 'N/A'; const m = statusData?.model || 'N/A'; const t = statusData?.team || 'None'; i.title = `ID: ${agentId}\nProvider: ${v}\nModel: ${m}\nTeam: ${t}\nStatus: ${s}`; i.innerHTML = `<strong>${p}</strong> <span class="agent-model">(${m})</span> <span>[Team: ${t}]</span> <span class="agent-status">${s.replace('_', ' ')}</span>`; i.className = `agent-status-item status-${s}`; }
function removeAgentStatusEntry(agentId) { if (!agentStatusContent) return; const i = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`); if (i) i.remove(); if (!agentStatusContent.hasChildNodes() || agentStatusContent.innerHTML.trim() === '') { agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>'; } }
function addRawLogEntry(data) { try { const t = JSON.stringify(data); console.debug("Raw:", t.substring(0, 300) + (t.length > 300 ? '...' : '')); } catch (e) { console.warn("Cannot stringify raw data:", data); } }


// --- Event Listeners ---
function setupEventListeners() {
    sendButton?.addEventListener('click', handleSendMessage);
    messageInput?.addEventListener('keypress', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSendMessage(); } });
    attachFileButton?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);
    addAgentButton?.addEventListener('click', () => openModal('agent-modal'));
    refreshConfigButton?.addEventListener('click', displayAgentConfigurations);
    agentForm?.addEventListener('submit', handleSaveAgent);
    overrideForm?.addEventListener('submit', handleSubmitOverride);
    navButtons?.forEach(button => { button.addEventListener('click', () => { const viewId = button.dataset.view; showView(viewId); }); });
    window.addEventListener('click', function(event) { if (event.target.classList.contains('modal')) closeModal(event.target.id); });
    console.log("Global event listeners setup complete (Bottom Nav).");
}

// --- Navigation Logic ---
function showView(viewId) {
    console.log(`Switching view to: ${viewId}`);
    viewPanels?.forEach(panel => { panel.classList.remove('active'); });
    navButtons?.forEach(button => { button.classList.remove('active'); });
    const targetPanel = document.getElementById(viewId);
    if (targetPanel) { targetPanel.classList.add('active'); }
    else { console.error(`View panel ${viewId} not found!`); document.getElementById('chat-view')?.classList.add('active'); viewId = 'chat-view'; }
    const targetButton = bottomNav?.querySelector(`button[data-view="${viewId}"]`);
    if (targetButton) { targetButton.classList.add('active'); }
}

// --- Action Handlers ---
function handleSendMessage() { const messageText = messageInput?.value.trim() ?? ''; if (!messageText && !currentFile) { return; } if (!ws || ws.readyState !== WebSocket.OPEN) { addMessage('system-log-area', '[SysErr] WS not connected.', 'error'); console.error("WS not open. State:", ws ? ws.readyState : 'null'); return; } const messageToSend = { type: currentFile ? 'user_message_with_file' : 'user_message', text: messageText }; if (currentFile && currentFileContent) { messageToSend.filename = currentFile.name; messageToSend.file_content = currentFileContent; } else if (currentFile && !currentFileContent) { console.error("File selected but no content."); addMessage('system-log-area', '[UI Err] File content missing.', 'error'); clearFileInput(); return; } const displayMessage = currentFile ? `[File: ${currentFile.name}]\n${messageText}` : messageText; addMessage('conversation-area', displayMessage, 'user'); try { const messageString = JSON.stringify(messageToSend); console.log(`Sending WS msg: ${messageString.substring(0,100)}...`); ws.send(messageString); } catch (error) { console.error("Error sending WS message:", error); addMessage('system-log-area', '[SysErr] Failed to send message.', 'error'); return; } if(messageInput) messageInput.value = ''; clearFileInput(); }

// --- File Handling ---
function handleFileSelect(event) { const f = event.target.files[0]; if (!f) { clearFileInput(); return; } const t=['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml']; const e=['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log']; const x='.' + f.name.split('.').pop().toLowerCase(); const v=t.includes(f.type) || e.includes(x); if (!v) { alert(`Unsupported type: ${f.type || x}. Upload text files.`); clearFileInput(); return; } const s=1*1024*1024; if (f.size > s) { alert(`File too large (${(f.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`); clearFileInput(); return; } const r=new FileReader(); r.onload = (ev) => { currentFile = { name: f.name, size: f.size, type: f.type }; currentFileContent = ev.target.result; displayFileInfo(); console.log(`File "${f.name}" selected.`); }; r.onerror = (ev) => { console.error("File read error:", ev); alert("Error reading file."); clearFileInput(); }; r.readAsText(f); }
function displayFileInfo() { if (!fileInfoArea) return; if (currentFile) { fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`; } else { fileInfoArea.innerHTML = ''; } }
function clearFileInput() { currentFile = null; currentFileContent = null; if(fileInput) fileInput.value = ''; displayFileInfo(); console.log("File input cleared."); }

// --- Configuration Management UI ---
async function displayAgentConfigurations() { if (!configContent) { console.warn("Config area not found."); return; } configContent.innerHTML = '<span class="status-placeholder">Loading...</span>'; try { const r = await fetch('/api/config/agents'); if (!r.ok) throw new Error(`HTTP ${r.status}`); const a = await r.json(); configContent.innerHTML = ''; if (!Array.isArray(a)) throw new Error("Expected agent array"); if (a.length === 0) { configContent.innerHTML = '<span class="status-placeholder">No static agents.</span>'; return; } a.sort((x, y) => (x.agent_id || "").localeCompare(y.agent_id || "")); a.forEach(g => { if (!g || !g.agent_id) return; const i = document.createElement('div'); i.classList.add('config-item'); const dT = `- ${g.provider || 'N/A'} / ${g.model || 'N/A'}`; i.innerHTML = `<span><strong></strong> (${g.agent_id}) <span class="agent-details"></span></span> <div class="config-item-actions"> <button class="config-action-button edit-button" data-id="${g.agent_id}" title="Edit">✏️</button> <button class="config-action-button delete-button" data-id="${g.agent_id}" title="Delete">🗑️</button> </div>`; i.querySelector('strong').textContent = g.persona || g.agent_id; i.querySelector('.agent-details').textContent = dT; configContent.appendChild(i); i.querySelector('.edit-button')?.addEventListener('click', () => openModal('agent-modal', g.agent_id)); i.querySelector('.delete-button')?.addEventListener('click', () => handleDeleteAgent(g.agent_id)); }); } catch (e) { console.error('Error fetching configs:', e); if(configContent) configContent.innerHTML = '<span class="status-placeholder error">Error loading.</span>'; addMessage('system-log-area', `[UI Error] Failed config load: ${e}`, 'error'); } }
async function handleSaveAgent(event) { event.preventDefault(); const f = event.target; if (!f) return; const iI = f.querySelector('#agent-id'); const eI = f.querySelector('#edit-agent-id'); if (!iI || !eI) return; const aId = iI.value.trim(); const eId = eI.value; const ed = !!eId; if (!aId || !/^[a-zA-Z0-9_-]+$/.test(aId)) { alert("Valid Agent ID required."); return; } const cfg = { provider: f.querySelector('#provider')?.value, model: f.querySelector('#model')?.value.trim() ?? '', persona: f.querySelector('#persona')?.value.trim() || aId, temperature: parseFloat(f.querySelector('#temperature')?.value) || 0.7, system_prompt: f.querySelector('#system_prompt')?.value.trim() || 'You are helpful.' }; if (!cfg.provider || !cfg.model) { alert("Provider/Model required."); return; } const u = ed ? `/api/config/agents/${eId}` : '/api/config/agents'; const m = ed ? 'PUT' : 'POST'; const p = ed ? cfg : { agent_id: aId, config: cfg }; console.log(`Sending ${m} to ${u}`); try { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) }); const x = await r.json(); if (r.ok && x.success) { alert(x.message || `Agent ${ed ? 'updated' : 'added'}. Restart.`); closeModal('agent-modal'); displayAgentConfigurations(); } else { throw new Error(x.detail || x.message || `Failed op.`); } } catch (e) { console.error(`Save agent err:`, e); alert(`Error: ${e.message}`); addMessage('system-log-area', `[UI Err] Save agent failed: ${e.message}`, 'error'); } }
async function handleDeleteAgent(agentId) { if (!confirm(`Delete static cfg '${agentId}'? Restart needed.`)) return; try { const r = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' }); const x = await r.json(); if (r.ok && x.success) { alert(x.message || 'Agent cfg deleted. Restart.'); displayAgentConfigurations(); } else { throw new Error(x.detail || x.message || 'Failed delete.'); } } catch (e) { console.error('Delete agent err:', e); alert(`Error: ${e.message}`); addMessage('system-log-area', `[UI Err] Delete agent failed: ${e.message}`, 'error'); } }


// --- Modal Handling ---
async function openModal(modalId, editId = null) { const m = document.getElementById(modalId); if (!m) { console.error(`Modal ${modalId} not found.`); return; } if (modalId === 'agent-modal') { const f = m.querySelector('#agent-form'); const t = m.querySelector('#modal-title'); const iI = f?.querySelector('#agent-id'); const eI = f?.querySelector('#edit-agent-id'); if (!f || !t || !iI || !eI) { console.error("Agent modal elements missing."); return; } f.reset(); eI.value = ''; iI.disabled = false; if (editId) { t.textContent = `Edit Agent: ${editId}`; eI.value = editId; iI.value = editId; iI.disabled = true; try { console.log(`Fetching list for edit: ${editId}`); const r = await fetch('/api/config/agents'); if (!r.ok) throw new Error('Fetch list failed.'); const a = await r.json(); const d = a.find(x => x.agent_id === editId); if (!d) throw new Error(`Agent ${editId} not found.`); f.querySelector('#persona').value = d.persona || editId; f.querySelector('#provider').value = d.provider || 'openrouter'; f.querySelector('#model').value = d.model || ''; console.warn("Edit modal prefilled limited data."); } catch (e) { console.error("Edit fetch err:", e); alert(`Load agent error: ${e.message}`); return; } } else { t.textContent = 'Add New Static Agent'; f.querySelector('#temperature').value = 0.7; f.querySelector('#provider').value = 'openrouter'; } } m.style.display = 'block'; }
function closeModal(modalId) { const m = document.getElementById(modalId); if (m) m.style.display = 'none'; const f = m?.querySelector('form'); if(f) f.reset(); if(modalId === 'agent-modal') { const eI = document.getElementById('edit-agent-id'); const iI = document.getElementById('agent-id'); if(eI) eI.value = ''; if(iI) iI.disabled = false; } }
function showOverrideModal(data) { if (!overrideModal) return; const aId = data.agent_id; const p = data.persona || aId; document.getElementById('override-agent-id').value = aId; document.getElementById('override-modal-title').textContent = `Override for: ${p}`; document.getElementById('override-message').textContent = data.message || `Agent '${p}' (${aId}) failed. Provide alternative.`; document.getElementById('override-last-error').textContent = data.last_error || "Unknown error"; const s = document.getElementById('override-provider'); const i = document.getElementById('override-model'); if (s && data.current_provider) s.value = data.current_provider; if (i) i.value = data.current_model || ''; openModal('override-modal'); }
function handleSubmitOverride(event) { event.preventDefault(); const aId = document.getElementById('override-agent-id')?.value; const nP = document.getElementById('override-provider')?.value; const nM = document.getElementById('override-model')?.value.trim(); if (!aId || !nP || !nM) { alert("Fill all override fields."); return; } const oD = { type: "submit_user_override", agent_id: aId, new_provider: nP, new_model: nM }; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(oD)); addMessage('system-log-area', `[UI] Submitted override for ${aId} (Prov: ${nP}, Model: ${nM}).`, 'status'); closeModal('override-modal'); } else { alert("WS not connected."); addMessage('system-log-area', `[UI Err] Override failed for ${aId}: WS not connected.`, 'error'); } }

// --- Make necessary functions globally accessible ---
window.closeModal = closeModal;
window.clearFileInput = clearFileInput;

console.log("Main app.js execution finished (Bottom Nav - Agent Label Fix).");
