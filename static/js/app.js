// START OF FILE static/js/app.js

// --- WebSocket Connection ---
let ws; // WebSocket instance
let reconnectInterval = 5000; // Reconnect attempt interval in ms
let currentFile = null; // To store the currently selected file object

// --- Swipe Navigation State ---
const contentWrapper = document.querySelector('.content-wrapper'); // Target the new wrapper
const swipeSections = document.querySelectorAll('.swipe-section'); // Target the sections
const numSections = swipeSections.length;
let currentSectionIndex = 0; // Start at the main section (index 0)
let touchStartX = 0;
let touchStartY = 0; // Track Y position as well
let touchCurrentX = 0;
let isSwiping = false;
let horizontalSwipeConfirmed = false; // Flag to confirm horizontal intent
let swipeThreshold = 50; // Minimum pixels to register as a swipe

// --- DOM Elements (Ensure correct selectors within sections) ---
// Section 1 Elements
const mainSection = document.getElementById('main-section');
const conversationArea = document.getElementById('conversation-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const agentStatusContent = document.getElementById('agent-status-content');
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');

// Section 2 Elements
const logSection = document.getElementById('log-section');
const systemLogArea = document.getElementById('system-log-area');

// Section 3 Elements
const configSection = document.getElementById('config-section');
const configContent = document.getElementById('config-content');
const addAgentButton = document.getElementById('add-agent-button');
const refreshConfigButton = document.getElementById('refresh-config-button');

// Modals (Remain global)
const agentModal = document.getElementById('agent-modal');
const overrideModal = document.getElementById('override-modal');
const agentForm = document.getElementById('agent-form');
const overrideForm = document.getElementById('override-form');
const modalTitle = document.getElementById('modal-title');

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded (Swipe Sections - Full JS).");
    // Check for essential elements for swipe layout
    if (!contentWrapper || swipeSections.length === 0 || !conversationArea || !systemLogArea || !messageInput || !agentStatusContent || !configContent) {
        console.error("Essential UI elements for swipe section layout not found! Check HTML structure & IDs.");
        document.body.innerHTML = '<h1 style="color: red; text-align: center;">UI Initialization Error: Swipe elements missing.</h1>';
        return;
    }
    if (numSections > 0) {
         console.log(`Found ${numSections} swipe sections.`);
         setupWebSocket();
         setupEventListeners();
         displayAgentConfigurations(); // Initial load attempt for config section content
         updateContentWrapperTransform(false); // Set initial section position visually WITHOUT transition
    } else {
        console.error("Initialization Error: No elements with class 'swipe-section' found.");
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
        updateLogStatus('Connected!', false);
        requestInitialAgentStatus();
        reconnectInterval = 5000;
    };

    ws.onmessage = (event) => {
        try {
            const messageData = JSON.parse(event.data);
            handleWebSocketMessage(messageData);
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
        const message = event.wasClean
            ? `[WS Closed] Code: ${event.code}.`
            : `[WS Closed] Lost connection. Code: ${event.code}. Reconnecting...`;
        const type = event.wasClean ? 'status' : 'error';
        addMessage('system-log-area', message, type);
        updateLogStatus('Disconnected. Retry...', true);
        setTimeout(setupWebSocket, reconnectInterval);
        reconnectInterval = Math.min(reconnectInterval * 1.5, 30000);
    };
}

// --- WebSocket Message Handling ---
function handleWebSocketMessage(data) {
    addRawLogEntry(data); // Log raw data for debugging

    // Process message based on type
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
                status: data.config?.status || 'idle', // Assuming default status
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
             requestAgentStatus(data.agent_id); // Request specific update
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
    // if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "get_initial_status" })); }
}
function requestAgentStatus(agentId) {
    console.log(`Requesting status update for agent ${agentId} (needs backend implementation)...`);
    // if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "get_agent_status", agent_id: agentId })); }
}

// --- UI Update Functions ---
function addMessage(areaId, text, type = 'status', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) { console.error(`Message area #${areaId} not found.`); return; }
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
    // Use textContent to prevent XSS, replace newlines with <br> for display
    contentSpan.innerHTML = text.replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">").replace(/\n/g, '<br>');
    messageDiv.appendChild(contentSpan);

    area.appendChild(messageDiv);
    // Scroll only if the area is within the currently active section
    const currentSection = swipeSections[currentSectionIndex];
    if (area.closest('.swipe-section') === currentSection) {
        // Scroll smoothly after a short delay to allow rendering
        setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
    }
}

function appendAgentResponseChunk(agentId, chunk) {
    const area = conversationArea; if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);
    if (!agentMsgDiv) {
        const placeholder = area.querySelector('.initial-placeholder'); if (placeholder) placeholder.remove();
        agentMsgDiv = document.createElement('div'); agentMsgDiv.classList.add('message', 'agent_response', 'incomplete');
        agentMsgDiv.dataset.agentId = agentId; const label = document.createElement('strong');
        label.textContent = `Agent @${agentId}:\n`; agentMsgDiv.appendChild(label); area.appendChild(agentMsgDiv);
    }
    const chunkNode = document.createTextNode(chunk); agentMsgDiv.appendChild(chunkNode);
    // Scroll only if the area is within the currently active section
    if (area.closest('.swipe-section') === swipeSections[currentSectionIndex]) {
         // Scroll smoothly after a short delay
        setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
    }
}

function finalizeAgentResponse(agentId, finalContent) {
    const area = conversationArea; if (!area) return;
    let agentMsgDiv = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"].incomplete`);
    if (agentMsgDiv) { agentMsgDiv.classList.remove('incomplete'); }
    else if (finalContent) { addMessage('conversation-area', `Agent @${agentId}:\n${finalContent}`, 'agent_response', agentId); }
    if (area.closest('.swipe-section') === swipeSections[currentSectionIndex]) {
         setTimeout(() => { area.scrollTop = area.scrollHeight; }, 50);
    }
}

function updateLogStatus(message, isError = false) {
    const area = systemLogArea; if (!area) return;
    let statusDiv = area.querySelector('.status.initial-connecting');
    if (!statusDiv) statusDiv = area.querySelector('.message.status:last-child');
    if (!statusDiv && message) { addMessage('system-log-area', message, isError ? 'error' : 'status'); }
    else if (statusDiv) { statusDiv.textContent = message; statusDiv.className = `message status ${isError ? 'error' : ''}`; if (message === 'Connected to backend!') statusDiv.classList.remove('initial-connecting'); }
}

function updateAgentStatusUI(agentId, statusData) {
    if (!agentStatusContent) return; const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove(); addOrUpdateAgentStatusEntry(agentId, statusData);
}

function addOrUpdateAgentStatusEntry(agentId, statusData) {
     if (!agentStatusContent) return; let itemDiv = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);
     if (!itemDiv) { itemDiv = document.createElement('div'); itemDiv.classList.add('agent-status-item'); itemDiv.dataset.agentId = agentId; agentStatusContent.appendChild(itemDiv); }
     const persona = statusData?.persona || agentId; const status = statusData?.status || 'unknown'; const provider = statusData?.provider || 'N/A'; const model = statusData?.model || 'N/A'; const team = statusData?.team || 'None';
     itemDiv.title = `ID: ${agentId}\nProvider: ${provider}\nModel: ${model}\nTeam: ${team}\nStatus: ${status}`;
     itemDiv.innerHTML = `<strong>${persona}</strong> <span class="agent-model">(${model})</span> <span>[Team: ${team}]</span> <span class="agent-status">${status.replace('_', ' ')}</span>`;
     itemDiv.className = `agent-status-item status-${status}`;
}

function removeAgentStatusEntry(agentId) {
    if (!agentStatusContent) return; const itemDiv = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);
    if (itemDiv) itemDiv.remove(); if (!agentStatusContent.hasChildNodes() || agentStatusContent.innerHTML.trim() === '') { agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>'; }
}

function addRawLogEntry(data) {
    try {
        const logText = JSON.stringify(data);
        console.debug("Raw WS Data:", logText.substring(0, 500) + (logText.length > 500 ? '...' : ''));
    } catch (e) { console.warn("Could not stringify raw WS data:", data); }
}

// --- Event Listeners ---
function setupEventListeners() {
    // Ensure elements exist before adding listeners
    sendButton?.addEventListener('click', sendMessage);
    messageInput?.addEventListener('keypress', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
    attachFileButton?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);
    addAgentButton?.addEventListener('click', () => openModal('agent-modal'));
    refreshConfigButton?.addEventListener('click', displayAgentConfigurations);
    agentForm?.addEventListener('submit', handleSaveAgent);
    overrideForm?.addEventListener('submit', handleSubmitOverride);

    // Swipe Listeners on the wrapper
    if (contentWrapper) {
        contentWrapper.addEventListener('touchstart', handleTouchStart, { passive: false });
        contentWrapper.addEventListener('touchmove', handleTouchMove, { passive: false });
        contentWrapper.addEventListener('touchend', handleTouchEnd);
        contentWrapper.addEventListener('touchcancel', handleTouchEnd); // Treat cancel like end
        console.log("Swipe event listeners added to content wrapper.");
    } else { console.error("Content wrapper not found for swipe listeners!"); }

     // Keyboard listeners
     document.addEventListener('keydown', (e) => {
        const targetTagName = document.activeElement?.tagName.toLowerCase();
        const isModalOpen = agentModal?.style.display !== 'none' || overrideModal?.style.display !== 'none';
        const isInputFocused = ['textarea', 'input', 'select'].includes(targetTagName);
        if (isModalOpen || isInputFocused) return; // Ignore if modal open or input focused

        if (e.key === 'ArrowLeft' && currentSectionIndex > 0) {
            console.log("Key Left -> Previous Section");
            currentSectionIndex--;
            updateContentWrapperTransform(true); // Use transition
        } else if (e.key === 'ArrowRight' && currentSectionIndex < numSections - 1) {
             console.log("Key Right -> Next Section");
            currentSectionIndex++;
            updateContentWrapperTransform(true); // Use transition
        }
    });
}

// --- Send Message ---
function sendMessage() {
    const messageText = messageInput?.value.trim() ?? ''; if (!messageText && !currentFile) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) { addMessage('system-log-area', '[SysErr] WS not connected.', 'error'); return; }
    const messageToSend = { type: currentFile ? 'user_message_with_file' : 'user_message', text: messageText };
    if (currentFile) { messageToSend.filename = currentFile.name; messageToSend.file_content = currentFile.content; }
    const displayMessage = currentFile ? `[File: ${currentFile.name}]\n${messageText}` : messageText;
    addMessage('conversation-area', displayMessage, 'user');
    ws.send(JSON.stringify(messageToSend));
    if(messageInput) messageInput.value = ''; clearFileInput();
}

// --- File Handling ---
function handleFileSelect(event) { const f = event.target.files[0]; if (!f) { clearFileInput(); return; } const t = ['text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/css', 'application/javascript', 'application/json', 'application/x-yaml', 'application/yaml']; const e = ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.yaml', '.yml', '.csv', '.log']; const x = '.' + f.name.split('.').pop().toLowerCase(); if (!t.includes(f.type) && !e.includes(x)) { alert(`Unsupported type: ${f.type || x}. Upload text files.`); clearFileInput(); return; } const s = 1 * 1024 * 1024; if (f.size > s) { alert(`File too large (${(f.size / 1024 / 1024).toFixed(2)} MB). Max 1 MB.`); clearFileInput(); return; } const r = new FileReader(); r.onload = (ev) => { currentFile = { name: f.name, content: ev.target.result, size: f.size, type: f.type }; displayFileInfo(); } r.onerror = (ev) => { console.error("File read error:", ev); alert("Error reading file."); clearFileInput(); } r.readAsText(f); }
function displayFileInfo() { if (!fileInfoArea) return; if (currentFile) { fileInfoArea.innerHTML = `<span>📎 ${currentFile.name} (${(currentFile.size / 1024).toFixed(1)} KB)</span><button onclick="clearFileInput()" title="Remove file">×</button>`; } else { fileInfoArea.innerHTML = ''; } }
function clearFileInput() { currentFile = null; if(fileInput) fileInput.value = ''; displayFileInfo(); }

// --- Swipe Navigation Handlers ---
function handleTouchStart(event) {
    const target = event.target;
    // Allow swipe ONLY if the touch starts directly on the wrapper or section background,
    // AND NOT on elements known to scroll or be interactive within a section.
    const isDirectTarget = target === contentWrapper || target.classList.contains('swipe-section') || target.classList.contains('section-content');
    const isInteractive = target.closest('button, input, textarea, select, a, .modal-content');
    // Check if the touch started INSIDE a scrollable area
    const isInsideScrollable = target.closest('.message-area, #agent-status-content, #config-content');
    const isModalOpen = agentModal?.style.display !== 'none' || overrideModal?.style.display !== 'none';

    // Ignore swipe if touching interactive element, modal is open, or touch starts inside a scrollable div
    if (isInteractive || isModalOpen || isInsideScrollable) {
        isSwiping = false;
        console.log(`Swipe ignored (target: ${target.tagName}, direct: ${isDirectTarget}, interact: ${!!isInteractive}, insideScroll: ${!!isInsideScrollable}, modal: ${isModalOpen})`);
        return; // Exit if interaction is likely needed or target is wrong
    }

    // Proceed with swipe initialization
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
    touchCurrentX = touchStartX;
    isSwiping = true;
    horizontalSwipeConfirmed = false;
    if (contentWrapper) contentWrapper.style.transition = 'none';
    console.log(`TouchStart: startX=${touchStartX.toFixed(0)}`);
}

function handleTouchMove(event) {
    if (!isSwiping) return; // Exit if not currently swiping

    const currentY = event.touches[0].clientY;
    touchCurrentX = event.touches[0].clientX;
    const diffX = touchCurrentX - touchStartX;
    const diffY = currentY - touchStartY;

    // Determine dominant direction ONCE per swipe
    if (!horizontalSwipeConfirmed) {
        if (Math.abs(diffX) > Math.abs(diffY) + 5) { // Horizontal movement dominant
            horizontalSwipeConfirmed = true;
            console.log("Horizontal swipe confirmed.");
        } else if (Math.abs(diffY) > Math.abs(diffX) + 5) { // Vertical movement dominant
            isSwiping = false; // Cancel swipe
            console.log("Vertical scroll detected, canceling swipe.");
            return; // Allow default vertical scroll
        }
    }

    // Only prevent default and apply transform if horizontal swipe is confirmed
    if (horizontalSwipeConfirmed) {
        event.preventDefault(); // Prevent page scroll during horizontal drag
        const baseTranslateXPercent = -currentSectionIndex * 100;
        if (contentWrapper) {
             // Apply immediate transform feedback based on drag distance
             contentWrapper.style.transform = `translateX(calc(${baseTranslateXPercent}% + ${diffX}px))`;
        }
    }
}

function handleTouchEnd(event) {
    if (!isSwiping) return; // Exit if swipe was cancelled (e.g., vertical scroll)
    const wasHorizontal = horizontalSwipeConfirmed; // Store confirmation state
    // Reset flags
    isSwiping = false;
    horizontalSwipeConfirmed = false;

    // Only process section change if horizontal swipe was confirmed
    if (wasHorizontal) {
        const diffX = touchCurrentX - touchStartX;
        console.log(`TouchEnd (Horizontal): diffX=${diffX.toFixed(0)}, threshold=${swipeThreshold}`);

        let finalSectionIndex = currentSectionIndex; // Start with current index

        // Determine if swipe was significant enough to change section
        if (Math.abs(diffX) > swipeThreshold) {
            if (diffX < 0 && currentSectionIndex < numSections - 1) { // Swipe Left
                finalSectionIndex++; console.log("Swipe Left -> New Index:", finalSectionIndex);
            } else if (diffX > 0 && currentSectionIndex > 0) { // Swipe Right
                finalSectionIndex--; console.log("Swipe Right -> New Index:", finalSectionIndex);
            } else { console.log("Swipe threshold met but at section boundary."); }
        } else { console.log("Swipe distance below threshold."); }

        currentSectionIndex = finalSectionIndex; // Update the global index
    } else {
         console.log("TouchEnd: No horizontal swipe confirmed. Snapping back.");
    }

    updateContentWrapperTransform(true); // Animate to the final section position (snap back or move)
}

function updateContentWrapperTransform(useTransition = true) {
    if (contentWrapper) {
        // Ensure index is valid (clamp between 0 and numSections - 1)
        currentSectionIndex = Math.max(0, Math.min(numSections - 1, currentSectionIndex));
        const newTranslateXPercent = -currentSectionIndex * 100;
        console.log(`Updating transform: Index=${currentSectionIndex}, TranslateX=${newTranslateXPercent}% (transition: ${useTransition})`);
        // Apply or remove transition based on flag
        contentWrapper.style.transition = useTransition ? 'transform 0.3s ease-in-out' : 'none';
        // Apply final transform
        contentWrapper.style.transform = `translateX(${newTranslateXPercent}%)`;
    } else {
        console.error("contentWrapper not found in updateContentWrapperTransform!");
    }
}


// --- Configuration Management UI ---
async function displayAgentConfigurations() {
    if (!configContent) { console.warn("Config area not found."); return; }
    configContent.innerHTML = '<span class="status-placeholder">Loading...</span>';
    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const agents = await response.json();
        configContent.innerHTML = '';
        if (agents.length === 0) { configContent.innerHTML = '<span class="status-placeholder">No static agents.</span>'; return; }
        agents.sort((a, b) => a.agent_id.localeCompare(b.agent_id));
        agents.forEach(agent => {
            const item = document.createElement('div'); item.classList.add('config-item');
            item.innerHTML = `<span><strong>${agent.persona || agent.agent_id}</strong> (${agent.agent_id}) <span class="agent-details">- ${agent.provider} / ${agent.model}</span></span> <div class="config-item-actions"> <button class="config-action-button edit-button" data-id="${agent.agent_id}" title="Edit">✏️</button> <button class="config-action-button delete-button" data-id="${agent.agent_id}" title="Delete">🗑️</button> </div>`;
            configContent.appendChild(item);
            item.querySelector('.edit-button')?.addEventListener('click', () => openModal('agent-modal', agent.agent_id));
            item.querySelector('.delete-button')?.addEventListener('click', () => handleDeleteAgent(agent.agent_id));
        });
    } catch (error) {
        console.error('Error fetching configs:', error);
        if(configContent) configContent.innerHTML = '<span class="status-placeholder error">Error loading.</span>';
        addMessage('system-log-area', `[UI Error] Failed config load: ${error}`, 'error');
    }
}

async function handleSaveAgent(event) {
    event.preventDefault(); const form = event.target; if (!form) return;
    const agentIdInput = form.querySelector('#agent-id'); const editAgentIdInput = form.querySelector('#edit-agent-id'); if (!agentIdInput || !editAgentIdInput) return;
    const agentId = agentIdInput.value.trim(); const editAgentId = editAgentIdInput.value; const isEditing = !!editAgentId;
    if (!agentId || !/^[a-zA-Z0-9_-]+$/.test(agentId)) { alert("Valid Agent ID required."); return; }
    const agentConfig = { provider: form.querySelector('#provider')?.value, model: form.querySelector('#model')?.value.trim() ?? '', persona: form.querySelector('#persona')?.value.trim() || agentId, temperature: parseFloat(form.querySelector('#temperature')?.value) || 0.7, system_prompt: form.querySelector('#system_prompt')?.value.trim() || 'You are helpful.' };
    if (!agentConfig.provider || !agentConfig.model) { alert("Provider/Model required."); return; }
    const url = isEditing ? `/api/config/agents/${editAgentId}` : '/api/config/agents'; const method = isEditing ? 'PUT' : 'POST'; const payload = isEditing ? agentConfig : { agent_id: agentId, config: agentConfig }; console.log(`Sending ${method} to ${url}`);
    try {
        const response = await fetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); const result = await response.json();
        if (response.ok && result.success) { alert(result.message || `Agent ${isEditing ? 'updated' : 'added'}. Restart.`); closeModal('agent-modal'); displayAgentConfigurations(); }
        else { throw new Error(result.detail || result.message || `Failed op.`); }
    } catch (error) { console.error(`Save agent err:`, error); alert(`Error: ${error.message}`); addMessage('system-log-area', `[UI Err] Save agent failed: ${error.message}`, 'error'); }
}

async function handleDeleteAgent(agentId) {
    if (!confirm(`Delete static cfg '${agentId}'? Restart needed.`)) return;
    try {
        const response = await fetch(`/api/config/agents/${agentId}`, { method: 'DELETE' }); const result = await response.json();
        if (response.ok && result.success) { alert(result.message || 'Agent cfg deleted. Restart.'); displayAgentConfigurations(); }
        else { throw new Error(result.detail || result.message || 'Failed delete.'); }
    } catch (error) { console.error('Delete agent err:', e); alert(`Error: ${error.message}`); addMessage('system-log-area', `[UI Err] Delete agent failed: ${e.message}`, 'error'); }
}

// --- Modal Handling ---
async function openModal(modalId, editId = null) {
    const modal = document.getElementById(modalId); if (!modal) { console.error(`Modal ${modalId} not found.`); return; }
    if (modalId === 'agent-modal') {
        const form = modal.querySelector('#agent-form'); const titleEl = modal.querySelector('#modal-title'); const agentIdInput = form?.querySelector('#agent-id'); const editAgentIdInput = form?.querySelector('#edit-agent-id');
        if (!form || !titleEl || !agentIdInput || !editAgentIdInput) { console.error("Agent modal elements missing."); return; }
        form.reset(); editAgentIdInput.value = ''; agentIdInput.disabled = false;
        if (editId) {
            titleEl.textContent = `Edit Agent: ${editId}`; eI.value = editId; iI.value = editId; iI.disabled = true;
            try {
                 console.log(`Fetching list for edit: ${editId}`); const r = await fetch('/api/config/agents'); if (!r.ok) throw new Error('Fetch list failed.'); const a = await r.json(); const d = a.find(x => x.agent_id === editId); if (!d) throw new Error(`Agent ${editId} not found.`);
                 form.querySelector('#persona').value = d.persona || editId; form.querySelector('#provider').value = d.provider || 'openrouter'; form.querySelector('#model').value = d.model || ''; console.warn("Edit modal prefilled limited data.");
            } catch (e) { console.error("Edit fetch err:", e); alert(`Load agent error: ${e.message}`); return; }
        } else { titleEl.textContent = 'Add New Static Agent'; form.querySelector('#temperature').value = 0.7; form.querySelector('#provider').value = 'openrouter'; }
    }
    modal.style.display = 'block';
}

function closeModal(modalId) { const m = document.getElementById(modalId); if (m) m.style.display = 'none'; const f = m?.querySelector('form'); if(f) f.reset(); if(modalId === 'agent-modal') { const eI = document.getElementById('edit-agent-id'); const iI = document.getElementById('agent-id'); if(eI) eI.value = ''; if(iI) iI.disabled = false; } }
window.onclick = function(event) { if (event.target.classList.contains('modal')) closeModal(event.target.id); }

function showOverrideModal(data) {
    if (!overrideModal) return; const aId = data.agent_id; const p = data.persona || aId;
    document.getElementById('override-agent-id').value = aId; document.getElementById('override-modal-title').textContent = `Override for: ${p}`;
    document.getElementById('override-message').textContent = data.message || `Agent '${p}' (${aId}) failed. Provide alternative.`; document.getElementById('override-last-error').textContent = data.last_error || "Unknown error";
    const s = document.getElementById('override-provider'); const i = document.getElementById('override-model');
    if (s && data.current_provider) s.value = data.current_provider; if (i) i.value = data.current_model || '';
    openModal('override-modal');
}

function handleSubmitOverride(event) {
    event.preventDefault(); const aId = document.getElementById('override-agent-id')?.value; const nP = document.getElementById('override-provider')?.value; const nM = document.getElementById('override-model')?.value.trim();
    if (!aId || !nP || !nM) { alert("Fill all override fields."); return; } const oD = { type: "submit_user_override", agent_id: aId, new_provider: nP, new_model: nM };
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(oD)); addMessage('system-log-area', `[UI] Submitted override for ${aId} (Prov: ${nP}, Model: ${nM}).`, 'status'); closeModal('override-modal'); }
    else { alert("WS not connected."); addMessage('system-log-area', `[UI Err] Override failed for ${aId}: WS not connected.`, 'error'); }
}
