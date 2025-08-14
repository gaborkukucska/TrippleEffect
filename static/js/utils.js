// START OF FILE static/js/utils.js

/**
 * Frontend Utility Functions
 */

/**
 * Escapes HTML special characters in a string.
 * Handles null/undefined and objects/arrays by attempting JSON stringification.
 * @param {*} str - The input value to escape.
 * @returns {string} The escaped string.
 */
export const escapeHTML = (str) => {
    if (str == null) return ''; // Handles null and undefined

    // Attempt to stringify objects/arrays, fallback to standard toString
    if (typeof str === 'object') {
        try {
            str = JSON.stringify(str);
        } catch (e) {
            console.warn("escapeHTML: Could not stringify object, using default toString.", str);
            str = Object.prototype.toString.call(str);
        }
    }

    // Ensure it's a string before replacing
    return String(str).replace(/[&<>"']/g, (match) => {
        switch (match) {
            case '&': return '&amp;';
            case '<': return '&lt;';
            case '>': return '&gt;';
            case '"': return '&quot;';
            case "'": return '&#39;'; // Standard HTML entity for single quote
            default: return match;
        }
    });
};

/**
 * Gets the current time as HH:MM:SS string.
 * @returns {string} Formatted timestamp.
 */
export const getCurrentTimestamp = () => {
    const now = new Date();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    return `${hours}:${minutes}:${seconds}`;
};

console.log("Frontend utils module loaded.");
