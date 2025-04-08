// static/js/modules/websocket.js

// WebSocket Connection Logic

let ws = null; // Module-local WebSocket instance
let reconnectInterval = 5000;
let messageHandlerCallback = null; // Function provided by app.js to handle messages
let updateLogStatusCallback = null; // Function provided by app.js to update status display
let addMessageCallback = null; // Function provided by app.js

/**
 * Initializes the WebSocket connection and sets up handlers.
 * @param {function} handlerCallback - Function from app.js to process incoming messages.
 * @param {function} statusCallback - Function from uiUpdate.js (via app.js) to update connection status display.
 * @param {function} addMsgCallback - Function from uiUpdate.js (via app.js) to add log messages.
 */
export function initWebSocket(handlerCallback, statusCallback, addMsgCallback) {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        console.log("WebSocket already connecting or open.");
        return;
    }

    messageHandlerCallback = handlerCallback;
    updateLogStatusCallback = statusCallback;
    addMessageCallback = addMsgCallback; // Store the callback

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting WS connection: ${wsUrl}`);
    if(updateLogStatusCallback) updateLogStatusCallback('Connecting...', true);

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WS connected.');
        if(updateLogStatusCallback) updateLogStatusCallback('Connected!', false);
        // requestInitialAgentStatus(); // Belongs in app.js logic after connect
        reconnectInterval = 5000; // Reset reconnect interval
        // Pass the WebSocket instance back to modules that might need it (e.g., modals)
        // This is slightly tricky, maybe use event bus or direct reference setting in app.js
        console.log("WS Instance created in websocket.js:", ws);
    };

    ws.onmessage = (event) => {
        if (messageHandlerCallback) {
            try {
                const messageData = JSON.parse(event.data);
                messageHandlerCallback(messageData); // Pass parsed data to main handler
            } catch (error) {
                console.error('WS parse error:', error);
                 if (addMessageCallback) addMessageCallback('system-log-area', `[SysErr] Bad msg: ${event.data}`, 'error');
            }
        } else {
            console.error("WebSocket message handler not set!");
        }
    };

    ws.onerror = (event) => {
        console.error('WS error:', event);
         if (addMessageCallback) addMessageCallback('system-log-area', '[WS Error] Connect error.', 'error');
         if (updateLogStatusCallback) updateLogStatusCallback('Connect Error. Retry...', true);
         // No automatic reconnect here, onclose handles it
    };

    ws.onclose = (event) => {
        console.log('WS closed:', event.reason, `(${event.code})`);
        const message = event.wasClean
            ? `[WS Closed] Code: ${event.code}.`
            : `[WS Closed] Lost connection. Code: ${event.code}. Reconnecting...`;
        const type = event.wasClean ? 'status' : 'error';
         if (addMessageCallback) addMessageCallback('system-log-area', message, type);
         if (updateLogStatusCallback) updateLogStatusCallback('Disconnected. Retry...', true);
        ws = null; // Clear the instance
        // Schedule reconnect attempt
        setTimeout(() => initWebSocket(messageHandlerCallback, updateLogStatusCallback, addMessageCallback), reconnectInterval);
        reconnectInterval = Math.min(reconnectInterval * 1.5, 30000); // Exponential backoff
    };
}

/**
 * Sends a message object (usually JSON stringified) over the WebSocket.
 * @param {object | string} message - The message to send.
 */
export function sendMessageToServer(message) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        try {
             const messageString = typeof message === 'string' ? message : JSON.stringify(message);
             ws.send(messageString);
             // console.debug("Message sent:", messageString.substring(0,100)); // Optional debug
        } catch (error) {
            console.error("Error sending message:", error);
            if (addMessageCallback) addMessageCallback('system-log-area', '[SysErr] Failed to send message.', 'error');
        }
    } else {
        console.warn("WebSocket not open. Cannot send message.");
        if (addMessageCallback) addMessageCallback('system-log-area', '[SysErr] Cannot send: WS not connected.', 'error');
    }
}

/**
 * Provides access to the current WebSocket instance state.
 * Needed by other modules (like modals) to check connection before sending commands.
 */
export function getWebSocketState() {
    return ws ? ws.readyState : WebSocket.CLOSED; // Return state or CLOSED if null
}

export function getWebSocketInstance() {
     // Use cautiously - modules shouldn't directly manipulate WS outside this file ideally
    return ws;
}
