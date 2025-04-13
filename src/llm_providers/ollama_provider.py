# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
RETRYABLE_OLLAMA_EXCEPTIONS = (
    aiohttp.ClientConnectorError,
    asyncio.TimeoutError
)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600

class OllamaProvider(BaseLLMProvider):
    # ... (init, _get_session, close_session remain the same) ...
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the Ollama provider. """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key:
            logger.warning("OllamaProvider Warning: API key provided but not used by standard Ollama.")

        self.streaming_mode = kwargs.pop('stream', True) # Default to stream=True

        self._session_kwargs = kwargs
        self._session: Optional[aiohttp.ClientSession] = None
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider initialized. Base URL: {self.base_url}. Mode: {mode_str}. Tool support via XML parsing by Agent.")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Creates or returns an existing aiohttp ClientSession using default timeouts."""
        if self._session is None or self._session.closed:
            # Pass any specific timeout settings from kwargs if needed
            timeout = aiohttp.ClientTimeout(total=self._session_kwargs.get('timeout', DEFAULT_REQUEST_TIMEOUT_SECONDS))
            self._session = aiohttp.ClientSession(timeout=timeout, **{k:v for k,v in self._session_kwargs.items() if k != 'timeout'})
            logger.info(f"OllamaProvider: Created new aiohttp session with timeout {timeout.total}s.")
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
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:

        session = await self._get_session()
        chat_endpoint = f"{self.base_url}/api/chat"

        if tools or tool_choice:
            logger.warning(f"OllamaProvider received tools/tool_choice arguments, but they will be ignored.")

        payload = {
            "model": model,
            "messages": messages,
            "stream": self.streaming_mode,
            "format": "json", # Keep requesting JSON for consistency
            "options": {"temperature": temperature, **kwargs}
        }
        if max_tokens is not None: payload["options"]["num_predict"] = max_tokens
        payload["options"] = {k: v for k, v in payload["options"].items() if v is not None}

        mode_log = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider preparing request ({mode_log}). Model: {model}, Format: json, Options: {payload.get('options')}.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' ({mode_log})..."}

        response = None; last_exception = None; response_status = 0; response_text = ""
        request_timeout = self._session_kwargs.get('timeout', DEFAULT_REQUEST_TIMEOUT_SECONDS if not self.streaming_mode else 300)

        # --- Retry Loop for API Call (same as before) ---
        for attempt in range(MAX_RETRIES + 1):
            response = None
            try:
                 logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Timeout: {request_timeout}s")
                 # Use configured session timeout
                 async with session.post(chat_endpoint, json=payload) as resp: # Removed explicit timeout here, uses session default
                    response_status = resp.status
                    response_text = ""
                    if response_status >= 400:
                        response_text = await resp.text() # Read error text immediately
                        logger.debug(f"Ollama API returned error status {response_status}. Body: {response_text[:500]}...")

                    if response_status == 200:
                         logger.info(f"API call successful (Status {response_status}) on attempt {attempt + 1}.")
                         last_exception = None
                         response = resp
                         break # Success, break retry loop
                    # ... (rest of retry/error handling for 4xx/5xx remains the same as your original) ...
                    elif response_status >= 500:
                         last_exception = ValueError(f"Ollama API Error {response_status}")
                         logger.warning(f"Ollama API Error on attempt {attempt + 1}: Status {response_status}, Response: {response_text[:200]}...")
                         if attempt < MAX_RETRIES:
                             logger.info(f"Status {response_status} >= 500. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                             await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                         else: logger.error(f"Max retries ({MAX_RETRIES}) reached after status {response_status}."); yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries. Last error: Status {response_status} - {response_text[:100]}"}; return
                    else: # 4xx errors (non-retryable)
                        logger.error(f"Ollama API Client Error: Status {response_status}, Response: {response_text[:200]}"); yield {"type": "error", "content": f"[OllamaProvider Error]: Client Error {response_status} - {response_text[:100]}"}; return
            except RETRYABLE_OLLAMA_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable connection/timeout error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}."); yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries reached after connection/timeout error. Last: {type(e).__name__}"}; return
            except Exception as e:
                last_exception = e; logger.exception(f"Unexpected Error during Ollama API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error."); yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected Error after retries - {type(e).__name__}"}; return


        if response is None or response.status != 200:
            logger.error(f"Ollama API call failed after all retries. Last status: {response_status}. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[OllamaProvider Error]: API call failed after {MAX_RETRIES} retries. Status: {response_status}. Error: {type(last_exception).__name__ if last_exception else 'Failed Request'}"
            yield {"type": "error", "content": err_content}; return

        # --- Process the Response ---
        byte_buffer = b"" # Buffer to hold incoming data chunks
        try:
            if self.streaming_mode:
                # --- Process Streaming Response with Buffering ---
                stream_error_occurred = False
                async for chunk in response.content.iter_any(): # Read whatever chunk size arrives
                    if not chunk: continue # Skip empty chunks
                    byte_buffer += chunk
                    # Try to process complete JSON objects separated by newlines
                    while True:
                        try:
                            # Find the first newline character
                            newline_pos = byte_buffer.find(b'\n')
                            if newline_pos == -1:
                                break # No complete object yet, wait for more data

                            # Extract the potential JSON object string
                            json_line = byte_buffer[:newline_pos]
                            byte_buffer = byte_buffer[newline_pos + 1:] # Remove processed part + newline

                            if not json_line.strip(): continue # Skip empty lines

                            # Decode and parse the JSON object
                            chunk_data = json.loads(json_line.decode('utf-8'))

                            # Process the parsed JSON object (same logic as before)
                            if chunk_data.get("error"):
                                error_msg = chunk_data["error"]
                                logger.error(f"Received error in Ollama stream object: {error_msg}")
                                yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}
                                stream_error_occurred = True; break # Break inner loop on error

                            message_chunk = chunk_data.get("message")
                            if message_chunk and isinstance(message_chunk, dict):
                                content_chunk = message_chunk.get("content")
                                if content_chunk:
                                    yield {"type": "response_chunk", "content": content_chunk}

                            if chunk_data.get("done", False):
                                logger.debug(f"Received done=true from stream object for model {model}.")
                                total_duration = chunk_data.get("total_duration")
                                if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                                # Don't break inner loop here, process rest of buffer if any, let outer loop finish
                        except json.JSONDecodeError:
                            logger.warning(f"JSONDecodeError processing line: {json_line.decode('utf-8')[:100]}... Buffer content starts with: {byte_buffer[:100]}... Waiting for more data.")
                            # Put the unprocessed part back into the buffer *before* the newline position
                            # This handles cases where a chunk ends mid-JSON object before a newline
                            byte_buffer = json_line + b'\n' + byte_buffer
                            break # Wait for more data in the next chunk
                        except Exception as e:
                            logger.error(f"Error processing buffered Ollama stream line: {e}", exc_info=True)
                            logger.error(f"Problematic line (bytes): {json_line[:200]}")
                            yield {"type": "error", "content": f"[OllamaProvider Error]: Error processing stream object - {type(e).__name__}"}
                            stream_error_occurred = True; break # Break inner loop

                    if stream_error_occurred: break # Break outer async for loop

                # After stream ends, check if anything remains in buffer (last object might not have newline)
                if not stream_error_occurred and byte_buffer.strip():
                    logger.debug(f"Processing remaining buffer content after stream end: {byte_buffer[:200]}...")
                    try:
                        chunk_data = json.loads(byte_buffer.decode('utf-8'))
                        # Process the final object (similar logic as above)
                        if chunk_data.get("error"):
                            # ... handle error ...
                            pass
                        message_chunk = chunk_data.get("message")
                        if message_chunk and isinstance(message_chunk, dict):
                             # ... yield content_chunk ...
                             pass
                        if chunk_data.get("done", False):
                             # ... yield final status ...
                             pass
                    except json.JSONDecodeError:
                         logger.error(f"Failed to decode final buffer content: {byte_buffer.decode('utf-8')[:500]}...")
                         yield {"type": "error", "content": "[OllamaProvider Error]: Failed to decode final stream object."}
                    except Exception as e:
                         logger.error(f"Error processing final buffer content: {e}", exc_info=True)
                         yield {"type": "error", "content": f"[OllamaProvider Error]: Error processing final object - {type(e).__name__}"}

                if stream_error_occurred:
                    logger.error(f"Exiting Ollama stream processing due to error encountered.")


            else:
                # --- Process Non-Streaming Response (remains largely the same) ---
                logger.debug("Processing non-streaming response...")
                try:
                    # Note: Ensure session timeout was long enough for this
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
                        else: logger.warning("Non-streaming response message content was empty.")
                        if response_data.get("done", False):
                            total_duration = response_data.get("total_duration")
                            if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                        else: logger.warning("Non-streaming response did not include done=true.")
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

        # --- Catch errors during stream/response processing (aiohttp errors) ---
        except aiohttp.ClientPayloadError as payload_err:
             logger.error(f"Ollama connection error (Payload): {payload_err}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Stream connection error (Payload) - {payload_err}"}
        except aiohttp.ClientResponseError as response_err:
             logger.error(f"Ollama connection error (Response): {response_err.status} {response_err.message}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Stream connection error ({response_err.status}) - {response_err.message}"}
        except asyncio.TimeoutError as timeout_err: # Catch timeout during stream reading
             logger.error(f"Ollama timeout error during stream read: {timeout_err}", exc_info=False) # Don't need full stack usually
             yield {"type": "error", "content": f"[OllamaProvider Error]: Timed out waiting for stream data - {timeout_err}"}
        except aiohttp.ClientConnectionError as conn_err:
             # This might be the original error seen in logs, now caught explicitly
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
