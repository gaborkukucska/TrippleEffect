# START OF FILE src/llm_providers/openrouter_provider.py
import openai
import json
import asyncio
import logging
import time # Import time for delay calculation if needed (asyncio.sleep is better)
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict
from src.config.settings import settings # Import settings directly

logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') # Configured in main

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
# Fallback referer using default persona if OPENROUTER_REFERER is not set
DEFAULT_REFERER = settings.DEFAULT_PERSONA
# Use settings.OPENROUTER_REFERER if available, otherwise default
OPENROUTER_REFERER = settings.OPENROUTER_REFERER if hasattr(settings, 'OPENROUTER_REFERER') and settings.OPENROUTER_REFERER else DEFAULT_REFERER


# Safety limit MAX_TOOL_CALLS_PER_TURN_OR no longer needed here


class OpenRouterProvider(BaseLLMProvider):
    """
    LLM Provider implementation for OpenRouter API with retry mechanism.
    Uses the openai library configured for OpenRouter.
    Streams raw text completions. Tool handling is done by the Agent Core via XML parsing.
    Includes enhanced handling for errors occurring during stream processing.
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
            # Updated log message
            logger.info(f"OpenRouterProvider initialized. Base URL: {self.base_url}. Client: {self._openai_client}. Tool support via XML parsing by Agent.")
        except Exception as e:
            logger.error(f"Error initializing OpenAI client for OpenRouter: {e}", exc_info=True)
            raise ValueError(f"Failed to initialize OpenAI client for OpenRouter: {e}") from e

    # --- MODIFIED stream_completion ---
    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDict]] = None, # Ignored
        tool_choice: Optional[str] = None,   # Ignored
        **kwargs # Allow provider-specific parameters
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Streams text completion, includes retry logic for initial call and handling for stream errors.
        Ignores tools/tool_choice.
        """
        logger.info(f"Starting stream_completion with OpenRouter model {model}. History length: {len(messages)}.")
        if tools or tool_choice:
            logger.warning(f"OpenRouterProvider received tools/tool_choice arguments, but they will be ignored.")

        response_stream = None
        last_exception = None
        api_params: Dict[str, Any] = {} # Define api_params early

        # --- Retry Loop for Initial API Call ---
        for attempt in range(MAX_RETRIES + 1):
            try:
                api_params = { "model": model, "messages": messages, "temperature": temperature, "stream": True, **kwargs }
                if max_tokens: api_params["max_tokens"] = max_tokens

                log_params = {k: v for k, v in api_params.items() if k != 'messages'}
                logger.info(f"OpenRouterProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Params: {log_params}")

                response_stream = await self._openai_client.chat.completions.create(**api_params)
                logger.info(f"API call successful on attempt {attempt + 1}.")
                last_exception = None # Reset last exception on success
                break # Exit retry loop

            # --- Retryable Error Handling ---
            except RETRYABLE_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable OpenRouter error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after retryable error."); yield {"type": "error", "content": f"[OpenRouterProvider Error]: Max retries reached. Last error: {type(e).__name__}"}; return
            except openai.APIStatusError as e:
                last_exception = e; logger.warning(f"OpenRouter API Status Error on attempt {attempt + 1}/{MAX_RETRIES + 1}: Status={e.status_code}, Body={e.body}")
                if (e.status_code >= 500 or e.status_code in RETRYABLE_STATUS_CODES) and attempt < MAX_RETRIES: await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Non-retryable API Status Error ({e.status_code}) or max retries reached."); user_message = f"[OpenRouterProvider Error]: API Status {e.status_code}"; try: body_dict = json.loads(e.body) if isinstance(e.body, str) else (e.body if isinstance(e.body, dict) else {}); error_detail = body_dict.get('error', {}).get('message') or body_dict.get('message'); if error_detail: user_message += f" - {str(error_detail)[:100]}"; except: pass; yield {"type": "error", "content": user_message}; return
            # --- Non-Retryable Error Handling ---
            except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e:
                 error_type_name = type(e).__name__; status_code = getattr(e, 'status_code', 'N/A'); error_body = getattr(e, 'body', 'N/A'); logger.error(f"Non-retryable OpenAI API error (via OpenRouter): {error_type_name} (Status: {status_code}), Body: {error_body}"); user_message = f"[OpenRouterProvider Error]: {error_type_name}"; try: body_dict = json.loads(error_body) if isinstance(error_body, str) else (error_body if isinstance(error_body, dict) else {}); error_detail = body_dict.get('error', {}).get('message') or body_dict.get('message'); if error_detail: user_message += f" - {str(error_detail)[:100]}"; except: pass; yield {"type": "error", "content": user_message}; return
            except Exception as e: # General catch-all for unexpected errors during API call
                last_exception = e; logger.exception(f"Unexpected Error during OpenRouter API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error."); yield {"type": "error", "content": f"[OpenRouterProvider Error]: Unexpected Error after retries - {type(e).__name__}"}; return

        # --- Check if API call failed after all retries ---
        if response_stream is None:
             logger.error("OpenRouter API call failed after all retries, response_stream is None.")
             err_msg = f"[OpenRouterProvider Error]: API call failed after {MAX_RETRIES} retries."
             if last_exception: err_msg += f" Last error: {type(last_exception).__name__}"
             yield {"type": "error", "content": err_msg}; return

        # --- Process the Successful Stream ---
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
                        logger.error(f"Error processing OpenRouter chunk content: {chunk_proc_err}", exc_info=True)
                        try: raw_chunk_data_for_log = chunk.model_dump_json(); logger.error(f"Raw OpenRouter chunk causing processing error: {raw_chunk_data_for_log}")
                        except Exception: pass
                        yield {"type": "error", "content": f"[OpenRouterProvider Error]: Error processing stream chunk - {type(chunk_proc_err).__name__}"}
                        return # Stop processing stream on chunk error

            except openai.APIError as stream_api_err:
                # Catch APIError specifically during stream iteration (this is what happened in the logs)
                logger.error(f"OpenRouter APIError occurred during stream processing: {stream_api_err}", exc_info=True)
                try: logger.error(f"APIError details: Status={stream_api_err.status_code}, Body={stream_api_err.body}")
                except Exception: pass
                # Yield a specific error message indicating a stream processing failure
                yield {"type": "error", "content": f"[OpenRouterProvider Error]: APIError during stream - {stream_api_err}"}
                return # Stop processing stream
            # *** END ADDED TRY/EXCEPT ***

            logger.debug(f"OpenRouter stream finished processing loop. Finish reason captured: {finish_reason}")

        except Exception as stream_err:
             # Catch any other unexpected errors during stream processing
             logger.exception(f"Unexpected Error processing OpenRouter response stream: {stream_err}")
             yield {"type": "error", "content": f"[OpenRouterProvider Error]: Unexpected Error processing stream - {type(stream_err).__name__}"}


        logger.info(f"OpenRouterProvider stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client_initialized={bool(self._openai_client)})>"
