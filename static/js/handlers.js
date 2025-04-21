// START OF FILE static/js/handlers.js

import * as ui from './ui.js';
import * as api from './api.js';
import * as ws from './websocket.js';
import * as state from './state.js';
import * as DOM from './domElements.js';
import * as session from './session.js'; // Import session functions
import * as configView from './configView.js'; // Import config view functions
import { escapeHTML } from './utils.js';

/**
 * Handles incoming WebSocket messages and routes them to appropriate UI handlers.
 * @param {object} data The parsed message data from the WebSocket.
 */
export const handleWebSocketMessage = (data) => {
    console.log("Handler: Processing WebSocket message", data);
    try {
        const messageType = data.type;
        const agentId = data.agent_id || 'system';
        const agentPersona = data.persona;

        console.log(`Handler: Message type: ${messageType}, Agent: ${agentId}`);

        // Remove initial connecting message if it exists
        const connectingMsg = DOM.internalCommsArea?.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();

        // --- Routing Logic ---
        let targetArea = 'internal-comms-area'; // Default target
        let displayPersona = agentPersona;
        let displayAgentId = agentId;
        let displayContent = data.content || data.message || JSON.stringify(data);

        switch (messageType) {
            case 'agent_response':
                if (agentId === 'admin_ai') {
                    targetArea = 'conversation-area';
                    // Content might be HTML/XML, pass directly (UI function should handle)
                    displayContent = data.content;
                    console.log(`Handler: Routing admin_ai response to ${targetArea}`);
                } else {
                    targetArea = 'internal-comms-area';
                    // Display internal agent responses in the internal comms view
                    // Escape content from non-admin agents just in case
                    displayContent = escapeHTML(data.content);
                    console.log(`Handler: Routing agent ${agentId} response to ${targetArea}`);
                }
                break;

            // User messages displayed locally on send, not via WebSocket echo

            case 'status':
            case 'system_event':
            case 'log':
                 targetArea = 'internal-comms-area';
                 displayContent = escapeHTML(displayContent);
                 displayAgentId = agentId || 'system';
                 console.log(`Handler: Routing ${messageType} to ${targetArea}`);
                 break;

            case 'error':
                targetArea = 'internal-comms-area';
                displayContent = `❗ Error: ${escapeHTML(data.content || 'Unknown error')}`;
                displayAgentId = agentId || 'system';
                console.log(`Handler: Routing error to ${targetArea}`);
                break;

            case 'agent_status_update':
                console.log(`Handler: Handling agent_status_update for ${agentId}`);
                if (data.status && typeof data.status === 'object') {
                    const statusPayload = { ...data.status };
                    if (!statusPayload.agent_id && agentId) statusPayload.agent_id = agentId;
                    // Assume backend sends the *full* status object needed by the UI function
                    const singleAgentUpdate = { [agentId]: statusPayload };
                    ui.updateAgentStatusUI(singleAgentUpdate); // Call UI function
                } else {
                    console.warn("Handler: Received agent_status_update without valid status object:", data);
                }
                return; // No message display needed for this type

             case 'full_status': // Handle receiving the full agent status list
                 console.log("Handler: Handling full_status update");
                 if (data.agents && typeof data.agents === 'object') {
                     ui.updateAgentStatusUI(data.agents); // Update UI with the complete list
                 } else {
                     console.warn("Handler: Received full_status without valid agents object:", data);
                 }
                 return; // No message display needed


            case 'agent_added':
            case 'agent_deleted':
            case 'team_created':
            case 'team_deleted':
            case 'session_saved':
            case 'session_loaded':
                 targetArea = 'internal-comms-area';
                 const eventMap = {
                    'agent_added': `Agent Added: ${data.agent_id} (${data.config?.persona || 'N/A'})`,
                    'agent_deleted': `Agent Deleted: ${data.agent_id}`,
                    'team_created': `Team Created: ${data.team_id}`,
                    'team_deleted': `Team Deleted: ${data.team_id}`,
                    'session_saved': `Session Saved: ${data.project}/${data.session}`,
                    'session_loaded': `Session Loaded: ${data.project}/${data.session}. UI Refreshing...`, // Added refresh note
                 };
                 displayContent = escapeHTML(eventMap[messageType] || data.message || `Event: ${messageType}`);
                 displayAgentId = 'system';
                 console.log(`Handler: Routing ${messageType} event to ${targetArea}`);
                 // If session loaded, refresh necessary parts of UI
                 if (messageType === 'session_loaded') {
                      // Clear conversation area (new session loaded)
                      if (DOM.conversationArea) DOM.conversationArea.innerHTML = '';
                      // Request full status to update agent list
                      ws.sendMessage(JSON.stringify({ type: 'get_full_status' })); // Assume backend handles this
                 }
                 break;

            default:
                console.warn(`Handler: Received unknown message type: ${messageType}`, data);
                targetArea = 'internal-comms-area';
                displayContent = `Unknown msg type '${escapeHTML(messageType)}': ${escapeHTML(JSON.stringify(data))}`;
                displayAgentId = 'system';
        }

        // Display the message in the determined target area
        if (targetArea && displayContent !== undefined) {
             console.log(`Handler: Final display call: target=${targetArea}, type=${messageType}, agentId=${displayAgentId}`);
             ui.displayMessage(displayContent, messageType, targetArea, displayAgentId, displayPersona);
        } else {
             console.error("Handler: Message handling resulted in no targetArea or displayContent", data);
        }

    } catch (error) {
        console.error("Error in handleWebSocketMessage:", error);
        ui.displayMessage(`!! JS Error handling WebSocket message: ${escapeHTML(error.message)} !!`, 'error', 'internal-comms-area', 'frontend');
    }
};

// --- UI Event Handlers ---

export const handleSendMessage = () => {
    console.log("Handler: Send button clicked or Enter pressed.");
    const message = DOM.messageInput?.value?.trim();
    const currentAttachedFile = state.getAttachedFile(); // Get from state

    if (message || currentAttachedFile) {
        if (currentAttachedFile) {
            // Send structured message with file content
            const messageData = {
                type: 'user_message_with_file',
                text: message,
                filename: currentAttachedFile.name,
                file_content: currentAttachedFile.content
            };
             ws.sendMessage(JSON.stringify(messageData));
             // Display user message *locally* in chat area
             ui.displayMessage(escapeHTML(message) + `<br><small><i>[Attached: ${escapeHTML(currentAttachedFile.name)}]</i></small>`, 'user', 'conversation-area');
             handleClearAttachment(); // Clear file after sending
        } else {
            // Send plain text message
            ws.sendMessage(message);
            // Display user message *locally* in chat area
            ui.displayMessage(escapeHTML(message), 'user', 'conversation-area');
        }
        if (DOM.messageInput) {
            DOM.messageInput.value = '';
            DOM.messageInput.style.height = '60px'; // Reset height
            DOM.messageInput.focus(); // Keep focus on input
        }
    } else {
        console.log("Handler: Send ignored, no message or file.");
    }
};

export const handleMessageInputKeypress = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault(); // Prevent default newline
        console.log("Handler: Enter pressed in message input.");
        handleSendMessage(); // Trigger send logic
    }
};

export const handleMessageInput = () => {
    if (DOM.messageInput) {
        DOM.messageInput.style.height = 'auto'; // Reset height
        const newHeight = Math.min(Math.max(DOM.messageInput.scrollHeight, 50), 150); // Clamp height between 50 and 150
        DOM.messageInput.style.height = newHeight + 'px';
    }
};

export const handleNavButtonClick = (event) => {
    const viewId = event.currentTarget.getAttribute('data-view');
    console.log(`Handler: Nav button clicked for view: ${viewId}`);
    if (viewId) {
        ui.switchView(viewId); // Call UI function
    }
};

export const handleFileSelect = (event) => {
    const file = event.target.files[0];
    if (file) {
        console.log(`Handler: File selected: ${file.name}, Size: ${file.size}, Type: ${file.type}`);
        // Basic validation
        if (!file.type.startsWith('text/') && !/\.(py|js|json|yaml|yml|md|log|csv|html|css|xml|txt)$/i.test(file.name)) {
             alert('Error: Only text-based files are currently supported.');
             if (DOM.fileInput) DOM.fileInput.value = '';
             return;
         }
         const maxSize = 5 * 1024 * 1024; // 5MB limit
         if (file.size > maxSize) {
             alert(`Error: File size exceeds the limit of ${maxSize / 1024 / 1024}MB.`);
             if (DOM.fileInput) DOM.fileInput.value = '';
             return;
         }

        const reader = new FileReader();
        reader.onload = (e) => {
            state.setAttachedFile({ // Update state
                name: file.name,
                content: e.target.result
            });
            ui.displayFileInfo(state.getAttachedFile()); // Update UI
        };
        reader.onerror = (e) => {
            console.error("Handler: File reading error:", e);
            alert("Error reading file.");
            handleClearAttachment();
        };
        reader.readAsText(file);
    }
    // Reset the input value so the 'change' event fires even if the same file is selected again
    event.target.value = null;
};

export const handleClearAttachment = () => {
    console.log("Handler: Clearing attachment.");
    state.setAttachedFile(null); // Update state
    if (DOM.fileInput) DOM.fileInput.value = ''; // Clear the actual file input
    ui.displayFileInfo(null); // Update UI
};

// --- Config View Handlers ---
export const handleRefreshConfig = () => {
    console.log("Handler: Refresh config button clicked.");
    configView.loadStaticAgentConfig();
};

export const handleAddAgentClick = () => {
    console.log("Handler: Add agent button clicked.");
    configView.openAgentModal(null); // Open modal for adding
};

export const handleAgentFormSubmit = async (event) => {
    event.preventDefault();
    console.log("Handler: Agent form submitted.");
    if (!DOM.agentForm) return;

    const formData = new FormData(DOM.agentForm);
    const agentId = formData.get('agent_id');
    const isEditing = !!DOM.editAgentIdInput?.value;

    // Basic validation (redundant with HTML5 required, but safe)
    if (!agentId || !formData.get('provider') || !formData.get('model')) {
        alert("Please fill in Agent ID, Provider, and Model.");
        return;
    }

    // Basic pattern check again just in case
    if (!/^[a-zA-Z0-9_-]+$/.test(agentId)) {
         alert("Agent ID can only contain alphanumeric characters, underscores, and hyphens.");
         return;
    }

    const extraArgs = {}; // Placeholder for future extra args collection

    const agentConfigData = {
        provider: formData.get('provider'),
        model: formData.get('model'),
        system_prompt: formData.get('system_prompt') || '', // Ensure string
        temperature: parseFloat(formData.get('temperature')) || 0.7, // Default if parsing fails
        persona: formData.get('persona') || 'Assistant Agent', // Default persona
        ...extraArgs
    };

    const payload = {
        agent_id: agentId,
        config: agentConfigData
    };

    const endpoint = isEditing ? `/api/config/agents/${agentId}` : '/api/config/agents';
    const method = isEditing ? 'PUT' : 'POST';
    const actionVerb = isEditing ? 'update' : 'create';

    console.log(`Handler: Attempting to ${actionVerb} agent ${agentId}...`, payload);

    try {
        // Send only config data for PUT
        const dataToSend = isEditing ? agentConfigData : payload;
        const result = await api.makeApiCall(endpoint, method, dataToSend);
        console.log(`Handler: Agent ${actionVerb} successful.`, result);
        ui.displayMessage(escapeHTML(result.message), 'system_event', 'internal-comms-area', 'system');
        ui.closeModal('agent-modal');
        configView.loadStaticAgentConfig(); // Refresh list
    } catch (error) {
        console.error(`Handler: Error ${actionVerb} agent config:`, error);
        // Error should be displayed by makeApiCall, but we can alert too
        alert(`Error ${actionVerb} agent config: ${error.responseBody?.detail || error.message || 'Unknown error'}`);
    }
};


export const handleDeleteAgentConfig = async (agentId) => {
    console.log(`Handler: Delete config requested for agent: ${agentId}`);
    if (!confirm(`Are you sure you want to delete the static configuration for agent '${agentId}'? This requires an application restart.`)) {
        return;
    }
    try {
        const result = await api.makeApiCall(`/api/config/agents/${agentId}`, 'DELETE');
        console.log(`Handler: Agent delete successful.`, result);
        ui.displayMessage(escapeHTML(result.message), 'system_event', 'internal-comms-area', 'system');
        configView.loadStaticAgentConfig(); // Refresh list
    } catch (error) {
        // Error message displayed by makeApiCall
        console.error(`Handler: Error deleting agent config for ${agentId}:`, error);
    }
};

// --- Session Management Handlers ---
export const handleProjectSelectionChange = () => {
    if (DOM.projectSelect) {
        const selectedProject = DOM.projectSelect.value;
        console.log(`Handler: Project selection changed to: ${selectedProject}`);
        session.loadSessions(selectedProject); // Call session module function
    }
};

export const handleLoadSession = async () => {
    const projectName = DOM.projectSelect?.value;
    const sessionName = DOM.sessionSelect?.value;
    console.log(`Handler: Load session clicked. Project: ${projectName}, Session: ${sessionName}`);

    if (!projectName || !sessionName) {
        ui.displaySessionStatus("Error: Please select both a project and a session.", false);
        return;
    }
    if (DOM.loadSessionButton) {
        DOM.loadSessionButton.disabled = true;
        DOM.loadSessionButton.textContent = "Loading...";
    }

    try {
        const result = await api.makeApiCall(`/api/projects/${projectName}/sessions/${sessionName}/load`, 'POST');
        console.log("Handler: Session load successful.", result);
        ui.displaySessionStatus(result.message, true);
        // UI update (clearing chat, refreshing status) should be triggered by session_loaded event from backend
        ui.switchView('chat-view'); // Switch view after successful load
    } catch (error) {
        console.error("Handler: Error loading session:", error);
        ui.displaySessionStatus(`Error loading session: ${error.responseBody?.detail || error.message || 'Unknown error'}`, false);
    } finally {
         if (DOM.loadSessionButton) {
             DOM.loadSessionButton.disabled = false;
             DOM.loadSessionButton.textContent = "Load Selected Session";
         }
    }
};

export const handleSaveSession = async () => {
    const projectName = DOM.saveProjectNameInput?.value?.trim();
    const sessionName = DOM.saveSessionNameInput?.value?.trim() || null;
    console.log(`Handler: Save session clicked. Project: ${projectName}, Session: ${sessionName || '(auto)'}`);

    if (!projectName) {
        ui.displaySessionStatus("Error: Project name is required to save.", false);
        return;
    }
    if (DOM.saveSessionButton) {
        DOM.saveSessionButton.disabled = true;
        DOM.saveSessionButton.textContent = "Saving...";
    }

    try {
        const payload = sessionName ? { session_name: sessionName } : {};
        const result = await api.makeApiCall(`/api/projects/${projectName}/sessions`, 'POST', payload);
        console.log("Handler: Session save successful.", result);
        ui.displaySessionStatus(result.message, true);
        // Refresh project list to show new project/session if created
        session.loadProjects();
        if (DOM.saveProjectNameInput) DOM.saveProjectNameInput.value = '';
        if (DOM.saveSessionNameInput) DOM.saveSessionNameInput.value = '';
    } catch (error) {
         console.error("Handler: Error saving session:", error);
         ui.displaySessionStatus(`Error saving session: ${error.responseBody?.detail || error.message || 'Unknown error'}`, false);
    } finally {
        if (DOM.saveSessionButton) {
            DOM.saveSessionButton.disabled = false;
            DOM.saveSessionButton.textContent = "Save Current Session";
        }
    }
};


console.log("Frontend handlers module loaded.");
