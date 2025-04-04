// START OF FILE static/js/app.js

document.addEventListener("DOMContentLoaded", () => {
    const messageArea = document.getElementById("message-area");
    const messageInput = document.getElementById("message-input");
    const sendButton = document.getElementById("send-button");
    // Placeholder for agent status area (can be used later)
    // const agentStatusArea = document.getElementById("agent-status-area");

    let websocket = null;
    const agentMessageBuffers = {}; // To group streamed messages per agent { agent_id: { element: null, content: "" } }

    function addMessage(data) {
        if (!messageArea) return;

        let messageElement = document.createElement("div");
        messageElement.classList.add("message"); // Base class

        const messageType = data.type || "raw"; // Default to 'raw' if type is missing
        const content = data.content || data.message || JSON.stringify(data); // Handle different content fields
        const agentId = data.agent_id || null; // Get agent ID if present

        messageElement.classList.add(messageType); // Add type-specific class (e.g., 'status', 'error', 'agent_response', 'user')

        if (agentId) {
            messageElement.dataset.agentId = agentId; // Add data attribute for CSS styling
        }

        // --- Streaming Logic for Agent Responses ---
        if (messageType === "agent_response" && agentId) {
            // Check if there's an existing buffer/element for this agent's current response stream
            if (!agentMessageBuffers[agentId] || !agentMessageBuffers[agentId].element) {
                 // Create a new message element for this agent's stream
                 messageElement.textContent = content;
                 messageArea.appendChild(messageElement);
                 // Store the element and initial content in the buffer
                 agentMessageBuffers[agentId] = { element: messageElement, content: content };
            } else {
                 // Append content to the existing element and buffer
                 agentMessageBuffers[agentId].content += content;
                 agentMessageBuffers[agentId].element.textContent = agentMessageBuffers[agentId].content;
                 // We don't need to append a new div, just update the existing one
                 messageElement = null; // Prevent appending a duplicate div later
            }
        } else {
             // For non-streamed messages or non-agent messages, just display directly
             messageElement.textContent = content;
             messageArea.appendChild(messageElement);

             // If this message is NOT an agent_response, clear the buffers for all agents
             // This assumes a non-agent message (like status 'finished') signifies the end of streaming for that interaction.
             // This might need refinement depending on backend message flow.
              if (messageType !== "agent_response") {
                  Object.keys(agentMessageBuffers).forEach(id => {
                      agentMessageBuffers[id] = { element: null, content: "" };
                  });
              }
              // If it *is* an agent response but *not* streamed (e.g., a full response at once)
              // clear the buffer specifically for *that* agent, assuming the stream is done.
              else if (agentId && agentMessageBuffers[agentId]) {
                  agentMessageBuffers[agentId] = { element: null, content: "" };
              }
        }


        // Scroll to the bottom of the message area
        messageArea.scrollTop = messageArea.scrollHeight;
    }

    function connectWebSocket() {
        // Determine WebSocket protocol (ws or wss)
        const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

        websocket = new WebSocket(wsUrl);

        websocket.onopen = (event) => {
            console.log("WebSocket connection opened");
            addMessage({ type: "status", content: "Connection established." });
            sendButton.disabled = false; // Enable send button on connect
        };

        websocket.onmessage = (event) => {
            console.log("Message from server:", event.data);
            try {
                const data = JSON.parse(event.data);
                addMessage(data); // Use the structured message handler
            } catch (e) {
                console.error("Failed to parse JSON message or handle message:", e);
                // Display raw message as fallback
                addMessage({ type: "raw", content: `Raw: ${event.data}` });
            }
        };

        websocket.onerror = (event) => {
            console.error("WebSocket error:", event);
            addMessage({ type: "error", content: "WebSocket connection error." });
            sendButton.disabled = true; // Disable send on error
        };

        websocket.onclose = (event) => {
            console.log("WebSocket connection closed:", event);
            addMessage({ type: "status", content: `Connection closed: ${event.code} ${event.reason}` });
            sendButton.disabled = true; // Disable send button on close
            websocket = null;
            // Optional: Attempt to reconnect after a delay
            // setTimeout(connectWebSocket, 5000); // Reconnect every 5 seconds
        };
    }

    function sendMessage() {
        if (!websocket || websocket.readyState !== WebSocket.OPEN) {
            addMessage({ type: "error", content: "WebSocket is not connected." });
            return;
        }

        const messageText = messageInput.value.trim();
        if (messageText) {
            // Display user message immediately
            addMessage({ type: "user", content: messageText });

            // Send message to backend
            websocket.send(messageText); // Send raw text, backend expects this based on current implementation

            // Clear input field
            messageInput.value = "";

            // Optional: Disable button while waiting for response (though multiple agents run concurrently)
            // sendButton.disabled = true;
            // Re-enable logic would need to track if *any* agent is still processing.
            // For now, allow sending new messages even if others are processing.
        }
    }

    // --- Event Listeners ---
    sendButton.addEventListener("click", sendMessage);

    messageInput.addEventListener("keypress", (event) => {
        // Send message on Enter key press (Shift+Enter for new line is standard textarea behavior)
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault(); // Prevent default Enter behavior (new line)
            sendMessage();
        }
    });

    // --- Initial Connection ---
    connectWebSocket();

});
