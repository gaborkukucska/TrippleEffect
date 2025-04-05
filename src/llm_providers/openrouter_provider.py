# START OF FILE src/llm_providers/openrouter_provider.py
import openai
import json
import asyncio
import logging
import time # Import time for delay calculation if needed (asyncio.sleep is better)
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict
from src.config.settings import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
RETRYABLE_STATUS_CODES = [429] # OpenAI specific retryable codes (like rate limit) - Note: 5xx handled separately
RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError # Treat RateLimitError as potentially temporary
)


# OpenRouter Constants
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_REFERER = settings.DEFAULT_PERSONA # Fallback referer using default persona
# Use settings.OPENROUTER_REFERER if available, otherwise default
OPENROUTER_REFERER = settings.OPENROUTER_REFERER if hasattr(settings, 'OPENROUTER_REFERER') and settings.OPENROUTER_REFERER else DEFAULT_REFERER


# Safety limit for tool call loops within a single stream_completion call
MAX_TOOL_CALLS_PER_TURN_OR = 5


class OpenRouterProvider(BaseLLMProvider):
    """
    LLM Provider implementation for OpenRouter API with retry mechanism.
    Uses the openai library configured for OpenRouter.
    Handles streaming completions and the tool call loop.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the OpenRouter provider using the openai library. """
        if not api_key:
            raise ValueError("OpenRouter API key is required for OpenRouterProvider.")

        self.base_url = (base_url or DEFAULT_OPENROUTER_BASE_URL).rstrip('/')
        self.api_key = api_key

        # Use referer from kwargs if provided, otherwise use the one from settings
        referer = kwargs.pop('referer', OPENROUTER_REFERER)

        default_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": referer,
            "X-Title": "TrippleEffect", # Set your app name here
        }
        logger.info(f"OpenRouterProvider: Using Referer: {referer}")

        try:
            # Initialize the AsyncOpenAI client configured for OpenRouter
            self._openai_client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=self.base_url,
                default_headers=default_headers,
                max_retries=0, # Disable automatic retries in underlying client, we handle it manually
                **kwargs # Pass any other provider-specific args
            )
            logger.info(f"OpenRouterProvider initialized. Base URL: {self.base_url}. Client: {self._openai_client}")
        except Exception as e:
            logger.error(f"Error initializing OpenAI client for OpenRouter: {e}", exc_info=True)
            raise ValueError(f"Failed to initialize OpenAI client for OpenRouter: {e}") from e

    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = "auto",
        **kwargs # Allow provider-specific parameters
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Streams completion, handles the tool call loop, includes enhanced logging and retry logic.

        Yields:
            Dict[str, Any]: Events like 'response_chunk', 'tool_requests', 'error', 'status'.
        Receives:
            Optional[List[ToolResultDict]]: Results from executed tool calls sent via `asend()`.
        """
        current_messages = list(messages)
        tool_call_attempts = 0

        logger.info(f"Starting stream_completion with OpenRouter model {model}. History length: {len(current_messages)}. Tools provided: {bool(tools)}")

        # --- Main Loop for Handling Tool Calls ---
        while tool_call_attempts < MAX_TOOL_CALLS_PER_TURN_OR:
            response_stream = None
            last_exception = None
            api_params: Dict[str, Any] = {} # Define api_params early

            # --- Retry Loop for a single API call ---
            for attempt in range(MAX_RETRIES + 1):
                try:
                    # --- Prepare API Call Args for this turn ---
                    api_params = {
                        "model": model, "messages": current_messages, "temperature": temperature,
                        "stream": True, **kwargs
                    }
                    if max_tokens: api_params["max_tokens"] = max_tokens
                    # Include tools only if they are provided and not empty
                    if tools: api_params["tools"], api_params["tool_choice"] = tools, tool_choice

                    # --- Log Request ---
                    # (Consider using pprint for better readability if needed)
                    log_params = {k: v for k, v in api_params.items() if k != 'messages'}
                    logger.info(f"OpenRouterProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Params: {log_params}")
                    # logger.debug(f"Request messages: {current_messages}") # Can be very verbose

                    # --- Make the API Call (Streaming) ---
                    response_stream = await self._openai_client.chat.completions.create(**api_params)
                    logger.info(f"API call successful on attempt {attempt + 1}.")
                    last_exception = None # Reset last exception on success
                    break # Exit retry loop

                # --- Retryable Error Handling ---
                except RETRYABLE_EXCEPTIONS as e:
                    last_exception = e
                    logger.warning(f"Retryable OpenRouter error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                        logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue # Go to next attempt
                    else:
                        logger.error(f"Max retries ({MAX_RETRIES}) reached after retryable error.")
                        yield {"type": "error", "content": f"[OpenRouterProvider Error]: Max retries reached. Last error: {type(e).__name__}"}
                        return

                except openai.APIStatusError as e:
                    last_exception = e
                    logger.warning(f"OpenRouter API Status Error on attempt {attempt + 1}/{MAX_RETRIES + 1}: Status={e.status_code}, Body={e.body}")
                    # Retry on 5xx or specific retryable codes
                    if (e.status_code >= 500 or e.status_code in RETRYABLE_STATUS_CODES) and attempt < MAX_RETRIES:
                        logger.info(f"Status {e.status_code} is retryable. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue
                    else:
                        logger.error(f"Non-retryable API Status Error ({e.status_code}) or max retries reached.")
                        user_message = f"[OpenRouterProvider Error]: API Status {e.status_code}"
                        # Try extracting message from body if it's a dict
                        try:
                            body_dict = json.loads(e.body) if isinstance(e.body, str) else (e.body if isinstance(e.body, dict) else {})
                            if body_dict.get('message'):
                                user_message += f" - {body_dict.get('message')}"
                            elif body_dict.get('error', {}).get('message'):
                                user_message += f" - {body_dict.get('error', {}).get('message')}"
                        except: pass # Ignore parsing errors
                        yield {"type": "error", "content": user_message}
                        return

                # --- Non-Retryable Error Handling ---
                except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e:
                     # Log and yield specific error types
                     error_type_name = type(e).__name__
                     status_code = getattr(e, 'status_code', 'N/A')
                     error_body = getattr(e, 'body', 'N/A')
                     logger.error(f"Non-retryable OpenAI API error (via OpenRouter): {error_type_name} (Status: {status_code}), Body: {error_body}")
                     user_message = f"[OpenRouterProvider Error]: {error_type_name}"
                     try:
                         body_dict = json.loads(error_body) if isinstance(error_body, str) else (error_body if isinstance(error_body, dict) else {})
                         if body_dict.get('message'):
                              user_message += f" - {body_dict['message']}"
                         elif body_dict.get('error', {}).get('message'):
                             user_message += f" - {body_dict.get('error', {}).get('message')}"
                     except: pass
                     yield {"type": "error", "content": user_message}
                     return # Exit function

                except Exception as e: # General catch-all for unexpected errors during API call
                    last_exception = e
                    logger.exception(f"Unexpected Error during OpenRouter API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                         logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                         await asyncio.sleep(RETRY_DELAY_SECONDS)
                         continue
                    else:
                         logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error.")
                         yield {"type": "error", "content": f"[OpenRouterProvider Error]: Unexpected Error after retries - {type(e).__name__}"}
                         return

            # --- Check if API call failed after all retries ---
            if response_stream is None:
                 # This should only happen if all retries failed and the error yielding didn't exit
                 logger.error("OpenRouter API call failed after all retries, response_stream is None.")
                 err_msg = f"[OpenRouterProvider Error]: API call failed after {MAX_RETRIES} retries."
                 if last_exception: err_msg += f" Last error: {type(last_exception).__name__}"
                 yield {"type": "error", "content": err_msg}
                 return # Exit generator

            # --- Process the Successful Stream ---
            try:
                assistant_response_content = ""
                # Tool call accumulation state for this turn
                current_tool_call_chunks: Dict[str, Dict[str, Any]] = {} # {call_id: {id:.., name:.., args_str:""}}
                finish_reason = None

                async for chunk in response_stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    # Capture the latest finish_reason provided in the stream
                    # (OpenAI SDK might provide it in multiple chunks, the last one is the definitive one for the turn)
                    if chunk.choices and chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason

                    if not delta: continue

                    # --- Handle Text Content ---
                    if delta.content:
                        assistant_response_content += delta.content
                        yield {"type": "response_chunk", "content": delta.content}

                    # --- Handle Tool Call Deltas ---
                    if delta.tool_calls:
                        for tool_call_chunk in delta.tool_calls:
                            # tool_call_chunk has index, id?, function? {name?, arguments?}
                            if tool_call_chunk.index is not None: # Should always be present
                                call_id = tool_call_chunk.id # ID might arrive later
                                # Use a temporary index-based key if ID is not yet available
                                temp_key = f"index_{tool_call_chunk.index}"

                                # Initialize or retrieve the accumulating chunk
                                if call_id and temp_key in current_tool_call_chunks:
                                    # ID arrived, update key and transfer data
                                    current_tool_call_chunks[call_id] = current_tool_call_chunks.pop(temp_key)
                                    current_tool_call_chunks[call_id]["id"] = call_id
                                    # Use the actual call_id now
                                    current_key = call_id
                                elif call_id:
                                    current_key = call_id
                                else:
                                    current_key = temp_key

                                if current_key not in current_tool_call_chunks:
                                     current_tool_call_chunks[current_key] = {
                                         "id": call_id, # May be None initially
                                         "name": "",
                                         "arguments": "",
                                         "index": tool_call_chunk.index # Store index for potential ordering later if needed
                                     }

                                # Update ID if it arrives
                                if call_id and not current_tool_call_chunks[current_key]["id"]:
                                     current_tool_call_chunks[current_key]["id"] = call_id

                                # Accumulate name and arguments
                                chunk_func = tool_call_chunk.function
                                if chunk_func:
                                     if chunk_func.name:
                                         current_tool_call_chunks[current_key]["name"] += chunk_func.name
                                     if chunk_func.arguments:
                                          current_tool_call_chunks[current_key]["arguments"] += chunk_func.arguments

                            else:
                                logger.warning(f"Received tool call chunk without index: {tool_call_chunk}")

                # --- Stream Finished for this turn ---
                logger.debug(f"OpenRouter stream finished for this turn. Finish reason: {finish_reason}")

                # --- Assemble Completed Tool Calls ---
                requests_to_yield: List[Dict[str, Any]] = []
                history_tool_calls_for_next_turn: List[Dict[str, Any]] = []
                parsing_error_occurred = False

                # Sort accumulated chunks by index to maintain order, just in case
                sorted_tool_chunks = sorted(current_tool_call_chunks.values(), key=lambda x: x.get('index', 0))

                for call_info in sorted_tool_chunks:
                    call_id = call_info.get("id")
                    tool_name = call_info.get("name")
                    arguments_str = call_info.get("arguments")

                    # Ensure we have all necessary parts for a valid tool call
                    if call_id and tool_name and arguments_str is not None:
                        try:
                            # Arguments are received as a JSON string, parse them
                            parsed_args = json.loads(arguments_str)
                            requests_to_yield.append({
                                "id": call_id,
                                "name": tool_name,
                                "arguments": parsed_args
                            })
                            # Store the format needed for the *next* API call's history
                            history_tool_calls_for_next_turn.append({
                                "id": call_id,
                                "type": "function", # OpenAI uses 'function' type for tools
                                "function": {"name": tool_name, "arguments": arguments_str} # Keep args as string for history
                            })
                            logger.debug(f"Completed tool call request: ID={call_id}, Name={tool_name}, Args='{arguments_str[:50]}...'")
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode JSON arguments for tool call {call_id} ('{tool_name}'): {e}. Args received: '{arguments_str}'")
                            yield {"type": "error", "content": f"[OpenRouterProvider Error]: Failed to parse arguments for tool '{tool_name}' (ID: {call_id}). Invalid JSON received."}
                            parsing_error_occurred = True
                            break # Stop processing more tools if one fails
                    # else: # Log incomplete tool calls for debugging? Only if finish_reason was tool_calls?
                        # logger.warning(f"Incomplete tool call data accumulated: {call_info}")


                if parsing_error_occurred:
                     break # Exit the outer while loop

                # --- Append Assistant Message to History (for next turn) ---
                assistant_message: MessageDict = {"role": "assistant"}
                # Add content only if it exists (might be tool_calls only)
                if assistant_response_content:
                     assistant_message["content"] = assistant_response_content
                # Add tool_calls only if they were successfully parsed and formatted
                if history_tool_calls_for_next_turn:
                     assistant_message["tool_calls"] = history_tool_calls_for_next_turn

                # Add the message to history only if it has content or tool calls
                if assistant_message.get("content") or assistant_message.get("tool_calls"):
                    current_messages.append(assistant_message)
                    # logger.debug(f"Appended assistant message to history for next turn: {assistant_message}")


                # --- Handle Tool Call Flow ---
                # Check if the *reason* the stream finished was because the model wants to call tools
                # AND if we actually successfully parsed any tool calls
                if finish_reason == "tool_calls" and requests_to_yield:
                    tool_call_attempts += 1
                    logger.info(f"Requesting execution for {len(requests_to_yield)} tool call(s). Attempt {tool_call_attempts}/{MAX_TOOL_CALLS_PER_TURN_OR}.")

                    try:
                         # Yield requests and wait for results from the AgentManager via Agent
                         tool_results: Optional[List[ToolResultDict]] = yield {"type": "tool_requests", "calls": requests_to_yield}

                         if tool_results is None:
                             logger.warning("Did not receive tool results back from manager. Aborting tool loop.")
                             yield {"type": "error", "content": "[OpenRouterProvider Error]: Failed to get tool results"}
                             break # Exit the while loop

                         logger.info(f"Received {len(tool_results)} tool result(s) from manager.")

                         # Format results into 'tool' role messages and append to history for the next API call
                         results_appended_count = 0
                         for result in tool_results:
                              if "call_id" in result and "content" in result:
                                  # Manager must ensure content is a string
                                  current_messages.append({
                                      "role": "tool",
                                      "tool_call_id": result["call_id"],
                                      "content": str(result["content"]) # Ensure content is string
                                  })
                                  results_appended_count += 1
                              else:
                                  logger.warning(f"Received invalid tool result format from manager: {result}")

                         if results_appended_count == 0:
                              logger.warning("No valid tool results appended to history. Aborting loop.")
                              yield {"type": "error", "content": "[OpenRouterProvider Error]: No valid tool results processed"}
                              break # Exit the while loop

                         # Reset state for the next iteration of the while loop
                         current_tool_call_chunks = {}
                         assistant_response_content = ""
                         # Continue the while loop to make the next API call with tool results
                         continue

                    except GeneratorExit:
                         logger.warning("OpenRouterProvider: Generator closed externally while awaiting tool results.")
                         raise # Re-raise to ensure proper cleanup by the caller

                    except Exception as e_yield:
                         logger.exception(f"Error yielding tool requests or receiving results: {e_yield}")
                         yield {"type": "error", "content": f"[OpenRouterProvider Error]: Communication error during tool call - {type(e_yield).__name__}"}
                         break # Exit the while loop

                else:
                     # No tool calls requested by the model in this turn, or stream ended for other reasons (e.g., 'stop', 'length')
                     logger.info(f"Finishing OpenRouter turn. Finish reason: {finish_reason}. No tool calls requested or processed this turn.")
                     break # Exit the while loop

            # --- Handle potential errors *during* stream processing ---
            except Exception as stream_err:
                 logger.exception(f"Error processing OpenRouter response stream: {stream_err}")
                 yield {"type": "error", "content": f"[OpenRouterProvider Error]: Error processing stream - {type(stream_err).__name__}"}
                 break # Exit the while loop if stream processing fails

        # --- End of while tool_call_attempts... loop ---
        if tool_call_attempts >= MAX_TOOL_CALLS_PER_TURN_OR:
            logger.warning(f"Reached maximum tool call attempts ({MAX_TOOL_CALLS_PER_TURN_OR}) for this request.")
            yield {"type": "error", "content": f"[OpenRouterProvider Error]: Reached maximum tool call limit ({MAX_TOOL_CALLS_PER_TURN_OR})"}

        logger.info(f"OpenRouterProvider stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client_initialized={bool(self._openai_client)})>"
