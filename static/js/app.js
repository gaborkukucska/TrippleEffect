// START OF FILE static/js/app.js

const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const configContent = document.getElementById('config-content');
const agentStatusContent = document.getElementById('agent-status-content');
const addAgentButton = document.getElementById('add-agent-button');
const agentModal = document.getElementById('agent-modal');
const agentForm = document.getElementById('agent-form');
const modalTitle = document.getElementById('modal-title');
const editAgentIdInput = document.getElementById('edit-agent-id');
const refreshConfigButton = document.getElementById('refresh-config-button'); // Added Refresh Button
// File attachment elements
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');
let attachedFile = null;
// Override Modal Elements
const overrideModal = document.getElementById('override-modal');
const overrideForm = document.getElementById('override-form');
const overrideAgentIdInput = document.getElementById('override-agent-id');
const overrideMessage = document.getElementById('override-message');
const overrideLastError = document.getElementById('override-last-error');
const overrideProviderSelect = document.getElementById('override-provider');
const overrideModelInput = document.getElementById('override-model');


let websocket = null; // Variable to hold the WebSocket connection

// --- WebSocket Connection ---
function connectWebSocket() {
    // Determine WebSocket protocol (ws or wss)
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

    console.log("Attempting to connect WebSocket:", wsUrl);
    websocket = new WebSocket(wsUrl);

    websocket.onopen = function(event) {
        console.log("WebSocket connection opened");
        addSystemLogMessage("Connected to backend.", "status");
        // Remove "connecting" message if it exists
        const connectingMsg = systemLogArea.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();
        // Fetch initial data on successful connection
        fetchAgentConfigurations();
    };

    websocket.onmessage = function(event) {
        //console.log("WebSocket message received:", event.data); // Log raw data
        try {
            const data = JSON.parse(event.data);
            //console.log("Parsed data:", data); // Log parsed data

            switch (data.type) {
                case 'status': // General status from backend/agents
                    if (data.agent_id) {
                        addAgentStatusMessage(data.agent_id, data.content);
                    } else {
                        addSystemLogMessage(data.message || data.content, 'status'); // Use message or content
                    }
                    break;
                case 'agent_status_update': // Specific structure for status updates
                     updateAgentStatusUI(data.agent_id, data.status);
                    break;
                case 'response_chunk': // LLM response stream
                    addAgentResponse(data.agent_id, data.content, true); // Append chunk
                    break;
                case 'final_response': // Complete LLM response (if streamed)
                    // Handled by appending chunks, potentially mark message as complete?
                    // We might not need specific handling if streaming works well.
                    // Mark the last message from this agent as complete (visually?)
                     markLastAgentMessageComplete(data.agent_id);
                    break;
                 case 'tool_requests': // Agent wants to use a tool (info only for UI)
                     addSystemLogMessage(`Agent ${data.agent_id || 'Unknown'} requesting tool(s): ${data.calls.map(c => c.name).join(', ')}`, 'status tool-execution');
                     // The manager handles execution, we just log the request.
                     // Update status if provided by agent core
                     updateAgentStatusIfChanged(data.agent_id, 'awaiting_tool_result');
                     break;
                case 'tool_result': // Result of tool execution (info only for UI)
                     addSystemLogMessage(`Tool result for ${data.agent_id || 'Unknown'} (Call ID: ${data.call_id}): ${data.content}`, 'status');
                      // Update status if provided by agent core
                     updateAgentStatusIfChanged(data.agent_id, 'processing');
                     break;
                case 'error': // Errors from backend/agents
                    addSystemLogMessage(`ERROR (${data.agent_id || 'System'}): ${data.content}`, 'error');
                     // Update status of specific agent if error is agent-related
                    if (data.agent_id) {
                         updateAgentStatusUI(data.agent_id, { status: 'error' }); // Simplified status update
                    }
                    break;
                 case 'agent_added': // Dynamic agent added
                     addSystemLogMessage(`Agent Added: ${data.config?.persona || data.agent_id}`, 'status');
                     fetchAgentConfigurations(); // Refresh config list
                     addOrUpdateAgentStatus(data.agent_id, { persona: data.config?.persona, status: 'idle', ...data.config, team: data.team });
                     break;
                 case 'agent_deleted': // Dynamic agent deleted
                     addSystemLogMessage(`Agent Deleted: ${data.agent_id}`, 'status');
                     fetchAgentConfigurations(); // Refresh config list
                     removeAgentStatus(data.agent_id);
                     break;
                case 'team_created':
                     addSystemLogMessage(`Team Created: ${data.team_id}`, 'status');
                     // Potentially update UI showing teams in future
                     break;
                 case 'team_deleted':
                     addSystemLogMessage(`Team Deleted: ${data.team_id}`, 'status');
                     // Potentially update UI showing teams in future
                     break;
                 case 'agent_moved_team':
                      addSystemLogMessage(`Agent ${data.agent_id} moved to team: ${data.new_team_id || 'None'} (from ${data.old_team_id || 'None'})`, 'status');
                      updateAgentStatusTeam(data.agent_id, data.new_team_id);
                      break;
                 case 'system_event': // General events like save/load
                      addSystemLogMessage(`System Event: ${data.event} - ${data.message || ''}`, 'status');
                      if (data.event === 'session_loaded') {
                         // Reload everything after session load
                         conversationArea.innerHTML = ''; // Clear conversation
                         addMessage('Conversation Area', 'Conversation cleared for loaded session.', 'status', conversationArea);
                         fetchAgentConfigurations();
                         agentStatusContent.innerHTML = '<span class="status-placeholder">Loading agent statuses...</span>'; // Reset status area
                         // Statuses should be pushed individually by backend after load
                      }
                      break;
                 // ****** NEW CASE: Handle Override Request ******
                 case 'request_user_override':
                     handleUserOverrideRequest(data);
                     break;
                 // ****** END NEW CASE ******
                default:
                    console.warn("Received unknown WebSocket message type:", data.type, data);
                    addSystemLogMessage(`Received unhandled message type: ${data.type}`, 'status');
            }
        } catch (error) {
            console.error("Error processing WebSocket message:", error);
            console.error("Original data:", event.data); // Log the raw data on error
             addSystemLogMessage(`Error processing message from backend: ${error}`, 'error');
        }
    };

    websocket.onerror = function(event) {
        console.error("WebSocket error observed:", event);
        addSystemLogMessage("WebSocket connection error.", 'error');
        // Indicate disconnected state
        const connectingMsg = systemLogArea.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.textContent = 'Connection failed.';
    };

    websocket.onclose = function(event) {
        console.log("WebSocket connection closed:", event.wasClean, "Code:", event.code, "Reason:", event.reason);
        addSystemLogMessage(`WebSocket connection closed. ${event.reason ? `Reason: ${event.reason}` : ''}`, 'error');
        websocket = null; // Reset websocket variable
        // Optionally try to reconnect after a delay
        // setTimeout(connectWebSocket, 5000); // Reconnect after 5 seconds
        addSystemLogMessage("Disconnected. Please refresh the page to reconnect.", 'error');
        // Disable input if disconnected
        messageInput.disabled = true;
        sendButton.disabled = true;
        attachFileButton.disabled = true;
    };
}

// --- Message Display Functions ---

function addMessage(targetAreaElement, text, type = 'status', agentId = null) {
    const messageElement = document.createElement('div');
    messageElement.classList.add('message', type);
    if (agentId) {
        messageElement.dataset.agentId = agentId; // Add agent ID for styling
    }

    // Basic Markdown-like formatting for bold and code
    let formattedText = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>'); // Bold
    formattedText = formattedText.replace(/`(.*?)`/g, '<code>$1</code>');     // Inline code
    // Basic handling for newlines within the text content
    formattedText = formattedText.replace(/\n/g, '<br>');

    messageElement.innerHTML = `<span>${formattedText}</span>`; // Wrap in span for potential styling

    // Remove initial placeholder if it exists
    const placeholder = targetAreaElement.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();

    targetAreaElement.appendChild(messageElement);
    targetAreaElement.scrollTop = targetAreaElement.scrollHeight; // Scroll to bottom
}

function addConversationMessage(text, isUser = true) {
     addMessage(conversationArea, text, isUser ? 'user' : 'agent_response', isUser ? 'user' : 'unknown_agent'); // Default agent ID if not specified
}

function addSystemLogMessage(text, type = 'status') { // Type can be 'status', 'error', 'tool-execution' etc.
    addMessage(systemLogArea, text, type);
}

function addAgentResponse(agentId, textChunk, isChunk = false) {
    // Try to find the last message from this agent
    let lastMessage = conversationArea.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);

    if (isChunk && lastMessage && !lastMessage.classList.contains('complete')) {
        // Append to the last message if it's a chunk and message isn't marked complete
        const span = lastMessage.querySelector('span');
        // Append text, converting newlines
        span.innerHTML += textChunk.replace(/\n/g, '<br>');
    } else {
        // Create a new message element for this agent
        addMessage(conversationArea, textChunk, 'agent_response', agentId);
        // Get the newly added message
        lastMessage = conversationArea.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);
        if (lastMessage) {
             // Optionally add a 'streaming' class while receiving chunks
             lastMessage.classList.add('streaming');
             lastMessage.classList.remove('complete'); // Ensure complete is removed if reusing element
        }
    }
     conversationArea.scrollTop = conversationArea.scrollHeight;
}

function markLastAgentMessageComplete(agentId) {
     const lastMessage = conversationArea.querySelector(`.message.agent_response[data-agent-id="${agentId}"]:last-child`);
     if (lastMessage) {
          lastMessage.classList.remove('streaming');
          lastMessage.classList.add('complete');
     }
}

function addAgentStatusMessage(agentId, text) {
    // Could add to system log or a dedicated agent log area
    addSystemLogMessage(`Status (@${agentId}): ${text}`, 'status');
}

// --- UI Update Functions ---

function updateAgentStatusIfChanged(agentId, newStatus) {
     const statusElement = document.getElementById(`status-${agentId}`);
     if (statusElement && !statusElement.classList.contains(`status-${newStatus}`)) {
         // If status changed significantly, update the whole block via updateAgentStatusUI
         // This is a simplified check; updateAgentStatusUI is more robust.
         // For now, just log the potential change.
         console.log(`Agent ${agentId} status potentially changed to ${newStatus}, full update recommended.`);
         // Ideally, fetch the full status dict and call updateAgentStatusUI
     }
}

// Function to update the Agent Status UI area
function updateAgentStatusUI(agentId, statusData) {
    // Ensure statusData is an object and has a status property
    if (typeof statusData !== 'object' || statusData === null || typeof statusData.status !== 'string') {
        console.warn(`Invalid statusData received for agent ${agentId}:`, statusData);
         // Optionally display placeholder or default status
         const statusText = 'unknown'; // Default text
         const statusClass = 'status-unknown'; // Default class
         const agentItem = addOrUpdateAgentStatus(agentId, { status: statusText });
         if (agentItem) {
             const statusSpan = agentItem.querySelector('.agent-status');
             if(statusSpan) {
                statusSpan.textContent = statusText;
                // Reset classes and apply the default one
                statusSpan.className = 'agent-status'; // Reset classes
                statusSpan.classList.add(statusClass);
             }
             // Update overall item class
             agentItem.className = 'agent-status-item'; // Reset classes
             agentItem.classList.add(statusClass);
         }
        return;
    }

    const statusText = statusData.status.toLowerCase().replace(/\s+/g, '_'); // e.g., "executing_tool"
    const statusClass = `status-${statusText}`; // e.g., "status-executing_tool"

    // Pass the whole statusData to potentially display more info
    const agentItem = addOrUpdateAgentStatus(agentId, statusData);

    if (agentItem) {
        const statusSpan = agentItem.querySelector('.agent-status');
        if (statusSpan) {
            statusSpan.textContent = statusData.status; // Display original status text

            // Update status class for styling
            statusSpan.className = 'agent-status'; // Reset classes first
            statusSpan.classList.add(statusClass);
        }
         // Update overall item class
         agentItem.className = 'agent-status-item'; // Reset classes first
         agentItem.classList.add(statusClass);

        // Update tool info if executing tool
        const toolInfoSpan = agentItem.querySelector('.tool-info');
        if (statusData.status === 'executing_tool' && statusData.current_tool) {
            if (toolInfoSpan) {
                toolInfoSpan.textContent = ` [${statusData.current_tool.name}]`;
            } else {
                const newToolInfoSpan = document.createElement('span');
                newToolInfoSpan.className = 'tool-info';
                newToolInfoSpan.textContent = ` [${statusData.current_tool.name}]`;
                statusSpan.insertAdjacentElement('afterend', newToolInfoSpan);
            }
        } else if (toolInfoSpan) {
            toolInfoSpan.remove(); // Remove tool info if not executing
        }

         // Update team info
         const teamSpan = agentItem.querySelector('.team-info');
         const teamText = statusData.team ? ` (Team: ${statusData.team})` : '';
         if (teamSpan) {
             teamSpan.textContent = teamText;
         } else if (teamText) {
             const newTeamSpan = document.createElement('span');
             newTeamSpan.className = 'team-info agent-model'; // Use similar styling to model
             newTeamSpan.textContent = teamText;
             // Insert after model span if it exists, otherwise after persona
             const modelSpan = agentItem.querySelector('.agent-model');
             if (modelSpan) {
                  modelSpan.insertAdjacentElement('afterend', newTeamSpan);
             } else {
                  const personaSpan = agentItem.querySelector('strong');
                  if (personaSpan) personaSpan.insertAdjacentElement('afterend', newTeamSpan);
             }
         }
    }
}

// Helper to add or update an agent's status entry
function addOrUpdateAgentStatus(agentId, statusData) {
    let agentItem = document.getElementById(`status-${agentId}`);
    const persona = statusData.persona || agentId; // Use persona or agentId
    const model = statusData.model || 'N/A'; // Show model
    const provider = statusData.provider || 'N/A'; // Show provider
    const team = statusData.team || null; // Team ID

    if (!agentItem) {
        // Remove placeholder if it exists
        const placeholder = agentStatusContent.querySelector('.status-placeholder');
        if (placeholder) placeholder.remove();

        agentItem = document.createElement('div');
        agentItem.id = `status-${agentId}`;
        agentItem.classList.add('agent-status-item');
        agentStatusContent.appendChild(agentItem);

        // Initial structure
        agentItem.innerHTML = `
            <strong>${persona}</strong>
            <span class="agent-model">(${provider}/${model})</span>
            ${team ? `<span class="team-info agent-model">(Team: ${team})</span>` : ''}
            <span class="agent-status">unknown</span>
            <span class="tool-info" style="display: none;"></span>
        `;
    } else {
        // Update potentially changed info like persona, model, provider if needed
        agentItem.querySelector('strong').textContent = persona;
        const modelSpan = agentItem.querySelector('.agent-model');
        if (modelSpan) modelSpan.textContent = `(${provider}/${model})`;
    }

    // Return the element for further updates (like status class)
    return agentItem;
}

// Helper to remove an agent's status entry
function removeAgentStatus(agentId) {
     const agentItem = document.getElementById(`status-${agentId}`);
     if (agentItem) {
         agentItem.remove();
     }
     // Add placeholder back if empty
     if (!agentStatusContent.hasChildNodes()) {
         agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents.</span>';
     }
}

// Helper to update only the team info in the status UI
function updateAgentStatusTeam(agentId, newTeamId) {
     const agentItem = document.getElementById(`status-${agentId}`);
     if (!agentItem) return;

     let teamSpan = agentItem.querySelector('.team-info');
     const teamText = newTeamId ? ` (Team: ${newTeamId})` : '';

     if (teamSpan) {
         teamSpan.textContent = teamText;
         if (!newTeamId) teamSpan.remove(); // Remove span if no longer in team
     } else if (newTeamId) {
         // Create and insert if it didn't exist
         const newTeamSpan = document.createElement('span');
         newTeamSpan.className = 'team-info agent-model';
         newTeamSpan.textContent = teamText;
         const modelSpan = agentItem.querySelector('.agent-model');
         if (modelSpan) modelSpan.insertAdjacentElement('afterend', newTeamSpan);
         else {
             const personaSpan = agentItem.querySelector('strong');
             if (personaSpan) personaSpan.insertAdjacentElement('afterend', newTeamSpan);
         }
     }
}


// --- Agent Config CRUD ---

// Fetch and display agent configurations
async function fetchAgentConfigurations() {
    console.log("Fetching agent configurations...");
    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const agents = await response.json();
        displayAgentConfigurations(agents);
    } catch (error) {
        console.error('Error fetching agent configurations:', error);
        configContent.innerHTML = '<span class="status-placeholder">Error loading config.</span>';
         addSystemLogMessage(`Error loading config: ${error}`, 'error');
    }
}

// Display agent configurations in the UI
function displayAgentConfigurations(agents) {
    configContent.innerHTML = ''; // Clear previous content
    if (agents.length === 0) {
        configContent.innerHTML = '<span class="status-placeholder">No agents configured.</span>';
        return;
    }
    agents.forEach(agent => {
        const div = document.createElement('div');
        div.classList.add('config-item');
        div.innerHTML = `
            <span>
                <strong>${agent.persona || agent.agent_id}</strong>
                <span class="agent-details">(${agent.agent_id} - ${agent.provider}/${agent.model})</span>
            </span>
            <div class="config-item-actions">
                <button class="config-action-button edit-button" data-agent-id="${agent.agent_id}" title="Edit Agent">✏️</button>
                <button class="config-action-button delete-button" data-agent-id="${agent.agent_id}" title="Delete Agent">🗑️</button>
            </div>
        `;
        configContent.appendChild(div);

        // Add event listeners for edit/delete buttons
        div.querySelector('.edit-button').addEventListener('click', handleEditAgent);
        div.querySelector('.delete-button').addEventListener('click', handleDeleteAgent);
    });
}

// Handle Add Agent button click
addAgentButton.addEventListener('click', () => {
    modalTitle.textContent = 'Add New Agent';
    agentForm.reset(); // Clear form
    editAgentIdInput.value = ''; // Ensure hidden ID is empty
     // Make agent ID editable for adding
    document.getElementById('agent-id').disabled = false;
    document.getElementById('agent-id').readOnly = false;
    // Set default values maybe?
     document.getElementById('provider').value = 'openrouter';
     document.getElementById('model').value = 'google/gemini-flash-1.5'; // Update default?
     document.getElementById('temperature').value = 0.7;
    openModal('agent-modal');
});

// Handle Edit Agent button click
async function handleEditAgent(event) {
    const agentId = event.target.closest('button').dataset.agentId;
    console.log("Editing agent:", agentId);

    // Fetch current full config to populate form
     try {
        const response = await fetch('/api/config/agents'); // Re-fetch list
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const agents = await response.json();
        const agentData = agents.find(a => a.agent_id === agentId);

        // Need more details than the list provides - ideally a GET /api/config/agents/{id}
        // Workaround: We'll only populate basic fields for now. A dedicated endpoint is better.
        // Let's assume we need the *full* config from the manager's internal state via a new endpoint,
        // or just work with what we have. For now, limited edit:
        if (agentData) {
             modalTitle.textContent = `Edit Agent: ${agentId}`;
             agentForm.reset(); // Clear previous
             editAgentIdInput.value = agentId; // Set hidden ID

            // Populate known fields
            document.getElementById('agent-id').value = agentId;
            document.getElementById('agent-id').disabled = true; // Disable editing ID
            document.getElementById('agent-id').readOnly = true;
            document.getElementById('persona').value = agentData.persona || '';
            document.getElementById('provider').value = agentData.provider || 'openrouter'; // Default if missing
            document.getElementById('model').value = agentData.model || '';
            // We don't have temp/prompt from the basic list endpoint
            document.getElementById('temperature').value = 0.7; // Default
            document.getElementById('system_prompt').value = ''; // Default (needs full config)

             openModal('agent-modal');
        } else {
            addSystemLogMessage(`Error: Could not find details for agent ${agentId} to edit. Config might be out of sync.`, 'error');
        }
    } catch (error) {
        console.error('Error preparing agent edit:', error);
        addSystemLogMessage(`Error fetching agent details for edit: ${error}`, 'error');
    }


}

// Handle Delete Agent button click
async function handleDeleteAgent(event) {
    const agentId = event.target.closest('button').dataset.agentId;
    if (confirm(`Are you sure you want to delete agent configuration '${agentId}'? Restart required.`)) {
        console.log("Deleting agent config:", agentId);
        try {
            const response = await fetch(`/api/config/agents/${agentId}`, {
                method: 'DELETE',
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP error! status: ${response.status}`);
            }
            addSystemLogMessage(result.message, 'status');
            fetchAgentConfigurations(); // Refresh list
        } catch (error) {
            console.error('Error deleting agent configuration:', error);
            addSystemLogMessage(`Error deleting agent ${agentId}: ${error}`, 'error');
        }
    }
}

// Handle Agent Form submission (Add or Edit)
agentForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData(agentForm);
    const agentIdValue = formData.get('agent_id'); // Get ID from form data
    const isEditing = !!editAgentIdInput.value; // Check hidden field

    const agentConfigData = {};
    // Extract only config fields, handle type conversion
    agentConfigData['provider'] = formData.get('provider');
    agentConfigData['model'] = formData.get('model');
    agentConfigData['persona'] = formData.get('persona');
    agentConfigData['system_prompt'] = formData.get('system_prompt');
    const tempValue = formData.get('temperature');
    if (tempValue) agentConfigData['temperature'] = parseFloat(tempValue);

    // --- Construct payload ---
     let payload;
     let url;
     let method;

     if (isEditing) {
         payload = agentConfigData; // PUT payload is just the config part
         url = `/api/config/agents/${editAgentIdInput.value}`;
         method = 'PUT';
         console.log("Submitting EDIT:", url, payload);
     } else {
          payload = { // POST payload includes agent_id and nested config
               agent_id: agentIdValue,
               config: agentConfigData
          };
         url = '/api/config/agents';
         method = 'POST';
         console.log("Submitting ADD:", url, payload);
     }


    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (!response.ok) {
             // Try to get detail, fallback to status text
             const errorDetail = result.detail || response.statusText || `HTTP ${response.status}`;
             throw new Error(errorDetail);
        }

        addSystemLogMessage(result.message, 'status');
        closeModal('agent-modal');
        fetchAgentConfigurations(); // Refresh the list in the UI
    } catch (error) {
        console.error(`Error ${isEditing ? 'updating' : 'adding'} agent:`, error);
        addSystemLogMessage(`Error ${isEditing ? 'updating' : 'adding'} agent: ${error}`, 'error');
        // Optionally show error within the modal
    }
});

// --- Modal Helper Functions ---
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'block';
    }
}

function closeModal(modalId) {
     const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
}

// Close modals if clicked outside the content area
window.onclick = function(event) {
    if (event.target.classList.contains('modal')) {
        closeModal(event.target.id);
    }
}


// --- Refresh Button ---
refreshConfigButton.addEventListener('click', () => {
    addSystemLogMessage("Manual refresh requested. Reloading page...", "status");
    // Add a small delay before reloading to allow the message to be seen/sent
    setTimeout(() => {
        window.location.reload();
    }, 300);
});

// --- File Attachment ---
attachFileButton.addEventListener('click', () => {
    fileInput.click(); // Trigger hidden file input
});

fileInput.addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (file) {
        // Basic validation (optional - check type again, size)
        if (file.size > 1024 * 1024) { // Limit to 1MB for example
             addSystemLogMessage(`File "${file.name}" is too large (max 1MB).`, 'error');
             clearAttachedFile();
             return;
        }

        console.log("File attached:", file.name, file.type, file.size);
        attachedFile = file;
        displayFileInfo(file.name);
    }
});

function displayFileInfo(filename) {
    fileInfoArea.innerHTML = `
        <span>📎 ${filename}</span>
        <button id="clear-file-button" title="Remove file">×</button>
    `;
    // Add event listener to the clear button
    document.getElementById('clear-file-button').addEventListener('click', clearAttachedFile);
}

function clearAttachedFile() {
    attachedFile = null;
    fileInput.value = ''; // Reset file input
    fileInfoArea.innerHTML = ''; // Clear display area
    console.log("Attached file cleared.");
}

// --- Send Message Function ---
function sendMessage() {
    const message = messageInput.value.trim();

    if (!message && !attachedFile) {
        console.log("No message or file to send.");
        return; // Don't send empty messages unless a file is attached
    }
    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
        addSystemLogMessage("WebSocket not connected. Cannot send message.", 'error');
        return;
    }

    let messageToSend = message || `Attached file: ${attachedFile?.name || 'Unknown'}`; // Use message or filename

    // Add user message to conversation area immediately
    addConversationMessage(messageToSend, true);

    // Check for file attachment
    if (attachedFile) {
        const reader = new FileReader();
        reader.onload = function(e) {
            const fileContent = e.target.result;
             // Construct message object for file + text
            const payload = {
                type: "user_message_with_file",
                text: message, // Send original text message too
                file_content: fileContent,
                filename: attachedFile.name
            };
            console.log("Sending message with file:", payload.filename);
            websocket.send(JSON.stringify(payload));
            clearAttachedFile(); // Clear after sending attempt
             messageInput.value = ''; // Clear text input after sending
        };
        reader.onerror = function(e) {
            console.error("Error reading file:", e);
            addSystemLogMessage(`Error reading file ${attachedFile.name}`, 'error');
            clearAttachedFile();
        };
        reader.readAsText(attachedFile); // Read file as text
    } else {
        // Send only text message
        console.log("Sending text message:", messageToSend);
        websocket.send(messageToSend); // Send raw text for now
         messageInput.value = ''; // Clear input after sending
    }

     messageInput.focus(); // Keep focus on input
     // Disable send button temporarily? Maybe not needed if backend handles concurrency.
}


// --- Event Listeners ---
sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', function(event) {
    // Send message on Enter key press, unless Shift is held
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault(); // Prevent default Enter behavior (newline)
        sendMessage();
    }
});

// --- ****** NEW: Override Modal Logic ****** ---

// Handle the request from backend
function handleUserOverrideRequest(data) {
    console.log("Handling user override request:", data);
    overrideAgentIdInput.value = data.agent_id || '';
    overrideMessage.textContent = data.message || `Agent '${data.persona || data.agent_id}' needs assistance.`;
    overrideLastError.textContent = data.last_error || 'No error details provided.';

    // Pre-fill with current values
    overrideProviderSelect.value = data.current_provider || 'openrouter';
    overrideModelInput.value = data.current_model || '';
    overrideModelInput.placeholder = `Current: ${data.current_model || 'N/A'}. Enter new model.`;

    // TODO: Dynamically fetch and populate provider/model options? (Deferred)

    openModal('override-modal');
}

// Handle submission of the override form
overrideForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const agentId = overrideAgentIdInput.value;
    const newProvider = overrideProviderSelect.value;
    const newModel = overrideModelInput.value.trim();

    if (!agentId || !newProvider || !newModel) {
        alert("Please ensure Agent ID, Provider, and Model are provided.");
        return;
    }

    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
        addSystemLogMessage("WebSocket not connected. Cannot submit override.", 'error');
        return;
    }

    const overridePayload = {
        type: "submit_user_override", // Define a new type for this message
        agent_id: agentId,
        new_provider: newProvider,
        new_model: newModel
        // Add other override fields if implemented
    };

    console.log("Sending user override:", overridePayload);
    websocket.send(JSON.stringify(overridePayload));

    addSystemLogMessage(`Submitted override for agent ${agentId} (Provider: ${newProvider}, Model: ${newModel}). Waiting for manager...`, 'status');
    closeModal('override-modal');
});

// --- Update Status Display for New Status ---
// Add the new status to the style mapping in updateAgentStatusUI
// (Modify the updateAgentStatusUI function - already done above by adding statusClass logic)
// Let's ensure the CSS has a style for it too.

// Add CSS rule for 'awaiting_user_override' in style.css (manual step - will remind user)


// --- Initial Setup ---
document.addEventListener('DOMContentLoaded', () => {
    // Add placeholder messages
    addMessage(conversationArea, 'Start by entering your task below.', 'status', null);
    // addMessage(systemLogArea, 'Connecting...', 'status', null); // Replaced by logic in connectWebSocket

    connectWebSocket(); // Establish WebSocket connection on load
    fetchAgentConfigurations(); // Fetch initial agent config list

     // Remove placeholders if content already loaded by WS connection opening
     setTimeout(() => {
        const convPlaceholder = conversationArea.querySelector('.initial-placeholder');
        if (conversationArea.children.length > 1 && convPlaceholder) convPlaceholder.remove();
        const sysPlaceholder = systemLogArea.querySelector('.initial-placeholder');
         // Keep connecting message until connection opens/fails
        // if (systemLogArea.children.length > 1 && sysPlaceholder) sysPlaceholder.remove();
        const statusPlaceholder = agentStatusContent.querySelector('.status-placeholder');
        if(agentStatusContent.children.length > 1 && statusPlaceholder) statusPlaceholder.remove();
     }, 1500); // Remove placeholders after a short delay if real content exists
});
