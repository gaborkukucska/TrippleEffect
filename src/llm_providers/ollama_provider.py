# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp # Changed from httpx
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
# Define retryable aiohttp exceptions (adjust as needed)
RETRYABLE_AIOHTTP_EXCEPTIONS = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError, # aiohttp raises this for timeouts
)
RETRYABLE_STATUS_CODES = [429, 500, 502, 503, 504]

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 1200.0
DEFAULT_TOTAL_TIMEOUT = 1800.0 # Total timeout for the request including connection and read

# Known valid Ollama options (Unchanged)
KNOWN_OLLAMA_OPTIONS = {
    "mirostat", "mirostat_eta", "mirostat_tau", "num_ctx", "num_gpu", "num_thread",
    "num_keep", "seed", "num_predict", "repeat_last_n", "repeat_penalty",
    "temperature", "tfs_z", "top_k", "top_p", "min_p", "use_mmap", "use_mlock",
    "numa", "num_batch", "main_gpu", "low_vram", "f16_kv", "logits_all",
    "vocab_only", "stop", "presence_penalty", "frequency_penalty", "penalize_newline",
    "typical_p"
}

class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models using aiohttp.
    Handles streaming by reading raw bytes and splitting by newline.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key: logger.warning("OllamaProvider Warning: API key provided but not used.")
        self.streaming_mode = kwargs.pop('stream', True)
        self._session_timeout_config = kwargs.pop('timeout', None) # Store timeout config if provided
        self._client_kwargs = kwargs # Store remaining kwargs for session creation
        self._session: Optional[aiohttp.ClientSession] = None
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider initialized with aiohttp. Base URL: {self.base_url}. Mode: {mode_str}.")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # Configure timeout using aiohttp.ClientTimeout
            if isinstance(self._session_timeout_config, aiohttp.ClientTimeout):
                timeout = self._session_timeout_config
            elif isinstance(self._session_timeout_config, (int, float)):
                 # If single value, use it for total, default others
                 timeout = aiohttp.ClientTimeout(
                     total=float(self._session_timeout_config),
                     connect=DEFAULT_CONNECT_TIMEOUT,
                     sock_read=DEFAULT_READ_TIMEOUT # sock_read is similar to httpx read timeout
                 )
                 logger.warning(f"Using provided single timeout value ({timeout.total}s) for total, explicit defaults for connect/read.")
            else:
                 timeout = aiohttp.ClientTimeout(
                     total=DEFAULT_TOTAL_TIMEOUT,
                     connect=DEFAULT_CONNECT_TIMEOUT,
                     sock_read=DEFAULT_READ_TIMEOUT
                 )
                 if self._session_timeout_config is not None: logger.warning(f"Invalid timeout config '{self._session_timeout_config}', using defaults.")

            # Create connector with limits (optional, but good practice)
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=10)

            # Default headers (aiohttp handles Connection header automatically based on HTTP version)
            headers = {
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json',
            }

            self._session = aiohttp.ClientSession(
                base_url=self.base_url,
                timeout=timeout,
                connector=connector,
                headers=headers,
                # Pass any remaining kwargs from init
                **self._client_kwargs
            )
            logger.info(f"OllamaProvider: Created new aiohttp ClientSession. Timeout: {timeout}")
        return self._session

    async def close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.info("OllamaProvider: Closed aiohttp ClientSession.")

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
        chat_endpoint = "/api/chat" # Correct Ollama endpoint

        if tools or tool_choice: logger.warning(f"OllamaProvider ignoring tools/tool_choice.")

        # Filter options (Unchanged logic)
        raw_options = {"temperature": temperature, **kwargs}
        valid_options = {k: v for k, v in raw_options.items() if k in KNOWN_OLLAMA_OPTIONS and v is not None}
        if max_tokens is not None: valid_options["num_predict"] = max_tokens
        ignored_options = {k: v for k, v in raw_options.items() if k not in KNOWN_OLLAMA_OPTIONS}
        if ignored_options: logger.warning(f"OllamaProvider ignoring unknown options: {ignored_options}")

        payload = { "model": model, "messages": messages, "stream": self.streaming_mode, **valid_options }

        mode_log = "Streaming" if self.streaming_mode else "Non-Streaming"
        options_log = valid_options # Log only valid options sent
        logger.info(f"OllamaProvider preparing request ({mode_log}). Model: {model}, Endpoint: {chat_endpoint}, Options: {options_log}.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' ({mode_log})..."}

        last_exception = None
        response: Optional[aiohttp.ClientResponse] = None

        # --- Retry Loop for Initial API Request ---
        for attempt in range(MAX_RETRIES + 1):
            last_exception = None
            response = None
            try:
                logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                # Use session.post for the request
                response = await session.post(chat_endpoint, json=payload)

                logger.info(f"Request headers: {dict(response.request_info.headers)}")
                logger.info(f"Response headers: {dict(response.headers)}")
                response_status = response.status

                # Check for HTTP errors
                if response_status >= 400:
                    response_text = ""
                    try:
                        response_text = await response.text()
                    except Exception as read_err:
                        logger.warning(f"Could not read error body status {response_status}: {read_err}")
                        response_text = f"(Read err: {read_err})"

                    logger.debug(f"Ollama API error status {response_status}. Body: {response_text[:500]}...")

                    # Check if retryable status code
                    if response_status in RETRYABLE_STATUS_CODES or response_status >= 500:
                        last_exception = aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response_status,
                            message=f"Status {response_status}",
                            headers=response.headers,
                        )
                        logger.warning(f"Ollama API Error attempt {attempt + 1}: Status {response_status}, Resp: {response_text[:200]}...")
                        await response.release() # Release connection before retry
                        if attempt < MAX_RETRIES:
                            logger.info(f"Status {response_status} retryable. Wait {RETRY_DELAY_SECONDS}s...")
                            await asyncio.sleep(RETRY_DELAY_SECONDS)
                            continue
                        else:
                            logger.error(f"Max retries ({MAX_RETRIES}) after status {response_status}.")
                            yield {"type": "error", "content": f"[Ollama Error]: Max retries. Last: Status {response_status} - {response_text[:100]}"}
                            return
                    else: # Non-retryable 4xx client error
                        logger.error(f"Ollama API Client Error: Status {response_status}, Resp: {response_text[:200]}")
                        yield {"type": "error", "content": f"[Ollama Error]: Client Error {response_status} - {response_text[:100]}"}
                        await response.release()
                        return
                else: # Status 2xx - Success
                    logger.info(f"API call headers OK (Status {response_status}) attempt {attempt + 1}. Start stream.")
                    break # Exit retry loop successfully

            except RETRYABLE_AIOHTTP_EXCEPTIONS as e:
                last_exception = e
                logger.warning(f"Retryable aiohttp error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if response: await response.release() # Ensure connection is released
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}.")
                    yield {"type": "error", "content": f"[Ollama Error]: Max retries after connection/timeout. Last: {e}"}
                    return
            except Exception as e:
                last_exception = e
                logger.exception(f"Unexpected Error during API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if response: await response.release() # Ensure connection is released
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) after unexpected error.")
                    yield {"type": "error", "content": f"[Ollama Error]: Unexpected Error after retries - {type(e).__name__}"}
                    return

        if response is None or response.status >= 400: # Check if request failed after all retries
            logger.error(f"Ollama API request failed. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[Ollama Error]: API request failed after {MAX_RETRIES} retries. Error: {type(last_exception).__name__ if last_exception else 'Request Failed'}"
            if response: await response.release()
            yield {"type": "error", "content": err_content}
            return

        # --- Process the Successful Response Stream ---
        byte_buffer = b""
        processed_lines = 0
        stream_error_occurred = False
        try:
            if self.streaming_mode:
                logger.debug("Starting streaming using response.content.iter_any()")
                # Use iter_any() for raw bytes
                async for chunk in response.content.iter_any():
                    if not chunk: # Handle empty chunks if they occur
                        continue
                    byte_buffer += chunk
                    # Process lines separated by newline
                    while b'\n' in byte_buffer:
                        line_bytes, byte_buffer = byte_buffer.split(b'\n', 1)
                        line = line_bytes.decode('utf-8').strip()
                        if not line:
                            continue
                        processed_lines += 1
                        try:
                            chunk_data = json.loads(line)
                            if chunk_data.get("error"):
                                error_msg = chunk_data["error"]
                                logger.error(f"Ollama stream error: {error_msg}")
                                yield {"type": "error", "content": f"[Ollama Error]: {error_msg}"}
                                stream_error_occurred = True; await response.release(); return # Exit on error

                            if content_chunk := chunk_data.get("message", {}).get("content"):
                                yield {"type": "response_chunk", "content": content_chunk}

                            if chunk_data.get("done", False):
                                total_duration = chunk_data.get("total_duration")
                                logger.debug(f"Received done=true. Total duration: {total_duration}ns")
                                if total_duration:
                                    yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)"}
                                stream_error_occurred = False; await response.release(); return # Exit cleanly on done
                        except json.JSONDecodeError as e:
                            logger.error(f"JSONDecodeError: {e} - Line: {line[:200]}")
                            yield {"type": "error", "content": "Invalid JSON response"}
                            stream_error_occurred = True; await response.release(); return # Exit on error
                        except Exception as e:
                            logger.error(f"Stream processing error: {str(e)}")
                            yield {"type": "error", "content": f"Stream error: {str(e)}"}
                            stream_error_occurred = True; await response.release(); return # Exit on error

                logger.debug(f"Finished streaming loop (iter_any). Processed lines: {processed_lines}. Error: {stream_error_occurred}")

                # Process any remaining data in the buffer after the loop finishes
                if byte_buffer.strip() and not stream_error_occurred:
                    logger.warning(f"Processing remaining buffer after loop: {byte_buffer[:200]}...")
                    line = byte_buffer.decode('utf-8').strip()
                    try:
                        chunk_data = json.loads(line)
                        if chunk_data.get("done", False):
                            logger.debug("Processed final 'done' from remaining buffer.")
                            total_duration = chunk_data.get("total_duration")
                            if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)"}
                        elif content_chunk := chunk_data.get("message", {}).get("content"):
                            logger.warning("Final buffer had message content.")
                            yield {"type": "response_chunk", "content": content_chunk}
                        else:
                            logger.warning("Final buffer not 'done' or message.")
                    except Exception as final_e:
                        logger.error(f"Could not parse final buffer: {final_e}")
                        yield {"type": "error", "content": "Invalid JSON in final buffer"}
                        stream_error_occurred = True

            else: # Non-Streaming
                 logger.debug("Processing non-streaming response...")
                 try:
                     response_data_text = await response.text()
                     response_data = json.loads(response_data_text)
                     if response_data.get("error"):
                         error_msg = response_data["error"]
                         logger.error(f"Ollama non-streaming error: {error_msg}")
                         yield {"type": "error", "content": f"[Ollama Error]: {error_msg}"}
                     elif response_data.get("message") and isinstance(response_data["message"], dict):
                         full_content = response_data["message"].get("content")
                         if full_content:
                             logger.info(f"Non-streaming len: {len(full_content)}")
                             yield {"type": "response_chunk", "content": full_content}
                         else:
                             logger.warning("Non-streaming message content empty.")
                         if response_data.get("done", False):
                             total_duration = response_data.get("total_duration")
                             if total_duration:
                                 yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                         else:
                             logger.warning("Non-streaming missing done=true.")
                     else:
                         logger.error(f"Unexpected non-streaming structure: {response_data}")
                         yield {"type": "error", "content": "[Ollama Error]: Unexpected non-streaming structure."}
                 except json.JSONDecodeError:
                     logger.error(f"Failed non-streaming JSON decode. Raw: {response_data_text[:500]}...")
                     yield {"type": "error", "content": "[Ollama Error]: Failed non-streaming decode."}
                 except Exception as e:
                     logger.error(f"Error processing non-streaming: {e}", exc_info=True)
                     yield {"type": "error", "content": f"[Ollama Error]: Non-streaming processing error - {type(e).__name__}"}

        # --- Catch exceptions DURING stream processing ---
        except aiohttp.ClientPayloadError as payload_err: # Specific error for payload issues during streaming
            logger.error(f"Ollama processing failed with ClientPayloadError: {payload_err}", exc_info=True)
            yield {"type": "error", "content": f"[Ollama Error]: Connection closed unexpectedly during stream - {payload_err}"}
            stream_error_occurred = True
        except aiohttp.ClientConnectionError as conn_err: # Catch connection errors during streaming
             logger.error(f"Ollama processing failed with ClientConnectionError: {conn_err}", exc_info=True)
             yield {"type": "error", "content": f"[Ollama Error]: Network error during stream - {conn_err}"}
             stream_error_occurred = True
        except asyncio.TimeoutError as timeout_err: # Catch timeouts during streaming
            logger.error(f"Ollama timeout during stream read (read={session.timeout.sock_read}s): {timeout_err}", exc_info=False)
            yield {"type": "error", "content": f"[Ollama Error]: Timeout waiting for stream data (read={session.timeout.sock_read}s)"}
            stream_error_occurred = True
        except Exception as e:
            logger.exception(f"Unexpected Error processing Ollama response stream: {type(e).__name__} - {e}")
            yield {"type": "error", "content": f"[Ollama Error]: Unexpected stream processing error - {type(e).__name__}"}
            stream_error_occurred = True
        finally:
            # Ensure the response connection is always released
            if response and not response.closed:
                response.release()

        if not stream_error_occurred:
            logger.info(f"OllamaProvider: stream_completion finished cleanly for model {model}.")
        else:
            logger.warning(f"OllamaProvider: stream_completion finished for model {model}, but error encountered.")


    def __repr__(self) -> str:
        session_status = "closed" if self._session is None or self._session.closed else "open"
        mode = "streaming" if self.streaming_mode else "non-streaming"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', session='{session_status}', mode='{mode}')>"

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
