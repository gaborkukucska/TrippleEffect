// START OF FILE static/js/app.js

let websocket;
// Cache DOM elements
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');
const configContentArea = document.getElementById('config-content'); // Added for config display
const agentStatusContent = document.getElementById('agent-status-content'); // Added for agent status

// Cache for agent response elements to append chunks
let agentResponseElements = {}; // { "agent_id": { element: <div_element>, timeoutId: null } }
// Cache for agent status elements
let agentStatusElements = {}; // { "agent_id": <div_element> }

// Client-side history (simple array for demonstration)
let conversationHistory = [];
let selectedFile = null; // To store the selected file object

// --- Helper Functions ---

// Function to scroll to the bottom of an element if user is near the bottom
function scrollToBottom(element) {
    const threshold = 50; // Pixels from bottom
    const isNearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < threshold;
    // console.log(`Scroll Info: ScrollHeight=${element.scrollHeight}, ScrollTop=${element.scrollTop}, ClientHeight=${element.clientHeight}, NearBottom=${isNearBottom}`);
    if (isNearBottom) {
        // Use setTimeout to allow the DOM to update before scrolling
        setTimeout(() => {
            element.scrollTop = element.scrollHeight;
            // console.log(`Scrolled ${element.id} to bottom.`);
        }, 0);
    }
}

// --- WebSocket Handling ---

function connectWebSocket() {
    // Determine WebSocket protocol (ws or wss)
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    websocket = new WebSocket(wsUrl);

    websocket.onopen = (event) => {
        console.log("WebSocket connection opened");
        // Clear initial connecting message and show connected status
        const connectingMsg = systemLogArea.querySelector('.initial-connecting');
        if (connectingMsg) connectingMsg.remove();
        addMessage({ type: "status", message: "WebSocket Connected!" }, systemLogArea);
        // Enable input/send button
        messageInput.disabled = false;
        sendButton.disabled = false;
        attachFileButton.disabled = false;
        // Fetch config and initial agent status on connect
        fetchAndDisplayConfig();
        // Request initial status (optional, as backend might push on connect)
        // You might need a specific message type for this if implemented
    };

    websocket.onmessage = (event) => {
        // console.log("WebSocket message received:", event.data);
        try {
            const data = JSON.parse(event.data);

            // Route message based on type
            switch (data.type) {
                case 'agent_response':
                case 'user': // Shouldn't receive user messages back, but handle just in case
                    addMessage(data, conversationArea); // Add to conversation area
                    break;
                case 'status':
                case 'error':
                     // Add errors/status to system log area
                    addMessage(data, systemLogArea);
                    // Also display errors prominently in conversation area if they are agent-specific
                    if (data.type === 'error' && data.agent_id && data.agent_id !== 'manager') {
                         addMessage({ ...data, is_error_duplicate: true }, conversationArea); // Mark as duplicate
                    }
                    break;
                 case 'agent_status_update':
                      updateAgentStatusUI(data.status); // Pass the nested status object
                      break;
                // Handle other potential message types if needed
                default:
                    console.warn("Received unknown message type:", data.type, data);
                    // Add to system log as generic message
                    addMessage({ type: "status", agent_id: "system", message: `Received unhandled type '${data.type}': ${JSON.stringify(data)}` }, systemLogArea);
            }
        } catch (e) {
            console.error("Failed to parse WebSocket message or handle UI update:", e);
             addMessage({ type: "error", agent_id: "system", content: `System Error: Failed to process message from backend. ${e}` }, systemLogArea);
        }
    };

    websocket.onerror = (event) => {
        console.error("WebSocket error:", event);
        addMessage({ type: "error", agent_id: "system", content: "WebSocket connection error. Check the backend server and browser console." }, systemLogArea);
        // Disable input
        messageInput.disabled = true;
        sendButton.disabled = true;
        attachFileButton.disabled = true;
    };

    websocket.onclose = (event) => {
        console.log("WebSocket connection closed:", event.reason, `Code: ${event.code}`);
        const reason = event.reason || `Code ${event.code}`;
        addMessage({ type: "status", message: `WebSocket Disconnected (${reason}). Attempting to reconnect...` }, systemLogArea);
        // Disable input
        messageInput.disabled = true;
        sendButton.disabled = true;
        attachFileButton.disabled = true;
        // Reset agent status UI
        clearAgentStatusUI("Connection closed. Status unknown.");
        // Attempt to reconnect after a delay
        setTimeout(connectWebSocket, 5000); // Reconnect every 5 seconds
    };
}

// --- Sending Messages ---

function sendMessage() {
    const messageText = messageInput.value.trim();

    // Handle file content if a file is selected
    if (selectedFile) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const fileContent = e.target.result;
            let combinedMessage;
            // Add clear markers for the LLM
            combinedMessage = `--- START OF FILE: ${selectedFile.name} ---\n${fileContent}\n--- END OF FILE: ${selectedFile.name} ---\n\nUser Task:\n${messageText}`;

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                 console.log("Sending message with file context:", combinedMessage.substring(0, 100) + "...");
                 // Add user message to local history and UI immediately
                 const userMessage = { type: "user", content: combinedMessage }; // Show combined in UI? Or just user text? Let's show combined for clarity.
                 conversationHistory.push(userMessage);
                 addMessage(userMessage, conversationArea);

                websocket.send(combinedMessage); // Send combined message over WebSocket
                messageInput.value = ''; // Clear input field
                clearSelectedFile(); // Clear the selected file state and UI
            } else {
                console.error("WebSocket is not connected.");
                addMessage({ type: "error", content: "Cannot send message: WebSocket not connected." }, systemLogArea);
            }
        };
        reader.onerror = (e) => {
             console.error("Error reading file:", e);
             addMessage({ type: "error", content: `Error reading file ${selectedFile.name}.` }, systemLogArea);
             clearSelectedFile(); // Clear file state on error
        };
        reader.readAsText(selectedFile); // Read the file as text
    } else {
        // No file selected, send only the text message
        if (messageText && websocket && websocket.readyState === WebSocket.OPEN) {
             console.log("Sending message:", messageText);
             // Add user message to local history and UI immediately
             const userMessage = { type: "user", content: messageText };
             conversationHistory.push(userMessage);
             addMessage(userMessage, conversationArea);

            websocket.send(messageText); // Send plain text message over WebSocket
            messageInput.value = ''; // Clear input field
        } else if (!messageText) {
             console.warn("Attempted to send empty message.");
        } else {
            console.error("WebSocket is not connected.");
             addMessage({ type: "error", content: "Cannot send message: WebSocket not connected." }, systemLogArea);
        }
    }
    messageInput.focus(); // Keep focus on input
}


// --- UI Updates ---

function addMessage(data, targetArea) {
    // Remove initial placeholders if they exist in the target area
    const initialPlaceholders = targetArea.querySelectorAll('.initial-placeholder');
    initialPlaceholders.forEach(p => p.remove());

    const messageElement = document.createElement('div');
    messageElement.classList.add('message');
    messageElement.dataset.timestamp = new Date().toISOString(); // Add timestamp data attribute

    let messageContent = '';
    let agentId = data.agent_id || 'unknown'; // Default agent ID

    switch (data.type) {
        case 'user':
            messageElement.classList.add('user');
            // Simple text display, escaping HTML special chars
            messageContent = document.createElement('span');
            messageContent.textContent = data.content;
            messageElement.appendChild(messageContent);
            break;

        case 'agent_response':
            messageElement.classList.add('agent_response');
            messageElement.dataset.agentId = agentId; // Associate with agent ID for styling/grouping

            const agentKey = agentId;
            let existingAgentElementInfo = agentResponseElements[agentKey];

            // Check if we should append to an existing element for this agent
            if (existingAgentElementInfo && existingAgentElementInfo.element.parentNode === targetArea) {
                // Append content, reset timeout
                const span = document.createElement('span'); // Wrap chunk in span
                span.textContent = data.content;
                existingAgentElementInfo.element.appendChild(span);
                messageElement = existingAgentElementInfo.element; // Use existing element

                // Reset removal timeout
                if (existingAgentElementInfo.timeoutId) {
                    clearTimeout(existingAgentElementInfo.timeoutId);
                }
                 existingAgentElementInfo.timeoutId = setTimeout(() => {
                      console.log(`Timeout expired for agent ${agentKey}, finalizing element.`);
                      delete agentResponseElements[agentKey]; // Remove from cache after timeout
                 }, 3000); // 3 seconds of inactivity

            } else {
                // Create a new element for this agent's response stream
                const span = document.createElement('span'); // Wrap first chunk
                span.textContent = data.content;
                messageElement.appendChild(span);
                targetArea.appendChild(messageElement); // Add to DOM first

                // Store the new element and set timeout
                agentResponseElements[agentKey] = {
                     element: messageElement,
                     timeoutId: setTimeout(() => {
                         console.log(`Timeout expired for agent ${agentKey}, finalizing element.`);
                         delete agentResponseElements[agentKey]; // Remove from cache after timeout
                     }, 3000) // 3 seconds of inactivity
                };
            }
            break; // agent_response handling ends here

        case 'status':
            messageElement.classList.add('status');
             // Add agent ID if present (e.g., for tool execution status)
            if (agentId && agentId !== 'system' && agentId !== 'unknown') {
                messageElement.dataset.agentId = agentId;
                 // Check for tool execution hints
                 if (data.message && (data.message.toLowerCase().includes('executing tool') || data.message.toLowerCase().includes('awaiting tool'))) {
                    messageElement.classList.add('tool-execution');
                }
                messageContent = `[${agentId}] ${data.message || data.content || 'Status update'}`;
            } else {
                 messageContent = data.message || data.content || 'Status update'; // Use message or content field
            }
             const statusSpan = document.createElement('span');
             statusSpan.textContent = messageContent;
             messageElement.appendChild(statusSpan);
            break;

        case 'error':
             messageElement.classList.add('error');
             messageElement.dataset.agentId = agentId; // Associate error with agent/manager
             // Prefix error with agent ID if available and not a duplicate conversation msg
             const errorPrefix = (agentId !== 'system' && agentId !== 'unknown' && !data.is_error_duplicate) ? `[${agentId} ERROR] ` : '[ERROR] ';
             messageContent = errorPrefix + (data.content || data.message || 'Unknown error');

             const errorSpan = document.createElement('span');
             errorSpan.textContent = messageContent;
             messageElement.appendChild(errorSpan);
            break;

        default:
            console.warn("Unknown message type in addMessage:", data.type);
            messageElement.classList.add('status'); // Treat as status
            messageContent = `[System] Unknown msg type '${data.type}': ${JSON.stringify(data.content || data.message || data)}`;
            const defaultSpan = document.createElement('span');
            defaultSpan.textContent = messageContent;
            messageElement.appendChild(defaultSpan);
    }

    // Append only if it's not an existing agent response element being updated
    if (data.type !== 'agent_response' || !agentResponseElements[agentId] || agentResponseElements[agentId].element !== messageElement) {
         if (messageElement.innerHTML.trim() !== '') { // Avoid adding empty messages
              targetArea.appendChild(messageElement);
         }
    }

    // Scroll the target area down
    scrollToBottom(targetArea);
}

function clearAgentStatusUI(message = "Status area cleared.") {
    if (!agentStatusContent) return;
    agentStatusContent.innerHTML = ''; // Clear existing content
    agentStatusElements = {}; // Clear the cache

    // Add a placeholder message
    const placeholder = document.createElement('span');
    placeholder.classList.add('status-placeholder');
    placeholder.textContent = message;
    agentStatusContent.appendChild(placeholder);
}

function updateAgentStatusUI(statusData) {
    if (!agentStatusContent) {
        console.error("Agent status content area not found.");
        return;
    }
    // console.log("Updating Agent Status UI with data:", statusData); // Debug log

    // Clear placeholder if it exists
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();

    const agentId = statusData.agent_id;
    if (!agentId) {
        console.warn("Received agent status update without agent_id:", statusData);
        return;
    }

    let agentElement = agentStatusElements[agentId];

    // Create element if it doesn't exist
    if (!agentElement) {
        agentElement = document.createElement('div');
        agentElement.classList.add('agent-status-item');
        agentElement.dataset.agentId = agentId;
        agentStatusContent.appendChild(agentElement);
        agentStatusElements[agentId] = agentElement; // Cache it
    }

    // Determine status class
    const statusClass = `status-${(statusData.status || 'unknown').replace(/[\s:]+/g, '_').toLowerCase()}`; // Normalize status string for class name
    // Remove previous status classes and add the new one
    agentElement.className = 'agent-status-item'; // Reset classes
    agentElement.classList.add(statusClass); // Add the specific status class

    // Build inner HTML for the status item
    let toolInfo = '';
    if (statusData.status === 'executing_tool' && statusData.current_tool) {
        toolInfo = ` (Tool: ${statusData.current_tool.name} [${statusData.current_tool.call_id}])`;
    } else if (statusData.status === 'awaiting_tool_result') {
         toolInfo = ` (Awaiting Tool Result)`;
    }

    agentElement.innerHTML = `
        <strong>${statusData.persona || agentId}</strong>
        <span class="agent-model">(${statusData.provider}/${statusData.model})</span>:
        <span class="agent-status">${statusData.status || 'unknown'}</span>
        <span class="agent-tool-info">${toolInfo}</span>
    `;
}


// --- Configuration Display ---

async function fetchAndDisplayConfig() {
    if (!configContentArea) {
        console.error("Configuration content area not found in the DOM.");
        return;
    }
    configContentArea.innerHTML = '<span class="status-placeholder">Loading config...</span>'; // Show loading state

    try {
        const response = await fetch('/api/config/agents');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const agentConfigs = await response.json();

        configContentArea.innerHTML = ''; // Clear loading state

        if (agentConfigs.length === 0) {
            configContentArea.innerHTML = '<span class="status-placeholder">No agents configured.</span>';
            return;
        }

        const list = document.createElement('ul');
        list.style.listStyle = 'none'; // Basic styling
        list.style.padding = '0';
        list.style.margin = '0';

        agentConfigs.forEach(agent => {
            const listItem = document.createElement('li');
            listItem.style.marginBottom = '5px';
             listItem.style.fontSize = '0.9em';
            listItem.innerHTML = `
                <strong>${agent.agent_id}</strong> (${agent.persona || 'N/A'}):
                <span style="color: #555;">[${agent.provider} / ${agent.model}]</span>
            `;
            list.appendChild(listItem);
        });
        configContentArea.appendChild(list);

    } catch (error) {
        console.error("Error fetching or displaying agent configurations:", error);
        configContentArea.innerHTML = `<span class="error">Error loading configuration: ${error.message}</span>`;
    }
}


// --- File Handling ---

function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) {
        const file = files[0];
        // Basic validation (optional: add more checks like size, specific types)
        if (file.type.startsWith('text/') || /\.(py|js|html|css|md|json|yaml|csv)$/i.test(file.name)) {
             selectedFile = file;
             displayFileInfo();
        } else {
             alert(`File type "${file.type}" may not be suitable. Please select a text-based file (.txt, .py, .js, .html, .css, .md, .json, .yaml, .csv).`);
             fileInput.value = ''; // Clear the selection
             selectedFile = null;
             displayFileInfo();
        }
    } else {
        selectedFile = null;
         displayFileInfo();
    }
}

function displayFileInfo() {
    if (!fileInfoArea) return;
    if (selectedFile) {
        fileInfoArea.innerHTML = `
            <span>Attached: ${selectedFile.name} (${(selectedFile.size / 1024).toFixed(1)} KB)</span>
            <button onclick="clearSelectedFile()" title="Clear attached file">❌</button>
        `;
    } else {
        fileInfoArea.innerHTML = ''; // Clear the area
    }
}

function clearSelectedFile() {
    selectedFile = null;
    fileInput.value = ''; // Reset the file input element
    displayFileInfo(); // Update the UI
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

// Trigger hidden file input when attach button is clicked
attachFileButton.addEventListener('click', () => {
    fileInput.click();
});

// Handle file selection change
fileInput.addEventListener('change', handleFileSelect);


// --- Initial Setup ---

// Disable input until WebSocket connects
messageInput.disabled = true;
sendButton.disabled = true;
attachFileButton.disabled = true;

// Initialize WebSocket connection on load
connectWebSocket();
