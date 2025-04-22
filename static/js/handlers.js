// START OF FILE static/js/handlers.js

import * as ui from './ui.js';
import * as api from './api.js';
import * as ws from './websocket.js';
// --- MODIFIED: Import specific state functions ---
import {
    getAttachedFile,
    setAttachedFile,
    updateKnownAgentStatus, // Import directly
    setFullKnownAgentStatuses, // Import directly
    removeKnownAgentStatus // Import directly
} from './state.js';
// --- END MODIFIED ---
import * as DOM from './domElements.js';
import * as session from './session.js'; // Import session functions
import * as configView from './configView.js'; // Import config view functions
import { escapeHTML } from './utils.js';

/**
 * Handles incoming WebSocket messages and routes them to appropriate UI display areas.
 * Routes User<->Admin messages/responses to #conversation-area.
 * Routes internal comms, status, tools, errors etc. to #internal-comms-area.
 * Updates agent status cache and triggers UI redraw.
 * @param {object} data The parsed message data from the WebSocket.
 */
export const handleWebSocketMessage = (data) => {
    console.log("Handler: Processing WebSocket message", data);
    try {
        const messageType = data.type;
        const agentId = data.agent_id || 'system'; // Default to 'system' if no agent_id
        const agentPersona = data.persona; // Persona might be included for context

        console.log(`Handler: Message type: ${messageType}, Agent: ${agentId}`);

        // --- State Update Logic (Cache agent statuses) ---
        let triggerStatusRedraw = false;
        if (messageType === 'agent_status_update') {
            console.log(`Handler: Handling agent_status_update for ${agentId}`);
            if (data.status && typeof data.status === 'object') {
                // Ensure agent_id is in the status payload for the state update
                const statusPayload = { ...data.status };
                if (!statusPayload.agent_id && agentId) statusPayload.agent_id = agentId;
                // --- MODIFIED: Call imported function directly ---
                updateKnownAgentStatus(agentId, statusPayload);
                // --- END MODIFIED ---
                triggerStatusRedraw = true; // Flag to redraw UI
            } else {
                 console.warn("Handler: Received agent_status_update without valid status object:", data);
            }
            // Don't display a message for this type, just update state and potentially redraw UI below
        } else if (messageType === 'full_status') {
             console.log("Handler: Handling full_status update");
             if (data.agents && typeof data.agents === 'object') {
                 // --- MODIFIED: Call imported function directly ---
                 setFullKnownAgentStatuses(data.agents);
                 // --- END MODIFIED ---
                 triggerStatusRedraw = true; // Flag to redraw UI
             } else {
                  console.warn("Handler: Received full_status without valid agents object:", data);
             }
             // Don't display a message for this type
        } else if (messageType === 'agent_deleted') {
             console.log(`Handler: Handling agent_deleted event for ${agentId}`);
             // --- MODIFIED: Call imported function directly ---
             removeKnownAgentStatus(agentId);
             // --- END MODIFIED ---
             triggerStatusRedraw = true; // Flag to redraw UI
             // Proceed to display a system message below
        } else if (messageType === 'agent_added') {
             console.log(`Handler: Handling agent_added event for ${agentId}`);
             // Add or update the agent in the cache
             if (data.config && typeof data.config === 'object') {
                // --- MODIFIED: Call imported function directly ---
                updateKnownAgentStatus(agentId, {
                    agent_id: agentId, // Ensure ID is present
                    status: data.status?.status || 'idle', // Use incoming status or default
                    persona: data.config.persona,
                    model: data.config.model,
                    team: data.team, // Include team if provided
                    provider: data.config.provider
                    // Add other relevant fields from config if needed
                 });
                 // --- END MODIFIED ---
                triggerStatusRedraw = true;
             } else {
                  console.warn("Handler: Received agent_added without valid config object:", data);
             }
             // Proceed to display a system message below
        } else if (messageType === 'agent_moved_team') {
             console.log(`Handler: Handling agent_moved_team event for ${agentId}`);
              // Update the team in the agent's cached status
             // --- MODIFIED: Call imported function directly ---
             updateKnownAgentStatus(agentId, { team: data.new_team_id });
             // --- END MODIFIED ---
             triggerStatusRedraw = true;
             // Proceed to display a system message below
        } else if (messageType === 'session_loaded') {
            console.log("Handler: Handling session_loaded event.");
            if (DOM.conversationArea) DOM.conversationArea.innerHTML = ''; // Clear chat
            ws.sendMessage(JSON.stringify({ type: 'get_full_status' })); // Request full status to rebuild cache
            triggerStatusRedraw = true; // Should be handled by full_status response
            // Proceed to display system message below
        }

        // --- Now, determine message display ---
        // Remove initial connecting message from internal comms if it exists
        const connectingMsg = DOM.internalCommsArea?.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();

        // --- Routing Logic: Determine Target Area ---
        let targetAreaId = 'internal-comms-area'; // Default to internal comms
        let displayType = messageType; // Use original type for class, might adjust later

        // Route specific message types to the main chat area
        if (agentId === 'admin_ai' && (messageType === 'agent_response' || messageType === 'final_response')) {
            targetAreaId = 'conversation-area';
            displayType = 'agent_response';
        } else if (messageType === 'user_message') {
            // Backend should ideally not echo user messages, but handle if it does
            targetAreaId = 'conversation-area';
            displayType = 'user';
        }

        console.log(`Handler: Routing message type '${messageType}' to targetAreaId: ${targetAreaId}`);

        // --- Prepare Content for Display ---
        let displayContent = data.content;
        let displayAgentId = agentId;
        let displayPersonaForUI = agentPersona;
        let shouldDisplay = true; // Flag to control if ui.displayMessage should be called

        // Format specific message types for better readability
        switch (messageType) {
            // --- Types that only update state ---
            case 'agent_status_update':
            case 'full_status':
                shouldDisplay = false; // State updated above, no message needed
                break;

            // --- Types handled mostly by state update + system message ---
            case 'agent_deleted':
            case 'agent_added':
            case 'team_created':
            case 'team_deleted':
            case 'session_saved':
            case 'session_loaded':
            case 'agent_moved_team':
                 const eventMap = {
                    'agent_added': `Agent Added: ${data.agent_id} (${data.config?.persona || 'N/A'}) to team ${data.team || 'N/A'}`,
                    'agent_deleted': `Agent Deleted: ${data.agent_id}`,
                    'team_created': `Team Created: ${data.team_id}`,
                    'team_deleted': `Team Deleted: ${data.team_id}`,
                    'session_saved': `Session Saved: ${data.project}/${data.session}`,
                    'session_loaded': `Session Loaded: ${data.project}/${data.session}.`, // Adjusted message
                    'agent_moved_team': `Agent Moved: ${data.agent_id} to team ${data.new_team_id || 'None'} from ${data.old_team_id || 'N/A'}`,
                 };
                 displayContent = escapeHTML(eventMap[messageType] || data.message || `Event: ${messageType}`);
                 displayAgentId = 'system';
                 displayType = 'system_event'; // Use a consistent class for system events
                 targetAreaId = 'internal-comms-area'; // System events go to internal comms
                 break; // Proceed to display

            // --- Types displayed normally ---
            case 'response_chunk':
            case 'agent_response':
            case 'final_response':
                 if (displayContent === undefined || displayContent === null) {
                    // Display empty responses only in internal comms for debugging
                    if(targetAreaId === 'internal-comms-area') {
                        displayContent = '[Empty Response Chunk/Msg]';
                    } else {
                        shouldDisplay = false; // Don't show empty messages in chat
                    }
                    console.warn(`Handler: Received ${messageType} from ${agentId} with no content.`);
                 }
                 // Content is passed as is (potentially HTML for displayMessage)
                 // Grouping of chunks is handled within displayMessage
                break;
            case 'status':
            case 'system_event': // Already handled above, but catchall
            case 'log':
                displayContent = escapeHTML(data.content || data.message || `Event: ${messageType}`);
                displayAgentId = agentId || 'system';
                targetAreaId = 'internal-comms-area'; // Ensure these go to internal comms
                break;
            case 'error':
                displayContent = `❗ Error (${escapeHTML(agentId)}): ${escapeHTML(data.content || 'Unknown error')}`;
                displayAgentId = agentId || 'system';
                displayType = 'error';
                targetAreaId = 'internal-comms-area'; // Errors go to internal comms
                break;
            case 'tool_requests':
                displayContent = `Requesting Tool(s): ${escapeHTML(JSON.stringify(data.calls))}`;
                displayType = 'log-tool-use'; // Use specific class
                targetAreaId = 'internal-comms-area';
                 break;
            case 'tool_results':
                displayContent = `Tool Result (${escapeHTML(data.call_id || 'N/A')}): ${escapeHTML(data.content)}`;
                displayType = 'log-tool-use';
                targetAreaId = 'internal-comms-area';
                break;

            default:
                console.warn(`Handler: Received unknown message type: ${messageType}`, data);
                targetAreaId = 'internal-comms-area';
                displayContent = `Unknown msg type '${escapeHTML(messageType)}': ${escapeHTML(JSON.stringify(data))}`;
                displayAgentId = 'system';
                displayType = 'status';
        }

        // --- Display Message if needed ---
        if (shouldDisplay && targetAreaId && displayContent !== undefined) {
             console.log(`Handler: Final display call: target=${targetAreaId}, type=${displayType}, agentId=${displayAgentId}`);
             ui.displayMessage(displayContent, displayType, targetAreaId, displayAgentId, displayPersonaForUI);
        }

        // --- Trigger Agent Status UI Redraw if needed ---
        if (triggerStatusRedraw) {
            console.log("Handler: Triggering agent status UI redraw.");
            ui.updateAgentStatusUI(); // Call without args to redraw from cache
        }

    } catch (error) {
        console.error("Error in handleWebSocketMessage:", error);
        // Ensure errors in the handler itself are displayed
        ui.displayMessage(`!! JS Error handling WebSocket message: ${escapeHTML(error.message)} !!`, 'error', 'internal-comms-area', 'frontend');
    }
};


// --- UI Event Handlers ---

export const handleSendMessage = () => {
    console.log("Handler: Send button clicked or Enter pressed.");
    const message = DOM.messageInput?.value?.trim();
    // --- MODIFIED: Use imported getter ---
    const currentAttachedFile = getAttachedFile();
    // --- END MODIFIED ---

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
            // --- MODIFIED: Use imported setter ---
            setAttachedFile({
                name: file.name,
                content: e.target.result
            });
            ui.displayFileInfo(getAttachedFile()); // Use getter to update UI
            // --- END MODIFIED ---
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
    // --- MODIFIED: Use imported setter ---
    setAttachedFile(null);
    // --- END MODIFIED ---
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
