# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp
import json
import asyncio
import logging # Import logging
import time # Import time
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
# Define retryable exceptions for aiohttp
RETRYABLE_OLLAMA_EXCEPTIONS = (
    aiohttp.ClientConnectorError, # Includes connection refused, DNS errors etc.
    asyncio.TimeoutError
)

# Default Ollama API endpoint
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
# Default timeout for the single non-streaming request
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600 # 10 minutes (increase for non-streaming)

class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models with retry mechanism.
    Uses aiohttp to fetch the full completion in a single request (non-streaming).
    Tool handling is done by the Agent Core via XML parsing of the full response.
    Includes enhanced handling for errors. Uses JSON format.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the Ollama provider. """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key:
            logger.warning("OllamaProvider Warning: API key provided but not used by standard Ollama.")

        self._session_kwargs = kwargs # Store extra kwargs for session creation
        self._session: Optional[aiohttp.ClientSession] = None
        logger.info(f"OllamaProvider initialized. Base URL: {self.base_url}. **Mode: Non-Streaming**. Tool support via XML parsing by Agent.") # Updated log

    async def _get_session(self) -> aiohttp.ClientSession:
        """Creates or returns an existing aiohttp ClientSession using default timeouts."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(**self._session_kwargs)
            logger.info("OllamaProvider: Created new aiohttp session (using default timeouts).")
        return self._session

    async def close_session(self):
        """Closes the aiohttp session if it exists."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.info("OllamaProvider: Closed aiohttp session.")

    async def stream_completion( # Keep signature, but behavior changes
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None, # Ollama uses 'num_predict'
        tools: Optional[List[ToolDict]] = None, # Ignored
        tool_choice: Optional[str] = None, # Ignored
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Fetches the full completion from Ollama in a single request (non-streaming JSON mode).
        Yields the result as a single chunk or final response.
        """
        session = await self._get_session()
        chat_endpoint = f"{self.base_url}/api/chat"

        if tools or tool_choice:
            logger.warning(f"OllamaProvider received tools/tool_choice arguments, but they will be ignored.")

        # --- Payload for Non-Streaming JSON Mode ---
        payload = {
            "model": model,
            "messages": messages,
            "stream": False, # <<< Set stream to false
            "format": "json", # <<< Request JSON format
            "options": {"temperature": temperature, **kwargs}
        }
        # --- End Modification ---

        if max_tokens is not None: payload["options"]["num_predict"] = max_tokens
        payload["options"] = {k: v for k, v in payload["options"].items() if v is not None}

        logger.info(f"OllamaProvider preparing request. Model: {model}, Stream: False, Format: json, Options: {payload.get('options')}. Tools ignored.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' (non-streaming)..."}

        response_data = None; last_exception = None; response_status = 0
        # Increase timeout for non-streaming potentially long requests
        request_timeout = self._session_kwargs.get('timeout', DEFAULT_REQUEST_TIMEOUT_SECONDS)

        # --- Retry Loop for the Single API Call ---
        for attempt in range(MAX_RETRIES + 1):
            response_data = None # Reset data for this attempt
            try:
                 logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Timeout: {request_timeout}s")
                 # Use timeout for the entire request/response cycle
                 async with session.post(chat_endpoint, json=payload, timeout=request_timeout) as resp:
                    response_status = resp.status
                    if response_status == 200:
                        logger.info(f"API call successful (Status {response_status}) on attempt {attempt + 1}.")
                        response_data = await resp.json() # Get the full JSON response
                        last_exception = None
                        break # Exit retry loop on success
                    else:
                        # Handle API errors (retry 5xx, fail on 4xx)
                        error_text = await resp.text()
                        last_exception = ValueError(f"Ollama API Error {response_status}: {error_text[:200]}")
                        logger.warning(f"Ollama API Error on attempt {attempt + 1}: Status {response_status}, Response: {error_text[:200]}...")
                        if response_status >= 500 and attempt < MAX_RETRIES:
                             logger.info(f"Status {response_status} >= 500. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                             await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                        else: # 4xx or max retries for 5xx
                            logger.error(f"Non-retryable Ollama API error ({response_status}) or max retries reached.")
                            yield {"type": "error", "content": f"[OllamaProvider Error]: Status {response_status} - {error_text[:100]}"}
                            return

            except RETRYABLE_OLLAMA_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable connection/timeout error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}.")
                    yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries reached after connection/timeout error. Last: {type(e).__name__}"}
                    return
            except aiohttp.ContentTypeError as e: # Catch error if response isn't valid JSON
                 last_exception = e
                 logger.error(f"Ollama response was not valid JSON on attempt {attempt + 1}. Error: {e}")
                 # Read text for debugging if possible
                 try: error_text = await resp.text()
                 except: error_text = "[Could not read response text]"
                 logger.error(f"Raw response text (first 500 chars): {error_text[:500]}")
                 # Treat as fatal error after first attempt, or retry if configured? Let's treat as fatal for now.
                 yield {"type": "error", "content": f"[OllamaProvider Error]: Invalid JSON response received. Status: {response_status}."}
                 return
            except Exception as e:
                last_exception = e; logger.exception(f"Unexpected Error during Ollama API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error.")
                    yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected Error after retries - {type(e).__name__}"}
                    return

        # --- Check if API call failed after all retries ---
        if response_data is None:
            logger.error(f"Ollama API call failed after all retries. Last status: {response_status}. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[OllamaProvider Error]: API call failed after {MAX_RETRIES} retries. Status: {response_status}. Error: {type(last_exception).__name__ if last_exception else 'Failed Request'}"
            yield {"type": "error", "content": err_content}; return

        # --- Process the Successful Full Response ---
        try:
            if response_data.get("error"):
                error_msg = response_data["error"]
                logger.error(f"Ollama API returned an error in the response body: {error_msg}")
                yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}
                return

            message_data = response_data.get("message")
            full_content = ""
            if message_data and isinstance(message_data, dict):
                full_content = message_data.get("content", "")

            if not full_content:
                 logger.warning(f"Ollama response successful, but no message content found. Response keys: {response_data.keys()}")
                 # Yield nothing or an empty final response? Let's yield empty final response.
                 yield {"type": "final_response", "content": ""}
            else:
                 # Yield the full content as a single chunk (or final_response)
                 logger.debug(f"Ollama non-streaming request successful. Yielding full content (length: {len(full_content)}).")
                 yield {"type": "final_response", "content": full_content} # Use final_response type

            # Optionally yield status based on stats
            total_duration = response_data.get("total_duration")
            if total_duration:
                yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}

        except Exception as e:
             logger.exception(f"Unexpected Error processing Ollama full response: {type(e).__name__} - {e}")
             yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected error processing response - {type(e).__name__}"}

        logger.info(f"OllamaProvider: stream_completion (non-streaming mode) finished for model {model}.")


    def __repr__(self) -> str:
        session_status = "closed" if self._session is None or self._session.closed else "open"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', session='{session_status}', mode='non-streaming')>" # Indicate mode

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
