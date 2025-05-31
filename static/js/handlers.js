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
 * Central handler for WebSocket messages. Determines where to display messages
 * and triggers UI updates or specific actions based on message type.
 * @param {object} data Parsed message data.
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
                const statusPayload = { ...data.status };
                if (!statusPayload.agent_id && agentId) statusPayload.agent_id = agentId;
                updateKnownAgentStatus(agentId, statusPayload);
                triggerStatusRedraw = true;
            } else {
                 console.warn("Handler: Received agent_status_update without valid status object:", data);
            }
        } else if (messageType === 'full_status') {
             console.log("Handler: Handling full_status update");
             if (data.agents && typeof data.agents === 'object') {
                 setFullKnownAgentStatuses(data.agents);
                 triggerStatusRedraw = true;
             } else {
                  console.warn("Handler: Received full_status without valid agents object:", data);
             }
        } else if (messageType === 'agent_deleted') {
             console.log(`Handler: Handling agent_deleted event for ${agentId}`);
             removeKnownAgentStatus(agentId);
             triggerStatusRedraw = true;
        } else if (messageType === 'agent_added') {
             console.log(`Handler: Handling agent_added event for ${agentId}`);
             if (data.config && typeof data.config === 'object') {
                updateKnownAgentStatus(agentId, {
                    agent_id: agentId,
                    status: data.status?.status || 'idle',
                    persona: data.config.persona,
                    model: data.config.model,
                    team: data.team,
                    provider: data.config.provider
                 });
                triggerStatusRedraw = true;
             } else {
                  console.warn("Handler: Received agent_added without valid config object:", data);
             }
        } else if (messageType === 'agent_moved_team') {
             console.log(`Handler: Handling agent_moved_team event for ${agentId}`);
             updateKnownAgentStatus(agentId, { team: data.new_team_id });
             triggerStatusRedraw = true;
        } else if (messageType === 'session_loaded') {
            console.log("Handler: Handling session_loaded event.");
            if (DOM.conversationArea) DOM.conversationArea.innerHTML = ''; // Clear chat
            ws.sendMessage(JSON.stringify({ type: 'get_full_status' }));
            triggerStatusRedraw = true;
        }

        // --- Message Display Logic ---
        const connectingMsg = DOM.internalCommsArea?.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();

        let targetAreaId = 'internal-comms-area'; // Default
        let displayType = messageType;
        let displayContent = data.content;
        let displayAgentId = agentId;
        let displayPersonaForUI = agentPersona;
        let shouldDisplay = true;

        // --- Routing and Formatting ---
        switch (messageType) {
            // --- State-only updates ---
            case 'agent_status_update':
            case 'full_status':
                shouldDisplay = false;
                break;

            // --- System Events (Internal Comms) ---
            case 'agent_deleted':
            case 'agent_added':
            case 'team_created':
            case 'team_deleted':
            case 'session_saved':
            case 'session_loaded':
            case 'agent_moved_team':
            case 'project_approved': // NEW: Handle approval confirmation
                 const eventMap = {
                    'agent_added': `Agent Added: ${data.agent_id} (${data.config?.persona || 'N/A'}) to team ${data.team || 'N/A'}`,
                    'agent_deleted': `Agent Deleted: ${data.agent_id}`,
                    'team_created': `Team Created: ${data.team_id}`,
                    'team_deleted': `Team Deleted: ${data.team_id}`,
                    'session_saved': `Session Saved: ${data.project}/${data.session}`,
                    'session_loaded': `Session Loaded: ${data.project}/${data.session}.`,
                    'agent_moved_team': `Agent Moved: ${data.agent_id} to team ${data.new_team_id || 'None'} from ${data.old_team_id || 'N/A'}`,
                    'project_approved': `Project Approved: ${data.message || `Project for PM ${data.pm_agent_id} started.`}` // NEW
                 };
                 displayContent = escapeHTML(eventMap[messageType] || data.message || `Event: ${messageType}`);
                 displayAgentId = 'system';
                 displayType = 'system_event';
                 targetAreaId = 'internal-comms-area';
                 targetAreaId = 'internal-comms-area';
                 break;

            // --- Project Approval Request (Main Chat Area) ---
            case 'project_pending_approval':
                console.log("Handler: Formatting project_pending_approval for main chat");
                const safeTitle = escapeHTML(data.project_title);
                const safePlan = escapeHTML(data.plan_content);
                const safePmId = escapeHTML(data.pm_agent_id);
                displayContent = `
                    <strong>Project Approval Required</strong><br>
                    Project Title: <strong>${safeTitle}</strong><br>
                    PM Agent ID: <code>${safePmId}</code><br>
                    <details>
                        <summary>View Plan</summary>
                        <pre>${safePlan}</pre>
                    </details>
                    <button class="approve-project-btn" data-pm-id="${safePmId}">Approve Project Start</button>
                `;
                targetAreaId = 'conversation-area'; // Route to main chat
                displayType = 'system_request'; // Use a specific class for styling
                displayAgentId = 'system'; // Display as from the system
                shouldDisplay = true; // Let displayMessage handle it
                break;

            case 'cg_concern': // Renamed from constitutional_concern
                console.log("Handler: Formatting cg_concern for main chat", data);
                // Fields based on observed log: { agent_id, concern_details, original_text, type: "cg_concern" }
                displayAgentId = escapeHTML(data.agent_id || 'system');
                displayPersonaForUI = escapeHTML(data.persona || 'Constitutional Guardian');

                const guardianReportDetails = escapeHTML(data.concern_details || "No details provided.");
                const originalReviewedText = data.original_text ? escapeHTML(data.original_text) : ''; // Already escaped if not empty

                const defaultOptions = [
                    { text: "Acknowledge", command: "acknowledge_cg_concern" },
                    { text: "Revise Plan", command: "revise_plan_cg_concern" },
                    { text: "Provide Feedback", command: "feedback_cg_concern" }
                ];

                let buttonsHTML = '';
                defaultOptions.forEach(option => {
                    buttonsHTML += `<button class="message-button" data-command="${escapeHTML(option.command)}" data-original-text="${originalReviewedText}">${escapeHTML(option.text)}</button>`;
                });

                displayContent = `
                    <div>
                        <p><strong>${displayPersonaForUI} says:</strong></p>
                        <p>${guardianReportDetails}</p>
                        ${originalReviewedText ? `
                        <details>
                            <summary>View Original Text Reviewed</summary>
                            <pre>${originalReviewedText}</pre> {/* originalReviewedText is already escaped */}
                        </details>
                        ` : ''}
                        <div class="options-container">${buttonsHTML}</div>
                    </div>
                `;
                targetAreaId = 'conversation-area';
                displayType = 'cg_concern_message'; // New specific display type
                shouldDisplay = true;
                break;

            // --- Project Approved Confirmation (Main Chat Area) ---
             case 'project_approved':
                 displayContent = `✅ Project Approved: ${escapeHTML(data.message || `Project for PM ${data.pm_agent_id} started.`)}`;
                 displayAgentId = 'system';
                 displayType = 'system_confirmation'; // Use a specific class
                 targetAreaId = 'conversation-area'; // Route to main chat
                 break;

            // --- Admin AI Responses ---
            case 'response_chunk':
                 if (agentId === 'admin_ai') {
                     targetAreaId = 'internal-comms-area'; // <<< CORRECT: Chunks go to internal comms
                     displayType = 'response_chunk'; // Style as chunk
                 } else {
                     // Other agents' chunks also go to internal comms for now
                     targetAreaId = 'internal-comms-area';
                     displayType = 'response_chunk';
                 }
                 // Handle empty chunks if necessary
                 if (displayContent === undefined || displayContent === null) {
                     displayContent = '[Empty Chunk]';
                     console.warn(`Handler: Received empty response_chunk from ${agentId}.`);
                 }
                 break;
            case 'agent_response': // Assuming this is used for the final complete message from Admin AI
            case 'final_response': // Handling both just in case
                 if (agentId === 'admin_ai') {
                     targetAreaId = 'conversation-area'; // <<< CORRECT: Final message goes to main chat
                     displayType = 'agent_response'; // Style as agent response
                 } else {
                    // Check if it's a constitutional agent's final response, should not go to internal-comms
                    if (agentId !== data.agent_id || messageType !== 'cg_concern') { // Avoid double display or wrong routing for constitutional concerns
                        targetAreaId = 'internal-comms-area';
                        displayType = 'log';
                    } else {
                        // This case should ideally be handled by 'cg_concern' block if that's the final message format
                        // If not, and it's a final response from constitutional agent meant for chat, this might need adjustment
                        // For now, prevent display if it's a duplicate or misrouted cg_concern message
                        shouldDisplay = false;
                    }
                 }
                 // Handle empty final messages
                 if (displayContent === undefined || displayContent === null) {
                    if(targetAreaId === 'internal-comms-area') {
                        displayContent = '[Empty Final Response/Msg]';
                    } else {
                        shouldDisplay = false; // Don't show empty final messages in main chat
                    }
                    console.warn(`Handler: Received ${messageType} from ${agentId} with no content.`);
                 }
                 break;

            // --- Other Internal Comms ---
            case 'status':
            case 'system_event': // Catchall if not handled above
            case 'log':
                displayContent = escapeHTML(data.content || data.message || `Event: ${messageType}`);
                displayAgentId = agentId || 'system';
                targetAreaId = 'internal-comms-area';
                break;
            case 'error':
                displayContent = `❗ Error (${escapeHTML(agentId)}): ${escapeHTML(data.content || 'Unknown error')}`;
                displayAgentId = agentId || 'system';
                displayType = 'error';
                targetAreaId = 'internal-comms-area';
                break;
            case 'tool_requests':
                displayContent = `Requesting Tool(s): ${escapeHTML(JSON.stringify(data.calls))}`;
                displayType = 'log-tool-use';
                targetAreaId = 'internal-comms-area';
                 break;
            case 'tool_result': // Corrected type name from backend? Assuming 'tool_result'
                displayContent = `Tool Result (${escapeHTML(data.call_id || 'N/A')}): ${escapeHTML(data.content)}`;
                displayType = 'log-tool-use';
                targetAreaId = 'internal-comms-area';
                break;
             case 'system_feedback': // Handle system feedback explicitly
                 displayContent = `System Feedback: ${escapeHTML(data.content)}`;
                 displayType = 'system_event'; // Style like other system events
                 targetAreaId = 'internal-comms-area';
                 break;

            case 'agent_state_change':
                displayContent = `Agent ${escapeHTML(agentId)} state changed from ${escapeHTML(data.old_state)} to ${escapeHTML(data.new_state)}. ${data.message ? escapeHTML(data.message) : ''}`;
                displayAgentId = agentId || 'system_event_source_agent';
                displayType = 'agent_state_transition'; // Or 'system_event'
                targetAreaId = 'internal-comms-area';
                shouldDisplay = true;
                break;

            case 'agent_state_change_requested':
                displayContent = `Agent ${escapeHTML(agentId)} requested state change to ${escapeHTML(data.requested_state)}. ${data.message ? escapeHTML(data.message) : ''}`;
                displayAgentId = agentId || 'system_event_source_agent';
                displayType = 'agent_state_request'; // Or 'system_event'
                targetAreaId = 'internal-comms-area';
                shouldDisplay = true;
                break;

            default:
                console.warn(`Handler: Received unknown message type: ${messageType}`, data);
                targetAreaId = 'internal-comms-area';
                displayContent = `Unknown msg type '${escapeHTML(messageType)}': ${escapeHTML(JSON.stringify(data))}`;
                displayAgentId = 'system';
                displayType = 'status';
        }

        // --- Display Message ---
        if (shouldDisplay && targetAreaId && displayContent !== undefined) {
             console.log(`Handler: Final display call: target=${targetAreaId}, type=${displayType}, agentId=${displayAgentId}`);
             ui.displayMessage(displayContent, displayType, targetAreaId, displayAgentId, displayPersonaForUI);
        }

        // --- Trigger Agent Status UI Redraw ---
        if (triggerStatusRedraw) {
            console.log("Handler: Triggering agent status UI redraw.");
            ui.updateAgentStatusUI();
        }

    } catch (error) {
        console.error("Error in handleWebSocketMessage:", error);
        ui.displayMessage(`!! JS Error handling WebSocket message: ${escapeHTML(error.message)} !!`, 'error', 'internal-comms-area', 'frontend');
    }
};

// --- NEW: Generic Button Click Handler ---
export const handleMessageButtonClick = (event) => {
    console.log("Handler: handleMessageButtonClick triggered.", event); // Log 1: Function triggered
    const clickedElement = event.target;
    console.log("Handler: Clicked element:", clickedElement); // Log 2: Clicked element

    // Use event delegation - check if the clicked element is a button with the correct class
    if (clickedElement.tagName === 'BUTTON' && clickedElement.classList.contains('message-button')) {
        console.log("Handler: Clicked element dataset:", clickedElement.dataset); // Log 3: Dataset if it's a message-button

        const command = clickedElement.dataset.command;
        const originalText = clickedElement.dataset.originalText;
        const concernId = clickedElement.dataset.concernId;
        const payloadString = clickedElement.dataset.payload;
        const buttonText = clickedElement.textContent;

        // Log 4: Values being checked (originalText specifically for cg_concern path)
        console.log("Handler: Checking for originalText. Command:", command, "OriginalText:", originalText);

        if (command) {
            let messageToSend;
            let messageObject;

            if (originalText !== undefined) { // Check for undefined, as empty string is a valid value for originalText
                console.log("Handler: 'originalText' condition met. Processing as cg_concern button."); // Log 5a: Outcome of originalText check
                // This is a cg_concern button click
                messageObject = {
                    type: "cg_concern_response",
                    action: command,
                    original_text: originalText, // originalText is already escaped when set in data attribute
                    user_feedback: null
                };
                console.log("Handler: Preparing to send WebSocket message:", messageObject); // Log 6: Before sending
                messageToSend = JSON.stringify(messageObject);
                ui.displayMessage(escapeHTML(`You chose to: ${buttonText}`), 'user', 'conversation-area', 'human_user');

            } else if (concernId) {
                console.log("Handler: 'originalText' condition NOT met. Checking for 'concernId'."); // Log 5b: Outcome if originalText not met
                // This is likely an older constitutional_response button click
                console.log("Handler: Handling button click with data-concern-id (older constitutional_response flow).");
                messageObject = {
                    type: "constitutional_response",
                    action: command,
                    concern_id: concernId,
                    user_feedback: null,
                };
                if (payloadString) {
                    try {
                        messageObject.payload = JSON.parse(payloadString);
                    } catch (e) {
                        console.error("Handler: Failed to parse payload JSON for button command:", payloadString, e);
                        // Potentially notify user or send without payload
                    }
                }
                console.log("Handler: Preparing to send WebSocket message:", messageObject); // Log 6: Before sending
                messageToSend = JSON.stringify(messageObject);
                // Display a more contextual message for the user
                ui.displayMessage(escapeHTML(`Decision: ${buttonText} (for concern ${concernId})`), 'user', 'conversation-area', 'human_user');

            } else {
                console.log("Handler: Clicked element does not seem to be a cg_concern or other known message button with concernId."); // Log 5c: If neither
                // Fallback for generic commands if any button uses this class without concern context
                messageToSend = command; // This might be just a string if no messageObject was formed
                // No specific messageObject to log here if it's just a raw command string
                console.log("Handler: Preparing to send raw command string via WebSocket:", messageToSend);
                ui.displayMessage(escapeHTML(command), 'user', 'conversation-area', 'human_user');
            }

            // Ensure messageToSend is defined before sending
            if (messageToSend) {
                ws.sendMessage(messageToSend);
            } else {
                console.warn("Handler: messageToSend was not defined, WebSocket message aborted.", "Command was:", command);
            }

            // Optional: Disable the button or all buttons in the group after click
            // clickedElement.disabled = true; // Corrected from button.disabled
            // clickedElement.textContent = 'Processing...'; // Corrected from button.textContent

            // Disable all buttons in the same options container
            // Handles both .options (older) and .options-container (newer for cg_concern)
            const parentOptionsContainer = clickedElement.closest('.options-container') || clickedElement.closest('.options');
            if (parentOptionsContainer) {
                parentOptionsContainer.querySelectorAll('.message-button').forEach(btn => {
                    btn.disabled = true;
                    btn.style.opacity = '0.5';
                });
            }

        } else {
            console.warn("Handler: Clicked message button has no data-command attribute.");
        }
    } else if (clickedElement.tagName === 'BUTTON' && clickedElement.classList.contains('approve-project-btn')) {
        // --- SPECIFIC Handler for existing Approve button ---
        // Ensure 'clickedElement' is used here as well, as 'button' might not be in this scope if the first if was false.
        const pmId = clickedElement.dataset.pmId;
        const commandText = `approve project ${pmId}`;
        console.log(`Handler: Approve Project button clicked for PM: ${pmId}`);

        // Display locally and send
        ui.displayMessage(escapeHTML(commandText), 'user', 'conversation-area', 'human_user');
        ws.sendMessage(commandText);

        // Disable button after click
        event.target.disabled = true;
        event.target.textContent = 'Approval Sent';
        event.target.style.backgroundColor = '#ccc'; // Indicate disabled state
    }
};
// --- END NEW ---


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

// --- Add Event Listener (in main.js or here if appropriate) ---
// This part should ideally go in main.js where DOM elements are assigned,
// but adding it here for completeness of the logic.
// Ensure DOM.conversationArea is available before adding listener.
// document.addEventListener('DOMContentLoaded', () => {
//     if (DOM.conversationArea) {
//         DOM.conversationArea.addEventListener('click', handleMessageButtonClick);
//         console.log("Added generic message button click listener to conversation area.");
//     } else {
//         console.error("Could not attach message button listener: conversationArea not found.");
//     }
// });
