// START OF FILE static/js/session.js

import * as api from './api.js';
import * as DOM from './domElements.js';

/**
 * Fetches the list of projects from the backend API and populates the project dropdown.
 * Also triggers loading sessions for the first project found.
 */
export const loadProjects = async () => {
    if (!DOM.projectSelect) {
        console.warn("Session module: Project select element not found.");
        return;
    }
    console.log("Session: Loading projects...");
    DOM.projectSelect.innerHTML = '<option value="">Loading Projects...</option>';
    DOM.projectSelect.disabled = true;
    if (DOM.sessionSelect) {
        DOM.sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
        DOM.sessionSelect.disabled = true;
    }
    if (DOM.loadSessionButton) DOM.loadSessionButton.disabled = true;

    try {
        const projects = await api.makeApiCall('/api/projects');
        DOM.projectSelect.innerHTML = '<option value="">-- Select Project --</option>'; // Reset
        if (projects && projects.length > 0) {
            console.log(`Session: Found ${projects.length} projects.`);
            projects.forEach(proj => {
                const option = document.createElement('option');
                option.value = proj.project_name;
                option.textContent = proj.project_name;
                DOM.projectSelect.appendChild(option);
            });
            DOM.projectSelect.disabled = false;
            // Select and load sessions for the first project
            DOM.projectSelect.value = projects[0].project_name;
            await loadSessions(projects[0].project_name);
        } else {
            console.log("Session: No projects found.");
            DOM.projectSelect.innerHTML = '<option value="">-- No Projects Found --</option>';
        }
    } catch (error) {
        console.error("Session Error loading projects:", error);
        DOM.projectSelect.innerHTML = '<option value="">-- Error Loading Projects --</option>';
        // UI error display handled by makeApiCall
    }
};

/**
 * Fetches the list of sessions for a given project and populates the session dropdown.
 * @param {string} projectName The name of the project whose sessions to load.
 */
export const loadSessions = async (projectName) => {
    if (!DOM.sessionSelect || !projectName) {
        console.log("Session: Cannot load sessions, project name or session select element missing.");
        if(DOM.sessionSelect) {
            DOM.sessionSelect.innerHTML = '<option value="">-- Select Project First --</option>';
            DOM.sessionSelect.disabled = true;
        }
        if(DOM.loadSessionButton) DOM.loadSessionButton.disabled = true;
        return;
    }
    console.log(`Session: Loading sessions for project: ${projectName}...`);
    DOM.sessionSelect.innerHTML = '<option value="">Loading Sessions...</option>';
    DOM.sessionSelect.disabled = true;
    if(DOM.loadSessionButton) DOM.loadSessionButton.disabled = true;

    try {
        const sessions = await api.makeApiCall(`/api/projects/${projectName}/sessions`);
        DOM.sessionSelect.innerHTML = '<option value="">-- Select Session --</option>'; // Reset
        if (sessions && sessions.length > 0) {
            console.log(`Session: Found ${sessions.length} sessions for ${projectName}.`);
            sessions.forEach(sess => {
                const option = document.createElement('option');
                option.value = sess.session_name;
                option.textContent = sess.session_name;
                DOM.sessionSelect.appendChild(option);
            });
            DOM.sessionSelect.disabled = false;
            // Ensure load button state reflects initial potentially empty selection
            if (DOM.loadSessionButton) {
                DOM.loadSessionButton.disabled = !DOM.sessionSelect.value;
            }
        } else {
            console.log(`Session: No sessions found for ${projectName}.`);
            DOM.sessionSelect.innerHTML = '<option value="">-- No Sessions Found --</option>';
        }
    } catch (error) {
        console.error(`Session Error loading sessions for ${projectName}:`, error);
        DOM.sessionSelect.innerHTML = '<option value="">-- Error Loading Sessions --</option>';
        if(DOM.loadSessionButton) DOM.loadSessionButton.disabled = true;
        // UI error display handled by makeApiCall
    }
};

console.log("Frontend session module loaded.");
