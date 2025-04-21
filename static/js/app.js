// START OF FILE static/js/app.js

// --- Configuration ---
const WS_URL = `ws://${window.location.host}/ws`;
const API_BASE_URL = ''; // Relative path for API calls
const INITIAL_RECONNECT_DELAY = 1000; // 1 second
const MAX_RECONNECT_DELAY = 30000; // 30 seconds
const MAX_LOG_MESSAGES = 200; // Max messages to keep in internal comms view
const MAX_CHAT_MESSAGES = 100; // Max messages to keep in main chat view

// --- State ---
let websocket = null;
let reconnectDelay = INITIAL_RECONNECT_DELAY;
let isConnected = false;
let currentView = 'chat-view'; // Default view
let attachedFile = null; // { name: string, content: string }

// --- DOM Elements ---
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const conversationArea = document.getElementById('conversation-area');
const internalCommsArea = document.getElementById('internal-comms-area'); // New area
const agentStatusContent = document.getElementById('agent-status-content');
const viewPanels = document.querySelectorAll('.view-panel');
const navButtons = document.querySelectorAll('.nav-button');
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');

// Session View Elements
const projectSelect = document.getElementById('project-select');
const sessionSelect = document.getElementById('session-select');
const loadSessionButton = document.getElementById('load-session-button');
const saveProjectNameInput = document.getElementById('save-project-name');
const saveSessionNameInput = document.getElementById('save-session-name');
const saveSessionButton = document.getElementById('save-session-button');
const sessionStatusMessage = document.getElementById('session-status-message');

// Config View Elements
const configContent = document.getElementById('config-content');
const refreshConfigButton = document.getElementById('refresh-config-button');
const addAgentButton = document.getElementById('add-agent-button');

// Agent Modal Elements
const agentModal = document.getElementById('agent-modal');
const agentForm = document.getElementById('agent-form');
const modalTitle = document.getElementById('modal-title');
const editAgentIdInput = document.getElementById('edit-agent-id'); // Hidden field for edits


// --- Utility Functions ---
const escapeHTML = (str) => {
    if (str === null || str === undefined) return '';
    return String(str).replace(/[&<>"']/g, (match) => {
        switch (match) {
            case '&': return '&';
            case '<': return '<';
            case '>': return '>';
            case '"': return '"';
            case "'": return ''';
            default: return match;
        }
    });
};

const getCurrentTimestamp = () => {
    const now = new Date();
    // Format with leading zeros
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    return `${hours}:${minutes}:${seconds}`;
};

// --- WebSocket Management ---
const connectWebSocket = () => {
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        console.log("WebSocket already open.");
        return;
    }

    console.log(`Attempting to connect WebSocket to ${WS_URL}...`);
    displayStatusMessage("Connecting...", true); // Show initial connecting in internal comms

    websocket = new WebSocket(WS_URL);

    websocket.onopen = (event) => {
        console.log("WebSocket connection established.");
        isConnected = true;
        reconnectDelay = INITIAL_RECONNECT_DELAY; // Reset delay on successful connection
        displayStatusMessage("Connected to backend.", true);
        // Optionally request initial status or data here
    };

    websocket.onmessage = (event) => {
        try {
            const messageData = JSON.parse(event.data);
            // console.log("Message received:", messageData); // Can be noisy
            handleWebSocketMessage(messageData);
        } catch (error) {
            console.error("Error parsing WebSocket message:", error);
            displayMessage(`Error parsing message: ${event.data}`, 'error', 'internal-comms-area');
        }
    };

    websocket.onerror = (event) => {
        console.error("WebSocket error:", event);
        displayStatusMessage(`WebSocket error: ${event.type}`, true, true);
    };

    websocket.onclose = (event) => {
        console.log(`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`);
        isConnected = false;
        displayStatusMessage(`Connection closed (${event.code}). Reconnecting...`, true, true);
        // Schedule reconnection attempt
        setTimeout(connectWebSocket, reconnectDelay);
        // Exponential backoff for reconnection delay
        reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
    };
};

const sendMessage = (message) => {
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        websocket.send(message);
        console.debug(`Sent message: ${message.substring(0, 100)}...`);
    } else {
        console.error("WebSocket is not connected. Cannot send message.");
        displayMessage("Error: Not connected to backend. Message not sent.", "error", "conversation-area");
    }
};

// --- UI Update Functions ---

/**
 * Displays a message in the specified message area (either conversation or internal comms).
 * Also handles auto-scrolling and message limits.
 * @param {string} text The message content (HTML allowed).
 * @param {string} type The message type (e.g., 'user', 'agent_response', 'status', 'error', 'log-tool-use').
 * @param {string} targetAreaId The ID of the container element ('conversation-area' or 'internal-comms-area').
 * @param {string} [agentId=null] Optional agent ID for styling.
 * @param {string} [agentPersona=null] Optional agent persona for display.
 */
const displayMessage = (text, type, targetAreaId, agentId = null, agentPersona = null) => {
    const messageArea = document.getElementById(targetAreaId);
    if (!messageArea) {
        console.error(`Target message area #${targetAreaId} not found.`);
        return;
    }

    // Remove placeholder if it exists
    const placeholder = messageArea.querySelector('.initial-placeholder');
    if (placeholder) {
        placeholder.remove();
    }

     // Remove oldest messages if limit exceeded
     const maxMessages = targetAreaId === 'conversation-area' ? MAX_CHAT_MESSAGES : MAX_LOG_MESSAGES;
     while (messageArea.children.length >= maxMessages) {
        messageArea.removeChild(messageArea.firstChild);
     }

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', type);
    if (agentId) {
        messageElement.setAttribute('data-agent-id', agentId);
        // Add specific class for agent responses for potential styling
        if (type === 'agent_response') {
            messageElement.classList.add('agent_response');
        }
    }
    // Use timestamp from internal comms area styling
    const timestampSpan = `<span class="timestamp">${getCurrentTimestamp()}</span>`;

    // Structure message content based on type and target area
    let innerHTMLContent = '';
    if (targetAreaId === 'internal-comms-area') {
        innerHTMLContent += timestampSpan; // Add timestamp first for internal comms
        if (agentPersona) {
            innerHTMLContent += `<span class="agent-label">${escapeHTML(agentPersona)} (${escapeHTML(agentId)}):</span>`;
        } else if (agentId && agentId !== 'manager' && agentId !== 'system') {
             innerHTMLContent += `<span class="agent-label">Agent (${escapeHTML(agentId)}):</span>`;
        }
    } else if (targetAreaId === 'conversation-area') {
        if (type === 'agent_response' && agentPersona) {
            innerHTMLContent += `<span class="agent-label">${escapeHTML(agentPersona)}:</span>`;
        }
        // User messages don't get a label here, they are styled differently
    }

    // Add the main message content
    innerHTMLContent += `<span class="message-content">${text}</span>`; // Use span for consistency, text already escaped or is HTML

    messageElement.innerHTML = innerHTMLContent;
    messageArea.appendChild(messageElement);

    // Auto-scroll to the bottom
    messageArea.scrollTop = messageArea.scrollHeight;
};


/**
 * Displays a status message specifically in the Internal Communications view.
 * @param {string} message The status text.
 * @param {boolean} [temporary=false] If true, the message might be removed later (not implemented yet).
 * @param {boolean} [isError=false] If true, style as an error.
 */
const displayStatusMessage = (message, temporary = false, isError = false) => {
    const messageType = isError ? 'error' : 'status';
    // Always display status messages in the internal comms area now
    displayMessage(escapeHTML(message), messageType, 'internal-comms-area', 'system');
};


/**
 * Updates the agent status list UI in the Chat View.
 * @param {object} agentStatusData Agent status keyed by agent ID.
 */
const updateAgentStatusUI = (agentStatusData) => {
    if (!agentStatusContent) return;
    agentStatusContent.innerHTML = ''; // Clear existing statuses

    const agentIds = Object.keys(agentStatusData);

    if (agentIds.length === 0) {
        agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>';
        return;
    }

    // Sort agents: admin_ai first, then alphabetically
    agentIds.sort((a, b) => {
        if (a === 'admin_ai') return -1;
        if (b === 'admin_ai') return 1;
        return a.localeCompare(b);
    });


    agentIds.forEach(agentId => {
        const agent = agentStatusData[agentId];
        if (!agent || agent.status === 'deleted') return; // Skip deleted agents

        const statusItem = document.createElement('div');
        statusItem.classList.add('agent-status-item', `status-${agent.status || 'unknown'}`);
        statusItem.setAttribute('data-agent-id', agentId);

        const persona = agent.persona || agentId;
        const modelInfo = (agent.provider && agent.model) ? `(${agent.model})` : '(Model N/A)';
        const teamInfo = agent.team ? `<span class="agent-team">[${escapeHTML(agent.team)}]</span>` : '';

        // Agent info part
        const agentInfoSpan = document.createElement('span');
        agentInfoSpan.innerHTML = `<strong>${escapeHTML(persona)}</strong> <span class="agent-model">${escapeHTML(modelInfo)}</span> ${teamInfo}`;

        // Status badge part
        const statusBadgeSpan = document.createElement('span');
        statusBadgeSpan.classList.add('agent-status');
        statusBadgeSpan.textContent = agent.status || 'unknown';

        statusItem.appendChild(agentInfoSpan);
        statusItem.appendChild(statusBadgeSpan);

        agentStatusContent.appendChild(statusItem);
    });
};

/**
 * Handles incoming WebSocket messages and routes them to appropriate UI handlers.
 * @param {object} data The parsed message data from the WebSocket.
 */
const handleWebSocketMessage = (data) => {
    const messageType = data.type;
    const agentId = data.agent_id; // Agent originating the message/status

    // Remove initial connecting message if it exists
    const connectingMsg = internalCommsArea.querySelector('.initial-connecting');
    if (connectingMsg) connectingMsg.remove();

    switch (messageType) {
        case 'agent_response':
            // Route based on agent ID
            if (agentId === 'admin_ai') {
                displayMessage(data.content, messageType, 'conversation-area', agentId, data.persona || agentId);
            } else {
                // Optional: Display internal agent responses in the internal comms view
                 displayMessage(data.content, messageType, 'internal-comms-area', agentId, data.persona || agentId);
            }
            break;

        case 'status':
        case 'system_event':
            // Display general status/events in internal comms
             displayMessage(escapeHTML(data.content || data.message || 'Unknown status'), messageType, 'internal-comms-area', agentId || 'system');
            break;

        case 'error':
            // Display errors in internal comms
            displayMessage(escapeHTML(data.content || 'Unknown error'), messageType, 'internal-comms-area', agentId || 'system');
            break;

        case 'agent_status_update':
            // Update the status list in the Chat view
            if (data.status && typeof data.status === 'object') {
                // Ensure agent ID is included if missing (shouldn't happen ideally)
                if (!data.status.agent_id && agentId) data.status.agent_id = agentId;
                // Assuming the backend sends the full status object needed by updateAgentStatusUI
                updateAgentStatusUI({ [agentId]: data.status });
            } else {
                 console.warn("Received agent_status_update without valid status object:", data);
            }
            break;

        case 'agent_added':
        case 'agent_deleted':
            // Log the event in internal comms
            const eventMsg = messageType === 'agent_added'
                ? `Agent Added: ${data.agent_id} (${data.config?.persona || 'N/A'}) - Model: ${data.config?.model || 'N/A'}`
                : `Agent Deleted: ${data.agent_id}`;
            displayMessage(escapeHTML(eventMsg), 'system_event', 'internal-comms-area', 'system');
            // Request a full status update to refresh the agent list UI correctly
            // (The backend should ideally send the full list on add/delete,
            // but requesting works as a fallback)
             // Example: sendMessage(JSON.stringify({ type: 'get_full_status' }));
             // For now, we assume the backend pushes the necessary individual update or full list.
            break;
         case 'team_created':
         case 'team_deleted':
            const teamEventMsg = messageType === 'team_created'
                 ? `Team Created: ${data.team_id}`
                 : `Team Deleted: ${data.team_id}`;
            displayMessage(escapeHTML(teamEventMsg), 'system_event', 'internal-comms-area', 'system');
            break;

        default:
            console.warn(`Received unknown message type: ${messageType}`, data);
            // Display unrecognized messages in internal comms as a fallback
            displayMessage(`Unknown message type '${messageType}': ${escapeHTML(JSON.stringify(data))}`, 'status', 'internal-comms-area', 'system');
    }
};


// --- View Switching ---
const switchView = (viewId) => {
    console.log(`Switching view to: ${viewId}`);
    viewPanels.forEach(panel => {
        panel.classList.remove('active');
        if (panel.id === viewId) {
            panel.classList.add('active');
        }
    });
    navButtons.forEach(button => {
        button.classList.remove('active');
        if (button.getAttribute('data-view') === viewId) {
            button.classList.add('active');
        }
    });
    currentView = viewId;

    // Load data relevant to the view when switching TO it
    if (viewId === 'config-view') {
        loadStaticAgentConfig();
    } else if (viewId === 'session-view') {
        loadProjects(); // Also loads sessions for the first project initially
    }
};

// --- API Call Functions ---
const makeApiCall = async (endpoint, method = 'GET', body = null) => {
    const url = `${API_BASE_URL}${endpoint}`;
    const options = {
        method,
        headers: {},
    };
    if (body) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(url, options);
        const responseData = await response.json(); // Assume JSON response
        if (!response.ok) {
            // Throw an error object compatible with how displayMessage handles errors
            const error = new Error(responseData.detail || `HTTP error ${response.status}`);
            error.status = response.status;
            error.responseBody = responseData;
            throw error;
        }
        return responseData;
    } catch (error) {
        console.error(`API call error (${method} ${endpoint}):`, error);
        // Display API errors in the internal comms area
        const errorDetail = error.responseBody?.detail || error.message || 'Unknown API error';
        displayMessage(`API Error (${method} ${endpoint}): ${escapeHTML(errorDetail)}`, 'error', 'internal-comms-area', 'api');
        throw error; // Re-throw to indicate failure to the caller
    }
};


// --- Event Listeners ---
const setupEventListeners = () => {
    // Send message on button click
    sendButton.addEventListener('click', () => {
        const message = messageInput.value.trim();
        if (message || attachedFile) {
            if (attachedFile) {
                // Send structured message with file content
                const messageData = {
                    type: 'user_message_with_file',
                    text: message,
                    filename: attachedFile.name,
                    file_content: attachedFile.content
                };
                 sendMessage(JSON.stringify(messageData));
                 displayMessage(escapeHTML(message) + `<br><small><i>[Attached: ${escapeHTML(attachedFile.name)}]</i></small>`, 'user', 'conversation-area');
                 clearAttachment();
            } else {
                // Send plain text message
                sendMessage(message);
                 displayMessage(escapeHTML(message), 'user', 'conversation-area');
            }
            messageInput.value = '';
            messageInput.style.height = 'auto'; // Reset height after sending
            messageInput.style.height = messageInput.scrollHeight + 'px'; // Adjust to content briefly
            messageInput.style.height = '60px'; // Reset to default fixed height
        }
    });

    // Send message on Enter key press (Shift+Enter for newline)
    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault(); // Prevent default newline behavior
            sendButton.click(); // Trigger send button click
        }
    });

    // Auto-resize textarea
    messageInput.addEventListener('input', () => {
        messageInput.style.height = 'auto'; // Reset height
        messageInput.style.height = messageInput.scrollHeight + 'px'; // Set to scroll height
    });

    // Navigation Button Clicks
    navButtons.forEach(button => {
        button.addEventListener('click', () => {
            const viewId = button.getAttribute('data-view');
            if (viewId) {
                switchView(viewId);
            }
        });
    });

     // File Attachment
     attachFileButton.addEventListener('click', () => fileInput.click());
     fileInput.addEventListener('change', handleFileSelect);

     // Config View Buttons
     refreshConfigButton?.addEventListener('click', loadStaticAgentConfig);
     addAgentButton?.addEventListener('click', () => openAgentModal(null)); // Open modal for adding

     // Agent Modal Form Submit
     agentForm?.addEventListener('submit', handleAgentFormSubmit);

     // Session Management Event Listeners
     projectSelect?.addEventListener('change', handleProjectSelectionChange);
     loadSessionButton?.addEventListener('click', handleLoadSession);
     saveSessionButton?.addEventListener('click', handleSaveSession);
};

// --- File Attachment Handling ---
const handleFileSelect = (event) => {
    const file = event.target.files[0];
    if (file) {
        // Basic validation (e.g., type, size)
        if (!file.type.startsWith('text/') && !/\.(py|js|json|yaml|md|log|csv|html|css)$/i.test(file.name)) {
             alert('Error: Only text-based files (.txt, .py, .js, .css, .html, .md, .json, .yaml, .csv, .log) are allowed.');
             fileInput.value = ''; // Reset input
             return;
         }
         const maxSize = 5 * 1024 * 1024; // 5MB limit
         if (file.size > maxSize) {
             alert(`Error: File size exceeds the limit of ${maxSize / 1024 / 1024}MB.`);
             fileInput.value = ''; // Reset input
             return;
         }

        const reader = new FileReader();
        reader.onload = (e) => {
            attachedFile = {
                name: file.name,
                content: e.target.result
            };
            displayFileInfo();
        };
        reader.onerror = (e) => {
            console.error("File reading error:", e);
            alert("Error reading file.");
            clearAttachment();
        };
        reader.readAsText(file); // Read as text
    }
     // Reset the input value so the 'change' event fires even if the same file is selected again
     event.target.value = null;
};

const displayFileInfo = () => {
    if (attachedFile) {
        fileInfoArea.innerHTML = `
            <span>Attached: ${escapeHTML(attachedFile.name)}</span>
            <button onclick="clearAttachment()" title="Remove file">×</button>
        `;
        fileInfoArea.style.display = 'flex';
    } else {
        fileInfoArea.style.display = 'none';
        fileInfoArea.innerHTML = '';
    }
};

const clearAttachment = () => {
    attachedFile = null;
    fileInput.value = ''; // Clear the file input
    displayFileInfo();
};


// --- Static Agent Config Functions ---
const loadStaticAgentConfig = async () => {
    if (!configContent) return;
    configContent.innerHTML = '<span class="status-placeholder">Loading config...</span>';
    try {
        const agentConfigs = await makeApiCall('/api/config/agents');
        renderStaticAgentConfig(agentConfigs);
    } catch (error) {
         configContent.innerHTML = '<span class="status-placeholder">Error loading config.</span>';
        // Error already displayed by makeApiCall
    }
};

const renderStaticAgentConfig = (agentConfigs) => {
    if (!configContent) return;
    configContent.innerHTML = ''; // Clear previous

    if (!agentConfigs || agentConfigs.length === 0) {
        configContent.innerHTML = '<span class="status-placeholder">No static agent configurations found.</span>';
        return;
    }

    agentConfigs.forEach(agent => {
        const item = document.createElement('div');
        item.classList.add('config-item');
        item.innerHTML = `
            <span>
                <strong>${escapeHTML(agent.agent_id)}</strong>
                <small class="agent-details">(${escapeHTML(agent.provider)} / ${escapeHTML(agent.model)}) - ${escapeHTML(agent.persona)}</small>
            </span>
            <span class="config-item-actions">
                <button class="config-action-button edit-button" data-agent-id="${escapeHTML(agent.agent_id)}" title="Edit Agent">✏️</button>
                <button class="config-action-button delete-button" data-agent-id="${escapeHTML(agent.agent_id)}" title="Delete Agent">🗑️</button>
            </span>
        `;
        configContent.appendChild(item);
    });

     // Add event listeners for edit/delete buttons after rendering
     configContent.querySelectorAll('.edit-button').forEach(button => {
         button.addEventListener('click', (e) => {
             const agentId = e.currentTarget.getAttribute('data-agent-id');
             // Find the full config data (requires fetching it - could be optimized)
             makeApiCall('/api/config/agents') // Re-fetch might be inefficient, ideally get full data initially
                 .then(allConfigs => {
                     const agentData = allConfigs.find(a => a.agent_id === agentId);
                     // Need to get the FULL config, not just AgentInfo. This API needs adjustment
                     // For now, we can only edit basic info or prompt for full details.
                     // Let's assume we can fetch full details somehow (requires backend change).
                     // Placeholder: open modal with limited data
                     console.warn("Edit requires fetching full agent config data - not fully implemented yet.");
                     // Find the *full* configuration for this agent - requires a different API endpoint or data structure
                     // For now, let's simulate opening with basic info
                     openAgentModal(agentId, { /* Assume fullConfig fetched */
                        provider: agentData?.provider,
                        model: agentData?.model,
                        persona: agentData?.persona,
                        // Missing: system_prompt, temperature, other kwargs
                     });

                 })
                 .catch(err => console.error("Error fetching agent details for edit:", err));
         });
     });
     configContent.querySelectorAll('.delete-button').forEach(button => {
         button.addEventListener('click', (e) => {
             const agentId = e.currentTarget.getAttribute('data-agent-id');
             handleDeleteAgentConfig(agentId);
         });
     });
};

const handleDeleteAgentConfig = async (agentId) => {
    if (!confirm(`Are you sure you want to delete the static configuration for agent '${agentId}'? This requires an application restart.`)) {
        return;
    }
    try {
        const result = await makeApiCall(`/api/config/agents/${agentId}`, 'DELETE');
        displayMessage(escapeHTML(result.message), 'system_event', 'internal-comms-area', 'system');
        loadStaticAgentConfig(); // Refresh list
    } catch (error) {
        // Error message displayed by makeApiCall
    }
};

// --- Agent Modal Functions ---
const openAgentModal = (agentIdToEdit = null, agentData = null) => {
    agentForm.reset(); // Clear form
    editAgentIdInput.value = agentIdToEdit || ''; // Set hidden field if editing

    if (agentIdToEdit && agentData) {
        modalTitle.textContent = `Edit Agent: ${agentIdToEdit}`;
        document.getElementById('agent-id').value = agentIdToEdit;
        document.getElementById('agent-id').readOnly = true; // Prevent editing ID
        // Populate form - NEED FULL CONFIG DATA
        document.getElementById('persona').value = agentData.persona || '';
        document.getElementById('provider').value = agentData.provider || 'openrouter';
        document.getElementById('model').value = agentData.model || '';
        document.getElementById('temperature').value = agentData.temperature || 0.7;
        document.getElementById('system_prompt').value = agentData.system_prompt || '';
        // TODO: Handle additional kwargs if they exist in agentData.config
    } else {
        modalTitle.textContent = 'Add New Static Agent';
        document.getElementById('agent-id').readOnly = false;
        // Set defaults for add mode if needed
        document.getElementById('temperature').value = 0.7;
    }

    agentModal.style.display = 'block';
};

const closeModal = (modalId) => {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
};

const handleAgentFormSubmit = async (event) => {
    event.preventDefault();
    const formData = new FormData(agentForm);
    const agentId = formData.get('agent_id');
    const isEditing = !!editAgentIdInput.value;

    // Collect extra args (basic example)
    const extraArgs = {};
    // Add logic here to collect fields not explicitly named if needed

    const agentConfigData = {
        provider: formData.get('provider'),
        model: formData.get('model'),
        system_prompt: formData.get('system_prompt'),
        temperature: parseFloat(formData.get('temperature')),
        persona: formData.get('persona'),
        ...extraArgs // Add any extra kwargs collected
    };

    const payload = {
        agent_id: agentId,
        config: agentConfigData
    };

    const endpoint = isEditing ? `/api/config/agents/${agentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';

    try {
        const result = await makeApiCall(endpoint, method, isEditing ? agentConfigData : payload); // Send AgentConfigInput for PUT
        displayMessage(escapeHTML(result.message), 'system_event', 'internal-comms-area', 'system');
        closeModal('agent-modal');
        loadStaticAgentConfig(); // Refresh list
    } catch (error) {
        // Error displayed by makeApiCall
        // Optional: display error inside modal?
        alert(`Error saving agent config: ${error.message || 'Unknown error'}`);
    }
};


// --- Session Management Functions ---
const loadProjects = async () => {
    if (!projectSelect) return;
    projectSelect.innerHTML = '<option value="">Loading Projects...</option>';
    projectSelect.disabled = true;
    sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
    sessionSelect.disabled = true;
    loadSessionButton.disabled = true;

    try {
        const projects = await makeApiCall('/api/projects');
        projectSelect.innerHTML = '<option value="">-- Select Project --</option>'; // Reset
        if (projects && projects.length > 0) {
            projects.forEach(proj => {
                const option = document.createElement('option');
                option.value = proj.project_name;
                option.textContent = proj.project_name;
                projectSelect.appendChild(option);
            });
            projectSelect.disabled = false;
            // Automatically load sessions for the first project if exists
            if (projects.length > 0) {
                 projectSelect.value = projects[0].project_name; // Select first project
                 await loadSessions(projects[0].project_name); // Load its sessions
            }
        } else {
            projectSelect.innerHTML = '<option value="">-- No Projects Found --</option>';
        }
    } catch (error) {
        projectSelect.innerHTML = '<option value="">-- Error Loading Projects --</option>';
        // Error message handled by makeApiCall
    }
};

const loadSessions = async (projectName) => {
    if (!sessionSelect || !projectName) {
        sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
        sessionSelect.disabled = true;
        loadSessionButton.disabled = true;
        return;
    }
    sessionSelect.innerHTML = '<option value="">Loading Sessions...</option>';
    sessionSelect.disabled = true;
    loadSessionButton.disabled = true;

    try {
        const sessions = await makeApiCall(`/api/projects/${projectName}/sessions`);
        sessionSelect.innerHTML = '<option value="">-- Select Session --</option>'; // Reset
        if (sessions && sessions.length > 0) {
            sessions.forEach(sess => {
                const option = document.createElement('option');
                option.value = sess.session_name;
                option.textContent = sess.session_name;
                sessionSelect.appendChild(option);
            });
            sessionSelect.disabled = false;
            // Enable load button only if a session is selected
            sessionSelect.addEventListener('change', () => {
                loadSessionButton.disabled = !sessionSelect.value;
            });
            loadSessionButton.disabled = !sessionSelect.value; // Initial state
        } else {
            sessionSelect.innerHTML = '<option value="">-- No Sessions Found --</option>';
        }
    } catch (error) {
        sessionSelect.innerHTML = '<option value="">-- Error Loading Sessions --</option>';
        loadSessionButton.disabled = true;
         // Error message handled by makeApiCall
    }
};

const handleProjectSelectionChange = () => {
    const selectedProject = projectSelect.value;
    loadSessions(selectedProject);
};

const displaySessionStatus = (message, isSuccess) => {
    if (!sessionStatusMessage) return;
    sessionStatusMessage.textContent = message;
    sessionStatusMessage.className = isSuccess ? 'session-status success' : 'session-status error'; // Use className to overwrite previous status
    sessionStatusMessage.style.display = 'block';
    // Optionally hide after a few seconds
    setTimeout(() => {
        sessionStatusMessage.style.display = 'none';
    }, 5000);
};

const handleLoadSession = async () => {
    const projectName = projectSelect.value;
    const sessionName = sessionSelect.value;

    if (!projectName || !sessionName) {
        displaySessionStatus("Error: Please select both a project and a session.", false);
        return;
    }
    loadSessionButton.disabled = true; // Disable button during load
    loadSessionButton.textContent = "Loading...";

    try {
        const result = await makeApiCall(`/api/projects/${projectName}/sessions/${sessionName}/load`, 'POST');
        displaySessionStatus(result.message, true);
        // Switch view to chat after successful load
        switchView('chat-view');
    } catch (error) {
        displaySessionStatus(`Error loading session: ${error.message || 'Unknown error'}`, false);
    } finally {
         loadSessionButton.disabled = false; // Re-enable button
         loadSessionButton.textContent = "Load Selected Session";
    }
};

const handleSaveSession = async () => {
    const projectName = saveProjectNameInput.value.trim();
    const sessionName = saveSessionNameInput.value.trim() || null; // Send null if empty

    if (!projectName) {
        displaySessionStatus("Error: Project name is required to save.", false);
        return;
    }
    saveSessionButton.disabled = true;
    saveSessionButton.textContent = "Saving...";

    try {
        const payload = sessionName ? { session_name: sessionName } : {};
        const result = await makeApiCall(`/api/projects/${projectName}/sessions`, 'POST', payload);
        displaySessionStatus(result.message, true);
        // Refresh project/session lists after saving
        await loadProjects();
        // Optionally select the newly saved project/session?
        // Might be complex if session name was auto-generated.
        // For now, just refresh the lists.
        saveProjectNameInput.value = ''; // Clear inputs after save
        saveSessionNameInput.value = '';
    } catch (error) {
        displaySessionStatus(`Error saving session: ${error.message || 'Unknown error'}`, false);
    } finally {
        saveSessionButton.disabled = false;
        saveSessionButton.textContent = "Save Current Session";
    }
};


// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed.");
    displayMessage("Welcome! Connecting to backend...", "status", "internal-comms-area", "system"); // Use internal comms for initial status
    connectWebSocket();
    setupEventListeners();
    switchView('chat-view'); // Ensure initial view is set correctly
    // Load initial data for views that need it
    // loadStaticAgentConfig(); // Load config on demand when switching view
    // loadProjects(); // Load projects/sessions on demand
});
