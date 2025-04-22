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
 * Handles incoming WebSocket messages and routes them to appropriate UI display areas.
 * Routes User<->Admin messages to #conversation-area.
 * Routes internal comms, status, tools, errors etc. to #internal-comms-area.
 * @param {object} data The parsed message data from the WebSocket.
 */
export const handleWebSocketMessage = (data) => {
    console.log("Handler: Processing WebSocket message", data);
    try {
        const messageType = data.type;
        const agentId = data.agent_id || 'system'; // Default to 'system' if no agent_id
        const agentPersona = data.persona; // Persona might be included for context

        console.log(`Handler: Message type: ${messageType}, Agent: ${agentId}`);

        // Remove initial connecting message from internal comms if it exists
        const connectingMsg = DOM.internalCommsArea?.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();

        // --- Routing Logic: Determine Target Area ---
        let targetAreaId = 'internal-comms-area'; // Default to internal comms
        let displayType = messageType; // Use original type for class, might adjust later

        if (messageType === 'agent_response' && agentId === 'admin_ai') {
            // Admin AI responses directed to the user go to the main chat area
            targetAreaId = 'conversation-area';
            // We can keep the 'agent_response' class or use a more specific one if needed
        } else if (messageType === 'user_message') {
            // If backend ever echoes user messages, route them to chat area
            // NOTE: Currently, user messages are displayed locally in handleSendMessage
            targetAreaId = 'conversation-area';
            displayType = 'user'; // Use 'user' class for styling
        }
        // All other types default to internal-comms-area (status, errors, system events,
        // internal agent responses, tool calls/results if displayed, etc.)

        console.log(`Handler: Routing message to targetAreaId: ${targetAreaId}`);

        // --- Prepare Content for Display ---
        let displayContent = data.content; // Start with raw content
        let displayAgentId = agentId;
        let displayPersonaForUI = agentPersona;

        // Format specific message types for better readability
        switch (messageType) {
            case 'agent_response':
                 // Content is expected to be HTML/text from the agent.
                 // No extra formatting needed here, ui.displayMessage handles layout.
                 // If not admin_ai, it goes to internal comms by default route.
                break;
            case 'status':
            case 'system_event':
            case 'log': // Keep log type for now, might receive from older backend parts
                // These are typically simple strings, escape them
                displayContent = escapeHTML(data.content || data.message || `Event: ${messageType}`);
                displayAgentId = agentId || 'system'; // Ensure agentId for display
                break;
            case 'error':
                // Format error messages clearly
                displayContent = `❗ Error: ${escapeHTML(data.content || 'Unknown error')}`;
                displayAgentId = agentId || 'system';
                displayType = 'error'; // Ensure error class is used
                break;
            case 'agent_status_update':
                // Handled directly, no message display needed here
                console.log(`Handler: Handling agent_status_update for ${agentId}`);
                if (data.status && typeof data.status === 'object') {
                    const statusPayload = { ...data.status };
                    if (!statusPayload.agent_id && agentId) statusPayload.agent_id = agentId;
                    const singleAgentUpdate = { [agentId]: statusPayload };
                    ui.updateAgentStatusUI(singleAgentUpdate);
                } else {
                    console.warn("Handler: Received agent_status_update without valid status object:", data);
                }
                return; // Stop processing for this type

             case 'full_status':
                 // Handled directly, no message display needed here
                 console.log("Handler: Handling full_status update");
                 if (data.agents && typeof data.agents === 'object') {
                     ui.updateAgentStatusUI(data.agents);
                 } else {
                     console.warn("Handler: Received full_status without valid agents object:", data);
                 }
                 return; // Stop processing for this type

            // Handle lifecycle events (agent add/delete, team create/delete, session save/load)
            case 'agent_added':
            case 'agent_deleted':
            case 'team_created':
            case 'team_deleted':
            case 'session_saved':
            case 'session_loaded':
                 const eventMap = {
                    'agent_added': `Agent Added: ${data.agent_id} (${data.config?.persona || 'N/A'})`,
                    'agent_deleted': `Agent Deleted: ${data.agent_id}`,
                    'team_created': `Team Created: ${data.team_id}`,
                    'team_deleted': `Team Deleted: ${data.team_id}`,
                    'session_saved': `Session Saved: ${data.project}/${data.session}`,
                    'session_loaded': `Session Loaded: ${data.project}/${data.session}. UI Refreshing...`,
                 };
                 displayContent = escapeHTML(eventMap[messageType] || data.message || `Event: ${messageType}`);
                 displayAgentId = 'system';
                 displayType = 'system_event'; // Use a consistent class for system events

                 // If session loaded, refresh necessary parts of UI
                 if (messageType === 'session_loaded') {
                      if (DOM.conversationArea) DOM.conversationArea.innerHTML = ''; // Clear chat
                      ws.sendMessage(JSON.stringify({ type: 'get_full_status' })); // Refresh agent list
                 }
                 break;

            // --- Tool Handling Display (Internal Comms Only) ---
            // Decide IF and HOW to display tool requests/results.
            // Often, just seeing the agent's thought process leading to the tool call
            // and the subsequent result fed back is enough (which are agent_response/tool messages).
            // Displaying the raw requests/results might be too verbose.
            // Let's only display errors or explicit feedback related to tools for now.

            // case 'tool_requests':
            //     // Example: Could display a brief summary if needed
            //     // displayContent = `Agent ${agentId} requested ${data.calls?.length || 0} tool(s): ${data.calls?.map(c => c.name).join(', ')}`;
            //     // displayType = 'status';
            //     // displayAgentId = 'system'; // Or maybe the agent who requested?
            //     // break; // Commented out - likely too verbose

            // case 'tool_results': // Backend shouldn't really send this, it feeds back to agent
            //     // Could display if needed for debugging
            //     // displayContent = `Received tool result for call ${data.call_id}`;
            //     // displayType = 'status';
            //     // break; // Commented out

            // --- Default for Unknown Types ---
            default:
                console.warn(`Handler: Received unknown message type: ${messageType}`, data);
                targetAreaId = 'internal-comms-area'; // Default to internal comms
                displayContent = `Unknown msg type '${escapeHTML(messageType)}': ${escapeHTML(JSON.stringify(data))}`;
                displayAgentId = 'system';
                displayType = 'status'; // Treat unknown as status? Or error?
        }

        // --- Final Display Call ---
        if (targetAreaId && displayContent !== undefined) {
             console.log(`Handler: Final display call: target=${targetAreaId}, type=${displayType}, agentId=${displayAgentId}`);
             // Pass the calculated display parameters to the UI function
             ui.displayMessage(displayContent, displayType, targetAreaId, displayAgentId, displayPersonaForUI);
        } else {
             console.error("Handler: Message handling resulted in no targetAreaId or displayContent", data);
        }

    } catch (error) {
        console.error("Error in handleWebSocketMessage:", error);
        // Ensure errors in the handler itself are displayed
        ui.displayMessage(`!! JS Error handling WebSocket message: ${escapeHTML(error.message)} !!`, 'error', 'internal-comms-area', 'frontend');
    }
};


// --- UI Event Handlers (Remain Unchanged from previous version) ---

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
             ui.displayMessage(escapeHTML(message) + `<br><small><i>[Attached: ${escapeHTML(currentAttachedFile.name)}]</i></small>`, 'user', 'conversation-area', 'human_user'); // Added agentId
             handleClearAttachment(); // Clear file after sending
        } else {
            // Send plain text message
            ws.sendMessage(message);
            // Display user message *locally* in chat area
            ui.displayMessage(escapeHTML(message), 'user', 'conversation-area', 'human_user'); // Added agentId
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

// --- Config View Handlers (Unchanged) ---
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

// --- Session Management Handlers (Unchanged) ---
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
