// START OF FILE static/js/ui.js

let currentStreamingMessageElements = {}; // Added module-level variable

import { escapeHTML, getCurrentTimestamp } from './utils.js';
import * as config from './config.js';
import * as DOM from './domElements.js'; // Import all exported elements
import * as state from './state.js'; // Import state getter/setters

/**
 * Displays a message in the specified message area (conversation or internal comms).
 * Handles auto-scrolling, message limits, and groups response chunks in internal comms
 * even during concurrent streaming from multiple agents.
 * @param {string} text The message content (can be HTML, should be pre-escaped if needed).
 * @param {string} type Message type class (e.g., 'user', 'agent_response', 'status', 'response_chunk').
 * @param {string} targetAreaId ID of the container ('conversation-area' or 'internal-comms-area').
 * @param {string} [agentId=null] Optional agent ID.
 * @param {string} [agentPersona=null] Optional agent persona.
 */
export const displayMessage = (text, type, targetAreaId, agentId = null, agentPersona = null) => {
    console.debug(`UI: Display request in #${targetAreaId}. Type: ${type}, Agent: ${agentId}`);
    try {
        const messageArea = DOM[targetAreaId === 'conversation-area' ? 'conversationArea' : 'internalCommsArea'];
        if (!messageArea) {
            console.error(`UI Error: Target message area #${targetAreaId} not found! Cannot display message.`);
            return;
        }
        // console.debug(`Target area #${targetAreaId} found.`); // Less verbose

        const placeholder = messageArea.querySelector('.initial-placeholder');
        if (placeholder) {
            placeholder.remove();
        }

        let messageToAppendTo = null;

        // --- NEW Logic for grouping response chunks using currentStreamingMessageElements ---
        if (type === 'response_chunk' && targetAreaId === 'internal-comms-area' && agentId) {
            messageToAppendTo = currentStreamingMessageElements[agentId];

            if (messageToAppendTo) {
                // Validate the tracked element
                const stillInDom = document.body.contains(messageToAppendTo);
                const stillChunk = messageToAppendTo.classList.contains('response_chunk');
                if (!stillInDom || !stillChunk) {
                    console.log(`UI_CHUNK_DEBUG: Tracked element for ${agentId} is no longer valid (InDOM: ${stillInDom}, IsChunk: ${stillChunk}). ID: ${messageToAppendTo.getAttribute('data-message-id') || 'N/A'}`);
                    messageToAppendTo = null;
                    currentStreamingMessageElements[agentId] = null; // Clear invalid tracking
                } else {
                    console.log(`UI_CHUNK_DEBUG: Using valid tracked element for ${agentId}. ID: ${messageToAppendTo.getAttribute('data-message-id')}`);
                }
            } else {
                 console.log(`UI_CHUNK_DEBUG: No actively tracked streaming element for ${agentId}. Will create new or find previous.`);
                 // Fallback to previous logic if no actively tracked element (e.g., after page reload or if logic was different)
                 // This also covers the case where an agent might have multiple "streams" if not cleared properly.
                 // For now, we want the new logic to be primary for active streams.
                 // The old debug logs can remain for comparison during testing.
                const agentMessages = messageArea.querySelectorAll(`.message[data-agent-id="${agentId}"]`);
                const lastAgentMessage = agentMessages.length > 0 ? agentMessages[agentMessages.length - 1] : null;

                if (lastAgentMessage) {
                    console.log(`UI_CHUNK_DEBUG (Fallback): Found lastAgentMessage for ${agentId}. ID: ${lastAgentMessage.getAttribute('data-message-id') || 'N/A'}, Classes: ${lastAgentMessage.className}, HTML: ${lastAgentMessage.outerHTML.substring(0, 150)}`);
                    console.log(`UI_CHUNK_DEBUG (Fallback): lastAgentMessage.classList.contains('response_chunk') is ${lastAgentMessage.classList.contains('response_chunk')}`);
                    if (lastAgentMessage.classList.contains('response_chunk')) {
                        // messageToAppendTo = lastAgentMessage; // Do not set here, let new element be created and tracked
                        console.log(`UI_CHUNK_DEBUG (Fallback): lastAgentMessage for ${agentId} is a chunk, but new tracking logic will create a new element as primary.`);
                    }
                } else {
                    console.log(`UI_CHUNK_DEBUG (Fallback): No lastAgentMessage found for ${agentId}.`);
                }
            }
        }
        // --- End NEW chunk grouping logic ---

        // If any message for internal-comms with an agentId is NOT a response_chunk,
        // or if it IS a response_chunk BUT we are about to create a new element for it (messageToAppendTo is null),
        // then we should clear the tracked streaming element for that agent.
        if (targetAreaId === 'internal-comms-area' && agentId) {
            if (type !== 'response_chunk' || !messageToAppendTo) {
                if (currentStreamingMessageElements[agentId]) {
                    console.log(`UI_CHUNK_DEBUG: Clearing tracked stream element for ${agentId} due to new message type '${type}' or new chunk element creation.`);
                    currentStreamingMessageElements[agentId] = null;
                }
            }
        }


        if (messageToAppendTo) { // This means we are appending to an existing, valid, tracked element
            console.log(`UI_CHUNK_DEBUG: Appending text to tracked stream element for ${agentId}. ID: ${messageToAppendTo.getAttribute('data-message-id')}`);
            const contentSpan = messageToAppendTo.querySelector('.message-content');
            if (contentSpan) {
                // Append text content
                contentSpan.textContent += text;
            } else {
                console.warn("UI: Could not find content span in last message element to append chunk. Creating new message.");
                messageToAppendTo = null; // Fallback to creating a new message
            }
        }

        // If not appending, create a new message element
        if (!messageToAppendTo) {
             console.debug(`UI: Creating new message element for type ${type} in #${targetAreaId}.`);
             // Determine max messages based on the target area
            const maxMessages = targetAreaId === 'conversation-area' ? config.MAX_CHAT_MESSAGES : config.MAX_COMM_MESSAGES;
            while (messageArea.children.length >= maxMessages) {
                console.debug(`Trimming messages in #${targetAreaId}`);
                messageArea.removeChild(messageArea.firstChild);
            }

            const messageElement = document.createElement('div');
            messageElement.classList.add('message', type); // Use original type for class
            // Add unique message ID
            messageElement.setAttribute('data-message-id', `msg-${Date.now()}-${Math.floor(Math.random() * 1000)}`);
            if (agentId) {
                messageElement.setAttribute('data-agent-id', agentId);
                // Keep specific class for agent responses in conversation for potential specific styling
                if (type === 'agent_response' && targetAreaId === 'conversation-area') {
                    messageElement.classList.add('agent_response');
                }
                // --- Apply border styling for internal comms here ---
                if (targetAreaId === 'internal-comms-area') {
                    // Use styles defined in CSS instead of inline styles
                    messageElement.classList.add(`agent-border-${agentId.split('_')[0] || agentId}`); // Add class like agent-border-admin or agent-border-agent
                     // Add specific classes for common agent types if needed for backgrounds etc.
                     if (agentId === 'admin_ai') { messageElement.classList.add('agent-admin'); }
                     else if (agentId.includes('coder')) { messageElement.classList.add('agent-coder'); }
                     // Add more as needed
                }
                // --- End border styling ---
            }

            const timestampSpan = (targetAreaId === 'internal-comms-area')
                ? `<span class="timestamp">${getCurrentTimestamp()}</span>`
                : '';

            let innerHTMLContent = timestampSpan;

            // Add Agent Label based on target area and type
             if (targetAreaId === 'internal-comms-area') {
                 // Show detailed labels in internal comms
                 if (agentPersona) {
                     innerHTMLContent += `<span class="agent-label">${escapeHTML(agentPersona)} (${escapeHTML(agentId)}):</span>`;
                 } else if (agentId && !['system', 'api', 'frontend', 'manager', 'human_user'].includes(agentId)) {
                     innerHTMLContent += `<span class="agent-label">Agent (${escapeHTML(agentId)}):</span>`;
                 } else if (agentId) { // Handle system/manager/etc.
                      innerHTMLContent += `<span class="agent-label">${escapeHTML(agentId.replace(/_/g,' ').toUpperCase())}:</span>`;
                 }
             } else if (targetAreaId === 'conversation-area') {
                 // Only show persona for agent responses in chat
                 if (type === 'agent_response' && agentPersona) {
                     innerHTMLContent += `<span class="agent-label">${escapeHTML(agentPersona)}:</span>`;
                 }
                 // User messages don't need a label here
             }

            // Append the actual message content
            const contentSpan = document.createElement('span');
            contentSpan.classList.add('message-content');
            if (type === 'response_chunk') {
                 contentSpan.textContent = text; // Set initial text content for the first chunk
            } else {
                 contentSpan.innerHTML = text; // Use innerHTML for other types that might contain pre-formatted HTML
            }
            messageElement.innerHTML = innerHTMLContent; // Add timestamp and label first
            messageElement.appendChild(contentSpan); // Append the content span

            messageArea.appendChild(messageElement);

            // If this new element is a response_chunk in internal comms, track it.
            if (type === 'response_chunk' && targetAreaId === 'internal-comms-area' && agentId) {
                currentStreamingMessageElements[agentId] = messageElement;
                console.log(`UI_CHUNK_DEBUG: NEW stream element created and TRACKED for ${agentId}. ID: ${messageElement.getAttribute('data-message-id')}`);
            }
            // console.debug(`Message appended to #${targetAreaId}.`); // Less verbose
        }

        // Auto-scroll only if the user isn't scrolled up significantly
        const isScrolledNearBottom = messageArea.scrollHeight - messageArea.scrollTop - messageArea.clientHeight < 150; // Check if near bottom
        if (isScrolledNearBottom) {
            messageArea.scrollTop = messageArea.scrollHeight;
            // console.debug(`Auto-scrolled #${targetAreaId}`);
        } else {
            // console.debug(`User scrolled up in #${targetAreaId}, not auto-scrolling.`);
        }


    } catch (error) {
        console.error(`UI Error in displayMessage (Target: ${targetAreaId}, Type: ${type}):`, error);
        // Attempt to display error in internal comms area as a fallback
        try {
             if (DOM.internalCommsArea) {
                const errorEl = document.createElement('div');
                errorEl.className = 'message error';
                errorEl.innerHTML = `<span class="timestamp">${getCurrentTimestamp()}</span><span class="message-content">!! JS Error displaying message (Type: ${type}, Target: ${targetAreaId}). Check console. !!</span>`;
                DOM.internalCommsArea.appendChild(errorEl);
                DOM.internalCommsArea.scrollTop = DOM.internalCommsArea.scrollHeight;
             }
        } catch (fallbackError) {
            console.error("UI Fallback error display failed:", fallbackError);
        }
    }
};


/**
 * Displays a status message specifically in the specified target area.
 * @param {string} message The status text.
 * @param {boolean} [temporary=false] If true, message might be removed later (currently unused).
 * @param {boolean} [isError=false] If true, style as an error.
 * @param {string} [targetAreaId='internal-comms-area'] Target area ID.
 */
export const displayStatusMessage = (message, temporary = false, isError = false, targetAreaId = 'internal-comms-area') => {
    const messageType = isError ? 'error' : 'status';
    // Ensure status messages are escaped as they come from internal system events primarily
    displayMessage(escapeHTML(message), messageType, targetAreaId, 'system');
};


/**
 * Updates the agent status list UI in the Chat View based on the *cached* known statuses.
 * Should be called after the state cache is updated.
 */
export const updateAgentStatusUI = () => {
    if (!DOM.agentStatusContent) {
        console.warn("UI: Agent status container not found for update.");
        return;
    }

    const agentStatusData = state.getKnownAgentStatuses(); // Get the cached data
    console.debug("UI: Updating agent status list from cached state", agentStatusData);

    try {
        DOM.agentStatusContent.innerHTML = ''; // Clear previous content
        const agentIds = Object.keys(agentStatusData);

        if (agentIds.length === 0) {
            DOM.agentStatusContent.innerHTML = '<span class="status-placeholder">No active agents known.</span>';
            return;
        }

        // Sort admin_ai first, then alphabetically
        agentIds.sort((a, b) => {
            if (a === 'admin_ai') return -1;
            if (b === 'admin_ai') return 1;
            return a.localeCompare(b);
        });

        agentIds.forEach(agentId => {
            const agent = agentStatusData[agentId];
            // Skip rendering if status somehow became 'deleted' in cache (shouldn't happen with new state logic, but safe)
            if (!agent || agent.status === 'deleted') {
                 console.debug(`UI: Skipping deleted/missing agent ${agentId} during render.`);
                 return;
            }

            const statusItem = document.createElement('div');
            // Use 'idle' as default if status is missing, replace spaces for class name
            const statusValue = (agent.status || 'idle').replace(/ /g, '_');
            const statusClass = `status-${statusValue}`;
            statusItem.classList.add('agent-status-item', statusClass);
            statusItem.setAttribute('data-agent-id', agentId);

            const persona = agent.persona || agentId;
            const modelDisplay = agent.model ? `(${escapeHTML(agent.model)})` : '(Model N/A)';
            const teamInfo = agent.team ? `<span class="agent-team">[${escapeHTML(agent.team)}]</span>` : '';

            const agentInfoSpan = document.createElement('span');
            // Display relevant info: Persona, Model, Team
            agentInfoSpan.innerHTML = `<strong>${escapeHTML(persona)}</strong> <span class="agent-model">${modelDisplay}</span> ${teamInfo}`;

            const statusBadgeSpan = document.createElement('span');
            statusBadgeSpan.classList.add('agent-status');
            statusBadgeSpan.textContent = agent.status || 'idle'; // Default to idle if missing

            statusItem.appendChild(agentInfoSpan);
            statusItem.appendChild(statusBadgeSpan);
            DOM.agentStatusContent.appendChild(statusItem);
        });
        // console.debug("UI: Agent status list updated from cache."); // Less verbose
    } catch (error) {
        console.error("UI Error updating agent status UI:", error);
        if(DOM.agentStatusContent) DOM.agentStatusContent.innerHTML = '<span class="status-placeholder">Error updating agent status.</span>';
    }
};


/**
 * Switches the active view panel in the UI.
 * @param {string} viewId The ID of the view panel to activate.
 */
export const switchView = (viewId) => {
    console.log(`UI: Attempting to switch view to: ${viewId}`);
    const targetPanel = document.getElementById(viewId);
    if (!targetPanel) {
        console.error(`UI Error: Cannot switch view. Element with ID '${viewId}' not found.`);
        return;
    }
    // Ensure DOM elements have been assigned
    if (!DOM.viewPanels || DOM.viewPanels.length === 0) {
        console.error("UI Error: viewPanels not assigned or empty. Cannot switch view.");
        return;
    }
     if (!DOM.navButtons || DOM.navButtons.length === 0) {
        console.error("UI Error: navButtons not assigned or empty. Cannot switch view.");
        return;
    }

    // Deactivate all panels
    DOM.viewPanels.forEach(panel => {
        panel.classList.remove('active');
    });
    // Activate the target panel
    targetPanel.classList.add('active');

    // Update navigation button states
    DOM.navButtons.forEach(button => {
        button.classList.remove('active');
        if (button.getAttribute('data-view') === viewId) {
            button.classList.add('active');
        }
    });

    state.setCurrentView(viewId); // Update shared state
    console.log(`UI: View switched successfully to: ${viewId}`);

    // Trigger data loading for the newly activated view
    // Using dynamic imports to avoid circular dependencies if modules call each other
    if (viewId === 'config-view' && DOM.configContent) {
        console.log("UI: Triggering loadStaticAgentConfig for config-view...");
        import('./configView.js')
            .then(module => module.loadStaticAgentConfig())
            .catch(err => console.error("UI Error importing configView.js:", err));
    } else if (viewId === 'session-view' && DOM.projectSelect) {
         console.log("UI: Triggering loadProjects for session-view...");
         import('./session.js')
            .then(module => module.loadProjects())
            .catch(err => console.error("UI Error importing session.js:", err));
    }
};

/**
 * Opens the specified modal dialog.
 * @param {string} modalId The ID of the modal element to open.
 */
export const openModal = (modalId) => {
    const modal = document.getElementById(modalId);
    if (modal) {
        console.log(`UI: Opening modal #${modalId}`);
        modal.style.display = 'block';
    } else {
        console.error(`UI Error: Modal with ID #${modalId} not found.`);
    }
};

/**
 * Closes the specified modal dialog.
 * @param {string} modalId The ID of the modal element to close.
 */
export const closeModal = (modalId) => {
    const modal = document.getElementById(modalId);
    if (modal) {
        console.log(`UI: Closing modal #${modalId}`);
        modal.style.display = 'none';
    } else {
        console.warn(`UI Warning: Modal with ID #${modalId} not found during close attempt.`);
    }
};

/**
 * Updates the UI element displaying attached file information.
 * @param {{name: string}} [fileData=null] File object or null to clear.
 */
export const displayFileInfo = (fileData = null) => {
    if (!DOM.fileInfoArea) return;
    if (fileData && fileData.name) {
        DOM.fileInfoArea.innerHTML = `
            <span>Attached: ${escapeHTML(fileData.name)}</span>
            <button id="clear-attachment-btn" title="Remove file">×</button>
        `;
        DOM.fileInfoArea.style.display = 'flex';
        // Add listener dynamically, import handler
        const clearBtn = document.getElementById('clear-attachment-btn');
        if(clearBtn) {
            import('./handlers.js')
                .then(module => {
                     // Remove previous listener if any to avoid duplicates
                     clearBtn.onclick = null;
                     clearBtn.onclick = module.handleClearAttachment;
                 })
                 .catch(err => console.error("Failed to import handlers for clear attachment button:", err));
        }
    } else {
        DOM.fileInfoArea.style.display = 'none';
        DOM.fileInfoArea.innerHTML = '';
    }
};

/**
 * Displays a status message in the session management view.
 * @param {string} message The message text.
 * @param {boolean} isSuccess True for success style, false for error style.
 */
export const displaySessionStatus = (message, isSuccess) => {
    if (!DOM.sessionStatusMessage) return;
    console.log(`UI: Displaying session status (Success: ${isSuccess}): ${message}`);
    DOM.sessionStatusMessage.textContent = message;
    DOM.sessionStatusMessage.className = isSuccess ? 'session-status success' : 'session-status error';
    DOM.sessionStatusMessage.style.display = 'block';
    // Set timeout to hide the message
    setTimeout(() => {
        if (DOM.sessionStatusMessage) DOM.sessionStatusMessage.style.display = 'none';
    }, 5000); // Hide after 5 seconds
};


console.log("Frontend UI module loaded.");
