// START OF FILE static/js/ui.js

let currentStreamingMessageElements = {}; // Added module-level variable
let teamToggleStates = {}; // Track open/close state for teams

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

            // Create structured content for internal comms vs conversation
            if (targetAreaId === 'internal-comms-area') {
                // For internal comms, create a column layout with separate lines
                const timestampDiv = document.createElement('div');
                timestampDiv.classList.add('timestamp');
                timestampDiv.textContent = getCurrentTimestamp();
                messageElement.appendChild(timestampDiv);

                // Add Agent Label
                if (agentPersona) {
                    const agentLabelDiv = document.createElement('div');
                    agentLabelDiv.classList.add('agent-label');
                    agentLabelDiv.textContent = `${agentPersona} (${agentId}):`;
                    messageElement.appendChild(agentLabelDiv);
                } else if (agentId && !['system', 'api', 'frontend', 'manager', 'human_user'].includes(agentId)) {
                    const agentLabelDiv = document.createElement('div');
                    agentLabelDiv.classList.add('agent-label');
                    agentLabelDiv.textContent = `Agent (${agentId}):`;
                    messageElement.appendChild(agentLabelDiv);
                } else if (agentId) {
                    const agentLabelDiv = document.createElement('div');
                    agentLabelDiv.classList.add('agent-label');
                    agentLabelDiv.textContent = `${agentId.replace(/_/g,' ').toUpperCase()}:`;
                    messageElement.appendChild(agentLabelDiv);
                }

                // Add content on its own line
                const contentDiv = document.createElement('div');
                contentDiv.classList.add('message-content');
                if (type === 'response_chunk') {
                    contentDiv.textContent = text;
                } else {
                    contentDiv.innerHTML = text;
                }
                messageElement.appendChild(contentDiv);

            } else {
                // For conversation area, keep the original inline layout
                const timestampSpan = '';
                let innerHTMLContent = timestampSpan;

                // Only show persona for agent responses in chat
                if (type === 'agent_response' && agentPersona) {
                    innerHTMLContent += `<span class="agent-label">${escapeHTML(agentPersona)}:</span>`;
                }

                // Append the actual message content
                const contentSpan = document.createElement('span');
                contentSpan.classList.add('message-content');
                if (type === 'response_chunk') {
                     contentSpan.textContent = text;
                } else {
                     contentSpan.innerHTML = text;
                }
                messageElement.innerHTML = innerHTMLContent;
                messageElement.appendChild(contentSpan);
            }

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

        const createAgentStatusItem = (agentId, agent) => {
            const statusItem = document.createElement('div');
            const statusValue = (agent.status || 'idle').replace(/ /g, '_');
            const statusClass = `status-${statusValue}`;
            statusItem.classList.add('agent-status-item', statusClass);
            statusItem.setAttribute('data-agent-id', agentId);

            const persona = agent.persona || agentId;
            const modelDisplay = agent.model ? `(${escapeHTML(agent.model)})` : '(Model N/A)';
            // Removed teamInfo rendering here
            const stateDisplay = agent.state ? ` <span style="background-color: var(--bg-color-secondary); padding: 1px 6px; border-radius: 12px; font-size: 0.75em; border: 1px solid var(--border-color); margin-left: 5px; color: var(--text-color-secondary);">⚙️ ${escapeHTML(agent.state)}</span>` : '';

            const agentInfoSpan = document.createElement('span');
            agentInfoSpan.className = 'agent-info-span'; // Add class for easy targeting later
            agentInfoSpan.innerHTML = `<strong>${escapeHTML(persona)}</strong> <span class="agent-model">${modelDisplay}</span>${stateDisplay}`;

            const statusBadgeSpan = document.createElement('span');
            statusBadgeSpan.classList.add('agent-status');
            statusBadgeSpan.textContent = agent.status || 'idle';

            statusItem.appendChild(agentInfoSpan);
            statusItem.appendChild(statusBadgeSpan);
            
            if (agent.estimated_tokens !== undefined && agent.max_tokens) {
                const ratio = Math.min(100, Math.round((agent.estimated_tokens / agent.max_tokens) * 100));
                let colorVar = 'var(--accent-color-green)';
                if (ratio >= 90) colorVar = 'var(--accent-color-red)';
                else if (ratio >= 75) colorVar = 'var(--accent-color-yellow)';
                
                const progressDiv = document.createElement('div');
                progressDiv.style.marginTop = '6px';
                progressDiv.style.width = '100%';
                progressDiv.style.height = '4px';
                progressDiv.style.backgroundColor = 'var(--bg-color-primary)';
                progressDiv.style.borderRadius = '2px';
                progressDiv.style.overflow = 'hidden';
                progressDiv.title = `Token Usage: ${agent.estimated_tokens} / ${agent.max_tokens} (${ratio}%)`;
                
                const progressBar = document.createElement('div');
                progressBar.style.height = '100%';
                progressBar.style.backgroundColor = colorVar;
                progressBar.style.width = `${ratio}%`;
                progressBar.style.transition = 'width 0.3s ease';
                
                progressDiv.appendChild(progressBar);
                statusItem.appendChild(progressDiv);
            }
            return statusItem;
        };

        const standaloneAgents = [];
        const teams = {}; // teamName -> { pms: [], workers: [] }

        agentIds.forEach(agentId => {
            const agent = agentStatusData[agentId];
            if (!agent || agent.status === 'deleted') return;
            
            if (agent.team) {
                if (!teams[agent.team]) teams[agent.team] = { pms: [], workers: [] };
                if (agentId.toLowerCase().includes('pm') || (agent.persona && agent.persona.toLowerCase().includes('manager'))) {
                     teams[agent.team].pms.push(agentId);
                } else {
                     teams[agent.team].workers.push(agentId);
                }
            } else {
                standaloneAgents.push(agentId);
            }
        });

        // Render standalone agents
        standaloneAgents.sort((a, b) => {
            if (a === 'admin_ai') return -1;
            if (b === 'admin_ai') return 1;
            return a.localeCompare(b);
        }).forEach(id => {
             DOM.agentStatusContent.appendChild(createAgentStatusItem(id, agentStatusData[id]));
        });

        // Render teams
        Object.keys(teams).sort().forEach(teamName => {
             const team = teams[teamName];
             const teamContainer = document.createElement('div');
             teamContainer.className = 'team-container';
             teamContainer.style.marginBottom = '10px';
             
             // If there's a PM, use it as the header
             if (team.pms.length > 0) {
                 team.pms.forEach(pmId => {
                      const pmItem = createAgentStatusItem(pmId, agentStatusData[pmId]);
                      pmItem.style.marginBottom = '0'; // Tighter grouping
                      
                      if (team.workers.length > 0) {
                          const isTeamOpen = teamToggleStates[teamName] === true;
                          
                          const toggleBtn = document.createElement('button');
                          toggleBtn.textContent = isTeamOpen ? '🔼 Hide Team' : '🔽 Show Team';
                          toggleBtn.style.marginLeft = '10px';
                          toggleBtn.style.fontSize = '0.75em';
                          toggleBtn.style.padding = '2px 6px';
                          toggleBtn.style.cursor = 'pointer';
                          toggleBtn.style.borderRadius = '4px';
                          toggleBtn.style.border = '1px solid var(--border-color)';
                          toggleBtn.style.background = 'var(--bg-color-secondary)';
                          toggleBtn.style.color = 'var(--text-color)';
                          
                          const workersDiv = document.createElement('div');
                          workersDiv.style.display = isTeamOpen ? 'block' : 'none';
                          workersDiv.style.marginLeft = '15px';
                          workersDiv.style.marginTop = '5px';
                          workersDiv.style.borderLeft = '2px solid var(--border-color)';
                          workersDiv.style.paddingLeft = '10px';

                          toggleBtn.onclick = (e) => {
                               e.stopPropagation();
                               const willOpen = workersDiv.style.display === 'none';
                               workersDiv.style.display = willOpen ? 'block' : 'none';
                               toggleBtn.textContent = willOpen ? '🔼 Hide Team' : '🔽 Show Team';
                               teamToggleStates[teamName] = willOpen;
                          };
                          
                          pmItem.querySelector('.agent-info-span').appendChild(toggleBtn);
                          
                          team.workers.sort().forEach(wId => {
                               workersDiv.appendChild(createAgentStatusItem(wId, agentStatusData[wId]));
                          });
                          
                          teamContainer.appendChild(pmItem);
                          teamContainer.appendChild(workersDiv);
                      } else {
                          teamContainer.appendChild(pmItem);
                      }
                 });
             } else {
                 // No PM found, just render workers
                 team.workers.sort().forEach(wId => {
                      teamContainer.appendChild(createAgentStatusItem(wId, agentStatusData[wId]));
                 });
             }
             
             DOM.agentStatusContent.appendChild(teamContainer);
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

/**
 * Updates or creates a loading indicator for an active agent.
 */
export const updateAgentLoadingIndicator = (agentId, status, persona) => {
    const isChatAgent = agentId === 'admin_ai';
    const targetAreaId = isChatAgent ? 'conversation-area' : 'internal-comms-area';
    const messageArea = DOM[targetAreaId === 'conversation-area' ? 'conversationArea' : 'internalCommsArea'];
    
    if (!messageArea) return;

    const indicatorId = `loading-indicator-${agentId}`;
    let indicatorElement = document.getElementById(indicatorId);

    // Define states that indicate the agent is resting and shouldn't have a loading bubble
    const restingStates = ['idle', 'worker_wait', 'pm_standby', 'admin_standby', 'deleted'];
    const isActive = status && !restingStates.includes(status.toLowerCase());

    if (isActive) {
        if (!indicatorElement) {
            indicatorElement = document.createElement('div');
            indicatorElement.id = indicatorId;
            indicatorElement.classList.add('message', 'status', 'loading-indicator');
            
            const contentSpan = document.createElement('span');
            contentSpan.classList.add('message-content');
            contentSpan.innerHTML = `<i>${escapeHTML(status)}</i>`;
            
            if (isChatAgent) {
                indicatorElement.classList.add('agent_response');
                indicatorElement.innerHTML = `<span class="agent-label">${escapeHTML(persona || 'System')}:</span>`;
                contentSpan.style.opacity = '0.6';
                indicatorElement.appendChild(contentSpan);
            } else {
                indicatorElement.classList.add(`agent-border-${agentId.split('_')[0] || agentId}`);
                indicatorElement.innerHTML = `<div class="agent-label">${escapeHTML(persona || agentId)}:</div>`;
                const contentWrapper = document.createElement('div');
                contentWrapper.classList.add('message-content');
                contentWrapper.style.opacity = '0.6';
                contentWrapper.innerHTML = `<i>${escapeHTML(status)}</i>`;
                indicatorElement.appendChild(contentWrapper);
            }
            
            messageArea.appendChild(indicatorElement);
            
            // Scroll to bottom if already near bottom
            const isScrolledNearBottom = messageArea.scrollHeight - messageArea.scrollTop - messageArea.clientHeight < 150;
            if (isScrolledNearBottom) {
                messageArea.scrollTop = messageArea.scrollHeight;
            }
        } else {
            // Update text and move to bottom if it exists
            const contentEl = isChatAgent ? indicatorElement.querySelector('.message-content') : indicatorElement.querySelectorAll('.message-content')[indicatorElement.querySelectorAll('.message-content').length - 1];
            if (contentEl) {
                contentEl.innerHTML = `<i>${escapeHTML(status)}</i>`;
            }
            // Move it to the very bottom
            messageArea.appendChild(indicatorElement);
        }
    } else {
        if (indicatorElement) {
            indicatorElement.remove();
        }
    }
};

/**
 * Specifically clears a loading indicator, e.g. when streaming starts.
 */
export const clearAgentLoadingIndicator = (agentId) => {
    const indicatorId = `loading-indicator-${agentId}`;
    const indicatorElement = document.getElementById(indicatorId);
    if (indicatorElement) {
        indicatorElement.remove();
    }
};

/**
 * Renders the fetched tasks into the project-tasks-content container.
 * @param {Array} tasksData The list of task objects.
 */
export const renderProjectTasks = (tasksData) => {
    if (!DOM.projectTasksContent) return;

    if (!tasksData || !Array.isArray(tasksData) || tasksData.length === 0) {
        DOM.projectTasksContent.innerHTML = '<span class="status-placeholder">No active project tasks.</span>';
        return;
    }

    // Preserve open state of existing groups before unmounting
    const openStates = {};
    const existingDetails = DOM.projectTasksContent.querySelectorAll('details.task-group');
    existingDetails.forEach(detail => {
        if (detail.id) {
            openStates[detail.id] = detail.open;
        }
    });

    DOM.projectTasksContent.innerHTML = '';

    // Group tasks by category
    const activeTasks = [];    // assigned AND actively being worked on
    const assignedTasks = [];  // assigned but not yet started (todo)
    const unassignedTasks = [];
    const completedTasks = [];

    const activeProgressValues = ['in_progress', 'doing', 'working', 'started', 'waiting', 'blocked', 'stuck'];

    tasksData.forEach(task => {
        const prog = (task.task_progress || 'todo').toLowerCase();
        if (prog === 'finished' || prog === 'done' || prog === 'completed') {
            completedTasks.push(task);
        } else if (!task.assignee) {
            unassignedTasks.push(task);
        } else if (activeProgressValues.includes(prog)) {
            activeTasks.push(task);
        } else {
            // Assigned but still 'todo' or other non-active state
            assignedTasks.push(task);
        }
    });

    const createGroup = (id, title, tasks, defaultOpen = true) => {
        if (tasks.length === 0) return null;
        const details = document.createElement('details');
        details.id = id;
        
        // Restore previous open state if it exists, otherwise use default
        if (openStates.hasOwnProperty(id)) {
            details.open = openStates[id];
        } else if (defaultOpen) {
            details.open = true;
        }
        
        details.className = 'task-group';
        
        const summary = document.createElement('summary');
        summary.textContent = `${title} (${tasks.length})`;
        details.appendChild(summary);

        const listDiv = document.createElement('div');
        listDiv.className = 'task-list';

        tasks.forEach(task => {
            const item = document.createElement('div');
            item.className = `task-item prog-${task.task_progress || 'todo'}`;
            
            const desc = document.createElement('div');
            desc.className = 'task-desc';
            desc.textContent = task.description || 'No description';
            desc.title = task.description || '';
            
            const meta = document.createElement('div');
            meta.className = 'task-meta';
            
            const prog = document.createElement('span');
            prog.className = 'task-prog';
            // Shorten commonly long progress strings
            let progText = (task.task_progress || 'todo').replace(/_/g, ' ');
            if (progText === 'in progress') progText = 'doing';
            prog.textContent = progText;
            
            meta.appendChild(prog);
            
            if (task.assignee) {
                const assignee = document.createElement('span');
                assignee.className = 'task-assignee';
                // Try to shorten to something like "worker_1" instead of the full prefix
                let shortAssignee = task.assignee;
                if (shortAssignee.includes('_worker_')) {
                    shortAssignee = shortAssignee.split('_worker_')[1];
                    shortAssignee = `worker_${shortAssignee}`;
                }
                assignee.textContent = `@${shortAssignee}`;
                meta.appendChild(assignee);
            }
            
            item.appendChild(desc);
            item.appendChild(meta);
            listDiv.appendChild(item);
        });
        
        details.appendChild(listDiv);
        return details;
    };

    const activeGroup = createGroup('task-group-active', '🔥 Active', activeTasks, true);
    if (activeGroup) DOM.projectTasksContent.appendChild(activeGroup);
    
    const assignedGroup = createGroup('task-group-assigned', '📋 Assigned', assignedTasks, false);
    if (assignedGroup) DOM.projectTasksContent.appendChild(assignedGroup);
    
    const unassignedGroup = createGroup('task-group-unassigned', '📭 Unassigned', unassignedTasks, false);
    if (unassignedGroup) DOM.projectTasksContent.appendChild(unassignedGroup);
    
    const completedGroup = createGroup('task-group-completed', '✅ Completed', completedTasks, false);
    if (completedGroup) DOM.projectTasksContent.appendChild(completedGroup);
};

console.log("Frontend UI module loaded.");
