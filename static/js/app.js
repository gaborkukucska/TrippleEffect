// START OF FILE static/js/app.js

// --- WebSocket Connection ---
let ws;
let reconnectInterval = 5000;
let currentFile = null;

// --- Swipe Navigation State ---
const contentWrapper = document.querySelector('.content-wrapper');
const swipeSections = document.querySelectorAll('.swipe-section');
const numSections = swipeSections.length;
let currentSectionIndex = 0;
let touchStartX = 0;
let touchStartY = 0; // Track Y position as well
let touchCurrentX = 0;
let isSwiping = false;
let horizontalSwipeConfirmed = false; // Flag to confirm horizontal intent
let swipeThreshold = 50;

// --- DOM Elements ---
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const agentStatusContent = document.getElementById('agent-status-content');
const configContent = document.getElementById('config-content');
const addAgentButton = document.getElementById('add-agent-button');
const refreshConfigButton = document.getElementById('refresh-config-button');
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
    console.log("DOM fully loaded (Swipe Sections v3).");
    if (!contentWrapper || swipeSections.length === 0 || !conversationArea || !systemLogArea || !messageInput || !agentStatusContent || !configContent) {
        console.error("Essential UI elements missing! Check HTML structure & IDs.");
        document.body.innerHTML = '<h1 style="color: red; text-align: center;">UI Initialization Error</h1>';
        return;
    }
    if (numSections > 0) {
        console.log(`Found ${numSections} swipe sections.`);
        setupWebSocket();
        setupEventListeners();
        displayAgentConfigurations();
        updateContentWrapperTransform(false); // Set initial position NO transition
    } else {
        console.error("Initialization Error: No '.swipe-section' elements found.");
    }
});

// --- WebSocket Setup ---
// (Remains unchanged)
function setupWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting WS connection: ${wsUrl}`);
    ws = new WebSocket(wsUrl);
    ws.onopen = () => { console.log('WS connected.'); updateLogStatus('Connected!', false); requestInitialAgentStatus(); reconnectInterval = 5000; };
    ws.onmessage = (event) => { try { const d = JSON.parse(event.data); handleWebSocketMessage(d); } catch (e) { console.error('WS parse error:', e); addMessage('system-log-area', `[SysErr] Bad msg: ${event.data}`, 'error'); } };
    ws.onerror = (e) => { console.error('WS error:', e); addMessage('system-log-area', '[WS Error] Connect error.', 'error'); updateLogStatus('Connect Error. Retry...', true); };
    ws.onclose = (e) => { console.log('WS closed:', e.reason, `(${e.code})`); const m = e.wasClean ? `[WS Closed] Code: ${e.code}.` : `[WS Closed] Lost connection. Code: ${e.code}. Reconnecting...`; const t = e.wasClean ? 'status' : 'error'; addMessage('system-log-area', m, t); updateLogStatus('Disconnected. Retry...', true); setTimeout(setupWebSocket, reconnectInterval); reconnectInterval = Math.min(reconnectInterval * 1.5, 30000); };
}

// --- WebSocket Message Handling ---
// (Remains unchanged)
function handleWebSocketMessage(data) {
    addRawLogEntry(data);
    switch (data.type) {
        case 'response_chunk': appendAgentResponseChunk(data.agent_id, data.content); break;
        case 'status': case 'system_event': addMessage('system-log-area', `[${data.agent_id || 'System'}] ${data.content || data.message || 'Status.'}`, 'status'); break;
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
// (Remain unchanged)
function addMessage(areaId, text, type = 'status', agentId = null) { const area = document.getElementById(areaId); if (!area) return; const p = area.querySelector('.initial-placeholder'); if (p) p.remove(); const d = document.createElement('div'); d.classList.add('message', type); if (agentId) d.dataset.agentId = agentId; if (areaId === 'system-log-area') { const t = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); const s = document.createElement('span'); s.classList.add('timestamp'); s.textContent = `[${t}] `; d.appendChild(s); } const c = document.createElement('span'); c.innerHTML = text.replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">").replace(/\n/g, '<br>'); d.appendChild(c); area.appendChild(d); if (area.closest('.swipe-section') === swipeSections[currentSectionIndex]) area.scrollTop = area.scrollHeight; }
function appendAgentResponseChunk(agentId, chunk) { const area = conversationArea; if (!area) return; let d = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`); if (!d) { const p = area.querySelector('.initial-placeholder'); if (p) p.remove(); d = document.createElement('div'); d.classList.add('message', 'agent_response', 'incomplete'); d.dataset.agentId = agentId; const l = document.createElement('strong'); l.textContent = `Agent @${agentId}:\n`; d.appendChild(l); area.appendChild(d); } const n = document.createTextNode(chunk); d.appendChild(n); if (area.closest('.swipe-section') === swipeSections[currentSectionIndex]) area.scrollTop = area.scrollHeight; }
function finalizeAgentResponse(agentId, finalContent) { const area = conversationArea; if (!area) return; let d = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`); if (d) { d.classList.remove('incomplete'); } else if (finalContent) { addMessage('conversation-area', `Agent @${agentId}:\n${finalContent}`, 'agent_response', agentId); } if (area.closest('.swipe-section') === swipeSections[currentSectionIndex]) area.scrollTop = area.scrollHeight; }
function updateLogStatus(message, isError = false) { const area = systemLogArea; if (!area) return; let s = area.querySelector('.status.initial-connecting'); if (!s) s = area.querySelector('.message.status:last-child'); if (!s && message) { addMessage('system-log-area', message, isError ? 'error' : 'status'); } else if (s) { s.textContent = message; s.className = `message status ${isError ? 'error' : ''}`; if (message === 'Connected to backend!') s.classList.remove('initial-connecting'); } }
function updateAgentStatusUI(agentId, statusData) { if (!agentStatusContent) return; const p = agentStatusContent.querySelector('.status-placeholder'); if (p) p.remove(); addOrUpdateAgentStatusEntry(agentId, statusData); }
function addOrUpdateAgentStatusEntry(agentId, statusData) { if (!agentStatusContent) return; let i = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`); if (!i) { i = document.createElement('div'); i.classList.add('agent-status-item'); i.dataset.agentId = agentId; agentStatusContent.appendChild(i); } const p = statusData?.persona || agentId; const s = statusData?.status || 'unknown'; const v = statusData?.provider || 'N/A'; const m = statusData?.model || 'N/A'; const t = statusData?.team || 'None'; i.title = `ID: ${agentId}\nProvider: ${v}\nModel: ${m}\nTeam: ${t}\nStatus: ${s}`; i.innerHTML = `<strong>${p}</strong> <span class="agent-model">(${m})</span> <span>[Team: ${t}]</span> <span class="agent-status">${s.replace('_', ' ')}</span>`; i.className = `agent-status-item status-${s}`; }
function removeAgentStatusEntry(agentId) { if (!agentStatusContent) return; const i = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`); if (i) i.remove(); if (!agentStatusContent.hasChildNodes() || agentStatusContent.innerHTML.trim() === '') { agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>'; } }
function addRawLogEntry(data) { try { const t = JSON.stringify(data); console.debug("Raw:", t.substring(0, 300) + (t.length > 300 ? '...' : '')); } catch (e) { console.warn("Cannot stringify raw data:", data); } }

// --- Event Listeners ---
// (setupEventListeners remains unchanged)
function setupEventListeners() {
    sendButton?.addEventListener('click', sendMessage);
    messageInput?.addEventListener('keypress', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
    attachFileButton?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);
    addAgentButton?.addEventListener('click', () => openModal('agent-modal'));
    refreshConfigButton?.addEventListener('click', displayAgentConfigurations);
    agentForm?.addEventListener('submit', handleSaveAgent);
    overrideForm?.addEventListener('submit', handleSubmitOverride);
    if (contentWrapper) { contentWrapper.addEventListener('touchstart', handleTouchStart, { passive: false }); contentWrapper.addEventListener('touchmove', handleTouchMove, { passive: false }); contentWrapper.addEventListener('touchend', handleTouchEnd); contentWrapper.addEventListener('touchcancel', handleTouchEnd); console.log("Swipe listeners added."); } else { console.error("Content wrapper not found!"); }
    document.addEventListener('keydown', (e) => { const t = document.activeElement?.tagName.toLowerCase(); const m = agentModal?.style.display !== 'none' || overrideModal?.style.display !== 'none'; const i = ['textarea', 'input', 'select'].includes(t); if (m || i) return; if (e.key === 'ArrowLeft' && currentSectionIndex > 0) { console.log("Key Left -> Prev Section"); currentSectionIndex--; updateContentWrapperTransform(); } else if (e.key === 'ArrowRight' && currentSectionIndex < numSections - 1) { console.log("Key Right -> Next Section"); currentSectionIndex++; updateContentWrapperTransform(); } });
}

// --- Send Message ---
// (sendMessage remains unchanged)
function sendMessage() { const t = messageInput?.value.trim() ?? ''; if (!t && !currentFile) return; if (!ws || ws.readyState !== WebSocket.OPEN) { addMessage('system-log-area', '[SysErr] WS not connected.', 'error'); return; } const o = { type: currentFile ? 'user_message_with_file' : 'user_message', text: t }; if (currentFile) { o.filename = currentFile.name; o.file_content = currentFile.content; } const d = currentFile ? `[File: ${currentFile.name}]\n${t}` : t; addMessage('conversation-area', d, 'user'); ws.send(JSON.stringify(o)); if(messageInput) messageInput.value = ''; clearFileInput(); }

// --- File Handling ---
// (handleFileSelect, displayFileInfo, clearFileInput remain unchanged)
function handleFileSelect(event) { const f = event.target.files[0]; if (!f) { clearFileInput(); return; } const t = ['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml']; const e = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log']; const x = '.' + f.name.split('.').pop().toLowerCase(); if (!t.includes(f.type) && !e.includes(x)) { alert(`Unsupported type: ${f.type || x}. Upload text files.`); clearFileInput(); return; } const s = 1 * 1024 * 1024; if (f.size > s) { alert(`File too large (${(f.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`); clearFileInput(); return; } const r = new FileReader(); r.onload = (ev) => { currentFile = { name: f.name, content: ev.target.result, size: f.size, type: f.type }; displayFileInfo(); } r.onerror = (ev) => { console.error("File read error:", ev); alert("Error reading file."); clearFileInput(); } r.readAsText(f); }
function displayFileInfo() { if (!fileInfoArea) return; if (currentFile) { fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`; } else { fileInfoArea.innerHTML = ''; } }
function clearFileInput() { currentFile = null; if(fileInput) fileInput.value = ''; displayFileInfo(); }

// --- Swipe Navigation Handlers ---
function handleTouchStart(event) {
    const target = event.target;
    // Allow swipe ONLY if the touch starts directly on the wrapper or section,
    // NOT on elements known to scroll or be interactive within a section.
    const isDirectTarget = target === contentWrapper || target.classList.contains('swipe-section');
    const isInteractive = target.closest('button, input, textarea, select, a, .modal-content');
    const isScrollable = target.closest('.message-area, #agent-status-content, #config-content');
    const isModalOpen = agentModal?.style.display !== 'none' || overrideModal?.style.display !== 'none';

    if (!isDirectTarget || isInteractive || isScrollable || isModalOpen) {
        isSwiping = false;
        console.log(`Swipe ignored (target: ${target.tagName}, direct: ${isDirectTarget}, interact: ${!!isInteractive}, scroll: ${!!isScrollable}, modal: ${isModalOpen})`);
        return; // Exit if interaction is likely needed or target is wrong
    }

    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY; // Record Y start
    touchCurrentX = touchStartX;
    isSwiping = true;
    horizontalSwipeConfirmed = false; // Reset confirmation flag
    if (contentWrapper) contentWrapper.style.transition = 'none';
    console.log(`TouchStart: startX=${touchStartX.toFixed(0)}`);
}

function handleTouchMove(event) {
    if (!isSwiping) return;

    const currentY = event.touches[0].clientY;
    touchCurrentX = event.touches[0].clientX;
    const diffX = touchCurrentX - touchStartX;
    const diffY = currentY - touchStartY;

    // Determine dominant direction ONCE per swipe
    if (!horizontalSwipeConfirmed) {
        if (Math.abs(diffX) > Math.abs(diffY) + 5) { // Check if horizontal movement is clearly dominant
            horizontalSwipeConfirmed = true;
            console.log("Horizontal swipe confirmed.");
        } else if (Math.abs(diffY) > Math.abs(diffX) + 5) {
            // Vertical scroll is dominant, stop swipe attempt
            isSwiping = false;
            console.log("Vertical scroll detected, canceling swipe.");
            return;
        }
        // Else: direction not yet clear, continue monitoring
    }

    // Only prevent default and apply transform if horizontal swipe is confirmed
    if (horizontalSwipeConfirmed) {
        event.preventDefault(); // Prevent vertical scrolling
        const baseTranslateXPercent = -currentSectionIndex * 100;
        if (contentWrapper) {
             contentWrapper.style.transform = `translateX(calc(${baseTranslateXPercent}% + ${diffX}px))`;
        }
    }
}

function handleTouchEnd(event) {
    if (!isSwiping) return;
    const wasHorizontal = horizontalSwipeConfirmed; // Store confirmation state
    // Reset flags
    isSwiping = false;
    horizontalSwipeConfirmed = false;

    // Only process page change if horizontal swipe was confirmed
    if (wasHorizontal) {
        const diffX = touchCurrentX - touchStartX;
        console.log(`TouchEnd: diffX=${diffX.toFixed(0)}, threshold=${swipeThreshold}`);

        let finalSectionIndex = currentSectionIndex;

        if (Math.abs(diffX) > swipeThreshold) {
            if (diffX < 0 && currentSectionIndex < numSections - 1) { // Swipe Left
                finalSectionIndex++; console.log("Swipe Left -> New Index:", finalSectionIndex);
            } else if (diffX > 0 && currentSectionIndex > 0) { // Swipe Right
                finalSectionIndex--; console.log("Swipe Right -> New Index:", finalSectionIndex);
            } else { console.log("Swipe threshold met but at boundary."); }
        } else { console.log("Swipe distance below threshold."); }

        currentSectionIndex = finalSectionIndex;
    } else {
         console.log("TouchEnd: No horizontal swipe confirmed. Snapping back.");
    }

    updateContentWrapperTransform(true); // Animate to the final section position
}

function updateContentWrapperTransform(useTransition = true) {
    if (contentWrapper) {
        // Ensure index is valid
        currentSectionIndex = Math.max(0, Math.min(numSections - 1, currentSectionIndex));
        const newTranslateXPercent = -currentSectionIndex * 100;
        console.log(`Updating transform: Index=${currentSectionIndex}, TranslateX=${newTranslateXPercent}% (transition: ${useTransition})`);
        contentWrapper.style.transition = useTransition ? 'transform 0.3s ease-in-out' : 'none';
        contentWrapper.style.transform = `translateX(${newTranslateXPercent}%)`;
    } else {
        console.error("contentWrapper not found in updateContentWrapperTransform!");
    }
}

// --- Configuration Management UI ---
// (displayAgentConfigurations, handleSaveAgent, handleDeleteAgent remain unchanged)
async function displayAgentConfigurations() { if (!configContent) { console.warn("Config area not found."); return; } configContent.innerHTML = '<span class="status-placeholder">Loading...</span>'; try { const r = await fetch('/api/config/agents'); if (!r.ok) throw new Error(`HTTP ${r.status}`); const a = await r.json(); configContent.innerHTML = ''; if (a.length === 0) { configContent.innerHTML = '<span class="status-placeholder">No static agents.</span>'; return; } a.sort((x, y) => x.agent_id.localeCompare(y.agent_id)); a.forEach(g => { const i = document.createElement('div'); i.classList.add('config-item'); i.innerHTML = `<span><strong>${g.persona || g.agent_id}</strong> (${g.agent_id}) <span class="agent-details">- ${g.provider} / ${g.model}</span></span> <div class="config-item-actions"> <button class="config-action-button edit-button" data-id="${g.agent_id}" title="Edit">✏️</button> <button class="config-action-button delete-button" data-id="${g.agent_id}" title="Delete">🗑️</button> </div>`; configContent.appendChild(i); i.querySelector('.edit-button')?.addEventListener('click', () => openModal('agent-modal', g.agent_id)); i.querySelector('.delete-button')?.addEventListener('click', () => handleDeleteAgent(g.agent_id)); }); } catch (e) { console.error('Error fetching configs:', e); if(configContent) configContent.innerHTML = '<span class="status-placeholder error">Error loading.</span>'; addMessage('system-log-area', `[UI Error] Failed config load: ${e}`, 'error'); } }
async function handleSaveAgent(event) { event.preventDefault(); const f = event.target; if (!f) return; const iI = f.querySelector('#agent-id'); const eI = f.querySelector('#edit-agent-id'); if (!iI || !eI) return; const aId = iI.value.trim(); const eId = eI.value; const ed = !!eId; if (!aId || !/^[a-zA-Z0-9_-]+$/.test(aId)) { alert("Valid Agent ID required."); return; } const cfg = { provider: f.querySelector('#provider')?.value, model: f.querySelector('#model')?.value.trim() ?? '', persona: f.querySelector('#persona')?.value.trim() || aId, temperature: parseFloat(f.querySelector('#temperature')?.value) || 0.7, system_prompt: f.querySelector('#system_prompt')?.value.trim() || 'You are helpful.' }; if (!cfg.provider || !cfg.model) { alert("Provider/Model required."); return; } const u = ed ? `/api/config/agents/${eId}` : '/api/config/agents'; const m = ed ? 'PUT' : 'POST'; const p = ed ? cfg : { agent_id: aId, config: cfg }; console.log(`Sending ${m} to ${u}`); try { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) }); const x = await r.json(); if (r.ok && x.success) { alert(x.message || `Agent ${ed ? 'updated' : 'added'}. Restart.`); closeModal('agent-modal'); displayAgentConfigurations(); } else { throw new Error(x.detail || x.message || `Failed op.`); } } catch (e) { console.error(`Save agent err:`, e); alert(`Error: ${e.message}`); addMessage('system-log-area', `[UI Err] Save agent failed: ${e.message}`, 'error'); } }
async function handleDeleteAgent(agentId) { if (!confirm(`Delete static cfg '${agentId}'? Restart needed.`)) return; try { const r = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' }); const x = await r.json(); if (r.ok && x.success) { alert(x.message || 'Agent cfg deleted. Restart.'); displayAgentConfigurations(); } else { throw new Error(x.detail || x.message || 'Failed delete.'); } } catch (e) { console.error('Delete agent err:', e); alert(`Error: ${e.message}`); addMessage('system-log-area', `[UI Err] Delete agent failed: ${e.message}`, 'error'); } }

// --- Modal Handling ---
// (openModal, closeModal, showOverrideModal, handleSubmitOverride remain unchanged)
async function openModal(modalId, editId = null) { const m = document.getElementById(modalId); if (!m) { console.error(`Modal ${modalId} not found.`); return; } if (modalId === 'agent-modal') { const f = m.querySelector('#agent-form'); const t = m.querySelector('#modal-title'); const iI = f?.querySelector('#agent-id'); const eI = f?.querySelector('#edit-agent-id'); if (!f || !t || !iI || !eI) { console.error("Agent modal elements missing."); return; } f.reset(); eI.value = ''; iI.disabled = false; if (editId) { t.textContent = `Edit Agent: ${editId}`; eI.value = editId; iI.value = editId; iI.disabled = true; try { console.log(`Fetching list for edit: ${editId}`); const r = await fetch('/api/config/agents'); if (!r.ok) throw new Error('Fetch agent list failed.'); const a = await r.json(); const d = a.find(x => x.agent_id === editId); if (!d) throw new Error(`Agent ${editId} not found.`); f.querySelector('#persona').value = d.persona || editId; f.querySelector('#provider').value = d.provider || 'openrouter'; f.querySelector('#model').value = d.model || ''; console.warn("Edit modal prefilled limited data."); } catch (e) { console.error("Edit fetch err:", e); alert(`Load agent error: ${e.message}`); return; } } else { t.textContent = 'Add New Static Agent'; f.querySelector('#temperature').value = 0.7; f.querySelector('#provider').value = 'openrouter'; } } m.style.display = 'block'; }
function closeModal(modalId) { const m = document.getElementById(modalId); if (m) m.style.display = 'none'; const f = m?.querySelector('form'); if(f) f.reset(); if(modalId === 'agent-modal') { const eI = document.getElementById('edit-agent-id'); const iI = document.getElementById('agent-id'); if(eI) eI.value = ''; if(iI) iI.disabled = false; } }
window.onclick = function(event) { if (event.target.classList.contains('modal')) closeModal(event.target.id); }
function showOverrideModal(data) { if (!overrideModal) return; const aId = data.agent_id; const p = data.persona || aId; document.getElementById('override-agent-id').value = aId; document.getElementById('override-modal-title').textContent = `Override for: ${p}`; document.getElementById('override-message').textContent = data.message || `Agent '${p}' (${aId}) failed. Provide alternative.`; document.getElementById('override-last-error').textContent = data.last_error || "Unknown error"; const s = document.getElementById('override-provider'); const i = document.getElementById('override-model'); if (s && data.current_provider) s.value = data.current_provider; if (i) i.value = data.current_model || ''; openModal('override-modal'); }
function handleSubmitOverride(event) { event.preventDefault(); const aId = document.getElementById('override-agent-id')?.value; const nP = document.getElementById('override-provider')?.value; const nM = document.getElementById('override-model')?.value.trim(); if (!aId || !nP || !nM) { alert("Fill all override fields."); return; } const oD = { type: "submit_user_override", agent_id: aId, new_provider: nP, new_model: nM }; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(oD)); addMessage('system-log-area', `[UI] Submitted override for ${aId} (Prov: ${nP}, Model: ${nM}).`, 'status'); closeModal('override-modal'); } else { alert("WS not connected."); addMessage('system-log-area', `[UI Err] Override failed for ${aId}: WS not connected.`, 'error'); } }
