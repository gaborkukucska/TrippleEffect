# START OF FILE src/api/websocket_manager.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict
import json # Import json for potentially structured messages later

# Create an API router instance for WebSocket endpoints
router = APIRouter()

# In-memory storage for active WebSocket connections
# For simplicity now, just a list. Could evolve into a dict mapping user ID or session ID.
active_connections: List[WebSocket] = []

async def broadcast(message: str):
    """
    Sends a message to all active WebSocket connections.
    """
    disconnected_connections: List[WebSocket] = []
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except WebSocketDisconnect:
            disconnected_connections.append(connection)
        except Exception as e:
            print(f"Error sending message to a websocket: {e}")
            disconnected_connections.append(connection) # Assume connection is broken

    # Remove disconnected connections from the active list
    for connection in disconnected_connections:
        if connection in active_connections:
            active_connections.remove(connection)
            print("Removed a disconnected WebSocket connection.")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for handling client connections.
    - Accepts new connections.
    - Handles incoming messages (echoes back for now).
    - Removes connections on disconnect.
    """
    await websocket.accept()
    active_connections.append(websocket)
    print(f"New WebSocket connection established. Total clients: {len(active_connections)}")
    # Optionally send a welcome message upon connection
    await websocket.send_text(json.dumps({"type": "status", "message": "Connected to TrippleEffect backend!"}))

    try:
        while True:
            # Wait for a message from the client
            data = await websocket.receive_text()
            print(f"Received message: {data}")

            # Basic echo logic for now
            # In future phases, this will parse the message and route it to the AgentManager
            response = {"type": "echo", "original_message": data}
            await websocket.send_text(json.dumps(response))

            # Example of broadcasting (optional for now)
            # await broadcast(f"Client says: {data}")

    except WebSocketDisconnect:
        active_connections.remove(websocket)
        print(f"WebSocket connection closed. Total clients: {len(active_connections)}")
    except Exception as e:
        # Handle other potential errors during communication
        print(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)
        # Optionally try to close the websocket gracefully if possible
        try:
            await websocket.close(code=1011) # Internal Error
        except Exception:
            pass # Ignore errors during close
        print(f"WebSocket connection closed due to error. Total clients: {len(active_connections)}")
