// START OF FILE static/js/websocket.js

import * as config from './config.js';
import * as state from './state.js';
import { displayStatusMessage } from './ui.js';
import { handleWebSocketMessage } from './handlers.js'; // Import the central message handler

/**
 * Establishes and manages the WebSocket connection.
 */
export const connectWebSocket = () => {
    // Prevent multiple concurrent connection attempts
    if (state.getWebSocket() && state.getWebSocket().readyState === WebSocket.CONNECTING) {
        console.log("WebSocket connection attempt already in progress.");
        return;
    }
    if (state.getIsConnected()) {
        console.log("WebSocket is already connected.");
        return;
    }

    // Initialize reconnect delay if it's null (first connection attempt)
    if (state.getReconnectDelay() === null) {
        state.setReconnectDelay(config.INITIAL_RECONNECT_DELAY);
    }

    console.log(`Attempting WebSocket connection to ${config.WS_URL}...`);
    displayStatusMessage("Connecting...", false, false, 'internal-comms-area');

    try {
        const ws = new WebSocket(config.WS_URL);
        state.setWebSocket(ws); // Update state with the new WebSocket instance

        ws.onopen = (event) => {
            console.log("WebSocket onopen event fired.");
            state.setIsConnected(true);
            state.setReconnectDelay(config.INITIAL_RECONNECT_DELAY); // Reset delay
            displayStatusMessage("Connected to backend.", false, false, 'internal-comms-area');
            console.log("WebSocket connection established.");
            sendMessage(JSON.stringify({ type: "request_full_agent_status" }));
             // Maybe request initial full status upon connection?
             // sendMessage(JSON.stringify({ type: 'get_initial_status' })); // If backend supports
        };

        ws.onmessage = (event) => {
            console.debug("WebSocket onmessage event fired.");
            try {
                const messageData = JSON.parse(event.data);
                console.debug("WebSocket message received:", messageData);
                // Delegate processing to the central handler
                handleWebSocketMessage(messageData);
            } catch (error) {
                console.error("Error parsing WebSocket message:", error);
                displayMessage(`Error parsing message: ${escapeHTML(event.data)}`, 'error', 'internal-comms-area');
            }
        };

        ws.onerror = (event) => {
            console.error("WebSocket onerror event fired:", event);
             // Check if it's a simple closure event before logging as error
             // Note: onerror is often followed by onclose. Log the error but rely on onclose for reconnect.
             displayStatusMessage(`WebSocket error occurred. Check console.`, false, true, 'internal-comms-area');
        };

        ws.onclose = (event) => {
            console.log(`WebSocket onclose event fired. Code: ${event.code}, Reason: '${event.reason || 'No reason given'}'`);
            const wasConnected = state.getIsConnected(); // Check status *before* changing it
            state.setIsConnected(false);
            state.setWebSocket(null); // Clear the websocket instance from state

            let delay = state.getReconnectDelay() || config.INITIAL_RECONNECT_DELAY; // Get current delay or start fresh
            displayStatusMessage(`Connection closed (${event.code}). Reconnecting in ${delay / 1000}s...`, false, true, 'internal-comms-area');

            // Schedule reconnection attempt with exponential backoff
            setTimeout(connectWebSocket, delay);
            state.setReconnectDelay(Math.min(delay * 2, config.MAX_RECONNECT_DELAY)); // Update delay in state for next attempt
        };

    } catch (err) {
        console.error("Error creating WebSocket:", err);
        displayStatusMessage(`Failed to create WebSocket connection: ${err.message}`, false, true, 'internal-comms-area');
        // Ensure state reflects failure and attempt reconnect
        state.setIsConnected(false);
        state.setWebSocket(null);
        let delay = state.getReconnectDelay() || config.INITIAL_RECONNECT_DELAY;
         setTimeout(connectWebSocket, delay);
         state.setReconnectDelay(Math.min(delay * 2, config.MAX_RECONNECT_DELAY));
    }
};

/**
 * Sends a message over the WebSocket connection if open.
 * @param {string} message The message string to send (usually JSON stringified).
 */
export const sendMessage = (message) => {
    const ws = state.getWebSocket(); // Get current websocket instance from state
    if (ws && ws.readyState === WebSocket.OPEN) {
        try {
            ws.send(message);
            console.debug(`Sent message: ${message.substring(0, 100)}...`);
        } catch (error) {
            console.error("Error sending WebSocket message:", error);
            // Display error in main chat as it's usually user-triggered
            import('./ui.js').then(ui => ui.displayMessage("Error sending message. Check console.", "error", "conversation-area"));
        }
    } else {
        console.error("WebSocket is not connected. Cannot send message.");
        import('./ui.js').then(ui => ui.displayMessage("Error: Not connected to backend. Message not sent.", "error", "conversation-area"));
    }
};

console.log("Frontend websocket module loaded.");

// Import necessary functions dynamically or ensure they are loaded before use
// For now, using dynamic imports in the error handlers for displayMessage
import { displayMessage } from './ui.js';
import { escapeHTML } from './utils.js';
