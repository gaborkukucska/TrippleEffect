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
# Default timeout for initial connection/request
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600 # 10 minutes (Increased for potentially long non-streaming responses)

class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models with retry mechanism.
    Uses aiohttp. Can operate in streaming or non-streaming mode.
    Tool handling is done by the Agent Core via XML parsing.
    Includes enhanced handling for errors.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the Ollama provider. """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key:
            logger.warning("OllamaProvider Warning: API key provided but not used by standard Ollama.")

        # Determine mode based on kwargs or default to streaming
        self.streaming_mode = kwargs.pop('stream', True) # Default to stream=True if not specified

        self._session_kwargs = kwargs # Store remaining extra kwargs for session creation
        self._session: Optional[aiohttp.ClientSession] = None
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider initialized. Base URL: {self.base_url}. Mode: {mode_str}. Tool support via XML parsing by Agent.")

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

    async def stream_completion(
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
        Gets completion from Ollama. Operates in streaming or non-streaming mode.
        In non-streaming mode, yields a single 'response_chunk' with the full content.
        """
        session = await self._get_session()
        chat_endpoint = f"{self.base_url}/api/chat"

        if tools or tool_choice:
            logger.warning(f"OllamaProvider received tools/tool_choice arguments, but they will be ignored.")

        # Set payload based on streaming_mode
        payload = {
            "model": model,
            "messages": messages,
            "stream": self.streaming_mode, # Use the instance attribute
            "format": "json", # Request JSON format in both modes for consistency
            "options": {"temperature": temperature, **kwargs}
        }

        if max_tokens is not None: payload["options"]["num_predict"] = max_tokens
        payload["options"] = {k: v for k, v in payload["options"].items() if v is not None}

        mode_log = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider preparing request ({mode_log}). Model: {model}, Format: json, Options: {payload.get('options')}.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' ({mode_log})..."}

        response = None; last_exception = None; response_status = 0; response_text = ""
        # Adjust timeout based on mode - longer for non-streaming
        request_timeout = self._session_kwargs.get('timeout', DEFAULT_REQUEST_TIMEOUT_SECONDS if not self.streaming_mode else 300)

        # --- Retry Loop for API Call ---
        for attempt in range(MAX_RETRIES + 1):
            response = None
            try:
                 logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Timeout: {request_timeout}s")
                 async with session.post(chat_endpoint, json=payload, timeout=request_timeout) as resp:
                    response_status = resp.status
                    response_text = ""
                    # Read error body immediately if status indicates failure
                    if response_status >= 400:
                        response_text = await resp.text()
                        logger.debug(f"Ollama API returned error status {response_status}. Body: {response_text[:500]}...")

                    if response_status == 200:
                         logger.info(f"API call successful (Status {response_status}) on attempt {attempt + 1}.")
                         last_exception = None
                         response = resp # Store the response object for processing below
                         break
                    elif response_status >= 500:
                         last_exception = ValueError(f"Ollama API Error {response_status}")
                         logger.warning(f"Ollama API Error on attempt {attempt + 1}: Status {response_status}, Response: {response_text[:200]}...")
                         if attempt < MAX_RETRIES:
                             logger.info(f"Status {response_status} >= 500. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                             await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                         else: # Max retries reached for 5xx
                             logger.error(f"Max retries ({MAX_RETRIES}) reached after status {response_status}.")
                             yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries. Last error: Status {response_status} - {response_text[:100]}"}
                             return
                    else: # 4xx errors (non-retryable)
                        logger.error(f"Ollama API Client Error: Status {response_status}, Response: {response_text[:200]}")
                        yield {"type": "error", "content": f"[OllamaProvider Error]: Client Error {response_status} - {response_text[:100]}"}
                        return
            except RETRYABLE_OLLAMA_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable connection/timeout error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: # Max retries reached for retryable exceptions
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}.")
                    yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries reached after connection/timeout error. Last: {type(e).__name__}"}
                    return
            except Exception as e: # Catch unexpected errors during the request phase
                last_exception = e; logger.exception(f"Unexpected Error during Ollama API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: # Max retries reached for unexpected errors
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error.")
                    yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected Error after retries - {type(e).__name__}"}
                    return

        # --- Check if API call failed after all retries ---
        if response is None or response.status != 200:
            logger.error(f"Ollama API call failed after all retries. Last status: {response_status}. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[OllamaProvider Error]: API call failed after {MAX_RETRIES} retries. Status: {response_status}. Error: {type(last_exception).__name__ if last_exception else 'Failed Request'}"
            yield {"type": "error", "content": err_content}; return

        # --- Process the Response (Streaming or Non-Streaming) ---
        try:
            if self.streaming_mode:
                # --- Process Streaming Response ---
                stream_error_occurred = False
                chunks_received = 0
                async for line in response.content: # Default stream timeouts apply here
                    if line:
                        chunks_received += 1
                        decoded_line = ""
                        try:
                            decoded_line = line.decode('utf-8')
                            chunk_data = json.loads(decoded_line)

                            if chunk_data.get("error"):
                                error_msg = chunk_data["error"]
                                logger.error(f"Received error in Ollama stream: {error_msg}")
                                yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}
                                stream_error_occurred = True; break

                            message_chunk = chunk_data.get("message")
                            if message_chunk and isinstance(message_chunk, dict):
                                content_chunk = message_chunk.get("content")
                                if content_chunk:
                                    yield {"type": "response_chunk", "content": content_chunk}

                            if chunk_data.get("done", False):
                                if not stream_error_occurred:
                                    logger.debug(f"Received done=true from stream for model {model}. Chunks processed: {chunks_received}")
                                    total_duration = chunk_data.get("total_duration")
                                    if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                                break

                        except json.JSONDecodeError:
                            logger.error(f"Failed to decode JSON line from Ollama stream: {decoded_line}")
                            yield {"type": "error", "content": "[OllamaProvider Error]: Failed to decode stream chunk."}
                            stream_error_occurred = True; break
                        except Exception as e:
                            logger.error(f"Error processing Ollama stream line: {e}", exc_info=True)
                            logger.error(f"Problematic line (decoded): {decoded_line}")
                            yield {"type": "error", "content": f"[OllamaProvider Error]: Error processing stream chunk - {type(e).__name__}"}
                            stream_error_occurred = True; break

                if stream_error_occurred:
                    logger.error(f"Exiting Ollama stream processing due to error encountered after receiving {chunks_received} chunk(s).")

            else:
                # --- Process Non-Streaming Response ---
                logger.debug("Processing non-streaming response...")
                try:
                    response_data = await response.json()
                    if response_data.get("error"):
                         error_msg = response_data["error"]
                         logger.error(f"Received error in Ollama non-streaming response: {error_msg}")
                         yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}
                    elif response_data.get("message") and isinstance(response_data["message"], dict):
                        full_content = response_data["message"].get("content")
                        if full_content:
                            logger.info(f"Received non-streaming response content (length: {len(full_content)}). Yielding as single chunk.")
                            yield {"type": "response_chunk", "content": full_content}
                        else:
                             logger.warning("Non-streaming response message content was empty.")
                        # Yield final status based on non-streaming response data
                        if response_data.get("done", False):
                            total_duration = response_data.get("total_duration")
                            if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                        else:
                             logger.warning("Non-streaming response did not include done=true.")
                    else:
                         logger.error(f"Unexpected non-streaming response structure: {response_data}")
                         yield {"type": "error", "content": "[OllamaProvider Error]: Unexpected non-streaming response structure."}

                except json.JSONDecodeError:
                     raw_text = await response.text()
                     logger.error(f"Failed to decode non-streaming JSON response from Ollama. Raw text: {raw_text[:500]}...")
                     yield {"type": "error", "content": "[OllamaProvider Error]: Failed to decode non-streaming response."}
                except Exception as e:
                     logger.error(f"Error processing non-streaming response: {e}", exc_info=True)
                     yield {"type": "error", "content": f"[OllamaProvider Error]: Error processing non-streaming response - {type(e).__name__}"}

        # --- Catch errors during stream/response processing ---
        except aiohttp.ClientPayloadError as payload_err:
             logger.error(f"Ollama connection error (Payload): {payload_err}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Stream connection error (Payload) - {payload_err}"}
        except aiohttp.ClientResponseError as response_err:
             logger.error(f"Ollama connection error (Response): {response_err.status} {response_err.message}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Stream connection error ({response_err.status}) - {response_err.message}"}
        except asyncio.TimeoutError as timeout_err:
             logger.error(f"Ollama timeout error: {timeout_err}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Timed out waiting for data - {timeout_err}"}
        except aiohttp.ClientConnectionError as conn_err:
             logger.error(f"Ollama processing failed with ClientConnectionError: {conn_err}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Connection closed during processing - {conn_err}"}
        except Exception as e:
             logger.exception(f"Unexpected Error processing Ollama response: {type(e).__name__} - {e}")
             yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected processing error - {type(e).__name__}"}

        logger.info(f"OllamaProvider: stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        session_status = "closed" if self._session is None or self._session.closed else "open"
        mode = "streaming" if self.streaming_mode else "non-streaming"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', session='{session_status}', mode='{mode}')>"

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
