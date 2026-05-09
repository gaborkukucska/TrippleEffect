// START OF FILE static/js/auth.js

/**
 * Authentication module for TrippleEffect frontend.
 * Handles login, registration, logout, and auth state checking.
 */

const authView = () => document.getElementById('auth-view');
const appContainer = () => document.getElementById('app-container');

/**
 * Check if user is authenticated by calling /api/auth/me
 * Returns { authenticated, username, is_main_user } or { authenticated: false }
 */
export async function checkAuth() {
    try {
        const response = await fetch('/api/auth/me');
        if (response.ok) {
            return await response.json();
        }
        return { authenticated: false };
    } catch (e) {
        console.error('Auth check failed:', e);
        return { authenticated: false };
    }
}

/**
 * Check if any users exist and if registration is open.
 */
export async function checkAuthStatus() {
    try {
        const response = await fetch('/api/auth/check');
        if (response.ok) {
            return await response.json();
        }
        return { users_exist: false, registration_open: true };
    } catch (e) {
        console.error('Auth status check failed:', e);
        return { users_exist: false, registration_open: true };
    }
}

/**
 * Show the auth overlay and hide the app.
 */
export function showAuthView() {
    const av = authView();
    const ac = appContainer();
    if (av) av.classList.remove('hidden');
    if (ac) ac.style.display = 'none';
}

/**
 * Hide the auth overlay and show the app.
 */
export function hideAuthView() {
    const av = authView();
    const ac = appContainer();
    if (av) av.classList.add('hidden');
    if (ac) ac.style.display = '';
}

/**
 * Initialize auth UI: tab switching, form submissions, initial state.
 * @param {Function} onAuthSuccess - Callback when user is authenticated (to init the app)
 */
export function initAuth(onAuthSuccess) {
    const tabs = document.querySelectorAll('.auth-tab');
    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');
    const loginError = document.getElementById('login-error');
    const registerError = document.getElementById('register-error');
    const registerInfo = document.getElementById('register-info');
    const logoutButton = document.getElementById('logout-button');
    const sessionLogoutButton = document.getElementById('session-logout-button');

    // Tab switching
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const target = tab.dataset.tab;
            if (target === 'login') {
                loginForm.classList.add('active');
                registerForm.classList.remove('active');
            } else {
                registerForm.classList.add('active');
                loginForm.classList.remove('active');
            }
            // Clear errors on tab switch
            if (loginError) loginError.textContent = '';
            if (registerError) registerError.textContent = '';
        });
    });

    // Login form submission
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (loginError) loginError.textContent = '';
            const username = document.getElementById('login-username').value.trim();
            const password = document.getElementById('login-password').value;
            const submitBtn = loginForm.querySelector('.auth-submit-btn');

            if (!username || !password) {
                if (loginError) loginError.textContent = 'Please fill in all fields.';
                return;
            }

            submitBtn.disabled = true;
            submitBtn.textContent = 'Signing in...';

            try {
                const response = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password }),
                });
                const data = await response.json();
                if (response.ok && data.success) {
                    hideAuthView();
                    onAuthSuccess(data.username);
                } else {
                    if (loginError) loginError.textContent = data.detail || data.message || 'Login failed.';
                }
            } catch (err) {
                console.error('Login error:', err);
                if (loginError) loginError.textContent = 'Connection error. Is the server running?';
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Sign In';
            }
        });
    }

    // Register form submission
    if (registerForm) {
        registerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (registerError) registerError.textContent = '';
            const username = document.getElementById('register-username').value.trim();
            const password = document.getElementById('register-password').value;
            const submitBtn = registerForm.querySelector('.auth-submit-btn');

            if (!username || !password) {
                if (registerError) registerError.textContent = 'Please fill in all fields.';
                return;
            }

            submitBtn.disabled = true;
            submitBtn.textContent = 'Creating account...';

            try {
                const response = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password }),
                });
                const data = await response.json();
                if (response.ok && data.success) {
                    hideAuthView();
                    onAuthSuccess(data.username);
                } else {
                    if (registerError) registerError.textContent = data.detail || data.message || 'Registration failed.';
                }
            } catch (err) {
                console.error('Register error:', err);
                if (registerError) registerError.textContent = 'Connection error. Is the server running?';
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Create Account';
            }
        });
    }

    // Logout buttons
    const handleLogout = async () => {
        try {
            await fetch('/api/auth/logout', { method: 'POST' });
        } catch (e) {
            console.warn('Logout request failed:', e);
        }
        // Always show auth view after logout attempt
        showAuthView();
        // Close WebSocket
        const ws = window._trippleEffectWs;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.close();
        }
    };

    if (logoutButton) {
        logoutButton.addEventListener('click', handleLogout);
    }
    
    if (sessionLogoutButton) {
        sessionLogoutButton.addEventListener('click', handleLogout);
    }

    // Initial check: determine whether to show login or register tab first
    checkAuthStatus().then(status => {
        if (!status.users_exist) {
            // No users: auto-switch to register tab
            tabs.forEach(t => t.classList.remove('active'));
            const regTab = document.querySelector('.auth-tab[data-tab="register"]');
            if (regTab) regTab.classList.add('active');
            if (loginForm) loginForm.classList.remove('active');
            if (registerForm) registerForm.classList.add('active');
            if (registerInfo) registerInfo.textContent = 'First account will become the admin user.';
        } else if (!status.registration_open) {
            // Registration disabled: hide register tab
            const regTab = document.querySelector('.auth-tab[data-tab="register"]');
            if (regTab) regTab.style.display = 'none';
        }
    });
}

console.log("Frontend auth module loaded.");
