// START OF FILE static/js/app.js

// --- WebSocket Connection ---
const messageArea = document.getElementById('message-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
let websocket = null;

// Use ws:// or wss:// depending on the server protocol
// const wsUri = "ws://localhost:8000/ws";
// Dynamically determine WebSocket protocol based on window location protocol
const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const wsUri = `${wsProtocol}//${window.location.host}/ws`;


function connectWebSocket() {
    websocket = new WebSocket(wsUri);

    websocket.onopen = function(event) {
        console.log("WebSocket connection opened:", event);
        addMessage({ type: "status", message: "Connection established." });
        sendButton.disabled = false; // Enable send button on connect
        messageInput.disabled = false;
        messageInput.focus();
    };

    websocket.onmessage = function(event) {
        console.log("WebSocket message received:", event.data);
        try {
            const data = JSON.parse(event.data);
            addMessage(data); // Process and display the message
        } catch (e) {
            console.error("Failed to parse JSON message:", e);
            addMessage({ type: "raw", content: `Received non-JSON: ${event.data}` });
        }
    };

    websocket.onerror = function(event) {
        console.error("WebSocket error observed:", event);
        addMessage({ type: "error", content: "WebSocket connection error. Check backend." });
        sendButton.disabled = true; // Disable on error
        messageInput.disabled = true;
    };

    websocket.onclose = function(event) {
        console.log("WebSocket connection closed:", event);
        addMessage({ type: "status", message: `Connection closed (Code: ${event.code}). Trying to reconnect...` });
        sendButton.disabled = true; // Disable while closed
        messageInput.disabled = true;
        // Attempt to reconnect after a delay
        setTimeout(connectWebSocket, 5000); // Reconnect every 5 seconds
    };
}

// --- Message Handling ---

function addMessage(data) {
    const messageElement = document.createElement('div');
    messageElement.classList.add('message');

    let messageContent = '';
    let messageType = data.type || 'unknown'; // Default type
    let agentId = data.agent_id || 'system'; // Default agent ID

    messageElement.dataset.agentId = agentId; // Store agent ID for potential styling/grouping

    // --- Determine Message Type and Content ---
    switch (messageType) {
        case 'status':
            messageElement.classList.add('status');
            // Check for tool execution status
            if (data.content && data.content.startsWith("Executing tool:")) {
                messageContent = `⚙️ ${data.content}`; // Add an icon or indicator
                messageElement.classList.add('tool-execution'); // Add specific class
            } else if (data.message) { // Handle initial connect message format
                messageContent = data.message;
            } else {
                 messageContent = data.content || 'Status update.';
            }
            break;
        case 'error':
            messageElement.classList.add('error');
            messageContent = `❗ Error: ${data.content || 'An unknown error occurred.'}`;
            break;
        case 'user': // Local echo of user message
            messageElement.classList.add('user');
            messageContent = data.content;
            break;
        case 'agent_response':
            messageElement.classList.add('agent_response');
            messageContent = data.content; // Raw chunk from agent
            handleAgentResponseChunk(messageElement, agentId, messageContent);
            return; // Return early as grouping is handled separately
        case 'raw': // For non-JSON or debug messages
        default:
            messageElement.classList.add('raw');
            messageContent = `Raw: ${data.content || JSON.stringify(data)}`;
            break;
    }

    // For non-agent-response types, just set the content directly
    messageElement.textContent = messageContent;
    messageArea.appendChild(messageElement);
    scrollToBottom(); // Scroll down after adding message
}

// --- Agent Response Streaming/Grouping ---
let agentMessageBuffers = {}; // { agent_id: { element: div, content: string } }

function handleAgentResponseChunk(newChunkElement, agentId, chunkContent) {
    // Find the last message in the area
    const lastMessage = messageArea.lastElementChild;

    // Check if the last message was from the *same agent* and was an agent_response
    if (lastMessage && lastMessage.dataset.agentId === agentId && lastMessage.classList.contains('agent_response')) {
        // Append the chunk to the existing message element
        // Use textContent to avoid potential HTML injection issues if content isn't sanitized
        lastMessage.textContent += chunkContent;
    } else {
        // It's a new agent response block or follows a different message type
        // Set the content of the newly created element and append it
        newChunkElement.textContent = chunkContent; // Set content for the first chunk
        messageArea.appendChild(newChunkElement);
    }
    scrollToBottom(); // Scroll down after adding/appending chunk
}


function sendMessage() {
    const message = messageInput.value.trim();
    if (message && websocket && websocket.readyState === WebSocket.OPEN) {
        // Display user message locally immediately
        addMessage({ type: "user", content: message });

        // Send the message via WebSocket
        websocket.send(message);

        // Clear input field
        messageInput.value = '';
    } else if (!websocket || websocket.readyState !== WebSocket.OPEN) {
         addMessage({ type: "error", content: "WebSocket is not connected. Cannot send message." });
    }
}

// --- Utility Functions ---
function scrollToBottom() {
    messageArea.scrollTop = messageArea.scrollHeight;
}

// --- Event Listeners ---
sendButton.addEventListener('click', sendMessage);

messageInput.addEventListener('keypress', function(event) {
    // Send message on Enter key press (Shift+Enter for newline)
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault(); // Prevent default Enter behavior (newline)
        sendMessage();
    }
});

// --- Initial Connection ---
messageInput.disabled = true; // Disable input until connected
sendButton.disabled = true;
connectWebSocket();
