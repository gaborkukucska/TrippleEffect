// START OF FILE static/js/app.js

// Wait for the DOM to be fully loaded before running the script
document.addEventListener('DOMContentLoaded', (event) => {

    // Get references to the HTML elements we need to interact with
    const messageArea = document.getElementById('message-area');
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');

    let websocket = null; // Variable to hold the WebSocket instance
    let currentAgentResponseElement = null; // To group streamed responses

    function addMessage(message, type = 'info', agentId = null) {
        // Handle streamed agent responses - append to the last message element
        if (type === 'agent_response' && currentAgentResponseElement && currentAgentResponseElement.dataset.agentId === agentId) {
            // Append content without creating a new paragraph
            // Use textContent for safety against HTML injection in chunks
            currentAgentResponseElement.textContent += message;
        } else {
            // Create a new message element for other types or new agent streams
            currentAgentResponseElement = null; // Reset stream grouping if not an agent response chunk
            const messageElement = document.createElement('p');
            let prefix = '';
            if (agentId) {
                prefix = `[${agentId}] `;
                messageElement.dataset.agentId = agentId; // Store agentId for stream grouping
            }

            messageElement.textContent = prefix + message;
            messageElement.className = `message ${type}`; // Add classes for styling
            messageArea.appendChild(messageElement);

            // If this is the start of an agent response stream, keep reference
            if (type === 'agent_response') {
                currentAgentResponseElement = messageElement;
            }
        }

        // Scroll to the bottom of the message area
        messageArea.scrollTop = messageArea.scrollHeight;
    }

    function connectWebSocket() {
        // Determine WebSocket protocol (ws or wss) based on window location protocol
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Construct the WebSocket URL using the current hostname and port
        const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

        // Use a more specific status message on initial attempt
        const initialStatusMsg = messageArea.querySelector('p.status');
         if (!initialStatusMsg || !initialStatusMsg.textContent.includes('Attempting to connect')) {
             addMessage(`Attempting to connect to ${wsUrl}...`, 'status');
         }

        websocket = new WebSocket(wsUrl);

        // --- WebSocket Event Handlers ---

        websocket.onopen = (event) => {
            console.log("WebSocket connection opened:", event);
            // Server now sends status, no need for client-side message here.
            // Enable input fields on successful connection
            sendButton.disabled = false;
            messageInput.disabled = false;
            messageInput.focus();
            currentAgentResponseElement = null; // Reset on new connection
        };

        websocket.onmessage = (event) => {
            console.log("Message received from server:", event.data);
            try {
                // Assume messages from server are JSON strings
                const messageData = JSON.parse(event.data);
                const { type, agent_id, content } = messageData; // Destructure common fields

                // Use the addMessage function to display based on type
                // Pass agent_id to potentially prefix messages and group streams
                addMessage(content || '', type || 'server', agent_id || null);

            } catch (error) {
                console.error("Failed to parse message or invalid format:", error);
                // Display the raw data if parsing fails
                addMessage(`Received raw/invalid JSON: ${event.data}`, 'error');
                currentAgentResponseElement = null; // Stop grouping if format breaks
            }
        };

        websocket.onclose = (event) => {
            console.log("WebSocket connection closed:", event);
            addMessage(`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason || 'No reason provided'}`, 'error');
            websocket = null; // Reset websocket variable
            currentAgentResponseElement = null; // Reset stream grouping

            // Disable input/button and attempt to reconnect
            sendButton.disabled = true;
            messageInput.disabled = true;
            addMessage("Attempting to reconnect in 5 seconds...", 'status');
            setTimeout(connectWebSocket, 5000); // Try to reconnect after 5 seconds
        };

        websocket.onerror = (event) => {
            console.error("WebSocket error observed:", event);
             // Don't add duplicate messages if onclose follows immediately
            if (!websocket || websocket.readyState !== WebSocket.CLOSING && websocket.readyState !== WebSocket.CLOSED) {
               addMessage("WebSocket error occurred. Check console for details.", 'error');
            }
            currentAgentResponseElement = null; // Reset stream grouping
            // Note: 'onclose' will usually be called right after 'onerror'
        };
    }

    // --- Event Listener for the Send Button ---

    sendButton.addEventListener('click', () => {
        const message = messageInput.value.trim(); // Get message and remove leading/trailing whitespace
        if (message && websocket && websocket.readyState === WebSocket.OPEN) {
            console.log("Sending message:", message);
            websocket.send(message); // Send the message content as text
            addMessage(`You: ${message}`, 'user'); // Display the sent message immediately
            messageInput.value = ''; // Clear the input field
            messageInput.focus(); // Keep focus on input field
            currentAgentResponseElement = null; // Reset stream grouping after sending
        } else if (!websocket || websocket.readyState !== WebSocket.OPEN) {
            addMessage("WebSocket is not connected. Cannot send message.", 'error');
        }
    });

    // --- Allow sending message with Enter key in textarea ---
     messageInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) { // Send on Enter, allow Shift+Enter for newline
            e.preventDefault(); // Prevent default Enter behavior (newline)
            sendButton.click(); // Trigger the send button's click event
        }
    });


    // --- Initial Connection ---
    // Disable input initially until connection is successful
    sendButton.disabled = true;
    messageInput.disabled = true;
    connectWebSocket();

}); // End of DOMContentLoaded
