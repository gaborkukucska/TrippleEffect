// START OF FILE static/js/chatPersistence.js

/**
 * Persists chat and internal comms messages across browser refreshes
 * using sessionStorage. Messages are saved as HTML snapshots with
 * debouncing to avoid excessive writes during streaming.
 */

const CHAT_KEY = 'te_chat_html';
const COMMS_KEY = 'te_comms_html';
const MAX_STORED_CHAT_MESSAGES = 200;
const MAX_STORED_COMMS_MESSAGES = 500;
const SAVE_DEBOUNCE_MS = 1000;

let _saveTimer = null;

/**
 * Schedules a debounced save of both message areas to sessionStorage.
 * Call this after every displayMessage invocation.
 */
export const scheduleSaveMessages = () => {
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(_doSave, SAVE_DEBOUNCE_MS);
};

/**
 * Internal: performs the actual save to sessionStorage.
 * Clones the DOM areas and trims to max stored message counts
 * to stay within sessionStorage limits (~5MB).
 */
const _doSave = () => {
    try {
        const chatArea = document.getElementById('conversation-area');
        const commsArea = document.getElementById('internal-comms-area');

        if (chatArea) {
            _saveArea(chatArea, CHAT_KEY, MAX_STORED_CHAT_MESSAGES);
        }

        if (commsArea) {
            _saveArea(commsArea, COMMS_KEY, MAX_STORED_COMMS_MESSAGES);
        }

        console.debug('ChatPersistence: Saved messages to sessionStorage.');
    } catch (e) {
        console.warn('ChatPersistence: Save failed.', e);
        // If storage quota exceeded, clear old data and retry once
        if (e.name === 'QuotaExceededError') {
            console.warn('ChatPersistence: Quota exceeded, clearing stored messages.');
            sessionStorage.removeItem(CHAT_KEY);
            sessionStorage.removeItem(COMMS_KEY);
        }
    }
};

/**
 * Saves a single message area's HTML to sessionStorage,
 * trimming to the most recent maxMessages children.
 */
const _saveArea = (areaElement, storageKey, maxMessages) => {
    const clone = areaElement.cloneNode(true);

    // Remove any initial-placeholder elements from the clone
    const placeholder = clone.querySelector('.initial-placeholder');
    if (placeholder) placeholder.remove();

    // Trim oldest messages to stay within limits
    while (clone.children.length > maxMessages) {
        clone.removeChild(clone.firstChild);
    }

    const html = clone.innerHTML;
    // Only save if there's actual content (not empty or just whitespace)
    if (html.trim()) {
        sessionStorage.setItem(storageKey, html);
    }
};

/**
 * Restores chat and internal comms messages from sessionStorage.
 * Call this during app initialization, before the WebSocket connects.
 */
export const restoreMessages = () => {
    let restoredAny = false;
    try {
        const chatArea = document.getElementById('conversation-area');
        const commsArea = document.getElementById('internal-comms-area');

        const savedChat = sessionStorage.getItem(CHAT_KEY);
        const savedComms = sessionStorage.getItem(COMMS_KEY);

        if (savedChat && chatArea) {
            chatArea.innerHTML = savedChat;
            chatArea.scrollTop = chatArea.scrollHeight;
            restoredAny = true;
            console.log(`ChatPersistence: Restored chat messages (${chatArea.children.length} elements).`);
        }

        if (savedComms && commsArea) {
            commsArea.innerHTML = savedComms;
            commsArea.scrollTop = commsArea.scrollHeight;
            restoredAny = true;
            console.log(`ChatPersistence: Restored internal comms messages (${commsArea.children.length} elements).`);
        }

        if (!restoredAny) {
            console.log('ChatPersistence: No saved messages to restore.');
        }
    } catch (e) {
        console.warn('ChatPersistence: Restore failed.', e);
    }
};

/**
 * Clears all stored messages from sessionStorage.
 * Call this when a new session is loaded or chat is explicitly cleared.
 */
export const clearStoredMessages = () => {
    sessionStorage.removeItem(CHAT_KEY);
    sessionStorage.removeItem(COMMS_KEY);
    console.log('ChatPersistence: Cleared stored messages.');
};

console.log('ChatPersistence module loaded.');
