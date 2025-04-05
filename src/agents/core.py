# START OF FILE src/agents/core.py
import asyncio
import json
import logging # Use logging
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
import os
from pathlib import Path

from src.config.settings import settings, BASE_DIR
from src.llm_providers.base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict
# Import ToolExecutor instructions constant
from src.tools.executor import TOOL_USAGE_INSTRUCTIONS


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.tools.executor import ToolExecutor
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Constants
MAX_TOOL_CALLS_PER_TURN = 5

AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_PROCESSING = "processing"
AGENT_STATUS_AWAITING_TOOL = "awaiting_tool_result"
AGENT_STATUS_EXECUTING_TOOL = "executing_tool"
AGENT_STATUS_ERROR = "error"

# Define MessageHistory type alias for clarity
MessageHistory = List[MessageDict]


class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating via an injected LLM provider, managing its sandbox,
    and orchestrating tool use based on provider responses. Tracks its own status.
    Injects standard tool usage instructions into the system prompt.
    """
    def __init__(
        self,
        agent_config: Dict[str, Any],
        llm_provider: BaseLLMProvider,
        tool_executor: Optional['ToolExecutor'] = None,
        manager: Optional['AgentManager'] = None
        ):
        """ Initializes an Agent instance. """
        config: Dict[str, Any] = agent_config.get("config", {})
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")

        self.provider_name: str = config.get("provider", settings.DEFAULT_AGENT_PROVIDER)
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        # Store the user-defined system prompt separately
        self.user_defined_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA)
        self.provider_kwargs = {k: v for k, v in config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url', 'referer']}

        self.llm_provider: BaseLLMProvider = llm_provider
        self.tool_executor: Optional['ToolExecutor'] = tool_executor
        self.manager: Optional['AgentManager'] = manager

        self.status: str = AGENT_STATUS_IDLE
        self.current_tool_info: Optional[Dict[str, str]] = None
        # History is now dynamically constructed in process_message based on prompt modifications
        # self.message_history: MessageHistory = [] # Remove fixed history init

        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"

        logger.info(f"Agent {self.agent_id} ({self.persona}) initialized. Status: {self.status}. Provider: {self.provider_name}, Model: {self.model}. Sandbox: {self.sandbox_path}. Tool Executor Set: {bool(self.tool_executor)}")


    def set_status(self, new_status: str, tool_info: Optional[Dict[str, str]] = None):
        """Updates the agent's status and optionally tool info."""
        self.status = new_status
        self.current_tool_info = tool_info if new_status == AGENT_STATUS_EXECUTING_TOOL else None
        # logger.debug(f"Agent {self.agent_id} status changed to: {self.status}" + (f" (Tool: {self.current_tool_info})" if self.current_tool_info else ""))
        if self.manager:
            asyncio.create_task(self.manager.push_agent_status_update(self.agent_id))
        else:
             logger.warning(f"Agent {self.agent_id}: Manager not set, cannot push status update.")


    def set_manager(self, manager: 'AgentManager'):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def set_tool_executor(self, tool_executor: 'ToolExecutor'):
        """Sets a reference to the ToolExecutor."""
        self.tool_executor = tool_executor
        logger.info(f"Agent {self.agent_id}: ToolExecutor reference set post-init.")

    def ensure_sandbox_exists(self) -> bool:
        """Creates the agent's sandbox directory if it doesn't exist."""
        try:
            self.sandbox_path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            logger.error(f"Error creating sandbox directory for Agent {self.agent_id} at {self.sandbox_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}", exc_info=True)
            return False

    def _construct_final_system_prompt(self) -> str:
        """Constructs the system prompt including standard tool instructions if tools are available."""
        final_prompt = self.user_defined_system_prompt

        # Check if tool executor exists and has tools registered
        if self.tool_executor and self.tool_executor.tools:
            # Append the standard instructions
            # We don't need to list the tools here, as they are passed via the 'tools' parameter
            final_prompt += "\n\n" + TOOL_USAGE_INSTRUCTIONS # Use the imported constant
            logger.debug(f"Agent {self.agent_id}: Appended tool usage instructions to system prompt.")
        # else:
        #      logger.debug(f"Agent {self.agent_id}: No tools available or executor not set. Using original system prompt.")

        return final_prompt.strip() # Remove potential leading/trailing whitespace

    async def process_message(self, message_content: str) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Processes an incoming message using the injected LLM provider,
        constructs the final system prompt including tool instructions,
        handles the tool call loop, updates agent status, and yields events.
        """
        if self.status != AGENT_STATUS_IDLE:
            logger.warning(f"Agent {self.agent_id} is not idle (Status: {self.status}). Ignoring message: {message_content[:50]}...")
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Busy - Status: {self.status}]"}
            return

        if not self.llm_provider:
            logger.error(f"Agent {self.agent_id}: LLM Provider not set.")
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "agent_id": self.agent_id, "content": "[Agent Error: LLM Provider not configured]"}
            return

        self.set_status(AGENT_STATUS_PROCESSING)
        logger.info(f"Agent {self.agent_id} starting processing via {self.provider_name}: {message_content[:100]}...")

        provider_stream = None # Define provider_stream here to ensure it's available in finally

        try:
            if not self.ensure_sandbox_exists():
                 self.set_status(AGENT_STATUS_ERROR)
                 yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: Could not ensure sandbox directory {self.sandbox_path}]"}
                 return

            # --- Construct Message History ---
            # 1. Get the potentially modified system prompt
            final_system_prompt = self._construct_final_system_prompt()

            # 2. Build the history for this specific call
            current_history: MessageHistory = []
            if final_system_prompt:
                current_history.append({"role": "system", "content": final_system_prompt})
            # TODO: Add previous conversation history here if implementing context memory later
            current_history.append({"role": "user", "content": message_content})

            # --- Prepare tool schemas if tool executor is available ---
            tool_schemas: Optional[List[ToolDict]] = None
            if self.tool_executor:
                tool_schemas = self.tool_executor.get_tool_schemas()
                # Log the schemas being sent (at debug level)
                if tool_schemas:
                    logger.debug(f"Agent {self.agent_id}: Providing tool schemas to LLM: {[s['name'] for s in tool_schemas]}")
                # else: logger.debug(f"Agent {self.agent_id}: Tool executor set, but no tool schemas found.")


            # --- Call the provider's stream_completion method ---
            provider_stream = self.llm_provider.stream_completion(
                messages=current_history,
                model=self.model,
                temperature=self.temperature,
                tools=tool_schemas, # Pass the schemas here
                tool_choice="auto",
                **self.provider_kwargs
            )

            # --- Iterate through the provider's event stream ---
            async for event in provider_stream:
                event_type = event.get("type")

                if event_type == "response_chunk":
                    if self.status != AGENT_STATUS_PROCESSING:
                        self.set_status(AGENT_STATUS_PROCESSING)
                    yield event
                elif event_type == "status":
                    event["agent_id"] = self.agent_id # Ensure agent ID is present
                    yield event
                elif event_type == "error":
                    event["agent_id"] = self.agent_id # Ensure agent ID is present
                    event["provider"] = self.provider_name # Add provider info
                    self.set_status(AGENT_STATUS_ERROR)
                    yield event
                    logger.warning(f"Agent {self.agent_id}: Received error event from provider {self.provider_name}, stopping processing.")
                    break # Stop processing loop on provider error

                elif event_type == "tool_requests":
                    self.set_status(AGENT_STATUS_AWAITING_TOOL)
                    tool_calls_requested = event.get("calls")
                    if not tool_calls_requested or not isinstance(tool_calls_requested, list):
                        logger.error(f"Agent {self.agent_id}: Received invalid 'tool_requests' event: {event}")
                        self.set_status(AGENT_STATUS_ERROR)
                        yield {"type": "error", "agent_id": self.agent_id, "content": "[Agent Error: Invalid tool request format from provider]"}
                        break

                    logger.info(f"Agent {self.agent_id}: Forwarding {len(tool_calls_requested)} tool requests to manager. Status: {self.status}")
                    try:
                        # Yield requests and wait for manager to send results via asend()
                        # The provider_stream generator needs to be designed to handle this send
                        tool_results: Optional[List[ToolResultDict]] = await provider_stream.asend(tool_calls_requested)

                        # Once results are received, status should go back to processing (or handled by provider)
                        # We rely on the provider to manage its internal state after receiving results.
                        # Agent status will change to EXECUTING_TOOL via manager, then back to AWAITING,
                        # then PROCESSING when results are fed back into the provider stream here.
                        if self.status == AGENT_STATUS_AWAITING_TOOL:
                             # If status didn't change during execution (e.g. fast tool), set back to processing
                             self.set_status(AGENT_STATUS_PROCESSING)

                        if tool_results is None:
                             logger.warning(f"Agent {self.agent_id}: Manager sent back None for tool results. Provider should handle.")
                             # Don't set status to error here, let the provider decide how to proceed

                    except StopAsyncIteration:
                        logger.info(f"Agent {self.agent_id}: Provider generator finished after receiving tool results.")
                        break
                    except Exception as e:
                        logger.error(f"Agent {self.agent_id}: Error sending tool results back to provider stream: {e}", exc_info=True)
                        self.set_status(AGENT_STATUS_ERROR)
                        yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: Failed sending results to provider: {e}]"}
                        break

                else:
                    logger.warning(f"Agent {self.agent_id}: Received unknown event type '{event_type}' from provider.")

            # --- After the provider stream finishes ---
            # History management could be added here if needed for multi-turn context
            logger.info(f"Agent {self.agent_id}: Provider stream finished.")


        except Exception as e:
            # Catch unexpected errors in the agent's own logic
            logger.exception(f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}")
            self.set_status(AGENT_STATUS_ERROR)
            yield {"type": "error", "agent_id": self.agent_id, "content": f"[Agent Error: Unexpected internal error - {type(e).__name__}]"}
        finally:
            # Close the provider stream generator if it exists and is not already closed
            if provider_stream:
                try:
                    await provider_stream.aclose()
                    # logger.debug(f"Agent {self.agent_id}: Closed provider stream generator.")
                except Exception as close_err:
                    logger.warning(f"Agent {self.agent_id}: Error closing provider stream generator: {close_err}")

            # Set status to Idle unless it's already Error
            if self.status != AGENT_STATUS_ERROR:
                self.set_status(AGENT_STATUS_IDLE)
            logger.info(f"Agent {self.agent_id}: Finished processing cycle. Final Status: {self.status}")


    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent, including detailed status."""
        state = {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "status": self.status,
            "provider": self.provider_name,
            "model": self.model,
            "temperature": self.temperature,
            # "message_history_length": len(self.message_history), # History is dynamic now
            "llm_provider_info": repr(self.llm_provider),
            "sandbox_path": str(self.sandbox_path),
            "tool_executor_set": self.tool_executor is not None,
        }
        if self.status == AGENT_STATUS_EXECUTING_TOOL and self.current_tool_info:
            state["current_tool"] = self.current_tool_info

        return state

    def clear_history(self):
        """Clears the agent's message history. (Currently less relevant as history is built per request)."""
        logger.warning(f"Agent {self.agent_id}: clear_history called, but history is currently built dynamically per request.")
        # If implementing multi-turn memory later, this method would need actual logic.
        pass
