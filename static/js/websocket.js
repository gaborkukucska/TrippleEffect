// START OF FILE static/js/websocketModule.js

/**
 * @module websocketModule
 * @description Handles WebSocket connection setup, message sending, and basic lifecycle events.
 * Delegates message processing to the eventHandler module.
 */

import { eventHandler } from './eventHandler.js'; // Import the handler
import { uiModule } from './uiModule.js';       // Import uiModule for connection status updates

let websocket = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY = 3000; // 3 seconds

/**
 * Establishes the WebSocket connection.
 */
function connectWebSocket() {
    // Determine WebSocket protocol based on window location protocol
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

    console.log(`Attempting to connect WebSocket to ${wsUrl}`);
    uiModule.updateInitialConnectionStatus(false, "Connecting..."); // Show connecting status

    websocket = new WebSocket(wsUrl);

    websocket.onopen = () => {
        console.log('WebSocket connection established.');
        uiModule.updateInitialConnectionStatus(true); // Update UI to show connected
        reconnectAttempts = 0; // Reset reconnect attempts on successful connection
        // Maybe send a ping or initial message if required by backend?
    };

    websocket.onclose = (event) => {
        console.warn('WebSocket connection closed.', event.code, event.reason);
        uiModule.updateInitialConnectionStatus(false, `Disconnected. Attempting reconnect (${reconnectAttempts + 1}/${MAX_RECONNECT_ATTEMPTS})...`);
        websocket = null; // Clear the instance

        // Attempt to reconnect
        if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            setTimeout(() => {
                reconnectAttempts++;
                connectWebSocket(); // Retry connection
            }, RECONNECT_DELAY);
        } else {
            console.error("WebSocket reconnection attempts exhausted.");
            uiModule.updateInitialConnectionStatus(false, "Disconnected. Max reconnect attempts reached. Please refresh.");
            // Optionally display a more permanent error to the user
        }
    };

    websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
        uiModule.addLogEntry({ content: `WebSocket connection error: ${error.message || 'Unknown error'}` }, 'error');
        // onclose will likely be called after onerror, handling reconnection logic
    };

    /**
     * Handles incoming messages by delegating to the eventHandler.
     * @param {MessageEvent} event - The message event from the WebSocket.
     */
    websocket.onmessage = (event) => {
        // *** MODIFIED: Delegate message handling to eventHandler ***
        eventHandler.handleWebSocketMessage(event);
        // *** END MODIFICATION ***
    };
}

/**
 * Sends a message through the WebSocket connection.
 * @param {string | object} message - The message to send (can be string or object to be stringified).
 */
function sendMessage(message) {
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        const messageToSend = typeof message === 'string' ? message : JSON.stringify(message);
        // console.debug("Sending WS message:", messageToSend); // Optional log
        websocket.send(messageToSend);
    } else {
        console.error('WebSocket is not connected or not ready. Cannot send message.');
        // Optionally inform the user via UI
        uiModule.addLogEntry({ content: 'Error: Cannot send message. WebSocket not connected.' }, 'error');
    }
}

/**
 * Gets the current WebSocket instance.
 * @returns {WebSocket | null} The WebSocket instance or null if not connected.
 */
function getWebSocket() {
    return websocket;
}

// Export the necessary functions
export const websocketModule = {
    connectWebSocket,
    sendMessage,
    getWebSocket
};
