// START OF FILE static/js/app.js

// Wait for the DOM to be fully loaded before running the script
document.addEventListener('DOMContentLoaded', (event) => {

    // Get references to the HTML elements we need to interact with
    const messageArea = document.getElementById('message-area');
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');

    let websocket = null; // Variable to hold the WebSocket instance

    function addMessage(message, type = 'info') {
        const messageElement = document.createElement('p');
        messageElement.textContent = message;
        messageElement.className = `message ${type}`; // Add classes for styling
        messageArea.appendChild(messageElement);
        // Scroll to the bottom of the message area
        messageArea.scrollTop = messageArea.scrollHeight;
    }

    function connectWebSocket() {
        // Determine WebSocket protocol (ws or wss) based on window location protocol
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Construct the WebSocket URL using the current hostname and port
        const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

        addMessage(`Attempting to connect to ${wsUrl}...`, 'status');

        websocket = new WebSocket(wsUrl);

        // --- WebSocket Event Handlers ---

        websocket.onopen = (event) => {
            console.log("WebSocket connection opened:", event);
            // Clear the initial "Connecting..." message or replace it
            const initialMsg = messageArea.querySelector('p');
            if (initialMsg && initialMsg.textContent.includes('Connecting')) {
                initialMsg.remove(); // Remove the very first placeholder message
            }
             // The server now sends the "Connected" message upon connection (in websocket_manager.py)
            // addMessage("WebSocket connection established.", 'status');
        };

        websocket.onmessage = (event) => {
            console.log("Message received from server:", event.data);
            try {
                // Assume messages from server are JSON strings
                const messageData = JSON.parse(event.data);

                // Handle different message types (optional structure for future)
                let displayMessage = '';
                let messageType = 'server'; // Default style for server messages

                if (messageData.type === 'status') {
                    displayMessage = `Status: ${messageData.message}`;
                    messageType = 'status';
                } else if (messageData.type === 'echo') {
                     displayMessage = `Server Echo: ${messageData.original_message}`;
                     messageType = 'echo';
                } else if (messageData.message) {
                    // Generic message handling if type is not recognized but has a message field
                    displayMessage = messageData.message;
                } else {
                    // Fallback for non-JSON or unexpected format
                    displayMessage = event.data;
                }
                 addMessage(displayMessage, messageType);

            } catch (error) {
                console.error("Failed to parse message or invalid format:", error);
                // Display the raw data if parsing fails
                addMessage(`Received raw: ${event.data}`, 'raw');
            }
        };

        websocket.onclose = (event) => {
            console.log("WebSocket connection closed:", event);
            addMessage(`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason || 'No reason provided'}`, 'error');
            websocket = null; // Reset websocket variable
            // Optionally disable input/button or attempt to reconnect
            sendButton.disabled = true;
            messageInput.disabled = true;
            addMessage("Attempting to reconnect in 5 seconds...", 'status');
            setTimeout(connectWebSocket, 5000); // Try to reconnect after 5 seconds
        };

        websocket.onerror = (event) => {
            console.error("WebSocket error observed:", event);
            addMessage("WebSocket error occurred. Check console for details.", 'error');
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
    connectWebSocket();

}); // End of DOMContentLoaded
