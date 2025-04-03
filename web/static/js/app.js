const socket = io();
let isRecording = false;

// Collapsible sections
function toggleSection(agentId) {
    const content = document.getElementById(agentId);
    content.style.display = content.style.display === 'none' ? 'block' : 'none';
}

// WebSocket handlers
socket.on('agent_response', (data) => {
    data.results.forEach(result => {
        const agentPanel = document.querySelector(`#${result.agent} .message-log`);
        const messageElement = document.createElement('div');
        messageElement.innerHTML = `
            <p><strong>${new Date(data.timestamp).toLocaleTimeString()}</strong></p>
            <pre>${JSON.stringify(result.response, null, 2)}</pre>
        `;
        agentPanel.appendChild(messageElement);
    });
});

socket.on('status_update', (data) => {
    const statusBar = document.createElement('div');
    statusBar.textContent = data.msg;
    document.body.prepend(statusBar);
});

// Voice input
function startDictation() {
    if (!('webkitSpeechRecognition' in window)) {
        alert("Speech recognition not supported!");
        return;
    }

    const recognition = new webkitSpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        document.getElementById('user-input').value += transcript;
    };

    recognition.start();
}

// File handling
document.getElementById('file-upload').addEventListener('change', (e) => {
    const files = Array.from(e.target.files);
    files.forEach(file => {
        const reader = new FileReader();
        reader.onload = (event) => {
            socket.emit('file_upload', {
                name: file.name,
                content: event.target.result
            });
        };
        reader.readAsDataURL(file);
    });
});

// Request submission
function submitRequest() {
    const input = document.getElementById('user-input');
    const files = document.getElementById('file-upload').files;
    
    socket.emit('user_request', {
        prompt: input.value,
        files: Array.from(files),
        context: {}
    });
    
    input.value = '';
}
