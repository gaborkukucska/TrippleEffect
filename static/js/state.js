// START OF FILE static/js/state.js

/**
 * Manages shared frontend state.
 */

let websocket = null;
let isConnected = false;
let currentView = 'chat-view'; // Default view
let attachedFile = null; // { name: string, content: string } | null
let reconnectDelay = null; // Will be initialized by websocket module

// --- Getters ---
export const getWebSocket = () => websocket;
export const getIsConnected = () => isConnected;
export const getCurrentView = () => currentView;
export const getAttachedFile = () => attachedFile;
export const getReconnectDelay = () => reconnectDelay;

// --- Setters ---
export const setWebSocket = (newWebsocket) => {
    console.log("State: WebSocket instance updated.");
    websocket = newWebsocket;
};

export const setIsConnected = (status) => {
    if (isConnected !== status) {
        console.log(`State: Connection status changed to ${status}`);
        isConnected = status;
    }
};

export const setCurrentView = (viewId) => {
    if (currentView !== viewId) {
        console.log(`State: Current view changed from ${currentView} to ${viewId}`);
        currentView = viewId;
        // Potential future use: trigger events when view changes
    }
};

export const setAttachedFile = (fileData) => {
    console.log("State: Attached file updated.", fileData ? fileData.name : 'None');
    attachedFile = fileData;
    // Trigger UI update implicitly or explicitly if needed elsewhere
};

export const setReconnectDelay = (delay) => {
    console.log(`State: Reconnect delay set to ${delay}ms`);
    reconnectDelay = delay;
};

console.log("Frontend state module initialized.");
