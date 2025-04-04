# START OF FILE src/api/websocket_manager.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict, Optional, Any
import json
import asyncio # Import asyncio for create_task

# Import AgentManager for type hinting (optional but good practice)
# Use a forward reference string if AgentManager imports this module to avoid circular imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager


# Create an API router instance for WebSocket endpoints
router = APIRouter()

# In-memory storage for active WebSocket connections
active_connections: List[WebSocket] = []

# Module-level variable to hold the AgentManager instance
# This will be set by the main application startup logic
agent_manager_instance: Optional['AgentManager'] = None

def set_agent_manager(manager: 'AgentManager'):
    """Sets the global AgentManager instance for this module."""
    global agent_manager_instance
    agent_manager_instance = manager
    print("WebSocketManager: AgentManager instance set.")

async def broadcast(message: str):
    """
    Sends a message to all active WebSocket connections.
    """
    disconnected_connections: List[WebSocket] = []
    # Use a copy of the list to avoid issues if the list is modified during iteration
    connections_to_notify = list(active_connections)

    for connection in connections_to_notify:
        try:
            await connection.send_text(message)
        except WebSocketDisconnect:
            print("Broadcast: WebSocketDisconnect detected.")
            disconnected_connections.append(connection)
        except RuntimeError as e:
             # Handle cases where the connection might be closing
             print(f"Broadcast: Runtime error sending to a websocket: {e}")
             disconnected_connections.append(connection)
        except Exception as e:
            print(f"Broadcast: Error sending message to a websocket: {type(e).__name__} - {e}")
            disconnected_connections.append(connection) # Assume connection is broken

    # Remove disconnected connections from the main active list
    if disconnected_connections:
        print(f"Broadcast: Removing {len(disconnected_connections)} disconnected connection(s).")
        for connection in disconnected_connections:
            if connection in active_connections:
                active_connections.remove(connection)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for handling client connections.
    - Accepts new connections.
    - Handles incoming messages by forwarding them to the AgentManager.
    - Removes connections on disconnect.
    """
    await websocket.accept()
    active_connections.append(websocket)
    client_host = websocket.client.host if websocket.client else "unknown"
    print(f"New WebSocket connection from {client_host}. Total clients: {len(active_connections)}")

    # Send initial connection confirmation
    try:
        await websocket.send_text(json.dumps({"type": "status", "message": "Connected to TrippleEffect backend!"}))
    except Exception as e:
        print(f"Error sending initial status to {client_host}: {e}")
        active_connections.remove(websocket) # Remove if initial send fails
        return # Don't proceed if we can't even send the first message

    try:
        while True:
            # Wait for a message from the client
            data = await websocket.receive_text()
            print(f"Received message from {client_host}: {data[:100]}...")

            if agent_manager_instance:
                # Pass the message to the AgentManager asynchronously
                # Use create_task to avoid blocking the websocket loop
                # Pass client identifier (e.g., host) if needed by manager
                asyncio.create_task(agent_manager_instance.handle_user_message(data, client_id=client_host))
            else:
                # Fallback if AgentManager isn't initialized (shouldn't happen in normal operation)
                print("Error: AgentManager not initialized. Cannot process message.")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "Backend AgentManager is not available. Please contact administrator."
                }))
                # Removed the old echo logic:
                # response = {"type": "echo", "original_message": data}
                # await websocket.send_text(json.dumps(response))

    except WebSocketDisconnect:
        print(f"WebSocket connection closed cleanly by {client_host}.")
    except Exception as e:
        # Handle other potential errors during communication
        print(f"WebSocket error for {client_host}: {type(e).__name__} - {e}")
    finally:
        # Ensure connection removal on disconnect or error
        if websocket in active_connections:
            active_connections.remove(websocket)
        print(f"WebSocket connection for {client_host} removed. Total clients: {len(active_connections)}")
        # Optionally try to close the websocket gracefully if connection is still open
        try:
            await websocket.close()
        except Exception:
            pass # Ignore errors during close after potential prior error
