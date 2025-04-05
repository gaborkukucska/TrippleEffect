# START OF FILE src/llm_providers/openrouter_provider.py
import openai
import json
import asyncio
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

# Import settings to potentially get Referer URL if needed
from src.config.settings import settings # Assuming settings might hold some global config if needed later

# OpenRouter Constants
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Recommended headers - Referer can be your project URL or name
# Ensure OPENROUTER_REFERER is set in .env
DEFAULT_REFERER = settings.DEFAULT_PERSONA # Fallback if OPENROUTER_REFERER is not set, consider a better default maybe? Or make mandatory?
# Use env var if set, otherwise fallback
OPENROUTER_REFERER = settings.OPENROUTER_REFERER if hasattr(settings, 'OPENROUTER_REFERER') else DEFAULT_REFERER

# Safety limit for tool call loops (same as OpenAI for now)
MAX_TOOL_CALLS_PER_TURN_OR = 5


class OpenRouterProvider(BaseLLMProvider):
    """
    LLM Provider implementation for OpenRouter API.
    Uses the openai library configured for OpenRouter's endpoint and authentication.
    Supports tool calling via the OpenAI-compatible API.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """
        Initializes the OpenRouter provider using the openai library.

        Args:
            api_key (Optional[str]): The OpenRouter API key. Required.
            base_url (Optional[str]): The OpenRouter base URL. Defaults to OpenRouter's standard v1 API.
            **kwargs: Additional arguments, including 'referer' for the HTTP header.
        """
        if not api_key:
            raise ValueError("OpenRouter API key is required for OpenRouterProvider.")

        self.base_url = (base_url or DEFAULT_OPENROUTER_BASE_URL).rstrip('/')
        self.api_key = api_key

        # Get Referer from kwargs or default
        referer = kwargs.pop('referer', OPENROUTER_REFERER) # Allow override via config kwargs

        # Set up default headers for OpenRouter authentication and identification
        default_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": referer,
             # Optional: Identify your application
            "X-Title": "TrippleEffect",
        }
        print(f"OpenRouterProvider: Using Referer: {referer}")

        try:
            self._openai_client = openai.AsyncOpenAI(
                api_key=self.api_key, # Pass key here too, openai lib might use it internally
                base_url=self.base_url,
                default_headers=default_headers,
                **kwargs # Pass any other openai constructor args
            )
            print(f"OpenRouterProvider initialized. Base URL: {self.base_url}. Client: {self._openai_client}")
        except Exception as e:
            print(f"Error initializing OpenAI client for OpenRouter: {e}")
            raise ValueError(f"Failed to initialize OpenAI client for OpenRouter: {e}") from e

    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str, # Expects OpenRouter model string e.g., "mistralai/mistral-7b-instruct"
        temperature: float,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = "auto",
        **kwargs # Allow provider-specific parameters
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Streams the completion from OpenRouter using the OpenAI-compatible API, handling tool calls.

        Args:
            messages (List[MessageDict]): Conversation history.
            model (str): OpenRouter model identifier string.
            temperature (float): Sampling temperature.
            max_tokens (Optional[int]): Max tokens for completion.
            tools (Optional[List[ToolDict]]): Available tool schemas.
            tool_choice (Optional[str]): Tool usage strategy.
            **kwargs: Additional arguments for the OpenRouter API call (passed via openai library).

        Yields:
            Dict representing events: 'response_chunk', 'tool_requests', 'error', 'status'.

        Receives:
            Optional[List[ToolResultDict]]: Results of executed tool calls via `asend()`.
        """
        # This implementation mirrors OpenAIProvider closely due to API compatibility.
        current_messages = list(messages) # Work on a copy
        tool_call_attempts = 0

        print(f"OpenRouterProvider: Starting stream_completion with model {model}. History length: {len(current_messages)}. Tools: {bool(tools)}")

        while tool_call_attempts < MAX_TOOL_CALLS_PER_TURN_OR:
            try:
                # --- Prepare API Call Args ---
                api_params = {
                    "model": model, # Use the OpenRouter model string
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

                # --- Make the API Call (Streaming) via OpenAI client ---
                print(f"OpenRouterProvider: Making API call (attempt {tool_call_attempts + 1}). Model: {model}. Params: { {k: v for k, v in api_params.items() if k != 'messages'} }")
                response_stream = await self._openai_client.chat.completions.create(**api_params)

                # --- Process the Stream (Identical logic to OpenAIProvider) ---
                assistant_response_content = ""
                accumulated_tool_calls = []
                current_tool_call_chunks = {}

                async for chunk in response_stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta: continue

                    if delta.content:
                        assistant_response_content += delta.content
                        yield {"type": "response_chunk", "content": delta.content}

                    if delta.tool_calls:
                        for tool_call_chunk in delta.tool_calls:
                            call_id = tool_call_chunk.id
                            if call_id:
                                if call_id not in current_tool_call_chunks:
                                     current_tool_call_chunks[call_id] = {"id": call_id, "name": "", "arguments": ""}
                                     if tool_call_chunk.function:
                                         if tool_call_chunk.function.name:
                                             current_tool_call_chunks[call_id]["name"] = tool_call_chunk.function.name
                                             print(f"OpenRouterProvider: Started receiving tool call '{tool_call_chunk.function.name}' (ID: {call_id})")
                                         if tool_call_chunk.function.arguments:
                                              current_tool_call_chunks[call_id]["arguments"] += tool_call_chunk.function.arguments
                                else:
                                    if tool_call_chunk.function and tool_call_chunk.function.arguments:
                                        current_tool_call_chunks[call_id]["arguments"] += tool_call_chunk.function.arguments
                            else:
                                print(f"OpenRouterProvider: Warning - received tool call chunk without ID: {tool_call_chunk}")


                # --- Stream Finished - Assemble Completed Tool Calls ---
                requests_to_yield = []
                history_tool_calls = []

                for call_id, call_info in current_tool_call_chunks.items():
                    if call_info["name"] and call_info["arguments"] is not None:
                        try:
                            parsed_args = json.loads(call_info["arguments"])
                            requests_to_yield.append({
                                "id": call_id,
                                "name": call_info["name"],
                                "arguments": parsed_args
                            })
                            history_tool_calls.append({
                                "id": call_id, "type": "function",
                                "function": {"name": call_info["name"], "arguments": call_info["arguments"]}
                            })
                            print(f"OpenRouterProvider: Completed tool call request: ID={call_id}, Name={call_info['name']}, Args={call_info['arguments']}")
                        except json.JSONDecodeError as e:
                            print(f"OpenRouterProvider: Failed to decode JSON arguments for tool call {call_id}: {e}. Args received: '{call_info['arguments']}'")
                            yield {"type": "error", "content": f"[OpenRouterProvider Error: Failed to parse arguments for tool {call_info['name']} (ID: {call_id})]. Arguments: '{call_info['arguments']}'"}
                            return # Stop generator

                # --- Post-Stream Processing ---
                assistant_message: MessageDict = {"role": "assistant"}
                if assistant_response_content:
                    assistant_message["content"] = assistant_response_content
                if history_tool_calls:
                     assistant_message["tool_calls"] = history_tool_calls
                if assistant_message.get("content") or assistant_message.get("tool_calls"):
                    current_messages.append(assistant_message)

                # --- Check if Tool Calls Were Made ---
                if not requests_to_yield:
                    print(f"OpenRouterProvider: Finished turn with final response. Content length: {len(assistant_response_content)}")
                    break # Exit the while loop

                # --- Tools were called ---
                tool_call_attempts += 1
                print(f"OpenRouterProvider: Requesting execution for {len(requests_to_yield)} tool call(s). Attempt {tool_call_attempts}/{MAX_TOOL_CALLS_PER_TURN_OR}.")

                # Yield Tool Requests and Receive Results
                try:
                    tool_results: Optional[List[ToolResultDict]] = yield {"type": "tool_requests", "calls": requests_to_yield}
                except GeneratorExit:
                     print(f"OpenRouterProvider: Generator closed externally.")
                     raise

                if tool_results is None:
                    print(f"OpenRouterProvider: Did not receive tool results back. Aborting tool loop.")
                    yield {"type": "error", "content": "[OpenRouterProvider Error: Failed to get tool results]"}
                    break

                print(f"OpenRouterProvider: Received {len(tool_results)} tool result(s).")

                # Append results to history
                results_appended = 0
                for result in tool_results:
                    if "call_id" in result and "content" in result:
                        current_messages.append({
                            "role": "tool", "tool_call_id": result["call_id"], "content": result["content"]
                        })
                        results_appended += 1
                    else:
                        print(f"OpenRouterProvider: Received invalid tool result format: {result}")

                if results_appended == 0:
                    print(f"OpenRouterProvider: No valid tool results appended to history. Aborting loop.")
                    yield {"type": "error", "content": "[OpenRouterProvider Error: No valid tool results processed]"}
                    break

                # Loop continues...

            # --- Error Handling (Using updated openai v1.x exception names) ---
            except openai.AuthenticationError as e: # UPDATED
                error_msg = f"OpenRouter Authentication Error: Check API key. (Status: {e.status_code}, Message: {e.body.get('message', 'N/A') if e.body else 'N/A'})" # Adjusted message access
                print(f"OpenRouterProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenRouterProvider Error: {error_msg}]"}
                break
            except openai.RateLimitError as e: # UPDATED
                error_msg = f"OpenRouter Rate Limit Error: (Status: {e.status_code}, Message: {e.body.get('message', 'N/A') if e.body else 'N/A'})" # Adjusted message access
                print(f"OpenRouterProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenRouterProvider Error: {error_msg}]"}
                break
            except openai.APIConnectionError as e: # Stays the same
                error_msg = f"OpenRouter Connection Error: {e}"
                print(f"OpenRouterProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenRouterProvider Error: {error_msg}]"}
                break
            except openai.BadRequestError as e: # Catch 400 errors (e.g., invalid model, bad input)
                 error_msg = f"OpenRouter Bad Request Error: Status={e.status_code}, Response={e.response}, Message={e.body.get('message', 'N/A') if e.body else 'N/A'}" # Adjusted message access
                 print(f"OpenRouterProvider: {error_msg}")
                 # Check if it's a model not found error specifically
                 err_body_msg = str(e.body.get('message', '') if e.body else '').lower()
                 if "model_not_found" in err_body_msg or "context_length" in err_body_msg:
                     yield {"type": "error", "content": f"[OpenRouterProvider Error: Model '{model}' not found or context length exceeded.]"}
                 else:
                     yield {"type": "error", "content": f"[OpenRouterProvider Error: Bad Request (400) - {e.body.get('message', 'N/A') if e.body else 'N/A'}]"}
                 break
            except openai.APIStatusError as e: # Catch other non-2xx errors (like the 500 Internal Server Error)
                 error_msg = f"OpenRouter API Status Error: Status={e.status_code}, Response={e.response}, Message={e.body.get('message', 'N/A') if e.body else 'N/A'}" # Adjusted message access
                 print(f"OpenRouterProvider: {error_msg}")
                 yield {"type": "error", "content": f"[OpenRouterProvider Error: API Status {e.status_code} - {e.body.get('message', 'Server error occurred') if e.body else 'Server error occurred'}]"} # Provide default for 500
                 break
            except openai.APITimeoutError as e: # Add timeout handling
                 error_msg = f"OpenRouter Request Timeout Error: {e}"
                 print(f"OpenRouterProvider: {error_msg}")
                 yield {"type": "error", "content": f"[OpenRouterProvider Error: Request timed out]"}
                 break
            except Exception as e: # General catch-all remains
                import traceback
                traceback.print_exc()
                error_msg = f"Unexpected Error during OpenRouter completion: {type(e).__name__} - {e}"
                print(f"OpenRouterProvider: {error_msg}")
                yield {"type": "error", "content": f"[OpenRouterProvider Error: {error_msg}]"}
                break

        # End of while loop
        if tool_call_attempts >= MAX_TOOL_CALLS_PER_TURN_OR:
            print(f"OpenRouterProvider: Reached maximum tool call attempts ({MAX_TOOL_CALLS_PER_TURN_OR}).")
            yield {"type": "error", "content": f"[OpenRouterProvider Error: Reached maximum tool call limit ({MAX_TOOL_CALLS_PER_TURN_OR})]"}

        print(f"OpenRouterProvider: stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client_initialized={bool(self._openai_client)})>"

    # Note: No explicit session management needed like aiohttp, openai client handles it.
