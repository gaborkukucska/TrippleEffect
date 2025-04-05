# START OF FILE src/llm_providers/openai_provider.py
import openai
import json
import asyncio
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

# --- Constants ---
MAX_TOOL_CALLS_PER_TURN = 5 # Safety limit for tool call loops

class OpenAIProvider(BaseLLMProvider):
    """
    LLM Provider implementation for OpenAI's API.
    Handles streaming completions and tool calls using the openai library.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """
        Initializes the OpenAI async client.

        Args:
            api_key (Optional[str]): The OpenAI API key. If None, uses the OPENAI_API_KEY env var.
            base_url (Optional[str]): Optional base URL (e.g., for Azure OpenAI).
            **kwargs: Additional arguments passed to openai.AsyncOpenAI constructor.
        """
        try:
            self._openai_client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, **kwargs)
            print(f"OpenAIProvider initialized. Client: {self._openai_client}")
        except Exception as e:
            print(f"Error initializing OpenAI client: {e}")
            # Potentially raise or handle more gracefully
            raise ValueError(f"Failed to initialize OpenAI client: {e}") from e

    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = "auto",
        **kwargs # Allow provider-specific parameters like 'user', etc.
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Streams the completion from OpenAI, handling tool calls.

        Args:
            messages (List[MessageDict]): Conversation history.
            model (str): Model name (e.g., "gpt-4-turbo").
            temperature (float): Sampling temperature.
            max_tokens (Optional[int]): Max tokens for completion.
            tools (Optional[List[ToolDict]]): Available tool schemas.
            tool_choice (Optional[str]): Tool usage strategy.
            **kwargs: Additional arguments for the OpenAI API call.

        Yields:
            Dict representing events: 'response_chunk', 'tool_requests', 'error', 'status'.

        Receives:
            Optional[List[ToolResultDict]]: Results of executed tool calls via `asend()`.
        """
        current_messages = list(messages) # Work on a copy
        tool_call_attempts = 0

        print(f"OpenAIProvider: Starting stream_completion with model {model}. History length: {len(current_messages)}. Tools: {bool(tools)}")

        while tool_call_attempts < MAX_TOOL_CALLS_PER_TURN:
            try:
                # --- Prepare API Call Args ---
                api_params = {
                    "model": model,
                    "messages": current_messages,
                    "temperature": temperature,
                    "stream": True,
                    **kwargs # Include any extra provider args
                }
                if max_tokens:
                    api_params["max_tokens"] = max_tokens
                if tools:
                    api_params["tools"] = tools
                    api_params["tool_choice"] = tool_choice

                # --- Make the OpenAI API Call (Streaming) ---
                print(f"OpenAIProvider: Making API call (attempt {tool_call_attempts + 1}). Params: { {k: v for k, v in api_params.items() if k != 'messages'} }")
                response_stream = await self._openai_client.chat.completions.create(**api_params)

                # --- Process the Stream ---
                assistant_response_content = ""
                accumulated_tool_calls = [] # To hold completed tool call requests from the stream
                current_tool_call_chunks = {} # {call_id: {'id':.., 'name': '...', 'arguments': '...'}}

                async for chunk in response_stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta: continue # Skip empty deltas

                    # --- Content Chunks ---
                    if delta.content:
                        assistant_response_content += delta.content
                        yield {"type": "response_chunk", "content": delta.content}

                    # --- Tool Call Chunks ---
                    if delta.tool_calls:
                        for tool_call_chunk in delta.tool_calls:
                            # Need call_id to aggregate argument chunks
                            call_id = tool_call_chunk.id
                            if call_id:
                                if call_id not in current_tool_call_chunks:
                                     current_tool_call_chunks[call_id] = {
                                         "id": call_id,
                                         "name": "", # Name might come in subsequent chunks
                                         "arguments": ""
                                     }
                                     # Store function details if present in this chunk
                                     if tool_call_chunk.function:
                                         if tool_call_chunk.function.name:
                                             current_tool_call_chunks[call_id]["name"] = tool_call_chunk.function.name
                                             print(f"OpenAIProvider: Started receiving tool call '{tool_call_chunk.function.name}' (ID: {call_id})")
                                         if tool_call_chunk.function.arguments:
                                              current_tool_call_chunks[call_id]["arguments"] += tool_call_chunk.function.arguments
                                else:
                                    # Existing call_id, append arguments
                                    if tool_call_chunk.function and tool_call_chunk.function.arguments:
                                        current_tool_call_chunks[call_id]["arguments"] += tool_call_chunk.function.arguments
                            else:
                                print(f"OpenAIProvider: Warning - received tool call chunk without ID: {tool_call_chunk}")


                # --- Stream Finished - Assemble Completed Tool Calls ---
                requests_to_yield = [] # Tool requests formatted for the Agent/Manager
                history_tool_calls = [] # Tool call objects formatted for OpenAI history

                for call_id, call_info in current_tool_call_chunks.items():
                    if call_info["name"] and call_info["arguments"] is not None: # Ensure name was received and arguments string exists
                        try:
                            # Parse arguments string into a dictionary for the Agent/Manager
                            parsed_args = json.loads(call_info["arguments"])

                            # Format for yielding to Agent/Manager
                            requests_to_yield.append({
                                "id": call_id,
                                "name": call_info["name"],
                                "arguments": parsed_args # Yield parsed args
                            })

                            # Format for OpenAI history (needs the unparsed arguments string)
                            history_tool_calls.append({
                                "id": call_id,
                                "type": "function", # OpenAI API uses 'function' here
                                "function": {
                                    "name": call_info["name"],
                                    "arguments": call_info["arguments"] # History needs the original string
                                }
                            })
                            print(f"OpenAIProvider: Completed tool call request: ID={call_id}, Name={call_info['name']}, Args={call_info['arguments']}")

                        except json.JSONDecodeError as e:
                            print(f"OpenAIProvider: Failed to decode JSON arguments for tool call {call_id}: {e}. Args received: '{call_info['arguments']}'")
                            yield {"type": "error", "content": f"[OpenAIProvider Error: Failed to parse arguments for tool {call_info['name']} (ID: {call_id})]. Arguments: '{call_info['arguments']}'"}
                            return # Stop the generator

                # --- Post-Stream Processing ---

                # Append Assistant Message to History (content and/or tool calls)
                assistant_message: MessageDict = {"role": "assistant"}
                if assistant_response_content:
                    assistant_message["content"] = assistant_response_content
                if history_tool_calls:
                     assistant_message["tool_calls"] = history_tool_calls
                # Only add if it has content or tool calls
                if assistant_message.get("content") or assistant_message.get("tool_calls"):
                    current_messages.append(assistant_message)


                # Check if Tool Calls Were Made in this turn
                if not requests_to_yield:
                    # No tool calls, this turn is finished.
                    print(f"OpenAIProvider: Finished turn with final response. Content length: {len(assistant_response_content)}")
                    break # Exit the while loop

                # --- Tools were called ---
                tool_call_attempts += 1
                print(f"OpenAIProvider: Requesting execution for {len(requests_to_yield)} tool call(s). Attempt {tool_call_attempts}/{MAX_TOOL_CALLS_PER_TURN}.")

                # Yield Tool Requests and Receive Results via asend()
                try:
                    tool_results: Optional[List[ToolResultDict]] = yield {"type": "tool_requests", "calls": requests_to_yield}
                except GeneratorExit:
                     print(f"OpenAIProvider: Generator closed externally.")
                     raise # Re-raise to ensure cleanup happens correctly upstream

                # Process Tool Results
                if tool_results is None:
                    print(f"OpenAIProvider: Did not receive tool results back. Aborting tool loop.")
                    yield {"type": "error", "content": "[OpenAIProvider Error: Failed to get tool results]"}
                    break

                print(f"OpenAIProvider: Received {len(tool_results)} tool result(s).")

                # Append results to history for the next LLM iteration
                results_appended = 0
                for result in tool_results:
                    if "call_id" in result and "content" in result:
                        current_messages.append({
                            "role": "tool",
                            "tool_call_id": result["call_id"],
                            "content": result["content"] # Content should be string result from tool execution
                        })
                        results_appended += 1
                    else:
                        print(f"OpenAIProvider: Received invalid tool result format: {result}")

                if results_appended == 0:
                    print(f"OpenAIProvider: No valid tool results appended to history. Aborting loop.")
                    yield {"type": "error", "content": "[OpenAIProvider Error: No valid tool results processed]"}
                    break

                # Loop continues for the next LLM call...

            # --- Error Handling for API Calls (Using updated openai v1.x exception names) ---
            except openai.AuthenticationError as e: # UPDATED
                error_msg = f"OpenAI Authentication Error: Check API key (Status: {e.status_code}, Message: {e.body.get('message', 'N/A') if e.body else 'N/A'})" # Adjusted message access
                print(f"OpenAIProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenAIProvider Error: {error_msg}]"}
                break # Stop on auth errors
            except openai.RateLimitError as e: # UPDATED
                error_msg = f"OpenAI Rate Limit Error: (Status: {e.status_code}, Message: {e.body.get('message', 'N/A') if e.body else 'N/A'})" # Adjusted message access
                print(f"OpenAIProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenAIProvider Error: {error_msg}]"}
                # Could implement backoff/retry here, but for now, just break
                break
            except openai.APIConnectionError as e: # Stays the same
                error_msg = f"OpenAI Connection Error: {e}"
                print(f"OpenAIProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenAIProvider Error: {error_msg}]"}
                break # Stop on connection errors
            except openai.BadRequestError as e: # Stays the same
                 error_msg = f"OpenAI Bad Request Error: Status={e.status_code}, Response={e.response}, Message={e.body.get('message', 'N/A') if e.body else 'N/A'}" # Adjusted message access
                 print(f"OpenAIProvider: {error_msg}")
                 yield {"type": "error", "content": f"[OpenAIProvider Error: Bad Request (400) - {e.body.get('message', 'N/A') if e.body else 'N/A'}]"}
                 break
            except openai.APIStatusError as e: # Catch other API errors (e.g., 4xx, 5xx) - Stays the same
                 error_msg = f"OpenAI API Status Error: Status={e.status_code}, Response={e.response}, Message={e.body.get('message', 'N/A') if e.body else 'N/A'}" # Adjusted message access
                 print(f"OpenAIProvider: {error_msg}")
                 yield {"type": "error", "content": f"[OpenAIProvider Error: API Status {e.status_code} - {e.body.get('message', 'Server error occurred') if e.body else 'Server error occurred'}]"} # Provide default for 500
                 break
            except openai.APITimeoutError as e: # Add timeout handling
                 error_msg = f"OpenAI Request Timeout Error: {e}"
                 print(f"OpenAIProvider: {error_msg}")
                 yield {"type": "error", "content": f"[OpenAIProvider Error: Request timed out]"}
                 break
            except Exception as e: # General catch-all remains
                import traceback
                traceback.print_exc() # Print full traceback for unexpected errors
                error_msg = f"Unexpected Error during OpenAI completion: {type(e).__name__} - {e}"
                print(f"OpenAIProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenAIProvider Error: {error_msg}]"}
                break # Stop on unexpected errors

        # End of while loop
        if tool_call_attempts >= MAX_TOOL_CALLS_PER_TURN:
            print(f"OpenAIProvider: Reached maximum tool call attempts ({MAX_TOOL_CALLS_PER_TURN}).")
            yield {"type": "error", "content": f"[OpenAIProvider Error: Reached maximum tool call limit ({MAX_TOOL_CALLS_PER_TURN})]"}

        print(f"OpenAIProvider: stream_completion finished for model {model}.")

    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        # Avoid printing sensitive info like API key
        return f"<{self.__class__.__name__}(client_initialized={bool(self._openai_client)})>"
