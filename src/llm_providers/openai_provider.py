# START OF FILE src/llm_providers/openai_provider.py
import openai
import json
import asyncio
import logging # Import logging
import time # Import time
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') # Configured in main

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
    Includes enhanced handling for errors occurring during stream processing.
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

    # --- MODIFIED stream_completion ---
    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDict]] = None, # Ignored
        tool_choice: Optional[str] = None,   # Ignored
        **kwargs # Allow provider-specific parameters like 'user', etc.
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """ Streams text completion, includes retry logic for initial call and handling for stream errors. Ignores tools/tool_choice. """

        logger.info(f"Starting stream_completion with model {model}. History length: {len(messages)}.")
        if tools or tool_choice:
            logger.warning(f"OpenAIProvider received tools/tool_choice arguments, but they will be ignored.")

        response_stream = None
        last_exception = None

        # --- Retry Loop for Initial API Call ---
        for attempt in range(MAX_RETRIES + 1):
            try:
                api_params = { "model": model, "messages": messages, "temperature": temperature, "stream": True, **kwargs }
                if max_tokens: api_params["max_tokens"] = max_tokens

                log_params = {k: v for k, v in api_params.items() if k != 'messages'}
                logger.info(f"OpenAIProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Params: {log_params}")

                response_stream = await self._openai_client.chat.completions.create(**api_params)
                logger.info(f"API call successful on attempt {attempt + 1}.")
                last_exception = None
                break # Exit retry loop

            except RETRYABLE_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after retryable error.")
                    yield {"type": "error", "content": f"[OpenAIProvider Error]: Max retries reached. Last error: {type(e).__name__}"}
                    return
            except openai.APIStatusError as e:
                last_exception = e
                logger.warning(f"API Status Error on attempt {attempt + 1}/{MAX_RETRIES + 1}: Status={e.status_code}, Body={e.body}")
                if (e.status_code >= 500 or e.status_code in RETRYABLE_STATUS_CODES) and attempt < MAX_RETRIES:
                    logger.info(f"Status {e.status_code} is retryable. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                else:
                    # --- *** CORRECTED FORMATTING FOR THIS BLOCK *** ---
                    logger.error(f"Non-retryable API Status Error ({e.status_code}) or max retries reached.")
                    user_message = f"[OpenAIProvider Error]: API Status {e.status_code}"
                    try:
                        # Attempt to parse body for more details
                        body_dict = json.loads(e.body) if isinstance(e.body, str) else (e.body if isinstance(e.body, dict) else {})
                        error_detail = body_dict.get('error', {}).get('message') or body_dict.get('message')
                        if error_detail:
                            user_message += f" - {str(error_detail)[:100]}"
                    except Exception: # Ignore parsing errors
                        pass
                    yield {"type": "error", "content": user_message}
                    return
                    # --- *** END CORRECTION *** ---
            except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e:
                 error_type_name = type(e).__name__; status_code = getattr(e, 'status_code', 'N/A'); error_body = getattr(e, 'body', 'N/A'); logger.error(f"Non-retryable OpenAI API error: {error_type_name} (Status: {status_code}), Body: {error_body}"); user_message = f"[OpenAIProvider Error]: {error_type_name}"; try: body_dict = json.loads(error_body) if isinstance(error_body, str) else (error_body if isinstance(error_body, dict) else {}); error_detail = body_dict.get('error', {}).get('message') or body_dict.get('message'); if error_detail: user_message += f" - {str(error_detail)[:100]}"; except: pass; yield {"type": "error", "content": user_message}; return
            except Exception as e:
                last_exception = e; logger.exception(f"Unexpected Error during API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error.")
                    yield {"type": "error", "content": f"[OpenAIProvider Error]: Unexpected Error after retries - {type(e).__name__}"}
                    return

        # --- Check if API call failed after all retries ---
        if response_stream is None:
             logger.error("API call failed after all retries, response_stream is None.")
             err_msg = f"[OpenAIProvider Error]: API call failed after {MAX_RETRIES} retries."
             if last_exception: err_msg += f" Last error: {type(last_exception).__name__}"
             yield {"type": "error", "content": err_msg}; return

        # --- Process the Stream (with error handling for stream iteration) ---
        try:
            finish_reason = None
            # *** ADDED TRY/EXCEPT AROUND STREAM ITERATION ***
            try:
                async for chunk in response_stream:
                    raw_chunk_data_for_log = None
                    try:
                         # Process chunk content
                         delta = chunk.choices[0].delta if chunk.choices else None
                         if chunk.choices and chunk.choices[0].finish_reason:
                             finish_reason = chunk.choices[0].finish_reason

                         if not delta: continue
                         if delta.content:
                             yield {"type": "response_chunk", "content": delta.content}
                         # Tool call logic removed

                    except Exception as chunk_proc_err:
                         logger.error(f"Error processing chunk content: {chunk_proc_err}", exc_info=True)
                         try: raw_chunk_data_for_log = chunk.model_dump_json(); logger.error(f"Raw chunk causing processing error: {raw_chunk_data_for_log}")
                         except Exception: pass
                         yield {"type": "error", "content": f"[OpenAIProvider Error]: Error processing stream chunk - {type(chunk_proc_err).__name__}"}
                         return

            except openai.APIError as stream_api_err:
                logger.error(f"OpenAI APIError occurred during stream processing: {stream_api_err}", exc_info=True)
                try: logger.error(f"APIError details: Status={stream_api_err.status_code}, Body={stream_api_err.body}")
                except Exception: pass
                yield {"type": "error", "content": f"[OpenAIProvider Error]: APIError during stream - {stream_api_err}"}
                return
            # *** END ADDED TRY/EXCEPT ***

            logger.debug(f"OpenAI stream finished processing loop. Finish reason captured: {finish_reason}")

        except Exception as stream_err:
             logger.exception(f"Unexpected Error processing OpenAI response stream: {stream_err}")
             yield {"type": "error", "content": f"[OpenAIProvider Error]: Unexpected Error processing stream - {type(stream_err).__name__}"}

        logger.info(f"OpenAIProvider stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        return f"<{self.__class__.__name__}(client_initialized={bool(self._openai_client)})>"
