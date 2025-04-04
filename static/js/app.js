// START OF FILE static/js/app.js

// --- State Variables ---
let socket;
const conversationHistory = []; // Array to store conversation messages ({ type: 'user'/'agent_response', ...data })
const systemLogHistory = []; // Array to store system log messages ({ type: 'status'/'error', ...data })
const agentStatusElements = {}; // { agent_id: element } - For status UI
let selectedFile = null; // To store the selected file object
let selectedFileContent = null; // To store read file content before sending

// --- DOM Elements ---
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const conversationArea = document.getElementById('conversation-area');
const systemLogArea = document.getElementById('system-log-area');
const agentStatusContent = document.getElementById('agent-status-content');
// File input elements
const fileInput = document.getElementById('file-input');
const attachFileButton = document.getElementById('attach-file-button');
const fileInfoArea = document.getElementById('file-info-area');


// --- Helper: Scroll Area to Bottom ---
function scrollToBottom(element) {
    if (element) {
        const scrollThreshold = 50; // Pixels from bottom
        if (element.scrollHeight - element.scrollTop <= element.clientHeight + scrollThreshold) {
            element.scrollTop = element.scrollHeight;
        }
    }
}

// --- WebSocket Connection ---
function connectWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    socket = new WebSocket(wsUrl);

    socket.onopen = function(event) {
        console.log("WebSocket connection opened:", event);
        const connectMsg = { type: "status", content: "WebSocket connected!" };
        systemLogHistory.push(connectMsg);
        addMessage(connectMsg, systemLogArea);
        sendButton.disabled = false;
        messageInput.disabled = false;
        attachFileButton.disabled = false; // Enable attach button
        clearAgentStatusUI();
    };

    socket.onmessage = function(event) {
        console.log("WebSocket message received:", event.data);
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'agent_status_update') {
                 updateAgentStatusUI(data);
            } else {
                let targetArea = systemLogArea;
                let targetHistory = systemLogHistory;
                if (data.type === 'user' || data.type === 'agent_response') {
                    targetArea = conversationArea;
                    targetHistory = conversationHistory;
                }
                targetHistory.push(data);
                addMessage(data, targetArea);
            }
        } catch (e) {
            console.error("Failed to parse incoming message or add message to UI:", e);
            const rawMsg = { type: "raw", content: `Raw: ${event.data}` };
            const errorMsg = { type: "error", content: `Error processing message: ${e.message}` };
            systemLogHistory.push(rawMsg);
            systemLogHistory.push(errorMsg);
            addMessage(rawMsg, systemLogArea);
            addMessage(errorMsg, systemLogArea);
        }
    };

    socket.onerror = function(event) {
        console.error("WebSocket error observed:", event);
        const errorMsg = { type: "error", content: "WebSocket connection error. Check console." };
        systemLogHistory.push(errorMsg);
        addMessage(errorMsg, systemLogArea);
        sendButton.disabled = true;
        messageInput.disabled = true;
        attachFileButton.disabled = true; // Disable attach on error
    };

    socket.onclose = function(event) {
        console.log("WebSocket connection closed:", event);
        const reason = event.reason || `Code ${event.code}`;
        const closeMsg = { type: "status", content: `WebSocket disconnected. ${reason}. Attempting to reconnect...` };
        systemLogHistory.push(closeMsg);
        addMessage(closeMsg, systemLogArea);
        sendButton.disabled = true;
        messageInput.disabled = true;
        attachFileButton.disabled = true; // Disable attach on close
        clearAgentStatusUI("Disconnected - Status unavailable");
        clearSelectedFile(); // Clear file selection on disconnect
        setTimeout(connectWebSocket, 5000);
    };
}

// --- Sending Messages ---
function sendMessage() {
    if (socket && socket.readyState === WebSocket.OPEN) {
        const messageText = messageInput.value.trim();

        // If no message text AND no file, do nothing
        if (!messageText && !selectedFile) {
            console.log("No message text or file selected. Nothing to send.");
            return;
        }

        // Disable inputs while processing/sending
        messageInput.disabled = true;
        sendButton.disabled = true;
        attachFileButton.disabled = true;

        // Function to actually send the final combined message
        const sendFinalMessage = (finalText) => {
            // 1. Create user message object (using the final text) and add to history
            const userMessage = { type: "user", content: finalText };
            conversationHistory.push(userMessage);

            // 2. Render the user message
            addMessage(userMessage, conversationArea);

            // 3. Send the final text over WebSocket
            socket.send(finalText);

            // 4. Clear input, file selection, and re-enable inputs
            messageInput.value = '';
            clearSelectedFile(); // Clear file state and UI
            messageInput.disabled = false;
            sendButton.disabled = false;
            attachFileButton.disabled = false;
        };

        // Check if a file is selected
        if (selectedFile) {
            const reader = new FileReader();

            reader.onload = function(e) {
                const fileContent = e.target.result;
                // Combine file content and message text
                const combinedText = `Attached File: ${selectedFile.name}\n\`\`\`\n${fileContent}\n\`\`\`\n\n${messageText}`;
                console.log(`Sending message with attached file ${selectedFile.name}`);
                sendFinalMessage(combinedText);
            };

            reader.onerror = function(e) {
                console.error("Error reading file:", e);
                const errorMsg = { type: "error", content: `Error reading file ${selectedFile.name}: ${e.target.error}` };
                systemLogHistory.push(errorMsg);
                addMessage(errorMsg, systemLogArea);
                // Re-enable inputs on error
                messageInput.disabled = false;
                sendButton.disabled = false;
                attachFileButton.disabled = false;
                clearSelectedFile(); // Clear file state on error
            };

            // Read the file as text
            reader.readAsText(selectedFile);

        } else {
            // No file selected, just send the message text
            console.log("Sending message without attached file.");
            sendFinalMessage(messageText);
        }

    } else {
        console.error("WebSocket is not connected.");
        const errorMsg = { type: "error", content: "Cannot send message: WebSocket not connected." };
        systemLogHistory.push(errorMsg);
        addMessage(errorMsg, systemLogArea);
    }
}

// --- Adding Messages to UI (Rendering Function - unchanged from previous step) ---
function addMessage(data, targetArea) {
    if (!targetArea) {
        console.error("Target message area not provided for message:", data);
        return;
    }
    const messageElement = document.createElement('div');
    messageElement.classList.add('message');
    let messageContent = data.content || data.message || '';
    const contentElement = document.createElement('span');
    contentElement.textContent = messageContent;

    switch (data.type) {
        case 'user':
            messageElement.classList.add('user');
            contentElement.textContent = `You: ${messageContent}`;
            messageElement.appendChild(contentElement);
            break;
        case 'agent_response':
            messageElement.classList.add('agent_response');
            const agentId = data.agent_id || 'unknown_agent';
            messageElement.dataset.agentId = agentId;
            const existingBlock = targetArea.querySelector(`.agent-message-block[data-agent-id="${agentId}"]`);
            if (existingBlock) {
                 const blockContentSpan = existingBlock.querySelector('.agent-content');
                 if (blockContentSpan) {
                     blockContentSpan.textContent += messageContent;
                     scrollToBottom(targetArea); return;
                 } else { existingBlock.appendChild(contentElement); }
            } else {
                messageElement.classList.add('agent-message-block');
                const agentPrefix = document.createElement('strong');
                agentPrefix.textContent = `${agentId}: `;
                contentElement.classList.add('agent-content');
                messageElement.appendChild(agentPrefix);
                messageElement.appendChild(contentElement);
            }
            break;
        case 'status':
            messageElement.classList.add('status');
            if (messageContent.toLowerCase().includes("executing tool:")) {
                 messageElement.classList.add('tool-execution');
            }
            if (data.agent_id && !messageContent.toLowerCase().includes("websocket")) {
                 contentElement.textContent = `[${data.agent_id}] ${messageContent}`;
            }
            messageElement.appendChild(contentElement);
            break;
        case 'error':
            messageElement.classList.add('error');
            if (data.agent_id) {
                 contentElement.textContent = `[${data.agent_id}] Error: ${messageContent}`;
            } else {
                 contentElement.textContent = `Error: ${messageContent}`;
            }
            messageElement.appendChild(contentElement);
            break;
        case 'echo': case 'raw': default:
            messageElement.classList.add('server');
            messageElement.appendChild(contentElement);
            break;
    }

    if (!document.body.dataset.placeholdersRemoved) {
         const placeholders = document.querySelectorAll('.initial-placeholder');
         placeholders.forEach(p => p.remove());
         document.body.dataset.placeholdersRemoved = true;
    }

    targetArea.appendChild(messageElement);
    scrollToBottom(targetArea);
}


// --- Agent Status UI (Remains the same) ---
function clearAgentStatusUI(message = "Waiting for status...") {
    if (agentStatusContent) {
        agentStatusContent.innerHTML = `<span class="status-placeholder">${message}</span>`;
    }
    Object.keys(agentStatusElements).forEach(key => delete agentStatusElements[key]);
}
function updateAgentStatusUI(data) {
    if (!agentStatusContent) return;
    const agentId = data.agent_id;
    const statusData = data.status;
    if (!agentId || !statusData) { console.warn("Incomplete status update:", data); return; }
    const placeholder = agentStatusContent.querySelector('.status-placeholder');
    if (placeholder) placeholder.remove();
    let agentElement = agentStatusElements[agentId];
    if (!agentElement) {
        agentElement = document.createElement('div');
        agentElement.classList.add('agent-status-item');
        agentElement.dataset.agentId = agentId;
        agentStatusContent.appendChild(agentElement);
        agentStatusElements[agentId] = agentElement;
    }
    let statusText = statusData.status || 'unknown';
    if (statusData.status === 'executing_tool' && statusData.current_tool) {
        statusText = `Executing Tool: ${statusData.current_tool.name}`;
    } else if (statusData.status === 'awaiting_tool_result') {
        statusText = 'Waiting for Tool Result';
    }
    agentElement.innerHTML = `
        <strong>${agentId}</strong>
        <span class="agent-model">(${statusData.model || 'N/A'})</span>:
        <span class="agent-status agent-status-${statusData.status || 'unknown'}">${statusText}</span>
    `;
    agentElement.className = 'agent-status-item';
    agentElement.classList.add(`status-${statusData.status || 'unknown'}`);
}

// --- File Handling ---
function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) {
        selectedFile = files[0];
        console.log("File selected:", selectedFile.name);
        displayFileInfo();
        // Clear the input value so the same file can be selected again after clearing
        fileInput.value = null;
    } else {
         clearSelectedFile();
    }
}

function displayFileInfo() {
    if (fileInfoArea && selectedFile) {
        fileInfoArea.innerHTML = `
            <span>📎 ${selectedFile.name}</span>
            <button id="clear-file-button" title="Clear selected file">✖</button>
        `;
        // Add event listener to the new clear button
        document.getElementById('clear-file-button').addEventListener('click', clearSelectedFile);
    } else if (fileInfoArea) {
        fileInfoArea.innerHTML = ''; // Clear the area if no file
    }
}

function clearSelectedFile() {
    selectedFile = null;
    fileInput.value = null; // Clear the file input element itself
    displayFileInfo(); // Update UI to show no file selected
    console.log("Selected file cleared.");
}


// --- Event Listeners ---
sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', function(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});
// Trigger hidden file input when attach button is clicked
attachFileButton.addEventListener('click', () => {
    fileInput.click();
});
// Handle file selection
fileInput.addEventListener('change', handleFileSelect);


// --- Initial Setup ---
sendButton.disabled = true;
messageInput.disabled = true;
attachFileButton.disabled = true; // Disable initially

// Add initial placeholders with specific classes
function addInitialPlaceholder(area, text, className) {
    if (area) {
        if (!area.querySelector(`.${className}`)) {
            const placeholder = document.createElement('div');
            placeholder.classList.add('message', 'status', className, 'initial-placeholder');
            placeholder.innerHTML = `<span>${text}</span>`;
            area.appendChild(placeholder);
        }
    } else { console.error("Target area not found:", text); }
}
if(conversationArea) conversationArea.innerHTML = '';
if(systemLogArea) systemLogArea.innerHTML = '';
addInitialPlaceholder(systemLogArea, "System Logs & Status", "initial-system-log");
addInitialPlaceholder(conversationArea, "Conversation Area", "initial-conversation");
addInitialPlaceholder(systemLogArea, "Connecting to backend...", "initial-connecting");
clearAgentStatusUI(); // Initialize status area
displayFileInfo(); // Ensure file info area is initially empty
connectWebSocket();
