// START OF FILE static/js/config.js

/**
 * Frontend Configuration Constants
 */

// Determine WebSocket URL dynamically based on browser location protocol
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
export const WS_URL = `${wsProtocol}//${window.location.host}/ws`;

// API base URL (empty string means relative to the current host)
export const API_BASE_URL = '';

// WebSocket reconnect delays
export const INITIAL_RECONNECT_DELAY = 1000; // 1 second
export const MAX_RECONNECT_DELAY = 30000; // 30 seconds

// Message history limits for UI display
export const MAX_COMM_MESSAGES = 200; // Max messages in Internal Comms view
export const MAX_CHAT_MESSAGES = 100; // Max messages in Chat view

console.log("Frontend config loaded:", { WS_URL, API_BASE_URL });
