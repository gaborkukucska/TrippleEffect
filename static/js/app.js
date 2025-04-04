// START OF FILE static/js/app.js

// --- DOM Elements ---
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');
const configContent = document.getElementById('config-content'); // Added for config display
const agentStatusContent = document.getElementById('agent-status-content'); // Added for agent status display

// --- State Variables ---
let websocket = null;
let conversationHistory = []; // Store messages for the current session
let selectedFile = null; // Store the selected file object
let selectedFileContent = null; // Store the content of the selected file
let agentStatusElements = {}; // Cache for agent status DOM elements { agent_id: element }
let agentResponsePlaceholders = {}; // { agent_id: placeholderElement }

// --- Utility Functions ---

/**
 * Scrolls an element to its bottom if the user isn't scrolled up significantly.
 * @param {HTMLElement} element The element to scroll.
 */
function scrollToBottom(element) {
    // Small threshold to allow users to scroll up slightly without fighting auto-scroll
    const scrollThreshold = 50;
    const isScrolledNearBottom = element.scrollHeight - element.clientHeight <= element.scrollTop + scrollThreshold;

    // console.log(`Scroll near bottom: ${isScrolledNearBottom}, ScrollTop: ${element.scrollTop}, ScrollHeight: ${element.scrollHeight}, ClientHeight: ${element.clientHeight}`); // Debug logging

    if (isScrolledNearBottom) {
        element.scrollTop = element.scrollHeight;
    }
}

// --- WebSocket Management ---

/**
 * Establishes the WebSocket connection and sets up event handlers.
 */
function connectWebSocket() {
    // Determine WebSocket protocol (ws or wss)
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    console.log(`Attempting to connect WebSocket to: ${wsUrl}`);

    websocket = new WebSocket(wsUrl);

    websocket.onopen = (event) => {
        console.log("WebSocket connection opened");
        addMessage({ type: 'status', message: 'WebSocket connection established.' }, systemLogArea);
        // Remove initial connecting message if it exists
        const connectingMsg = systemLogArea.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();
        // Fetch initial data after connection
        fetchAgentConfigurations();
        // Potentially fetch initial agent statuses if needed, though updates should push them
    };

    websocket.onmessage = (event) => {
        // console.log("WebSocket message received:", event.data); // Log raw message
        try {
            const data = JSON.parse(event.data);

            // Determine target area based on message type
            let targetArea = systemLogArea; // Default to system log
            if (data.type === 'user' || data.type === 'agent_response') {
                targetArea = conversationArea;
            }

            // Handle specific message types
            if (data.type === 'agent_status_update') {
                updateAgentStatusUI(data.status); // Pass the nested status object
            } else {
                // Add regular messages (status, error, user, agent_response)
                addMessage(data, targetArea);
                // Store agent responses in history
                if (data.type === 'agent_response') {
                     // Note: This simple history doesn't handle chunk aggregation perfectly.
                     // It might store partial messages if connection resets mid-stream.
                     conversationHistory.push({ sender: data.agent_id, text: data.content });
                }
            }

        } catch (e) {
            console.error("Error parsing WebSocket message:", e);
            addMessage({ type: 'error', content: 'Received invalid message format from server.' }, systemLogArea);
        }
    };

    websocket.onerror = (event) => {
        console.error("WebSocket error:", event);
        addMessage({ type: 'error', content: 'WebSocket connection error occurred.' }, systemLogArea);
        // Attempt to reconnect after a delay?
    };

    websocket.onclose = (event) => {
        console.log("WebSocket connection closed:", event);
        addMessage({ type: 'status', message: `WebSocket connection closed (Code: ${event.code}). Attempting to reconnect...` }, systemLogArea);
        websocket = null;
        // Simple immediate reconnect attempt, consider exponential backoff for production
        setTimeout(connectWebSocket, 5000); // Reconnect after 5 seconds
    };
}

// --- Message Handling & Display ---

/**
 * Sends a message (potentially with file context) over the WebSocket.
 */
function sendMessage() {
    const messageText = messageInput.value.trim();
    let messageToSend = messageText;

    // Add user message to conversation history immediately
     if (messageText || selectedFileContent) {
         // Prepend file content if available
         if (selectedFile && selectedFileContent) {
              messageToSend = `--- Start of file: ${selectedFile.name} ---\n${selectedFileContent}\n--- End of file: ${selectedFile.name} ---\n\n${messageText}`;
              console.log(`Prepending content from file: ${selectedFile.name}`);
         }

         if (messageToSend) { // Ensure we have something to send
             // Add to local UI immediately
             addMessage({ type: 'user', content: messageToSend }, conversationArea);
             conversationHistory.push({ sender: 'user', text: messageToSend }); // Add to history

             // Send via WebSocket
             if (websocket && websocket.readyState === WebSocket.OPEN) {
                 websocket.send(messageToSend);
                 console.log("Message sent:", messageToSend);
             } else {
                 addMessage({ type: 'error', content: 'WebSocket not connected. Cannot send message.' }, systemLogArea);
             }
         }

         // Clear input and file state
         messageInput.value = '';
         clearSelectedFile();

     } else {
        console.log("No message or file content to send.");
     }
}


/**
 * Adds a message object to the specified message area in the UI.
 * Handles different message types and formatting.
 * @param {object} data - The message data object (e.g., { type: 'user', content: '...' } or { type: 'agent_response', agent_id: '...', content: '...' }).
 * @param {HTMLElement} targetArea - The DOM element to append the message to.
 */
function addMessage(data, targetArea) {
    // Remove initial placeholders if they exist and we are adding a real message
    const initialPlaceholders = targetArea.querySelectorAll('.initial-placeholder');
     if (initialPlaceholders.length > 0 && data.type !== 'status') { // Don't remove placeholders for status messages initially
        if (data.content || data.message) { // Check if there's actual content/message
             initialPlaceholders.forEach(p => p.remove());
        }
     }


    const messageElement = document.createElement('div');
    messageElement.classList.add('message');
    messageElement.classList.add(data.type); // e.g., 'user', 'agent_response', 'status', 'error'

    let messageContent = '';
    let agentId = data.agent_id || 'unknown_agent'; // Default if not specified

    switch (data.type) {
        case 'user':
            messageElement.textContent = data.content;
            break;
        case 'agent_response':
            messageElement.dataset.agentId = agentId; // Add data attribute for styling
            const agentName = agentId; // Use agent_id for display name for now

            // Handle streaming chunks: Aggregate content
            let existingPlaceholder = agentResponsePlaceholders[agentId];
            if (existingPlaceholder) {
                 // Append content to the existing placeholder's content area
                 const contentSpan = existingPlaceholder.querySelector('.content');
                 if (contentSpan) {
                    contentSpan.textContent += data.content;
                    messageElement = existingPlaceholder; // Use the existing element for scrolling logic
                 } else {
                     // Fallback if structure is wrong, append normally (less ideal)
                     existingPlaceholder.textContent += data.content;
                     messageElement = existingPlaceholder;
                 }
                 // No need to create a new element, just update and ensure scroll
            } else {
                 // Create new placeholder for this agent's response turn
                 messageElement.innerHTML = `<strong>[${agentName}]: </strong><span class="content"></span>`;
                 const contentSpan = messageElement.querySelector('.content');
                 contentSpan.textContent = data.content;
                 agentResponsePlaceholders[agentId] = messageElement; // Store the new placeholder
                 targetArea.appendChild(messageElement); // Append the new element
            }

             // Check if the stream for this agent is potentially done (heuristic: non-chunk or specific marker if available)
             // We need a reliable way to know when an agent's turn ends to remove the placeholder.
             // Let's assume agent status changes signal the end for now. Placeholder cleared by status update.
            break; // Handled above
        case 'status':
            messageContent = data.message || data.content || '[Status Update]';
            messageElement.textContent = messageContent;
             // Optional: Add agent ID if present
             if (data.agent_id) {
                 messageElement.textContent = `[${data.agent_id}] ${messageContent}`;
                 messageElement.dataset.agentId = data.agent_id;
             }
            // If status indicates tool execution, add specific class
            if (messageContent.toLowerCase().includes('executing tool')) {
                messageElement.classList.add('tool-execution');
            }
            break;
        case 'error':
            messageContent = data.content || data.message || '[Unknown Error]';
            // Add agent ID prefix if available
             if (data.agent_id) {
                 messageElement.textContent = `[${data.agent_id} Error]: ${messageContent}`;
                 messageElement.dataset.agentId = data.agent_id;
             } else {
                 messageElement.textContent = `[Error]: ${messageContent}`;
             }
            break;
        default:
            console.warn("Unknown message type:", data.type);
            messageElement.textContent = `[${data.type}] ${data.content || data.message || ''}`;
            break;
    }

    // Append only if it's not an existing element being updated
    if (!agentResponsePlaceholders[agentId] || agentResponsePlaceholders[agentId] !== messageElement) {
        if(messageElement.textContent.trim() !== '' || messageElement.innerHTML.trim() !== '') { // Avoid adding empty elements
            targetArea.appendChild(messageElement);
        }
    }

    // Scroll the target area to the bottom
    scrollToBottom(targetArea);
}


/**
 * Clears any temporary placeholders for agent responses.
 * Call this when an agent finishes its turn (e.g., goes idle or errors).
 * @param {string} agentId The ID of the agent whose placeholder to clear.
 */
function clearAgentResponsePlaceholder(agentId) {
     if (agentResponsePlaceholders[agentId]) {
        // Maybe add a final class or subtle indication it's complete?
        // For now, just remove from tracking. It stays in the DOM.
        // console.log(`Clearing placeholder tracking for ${agentId}`);
        delete agentResponsePlaceholders[agentId];
     }
}

/** Clears all agent response placeholders (e.g., on disconnect or full clear) */
function clearAllAgentResponsePlaceholders() {
    // console.log("Clearing all agent response placeholders.");
    agentResponsePlaceholders = {};
}

/**
 * Clears the agent status display area in the UI.
 * @param {string} [message="Waiting for status..."] Optional message to display.
 */
function clearAgentStatusUI(message = "Waiting for status...") {
    agentStatusContent.innerHTML = `<span class="status-placeholder">${message}</span>`;
    agentStatusElements = {}; // Clear the cache
}

/**
 * Updates the UI to display the current status of agents.
 * Handles 'agent_status_update' messages from the backend.
 * @param {object} statusData - The agent's state object received from the backend.
 *                                e.g., { agent_id: 'coder', status: 'idle', persona: '...', ... }
 */
function updateAgentStatusUI(statusData) {
    if (!statusData || !statusData.agent_id) {
        console.warn("Received invalid agent status update data:", statusData);
        return;
    }

    const agentId = statusData.agent_id;

    // Clear the initial placeholder if it's the first real status update
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    // Find or create the element for this agent
    let agentElement = agentStatusElements[agentId];
    if (!agentElement) {
        agentElement = document.createElement('div');
        agentElement.classList.add('agent-status-item');
        agentElement.dataset.agentId = agentId;
        agentStatusContent.appendChild(agentElement);
        agentStatusElements[agentId] = agentElement; // Cache it
    }

    // Determine status class
    const statusClass = `status-${statusData.status?.toLowerCase() || 'unknown'}`;
    agentElement.className = `agent-status-item ${statusClass}`; // Reset classes and set the new status class

    // Format the display string
    let statusText = statusData.status || 'Unknown';
    let toolInfo = '';
    if (statusData.status === 'executing_tool' && statusData.current_tool) {
        toolInfo = ` (Tool: ${statusData.current_tool.name} [${statusData.current_tool.call_id?.substring(0, 6) || '...'}])`;
    } else if (statusData.status === 'awaiting_tool_result') {
        toolInfo = ' (Waiting for tool...)';
    }

    // Update inner HTML
    agentElement.innerHTML = `
        <strong>${statusData.persona || agentId}:</strong>
        <span class="agent-model">(${statusData.provider || 'N/A'} / ${statusData.model || 'N/A'})</span>
        <span class="agent-status">${statusText}${toolInfo}</span>
    `;

     // If agent is now idle or errored, clear any streaming response placeholder for it
     if (statusData.status === 'idle' || statusData.status === 'error') {
         clearAgentResponsePlaceholder(agentId);
     }
}


// --- Configuration Display --- NEW FUNCTION ---

/**
 * Fetches agent configurations from the backend and displays them.
 */
async function fetchAgentConfigurations() {
    console.log("Fetching agent configurations...");
    configContent.innerHTML = `<span class="status-placeholder">Loading config...</span>`; // Show loading state

    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const agentConfigs = await response.json();
        console.log("Agent configurations received:", agentConfigs);
        displayAgentConfigurations(agentConfigs);
    } catch (error) {
        console.error("Error fetching agent configurations:", error);
        configContent.innerHTML = `<span class="error-placeholder">Error loading agent configurations: ${error.message}</span>`;
    }
}

/**
 * Renders the fetched agent configurations into the UI.
 * @param {Array<object>} configs - Array of agent configuration objects.
 */
function displayAgentConfigurations(configs) {
    configContent.innerHTML = ''; // Clear previous content or loading state

    if (!configs || configs.length === 0) {
        configContent.innerHTML = `<span class="status-placeholder">No agents configured in config.yaml.</span>`;
        return;
    }

    const ul = document.createElement('ul');
    ul.style.listStyle = 'none'; // Basic styling
    ul.style.padding = '0';

    configs.forEach(agent => {
        const li = document.createElement('li');
        li.style.marginBottom = '5px';
        li.style.paddingBottom = '5px';
        li.style.borderBottom = '1px dotted #eee';
        li.innerHTML = `
            <strong>${agent.agent_id} (${agent.persona || 'N/A'})</strong><br>
            <small>Provider: ${agent.provider || 'N/A'}, Model: ${agent.model || 'N/A'}</small>
        `;
        ul.appendChild(li);
    });

    configContent.appendChild(ul);
}


// --- File Handling ---

/**
 * Handles the file selection event. Reads the file content.
 * @param {Event} event - The file input change event.
 */
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) {
        clearSelectedFile();
        return;
    }

    // Basic validation (allow common text-based types)
    const allowedTypes = [
        'text/plain', 'text/markdown', 'text/csv',
        'application/json', 'application/x-yaml', 'text/yaml',
        'text/html', 'text/css', 'application/javascript',
        'text/x-python', 'application/x-python-code' // Common Python mimetypes
        // Add more as needed
    ];
    const maxSizeMB = 5; // Limit file size (e.g., 5MB)

    if (!allowedTypes.includes(file.type) && !file.name.match(/\.(txt|md|py|js|css|html|json|yaml|yml|csv)$/i)) {
         console.warn(`File type not explicitly allowed: ${file.type} / ${file.name}. Attempting to read anyway.`);
         // alert(`Unsupported file type: ${file.type || 'Unknown'}. Please select a text-based file.`);
         // clearSelectedFile(); // Don't clear, let user try
         // return;
    }

    if (file.size > maxSizeMB * 1024 * 1024) {
         alert(`File size exceeds the ${maxSizeMB}MB limit.`);
         clearSelectedFile();
         return;
    }


    selectedFile = file;
    console.log(`File selected: ${file.name}, Size: ${file.size}, Type: ${file.type}`);

    // Read file content
    const reader = new FileReader();
    reader.onload = function(e) {
        selectedFileContent = e.target.result;
        console.log(`File content loaded (first 100 chars): ${selectedFileContent.substring(0, 100)}...`);
        displayFileInfo(); // Update UI after content is read
    };
    reader.onerror = function(e) {
        console.error("Error reading file:", e);
        alert('Error reading file content.');
        clearSelectedFile();
    };
    reader.readAsText(file); // Read as text

    displayFileInfo(); // Update UI immediately (shows name while reading)
}

/**
 * Updates the file info display area.
 */
function displayFileInfo() {
    if (selectedFile) {
        fileInfoArea.innerHTML = `
            <span>Attached: ${selectedFile.name} (${(selectedFile.size / 1024).toFixed(1)} KB)</span>
            <button onclick="clearSelectedFile()" title="Clear attached file">×</button>
        `;
    } else {
        fileInfoArea.innerHTML = ''; // Clear the area
    }
}

/**
 * Clears the selected file state and updates the UI.
 */
function clearSelectedFile() {
    selectedFile = null;
    selectedFileContent = null;
    fileInput.value = ''; // Reset the file input element
    displayFileInfo();
    console.log("Cleared selected file.");
}

// --- Event Listeners ---

// Send message on button click
sendButton.addEventListener('click', sendMessage);

// Send message on Enter key press in textarea (Shift+Enter for newline)
messageInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault(); // Prevent default newline behavior
        sendMessage();
    }
});

// Trigger file input click when attach button is clicked
attachFileButton.addEventListener('click', () => {
    fileInput.click();
});

// Handle file selection
fileInput.addEventListener('change', handleFileSelect);

// --- Initialization ---

// Connect WebSocket when the script loads
connectWebSocket();

// Add initial status messages
addMessage({ type: 'status', message: 'Initializing interface...' }, systemLogArea);
clearAgentStatusUI(); // Display initial "Waiting..." message

// Initial fetch of configurations happens in websocket.onopen
// fetchAgentConfigurations(); // Moved to onopen
