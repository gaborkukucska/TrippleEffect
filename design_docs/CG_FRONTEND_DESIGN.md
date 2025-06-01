# Conceptual Outline: Frontend Changes for Constitutional Guardian (CG) Concerns

This document outlines the necessary frontend modifications to handle Constitutional Guardian (CG) concern events, display information to the user, and provide actions for concern resolution.

## Affected Files:

*   `static/js/handlers.js`: For processing incoming WebSocket messages.
*   `static/js/ui.js`: For rendering UI elements related to CG concerns.
*   `static/js/api.js`: For making API calls to resolve CG concerns.
*   `static/js/main.js` (or `handlers.js`): For attaching event listeners to new UI elements.

---

## 1. File: `static/js/handlers.js`

### Modify `handleWebSocketMessage(event)`:

The core function responsible for routing incoming WebSocket messages needs to be updated to recognize and process `cg_concern` messages.

*   **Identify Message Type:**
    *   Add a new `case` to the existing `switch (messageData.type)` statement (or an `else if` condition if using an if/else structure) for `messageData.type === 'cg_concern'`.
*   **Action within the `cg_concern` case:**
    1.  **Log for Debugging:**
        *   `console.log("CG Concern received:", messageData);`
    2.  **Extract Data:**
        *   Safely extract `agent_id`, `original_text`, and `concern_details` from the `messageData` object.
        *   Example:
            ```javascript
            const { agent_id, original_text, concern_details, status: newStatusObject } = messageData;
            ```
    3.  **Display Concern in UI:**
        *   Call a new function in `ui.js` to render the concern information.
        *   `ui.displayCGConcern(agent_id, original_text, concern_details);`
    4.  **Update Agent Status:**
        *   The `messageData` for a `cg_concern` should ideally include the new agent status object (e.g., `AGENT_STATUS_AWAITING_USER_REVIEW_CG`).
        *   Update the agent's status in the application's state management.
        *   `state.updateAgentStatus(agent_id, newStatusObject);` (Assuming `newStatusObject` is part of `messageData`)
        *   Or, if the specific status string is fixed: `state.updateAgentStatus(agent_id, AGENT_STATUS_AWAITING_USER_REVIEW_CG_STRING);` (where `AGENT_STATUS_AWAITING_USER_REVIEW_CG_STRING` is the actual status string).
        *   Refresh the UI element that lists agent statuses.
        *   `ui.updateAgentStatusList();`

---

## 2. File: `static/js/ui.js`

### Implement `displayCGConcern(agentId, originalText, concernDetails)` function:

This new function will be responsible for creating and injecting the HTML elements needed to show the CG concern to the user.

*   **Purpose:**
    *   To visually present the CG concern, including details, original text, and action buttons, within a designated UI area (e.g., `domElements.internalCommsArea` or a modal pop-up).
*   **Rendering Logic:**
    1.  **Create Container:**
        *   `const concernContainer = document.createElement('div');`
        *   `concernContainer.className = 'cg-concern-message system-message';` // Add appropriate classes for styling
        *   `concernContainer.id = \`cg-concern-${agentId}\`;` // Unique ID for later removal/update
    2.  **Add Title:**
        *   `const title = document.createElement('h4');`
        *   `title.textContent = \`Constitutional Guardian Review Needed for Agent: ${agentId}\`;`
        *   `concernContainer.appendChild(title);`
    3.  **Display Concern Details:**
        *   `const detailsPara = document.createElement('p');`
        *   `detailsPara.innerHTML = \`<strong>Concern:</strong> ${concernDetails}\`;` // Use innerHTML if details can contain HTML, otherwise textContent and sanitize.
        *   `concernContainer.appendChild(detailsPara);`
    4.  **Display Original Text:**
        *   Consider making this collapsible if `originalText` can be very long.
        *   `const originalTextLabel = document.createElement('strong');`
        *   `originalTextLabel.textContent = 'Original Proposed Output:';`
        *   `const originalTextPre = document.createElement('pre');`
        *   `originalTextPre.textContent = originalText;`
        *   `concernContainer.appendChild(originalTextLabel);`
        *   `concernContainer.appendChild(originalTextPre);`
    5.  **Create Action Buttons Container:**
        *   `const buttonsDiv = document.createElement('div');`
        *   `buttonsDiv.className = 'cg-action-buttons';`
    6.  **"Approve Output" Button:**
        *   `const approveButton = document.createElement('button');`
        *   `approveButton.id = \`cg-approve-${agentId}\`;`
        *   `approveButton.className = 'cg-action-button approve-button';`
        *   `approveButton.textContent = 'Approve Output';`
        *   `approveButton.dataset.agentId = agentId;`
        *   `buttonsDiv.appendChild(approveButton);`
    7.  **"Stop Agent" Button:**
        *   `const stopButton = document.createElement('button');`
        *   `stopButton.id = \`cg-stop-${agentId}\`;`
        *   `stopButton.className = 'cg-action-button stop-button';`
        *   `stopButton.textContent = 'Stop Agent';`
        *   `stopButton.dataset.agentId = agentId;`
        *   `buttonsDiv.appendChild(stopButton);`
    8.  **"Retry with Feedback" Button:**
        *   `const retryButton = document.createElement('button');`
        *   `retryButton.id = \`cg-retry-${agentId}\`;`
        *   `retryButton.className = 'cg-action-button retry-button';`
        *   `retryButton.textContent = 'Retry with Feedback';`
        *   `retryButton.dataset.agentId = agentId;`
        *   `buttonsDiv.appendChild(retryButton);`
    9.  **Feedback Input Textarea:**
        *   `const feedbackInput = document.createElement('textarea');`
        *   `feedbackInput.id = \`cg-feedback-input-${agentId}\`;`
        *   `feedbackInput.className = 'cg-feedback-textarea';`
        *   `feedbackInput.placeholder = 'Provide feedback for retry...';`
        *   `feedbackInput.style.display = 'none';` // Initially hidden, shown when "Retry" is clicked.
        *   `buttonsDiv.appendChild(feedbackInput);` // Or append after all buttons
    10. **Append Buttons and Container:**
        *   `concernContainer.appendChild(buttonsDiv);`
        *   `domElements.internalCommsArea.appendChild(concernContainer);` // Or other target area
    11. **Scroll into View:**
        *   `concernContainer.scrollIntoView({ behavior: 'smooth' });`

---

## 3. File: `static/js/api.js`

Implement new functions to communicate with the backend API endpoints for CG concern resolution.

1.  **`callApproveCGConcern(agentId)`:**
    *   **Purpose:** Inform the backend that the user has approved the CG-flagged output.
    *   **Implementation:**
        ```javascript
        async function callApproveCGConcern(agentId) {
            try {
                const response = await fetch(\`/api/agents/${agentId}/cg/approve\`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.message || \`HTTP error! status: ${response.status}\`);
                }
                console.log(\`CG Approve for ${agentId} successful:\`, result.message);
                // ui.showToastMessage(\`Approval for ${agentId} sent.\`, 'success');
                return true;
            } catch (error) {
                console.error(\`Error approving CG concern for ${agentId}:\`, error);
                // ui.showToastMessage(\`Error approving concern for ${agentId}: ${error.message}\`, 'error');
                return false;
            }
        }
        ```
    *   **Response Handling:** Log success/failure. UI updates (e.g., removing the concern box) should ideally be driven by subsequent WebSocket status updates, but a temporary toast message can be shown.

2.  **`callStopCGConcern(agentId)`:**
    *   **Purpose:** Inform the backend to stop the agent due to the CG concern.
    *   **Implementation:**
        ```javascript
        async function callStopCGConcern(agentId) {
            try {
                const response = await fetch(\`/api/agents/${agentId}/cg/stop\`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.message || \`HTTP error! status: ${response.status}\`);
                }
                console.log(\`CG Stop for ${agentId} successful:\`, result.message);
                // ui.showToastMessage(\`Stop command for ${agentId} sent.\`, 'success');
                return true;
            } catch (error) {
                console.error(\`Error stopping agent ${agentId} via CG:\`, error);
                // ui.showToastMessage(\`Error stopping agent ${agentId}: ${error.message}\`, 'error');
                return false;
            }
        }
        ```
    *   **Response Handling:** Similar to approve.

3.  **`callRetryCGConcern(agentId, feedback)`:**
    *   **Purpose:** Send user feedback to the backend for the agent to retry generating output.
    *   **Implementation:**
        ```javascript
        async function callRetryCGConcern(agentId, feedback) {
            try {
                const response = await fetch(\`/api/agents/${agentId}/cg/retry\`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_feedback: feedback }),
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.message || \`HTTP error! status: ${response.status}\`);
                }
                console.log(\`CG Retry for ${agentId} successful:\`, result.message);
                // ui.showToastMessage(\`Retry request for ${agentId} sent.\`, 'success');
                return true;
            } catch (error) {
                console.error(\`Error retrying CG concern for ${agentId}:\`, error);
                // ui.showToastMessage(\`Error sending retry for ${agentId}: ${error.message}\`, 'error');
                return false;
            }
        }
        ```
    *   **Response Handling:** Similar to approve.

---

## 4. File: `static/js/main.js` (or `handlers.js` for event listener delegation)

### Add Event Listeners for CG Action Buttons:

Event listeners are needed to trigger the API calls when users click the resolution buttons. Event delegation is recommended for dynamically added elements.

*   **Event Delegation Target:**
    *   Attach listener to a static parent container, e.g., `domElements.internalCommsArea`.
    *   `domElements.internalCommsArea.addEventListener('click', async function(event) { ... });`
*   **Handler Logic:**
    1.  **Identify Clicked Button:**
        *   Check if `event.target` matches a CG action button class: `if (event.target.classList.contains('cg-action-button')) { ... }`
    2.  **Get Agent ID:**
        *   `const agentId = event.target.dataset.agentId;`
        *   If `!agentId`, do nothing.
    3.  **Determine Action:**
        *   Use `event.target.id` or classes to differentiate:
            *   **Approve:** `if (event.target.classList.contains('approve-button')) { await api.callApproveCGConcern(agentId); }`
            *   **Stop:** `else if (event.target.classList.contains('stop-button')) { await api.callStopCGConcern(agentId); }`
            *   **Retry:** `else if (event.target.classList.contains('retry-button')) { ... }`
    4.  **Handle Retry Action:**
        *   If "Retry" is clicked:
            *   Toggle visibility of the feedback textarea: `const feedbackInput = document.getElementById(\`cg-feedback-input-${agentId}\`);`
            *   If textarea is already visible and has content (i.e., user clicks "Retry" again to submit feedback):
                *   `const feedbackText = feedbackInput.value.trim();`
                *   `if (feedbackText) { await api.callRetryCGConcern(agentId, feedbackText); feedbackInput.value = ''; feedbackInput.style.display = 'none'; } else { ui.showToastMessage('Please enter feedback to retry.', 'warning'); }`
            *   Else (first click on "Retry"):
                *   `feedbackInput.style.display = 'block';`
                *   `feedbackInput.focus();`
                *   Change button text to "Submit Feedback" or similar.
    5.  **Post-Action UI Cleanup (Optional but Recommended):**
        *   After a successful API call (approve, stop, or submitted retry), the specific CG concern UI (the `div#cg-concern-${agentId}`) could be greyed out, have its buttons disabled, or removed. This provides immediate feedback.
        *   The primary UI update (e.g., agent status change, removal of the concern from a task list) should be driven by WebSocket messages from the backend confirming the state change.
        *   Example: `event.target.closest('.cg-concern-message').remove();` (If immediate removal is desired)
        *   Or disable buttons: `event.target.closest('.cg-action-buttons').querySelectorAll('button').forEach(btn => btn.disabled = true);`

---

## 5. General UI/UX Considerations:

*   **Clarity:** CG concern messages in the `internalCommsArea` (or modal) must be visually distinct from regular messages. Use unique styling (borders, background colors, icons).
*   **Agent Status Indication:** The agent's status (e.g., in a list of all agents) must clearly show `AGENT_STATUS_AWAITING_USER_REVIEW_CG` (or its user-friendly equivalent like "Awaiting CG Review").
*   **Multiple Concerns:** The system should gracefully handle multiple CG concerns, potentially for different agents, displayed simultaneously without confusion. Each concern UI block should be self-contained.
*   **User Feedback:** Provide immediate feedback after a user clicks a resolution button (e.g., "Approval request sent..."). This can be a toast message or a change in the button's state.
*   **Accessibility:** Ensure all new UI elements are accessible (e.g., proper ARIA attributes for buttons, textarea labels).
*   **Error Handling:** If API calls fail, display user-friendly error messages.
*   **State Management:** Ensure agent states are consistently updated and reflected across all relevant UI components after a CG action is taken and confirmed by the backend.
*   **"Retry with Feedback" UX:**
    *   Clearly indicate that the "Retry with Feedback" button might first reveal a text area.
    *   The button text could change from "Retry with Feedback" to "Submit Feedback" once the textarea is visible.

---
This outline provides a comprehensive guide for the frontend development team to implement the CG concern resolution feature.
