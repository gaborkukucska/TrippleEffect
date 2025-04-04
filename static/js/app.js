// START OF FILE static/js/app.js

// --- WebSocket Setup ---
let socket;
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
// Get reference to the agent status display area
const agentStatusContent = document.getElementById('agent-status-content');

// Store agent status elements for easy update
const agentStatusElements = {}; // { agent_id: element }

// --- Helper: Scroll Area to Bottom ---
function scrollToBottom(element) {
    if (element) {
        // Only scroll if user isn't scrolled up significantly
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
        addMessage({ type: "status", content: "WebSocket connected!" }, systemLogArea);
        sendButton.disabled = false;
        messageInput.disabled = false;
        // Clear previous statuses on reconnect
        clearAgentStatusUI();
        // Optionally request initial status after connection (backend needs to handle this)
        // socket.send(JSON.stringify({ type: "request_all_statuses" }));
    };

    socket.onmessage = function(event) {
        console.log("WebSocket message received:", event.data);
        try {
            const data = JSON.parse(event.data);

            // Route message based on type
            if (data.type === 'agent_status_update') {
                 updateAgentStatusUI(data); // Handle status updates separately
            } else {
                // Determine target area for conversation/log messages
                let targetArea = systemLogArea;
                if (data.type === 'user' || data.type === 'agent_response') {
                    targetArea = conversationArea;
                }
                addMessage(data, targetArea);
            }
        } catch (e) {
            console.error("Failed to parse incoming message or add message to UI:", e);
            addMessage({ type: "raw", content: `Raw: ${event.data}` }, systemLogArea);
            addMessage({ type: "error", content: `Error processing message: ${e.message}` }, systemLogArea);
        }
    };

    socket.onerror = function(event) {
        console.error("WebSocket error observed:", event);
        addMessage({ type: "error", content: "WebSocket connection error. Check console." }, systemLogArea);
        sendButton.disabled = true;
        messageInput.disabled = true;
    };

    socket.onclose = function(event) {
        console.log("WebSocket connection closed:", event);
        const reason = event.reason || `Code ${event.code}`;
        addMessage({ type: "status", content: `WebSocket disconnected. ${reason}. Attempting to reconnect...` }, systemLogArea);
        sendButton.disabled = true;
        messageInput.disabled = true;
        // Clear statuses on disconnect
        clearAgentStatusUI("Disconnected - Status unavailable");
        setTimeout(connectWebSocket, 5000);
    };
}

// --- Sending Messages ---
function sendMessage() {
    if (socket && socket.readyState === WebSocket.OPEN) {
        const messageText = messageInput.value.trim();
        if (messageText) {
            addMessage({ type: "user", content: messageText }, conversationArea);
            socket.send(messageText);
            messageInput.value = '';
        }
    } else {
        console.error("WebSocket is not connected.");
        addMessage({ type: "error", content: "Cannot send message: WebSocket not connected." }, systemLogArea);
    }
}

// --- Adding Messages to UI (Conversation/Log Areas) ---
function addMessage(data, targetArea) {
    if (!targetArea) {
        console.error("Target message area not provided for message:", data);
        return;
    }

    const messageElement = document.createElement('div');
    messageElement.classList.add('message');

    let messageContent = data.content || data.message || '';
    const contentElement = document.createElement('span');
    contentElement.textContent = messageContent;

    switch (data.type) {
        case 'user':
            messageElement.classList.add('user');
            targetArea = conversationArea;
            contentElement.textContent = `You: ${messageContent}`;
            break;
        case 'agent_response':
            messageElement.classList.add('agent_response');
            targetArea = conversationArea;
            const agentId = data.agent_id || 'unknown_agent';
            messageElement.dataset.agentId = agentId;

            const existingBlock = targetArea.querySelector(`.agent-message-block[data-agent-id="${agentId}"]`);
            if (existingBlock) {
                 const blockContentSpan = existingBlock.querySelector('.agent-content');
                 if (blockContentSpan) {
                     blockContentSpan.textContent += messageContent;
                     scrollToBottom(targetArea);
                     return;
                 } else {
                     existingBlock.appendChild(contentElement);
                 }
            } else {
                messageElement.classList.add('agent-message-block');
                const agentPrefix = document.createElement('strong');
                agentPrefix.textContent = `${agentId}: `;
                contentElement.classList.add('agent-content');
                messageElement.appendChild(agentPrefix);
                messageElement.appendChild(contentElement);
            }
            break;

        case 'status':
            messageElement.classList.add('status');
            targetArea = systemLogArea;
            if (messageContent.toLowerCase().includes("executing tool:")) {
                 messageElement.classList.add('tool-execution');
            }
            if (data.agent_id && !messageContent.toLowerCase().includes("websocket")) {
                 contentElement.textContent = `[${data.agent_id}] ${messageContent}`;
            }
            break;
        case 'error':
            messageElement.classList.add('error');
            targetArea = systemLogArea;
            if (data.agent_id) {
                 contentElement.textContent = `[${data.agent_id}] Error: ${messageContent}`;
            } else {
                 contentElement.textContent = `Error: ${messageContent}`;
            }
            break;
        case 'echo': case 'raw': default:
            messageElement.classList.add('server');
            targetArea = systemLogArea;
            break;
    }

     if (data.type !== 'agent_response' || !targetArea.querySelector(`.agent-message-block[data-agent-id="${data.agent_id}"]`)) {
         messageElement.appendChild(contentElement);
     }

    // Remove placeholders only once needed
    if (!document.body.dataset.placeholdersRemoved) {
         const placeholders = document.querySelectorAll('.initial-connecting, .initial-placeholder');
         placeholders.forEach(p => p.remove());
         document.body.dataset.placeholdersRemoved = true; // Mark as removed
    }

    targetArea.appendChild(messageElement);
    scrollToBottom(targetArea);
}

// --- Agent Status UI ---

function clearAgentStatusUI(message = "Waiting for status...") {
    if (agentStatusContent) {
        agentStatusContent.innerHTML = `<span class="status-placeholder">${message}</span>`;
    }
    // Clear the storage object
    Object.keys(agentStatusElements).forEach(key => delete agentStatusElements[key]);
}

function updateAgentStatusUI(data) {
    if (!agentStatusContent) return;

    const agentId = data.agent_id;
    const statusData = data.status; // Expecting the full state dict here

    if (!agentId || !statusData) {
        console.warn("Received incomplete agent status update:", data);
        return;
    }

    // Remove placeholder if it exists
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();

    let agentElement = agentStatusElements[agentId];

    // Create element for this agent if it doesn't exist
    if (!agentElement) {
        agentElement = document.createElement('div');
        agentElement.classList.add('agent-status-item');
        agentElement.dataset.agentId = agentId;
        agentStatusContent.appendChild(agentElement);
        agentStatusElements[agentId] = agentElement; // Store reference
    }

    // Format the status string
    let statusText = statusData.status || 'unknown';
    if (statusData.status === 'executing_tool' && statusData.current_tool) {
        statusText = `Executing Tool: ${statusData.current_tool.name}`; // Display tool name
        if (statusData.current_tool.call_id) {
            // statusText += ` (ID: ${statusData.current_tool.call_id})`; // Maybe too verbose
        }
    } else if (statusData.status === 'awaiting_tool_result') {
        statusText = 'Waiting for Tool Result';
    }

    // Update the content of the agent's status element
    // Example: "coder (gpt-4-turbo): Idle"
    agentElement.innerHTML = `
        <strong>${agentId}</strong>
        <span class="agent-model">(${statusData.model || 'N/A'})</span>:
        <span class="agent-status agent-status-${statusData.status || 'unknown'}">${statusText}</span>
    `;

    // Optionally add/remove CSS classes based on status
    agentElement.className = 'agent-status-item'; // Reset classes
    agentElement.classList.add(`status-${statusData.status || 'unknown'}`); // Add status-specific class e.g., status-idle
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
        const placeholder = document.createElement('div');
        placeholder.classList.add('message', 'status', className, 'initial-placeholder');
        placeholder.innerHTML = `<span>${text}</span>`;
        area.appendChild(placeholder);
    } else {
        console.error("Target area not found for initial placeholder:", text);
    }
}

addInitialPlaceholder(systemLogArea, "System Logs & Status", "initial-system-log");
addInitialPlaceholder(conversationArea, "Conversation Area", "initial-conversation");
addInitialPlaceholder(systemLogArea, "Connecting to backend...", "initial-connecting"); // Keep connecting separate

// Initialize agent status area
clearAgentStatusUI();

connectWebSocket();
