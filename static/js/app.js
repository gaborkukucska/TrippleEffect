// START OF FILE static/js/app.js

// --- Global Variables ---
let ws; // WebSocket connection instance
let selectedFileContent = null; // Holds base64 content of the attached file
let selectedFileInfo = null; // Holds file metadata { name, size, type }
const MAX_FILE_SIZE_MB = 5; // Max file size allowed for upload
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

// --- DOM Element References ---
// Cache frequently used DOM elements
const chatView = document.getElementById('chat-view');
const logsView = document.getElementById('logs-view');
const configView = document.getElementById('config-view');
const sessionView = document.getElementById('session-view');
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const attachFileButton = document.getElementById('attach-file-button');
const fileInput = document.getElementById('file-input');
const fileInfoArea = document.getElementById('file-info-area');
const agentStatusContent = document.getElementById('agent-status-content');
const bottomNavButtons = document.querySelectorAll('.nav-button');
// Session View Elements
const projectSelect = document.getElementById('project-select');
const sessionSelect = document.getElementById('session-select');
const loadSessionButton = document.getElementById('load-session-button');
const saveProjectNameInput = document.getElementById('save-project-name');
const saveSessionNameInput = document.getElementById('save-session-name');
const saveSessionButton = document.getElementById('save-session-button');
const sessionStatusMessage = document.getElementById('session-status-message');
// --- REMOVED Config View / Modal Elements ---
// const configContent = document.getElementById('config-content');
// const addAgentButton = document.getElementById('add-agent-button');
// const refreshConfigButton = document.getElementById('refresh-config-button');
// const agentModal = document.getElementById('agent-modal');
// const overrideModal = document.getElementById('override-modal');
// const agentForm = document.getElementById('agent-form');
// const overrideForm = document.getElementById('override-form');

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed");
    try {
        setupWebSocket();
        setupEventListeners();
        // --- REMOVED load/display static config ---
        // displayAgentConfigurations();
        loadProjects(); // Load initial project list for session view
        showView('chat-view'); // Show chat view by default
        addMessage('system-log-area', 'UI Initialized. Connecting to backend...', 'status');
    } catch (error) {
        console.error("Initialization error:", error);
        addMessage('system-log-area', `Initialization Error: ${error.message}`, 'error');
        updateLogStatus("Initialization Error", true);
    }
});

// --- WebSocket Management ---
function setupWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting to connect WebSocket: ${wsUrl}`);
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("WebSocket connection established");
        updateLogStatus("Connected", false);
        addMessage('system-log-area', 'WebSocket Connected.', 'status');
        // Maybe request initial agent status after connection?
        // ws.send(JSON.stringify({ type: 'get_status' }));
        // Clear any stale 'Connecting...' message
        const connectingMsg = systemLogArea.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            // console.log("WebSocket message received:", data); // Debug: Log raw data
            handleWebSocketMessage(data);
        } catch (error) {
            console.error("Error parsing WebSocket message:", error);
            addMessage('system-log-area', `Error processing message: ${error.message}`, 'error');
            // Optionally display raw message if parsing fails
            // addMessage('system-log-area', `Raw Data: ${event.data}`, 'log-raw');
        }
    };

    ws.onerror = (error) => {
        console.error("WebSocket error:", error);
        updateLogStatus("Connection Error", true);
        addMessage('system-log-area', `WebSocket Error: ${error.message || 'Unknown error'}`, 'error');
    };

    ws.onclose = (event) => {
        console.log("WebSocket connection closed:", event);
        const reason = event.reason || `Code ${event.code}`;
        updateLogStatus(`Disconnected (${reason})`, true);
        addMessage('system-log-area', `WebSocket Disconnected (${reason}). Attempting to reconnect...`, 'error');
        // Simple reconnection attempt after a delay
        setTimeout(setupWebSocket, 5000); // Reconnect after 5 seconds
    };
}

function handleWebSocketMessage(data) {
    addRawLogEntry(data); // Log raw data for debugging

    switch (data.type) {
        case 'status': // General status or connection messages
            addMessage('system-log-area', data.content || data.message, 'status', data.agent_id);
            break;
        case 'error': // Backend errors or specific agent errors
            addMessage(data.agent_id ? 'conversation-area' : 'system-log-area', data.content || 'Unknown error', 'error', data.agent_id);
            // Update agent status in UI if agent-specific error
            if (data.agent_id && agentStatusContent) {
                 const agentEntry = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${data.agent_id}"]`);
                 if(agentEntry) {
                     const statusBadge = agentEntry.querySelector('.agent-status');
                     if (statusBadge) {
                         statusBadge.textContent = 'ERROR';
                         statusBadge.parentElement.className = 'agent-status-item status-error'; // Update class for styling
                     }
                 } else {
                     // If agent entry doesn't exist yet, add it with error status
                     updateAgentStatusUI(data.agent_id, { status: 'error', persona: 'Unknown Agent' });
                 }
            }
            break;
        case 'response_chunk':
            appendAgentResponseChunk(data.agent_id, data.content);
            break;
        case 'final_response':
            finalizeAgentResponse(data.agent_id, data.content);
            break;
        case 'agent_status_update':
            updateAgentStatusUI(data.agent_id, data.status);
            break;
        case 'agent_added':
             // Add agent to status list immediately
             updateAgentStatusUI(data.agent_id, { status: 'idle', ...data.config, team: data.team }); // Add team info if available
             addMessage('system-log-area', `Agent added: ${data.config?.persona || data.agent_id}`, 'status');
             break;
        case 'agent_deleted':
             removeAgentStatusEntry(data.agent_id);
             addMessage('system-log-area', `Agent deleted: ${data.agent_id}`, 'status');
             break;
        case 'agent_moved_team':
             // Update team display in the status list
             updateAgentStatusUI(data.agent_id, { team: data.new_team_id });
             addMessage('system-log-area', `Agent ${data.agent_id} moved to team ${data.new_team_id || 'None'}`, 'status');
             break;
        case 'team_created':
             addMessage('system-log-area', `Team created: ${data.team_id}`, 'status');
             break;
         case 'team_deleted':
             addMessage('system-log-area', `Team deleted: ${data.team_id}`, 'status');
             break;
        case 'system_event': // For events like save/load session
             addMessage('system-log-area', `System Event: ${data.event} - ${data.message || ''}`, 'status');
             // Optionally display session status in session view too
             if (sessionView.classList.contains('active') && data.event?.includes('session')) {
                  displaySessionStatus(data.message || data.event, false);
             }
             break;
        // --- REMOVED request_user_override case ---
        default:
            console.warn("Received unhandled WebSocket message type:", data.type, data);
            addMessage('system-log-area', `Unhandled message type: ${data.type}`, 'status');
    }
}

// --- UI Update Functions ---
function addMessage(areaId, text, type = 'log', agentId = null) {
    const area = document.getElementById(areaId);
    if (!area) {
        console.error(`Message area "${areaId}" not found.`);
        return;
    }

    // Prevent excessive logging if needed
    // if (area.children.length > 200) { area.removeChild(area.firstChild); }

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', type); // Add base 'message' class and specific type
    if (agentId) {
        messageElement.dataset.agentId = agentId; // Add data attribute for agent-specific styling
    }

    let contentHTML = '';
    const now = new Date();
    const timestamp = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Add timestamp only to system log messages
    if (areaId === 'system-log-area') {
        contentHTML += `<span class="timestamp">[${timestamp}]</span> `;
    }

    // Add agent label for agent responses in conversation area
    if (areaId === 'conversation-area' && type === 'agent_response' && agentId) {
         const agentState = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"] strong`)?.textContent || agentId;
         contentHTML += `<span class="agent-label">${agentState}:</span>`;
    }

    const messageSpan = document.createElement('span');
    messageSpan.textContent = text; // Use textContent to prevent XSS
    // Add specific class for content if needed for styling, e.g., for monospace
    if (type === 'agent_response') {
         messageSpan.classList.add('message-content');
    }

    messageElement.innerHTML += contentHTML; // Add timestamp/label first
    messageElement.appendChild(messageSpan); // Append the text content span

    area.appendChild(messageElement);

    // Auto-scroll to the bottom
    // Use requestAnimationFrame for smoother scrolling after DOM update
    requestAnimationFrame(() => {
         area.scrollTop = area.scrollHeight;
    });
}

function appendAgentResponseChunk(agentId, chunk) {
    const area = conversationArea; // Always append chunks to conversation area
    if (!area) return;

    // Find the last message from this agent, or create a new one
    let agentMessage = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);
    let contentSpan;

    if (!agentMessage || agentMessage.dataset.final === 'true') {
        // Create a new message container if none exists or last was finalized
        agentMessage = document.createElement('div');
        agentMessage.classList.add('message', 'agent_response');
        agentMessage.dataset.agentId = agentId;
        agentMessage.dataset.final = 'false'; // Mark as streaming

        const agentState = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"] strong`)?.textContent || agentId;
        const labelSpan = document.createElement('span');
        labelSpan.classList.add('agent-label');
        labelSpan.textContent = `${agentState}:`;
        agentMessage.appendChild(labelSpan);

        contentSpan = document.createElement('span');
        contentSpan.classList.add('message-content');
        agentMessage.appendChild(contentSpan);

        area.appendChild(agentMessage);
    } else {
        // Find the existing content span in the last message
        contentSpan = agentMessage.querySelector('.message-content');
        if (!contentSpan) { // Should not happen, but safety check
            contentSpan = document.createElement('span');
            contentSpan.classList.add('message-content');
            agentMessage.appendChild(contentSpan);
        }
    }

    // Append the chunk (using textContent for safety)
    contentSpan.textContent += chunk;

    // Scroll to bottom
     requestAnimationFrame(() => {
         area.scrollTop = area.scrollHeight;
    });
}

function finalizeAgentResponse(agentId, finalContent) {
    const area = conversationArea;
    if (!area) return;
    let agentMessage = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);

    if (agentMessage && agentMessage.dataset.final === 'false') {
        agentMessage.dataset.final = 'true'; // Mark as final
        // Optional: Update content if finalContent differs significantly (e.g., if only tool call was last chunk)
        const contentSpan = agentMessage.querySelector('.message-content');
        if (contentSpan && finalContent && contentSpan.textContent !== finalContent) {
            // This case might happen if the last chunk was just a tool call end tag
            // and the full response is needed for history. Let's ensure it's set.
            // console.log("Finalizing with different content:", finalContent);
             contentSpan.textContent = finalContent;
        }
    } else if (!agentMessage && finalContent) {
        // If no streaming chunks were received, add the final message directly
        addMessage('conversation-area', finalContent, 'agent_response', agentId);
        // Find the newly added message and mark it final
        const newMessage = area.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);
        if(newMessage) newMessage.dataset.final = 'true';
    }
     // Scroll to bottom
     requestAnimationFrame(() => {
         area.scrollTop = area.scrollHeight;
    });
}

function updateLogStatus(message, isError = false) {
    const statusElement = systemLogArea.querySelector('.initial-connecting');
    if (statusElement) {
        statusElement.textContent = message;
        statusElement.classList.toggle('error', isError);
        statusElement.classList.remove('initial-connecting', 'initial-placeholder');
    } else {
        // Add a new status message if the initial one isn't found
        addMessage('system-log-area', message, isError ? 'error' : 'status');
    }
}

function updateAgentStatusUI(agentId, statusData) {
    // statusData might be the full state dict OR just { team: newTeamId } for moves
    if (!agentStatusContent) return;
    addOrUpdateAgentStatusEntry(agentId, statusData);
}

function addOrUpdateAgentStatusEntry(agentId, statusData) {
    let entry = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);

    // Clear placeholder if it exists and we're adding the first real entry
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();

    if (!entry) {
        entry = document.createElement('div');
        entry.classList.add('agent-status-item');
        entry.dataset.agentId = agentId;
        agentStatusContent.appendChild(entry);
    }

    // Determine persona, model, team, status from statusData
    const persona = statusData?.persona || agentId; // Use ID if persona missing
    const model = statusData?.model ? `(${statusData.model})` : ''; // Add parentheses if model exists
    const team = statusData?.team ? `[Team: ${statusData.team}]` : ''; // Add team info
    const status = (statusData?.status || 'unknown').replace('_', ' '); // Replace underscore

    entry.innerHTML = `
        <span>
            <strong>${persona}</strong>
            <span class="agent-model">${model}</span>
            <span class="agent-team">${team}</span>
        </span>
        <span class="agent-status">${status.toUpperCase()}</span>
    `;

    // Update class for styling based on status
    entry.className = `agent-status-item status-${(statusData?.status || 'unknown')}`;
}

function removeAgentStatusEntry(agentId) {
    const entry = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);
    if (entry) {
        entry.remove();
    }
    // Add placeholder if list becomes empty
    if (agentStatusContent.children.length === 0) {
         const placeholder = document.createElement('span');
         placeholder.classList.add('status-placeholder');
         placeholder.textContent = 'No active agents.';
         agentStatusContent.appendChild(placeholder);
    }
}

function addRawLogEntry(data) {
    // Optional: Log raw data to browser console for advanced debugging
    // console.log("Raw WS Data:", data);
}

// --- Event Listeners ---
function setupEventListeners() {
    // Send Button Click
    sendButton.addEventListener('click', handleSendMessage);

    // Message Input Enter Key
    messageInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault(); // Prevent newline
            handleSendMessage();
        }
    });

    // Attach File Button
    attachFileButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', handleFileSelect);

    // --- REMOVED Config Button Listeners ---
    // addAgentButton.addEventListener('click', () => openModal('agent-modal'));
    // refreshConfigButton.addEventListener('click', displayAgentConfigurations);

    // Bottom Navigation
    bottomNavButtons.forEach(button => {
        button.addEventListener('click', () => {
            const viewId = button.getAttribute('data-view');
            showView(viewId);
        });
    });

    // Session Management Listeners
    projectSelect.addEventListener('change', () => {
        const selectedProject = projectSelect.value;
        if (selectedProject) {
            loadSessions(selectedProject);
            sessionSelect.disabled = false;
        } else {
            sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
            sessionSelect.disabled = true;
            loadSessionButton.disabled = true;
        }
    });

    sessionSelect.addEventListener('change', () => {
        loadSessionButton.disabled = !sessionSelect.value; // Enable load button only if a session is selected
    });

    loadSessionButton.addEventListener('click', handleLoadSession);
    saveSessionButton.addEventListener('click', handleSaveSession);

    // --- REMOVED Modal Form Listeners ---
    // agentForm.addEventListener('submit', handleSaveAgent);
    // overrideForm.addEventListener('submit', handleSubmitOverride);

    // --- REMOVED Global Modal Close Listener ---
    // window.addEventListener('click', (event) => {
    //     if (event.target === agentModal) closeModal('agent-modal');
    //     if (event.target === overrideModal) closeModal('override-modal');
    // });
}

// --- View Switching ---
function showView(viewId) {
    // Hide all panels
    document.querySelectorAll('.view-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    // Show the target panel
    const targetPanel = document.getElementById(viewId);
    if (targetPanel) {
        targetPanel.classList.add('active');
    } else {
        console.error(`View panel with ID "${viewId}" not found.`);
        // Show chat view as fallback
        document.getElementById('chat-view').classList.add('active');
    }

    // Update active button state
    bottomNavButtons.forEach(button => {
        button.classList.toggle('active', button.getAttribute('data-view') === viewId);
    });

    // Refresh project list when switching TO session view
    if (viewId === 'session-view') {
        loadProjects();
    }
}

// --- Message Sending ---
function handleSendMessage() {
    const messageText = messageInput.value.trim();

    if (!messageText && !selectedFileContent) {
        console.log("Empty message and no file selected.");
        return; // Don't send empty messages unless a file is attached
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('conversation-area', 'Error: Not connected to backend.', 'error');
        console.error("WebSocket is not open. ReadyState:", ws ? ws.readyState : 'N/A');
        return;
    }

    let messagePayload;

    if (selectedFileContent && selectedFileInfo) {
         // Send structured message with file data
         messagePayload = JSON.stringify({
             type: "user_message_with_file",
             text: messageText, // Can be empty if only sending file
             filename: selectedFileInfo.name,
             file_content: selectedFileContent // Base64 content
         });
         // Display combined info in user's chat
         addMessage('conversation-area', `You (sent file: ${selectedFileInfo.name}):\n${messageText}`, 'user');
         clearFileInput(); // Clear file input after preparing message
    } else {
        // Send plain text message
        messagePayload = messageText; // Send raw text if no file
        addMessage('conversation-area', `You: ${messageText}`, 'user');
    }


    console.log("Sending message:", messagePayload);
    ws.send(messagePayload);

    // Clear input only if text was sent (allow sending file without text)
    if(messageText){
        messageInput.value = '';
        messageInput.style.height = 'auto'; // Reset height
    }
}

// --- File Handling ---
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) {
        clearFileInput();
        return;
    }

    // Basic validation (type and size)
     if (!file.type.match('text.*') && !file.type.match(/application\/(json|yaml|x-yaml)/) && !file.name.match(/\.(py|js|html|css|md|csv|log)$/)) {
        alert('Error: Only text files, code files, markdown, json, yaml, csv, or log files are allowed.');
        clearFileInput();
        return;
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
        alert(`Error: File size exceeds ${MAX_FILE_SIZE_MB}MB limit.`);
        clearFileInput();
        return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
        selectedFileContent = e.target.result.split(',')[1]; // Get base64 part
        selectedFileInfo = { name: file.name, size: file.size, type: file.type };
        displayFileInfo();
        console.log(`File attached: ${file.name}, Size: ${file.size}`);
    };
    reader.onerror = (e) => {
        console.error("FileReader error:", e);
        alert('Error reading file.');
        clearFileInput();
    };
    reader.readAsDataURL(file); // Read as Base64 Data URL
}

function displayFileInfo() {
    if (selectedFileInfo) {
        fileInfoArea.innerHTML = `
            <span>📎 ${selectedFileInfo.name} (${(selectedFileInfo.size / 1024).toFixed(1)} KB)</span>
            <button onclick="clearFileInput()" title="Remove File">✖</button>
        `;
        fileInfoArea.style.display = 'flex';
    } else {
        fileInfoArea.innerHTML = '';
        fileInfoArea.style.display = 'none';
    }
}

function clearFileInput() {
    fileInput.value = null; // Clear the file input element
    selectedFileContent = null;
    selectedFileInfo = null;
    displayFileInfo();
    console.log("File attachment cleared.");
}


// --- REMOVED Static Agent Config Functions ---
// function displayAgentConfigurations() { ... }
// function handleSaveAgent(event) { ... }
// function handleDeleteAgent(agentId) { ... }

// --- REMOVED Modal Functions ---
// function openModal(modalId, editId = null) { ... }
// function closeModal(modalId) { ... }
// function showOverrideModal(data) { ... }
// function handleSubmitOverride(event) { ... }

// --- Session Management Functions ---
async function loadProjects() {
    try {
        const response = await fetch('/api/projects');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const projects = await response.json();

        projectSelect.innerHTML = '<option value="">-- Select Project --</option>'; // Reset
        sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
        sessionSelect.disabled = true;
        loadSessionButton.disabled = true;

        if (projects && projects.length > 0) {
            projects.forEach(proj => {
                const option = document.createElement('option');
                option.value = proj.project_name;
                option.textContent = proj.project_name;
                projectSelect.appendChild(option);
            });
        } else {
             projectSelect.innerHTML = '<option value="">-- No Projects Found --</option>';
        }
    } catch (error) {
        console.error('Error loading projects:', error);
        displaySessionStatus(`Error loading projects: ${error.message}`, true);
        projectSelect.innerHTML = '<option value="">-- Error Loading --</option>';
    }
}

async function loadSessions(projectName) {
    if (!projectName) return;
    try {
        const response = await fetch(`/api/projects/${projectName}/sessions`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const sessions = await response.json();

        sessionSelect.innerHTML = '<option value="">-- Select Session --</option>'; // Reset
        loadSessionButton.disabled = true; // Disable button initially

        if (sessions && sessions.length > 0) {
            sessions.forEach(sess => {
                const option = document.createElement('option');
                option.value = sess.session_name;
                option.textContent = sess.session_name; // Display timestamp or name
                sessionSelect.appendChild(option);
            });
            sessionSelect.disabled = false; // Enable dropdown
        } else {
            sessionSelect.innerHTML = '<option value="">-- No Sessions Found --</option>';
            sessionSelect.disabled = true; // Keep disabled
        }
    } catch (error) {
        console.error(`Error loading sessions for ${projectName}:`, error);
        displaySessionStatus(`Error loading sessions: ${error.message}`, true);
        sessionSelect.innerHTML = '<option value="">-- Error Loading --</option>';
        sessionSelect.disabled = true;
    }
}

async function handleLoadSession() {
    const projectName = projectSelect?.value; // Optional chaining for safety
    const sessionName = sessionSelect?.value;

    if (!projectName || !sessionName) {
        displaySessionStatus("Error: Please select both a project and a session.", true);
        return;
    }

    console.log(`Requesting load session: ${projectName}/${sessionName}`);
    displaySessionStatus(`Loading session ${projectName}/${sessionName}...`, false);
    loadSessionButton.disabled = true; // Disable during load

    try {
        const response = await fetch(`/api/projects/${projectName}/sessions/${sessionName}/load`, { method: 'POST' });
        const result = await response.json();

        if (response.ok && result.success) {
            displaySessionStatus(`Session '${sessionName}' loaded successfully!`, false);
            // Maybe switch back to chat view?
            // showView('chat-view');
            // Clear conversation area?
             // conversationArea.innerHTML = '<div class="message status"><span>Session Loaded.</span></div>';
        } else {
            throw new Error(result.message || `HTTP error ${response.status}`);
        }
    } catch (error) {
        console.error('Error loading session:', error);
        displaySessionStatus(`Error loading session: ${error.message}`, true);
    } finally {
        // Re-enable button only if a session is still selected
        loadSessionButton.disabled = !sessionSelect?.value;
    }
}

async function handleSaveSession() {
    const projectName = saveProjectNameInput?.value?.trim();
    const sessionName = saveSessionNameInput?.value?.trim() || null; // Send null if empty

    if (!projectName) {
        displaySessionStatus("Error: Project name is required to save.", true);
        return;
    }

    console.log(`Requesting save session: Project='${projectName}', Session='${sessionName || '(auto)'}'`);
    displaySessionStatus(`Saving session to project '${projectName}'...`, false);
    saveSessionButton.disabled = true; // Disable during save

    try {
        const response = await fetch(`/api/projects/${projectName}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_name: sessionName }) // Send optional name
        });
        const result = await response.json();

        if (response.ok && result.success) {
            displaySessionStatus(result.message || 'Session saved successfully!', false);
            // Refresh project/session list in case new project/session was created
            loadProjects();
            // Clear save inputs
            saveProjectNameInput.value = '';
            saveSessionNameInput.value = '';
        } else {
            throw new Error(result.message || `HTTP error ${response.status}`);
        }
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
    sessionStatusMessage.className = isError ? 'session-status error' : 'session-status success'; // Use specific classes
    sessionStatusMessage.style.display = 'block';

    // Optional: Auto-hide message after a delay
    // setTimeout(() => {
    //    sessionStatusMessage.style.display = 'none';
    // }, 5000);
}
