// START OF FILE static/js/app.js

// --- WebSocket Setup ---
let socket;
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
// Get references to the new message areas
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
// Keep the old messageArea reference maybe for fallback? Or remove if not needed.
// const messageArea = document.getElementById('message-area'); // Keep original ID reference? No, let's use the specific ones.

// --- Helper: Scroll Area to Bottom ---
function scrollToBottom(element) {
    if (element) {
        element.scrollTop = element.scrollHeight;
    }
}

// --- WebSocket Connection ---
function connectWebSocket() {
    // Determine WebSocket protocol based on window location protocol
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    socket = new WebSocket(wsUrl);

    socket.onopen = function(event) {
        console.log("WebSocket connection opened:", event);
        // Add connection status message to the SYSTEM LOG area
        addMessage({ type: "status", content: "WebSocket connected!" }, systemLogArea);
        sendButton.disabled = false; // Enable send button
        messageInput.disabled = false;
    };

    socket.onmessage = function(event) {
        console.log("WebSocket message received:", event.data);
        try {
            const data = JSON.parse(event.data);
            // Pass the appropriate message area to addMessage
            // Default to system log area if type doesn't match conversation types
            let targetArea = systemLogArea;
            if (data.type === 'user' || data.type === 'agent_response') {
                targetArea = conversationArea;
            }
            addMessage(data, targetArea);
        } catch (e) {
            console.error("Failed to parse incoming message or add message to UI:", e);
            // Display raw message in system log on error
            addMessage({ type: "raw", content: `Raw: ${event.data}` }, systemLogArea);
            addMessage({ type: "error", content: `Error processing message: ${e.message}` }, systemLogArea);
        }
    };

    socket.onerror = function(event) {
        console.error("WebSocket error observed:", event);
        // Add error message to the SYSTEM LOG area
        addMessage({ type: "error", content: "WebSocket connection error. Check console." }, systemLogArea);
        sendButton.disabled = true; // Disable send on error
        messageInput.disabled = true;
    };

    socket.onclose = function(event) {
        console.log("WebSocket connection closed:", event);
        const reason = event.reason || `Code ${event.code}`;
        // Add close message to the SYSTEM LOG area
        addMessage({ type: "status", content: `WebSocket disconnected. ${reason}. Attempting to reconnect...` }, systemLogArea);
        sendButton.disabled = true; // Disable send on close
        messageInput.disabled = true;
        // Attempt to reconnect after a delay
        setTimeout(connectWebSocket, 5000); // Reconnect after 5 seconds
    };
}

// --- Sending Messages ---
function sendMessage() {
    if (socket && socket.readyState === WebSocket.OPEN) {
        const messageText = messageInput.value.trim();
        if (messageText) {
            // 1. Add user message to the CONVERSATION area
            addMessage({ type: "user", content: messageText }, conversationArea);

            // 2. Send the message text over WebSocket
            socket.send(messageText);

            // 3. Clear the input field
            messageInput.value = '';
        }
    } else {
        console.error("WebSocket is not connected.");
        addMessage({ type: "error", content: "Cannot send message: WebSocket not connected." }, systemLogArea);
    }
}

// --- Adding Messages to UI ---
function addMessage(data, targetArea) {
    if (!targetArea) {
        console.error("Target message area not provided for message:", data);
        return; // Cannot add message without a target
    }

    const messageElement = document.createElement('div');
    messageElement.classList.add('message');

    let messageContent = data.content || data.message || ''; // Handle different possible keys

    // Sanitize content before setting innerHTML? For now, just textContent.
    // If rendering HTML from agents becomes necessary, SANITIZE CAREFULLY!
    const contentElement = document.createElement('span'); // Use span for content
    contentElement.textContent = messageContent;


    // Determine message type and apply appropriate class / target area
    switch (data.type) {
        case 'user':
            messageElement.classList.add('user');
            // Ensure target is conversationArea
            targetArea = conversationArea;
            contentElement.textContent = `You: ${messageContent}`; // Add prefix
            break;
        case 'agent_response':
            messageElement.classList.add('agent_response');
            // Ensure target is conversationArea
            targetArea = conversationArea;
            const agentId = data.agent_id || 'unknown_agent';
            messageElement.dataset.agentId = agentId; // Store agent_id for styling

            // Check if this is a chunk for an existing message block
            const existingBlock = targetArea.querySelector(`.agent-message-block[data-agent-id="${agentId}"]`);
            if (existingBlock) {
                 // Append chunk to existing block's content span
                 // Find the content span within the block
                 const blockContentSpan = existingBlock.querySelector('.agent-content');
                 if (blockContentSpan) {
                     blockContentSpan.textContent += messageContent; // Append text directly
                     scrollToBottom(targetArea); // Scroll on chunk append
                     return; // Stop here, don't create a new div
                 } else {
                     // Fallback if span structure missing (shouldn't happen)
                     existingBlock.appendChild(contentElement);
                 }

            } else {
                // Create a new message block for this agent
                messageElement.classList.add('agent-message-block'); // Mark as a block start
                const agentPrefix = document.createElement('strong');
                agentPrefix.textContent = `${agentId}: `;
                contentElement.classList.add('agent-content'); // Mark the content part
                messageElement.appendChild(agentPrefix);
                messageElement.appendChild(contentElement);
                // Fallthrough to append this new block
            }
            break; // agent_response handled, break switch

        case 'status':
            messageElement.classList.add('status');
            // Ensure target is systemLogArea
            targetArea = systemLogArea;
            // Check for tool execution status
            if (messageContent.toLowerCase().includes("executing tool:")) {
                 messageElement.classList.add('tool-execution');
            }
             // Add agent ID prefix if available and not connection status
            if (data.agent_id && !messageContent.toLowerCase().includes("websocket")) {
                 contentElement.textContent = `[${data.agent_id}] ${messageContent}`;
            }
            break;
        case 'error':
            messageElement.classList.add('error');
            // Ensure target is systemLogArea
            targetArea = systemLogArea;
             // Add agent ID prefix if available
            if (data.agent_id) {
                 contentElement.textContent = `[${data.agent_id}] Error: ${messageContent}`;
            } else {
                 contentElement.textContent = `Error: ${messageContent}`;
            }
            break;
        case 'echo': // Keep for potential debug
        case 'raw':  // Keep for potential debug
        default:
            messageElement.classList.add('server'); // Generic server message style
            // Ensure target is systemLogArea
            targetArea = systemLogArea;
            break;
    }

     // Only append contentElement if not handled by agent_response block logic
     if (data.type !== 'agent_response' || !targetArea.querySelector(`.agent-message-block[data-agent-id="${data.agent_id}"]`)) {
         messageElement.appendChild(contentElement);
     }


    // Remove the "Connecting..." placeholder if it exists
    const connectingPlaceholder = systemLogArea.querySelector('.initial-connecting');
    if (connectingPlaceholder) {
        connectingPlaceholder.remove();
    }
    // Remove other placeholders if needed
    const conversationPlaceholder = conversationArea.querySelector('.message.status span:only-child');
     if (conversationPlaceholder && conversationPlaceholder.textContent === "Conversation Area") {
         conversationPlaceholder.parentElement.remove();
     }
      const systemLogPlaceholder = systemLogArea.querySelector('.message.status span:only-child');
      if (systemLogPlaceholder && systemLogPlaceholder.textContent === "System Logs & Status") {
          systemLogPlaceholder.parentElement.remove();
      }


    // Append the new message element to the correct area
    targetArea.appendChild(messageElement);

    // Scroll the target area to the bottom
    scrollToBottom(targetArea);
}


// --- Event Listeners ---
sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', function(event) {
    // Send message on Enter key press (Shift+Enter for newline)
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault(); // Prevent default newline behavior
        sendMessage();
    }
});

// --- Initial Setup ---
sendButton.disabled = true; // Disable send button initially
messageInput.disabled = true; // Disable input initially
// Add initial connecting message placeholder with a class
const initialConnecting = document.createElement('div');
initialConnecting.classList.add('message', 'status', 'initial-connecting');
initialConnecting.innerHTML = '<span>Connecting to backend...</span>'; // Use innerHTML to include span easily
if(systemLogArea){ // Check if area exists before appending
    systemLogArea.appendChild(initialConnecting);
} else {
    console.error("System log area not found on initial load!");
}

connectWebSocket(); // Start WebSocket connection

// Placeholder for later: Fetch initial agent status?
// fetchAgentStatus();
