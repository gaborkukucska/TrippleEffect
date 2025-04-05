// START OF FILE static/js/app.js

// --- DOM Elements ---
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const attachFileButton = document.getElementById('attach-file-button');
const fileInput = document.getElementById('file-input');
const fileInfoArea = document.getElementById('file-info-area');
const configContent = document.getElementById('config-content');
const agentStatusContent = document.getElementById('agent-status-content');
const addAgentButton = document.getElementById('add-agent-button'); // Phase 8
const refreshConfigButton = document.getElementById('refresh-config-button'); // Phase 8 Refresh
const agentModal = document.getElementById('agent-modal'); // Phase 8
const agentForm = document.getElementById('agent-form'); // Phase 8
const modalTitle = document.getElementById('modal-title'); // Phase 8
const editAgentIdField = document.getElementById('edit-agent-id'); // Phase 8

// --- WebSocket Connection ---
let websocket = null;
let selectedFile = null; // To store the selected file object

// --- Helper Functions ---

// Scrolls a specific message area to the bottom
function scrollToBottom(element) {
    if (element) {
        element.scrollTop = element.scrollHeight;
    }
}

// Function to add a message to a specific area (conversation or system)
function addMessage(areaElement, content, type, agentId = null) {
    if (!areaElement) return;

    // Remove initial placeholder if it exists
    const placeholder = areaElement.querySelector('.initial-placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', type); // e.g., 'user', 'agent_response', 'status', 'error'
    if (agentId) {
        messageElement.dataset.agentId = agentId; // Add agent ID for styling
        // Sanitize agentId slightly for CSS class if using status updates directly as classes
        const safeAgentIdClass = agentId.replace(/[^a-zA-Z0-9-_]/g, '');
        messageElement.classList.add(`agent-${safeAgentIdClass}`);
    }

     // Simple check for potential HTML, basic escaping for safety (more robust needed for complex HTML)
     if (typeof content === 'string' ) {
          // Basic check for '<' and '>' which might indicate HTML
          if (content.includes('<') && content.includes('>')) {
                // In a real app, use a proper sanitizer here!
                // For now, let's just display it, assuming simple cases or backend is trusted
                messageElement.innerHTML = content; // Use innerHTML if content seems like HTML
          } else {
               messageElement.textContent = content; // Use textContent for plain text
          }
     } else {
          messageElement.textContent = JSON.stringify(content); // Fallback for non-strings
     }


    // Special handling for tool execution status messages
    if (type === 'status' && content.includes("Executing tool")) {
        messageElement.classList.add('tool-execution');
    }

    areaElement.appendChild(messageElement);
    scrollToBottom(areaElement); // Scroll down after adding message
}

// --- WebSocket Event Handlers ---

function connectWebSocket() {
    const wsScheme = window.location.protocol === "https:" ? "wss" : "ws";
    const wsURL = `${wsScheme}://${window.location.host}/ws`;
    websocket = new WebSocket(wsURL);

    websocket.onopen = (event) => {
        console.log("WebSocket connection established");
        // Remove initial "connecting" message if exists
        const connectingMsg = systemLogArea.querySelector('.initial-connecting');
        if(connectingMsg) connectingMsg.remove();
        // Add connection success message
        addMessage(systemLogArea, "Backend connection established.", "status");
        // Fetch initial configurations and agent statuses
        fetchAgentConfigurations();
        // Fetch initial agent statuses (might be combined or rely on updates)
        // clearAgentStatusUI(); // Clear placeholder
        // Optionally send a message to request current statuses if not pushed automatically
    };

    websocket.onmessage = (event) => {
        // console.log("Message received:", event.data); // Optional: Log raw message
        try {
            const messageData = JSON.parse(event.data);
            const messageType = messageData.type;
            const messageContent = messageData.content;
            const agentId = messageData.agent_id || 'unknown_agent'; // Default if not provided
            const agentPersona = messageData.persona || agentId; // Use persona if available

            switch (messageType) {
                case 'response_chunk':
                    // Append chunk to the agent's response area in conversation
                    // Find or create a placeholder for this agent's turn
                    let agentResponsePlaceholder = conversationArea.querySelector(`.agent_response[data-agent-id="${agentId}"].current-turn`);
                    if (!agentResponsePlaceholder) {
                        agentResponsePlaceholder = document.createElement('div');
                        agentResponsePlaceholder.classList.add('message', 'agent_response', 'current-turn');
                        agentResponsePlaceholder.dataset.agentId = agentId;
                        // Add agent identifier visually using persona
                         agentResponsePlaceholder.innerHTML = `<strong class="agent-name">[${agentPersona}]:</strong> `;
                        conversationArea.appendChild(agentResponsePlaceholder);
                    }
                     // Append text content safely
                     const contentSpan = document.createElement('span');
                     contentSpan.textContent = messageContent;
                     agentResponsePlaceholder.appendChild(contentSpan);

                    scrollToBottom(conversationArea);
                    break;
                case 'final_response':
                    // Mark the current turn as complete
                    let finalAgentResponse = conversationArea.querySelector(`.agent_response[data-agent-id="${agentId}"].current-turn`);
                    if (finalAgentResponse) {
                        finalAgentResponse.classList.remove('current-turn');
                         // Optionally add a small visual indicator that the turn is complete
                    }
                    // We already appended the content via chunks, so nothing more needed here usually
                    // Unless final_response contains extra info or needs different handling
                    break;
                case 'status':
                case 'info': // Treat 'info' like 'status'
                    // Display status messages in the system log area
                    addMessage(systemLogArea, `[${agentPersona}]: ${messageContent}`, "status", agentId);
                    break;
                case 'error':
                    // Display error messages prominently in the system log area
                    addMessage(systemLogArea, `[${agentPersona} ERROR]: ${messageContent}`, "error", agentId);
                     // Optionally also show a brief error indicator in the conversation area if needed
                     // addMessage(conversationArea, `[${agentPersona} Error Occurred]`, "error", agentId);
                    break;
                case 'agent_status_update':
                    // Handle detailed status updates for a specific agent
                    updateAgentStatusUI(messageData.agent_id, messageData.status);
                    break;
                 case 'tool_requests': // Manager handles execution, Agent yields this
                     addMessage(systemLogArea, `[${agentPersona}]: Requesting tool execution... (${messageData.calls?.length || 0} calls)`, "status tool-request", agentId);
                     // UI might show tool details based on messageData.calls if desired
                     break;
                 case 'tool_result': // Result being sent back to agent (maybe log?)
                     addMessage(systemLogArea, `[${agentPersona}]: Received tool result for call ${messageData.call_id}`, "status", agentId);
                     break;
                default:
                    // Handle unknown message types
                    console.warn("Received unknown message type:", messageType, messageData);
                    addMessage(systemLogArea, `Received unhandled message type '${messageType}' from backend.`, "status");
            }
        } catch (error) {
            console.error("Failed to parse WebSocket message or handle UI update:", error);
            addMessage(systemLogArea, `Error processing message from backend: ${error}`, "error");
        }
    };

    websocket.onclose = (event) => {
        console.log("WebSocket connection closed:", event.wasClean ? "Clean" : "Unclean", "Code:", event.code, "Reason:", event.reason);
        addMessage(systemLogArea, `Backend connection closed. ${event.reason ? `Reason: ${event.reason}` : ''} Attempting to reconnect...`, "error");
        websocket = null;
        // Attempt to reconnect after a delay
        setTimeout(connectWebSocket, 5000); // Reconnect after 5 seconds
    };

    websocket.onerror = (event) => {
        console.error("WebSocket error observed:", event);
        // Add error message - onclose will likely be called next, triggering reconnect logic
        addMessage(systemLogArea, "WebSocket connection error.", "error");
    };
}

// --- Message Sending ---

function sendMessage() {
    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
        addMessage(systemLogArea, "Cannot send message: WebSocket is not connected.", "error");
        return;
    }

    const messageText = messageInput.value.trim();
    let messageToSend = messageText;

    // Clear previous agent responses/statuses marked as 'current-turn' before sending new message
    clearAllAgentResponsePlaceholders();
    // Optionally clear system logs? Or just specific status messages? Decide based on desired UX.

    // --- File Handling ---
    if (selectedFile) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const fileContent = e.target.result;
            // Prepend file info and content to the message
            messageToSend = `Attached File: ${selectedFile.name}\n\n\`\`\`\n${fileContent}\n\`\`\`\n\nUser Task:\n${messageText}`;

            // Add user message (including file content) to conversation area
            addMessage(conversationArea, messageToSend, "user");
            // Send the combined message
            websocket.send(messageToSend);

            // Clear input and file selection after sending
            messageInput.value = '';
            clearSelectedFile();
        };
        reader.onerror = (e) => {
            addMessage(systemLogArea, `Error reading file ${selectedFile.name}: ${e.target.error}`, "error");
            clearSelectedFile();
        };
        reader.readAsText(selectedFile); // Read the file as text
    } else if (messageText) {
        // --- Send only text message ---
        addMessage(conversationArea, messageText, "user");
        websocket.send(messageText);
        messageInput.value = ''; // Clear input after sending
    } else {
        // No text and no file
        addMessage(systemLogArea, "Cannot send empty message.", "status");
    }
}

// Function to clear agent response placeholders marked as 'current-turn'
function clearAllAgentResponsePlaceholders() {
    const placeholders = conversationArea.querySelectorAll('.agent_response.current-turn');
    placeholders.forEach(p => p.classList.remove('current-turn')); // Just remove the class
}

// --- File Handling Functions ---

function handleFileSelect() {
    if (fileInput.files.length > 0) {
        selectedFile = fileInput.files[0];
        // Basic validation (optional: check file type, size)
        if (selectedFile.size > 5 * 1024 * 1024) { // Example: 5MB limit
            addMessage(systemLogArea, `File size (${(selectedFile.size / 1024 / 1024).toFixed(2)}MB) exceeds limit.`, "error");
            clearSelectedFile();
            return;
        }
         if (!selectedFile.type.startsWith('text/') && !/\.(py|js|html|css|md|json|yaml|csv)$/i.test(selectedFile.name)) {
              addMessage(systemLogArea, `Unsupported file type: ${selectedFile.type || 'Unknown'}. Please select a text-based file.`, "error");
              clearSelectedFile();
              return;
          }
        displayFileInfo(selectedFile.name);
    } else {
        selectedFile = null;
        clearSelectedFile();
    }
}

function displayFileInfo(filename) {
    fileInfoArea.innerHTML = `
        <span>Attached: ${filename}</span>
        <button onclick="clearSelectedFile()" title="Remove file">×</button>
    `;
    fileInfoArea.style.display = 'flex'; // Show the area
}

function clearSelectedFile() {
    selectedFile = null;
    fileInput.value = ''; // Clear the file input element
    fileInfoArea.innerHTML = '';
    fileInfoArea.style.display = 'none'; // Hide the area
}


// --- Agent Configuration and Status Display ---

// Fetch agent configurations from the backend API
async function fetchAgentConfigurations() {
    console.log("Fetching agent configurations...");
    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Failed to fetch configurations: ${response.status} ${response.statusText} - ${errorText}`);
        }
        const configs = await response.json();
        console.log("Configurations received:", configs);
        displayAgentConfigurations(configs);
    } catch (error) {
        console.error("Error fetching agent configurations:", error);
        configContent.innerHTML = `<span class="status-placeholder error">Error loading configurations: ${error.message}</span>`;
    }
}

// Display agent configurations in the UI (Phase 8: Adds Edit/Delete buttons)
function displayAgentConfigurations(configs) {
    configContent.innerHTML = ''; // Clear previous content
    if (configs.length === 0) {
        configContent.innerHTML = '<span class="status-placeholder">No agents configured.</span>';
        return;
    }
    configs.forEach(agent => {
        const item = document.createElement('div');
        item.classList.add('config-item');
        item.innerHTML = `
            <span>
                <strong>${agent.persona || agent.agent_id}</strong> <span class="agent-details">(${agent.agent_id} - ${agent.provider} / ${agent.model})</span>
            </span>
            <div class="config-item-actions">
                <button class="config-action-button edit-button" data-agent-id="${agent.agent_id}" title="Edit Agent">Edit</button>
                <button class="config-action-button delete-button" data-agent-id="${agent.agent_id}" title="Delete Agent">Delete</button>
            </div>
        `;
        configContent.appendChild(item);
    });

    // Add event listeners for the new buttons
    configContent.querySelectorAll('.edit-button').forEach(button => {
        button.addEventListener('click', () => openEditAgentModal(button.dataset.agentId));
    });
    configContent.querySelectorAll('.delete-button').forEach(button => {
        button.addEventListener('click', () => handleDeleteAgent(button.dataset.agentId));
    });
}

// Clear agent status UI placeholders
function clearAgentStatusUI() {
    agentStatusContent.innerHTML = ''; // Clear placeholder or previous statuses
}

// Update the status display for a specific agent
function updateAgentStatusUI(agentId, statusData) {
    if (!statusData || !agentId) return;

    let agentItem = agentStatusContent.querySelector(`.agent-status-item[data-agent-id="${agentId}"]`);

    if (!agentItem) {
        // If agent item doesn't exist, create it
        agentItem = document.createElement('div');
        agentItem.classList.add('agent-status-item');
        agentItem.dataset.agentId = agentId;
         // Clear placeholder if it's the first real status
         const placeholder = agentStatusContent.querySelector('.status-placeholder');
         if (placeholder) placeholder.remove();
        agentStatusContent.appendChild(agentItem);
    }

    // Determine status text and class
    const statusText = statusData.status || 'unknown';
    const statusClass = `status-${statusText.toLowerCase().replace(/\s+/g, '_')}`; // e.g., status-processing

    // Update content and classes
    agentItem.innerHTML = `
        <strong>${statusData.persona || agentId}</strong>
        <span class="agent-model">(${statusData.model || 'N/A'})</span>
        <span class="agent-status">${statusText}</span>
        ${statusData.current_tool ? `<span class="current-tool-info"> -> Tool: ${statusData.current_tool.name}</span>` : ''}
    `;

    // Update status class - remove old, add new
    agentItem.className = 'agent-status-item'; // Reset classes
    agentItem.classList.add(statusClass);
    agentItem.dataset.agentId = agentId; // Ensure data attribute persists
}


// --- Phase 8: Modal and CRUD Logic ---

function openAddAgentModal() {
    modalTitle.textContent = 'Add New Agent';
    agentForm.reset(); // Clear form
    editAgentIdField.value = ''; // Ensure hidden ID field is empty
    document.getElementById('agent-id').disabled = false; // Ensure Agent ID is editable
    agentModal.style.display = 'block';
}

async function openEditAgentModal(agentId) {
    modalTitle.textContent = `Edit Agent: ${agentId}`;
    agentForm.reset();
    editAgentIdField.value = agentId; // Store the ID of the agent being edited
    document.getElementById('agent-id').disabled = true; // Disable editing agent ID

    // Fetch the current config for this agent to pre-fill the form
    // This requires an endpoint like GET /api/config/agents/{agent_id} or filtering the main list
    // Filtering the main list is simpler for now.
    console.log(`Attempting to edit agent: ${agentId}`);
    try {
        // Re-fetch the full list to ensure we have the latest data before editing
        // NOTE: This still relies on get_config in ConfigManager providing full detail.
        // If get_config was changed to only return basic info, this needs adjustment.
        const response = await fetch('/api/config/agents'); // Fetch basic list first
        if (!response.ok) throw new Error('Could not fetch agent list for editing.');
        const allConfigsBasic = await response.json();
        const agentBasicInfo = allConfigsBasic.find(c => c.agent_id === agentId);

        // Now, assume ConfigManager holds the full, current data internally.
        // Fetching again via API might be cleaner if ConfigManager state could diverge.
        // For now, rely on ConfigManager's internal state via a dedicated endpoint (if we had one)
        // or assume the basic info is enough + defaults.
        // Let's *assume* we need the full config, requiring a better way.
        // WORKAROUND: Ask config_manager (via settings) - This won't work directly from JS.
        // We MUST rely on an API endpoint that returns the FULL config for the agent ID.
        // Since we don't have one, we'll prefill only based on the basic info and defaults.
        // This means System Prompt and Temperature won't be prefilled correctly on edit.

        if (agentBasicInfo) {
            console.log("Prefilling form for:", agentBasicInfo);
             document.getElementById('agent-id').value = agentBasicInfo.agent_id; // Although disabled, set for clarity
             document.getElementById('persona').value = agentBasicInfo.persona || '';
             document.getElementById('provider').value = agentBasicInfo.provider || 'openrouter';
             document.getElementById('model').value = agentBasicInfo.model || '';
             // Cannot reliably prefill temp or prompt without full config access. Use defaults.
             document.getElementById('temperature').value = settings?.DEFAULT_TEMPERATURE || 0.7;
             document.getElementById('system_prompt').value = settings?.DEFAULT_SYSTEM_PROMPT || 'You are a helpful assistant.';
             // Log a warning about incomplete prefill
             console.warn("Edit modal prefill is incomplete due to missing full config API endpoint. Temperature and System Prompt reset to defaults.");
             addMessage(systemLogArea, `Warning: Editing agent ${agentId}. Temperature and System Prompt shown are defaults, not current values.`, "status");


         } else {
             throw new Error(`Agent config for ${agentId} not found in fetched list.`);
         }

        agentModal.style.display = 'block';
    } catch (error) {
        console.error(`Error preparing edit modal for ${agentId}:`, error);
        addMessage(systemLogArea, `Error loading data for agent ${agentId}: ${error.message}`, 'error');
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
}

async function handleSaveAgent(event) {
    event.preventDefault(); // Prevent default form submission
    const agentIdInput = document.getElementById('agent-id');
    const agentId = agentIdInput.value.trim();
    const editAgentId = editAgentIdField.value; // Get ID from hidden field for PUT requests
    const isEditing = !!editAgentId; // Check if we are editing

    // --- Collect Form Data ---
    // Use FormData for easier collection, then convert to the nested structure API expects
    const formData = new FormData(agentForm);
    const agentConfigData = {}; // Build the 'config' object
    for (const [key, value] of formData.entries()) {
        // Skip the hidden edit-agent-id and the top-level agent_id
        if (key === 'edit-agent-id' || key === 'agent_id') continue;
        // Handle numerical fields
        if (key === 'temperature') {
             const tempValue = parseFloat(value);
             // Add validation for temperature range
             if (isNaN(tempValue) || tempValue < 0 || tempValue > 2.0) {
                  addMessage(systemLogArea, "Invalid temperature value. Must be between 0.0 and 2.0.", "error");
                  return; // Stop submission
             }
             agentConfigData[key] = tempValue;
        } else if (value !== '' && value !== null) { // Only include non-empty values
            agentConfigData[key] = value;
        }
        // Add handling for 'extra_args' JSON parsing if that field exists
    }

    const finalAgentId = isEditing ? editAgentId : agentId;
    if (!finalAgentId) {
         addMessage(systemLogArea, "Agent ID is missing.", "error");
         return;
    }
     // Validate Agent ID format using the input's pattern attribute
     if (!isEditing && !agentIdInput.checkValidity()) {
          addMessage(systemLogArea, `Invalid Agent ID format: "${agentId}". ${agentIdInput.title}`, "error");
          return;
     }


    const apiEndpoint = isEditing ? `/api/config/agents/${finalAgentId}` : '/api/config/agents';
    const apiMethod = isEditing ? 'PUT' : 'POST';

    let requestBody;
    if (isEditing) {
        // PUT expects only the 'config' object
        requestBody = agentConfigData;
    } else {
        // POST expects {'agent_id': '...', 'config': {...}}
        requestBody = {
            agent_id: finalAgentId,
            config: agentConfigData
        };
    }


    console.log(`Saving Agent (${apiMethod}) - Endpoint: ${apiEndpoint}`);
    console.log("Request Body:", requestBody);

    try {
        const response = await fetch(apiEndpoint, {
            method: apiMethod,
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestBody),
        });

        const result = await response.json(); // Get JSON response body

        if (!response.ok) {
            // Use detail from response if available, otherwise use status text
            const errorDetail = result.detail || `HTTP ${response.status} ${response.statusText}`;
            throw new Error(`Failed to save agent: ${errorDetail}`);
        }

        addMessage(systemLogArea, result.message || `Agent ${isEditing ? 'updated' : 'added'} successfully. Restart required.`, 'status');
        closeModal('agent-modal');
        fetchAgentConfigurations(); // Refresh the configuration list in the UI

    } catch (error) {
        console.error("Error saving agent:", error);
        addMessage(systemLogArea, `Error saving agent: ${error.message}`, 'error');
        // Optionally, keep modal open on error? For now, it closes.
        // closeModal('agent-modal');
    }
}

async function handleDeleteAgent(agentId) {
    if (!confirm(`Are you sure you want to delete agent "${agentId}"? This requires an application restart.`)) {
        return;
    }

    console.log(`Deleting Agent - Endpoint: /api/config/agents/${agentId}`);

    try {
        const response = await fetch(`/api/config/agents/${agentId}`, {
            method: 'DELETE',
        });

        const result = await response.json(); // Get JSON response body

        if (!response.ok) {
            const errorDetail = result.detail || `HTTP ${response.status} ${response.statusText}`;
            throw new Error(`Failed to delete agent: ${errorDetail}`);
        }

        addMessage(systemLogArea, result.message || `Agent "${agentId}" deleted successfully. Restart required.`, 'status');
        fetchAgentConfigurations(); // Refresh the configuration list

    } catch (error) {
        console.error(`Error deleting agent ${agentId}:`, error);
        addMessage(systemLogArea, `Error deleting agent ${agentId}: ${error.message}`, 'error');
    }
}


// --- Event Listeners ---

// Send message on button click
sendButton.addEventListener('click', sendMessage);

// Send message on Enter key press in textarea (Shift+Enter for newline)
messageInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault(); // Prevent default Enter behavior (newline)
        sendMessage();
    }
});

// Trigger hidden file input click
attachFileButton.addEventListener('click', () => {
    fileInput.click();
});

// Handle file selection
fileInput.addEventListener('change', handleFileSelect);

// Phase 8: Add Agent button listener
addAgentButton.addEventListener('click', openAddAgentModal);

// Phase 8: Refresh button listener
refreshConfigButton.addEventListener('click', () => {
    addMessage(systemLogArea, 'Reloading page... Backend restart may be needed for config changes.', 'status');
    // Short delay allows message to render before reload potentially interrupts things
    setTimeout(() => window.location.reload(), 300);
});


// Phase 8: Form submission listener
agentForm.addEventListener('submit', handleSaveAgent);

// Phase 8: Close modal if clicking outside the content area (optional)
window.addEventListener('click', (event) => {
  if (event.target == agentModal) {
    closeModal('agent-modal');
  }
});


// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded.");
    clearSelectedFile(); // Ensure file info area is hidden initially
    closeModal('agent-modal'); // Ensure modal is hidden initially
    connectWebSocket(); // Establish WebSocket connection on load
});
