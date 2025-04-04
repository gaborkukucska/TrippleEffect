# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp
import json
import asyncio
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

# Default Ollama API endpoint
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
# Safety limit for tool call loops
MAX_TOOL_CALLS_PER_TURN_OLLAMA = 5


class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models.
    Uses aiohttp to stream completions from the Ollama API.
    Attempts to support tool calling based on Ollama's OpenAI-compatible format.

    NOTE: Tool calling success is highly dependent on the specific Ollama model used.
          Ensure the selected model is fine-tuned for function/tool calling.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """
        Initializes the Ollama provider.

        Args:
            api_key (Optional[str]): Not used by Ollama, included for interface consistency.
            base_url (Optional[str]): The base URL for the Ollama API endpoint.
                                      Defaults to http://localhost:11434.
            **kwargs: Session related arguments (e.g. timeout). Can be passed to aiohttp.ClientSession.
        """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key:
            print("OllamaProvider Warning: API key provided but not used by standard Ollama.")

        self._session_kwargs = kwargs
        self._session: Optional[aiohttp.ClientSession] = None
        print(f"OllamaProvider initialized. Base URL: {self.base_url}. Tool support enabled (model dependent).")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Creates or returns an existing aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            timeout_seconds = self._session_kwargs.get('timeout', 300)
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            self._session_kwargs['timeout'] = timeout
            self._session = aiohttp.ClientSession(**self._session_kwargs)
            print(f"OllamaProvider: Created new aiohttp session with timeout {timeout_seconds}s.")
        return self._session

    async def close_session(self):
        """Closes the aiohttp session if it exists."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            print("OllamaProvider: Closed aiohttp session.")

    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None, # Ollama uses 'num_predict'
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = "auto", # Ollama might support "auto", "none" - specific tool choice less likely
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Streams the completion from Ollama /api/chat, handling tool calls if detected.

        Args:
            messages (List[MessageDict]): Conversation history.
            model (str): Ollama model name.
            temperature (float): Sampling temperature.
            max_tokens (Optional[int]): Maps to Ollama's 'num_predict'.
            tools (Optional[List[ToolDict]]): Tool schemas to provide to the model.
            tool_choice (Optional[str]): How the model should use tools (passed to Ollama if supported).
            **kwargs: Additional parameters for Ollama API (e.g., top_p, top_k, stop).

        Yields:
            Dict representing events: 'response_chunk', 'tool_requests', 'error', 'status'.

        Receives:
            Optional[List[ToolResultDict]]: Results of executed tool calls via `asend()`.
        """
        session = await self._get_session()
        chat_endpoint = f"{self.base_url}/api/chat"
        current_messages = list(messages) # Work on a copy
        tool_call_attempts = 0

        print(f"OllamaProvider: Starting stream_completion for model {model}. History: {len(current_messages)}. Tools provided: {bool(tools)}")

        while tool_call_attempts < MAX_TOOL_CALLS_PER_TURN_OLLAMA:
            # Prepare Ollama payload for this turn
            payload = {
                "model": model,
                "messages": current_messages,
                "stream": True,
                "options": {
                    "temperature": temperature,
                    **kwargs # Pass through other options
                }
            }
            if max_tokens is not None:
                payload["options"]["num_predict"] = max_tokens
            # Include tools if provided - Ollama expects a 'tools' key at the top level
            if tools:
                 payload["tools"] = tools
                 # Pass tool_choice if Ollama supports it, otherwise it might be ignored
                 # Simple "auto" or "none" might work. Specific function choice less likely standard.
                 # Let's pass it cautiously.
                 # payload["tool_choice"] = tool_choice # Revisit if Ollama standardizes this

            # Remove None values from options
            payload["options"] = {k: v for k, v in payload["options"].items() if v is not None}

            print(f"OllamaProvider: Sending request (attempt {tool_call_attempts + 1}). Model: {model}. Options: {payload.get('options')}. Tools included: {bool(payload.get('tools'))}")
            yield {"type": "status", "content": f"Contacting Ollama model '{model}' (Attempt {tool_call_attempts + 1})..."}

            accumulated_content = ""
            detected_tool_calls = None # Store tool calls detected in the *final* message object of a turn
            stream_error = False
            is_done = False

            try:
                async with session.post(chat_endpoint, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        error_msg = f"Ollama API Error ({response.status}): {error_text}"
                        print(f"OllamaProvider: {error_msg}")
                        yield {"type": "error", "content": f"[OllamaProvider Error: {error_msg}]"}
                        return # Stop generation

                    # Process the streaming response
                    async for line in response.content:
                        if line:
                            try:
                                chunk_data = json.loads(line.decode('utf-8'))

                                if chunk_data.get("error"):
                                    error_msg = chunk_data["error"]
                                    print(f"OllamaProvider: Received error in stream: {error_msg}")
                                    yield {"type": "error", "content": f"[OllamaProvider Error: {error_msg}]"}
                                    stream_error = True
                                    break # Exit inner loop on stream error

                                # Extract content chunk
                                message_chunk = chunk_data.get("message")
                                if message_chunk and isinstance(message_chunk, dict):
                                    content_chunk = message_chunk.get("content")
                                    if content_chunk:
                                        accumulated_content += content_chunk
                                        yield {"type": "response_chunk", "content": content_chunk}

                                    # --- Check for Tool Calls ---
                                    # Ollama often puts the tool_calls in the message object
                                    # when the turn that *generates* the call is finishing.
                                    # It might not stream them piece by piece like OpenAI delta.
                                    if "tool_calls" in message_chunk and message_chunk["tool_calls"]:
                                         # Store the detected calls. If the stream sends multiple updates,
                                         # the last one before 'done' is likely the definitive one.
                                         detected_tool_calls = message_chunk["tool_calls"]
                                         print(f"OllamaProvider: Detected tool_calls in message chunk: {detected_tool_calls}")

                                # Check if stream turn is done
                                is_done = chunk_data.get("done", False)
                                if is_done:
                                    if not stream_error: # Only print normal finish if no error occurred
                                        print(f"OllamaProvider: Received done=true from stream for model {model}.")
                                        total_duration = chunk_data.get("total_duration")
                                        if total_duration:
                                             yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                                    break # Exit inner loop (async for line) when done

                            except json.JSONDecodeError:
                                print(f"OllamaProvider Warning: Failed to decode JSON line: {line}")
                            except Exception as e:
                                print(f"OllamaProvider Error processing stream line: {e}")
                                yield {"type": "error", "content": f"[OllamaProvider Error processing stream: {e}]"}
                                stream_error = True
                                break # Exit inner loop on processing error

                    # --- After finishing reading the stream for this turn ---
                    if stream_error:
                        break # Exit the outer while loop if an error occurred during streaming

                    # Append the assistant's message (content and potentially detected tool calls)
                    assistant_message: MessageDict = {"role": "assistant"}
                    if accumulated_content:
                        assistant_message["content"] = accumulated_content
                    if detected_tool_calls:
                        # IMPORTANT: Ollama provides parsed arguments directly in 'arguments' dict
                        assistant_message["tool_calls"] = detected_tool_calls
                    # Only add if it has content or tool calls
                    if assistant_message.get("content") is not None or assistant_message.get("tool_calls"): # Allow empty string content
                         current_messages.append(assistant_message)


                    # --- Process Detected Tool Calls (if any) ---
                    if detected_tool_calls:
                        tool_call_attempts += 1
                        print(f"OllamaProvider: Processing {len(detected_tool_calls)} detected tool call(s). Attempt {tool_call_attempts}/{MAX_TOOL_CALLS_PER_TURN_OLLAMA}.")

                        requests_to_yield = []
                        parsing_failed = False
                        for call in detected_tool_calls:
                             call_id = call.get("id") # Ollama might not provide IDs, generate if missing? Let's assume present for now.
                             func = call.get("function")
                             if not call_id or not func or not isinstance(func, dict):
                                 print(f"OllamaProvider Error: Invalid tool call format received: {call}")
                                 yield {"type": "error", "content": f"[OllamaProvider Error: Invalid tool call format received from model.]"}
                                 parsing_failed = True
                                 break
                             tool_name = func.get("name")
                             # Ollama likely provides arguments as a dict directly
                             tool_args = func.get("arguments")
                             if not tool_name or tool_args is None: # Allow empty dict args
                                 print(f"OllamaProvider Error: Missing name or arguments in tool call: {call}")
                                 yield {"type": "error", "content": f"[OllamaProvider Error: Missing name/args in tool call.]"}
                                 parsing_failed = True
                                 break

                             # Arguments should already be a dict from Ollama, no JSON parsing needed here generally
                             if not isinstance(tool_args, dict):
                                  print(f"OllamaProvider Error: Expected tool arguments to be a dict, got {type(tool_args)}. Call: {call}")
                                  yield {"type": "error", "content": f"[OllamaProvider Error: Invalid tool argument format.]"}
                                  parsing_failed = True
                                  break

                             requests_to_yield.append({
                                 "id": call_id,
                                 "name": tool_name,
                                 "arguments": tool_args # Pass the dict directly
                             })

                        if parsing_failed:
                            break # Exit the while loop

                        if not requests_to_yield:
                             print("OllamaProvider Warning: Tool calls detected but none were valid to process.")
                             # Should we break or continue? Let's break as it implies an unrecoverable model/API issue.
                             yield {"type": "error", "content": f"[OllamaProvider Error: Detected tool calls but failed to format requests.]"}
                             break

                        # Yield Tool Requests and Receive Results via asend()
                        try:
                             print(f"OllamaProvider: Yielding tool requests: {requests_to_yield}")
                             tool_results: Optional[List[ToolResultDict]] = yield {"type": "tool_requests", "calls": requests_to_yield}
                        except GeneratorExit:
                             print(f"OllamaProvider: Generator closed externally.")
                             raise

                        if tool_results is None:
                            print(f"OllamaProvider: Did not receive tool results back. Aborting tool loop.")
                            yield {"type": "error", "content": "[OllamaProvider Error: Failed to get tool results]"}
                            break

                        print(f"OllamaProvider: Received {len(tool_results)} tool result(s).")

                        # Append results to history for the next LLM iteration
                        results_appended = 0
                        for result in tool_results:
                             # IMPORTANT: Ollama expects 'role: tool' messages, just like OpenAI
                             if "call_id" in result and "content" in result:
                                 current_messages.append({
                                     "role": "tool",
                                     "tool_call_id": result["call_id"],
                                     "content": result["content"]
                                 })
                                 results_appended += 1
                             else:
                                 print(f"OllamaProvider: Received invalid tool result format: {result}")

                        if results_appended == 0:
                            print(f"OllamaProvider: No valid tool results appended to history. Aborting loop.")
                            yield {"type": "error", "content": "[OllamaProvider Error: No valid tool results processed]"}
                            break

                        # Reset for next iteration of the while loop
                        detected_tool_calls = None
                        accumulated_content = ""
                        is_done = False
                        # Continue to the next iteration of the while loop to send results back
                        continue

                    # --- No Tool Calls Detected ---
                    elif is_done:
                        # Stream finished normally without tool calls in the last message
                        print(f"OllamaProvider: Finished turn with final response (no tool calls detected).")
                        break # Exit the while loop


            # --- Handle Errors During HTTP Request ---
            except aiohttp.ClientConnectorError as e:
                error_msg = f"Ollama Connection Error: Failed to connect to {self.base_url}. Ensure Ollama is running. Details: {e}"
                print(f"OllamaProvider: {error_msg}")
                yield {"type": "error", "content": f"[OllamaProvider Error: {error_msg}]"}
                break # Exit while loop
            except asyncio.TimeoutError:
                error_msg = f"Ollama Request Timeout: No response from {self.base_url} within the configured timeout."
                print(f"OllamaProvider: {error_msg}")
                yield {"type": "error", "content": f"[OllamaProvider Error: {error_msg}]"}
                break # Exit while loop
            except Exception as e:
                import traceback
                traceback.print_exc()
                error_msg = f"Unexpected Error during Ollama completion: {type(e).__name__} - {e}"
                print(f"OllamaProvider: {error_msg}")
                yield {"type": "error", "content": f"[OllamaProvider Error: {error_msg}]"}
                break # Exit while loop

        # End of while loop
        if tool_call_attempts >= MAX_TOOL_CALLS_PER_TURN_OLLAMA:
            print(f"OllamaProvider: Reached maximum tool call attempts ({MAX_TOOL_CALLS_PER_TURN_OLLAMA}).")
            yield {"type": "error", "content": f"[OllamaProvider Error: Reached maximum tool call limit ({MAX_TOOL_CALLS_PER_TURN_OLLAMA})]"}

        print(f"OllamaProvider: stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        session_status = "closed" if self._session is None or self._session.closed else "open"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', session='{session_status}')>"

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
