# START OF FILE src/agents/core.py
import asyncio
import json # Still needed for potential argument parsing debugging, maybe remove later
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
import os # For os.urandom fallback
from pathlib import Path

# Import settings for defaults and BASE_DIR
from src.config.settings import settings, BASE_DIR

# Import BaseLLMProvider for type hinting and interface adherence
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

# Import ToolExecutor for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.tools.executor import ToolExecutor
    from src.agents.manager import AgentManager

# Constants
MAX_TOOL_CALLS_PER_TURN = 5 # Safety limit remains relevant for the agent's loop logic

# Define possible agent statuses
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing" # General thinking/interacting with LLM
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error" # Added error state

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating via an injected LLM provider, managing its sandbox,
    and orchestrating tool use based on provider responses. Tracks its own status.
    """
    def __init__(
        self,
        agent_config: Dict[str, Any],
        llm_provider: BaseLLMProvider, # Inject the provider instance
        tool_executor: Optional['ToolExecutor'] = None, # Allow injection at init
        manager: Optional['AgentManager'] = None # Allow injection at init
        ):
        """
        Initializes an Agent instance using configuration and injected dependencies.

        Args:
            agent_config (Dict[str, Any]): Configuration dictionary for this agent.
                                           Expected: {'agent_id': '...', 'config': {'provider':..., 'model':...}}
            llm_provider (BaseLLMProvider): An initialized instance of an LLM provider.
            tool_executor (Optional['ToolExecutor']): An instance of the tool executor.
            manager (Optional['AgentManager']): A reference to the agent manager.
        """
        config: Dict[str, Any] = agent_config.get("config", {})
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")

        # Core configuration from agent_config, falling back to global defaults
        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER) # Store provider name
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        self.original_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        # Store any additional kwargs from config that might be passed to the provider
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']} # Exclude known top-level/handled keys


        # Injected dependencies
        self.llm_provider: BaseLLMProvider = llm_provider
        self.tool_executor: Optional['ToolExecutor'] = tool_executor
        self.manager: Optional['AgentManager'] = manager

        # State management
        self.status: str = AGENT_STATUS_IDLE # Current detailed status
        self.current_tool_info: Optional[Dict[str, str]] = None # e.g., {'name': 'file_system', 'call_id': '...'}
        self.message_history: MessageHistory = []
        if self.original_system_prompt:
             self.message_history.append({"role": "system", "content": self.original_system_prompt})

        # Sandboxing
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"
        # Note: Directory creation is handled by AgentManager before agent processing starts

        print(f"Agent {self.agent_id} ({self.persona}) initialized. Status: {self.status}. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. LLM Provider Instance: {self.llm_provider}")

    # --- Status Management ---
    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None):
        """Updates the agent's status and optionally tool info."""
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        # print(f"Agent {self.agent_id} status changed to: {self.status}" + (f" (Tool: {self.current_tool_info})" if self.current_tool_info else ""))
        # Trigger notification through manager
        if self.manager:
            # Schedule the status update instead of awaiting it here
            asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else:
             print(f"Agent {self.agent_id}: Warning - Manager not set, cannot push status update.")


    # --- Dependency Setters (Remain the same) ---
    def set_manager(self, manager: 'AgentManager'):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def set_tool_executor(self, tool_executor: 'ToolExecutor'):
        """Sets a reference to the ToolExecutor."""
        self.tool_executor = tool_executor
        print(f"Agent {self.agent_id}: ToolExecutor reference set post-init.")

    def ensure_sandbox_exists(self) -> bool:
        """Creates the agent's sandbox directory if it doesn't exist."""
        try:
            self.sandbox_path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            print(f"Error creating sandbox directory for Agent {self.agent_id} at {self.sandbox_path}: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}")
            return False

    # --- Main Processing Logic ---
    async def process_message(self, message_content: str) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Processes an incoming message using the injected LLM provider,
        handles the tool call loop based on provider events, updates agent status,
        and yields standardized events back to the AgentManager.

        Args:
            message_content (str): The user's message content.

        Yields:
            Dict[str, Any]: Events ('response_chunk', 'tool_requests', 'final_response', 'error', 'status').
                           'tool_requests' contains parsed arguments.

        Receives:
            Optional[List[ToolResultDict]]: Results from executed tool calls sent via `asend()`.
        """
        if self.status != AGENT_STATUS_IDLE:
            print(f"Agent {self.agent_id} is not idle (Status: {self.status}). Ignoring message: {message_content[:50]}...")
            yield {"type": "error", "content": f"[Agent Busy - Status: {self.status}]"}
            return

        if not self.llm_provider:
            print(f"Agent {self.agent_id}: LLM Provider not set.")
            self.set_status(AGENT_STATUS_ERROR) # Set error status
            yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}
            return

        self.set_status(AGENT_STATUS_PROCESSING)
        print(f"Agent {self.agent_id} starting processing via {self.provider_name}: {message_content[:100]}...")
        # Initial status sent via set_status call above
        # Manager no longer needs to send initial processing message

        try:
            # 1. Ensure sandbox exists (important pre-check for file system tool)
            if not self.ensure_sandbox_exists():
                 self.set_status(AGENT_STATUS_ERROR)
                 yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox directory {self.sandbox_path}]"}
                 return

            # 2. Add user message to *local* history copy for this request cycle
            current_history = list(self.message_history)
            current_history.append({"role": "user", "content": message_content})

            # 3. Prepare tool schemas if tool executor is available
            tool_schemas: Optional[List[ToolDict]] = None
            if self.tool_executor:
                tool_schemas = self.tool_executor.get_tool_schemas()
                print(f"Agent {self.agent_id}: Tools available for provider: {bool(tool_schemas)}")


            # 4. Call the provider's stream_completion method
            provider_stream = self.llm_provider.stream_completion(
                messages=current_history,
                model=self.model,
                temperature=self.temperature,
                tools=tool_schemas,
                tool_choice="auto", # Or pass from config if needed
                # Pass any additional kwargs specific to this agent's config
                **self.provider_kwargs
            )

            # 5. Iterate through the provider's event stream
            async for event in provider_stream:
                event_type = event.get("type")

                # --- Pass through simple events ---
                if event_type == "response_chunk":
                    # If status was something else (like awaiting tool), change back to processing
                    if self.status != AGENT_STATUS_PROCESSING:
                        self.set_status(AGENT_STATUS_PROCESSING)
                    yield event # Forward chunk directly
                elif event_type == "status":
                    # Add agent_id to status messages from provider
                    event["agent_id"] = self.agent_id
                    yield event
                elif event_type == "error":
                     # Add agent_id and potentially provider info to errors
                    event["agent_id"] = self.agent_id
                    event["content"] = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    self.set_status(AGENT_STATUS_ERROR) # Set error status
                    yield event
                    # Assume provider handles its internal state on error, but agent should stop processing
                    print(f"Agent {self.agent_id}: Received error event from provider, stopping.")
                    break # Stop processing loop on provider error

                # --- Handle Tool Requests ---
                elif event_type == "tool_requests":
                    # Change status before yielding/awaiting results
                    self.set_status(AGENT_STATUS_AWAITING_TOOL)
                    tool_calls_requested = event.get("calls") # Expect list of {'id':.., 'name':.., 'arguments':{}}
                    if not tool_calls_requested or not isinstance(tool_calls_requested, list):
                        print(f"Agent {self.agent_id}: Received invalid 'tool_requests' event: {event}")
                        self.set_status(AGENT_STATUS_ERROR)
                        yield {"type": "error", "agent_id": self.agent_id, "content": "[Agent Error: Invalid tool request format from provider]"}
                        break

                    # Yield the requests to the AgentManager for execution
                    print(f"Agent {self.agent_id}: Forwarding {len(tool_calls_requested)} tool requests to manager. Status: {self.status}")
                    try:
                        # Yield the requests and wait for the manager to send results
                        tool_results: Optional[List[ToolResultDict]] = await provider_stream.asend(tool_calls_requested)

                        # Once results are received back, status goes back to processing
                        # (The manager will update status to 'executing_tool' while executing)
                        self.set_status(AGENT_STATUS_PROCESSING)

                        if tool_results is None:
                             print(f"Agent {self.agent_id}: Manager sent back None for tool results. Provider should handle or error out.")
                             self.set_status(AGENT_STATUS_ERROR)
                             # Provider should yield an error if it cannot proceed

                    except StopAsyncIteration:
                        print(f"Agent {self.agent_id}: Provider generator finished after receiving tool results.")
                        break # Exit the loop
                    except Exception as e:
                        print(f"Agent {self.agent_id}: Error sending tool results back to provider stream: {e}")
                        self.set_status(AGENT_STATUS_ERROR)
                        yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: Failed sending results to provider: {e}]"}
                        break # Stop processing on send error

                # --- Handle Unknown Event Types ---
                else:
                    print(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
                    # Optionally yield a warning or ignore

            # --- After the provider stream finishes ---
            # History management remains complex, clear history for now.
            print(f"Agent {self.agent_id}: Provider stream finished.")


        except Exception as e:
            # Catch unexpected errors in the agent's own logic
            import traceback
            traceback.print_exc()
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            self.set_status(AGENT_STATUS_ERROR) # Set error status
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]"}
        finally:
            # Set status to Idle unless it's already Error
            if self.status != AGENT_STATUS_ERROR:
                self.set_status(AGENT_STATUS_IDLE)
            print(f"Agent {self.agent_id}: Finished processing cycle. Final Status: {self.status}")


    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent, including detailed status."""
        state = {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "status": self.status, # Use the new status field
            # "is_busy": self.status != AGENT_STATUS_IDLE, # Derive 'busy' from status
            "provider": self.provider_name,
            "model": self.model,
            "temperature": self.temperature,
            "message_history_length": len(self.message_history),
            "llm_provider_info": repr(self.llm_provider),
            "sandbox_path": str(self.sandbox_path),
            "tool_executor_set": self.tool_executor is not None,
        }
        # Add tool info if currently executing a tool
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info:
            state["current_tool"] = self.current_tool_info # e.g., {'name': 'file_system', 'call_id': 'call_123'}

        return state

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt."""
        print(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        if self.original_system_prompt:
             self.message_history.append({"role": "system", "content": self.original_system_prompt})
---
