# START OF FILE src/agents/core.py
import asyncio
import openai
import json # Import json for parsing tool arguments if needed (though OpenAI lib handles it)
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
import os # Import os for path operations
from pathlib import Path # Import Path for object-oriented paths

# Import settings to access the API key and defaults
from src.config.settings import settings, BASE_DIR # Import BASE_DIR too

# Import ToolExecutor for type hinting
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.tools.executor import ToolExecutor
    from src.agents.manager import AgentManager # Add AgentManager for type hint

# Define message history structure (matches OpenAI's format)
MessageDict = Dict[str, Any] # e.g., {"role": "user", "content": "Hello"} or {"role": "assistant", "tool_calls": [...]} or {"role": "tool", ...}
MessageHistory = List[MessageDict]

# --- Constants ---
MAX_TOOL_CALLS_PER_TURN = 5 # Safety limit for tool call loops

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating with an LLM, managing its sandbox, and using tools via OpenAI's tool-calling feature.
    """
    def __init__(self, agent_config: Dict[str, Any]):
        """
        Initializes an Agent instance using a configuration dictionary.

        Args:
            agent_config (Dict[str, Any]): The configuration dictionary for this agent,
                                           typically loaded from config.yaml via settings.
                                           Expected structure: {'agent_id': '...', 'config': {'model': ..., 'system_prompt': ...}}
        """
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")
        config: Dict[str, Any] = agent_config.get("config", {}) # Get the nested config dict

        # Load agent parameters from config, falling back to defaults from settings
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        # Store the *original* system prompt. Tool descriptions might be added later by the manager.
        self.original_system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA) # Added persona

        # Basic state management
        self.is_busy: bool = False
        self.message_history: MessageHistory = []
        # Initialize history with the system prompt
        if self.original_system_prompt:
             self.message_history.append({"role": "system", "content": self.original_system_prompt})

        # References set by AgentManager
        self.manager: Optional['AgentManager'] = None
        self.tool_executor: Optional['ToolExecutor'] = None

        # OpenAI client - will be initialized by initialize_openai_client()
        self._openai_client: Optional[openai.AsyncOpenAI] = None

        # --- Sandboxing ---
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"
        # Note: Directory creation is handled by the AgentManager after __init__

        print(f"Agent {self.agent_id} ({self.persona}) initialized with model {self.model}. Sandbox: {self.sandbox_path}")

    def set_manager(self, manager: 'AgentManager'):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def set_tool_executor(self, tool_executor: 'ToolExecutor'):
        """Sets a reference to the ToolExecutor."""
        self.tool_executor = tool_executor
        print(f"Agent {self.agent_id}: ToolExecutor reference set.")
        # Update system prompt if tool executor is set? - Let Manager handle this maybe
        # self.update_system_prompt_with_tools() # Example

    # --- System Prompt Handling (Consider letting Manager control this) ---
    def update_system_prompt_with_tools(self):
        """ Updates the 'system' message in history with current tool descriptions. """
        if not self.tool_executor:
            return # No tools to describe

        tool_descriptions = self.tool_executor.get_formatted_tool_descriptions()
        updated_prompt = self.original_system_prompt + "\n\n" + tool_descriptions

        # Find and update the system message in history, or add it if missing
        found = False
        for msg in self.message_history:
            if msg["role"] == "system":
                msg["content"] = updated_prompt
                found = True
                break
        if not found:
            self.message_history.insert(0, {"role": "system", "content": updated_prompt})

        print(f"Agent {self.agent_id}: System prompt updated with tool descriptions.")


    def initialize_openai_client(self, api_key: Optional[str] = None) -> bool:
        """
        Initializes the OpenAI async client.
        Uses the provided key, otherwise falls back to the key from settings.
        Returns True on success, False on failure.
        """
        resolved_api_key = api_key or settings.OPENAI_API_KEY

        if not resolved_api_key:
            print(f"Warning: OpenAI API key not available for Agent {self.agent_id}. LLM calls will fail.")
            self._openai_client = None
            return False

        if self._openai_client:
             return True

        try:
            self._openai_client = openai.AsyncOpenAI(api_key=resolved_api_key)
            print(f"OpenAI client initialized successfully for Agent {self.agent_id}.")
            return True
        except Exception as e:
            print(f"Error initializing OpenAI client for Agent {self.agent_id}: {e}")
            self._openai_client = None
            return False

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
    async def process_message(self, message_content: str) -> AsyncGenerator[Dict[str, Any], Optional[List[Dict[str, Any]]]]:
        """
        Processes an incoming message, interacts with the LLM (potentially using tools),
        and yields events back to the caller (AgentManager).

        This is an async generator that yields dictionaries representing events:
        - {'type': 'response_chunk', 'content': '...'} : Regular text streamed from LLM.
        - {'type': 'tool_request', 'call': { 'id': '...', 'name': '...', 'arguments': '...' }} : When LLM requests a *single* tool call. (Yield one per call needed).
        - {'type': 'final_response', 'content': '...'} : The final non-tool text response.
        - {'type': 'error', 'content': '...'} : If an error occurs.

        It can receive tool results via `generator.send(list_of_tool_results)`.
        The `list_of_tool_results` should be: [{'call_id': '...', 'content': '...'}, ...]

        Args:
            message_content (str): The user's message content.

        Yields:
            Dict[str, Any]: Events describing the processing steps.

        Receives:
            Optional[List[Dict[str, Any]]]: Results from executed tool calls sent via `send()`.
        """
        if self.is_busy:
            print(f"Agent {self.agent_id} is busy. Ignoring message: {message_content[:50]}...")
            yield {"type": "error", "content": "[Agent Busy]"}
            return

        if not self._openai_client:
            print(f"Agent {self.agent_id}: OpenAI client not initialized.")
            if not self.initialize_openai_client():
                 yield {"type": "error", "content": "[Agent Error: OpenAI Client not configured or initialization failed]"}
                 return

        self.is_busy = True
        print(f"Agent {self.agent_id} processing message: {message_content[:100]}...")
        await self.manager._send_to_ui({"type": "status", "agent_id": self.agent_id, "content": f"Agent '{self.agent_id}' processing..."}) # Notify UI

        try:
            # 1. Ensure sandbox exists (relevant for file-system tools)
            if not self.ensure_sandbox_exists():
                 yield {"type": "error", "content": f"[Agent Error: Could not ensure sandbox directory {self.sandbox_path}]"}
                 self.is_busy = False
                 return

            # 2. Add user message to history
            self.message_history.append({"role": "user", "content": message_content})

            # --- Tool Calling Loop ---
            tool_call_attempts = 0
            while tool_call_attempts < MAX_TOOL_CALLS_PER_TURN:

                # 3. Prepare for LLM Call
                tool_schemas = self.tool_executor.get_tool_schemas() if self.tool_executor else None
                print(f"Agent {self.agent_id}: Calling LLM. History length: {len(self.message_history)}. Tools available: {bool(tool_schemas)}")

                # --- Make the OpenAI API Call (Streaming) ---
                try:
                    response_stream = await self._openai_client.chat.completions.create(
                        model=self.model,
                        messages=self.message_history,
                        temperature=self.temperature,
                        tools=tool_schemas, # Pass tool schemas
                        tool_choice="auto", # Let the model decide
                        stream=True
                    )
                except Exception as api_error:
                     # Handle API errors during the call itself
                     error_msg = f"OpenAI API Error during call for Agent {self.agent_id}: {api_error}"
                     print(error_msg)
                     yield {"type": "error", "content": f"[Agent Error: {error_msg}]"}
                     # Potentially break the loop or return depending on severity
                     break # Exit the tool loop on API error

                # --- Process the Stream ---
                assistant_response_content = ""
                accumulated_tool_calls = [] # To hold tool calls from the stream
                current_tool_call_chunks = {} # {call_id: {'name': '...', 'arguments': '...'}}

                async for chunk in response_stream:
                    delta = chunk.choices[0].delta

                    # --- Content Chunks ---
                    if delta.content:
                        assistant_response_content += delta.content
                        yield {"type": "response_chunk", "content": delta.content}

                    # --- Tool Call Chunks ---
                    if delta.tool_calls:
                        for tool_call_chunk in delta.tool_calls:
                            call_id = tool_call_chunk.id
                            if call_id: # New tool call starts
                                if call_id not in current_tool_call_chunks:
                                     current_tool_call_chunks[call_id] = {
                                         "id": call_id,
                                         "name": tool_call_chunk.function.name if tool_call_chunk.function else "",
                                         "arguments": ""
                                     }
                                     print(f"Agent {self.agent_id}: Started receiving tool call '{current_tool_call_chunks[call_id]['name']}' (ID: {call_id})")

                            # Accumulate argument chunks for the current call_id
                            if call_id in current_tool_call_chunks and tool_call_chunk.function and tool_call_chunk.function.arguments:
                                current_tool_call_chunks[call_id]["arguments"] += tool_call_chunk.function.arguments

                # --- Stream Finished - Check for Completed Tool Calls ---
                for call_id, call_info in current_tool_call_chunks.items():
                     # Validate if name and arguments seem complete (basic check)
                     if call_info["name"] and call_info["arguments"]:
                         # Parse the accumulated arguments JSON string
                         try:
                             parsed_args = json.loads(call_info["arguments"])
                             accumulated_tool_calls.append({
                                 "id": call_id,
                                 "type": "function", # OpenAI uses 'function' here
                                 "function": {
                                     "name": call_info["name"],
                                     "arguments": call_info["arguments"] # Keep as string for OpenAI history
                                 }
                             })
                             print(f"Agent {self.agent_id}: Completed tool call request: ID={call_id}, Name={call_info['name']}, Args={call_info['arguments']}")
                         except json.JSONDecodeError as e:
                             print(f"Agent {self.agent_id}: Failed to decode JSON arguments for tool call {call_id}: {e}. Args received: '{call_info['arguments']}'")
                             # Decide how to handle - skip this tool call? Send error message?
                             # For now, let's skip it and potentially yield an error?
                             yield {"type": "error", "content": f"[Agent Error: Failed to parse arguments for tool {call_info['name']} (ID: {call_id})]"}


                # --- Post-Stream Processing ---

                # 4. Append Assistant Message to History (content and/or tool calls)
                assistant_message: MessageDict = {"role": "assistant"}
                if assistant_response_content:
                    assistant_message["content"] = assistant_response_content
                if accumulated_tool_calls:
                     # Note: OpenAI expects 'tool_calls' not 'tool_call' in the history message
                     assistant_message["tool_calls"] = accumulated_tool_calls
                # Only add if it has content or tool calls
                if assistant_message.get("content") or assistant_message.get("tool_calls"):
                    self.message_history.append(assistant_message)


                # 5. Check if Tool Calls Were Made
                if not accumulated_tool_calls:
                    # No tool calls, this is the final response (or just content before tool calls)
                    if assistant_response_content:
                         # We already yielded chunks, maybe yield a final marker?
                         yield {"type": "final_response", "content": assistant_response_content}
                         print(f"Agent {self.agent_id}: Finished with final response.")
                    else:
                         # No content and no tool calls - unusual, maybe an error state or empty response?
                         print(f"Agent {self.agent_id}: Finished with no content and no tool calls.")
                         yield {"type": "status", "content": "[Agent finished with empty response]"}
                    break # Exit the while loop

                # --- Tools were called ---
                tool_call_attempts += 1
                print(f"Agent {self.agent_id}: Requesting execution for {len(accumulated_tool_calls)} tool call(s). Attempt {tool_call_attempts}/{MAX_TOOL_CALLS_PER_TURN}.")

                # 6. Yield Tool Requests and Receive Results
                # We need to yield *requests* and get *results* back via send()
                # Structure to yield: {'type': 'tool_requests', 'calls': [{'id': ..., 'name': ..., 'arguments': ...}, ...]}
                requests_to_yield = []
                for call in accumulated_tool_calls:
                     # Need to re-parse the arguments string here for the executor
                     try:
                         args_dict = json.loads(call["function"]["arguments"])
                         requests_to_yield.append({
                             "id": call["id"],
                             "name": call["function"]["name"],
                             "arguments": args_dict
                         })
                     except json.JSONDecodeError:
                          # We already logged an error during accumulation, maybe yield placeholder result?
                          # Or the Manager should handle this when it fails to execute.
                          print(f"Agent {self.agent_id}: Skipping request for tool call {call['id']} due to previous argument parsing error.")


                if not requests_to_yield:
                     # If all tool calls failed parsing, break the loop?
                     yield {"type": "error", "content": "[Agent Error: All tool call arguments failed to parse.]"}
                     break

                # Yield the requests and wait for the manager to send results
                # The `yield` expression itself *receives* the sent value
                tool_results: Optional[List[Dict[str, Any]]] = yield {"type": "tool_requests", "calls": requests_to_yield}

                # 7. Process Tool Results
                if tool_results is None:
                     # Manager might send None if something went wrong upstream
                     print(f"Agent {self.agent_id}: Did not receive tool results back from manager. Aborting tool loop.")
                     yield {"type": "error", "content": "[Agent Error: Failed to get tool results from manager]"}
                     break

                print(f"Agent {self.agent_id}: Received {len(tool_results)} tool result(s) from manager.")

                # Append results to history for the next LLM iteration
                results_appended = 0
                for result in tool_results:
                     if "call_id" in result and "content" in result:
                         self.message_history.append({
                             "role": "tool",
                             "tool_call_id": result["call_id"],
                             "content": result["content"] # Content should be string result from tool execution
                         })
                         results_appended += 1
                     else:
                         print(f"Agent {self.agent_id}: Received invalid tool result format: {result}")

                if results_appended == 0:
                     print(f"Agent {self.agent_id}: No valid tool results appended to history. Aborting loop.")
                     yield {"type": "error", "content": "[Agent Error: No valid tool results processed]"}
                     break

                # Loop continues for the next LLM call...

            # End of while loop (either break or max attempts reached)
            if tool_call_attempts >= MAX_TOOL_CALLS_PER_TURN:
                 print(f"Agent {self.agent_id}: Reached maximum tool call attempts ({MAX_TOOL_CALLS_PER_TURN}).")
                 yield {"type": "error", "content": f"[Agent Error: Reached maximum tool call limit ({MAX_TOOL_CALLS_PER_TURN})]"}


        except openai.APIAuthenticationError as e:
            error_msg = f"OpenAI Authentication Error for Agent {self.agent_id}: Check API key ({e})"
            print(error_msg)
            yield {"type": "error", "content": f"[Agent Error: {error_msg}]"}
            self._openai_client = None # Invalidate client
        except openai.APIError as e:
            error_msg = f"OpenAI API Error for Agent {self.agent_id}: {e}"
            print(error_msg)
            yield {"type": "error", "content": f"[Agent Error: {error_msg}]"}
        except Exception as e:
            import traceback
            traceback.print_exc() # Print full traceback for unexpected errors
            error_msg = f"Unexpected Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            yield {"type": "error", "content": f"[Agent Error: {error_msg}]"}
        finally:
            self.is_busy = False
            print(f"Agent {self.agent_id} finished processing cycle.")
            # Final status update to UI? The manager might handle this based on the generator stopping.
            # await self.manager._send_to_ui({"type": "status", "agent_id": self.agent_id, "content": f"Agent '{agent.agent_id}' processing finished."})


    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent."""
        return {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "is_busy": self.is_busy,
            "model": self.model,
            "original_system_prompt": self.original_system_prompt,
            "temperature": self.temperature,
            "message_history_length": len(self.message_history),
            "client_initialized": self._openai_client is not None,
            "sandbox_path": str(self.sandbox_path),
            "tool_executor_set": self.tool_executor is not None,
        }

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt if one exists."""
        print(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        # Re-add the potentially tool-augmented system prompt if tools are enabled
        # Or just re-add the original one? Let's re-add original and let Manager handle tools if needed.
        if self.original_system_prompt:
             # Check if a system prompt with tools was potentially added and use that?
             # Safer to just reset to original, manager can re-apply tools if needed.
             self.message_history.append({"role": "system", "content": self.original_system_prompt})
             # If tools are active, maybe re-run update_system_prompt_with_tools() here?
             # self.update_system_prompt_with_tools()
