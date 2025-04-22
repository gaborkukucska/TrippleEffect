// START OF FILE static/js/api.js

import * as config from './config.js';
import { displayMessage } from './ui.js'; // For displaying API errors
import { escapeHTML } from './utils.js'; // For escaping error messages

/**
 * Makes an asynchronous API call to the backend.
 * @param {string} endpoint The API endpoint path (e.g., '/api/projects').
 * @param {string} [method='GET'] The HTTP method (GET, POST, PUT, DELETE).
 * @param {object} [body=null] The JSON body for POST/PUT requests.
 * @returns {Promise<any>} A promise that resolves with the JSON response data.
 * @throws {Error} Throws an error if the fetch fails or the response is not ok,
 *                 containing status and potentially parsed error details.
 */
export const makeApiCall = async (endpoint, method = 'GET', body = null) => {
    const url = `${config.API_BASE_URL}${endpoint}`;
    const options = {
        method: method.toUpperCase(), // Ensure method is uppercase
        headers: {},
    };

    // Add Content-Type and body for relevant methods
    if (body && (options.method === 'POST' || options.method === 'PUT')) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }

    console.debug(`API Call: ${options.method} ${url}`, body ? `Body: ${JSON.stringify(body).substring(0,100)}...` : ''); // Log API call attempt

    try {
        const response = await fetch(url, options);

        // Attempt to parse JSON regardless of status code to get potential error details
        let responseData = null;
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
            try {
                 responseData = await response.json();
                 console.debug(`API Response ${response.status} for ${options.method} ${url}:`, responseData);
            } catch (jsonError) {
                 console.error(`API Error: Failed to parse JSON response for ${options.method} ${url} (Status: ${response.status})`, jsonError);
                 // Create a synthetic error object if JSON parsing fails on a seemingly successful response
                 if (response.ok) {
                      throw new Error(`Received non-JSON response from server (Status: ${response.status})`);
                 }
                 // If response was not ok, use status text instead
                 responseData = { detail: `Non-JSON error response (Status: ${response.status}): ${response.statusText}` };
            }
        } else {
            // Handle non-JSON responses
            const textResponse = await response.text();
            console.warn(`API Warning: Received non-JSON response for ${options.method} ${url} (Status: ${response.status}). Body: ${textResponse.substring(0, 200)}...`);
             // If response was not ok, create an error detail from status text
             if (!response.ok) {
                  responseData = { detail: `Server returned status ${response.status}: ${response.statusText}` };
             } else {
                 // If response was ok but not JSON (e.g., 204 No Content), return something simple
                 responseData = { success: true, message: `Request successful with status ${response.status}` };
             }
        }

        // Check if response was successful (2xx status code)
        if (!response.ok) {
            const error = new Error(responseData?.detail || `HTTP error ${response.status}`);
            error.status = response.status;
            error.responseBody = responseData; // Attach parsed JSON or fallback detail
            console.error(`API Call Failed (${options.method} ${url}): Status ${error.status}`, error.responseBody);
            throw error; // Throw the error to be caught by the caller
        }

        return responseData; // Return parsed JSON data on success

    } catch (error) {
        // Log network errors or errors thrown from response handling
        console.error(`API Network/Fetch Error (${method} ${endpoint}):`, error);

        // Display the error in the internal comms area
        // Check if it's the custom error with responseBody or a generic fetch error
        const errorDetail = error.responseBody?.detail || error.message || 'Network request failed. Check console.';
        displayMessage(`API Error (${method} ${endpoint}): ${escapeHTML(errorDetail)}`, 'error', 'internal-comms-area', 'api');

        // Re-throw the error so calling functions know the request failed
        throw error;
    }
};

console.log("Frontend API module loaded.");
