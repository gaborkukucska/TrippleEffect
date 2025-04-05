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
DEFAULT_REFERER = settings.DEFAULT_PERSONA
OPENROUTER_REFERER = settings.OPENROUTER_REFERER if hasattr(settings, 'OPENROUTER_REFERER') else DEFAULT_REFERER

MAX_TOOL_CALLS_PER_TURN_OR = 5


class OpenRouterProvider(BaseLLMProvider):
    """
    LLM Provider implementation for OpenRouter API with retry mechanism.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the OpenRouter provider using the openai library. """
        if not api_key:
            raise ValueError("OpenRouter API key is required for OpenRouterProvider.")

        self.base_url = (base_url or DEFAULT_OPENROUTER_BASE_URL).rstrip('/')
        self.api_key = api_key

        referer = kwargs.pop('referer', OPENROUTER_REFERER)

        default_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": referer,
            "X-Title": "TrippleEffect",
        }
        logger.info(f"OpenRouterProvider: Using Referer: {referer}")

        try:
            self._openai_client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=self.base_url,
                default_headers=default_headers,
                max_retries=0, # Disable automatic retries in underlying client, we handle it manually
                **kwargs
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
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """ Streams completion, handles tools, includes enhanced logging and retry logic. """
        current_messages = list(messages)
        tool_call_attempts = 0

        logger.info(f"Starting stream_completion with model {model}. History length: {len(current_messages)}. Tools: {bool(tools)}")

        while tool_call_attempts < MAX_TOOL_CALLS_PER_TURN_OR:
            response_stream = None
            last_exception = None

            # --- Retry Loop ---
            for attempt in range(MAX_RETRIES + 1):
                try:
                    # --- Prepare API Call Args ---
                    api_params = {
                        "model": model, "messages": current_messages, "temperature": temperature,
                        "stream": True, **kwargs
                    }
                    if max_tokens: api_params["max_tokens"] = max_tokens
                    if tools: api_params["tools"], api_params["tool_choice"] = tools, tool_choice

                    # --- Log Request ---
                    import pprint
                    log_params = {k: v for k, v in api_params.items() if k != 'messages'}
                    logger.info(f"OpenRouterProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Params: {log_params}")
                    try:
                        messages_str = pprint.pformat(current_messages)
                        max_log_len = 2000
                        if len(messages_str) > max_log_len:
                            messages_str = messages_str[:max_log_len] + f"... (truncated {len(messages_str) - max_log_len} chars)"
                        logger.debug(f"Request messages:\n{messages_str}")
                    except Exception as log_e: logger.warning(f"Could not format messages for logging: {log_e}")

                    # --- Make the API Call ---
                    response_stream = await self._openai_client.chat.completions.create(**api_params)
                    # If call succeeds, break the retry loop and proceed to process the stream
                    logger.info(f"API call successful on attempt {attempt + 1}.")
                    last_exception = None # Reset last exception on success
                    break # Exit retry loop

                # --- Retryable Error Handling ---
                except RETRYABLE_EXCEPTIONS as e:
                    last_exception = e
                    logger.warning(f"Retryable error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                        logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue # Go to next attempt
                    else:
                        logger.error(f"Max retries ({MAX_RETRIES}) reached after retryable error.")
                        # Yield error and exit function if max retries exceeded
                        yield {"type": "error", "content": f"[OpenRouterProvider Error: Max retries reached. Last error: {type(e).__name__}]"}
                        return

                except openai.APIStatusError as e:
                    last_exception = e
                    logger.warning(f"API Status Error on attempt {attempt + 1}/{MAX_RETRIES + 1}: Status={e.status_code}, Body={e.body}")
                    # Retry on 5xx errors
                    if e.status_code >= 500 and attempt < MAX_RETRIES:
                        logger.info(f"Status {e.status_code} >= 500. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue # Go to next attempt
                    # Retry on specific status codes like 429 if needed (already covered by RateLimitError generally)
                    # elif e.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                    #    logger.info(f"Status {e.status_code} is retryable. Waiting {RETRY_DELAY_SECONDS}s...")
                    #    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    #    continue
                    else:
                        logger.error(f"Non-retryable API Status Error ({e.status_code}) or max retries reached.")
                        # Yield specific error info and exit function
                        user_message = f"[OpenRouterProvider Error: API Status {e.status_code}]"
                        if isinstance(e.body, dict) and e.body.get('message'): user_message += f" - {e.body.get('message')}"
                        elif e.body: user_message += f" - {str(e.body)[:100]}"
                        yield {"type": "error", "content": user_message}
                        return

                # --- Non-Retryable Error Handling ---
                except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e:
                     # Log these potentially more informative errors
                     error_type_name = type(e).__name__
                     status_code = getattr(e, 'status_code', 'N/A')
                     error_body = getattr(e, 'body', 'N/A')
                     logger.error(f"Non-retryable OpenAI API error: {error_type_name} (Status: {status_code}), Body: {error_body}")
                     user_message = f"[OpenRouterProvider Error: {error_type_name}]"
                     # Try to extract a useful message from the body if it's a dict
                     if isinstance(error_body, dict) and error_body.get('message'):
                          user_message += f" - {error_body['message']}"
                     elif error_body != 'N/A':
                          user_message += f" - {str(error_body)[:100]}"

                     yield {"type": "error", "content": user_message}
                     return # Exit function

                except Exception as e: # General catch-all
                    last_exception = e
                    logger.exception(f"Unexpected Error during API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                         logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                         await asyncio.sleep(RETRY_DELAY_SECONDS)
                         continue
                    else:
                         logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error.")
                         yield {"type": "error", "content": f"[OpenRouterProvider Error: Unexpected Error after retries - {type(e).__name__}]"}
                         return

            # --- Check if API call failed after all retries ---
            if response_stream is None:
                 # This should only happen if all retries failed and the error yielding didn't exit
                 logger.error("API call failed after all retries, response_stream is None.")
                 yield {"type": "error", "content": f"[OpenRouterProvider Error: API call failed after {MAX_RETRIES} retries. Last error: {type(last_exception).__name__ if last_exception else 'Unknown'}]"}
                 return # Exit


            # --- Process the Stream (if API call was successful) ---
            try:
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
                        # (Tool call chunk processing logic remains the same as before)
                        for tool_call_chunk in delta.tool_calls:
                            call_id = tool_call_chunk.id
                            if call_id:
                                if call_id not in current_tool_call_chunks:
                                     current_tool_call_chunks[call_id] = {"id": call_id, "name": "", "arguments": ""}
                                     if tool_call_chunk.function:
                                         if tool_call_chunk.function.name:
                                             current_tool_call_chunks[call_id]["name"] = tool_call_chunk.function.name
                                             logger.debug(f"Started receiving tool call '{tool_call_chunk.function.name}' (ID: {call_id})")
                                         if tool_call_chunk.function.arguments:
                                              current_tool_call_chunks[call_id]["arguments"] += tool_call_chunk.function.arguments
                                else:
                                    if tool_call_chunk.function and tool_call_chunk.function.arguments:
                                        current_tool_call_chunks[call_id]["arguments"] += tool_call_chunk.function.arguments
                            else:
                                logger.warning(f"Received tool call chunk without ID: {tool_call_chunk}")

                # --- Stream Finished - Assemble Completed Tool Calls ---
                # (Tool call assembly logic remains the same as before)
                requests_to_yield = []
                history_tool_calls = []
                parsing_error_occurred = False # Flag to track parsing errors

                for call_id, call_info in current_tool_call_chunks.items():
                    if call_info["name"] and call_info["arguments"] is not None:
                        try:
                            parsed_args = json.loads(call_info["arguments"])
                            requests_to_yield.append({
                                "id": call_id, "name": call_info["name"], "arguments": parsed_args
                            })
                            history_tool_calls.append({
                                "id": call_id, "type": "function",
                                "function": {"name": call_info["name"], "arguments": call_info["arguments"]}
                            })
                            logger.debug(f"Completed tool call request: ID={call_id}, Name={call_info['name']}, Args={call_info['arguments']}")
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode JSON arguments for tool call {call_id}: {e}. Args received: '{call_info['arguments']}'")
                            yield {"type": "error", "content": f"[OpenRouterProvider Error: Failed to parse arguments for tool {call_info['name']} (ID: {call_id})]. Arguments: '{call_info['arguments']}'"}
                            parsing_error_occurred = True
                            break # Stop processing further calls if one fails parsing
                # If parsing failed for any tool call, exit the outer while loop
                if parsing_error_occurred:
                     break


                # --- Post-Stream Processing ---
                # (History appending logic remains the same)
                assistant_message: MessageDict = {"role": "assistant"}
                if assistant_response_content: assistant_message["content"] = assistant_response_content
                if history_tool_calls: assistant_message["tool_calls"] = history_tool_calls
                if assistant_message.get("content") or assistant_message.get("tool_calls"):
                    current_messages.append(assistant_message)

                # --- Check if Tool Calls Were Made ---
                if not requests_to_yield:
                    logger.info(f"Finished turn with final response. Content length: {len(assistant_response_content)}")
                    break # Exit the while loop

                # --- Tools were called ---
                # (Yielding requests and receiving results logic remains the same)
                tool_call_attempts += 1
                logger.info(f"Requesting execution for {len(requests_to_yield)} tool call(s). Attempt {tool_call_attempts}/{MAX_TOOL_CALLS_PER_TURN_OR}.")

                try:
                    tool_results: Optional[List[ToolResultDict]] = yield {"type": "tool_requests", "calls": requests_to_yield}
                except GeneratorExit:
                     logger.warning(f"Generator closed externally.")
                     raise

                if tool_results is None:
                    logger.warning(f"Did not receive tool results back. Aborting tool loop.")
                    yield {"type": "error", "content": "[OpenRouterProvider Error: Failed to get tool results]"}
                    break

                logger.info(f"Received {len(tool_results)} tool result(s).")

                results_appended = 0
                for result in tool_results:
                    if "call_id" in result and "content" in result:
                        current_messages.append({
                            "role": "tool", "tool_call_id": result["call_id"], "content": result["content"]
                        })
                        results_appended += 1
                    else:
                        logger.warning(f"Received invalid tool result format: {result}")

                if results_appended == 0:
                    logger.warning(f"No valid tool results appended to history. Aborting loop.")
                    yield {"type": "error", "content": "[OpenRouterProvider Error: No valid tool results processed]"}
                    break

                # Loop continues for next tool call iteration...

            # --- Handle potential errors *during* stream processing ---
            # (These might be less likely to be retryable cleanly)
            except Exception as stream_err:
                 logger.exception(f"Error processing response stream: {stream_err}")
                 yield {"type": "error", "content": f"[OpenRouterProvider Error: Error processing stream - {type(stream_err).__name__}]"}
                 break # Exit the while loop if stream processing fails


        # End of while tool_call_attempts... loop
        if tool_call_attempts >= MAX_TOOL_CALLS_PER_TURN_OR:
            logger.warning(f"Reached maximum tool call attempts ({MAX_TOOL_CALLS_PER_TURN_OR}).")
            yield {"type": "error", "content": f"[OpenRouterProvider Error: Reached maximum tool call limit ({MAX_TOOL_CALLS_PER_TURN_OR})]"}

        logger.info(f"stream_completion finished for model {model}.")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client_initialized={bool(self._openai_client)})>"
