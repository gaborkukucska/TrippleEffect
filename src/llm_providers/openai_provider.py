# START OF FILE src/llm_providers/openai_provider.py
import openai
import json
import asyncio
import logging # Import logging
import time # Import time
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)
# Basic config (ideally configure centrally)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Retry Configuration (Remains the same)
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
RETRYABLE_STATUS_CODES = [429]
RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError
)

# --- Constants ---
# MAX_TOOL_CALLS_PER_TURN is no longer relevant here

class OpenAIProvider(BaseLLMProvider):
    """
    LLM Provider implementation for OpenAI's API with retry mechanism.
    Streams text completions. Tool handling is done by the Agent Core via XML parsing.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """
        Initializes the OpenAI async client, disabling its internal retries.
        """
        try:
            self._openai_client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=0, # Disable automatic retries in client
                **kwargs
            )
            logger.info(f"OpenAIProvider initialized. Client: {self._openai_client}")
        except Exception as e:
            logger.error(f"Error initializing OpenAI client: {e}", exc_info=True)
            raise ValueError(f"Failed to initialize OpenAI client: {e}") from e

    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDict]] = None, # Keep in signature for base class compatibility
        tool_choice: Optional[str] = None,   # Keep in signature for base class compatibility
        **kwargs # Allow provider-specific parameters like 'user', etc.
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """ Streams text completion, includes retry logic. Ignores tools/tool_choice. """

        logger.info(f"Starting stream_completion with model {model}. History length: {len(messages)}.")
        # Log that tools/tool_choice are ignored if provided
        if tools or tool_choice:
            logger.warning(f"OpenAIProvider received tools/tool_choice arguments, but they will be ignored as tool handling is done via XML parsing by the Agent.")

        response_stream = None
        last_exception = None

        # --- Retry Loop ---
        for attempt in range(MAX_RETRIES + 1):
            try:
                # --- Prepare API Call Args (NO tools/tool_choice) ---
                api_params = {
                    "model": model, "messages": messages, "temperature": temperature,
                    "stream": True, **kwargs
                }
                if max_tokens: api_params["max_tokens"] = max_tokens
                # REMOVED: api_params["tools"], api_params["tool_choice"]

                # --- Log Request ---
                log_params = {k: v for k, v in api_params.items() if k != 'messages'}
                logger.info(f"OpenAIProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Params: {log_params}")
                # logger.debug(f"Request messages: {messages}") # Optional verbose logging

                # --- Make the OpenAI API Call (Streaming) ---
                response_stream = await self._openai_client.chat.completions.create(**api_params)
                logger.info(f"API call successful on attempt {attempt + 1}.")
                last_exception = None
                break # Exit retry loop

            # --- Retryable Error Handling (remains the same) ---
            except RETRYABLE_EXCEPTIONS as e:
                last_exception = e
                logger.warning(f"Retryable error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after retryable error.")
                    yield {"type": "error", "content": f"[OpenAIProvider Error: Max retries reached. Last error: {type(e).__name__}]"}
                    return

            except openai.APIStatusError as e:
                last_exception = e
                logger.warning(f"API Status Error on attempt {attempt + 1}/{MAX_RETRIES + 1}: Status={e.status_code}, Body={e.body}")
                if (e.status_code >= 500 or e.status_code in RETRYABLE_STATUS_CODES) and attempt < MAX_RETRIES:
                    logger.info(f"Status {e.status_code} is retryable. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                else:
                    logger.error(f"Non-retryable API Status Error ({e.status_code}) or max retries reached.")
                    user_message = f"[OpenAIProvider Error: API Status {e.status_code}]"
                    if isinstance(e.body, dict) and e.body.get('message'): user_message += f" - {e.body.get('message')}"
                    elif e.body: user_message += f" - {str(e.body)[:100]}"
                    yield {"type": "error", "content": user_message}
                    return

            # --- Non-Retryable Error Handling (remains the same) ---
            except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e:
                 error_type_name = type(e).__name__
                 status_code = getattr(e, 'status_code', 'N/A')
                 error_body = getattr(e, 'body', 'N/A')
                 logger.error(f"Non-retryable OpenAI API error: {error_type_name} (Status: {status_code}), Body: {error_body}")
                 user_message = f"[OpenAIProvider Error: {error_type_name}]"
                 if isinstance(error_body, dict) and error_body.get('message'): user_message += f" - {error_body['message']}"
                 elif error_body != 'N/A': user_message += f" - {str(error_body)[:100]}"
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
                     yield {"type": "error", "content": f"[OpenAIProvider Error: Unexpected Error after retries - {type(e).__name__}]"}
                     return

        # --- Check if API call failed after all retries ---
        if response_stream is None:
             logger.error("API call failed after all retries, response_stream is None.")
             yield {"type": "error", "content": f"[OpenAIProvider Error: API call failed after {MAX_RETRIES} retries. Last error: {type(last_exception).__name__ if last_exception else 'Unknown'}]"}
             return # Exit

        # --- Process the Stream (if API call was successful) ---
        try:
            async for chunk in response_stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta: continue

                # Yield text content
                if delta.content:
                    yield {"type": "response_chunk", "content": delta.content}

                # REMOVED: Logic handling delta.tool_calls

            # --- Stream Finished ---
            # No need to assemble tool calls or append history here
            logger.info(f"Finished processing stream for model {model}.")

        except Exception as stream_err:
             logger.exception(f"Error processing response stream: {stream_err}")
             yield {"type": "error", "content": f"[OpenAIProvider Error: Error processing stream - {type(stream_err).__name__}]"}

        # REMOVED: Tool call loop logic (while tool_call_attempts...)

        logger.info(f"stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        return f"<{self.__class__.__name__}(client_initialized={bool(self._openai_client)})>"
