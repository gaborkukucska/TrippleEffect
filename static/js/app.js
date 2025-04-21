// START OF FILE static/js/app.js

// --- Configuration ---
const WS_URL = `ws://${window.location.host}/ws`;
const API_BASE_URL = ''; // Relative path for API calls
const INITIAL_RECONNECT_DELAY = 1000; // 1 second
const MAX_RECONNECT_DELAY = 30000; // 30 seconds
const MAX_COMM_MESSAGES = 200; // Max messages to keep in internal comms view (Renamed)
const MAX_CHAT_MESSAGES = 100; // Max messages to keep in main chat view

// --- State ---
let websocket = null;
let reconnectDelay = INITIAL_RECONNECT_DELAY;
let isConnected = false;
let currentView = 'chat-view'; // Default view
let attachedFile = null; // { name: string, content: string }

// --- DOM Elements (Ensure correct IDs) ---
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const conversationArea = document.getElementById('conversation-area');
const internalCommsArea = document.getElementById('internal-comms-area'); // *** Check ID is correct ***
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
const editAgentIdInput = document.getElementById('edit-agent-id');


// --- Utility Functions ---
const escapeHTML = (str) => {
    if (str === null || str === undefined) return '';
    // Replace potential objects/arrays with their JSON string representation before escaping
    if (typeof str === 'object') {
        try {
            str = JSON.stringify(str);
        } catch (e) {
            str = String(str); // Fallback to string conversion
        }
    }
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
    // Display initial connecting message in internal comms
    displayStatusMessage("Connecting...", true, false, 'internal-comms-area'); // Target internal comms

    websocket = new WebSocket(WS_URL);

    websocket.onopen = (event) => {
        console.log("WebSocket connection established.");
        isConnected = true;
        reconnectDelay = INITIAL_RECONNECT_DELAY;
        // Display connected message in internal comms
        displayStatusMessage("Connected to backend.", true, false, 'internal-comms-area');
    };

    websocket.onmessage = (event) => {
        try {
            const messageData = JSON.parse(event.data);
            console.debug("WebSocket message received:", messageData); // Log received data for debugging
            handleWebSocketMessage(messageData);
        } catch (error) {
            console.error("Error parsing WebSocket message:", error);
            displayMessage(`Error parsing message: ${escapeHTML(event.data)}`, 'error', 'internal-comms-area'); // Display raw data on parse error
        }
    };

    websocket.onerror = (event) => {
        console.error("WebSocket error:", event);
        // Display error in internal comms
        displayStatusMessage(`WebSocket error: ${event.type || 'Unknown error'}`, true, true, 'internal-comms-area');
    };

    websocket.onclose = (event) => {
        console.log(`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`);
        isConnected = false;
        // Display closed message in internal comms
        displayStatusMessage(`Connection closed (${event.code}). Reconnecting...`, true, true, 'internal-comms-area');
        setTimeout(connectWebSocket, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
    };
};

const sendMessage = (message) => {
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        websocket.send(message);
        console.debug(`Sent message: ${message.substring(0, 100)}...`);
    } else {
        console.error("WebSocket is not connected. Cannot send message.");
        // Display error in the main conversation area as it relates to user action
        displayMessage("Error: Not connected to backend. Message not sent.", "error", "conversation-area");
    }
};

// --- UI Update Functions ---

/**
 * Displays a message in the specified message area.
 * @param {string} text The message content (can be HTML, should be pre-escaped if needed).
 * @param {string} type Message type class (e.g., 'user', 'agent_response', 'status').
 * @param {string} targetAreaId ID of the container ('conversation-area' or 'internal-comms-area').
 * @param {string} [agentId=null] Optional agent ID.
 * @param {string} [agentPersona=null] Optional agent persona.
 */
const displayMessage = (text, type, targetAreaId, agentId = null, agentPersona = null) => {
    try { // Add try-catch around display logic
        const messageArea = document.getElementById(targetAreaId);
        if (!messageArea) {
            console.error(`Target message area #${targetAreaId} not found.`);
            return; // Exit if target doesn't exist
        }

        // Remove placeholder if it exists
        const placeholder = messageArea.querySelector('.initial-placeholder');
        if (placeholder) {
            placeholder.remove();
        }

        // Remove oldest messages if limit exceeded
        const maxMessages = targetAreaId === 'conversation-area' ? MAX_CHAT_MESSAGES : MAX_COMM_MESSAGES;
        while (messageArea.children.length >= maxMessages) {
            messageArea.removeChild(messageArea.firstChild);
        }

        const messageElement = document.createElement('div');
        messageElement.classList.add('message', type);
        if (agentId) {
            messageElement.setAttribute('data-agent-id', agentId);
            if (type === 'agent_response') {
                messageElement.classList.add('agent_response');
            }
        }

        // Timestamp only for internal comms
        const timestampSpan = (targetAreaId === 'internal-comms-area')
            ? `<span class="timestamp">${getCurrentTimestamp()}</span>`
            : '';

        let innerHTMLContent = timestampSpan; // Start with timestamp (if applicable)

        // Add Agent Label conditionally based on area and type
        if (targetAreaId === 'internal-comms-area') {
            if (agentPersona) {
                innerHTMLContent += `<span class="agent-label">${escapeHTML(agentPersona)} (${escapeHTML(agentId)}):</span>`;
            } else if (agentId && agentId !== 'manager' && agentId !== 'system' && agentId !== 'api' && agentId !== 'human_user') {
                 innerHTMLContent += `<span class="agent-label">Agent (${escapeHTML(agentId)}):</span>`;
            } else if (agentId) { // Handle system/manager/api labels
                 innerHTMLContent += `<span class="agent-label">${escapeHTML(agentId.replace('_',' ').toUpperCase())}:</span>`;
            }
        } else if (targetAreaId === 'conversation-area') {
            if (type === 'agent_response' && agentPersona) {
                innerHTMLContent += `<span class="agent-label">${escapeHTML(agentPersona)}:</span>`;
            }
        }

        // Add the main message content (assuming 'text' is HTML or pre-escaped)
        innerHTMLContent += `<span class="message-content">${text}</span>`;

        messageElement.innerHTML = innerHTMLContent;
        messageArea.appendChild(messageElement);

        // Auto-scroll to the bottom
        messageArea.scrollTop = messageArea.scrollHeight;
    } catch (error) {
        console.error(`Error in displayMessage (Target: ${targetAreaId}, Type: ${type}):`, error);
        // Optionally display a fallback error message in the UI
        const errorArea = document.getElementById('internal-comms-area'); // Default to internal comms for display errors
        if (errorArea) {
            const errorEl = document.createElement('div');
            errorEl.className = 'message error';
            errorEl.innerHTML = `<span class="timestamp">${getCurrentTimestamp()}</span><span class="message-content">!! UI Error displaying message (Type: ${type}) !!</span>`;
            errorArea.appendChild(errorEl);
            errorArea.scrollTop = errorArea.scrollHeight;
        }
    }
};


/**
 * Displays a status message specifically in the specified target area.
 * @param {string} message The status text.
 * @param {boolean} [temporary=false] If true, the message might be removed later.
 * @param {boolean} [isError=false] If true, style as an error.
 * @param {string} [targetAreaId='internal-comms-area'] Target area ID.
 */
const displayStatusMessage = (message, temporary = false, isError = false, targetAreaId = 'internal-comms-area') => {
    const messageType = isError ? 'error' : 'status';
    displayMessage(escapeHTML(message), messageType, targetAreaId, 'system');
};


const updateAgentStatusUI = (agentStatusData) => {
    if (!agentStatusContent) return;

    try { // Add try-catch
        agentStatusContent.innerHTML = ''; // Clear existing statuses
        const agentIds = Object.keys(agentStatusData);

        if (agentIds.length === 0) {
            agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>';
            return;
        }

        agentIds.sort((a, b) => {
            if (a === 'admin_ai') return -1;
            if (b === 'admin_ai') return 1;
            return a.localeCompare(b);
        });

        agentIds.forEach(agentId => {
            const agent = agentStatusData[agentId];
            if (!agent || agent.status === 'deleted') return;

            const statusItem = document.createElement('div');
            statusItem.classList.add('agent-status-item', `status-${agent.status || 'unknown'}`);
            statusItem.setAttribute('data-agent-id', agentId);

            const persona = agent.persona || agentId;
            // Adjusted to use the canonical model ID from config if available
            const modelDisplay = agent.model ? `(${escapeHTML(agent.model)})` : '(Model N/A)';
            const teamInfo = agent.team ? `<span class="agent-team">[${escapeHTML(agent.team)}]</span>` : '';

            const agentInfoSpan = document.createElement('span');
            agentInfoSpan.innerHTML = `<strong>${escapeHTML(persona)}</strong> <span class="agent-model">${modelDisplay}</span> ${teamInfo}`;

            const statusBadgeSpan = document.createElement('span');
            statusBadgeSpan.classList.add('agent-status');
            statusBadgeSpan.textContent = agent.status || 'unknown';

            statusItem.appendChild(agentInfoSpan);
            statusItem.appendChild(statusBadgeSpan);
            agentStatusContent.appendChild(statusItem);
        });
    } catch (error) {
        console.error("Error updating agent status UI:", error);
        agentStatusContent.innerHTML = '<span class="status-placeholder">Error loading agent status.</span>';
    }
};

/**
 * Handles incoming WebSocket messages and routes them to appropriate UI handlers.
 * @param {object} data The parsed message data from the WebSocket.
 */
const handleWebSocketMessage = (data) => {
    try { // Add try-catch around the handler
        const messageType = data.type;
        const agentId = data.agent_id || 'system'; // Default to system if no agent_id
        const agentPersona = data.persona; // May be present

        console.log(`Handling message type: ${messageType} from agent: ${agentId}`); // Added logging

        // Remove initial connecting message if it exists in internal comms
        const connectingMsg = internalCommsArea?.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();

        // --- Routing Logic ---
        let targetArea = 'internal-comms-area'; // Default target
        let displayPersona = agentPersona;
        let displayAgentId = agentId;
        let displayContent = data.content || data.message || JSON.stringify(data); // Fallback content

        switch (messageType) {
            case 'agent_response':
                if (agentId === 'admin_ai') {
                    targetArea = 'conversation-area';
                    // Content might be HTML (e.g., containing tool calls), handle carefully
                    // Assuming backend doesn't send harmful HTML
                    displayContent = data.content;
                } else {
                    targetArea = 'internal-comms-area';
                    displayContent = escapeHTML(data.content);
                }
                break;

            case 'user': // Should only happen if backend echoes user message (currently doesn't)
                targetArea = 'conversation-area';
                displayContent = escapeHTML(displayContent);
                break;

            case 'status':
            case 'system_event':
            case 'log': // Treat 'log' type messages as internal comms events
                targetArea = 'internal-comms-area';
                displayContent = escapeHTML(displayContent);
                displayAgentId = agentId || 'system'; // Ensure system label if agent missing
                break;

            case 'error':
                targetArea = 'internal-comms-area';
                displayContent = `❗ Error: ${escapeHTML(displayContent)}`; // Add indicator
                displayAgentId = agentId || 'system';
                break;

            case 'agent_status_update':
                // This updates the separate agent status list, not a message area
                if (data.status && typeof data.status === 'object') {
                    if (!data.status.agent_id && agentId) data.status.agent_id = agentId;
                    // Pass the single agent update, let updateAgentStatusUI handle merging/replacing
                    updateAgentStatusUI({ [agentId]: data.status });
                } else {
                    console.warn("Received agent_status_update without valid status object:", data);
                }
                return; // Don't proceed to displayMessage for status updates

            case 'agent_added':
            case 'agent_deleted':
            case 'team_created':
            case 'team_deleted':
            case 'session_saved': // Handle session events
            case 'session_loaded':
                 targetArea = 'internal-comms-area';
                 const eventMap = {
                    'agent_added': `Agent Added: ${data.agent_id} (${data.config?.persona || 'N/A'})`,
                    'agent_deleted': `Agent Deleted: ${data.agent_id}`,
                    'team_created': `Team Created: ${data.team_id}`,
                    'team_deleted': `Team Deleted: ${data.team_id}`,
                    'session_saved': `Session Saved: ${data.project}/${data.session}`,
                    'session_loaded': `Session Loaded: ${data.project}/${data.session}`,
                 };
                 displayContent = escapeHTML(eventMap[messageType] || data.message || `Event: ${messageType}`);
                 displayAgentId = 'system';
                 // Request full status after add/delete to refresh agent list UI
                 // if (messageType === 'agent_added' || messageType === 'agent_deleted') {
                 //   sendMessage(JSON.stringify({ type: 'get_full_status' })); // If backend supports this
                 // }
                 break; // Proceed to display this system event message

            default:
                console.warn(`Received unknown message type: ${messageType}`, data);
                targetArea = 'internal-comms-area'; // Fallback to internal comms
                displayContent = `Unknown msg type '${escapeHTML(messageType)}': ${escapeHTML(JSON.stringify(data))}`;
                displayAgentId = 'system';
        }

        // Display the message in the determined target area
        if (targetArea) {
            displayMessage(displayContent, messageType, targetArea, displayAgentId, displayPersona);
        }

    } catch (error) {
        console.error("Error in handleWebSocketMessage:", error);
        // Display a generic error in internal comms if the handler fails
        displayMessage(`!! JS Error handling message: ${escapeHTML(error.message)} !!`, 'error', 'internal-comms-area', 'frontend');
    }
};


// --- View Switching ---
const switchView = (viewId) => {
    console.log(`Switching view to: ${viewId}`);
    if (!document.getElementById(viewId)) {
        console.error(`Cannot switch view: Element with ID '${viewId}' not found.`);
        return;
    }
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

    // Load data relevant to the view
    if (viewId === 'config-view' && configContent) {
        loadStaticAgentConfig();
    } else if (viewId === 'session-view' && projectSelect) {
        loadProjects();
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
        console.debug(`API Call: ${method} ${url}`, body ? JSON.stringify(body).substring(0,100) + '...' : ''); // Log API call
        const response = await fetch(url, options);
        const responseData = await response.json();
        if (!response.ok) {
            const error = new Error(responseData.detail || `HTTP error ${response.status}`);
            error.status = response.status;
            error.responseBody = responseData;
            throw error;
        }
        return responseData;
    } catch (error) {
        console.error(`API call error (${method} ${endpoint}):`, error);
        const errorDetail = error.responseBody?.detail || error.message || 'Unknown API error';
        // Display API errors in the internal comms area
        displayMessage(`API Error (${method} ${endpoint}): ${escapeHTML(errorDetail)}`, 'error', 'internal-comms-area', 'api');
        throw error;
    }
};

// --- Event Listeners ---
const setupEventListeners = () => {
    // Ensure elements exist before adding listeners
    sendButton?.addEventListener('click', () => {
        const message = messageInput?.value?.trim();
        if (message || attachedFile) {
            if (attachedFile) {
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
                sendMessage(message);
                 displayMessage(escapeHTML(message), 'user', 'conversation-area');
            }
            if (messageInput) {
                messageInput.value = '';
                messageInput.style.height = 'auto';
                messageInput.style.height = messageInput.scrollHeight + 'px';
                messageInput.style.height = '60px';
            }
        }
    });

    messageInput?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendButton?.click();
        }
    });

    messageInput?.addEventListener('input', () => {
        if (messageInput) {
            messageInput.style.height = 'auto';
            messageInput.style.height = messageInput.scrollHeight + 'px';
        }
    });

    navButtons.forEach(button => {
        button.addEventListener('click', () => {
            const viewId = button.getAttribute('data-view');
            if (viewId) {
                switchView(viewId);
            }
        });
    });

     attachFileButton?.addEventListener('click', () => fileInput?.click());
     fileInput?.addEventListener('change', handleFileSelect);

     refreshConfigButton?.addEventListener('click', loadStaticAgentConfig);
     addAgentButton?.addEventListener('click', () => openAgentModal(null));

     agentForm?.addEventListener('submit', handleAgentFormSubmit);

     projectSelect?.addEventListener('change', handleProjectSelectionChange);
     loadSessionButton?.addEventListener('click', handleLoadSession);
     saveSessionButton?.addEventListener('click', handleSaveSession);
};

// --- File Attachment Handling --- (No changes needed)
const handleFileSelect = (event) => {
    const file = event.target.files[0];
    if (file) {
        if (!file.type.startsWith('text/') && !/\.(py|js|json|yaml|md|log|csv|html|css)$/i.test(file.name)) {
             alert('Error: Only text-based files (.txt, .py, .js, .css, .html, .md, .json, .yaml, .csv, .log) are allowed.');
             fileInput.value = '';
             return;
         }
         const maxSize = 5 * 1024 * 1024;
         if (file.size > maxSize) {
             alert(`Error: File size exceeds the limit of ${maxSize / 1024 / 1024}MB.`);
             fileInput.value = '';
             return;
         }
        const reader = new FileReader();
        reader.onload = (e) => {
            attachedFile = { name: file.name, content: e.target.result };
            displayFileInfo();
        };
        reader.onerror = (e) => {
            console.error("File reading error:", e);
            alert("Error reading file.");
            clearAttachment();
        };
        reader.readAsText(file);
    }
     event.target.value = null;
};

const displayFileInfo = () => {
    if (attachedFile && fileInfoArea) {
        fileInfoArea.innerHTML = `
            <span>Attached: ${escapeHTML(attachedFile.name)}</span>
            <button onclick="clearAttachment()" title="Remove file">×</button>
        `;
        fileInfoArea.style.display = 'flex';
    } else if (fileInfoArea) {
        fileInfoArea.style.display = 'none';
        fileInfoArea.innerHTML = '';
    }
};

const clearAttachment = () => {
    attachedFile = null;
    if(fileInput) fileInput.value = '';
    displayFileInfo();
};

// --- Static Agent Config Functions --- (Minor logging adjustment)
const loadStaticAgentConfig = async () => {
    if (!configContent) return;
    configContent.innerHTML = '<span class="status-placeholder">Loading config...</span>';
    try {
        const agentConfigs = await makeApiCall('/api/config/agents');
        renderStaticAgentConfig(agentConfigs);
    } catch (error) {
         configContent.innerHTML = '<span class="status-placeholder">Error loading config.</span>';
    }
};

const renderStaticAgentConfig = (agentConfigs) => {
    if (!configContent) return;
    configContent.innerHTML = '';

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
                <!-- Edit button disabled for now until full config fetching is implemented -->
                <button class="config-action-button edit-button" data-agent-id="${escapeHTML(agent.agent_id)}" title="Edit Agent (Requires Backend Update)" disabled>✏️</button>
                <button class="config-action-button delete-button" data-agent-id="${escapeHTML(agent.agent_id)}" title="Delete Agent">🗑️</button>
            </span>
        `;
        configContent.appendChild(item);
    });

     configContent.querySelectorAll('.edit-button:not([disabled])').forEach(button => { // Only add listener if not disabled
         button.addEventListener('click', (e) => {
             const agentId = e.currentTarget.getAttribute('data-agent-id');
             // Placeholder - requires backend endpoint to fetch FULL config for an agent ID
             alert(`Editing agent '${agentId}' requires a backend update to fetch full configuration details.`);
             // Example if backend provided full data:
             // makeApiCall(`/api/config/agents/${agentId}/details`) // Fictional endpoint
             //     .then(fullAgentData => {
             //         openAgentModal(agentId, fullAgentData.config); // Pass the inner config object
             //     })
             //     .catch(err => console.error("Error fetching agent details for edit:", err));
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
        loadStaticAgentConfig();
    } catch (error) {
        // Error handled by makeApiCall
    }
};

// --- Agent Modal Functions --- (No changes needed)
const openAgentModal = (agentIdToEdit = null, agentData = null) => {
    if (!agentModal || !agentForm || !modalTitle || !editAgentIdInput) return; // Check elements
    agentForm.reset();
    editAgentIdInput.value = agentIdToEdit || '';

    const agentIdField = document.getElementById('agent-id'); // Get field inside function

    if (agentIdToEdit && agentData) {
        modalTitle.textContent = `Edit Agent: ${agentIdToEdit}`;
        if (agentIdField) {
            agentIdField.value = agentIdToEdit;
            agentIdField.readOnly = true;
        }
        document.getElementById('persona').value = agentData.persona || '';
        document.getElementById('provider').value = agentData.provider || 'openrouter';
        document.getElementById('model').value = agentData.model || '';
        document.getElementById('temperature').value = agentData.temperature ?? 0.7; // Use ?? for default
        document.getElementById('system_prompt').value = agentData.system_prompt || '';
        // TODO: Handle additional kwargs if they exist in agentData
    } else {
        modalTitle.textContent = 'Add New Static Agent';
        if (agentIdField) agentIdField.readOnly = false;
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

    const extraArgs = {};

    const agentConfigData = {
        provider: formData.get('provider'),
        model: formData.get('model'),
        system_prompt: formData.get('system_prompt'),
        temperature: parseFloat(formData.get('temperature')),
        persona: formData.get('persona'),
        ...extraArgs
    };

    const payload = {
        agent_id: agentId,
        config: agentConfigData
    };

    const endpoint = isEditing ? `/api/config/agents/${agentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';

    try {
        const result = await makeApiCall(endpoint, method, isEditing ? agentConfigData : payload);
        displayMessage(escapeHTML(result.message), 'system_event', 'internal-comms-area', 'system');
        closeModal('agent-modal');
        loadStaticAgentConfig();
    } catch (error) {
        alert(`Error saving agent config: ${error.message || 'Unknown error'}`);
    }
};


// --- Session Management Functions --- (No changes needed)
const loadProjects = async () => {
    if (!projectSelect) return;
    projectSelect.innerHTML = '<option value="">Loading Projects...</option>';
    projectSelect.disabled = true;
    if (sessionSelect) {
        sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
        sessionSelect.disabled = true;
    }
    if (loadSessionButton) loadSessionButton.disabled = true;

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
            if (projects.length > 0) {
                 projectSelect.value = projects[0].project_name;
                 await loadSessions(projects[0].project_name);
            }
        } else {
            projectSelect.innerHTML = '<option value="">-- No Projects Found --</option>';
        }
    } catch (error) {
        projectSelect.innerHTML = '<option value="">-- Error Loading Projects --</option>';
    }
};

const loadSessions = async (projectName) => {
    if (!sessionSelect || !projectName) {
        if(sessionSelect) sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
        if(sessionSelect) sessionSelect.disabled = true;
        if(loadSessionButton) loadSessionButton.disabled = true;
        return;
    }
    sessionSelect.innerHTML = '<option value="">Loading Sessions...</option>';
    sessionSelect.disabled = true;
    if(loadSessionButton) loadSessionButton.disabled = true;

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
            if (loadSessionButton) {
                sessionSelect.addEventListener('change', () => {
                    loadSessionButton.disabled = !sessionSelect.value;
                });
                loadSessionButton.disabled = !sessionSelect.value;
            }
        } else {
            sessionSelect.innerHTML = '<option value="">-- No Sessions Found --</option>';
        }
    } catch (error) {
        sessionSelect.innerHTML = '<option value="">-- Error Loading Sessions --</option>';
        if(loadSessionButton) loadSessionButton.disabled = true;
    }
};

const handleProjectSelectionChange = () => {
    if(projectSelect) {
        const selectedProject = projectSelect.value;
        loadSessions(selectedProject);
    }
};

const displaySessionStatus = (message, isSuccess) => {
    if (!sessionStatusMessage) return;
    sessionStatusMessage.textContent = message;
    sessionStatusMessage.className = isSuccess ? 'session-status success' : 'session-status error';
    sessionStatusMessage.style.display = 'block';
    setTimeout(() => {
        sessionStatusMessage.style.display = 'none';
    }, 5000);
};

const handleLoadSession = async () => {
    const projectName = projectSelect?.value;
    const sessionName = sessionSelect?.value;

    if (!projectName || !sessionName) {
        displaySessionStatus("Error: Please select both a project and a session.", false);
        return;
    }
    if(loadSessionButton) {
        loadSessionButton.disabled = true;
        loadSessionButton.textContent = "Loading...";
    }

    try {
        const result = await makeApiCall(`/api/projects/${projectName}/sessions/${sessionName}/load`, 'POST');
        displaySessionStatus(result.message, true);
        switchView('chat-view');
    } catch (error) {
        displaySessionStatus(`Error loading session: ${error.message || 'Unknown error'}`, false);
    } finally {
         if(loadSessionButton) {
             loadSessionButton.disabled = false;
             loadSessionButton.textContent = "Load Selected Session";
         }
    }
};

const handleSaveSession = async () => {
    const projectName = saveProjectNameInput?.value?.trim();
    const sessionName = saveSessionNameInput?.value?.trim() || null;

    if (!projectName) {
        displaySessionStatus("Error: Project name is required to save.", false);
        return;
    }
    if(saveSessionButton) {
        saveSessionButton.disabled = true;
        saveSessionButton.textContent = "Saving...";
    }

    try {
        const payload = sessionName ? { session_name: sessionName } : {};
        const result = await makeApiCall(`/api/projects/${projectName}/sessions`, 'POST', payload);
        displaySessionStatus(result.message, true);
        await loadProjects(); // Refresh lists
        if(saveProjectNameInput) saveProjectNameInput.value = '';
        if(saveSessionNameInput) saveSessionNameInput.value = '';
    } catch (error) {
        displaySessionStatus(`Error saving session: ${error.message || 'Unknown error'}`, false);
    } finally {
        if(saveSessionButton) {
            saveSessionButton.disabled = false;
            saveSessionButton.textContent = "Save Current Session";
        }
    }
};


// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded and parsed.");
    // Use internal comms area for initial status messages
    displayStatusMessage("Welcome! Initializing connection...", true, false, 'internal-comms-area');
    connectWebSocket();
    setupEventListeners();
    switchView('chat-view'); // Start on chat view
});
