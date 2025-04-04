// START OF FILE static/js/app.js

// --- State Variables ---
let socket;
const conversationHistory = []; // Array to store conversation messages ({ type: 'user'/'agent_response', ...data })
const systemLogHistory = []; // Array to store system log messages ({ type: 'status'/'error', ...data })
const agentStatusElements = {}; // { agent_id: element } - For status UI

// --- DOM Elements ---
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const agentStatusContent = document.getElementById('agent-status-content');

// --- Helper: Scroll Area to Bottom ---
function scrollToBottom(element) {
    if (element) {
        const scrollThreshold = 50; // Pixels from bottom
        if (element.scrollHeight - element.scrollTop <= element.clientHeight + scrollThreshold) {
            element.scrollTop = element.scrollHeight;
        }
    }
}

// --- WebSocket Connection ---
function connectWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    socket = new WebSocket(wsUrl);

    socket.onopen = function(event) {
        console.log("WebSocket connection opened:", event);
        // Add connection status message TO HISTORY and then render
        const connectMsg = { type: "status", content: "WebSocket connected!" };
        systemLogHistory.push(connectMsg);
        addMessage(connectMsg, systemLogArea); // Render it

        sendButton.disabled = false;
        messageInput.disabled = false;
        clearAgentStatusUI();
        // Note: History arrays are NOT cleared here, they persist for the session.
        // If we wanted to reload history from server, we'd do it here.
    };

    socket.onmessage = function(event) {
        console.log("WebSocket message received:", event.data);
        try {
            const data = JSON.parse(event.data);

            // Route message based on type
            if (data.type === 'agent_status_update') {
                 updateAgentStatusUI(data); // Handle status updates separately
            } else {
                // Determine target area and history array
                let targetArea = systemLogArea;
                let targetHistory = systemLogHistory;
                if (data.type === 'user' || data.type === 'agent_response') {
                    targetArea = conversationArea;
                    targetHistory = conversationHistory;
                }
                // Add data to the appropriate history array FIRST
                targetHistory.push(data);
                // THEN render the message just added
                addMessage(data, targetArea);
            }
        } catch (e) {
            console.error("Failed to parse incoming message or add message to UI:", e);
            // Add errors to system log history and render
            const rawMsg = { type: "raw", content: `Raw: ${event.data}` };
            const errorMsg = { type: "error", content: `Error processing message: ${e.message}` };
            systemLogHistory.push(rawMsg);
            systemLogHistory.push(errorMsg);
            addMessage(rawMsg, systemLogArea);
            addMessage(errorMsg, systemLogArea);
        }
    };

    socket.onerror = function(event) {
        console.error("WebSocket error observed:", event);
        const errorMsg = { type: "error", content: "WebSocket connection error. Check console." };
        systemLogHistory.push(errorMsg);
        addMessage(errorMsg, systemLogArea);
        sendButton.disabled = true;
        messageInput.disabled = true;
    };

    socket.onclose = function(event) {
        console.log("WebSocket connection closed:", event);
        const reason = event.reason || `Code ${event.code}`;
        const closeMsg = { type: "status", content: `WebSocket disconnected. ${reason}. Attempting to reconnect...` };
        systemLogHistory.push(closeMsg);
        addMessage(closeMsg, systemLogArea);
        sendButton.disabled = true;
        messageInput.disabled = true;
        clearAgentStatusUI("Disconnected - Status unavailable");
        setTimeout(connectWebSocket, 5000);
    };
}

// --- Sending Messages ---
function sendMessage() {
    if (socket && socket.readyState === WebSocket.OPEN) {
        const messageText = messageInput.value.trim();
        if (messageText) {
            // 1. Create user message object and add to CONVERSATION history
            const userMessage = { type: "user", content: messageText };
            conversationHistory.push(userMessage);

            // 2. Render the user message
            addMessage(userMessage, conversationArea);

            // 3. Send the message text over WebSocket
            socket.send(messageText);

            // 4. Clear the input field
            messageInput.value = '';
        }
    } else {
        console.error("WebSocket is not connected.");
        const errorMsg = { type: "error", content: "Cannot send message: WebSocket not connected." };
        systemLogHistory.push(errorMsg);
        addMessage(errorMsg, systemLogArea);
    }
}

// --- Adding Messages to UI (Rendering Function) ---
// This function now primarily focuses on RENDERING data that's already in history
function addMessage(data, targetArea) {
    if (!targetArea) {
        console.error("Target message area not provided for message:", data);
        return;
    }

    // --- Create message element and content span ---
    const messageElement = document.createElement('div');
    messageElement.classList.add('message');
    let messageContent = data.content || data.message || ''; // Handle different keys
    const contentElement = document.createElement('span'); // Use span for content
    contentElement.textContent = messageContent;

    // --- Apply styles and structure based on message type ---
    switch (data.type) {
        case 'user':
            messageElement.classList.add('user');
            // Target area should already be conversationArea if called correctly
            contentElement.textContent = `You: ${messageContent}`; // Add prefix
            messageElement.appendChild(contentElement);
            break;
        case 'agent_response':
            messageElement.classList.add('agent_response');
            // Target area should already be conversationArea
            const agentId = data.agent_id || 'unknown_agent';
            messageElement.dataset.agentId = agentId; // Store agent_id for styling

            // Check if this is a chunk for an existing message block IN THE UI
            // IMPORTANT: Chunk aggregation logic relies on the UI state now.
            const existingBlock = targetArea.querySelector(`.agent-message-block[data-agent-id="${agentId}"]`);
            if (existingBlock) {
                 const blockContentSpan = existingBlock.querySelector('.agent-content');
                 if (blockContentSpan) {
                     blockContentSpan.textContent += messageContent; // Append text directly
                     scrollToBottom(targetArea); // Scroll on chunk append
                     return; // Stop here, don't create a new div
                 } else { // Fallback
                     existingBlock.appendChild(contentElement);
                 }
            } else { // Create a new message block for this agent
                messageElement.classList.add('agent-message-block'); // Mark as a block start
                const agentPrefix = document.createElement('strong');
                agentPrefix.textContent = `${agentId}: `;
                contentElement.classList.add('agent-content'); // Mark the content part
                messageElement.appendChild(agentPrefix);
                messageElement.appendChild(contentElement);
            }
            break; // agent_response rendering handled

        case 'status':
            messageElement.classList.add('status');
            // Target area should be systemLogArea
            if (messageContent.toLowerCase().includes("executing tool:")) {
                 messageElement.classList.add('tool-execution');
            }
             // Add agent ID prefix if available and not connection status
            if (data.agent_id && !messageContent.toLowerCase().includes("websocket")) {
                 contentElement.textContent = `[${data.agent_id}] ${messageContent}`;
            }
            messageElement.appendChild(contentElement);
            break;
        case 'error':
            messageElement.classList.add('error');
            // Target area should be systemLogArea
            if (data.agent_id) {
                 contentElement.textContent = `[${data.agent_id}] Error: ${messageContent}`;
            } else {
                 contentElement.textContent = `Error: ${messageContent}`;
            }
            messageElement.appendChild(contentElement);
            break;
        case 'echo': // Keep for potential debug
        case 'raw':  // Keep for potential debug
        default:
            messageElement.classList.add('server'); // Generic server message style
             // Target area should be systemLogArea
            messageElement.appendChild(contentElement);
            break;
    }

    // --- Remove Placeholders ---
    // Remove placeholders only once if not already done
    if (!document.body.dataset.placeholdersRemoved) {
         const placeholders = document.querySelectorAll('.initial-placeholder');
         placeholders.forEach(p => p.remove());
         document.body.dataset.placeholdersRemoved = true; // Mark as removed
    }

    // --- Append to Target Area ---
    // Append the new message element to the correct area
    targetArea.appendChild(messageElement);

    // --- Scroll ---
    // Scroll the target area to the bottom
    scrollToBottom(targetArea);
}


// --- Agent Status UI (Remains the same) ---
function clearAgentStatusUI(message = "Waiting for status...") {
    if (agentStatusContent) {
        agentStatusContent.innerHTML = `<span class="status-placeholder">${message}</span>`;
    }
    Object.keys(agentStatusElements).forEach(key => delete agentStatusElements[key]);
}

function updateAgentStatusUI(data) {
    if (!agentStatusContent) return;
    const agentId = data.agent_id;
    const statusData = data.status;
    if (!agentId || !statusData) {
        console.warn("Received incomplete agent status update:", data);
        return;
    }
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();
    let agentElement = agentStatusElements[agentId];
    if (!agentElement) {
        agentElement = document.createElement('div');
        agentElement.classList.add('agent-status-item');
        agentElement.dataset.agentId = agentId;
        agentStatusContent.appendChild(agentElement);
        agentStatusElements[agentId] = agentElement;
    }
    let statusText = statusData.status || 'unknown';
    if (statusData.status === 'executing_tool' && statusData.current_tool) {
        statusText = `Executing Tool: ${statusData.current_tool.name}`;
    } else if (statusData.status === 'awaiting_tool_result') {
        statusText = 'Waiting for Tool Result';
    }
    agentElement.innerHTML = `
        <strong>${agentId}</strong>
        <span class="agent-model">(${statusData.model || 'N/A'})</span>:
        <span class="agent-status agent-status-${statusData.status || 'unknown'}">${statusText}</span>
    `;
    agentElement.className = 'agent-status-item';
    agentElement.classList.add(`status-${statusData.status || 'unknown'}`);
}

// --- Event Listeners ---
sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', function(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});

// --- Initial Setup ---
sendButton.disabled = true;
messageInput.disabled = true;

// Add initial placeholders with specific classes
function addInitialPlaceholder(area, text, className) {
    if (area) {
        // Check if placeholder already exists to prevent duplicates on reconnect attempt
        if (!area.querySelector(`.${className}`)) {
            const placeholder = document.createElement('div');
            placeholder.classList.add('message', 'status', className, 'initial-placeholder');
            placeholder.innerHTML = `<span>${text}</span>`;
            area.appendChild(placeholder);
        }
    } else {
        console.error("Target area not found for initial placeholder:", text);
    }
}

// Clear UI areas before adding placeholders (needed if page wasn't fully reloaded)
if(conversationArea) conversationArea.innerHTML = '';
if(systemLogArea) systemLogArea.innerHTML = '';

addInitialPlaceholder(systemLogArea, "System Logs & Status", "initial-system-log");
addInitialPlaceholder(conversationArea, "Conversation Area", "initial-conversation");
addInitialPlaceholder(systemLogArea, "Connecting to backend...", "initial-connecting");

clearAgentStatusUI();
connectWebSocket();
