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

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating via an injected LLM provider, managing its sandbox,
    and orchestrating tool use based on provider responses.
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

        # Basic state management
        self.is_busy: bool = False
        self.message_history: MessageHistory = []
        if self.original_system_prompt:
             self.message_history.append({"role": "system", "content": self.original_system_prompt})

        # Sandboxing
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"
        # Note: Directory creation is handled by AgentManager before agent processing starts

        print(f"Agent {self.agent_id} ({self.persona}) initialized. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. LLM Provider Instance: {self.llm_provider}")

    # --- Dependency Setters (Can be used if not injected at init) ---
    def set_manager(self, manager: 'AgentManager'):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def set_tool_executor(self, tool_executor: 'ToolExecutor'):
        """Sets a reference to the ToolExecutor."""
        self.tool_executor = tool_executor
        print(f"Agent {self.agent_id}: ToolExecutor reference set post-init.")

    # Removed initialize_openai_client - provider is injected already initialized
    # Removed update_system_prompt_with_tools - relying on tools parameter now

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
        handles the tool call loop based on provider events, and yields
        standardized events back to the AgentManager.

        Args:
            message_content (str): The user's message content.

        Yields:
            Dict[str, Any]: Events ('response_chunk', 'tool_requests', 'final_response', 'error', 'status').
                           'tool_requests' contains parsed arguments.

        Receives:
            Optional[List[ToolResultDict]]: Results from executed tool calls sent via `asend()`.
        """
        if self.is_busy:
            print(f"Agent {self.agent_id} is busy. Ignoring message: {message_content[:50]}...")
            yield {"type": "error", "content": "[Agent Busy]"}
            return

        if not self.llm_provider:
            print(f"Agent {self.agent_id}: LLM Provider not set.")
            yield {"type": "error", "content": "[Agent Error: LLM Provider not configured]"}
            return

        self.is_busy = True
        print(f"Agent {self.agent_id} processing message via {self.provider_name}: {message_content[:100]}...")
        # Ensure manager reference exists before sending UI update
        if self.manager:
            await self.manager._send_to_ui({"type": "status", "agent_id": self.agent_id, "content": f"Agent '{self.agent_id}' processing..."})
        else:
            print(f"Agent {self.agent_id}: Warning - Manager not set, cannot send status updates to UI.")


        try:
            # 1. Ensure sandbox exists (important pre-check for file system tool)
            if not self.ensure_sandbox_exists():
                 yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox directory {self.sandbox_path}]"}
                 self.is_busy = False
                 return

            # 2. Add user message to *local* history copy for this request cycle
            # The provider manages its own internal history if needed for multi-turn tool calls
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
                    yield event # Forward chunk directly
                elif event_type == "status":
                    # Add agent_id to status messages from provider
                    event["agent_id"] = self.agent_id
                    yield event
                elif event_type == "error":
                     # Add agent_id and potentially provider info to errors
                    event["agent_id"] = self.agent_id
                    event["content"] = f"[{self.provider_name} Error] {event.get('content', 'Unknown provider error')}"
                    yield event
                    # Assume provider handles its internal state on error, but agent should stop processing
                    print(f"Agent {self.agent_id}: Received error event from provider, stopping.")
                    break # Stop processing loop on provider error

                # --- Handle Tool Requests ---
                elif event_type == "tool_requests":
                    tool_calls_requested = event.get("calls") # Expect list of {'id':.., 'name':.., 'arguments':{}}
                    if not tool_calls_requested or not isinstance(tool_calls_requested, list):
                        print(f"Agent {self.agent_id}: Received invalid 'tool_requests' event: {event}")
                        yield {"type": "error", "agent_id": self.agent_id, "content": "[Agent Error: Invalid tool request format from provider]"}
                        break

                    # Yield the requests to the AgentManager for execution
                    # The agent doesn't need to know *how* they are executed, just gets results back
                    print(f"Agent {self.agent_id}: Forwarding {len(tool_calls_requested)} tool requests to manager.")
                    try:
                        # Yield the requests and wait for the manager to send results
                        tool_results: Optional[List[ToolResultDict]] = await provider_stream.asend(tool_calls_requested)

                        # Provider's stream_completion should handle receiving these results
                        # and continuing its interaction with the LLM API.
                        # Agent doesn't need to explicitly manage history append for tool results here,
                        # as the provider handles the interaction loop that requires it.

                        if tool_results is None:
                             # This indicates the Manager failed to get results. Provider might have already yielded an error.
                             print(f"Agent {self.agent_id}: Manager sent back None for tool results. Provider should handle or error out.")
                             # We might not need to do anything here, rely on provider's error reporting.

                    except StopAsyncIteration:
                        # This means the provider's generator finished *after* we sent results.
                        print(f"Agent {self.agent_id}: Provider generator finished after receiving tool results.")
                        break # Exit the loop
                    except Exception as e:
                        print(f"Agent {self.agent_id}: Error sending tool results back to provider stream: {e}")
                        yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: Failed sending results to provider: {e}]"}
                        break # Stop processing on send error

                # --- Handle Unknown Event Types ---
                else:
                    print(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")
                    # Optionally yield a warning or ignore

            # --- After the provider stream finishes ---
            # Update the main message history with the final state from this interaction?
            # This is tricky because the provider manages the intermediate steps.
            # For now, let's NOT update self.message_history here to avoid inconsistencies.
            # History management might need refinement. Let's assume history is contained within a single call cycle for now.
            # If multi-turn conversations need more robust history, the Agent might need to
            # reconstruct it based on yielded events, or the provider needs to return the final history state.
            # Simpler approach: AgentManager clears history or Agent clears history before next user message.
            print(f"Agent {self.agent_id}: Provider stream finished.")
            # Yield a final marker? Let manager handle final status.
            # yield {"type": "final_response", "content": ""} # Or maybe the last accumulated chunk?

        except Exception as e:
            # Catch unexpected errors in the agent's own logic
            import traceback
            traceback.print_exc()
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: {error_msg}]"}
        finally:
            self.is_busy = False
            print(f"Agent {self.agent_id}: Finished processing cycle.")
            # Let manager send final UI status based on generator completion

    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent."""
        return {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "is_busy": self.is_busy,
            "provider": self.provider_name,
            "model": self.model,
            "temperature": self.temperature,
            "message_history_length": len(self.message_history), # Reflects initial history, not necessarily intermediate states
            "llm_provider_info": repr(self.llm_provider), # Get representation from provider
            "sandbox_path": str(self.sandbox_path),
            "tool_executor_set": self.tool_executor is not None,
        }

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt."""
        print(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        if self.original_system_prompt:
             self.message_history.append({"role": "system", "content": self.original_system_prompt})
