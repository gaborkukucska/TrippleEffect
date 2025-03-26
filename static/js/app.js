document.addEventListener('DOMContentLoaded', () => {
    console.log('TrippleEffect UI loaded');
    
    const ws = new WebSocket(`ws://${window.location.host}/ws`);
    
    ws.onmessage = (event) => {
        console.log('Message from server:', event.data);
    };
    
    ws.onopen = () => {
        ws.send('Client connected');
    };
});
