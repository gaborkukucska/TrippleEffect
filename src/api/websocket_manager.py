# START OF FILE src/api/websocket_manager.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict, Optional, Any
import json
import asyncio # Import asyncio for create_task
import logging # Added logging

# Import AgentManager for type hinting (optional but good practice)
# Use a forward reference string if AgentManager imports this module to avoid circular imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__) # Added logger

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
    logger.info("WebSocketManager: AgentManager instance set.") # Changed print to logger

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
            logger.warning("Broadcast: WebSocketDisconnect detected.") # Changed print to logger
            disconnected_connections.append(connection)
        except RuntimeError as e:
             # Handle cases where the connection might be closing
             logger.warning(f"Broadcast: Runtime error sending to a websocket: {e}") # Changed print to logger
             disconnected_connections.append(connection)
        except Exception as e:
            logger.error(f"Broadcast: Error sending message to a websocket: {type(e).__name__} - {e}") # Changed print to logger
            disconnected_connections.append(connection) # Assume connection is broken

    # Remove disconnected connections from the main active list
    if disconnected_connections:
        logger.info(f"Broadcast: Removing {len(disconnected_connections)} disconnected connection(s).") # Changed print to logger
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
    logger.info(f"New WebSocket connection from {client_host}. Total clients: {len(active_connections)}") # Changed print to logger

    # Send initial connection confirmation
    try:
        await websocket.send_text(json.dumps({"type": "status", "message": "Connected to TrippleEffect backend!"}))
    except Exception as e:
        logger.error(f"Error sending initial status to {client_host}: {e}") # Changed print to logger
        active_connections.remove(websocket) # Remove if initial send fails
        return # Don't proceed if we can't even send the first message

    try:
        while True:
            # Wait for a message from the client
            data = await websocket.receive_text()
            logger.debug(f"Received message from {client_host}: {data[:150]}...") # Changed print to logger, log more chars

            if not agent_manager_instance:
                # Fallback if AgentManager isn't initialized
                logger.error("WebSocket Error: AgentManager not initialized. Cannot process message.")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "Backend AgentManager is not available. Please contact administrator."
                }))
                continue # Skip processing this message

            # --- Process incoming message ---
            try:
                # Check if it's a JSON message (for structured commands like override)
                message_data = json.loads(data)
                message_type = message_data.get("type")

                if message_type == "submit_user_override":
                    logger.info(f"Received user override submission from {client_host}.")
                    # Pass the override data to the AgentManager asynchronously
                    asyncio.create_task(agent_manager_instance.handle_user_override(message_data))
                elif message_type == "user_message_with_file":
                     logger.info(f"Received message with file attachment from {client_host}.")
                     # Combine text and file content for the manager (or adjust manager to handle dict)
                     # For now, let's prepend file info to the text message
                     file_info = f"[Attached File: {message_data.get('filename', 'N/A')}]\n```\n{message_data.get('file_content', '')}\n```\n\n"
                     combined_message = file_info + message_data.get('text', '')
                     asyncio.create_task(agent_manager_instance.handle_user_message(combined_message, client_id=client_host))
                elif message_type == "cg_concern_response":
                    logger.info(f"Received cg_concern_response from {client_host}.")
                    action = message_data.get("action")
                    original_text = message_data.get("original_text")
                    user_feedback = message_data.get("user_feedback")
                    agent_id_for_cg_response = message_data.get("agent_id")

                    if not agent_id_for_cg_response:
                        logger.error(
                            f"Received cg_concern_response from {client_host} without agent_id. "
                            f"Cannot correctly target agent for resolution. Defaulting to 'admin_ai' with a warning."
                        )
                        agent_id_for_cg_response = "admin_ai" # Fallback as per instructions
                    else:
                        logger.info(f"CG concern response is for agent_id: {agent_id_for_cg_response}")

                    if action and original_text is not None: # user_feedback can be None
                        if action == "acknowledge_cg_concern":
                            if agent_manager_instance and hasattr(agent_manager_instance, 'resolve_cg_concern_approve'):
                                logger.debug(f"Forwarding 'acknowledge' for CG concern for agent '{agent_id_for_cg_response}' to AgentManager.")
                                asyncio.create_task(
                                    agent_manager_instance.resolve_cg_concern_approve(agent_id=agent_id_for_cg_response)
                                )
                            else:
                                logger.error(f"AgentManager not available or missing 'resolve_cg_concern_approve' method for cg_concern_response from {client_host}.")
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "content": "Backend cannot process CG concern approval at this time."
                                }))
                        elif action in ["revise_plan_cg_concern", "feedback_cg_concern"]:
                            if agent_manager_instance and hasattr(agent_manager_instance, 'resolve_cg_concern_retry'):
                                feedback_for_retry = user_feedback if user_feedback is not None else original_text
                                logger.debug(f"Forwarding 'retry/feedback' for CG concern for agent '{agent_id_for_cg_response}' to AgentManager with feedback.")
                                asyncio.create_task(
                                    agent_manager_instance.resolve_cg_concern_retry(agent_id=agent_id_for_cg_response, user_feedback=feedback_for_retry)
                                )
                            else:
                                logger.error(f"AgentManager not available or missing 'resolve_cg_concern_retry' method for cg_concern_response from {client_host}.")
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "content": "Backend cannot process CG concern retry/feedback at this time."
                                }))
                        else:
                            logger.warning(f"Unknown action '{action}' received in cg_concern_response from {client_host}.")
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "content": f"Unknown action '{action}' for constitutional guardian response."
                            }))
                    else:
                        logger.warning(f"Received incomplete cg_concern_response from {client_host}: {message_data}")
                        # Optionally notify client of error
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "content": "Incomplete constitutional guardian response received."
                        }))
                elif message_type == "request_full_agent_status":
                    logger.info(f"Received request_full_agent_status from {client_host}.")
                    if agent_manager_instance:
                        all_agent_statuses = agent_manager_instance.get_agent_status()
                        await websocket.send_text(json.dumps({
                            "type": "full_status",
                            "agents": all_agent_statuses
                        }))
                        logger.info(f"Sent full_status update to {client_host} with {len(all_agent_statuses)} agents.")
                    else:
                        logger.error("AgentManager not available. Cannot send full_status.")
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "content": "Backend AgentManager not available to provide full status."
                        }))
                else:
                    # Handle other potential JSON message types if added later
                    logger.warning(f"Received unknown JSON message type '{message_type}' from {client_host}.")
                    # Fallback: treat content as a plain message?
                    plain_text = message_data.get("text") or message_data.get("content") or str(message_data)
                    asyncio.create_task(agent_manager_instance.handle_user_message(plain_text, client_id=client_host))

            except json.JSONDecodeError:
                # If it's not JSON, treat it as a plain text message for the Admin AI
                logger.debug(f"Received plain text message from {client_host}. Forwarding to Admin AI.")
                asyncio.create_task(agent_manager_instance.handle_user_message(data, client_id=client_host))
            except Exception as e:
                 logger.error(f"Error processing incoming WebSocket message from {client_host}: {e}", exc_info=True)
                 logger.error(f"Original raw data: {data}")
                 try:
                     await websocket.send_text(json.dumps({"type": "error", "content": f"Error processing your message: {e}"}))
                 except: pass # Ignore send errors here

            # --- End message processing ---

    except WebSocketDisconnect:
        logger.info(f"WebSocket connection closed cleanly by {client_host}.") # Changed print to logger
    except Exception as e:
        # Handle other potential errors during communication
        logger.error(f"WebSocket error for {client_host}: {type(e).__name__} - {e}") # Changed print to logger
    finally:
        # Ensure connection removal on disconnect or error
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.info(f"WebSocket connection for {client_host} removed. Total clients: {len(active_connections)}") # Changed print to logger
        # Optionally try to close the websocket gracefully if connection is still open
        try:
            await websocket.close()
        except Exception:
            pass # Ignore errors during close after potential prior error
