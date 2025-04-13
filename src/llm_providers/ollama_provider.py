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
# Use explicit timeouts again
DEFAULT_CONNECT_TIMEOUT = 15 # seconds to establish connection
DEFAULT_TOTAL_TIMEOUT = 600 # seconds for the entire request/response cycle (10 mins)
DEFAULT_SOCK_READ_TIMEOUT = 300 # seconds between receiving chunks (5 minutes)

# Known valid Ollama options
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
    LLM Provider implementation for local Ollama models.
    Uses aiohttp with iter_chunked stream reading and corrected error handling.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the Ollama provider. """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key: logger.warning("OllamaProvider Warning: API key provided but not used.")
        self.streaming_mode = kwargs.pop('stream', True)
        self._session_timeout_config = kwargs.pop('timeout', None)
        self._session_kwargs = kwargs
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None # Keep for potential future use
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider initialized. Base URL: {self.base_url}. Mode: {mode_str}.")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Creates or returns an existing aiohttp ClientSession with explicit timeouts."""
        # (This method remains the same as the previous correct version)
        if self._session is None or self._session.closed:
            if isinstance(self._session_timeout_config, aiohttp.ClientTimeout):
                timeout = self._session_timeout_config
            elif isinstance(self._session_timeout_config, (int, float)):
                timeout_total = float(self._session_timeout_config)
                timeout = aiohttp.ClientTimeout(total=timeout_total, connect=DEFAULT_CONNECT_TIMEOUT, sock_read=DEFAULT_SOCK_READ_TIMEOUT)
                logger.warning(f"Using provided single timeout value ({timeout.total}s) for total, explicit defaults for connect/sock_read.")
            else:
                timeout = aiohttp.ClientTimeout(total=DEFAULT_TOTAL_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT, sock_read=DEFAULT_SOCK_READ_TIMEOUT)
                if self._session_timeout_config is not None: logger.warning(f"Invalid timeout config '{self._session_timeout_config}', using defaults.")

            self._session = aiohttp.ClientSession(timeout=timeout, **self._session_kwargs)
            logger.info(f"OllamaProvider: Created new aiohttp session with timeout settings: Total={timeout.total}s, Connect={timeout.connect}s, SockRead={timeout.sock_read}s")
        return self._session

    async def close_session(self):
        """Closes the aiohttp session if it exists."""
        # (This method remains the same as the previous correct version)
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.info("OllamaProvider: Closed aiohttp session.")
        if hasattr(self, '_connector') and self._connector and not self._connector.closed:
            await self._connector.close()
            self._connector = None
            logger.info("OllamaProvider: Closed custom TCP connector.")

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
        """
        Gets completion from Ollama. Handles streaming with buffering and timeouts.
        Corrected error handling and control flow.
        """
        session = await self._get_session()
        chat_endpoint = f"{self.base_url}/api/chat"

        if tools or tool_choice: logger.warning(f"OllamaProvider ignoring tools/tool_choice.")

        # Filter options
        raw_options = {"temperature": temperature, **kwargs}
        valid_options = {k: v for k, v in raw_options.items() if k in KNOWN_OLLAMA_OPTIONS and v is not None}
        if max_tokens is not None: valid_options["num_predict"] = max_tokens
        ignored_options = {k: v for k, v in raw_options.items() if k not in KNOWN_OLLAMA_OPTIONS}
        if ignored_options: logger.warning(f"OllamaProvider ignoring unknown options: {ignored_options}")

        payload = { "model": model, "messages": messages, "stream": self.streaming_mode, "format": "json", "options": valid_options }
        if not payload["options"]: del payload["options"]

        mode_log = "Streaming" if self.streaming_mode else "Non-Streaming"
        options_log = payload.get('options', '{}')
        logger.info(f"OllamaProvider preparing request ({mode_log}). Model: {model}, Options: {options_log}.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' ({mode_log})..."}

        response = None; last_exception = None; response_status = 0; response_text = ""

        # --- Retry Loop for Initial API Call (Unchanged) ---
        for attempt in range(MAX_RETRIES + 1):
            response = None
            try:
                 logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                 async with session.post(chat_endpoint, json=payload) as resp:
                    response_status = resp.status
                    response_text = ""
                    if response_status >= 400:
                        response_text = await resp.text(encoding='utf-8', errors='ignore')
                        logger.debug(f"Ollama API error status {response_status}. Body: {response_text[:500]}...")
                    if response_status == 200:
                         logger.info(f"API call successful (Status {response_status}) on attempt {attempt + 1}.")
                         last_exception = None; response = resp; break
                    elif response_status >= 500:
                         last_exception = ValueError(f"Ollama API Error {response_status}")
                         logger.warning(f"Ollama API Error attempt {attempt + 1}: Status {response_status}, Resp: {response_text[:200]}...")
                         if attempt < MAX_RETRIES: logger.info(f"Status {response_status} >= 500. Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                         else: logger.error(f"Max retries ({MAX_RETRIES}) reached after status {response_status}."); yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries. Last: Status {response_status} - {response_text[:100]}"}; return
                    else: # 4xx errors
                        logger.error(f"Ollama API Client Error: Status {response_status}, Resp: {response_text[:200]}"); yield {"type": "error", "content": f"[OllamaProvider Error]: Client Error {response_status} - {response_text[:100]}"}; return
            except RETRYABLE_OLLAMA_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}."); yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries after connection/timeout. Last: {type(e).__name__}"}; return
            except Exception as e:
                last_exception = e; logger.exception(f"Unexpected Error API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error."); yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected Error after retries - {type(e).__name__}"}; return

        if response is None or response.status != 200:
            logger.error(f"Ollama API call failed after all retries. Last status: {response_status}. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[OllamaProvider Error]: API call failed after {MAX_RETRIES} retries. Status: {response_status}. Error: {type(last_exception).__name__ if last_exception else 'Failed Request'}"
            yield {"type": "error", "content": err_content}; return

        # --- Process the Response ---
        byte_buffer = b""
        processed_lines = 0
        stream_error_occurred = False # Flag to signal error during stream processing

        # --- Main Try Block for Stream Processing ---
        try:
            if self.streaming_mode:
                logger.debug("Starting streaming response processing loop using iter_chunked...")
                chunk_size = 1024
                async for chunk in response.content.iter_chunked(chunk_size):
                    if not chunk: continue
                    # logger.log(logging.DEBUG - 1, f"Chunk: {chunk[:100]}...")
                    byte_buffer += chunk
                    while True:
                        newline_pos = byte_buffer.find(b'\n')
                        if newline_pos == -1: break

                        json_line = byte_buffer[:newline_pos]
                        byte_buffer = byte_buffer[newline_pos + 1:]

                        if not json_line.strip(): continue

                        processed_lines += 1
                        decoded_line = ""
                        try:
                            decoded_line = json_line.decode('utf-8')
                            # logger.log(logging.DEBUG - 1, f"Parsing line {processed_lines}: {decoded_line[:200]}...")
                            chunk_data = json.loads(decoded_line)

                            if chunk_data.get("error"):
                                error_msg = chunk_data["error"]; logger.error(f"Ollama stream error object: {error_msg}")
                                yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}; stream_error_occurred = True; break # Break inner loop

                            message_chunk = chunk_data.get("message")
                            if message_chunk and isinstance(message_chunk, dict):
                                content_chunk = message_chunk.get("content");
                                if content_chunk: yield {"type": "response_chunk", "content": content_chunk}

                            if chunk_data.get("done", False):
                                logger.debug(f"Received done=true object. Line: {processed_lines}")
                                total_duration = chunk_data.get("total_duration");
                                if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                                # Continue processing buffer even after done, then let outer loop finish

                        except json.JSONDecodeError:
                            logger.error(f"JSONDecodeError line {processed_lines}: {decoded_line[:500]}...")
                            yield {"type": "error", "content": "[OllamaProvider Error]: Failed stream JSON decode."}; stream_error_occurred = True; break # Break inner loop
                        except Exception as e: # Catch other errors during line processing
                            logger.error(f"Error processing stream line {processed_lines}: {e}", exc_info=True); logger.error(f"Problem line: {decoded_line[:500]}")
                            yield {"type": "error", "content": f"[OllamaProvider Error]: Stream processing error - {type(e).__name__}"}; stream_error_occurred = True; break # Break inner loop

                    if stream_error_occurred: break # Break outer async for loop

                logger.debug(f"Finished streaming loop. Processed {processed_lines} lines. Error encountered: {stream_error_occurred}")
                # If stream finished due to an error, return now
                if stream_error_occurred: return

                # Process any remaining buffer data after loop finishes cleanly
                if byte_buffer.strip():
                     logger.warning(f"Processing remaining buffer after loop: {byte_buffer[:200]}...")
                     try:
                         chunk_data = json.loads(byte_buffer.decode('utf-8'))
                         if chunk_data.get("done", False): logger.debug("Processed final 'done' object from buffer.")
                         else: logger.warning("Final buffer content wasn't a 'done' object.")
                     except Exception as final_e: logger.error(f"Could not parse final buffer: {final_e}")

            else: # Non-Streaming (Unchanged)
                 # ... (non-streaming logic remains the same) ...
                 logger.debug("Processing non-streaming response...")
                 try:
                     response_data = await response.json()
                     if response_data.get("error"): error_msg = response_data["error"]; logger.error(f"Ollama non-streaming error: {error_msg}"); yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}
                     elif response_data.get("message") and isinstance(response_data["message"], dict):
                         full_content = response_data["message"].get("content");
                         if full_content: logger.info(f"Non-streaming response len: {len(full_content)}"); yield {"type": "response_chunk", "content": full_content}
                         else: logger.warning("Non-streaming message content empty.")
                         if response_data.get("done", False): total_duration = response_data.get("total_duration");
                         if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                         else: logger.warning("Non-streaming response missing done=true.")
                     else: logger.error(f"Unexpected non-streaming structure: {response_data}"); yield {"type": "error", "content": "[OllamaProvider Error]: Unexpected non-streaming structure."}
                 except json.JSONDecodeError: raw_text = await response.text(); logger.error(f"Failed non-streaming JSON decode. Raw: {raw_text[:500]}..."); yield {"type": "error", "content": "[OllamaProvider Error]: Failed non-streaming decode."}
                 except Exception as e: logger.error(f"Error processing non-streaming: {e}", exc_info=True); yield {"type": "error", "content": f"[OllamaProvider Error]: Non-streaming processing error - {type(e).__name__}"}

        # --- Catch exceptions that occur DURING the stream processing loop ---
        except asyncio.TimeoutError as timeout_err:
             logger.error(f"Ollama session timeout during stream read (sock_read={session.timeout.sock_read}s): {timeout_err}", exc_info=False)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Session timed out waiting for stream data (sock_read={session.timeout.sock_read}s)"}
             # No need for return/break here, except block naturally exits the try
        except aiohttp.ClientConnectionError as conn_err:
             logger.error(f"Ollama processing failed with ClientConnectionError: {conn_err}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Connection closed during stream processing - {conn_err}"}
             # No need for return/break here
        except aiohttp.ClientPayloadError as payload_err:
             logger.error(f"Ollama error (Payload): {payload_err}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Stream error (Payload) - {payload_err}"}
             # No need for return/break here
        except aiohttp.ClientResponseError as response_err:
             logger.error(f"Ollama error (Response): {response_err.status} {response_err.message}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Stream error ({response_err.status}) - {response_err.message}"}
             # No need for return/break here
        except Exception as e:
             logger.exception(f"Unexpected Error processing Ollama response stream: {type(e).__name__} - {e}")
             yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected stream processing error - {type(e).__name__}"}
             # No need for return/break here
        # --- The 'finally' block is not needed here, the function proceeds ---

        # This log is reached if the try block completes without yielding an error and returning early
        if not stream_error_occurred:
            logger.info(f"OllamaProvider: stream_completion finished cleanly for model {model}.")
        else:
            logger.warning(f"OllamaProvider: stream_completion finished for model {model}, but an error was encountered during streaming.")


    def __repr__(self) -> str:
        # (Unchanged)
        session_status = "closed" if self._session is None or self._session.closed else "open"
        mode = "streaming" if self.streaming_mode else "non-streaming"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', session='{session_status}', mode='{mode}')>"

    async def __aenter__(self):
        # (Unchanged)
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # (Unchanged)
        await self.close_session()
