// START OF FILE static/js/state.js

/**
 * Manages shared frontend state.
 */

let websocket = null;
let isConnected = false;
let currentView = 'chat-view'; // Default view
let attachedFile = null; // { name: string, content: string } | null
let reconnectDelay = null; // Will be initialized by websocket module

// --- NEW: State for known agent statuses ---
let knownAgentStatuses = {}; // Stores the last known status for all agents { agentId: statusObject }
// --- End NEW ---

// --- Getters ---
export const getWebSocket = () => websocket;
export const getIsConnected = () => isConnected;
export const getCurrentView = () => currentView;
export const getAttachedFile = () => attachedFile;
export const getReconnectDelay = () => reconnectDelay;
export const getKnownAgentStatuses = () => knownAgentStatuses; // Getter for the agent status cache

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

// --- NEW: Agent Status Cache Updaters ---

/**
 * Updates the status for a single agent in the cache or adds a new one.
 * @param {string} agentId - The ID of the agent to update.
 * @param {object} statusData - The new status data object for the agent.
 */
export const updateKnownAgentStatus = (agentId, statusData) => {
     if (!agentId || !statusData || typeof statusData !== 'object') {
         console.warn("State: Invalid data passed to updateKnownAgentStatus", { agentId, statusData });
         return;
     }
     console.log(`State: Updating known status for agent ${agentId}`);
     // Merge new data with existing data if agent already exists, otherwise just add
     knownAgentStatuses[agentId] = { ...(knownAgentStatuses[agentId] || {}), ...statusData };
     // Handle deletion specifically
     if (statusData.status === 'deleted') {
         console.log(`State: Removing deleted agent ${agentId} from known statuses.`);
         delete knownAgentStatuses[agentId];
     }
};

/**
 * Replaces the entire agent status cache with new data.
 * Typically used when receiving a full status update from the backend.
 * @param {object} fullData - The complete agent status dictionary { agentId: statusObject }.
 */
export const setFullKnownAgentStatuses = (fullData) => {
    if (!fullData || typeof fullData !== 'object') {
        console.warn("State: Invalid data passed to setFullKnownAgentStatuses", fullData);
        knownAgentStatuses = {}; // Reset if invalid data received
        return;
    }
    console.log("State: Replacing known agent statuses with full update.");
    knownAgentStatuses = { ...fullData }; // Replace entire cache

    // Remove any agents marked as 'deleted' from the full update
    let deletedCount = 0;
    for (const agentId in knownAgentStatuses) {
        if (knownAgentStatuses[agentId]?.status === 'deleted') {
            delete knownAgentStatuses[agentId];
            deletedCount++;
        }
    }
    if (deletedCount > 0) console.log(`State: Removed ${deletedCount} deleted agents from full status update.`);
};

/**
 * Removes an agent from the known statuses cache.
 * @param {string} agentId - The ID of the agent to remove.
 */
export const removeKnownAgentStatus = (agentId) => {
    if (agentId && knownAgentStatuses.hasOwnProperty(agentId)) {
        console.log(`State: Removing agent ${agentId} from known statuses cache.`);
        delete knownAgentStatuses[agentId];
    }
};
// --- End NEW ---


console.log("Frontend state module initialized.");
