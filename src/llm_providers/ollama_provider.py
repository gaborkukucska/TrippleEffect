# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict
# --- Import centralized constants ---
from src.agents.constants import (
    MAX_RETRIES, RETRY_DELAY_SECONDS, RETRYABLE_STATUS_CODES, KNOWN_OLLAMA_OPTIONS
)
# --- End Import ---

logger = logging.getLogger(__name__)

# Define retryable aiohttp exceptions (adjust as needed)
RETRYABLE_AIOHTTP_EXCEPTIONS = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError, # aiohttp raises this for timeouts
)
# RETRYABLE_STATUS_CODES imported from constants

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 1200.0 # 20 minutes for reading the stream
DEFAULT_TOTAL_TIMEOUT = 1800.0 # 30 minutes total request time

# KNOWN_OLLAMA_OPTIONS imported from constants

from src.config.settings import settings # Import the global settings object

class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models using aiohttp.
    Handles streaming by reading raw bytes and splitting by newline.
    Conditionally uses an integrated proxy based on settings.
    Creates a new ClientSession for each request to ensure clean state
    and applies 'Connection: close' header.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        # Determine the effective base URL based on proxy settings
        use_proxy = settings.USE_OLLAMA_PROXY
        proxy_port = settings.OLLAMA_PROXY_PORT
        direct_base_url = base_url or settings.OLLAMA_BASE_URL or DEFAULT_OLLAMA_BASE_URL # Priority: explicit arg -> .env -> default

        if use_proxy:
            self.base_url = f"http://localhost:{proxy_port}" # Use proxy URL
            logger.info(f"Ollama proxy enabled. Using proxy URL: {self.base_url}")
        else:
            self.base_url = direct_base_url.rstrip('/') # Use direct URL
            logger.info(f"Ollama proxy disabled. Using direct URL: {self.base_url}")

        if api_key: logger.warning("OllamaProvider Warning: API key provided but not used.")
        # Timeout config can still be passed via kwargs if needed, applied per request
        self._session_timeout_config = kwargs.pop('timeout', None)
        # self._client_kwargs = kwargs # REMOVED - Don't store arbitrary kwargs for session creation
        # Remove session instance variable, sessions are created per request now
        # self._session: Optional[aiohttp.ClientSession] = None
        self.streaming_mode = True # Keep streaming default
        mode_str = "Streaming"
        # Log the *effective* base URL being used
        logger.info(f"OllamaProvider initialized with aiohttp. Effective Base URL: {self.base_url}. Mode: {mode_str}. Sessions created per-request.")

    async def _create_request_session(self) -> aiohttp.ClientSession:
        """Creates a new aiohttp ClientSession for a single request, targeting the effective base_url."""
        # Configure timeout using aiohttp.ClientTimeout
        if isinstance(self._session_timeout_config, aiohttp.ClientTimeout):
            timeout = self._session_timeout_config
        elif isinstance(self._session_timeout_config, (int, float)):
             timeout = aiohttp.ClientTimeout(
                 total=float(self._session_timeout_config), connect=DEFAULT_CONNECT_TIMEOUT, sock_read=DEFAULT_READ_TIMEOUT
             )
             logger.debug(f"Using provided single timeout value ({timeout.total}s) for total.")
        else:
             timeout = aiohttp.ClientTimeout(
                 total=DEFAULT_TOTAL_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT, sock_read=DEFAULT_READ_TIMEOUT
             )
             if self._session_timeout_config is not None: logger.warning(f"Invalid timeout config '{self._session_timeout_config}', using defaults.")

        # Create connector with limits (optional) - REMOVED force_close=True
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5) # Removed force_close=True

        # Default headers - REMOVED Connection: close
        headers = {
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
            # 'Connection': 'close', # REMOVED
        }

        session = aiohttp.ClientSession(
            base_url=self.base_url,
            timeout=timeout,
            connector=connector,
            headers=headers
            # **self._client_kwargs # REMOVED - Do not pass arbitrary kwargs here
        )
        logger.debug(f"OllamaProvider: Created new aiohttp ClientSession for request. Timeout: {timeout}")
        return session

    async def close_session(self):
        # This method is now a no-op as sessions are created/closed per request
        logger.debug("OllamaProvider: close_session called (no-op - sessions are per-request).")
        pass

    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None, # Added max_tokens
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:

        # Create a new session specifically for this request
        session = await self._create_request_session()
        chat_endpoint = "/api/chat"

        if tools or tool_choice: logger.warning(f"OllamaProvider ignoring tools/tool_choice.")

        raw_options = {"temperature": temperature, **kwargs}
        valid_options = {k: v for k, v in raw_options.items() if k in KNOWN_OLLAMA_OPTIONS and v is not None}
        # --- MODIFIED: Add max_tokens to valid_options ---
        if max_tokens is not None:
            valid_options["num_predict"] = max_tokens # Ollama uses num_predict for max tokens
            logger.debug(f"Setting Ollama num_predict (max_tokens) to: {max_tokens}")
        # --- END MODIFIED ---

        # --- Add default num_ctx if not provided ---
        if "num_ctx" not in valid_options and "num_ctx" not in kwargs:
            valid_options["num_ctx"] = 8192
            logger.debug("Added default context size 'num_ctx: 8192' to Ollama options.")
        # --- End add default num_ctx ---

        # --- Add default stop token if not provided ---
        # Check if 'stop' is already in valid_options or was passed in kwargs but filtered out (e.g., None)
        if "stop" not in valid_options and "stop" not in kwargs:
            # Add a common Llama 3 stop token if none specified
            valid_options["stop"] = ["<|eot_id|>"]
            logger.debug("Added default stop token '<|eot_id|>' to Ollama options.")
        # --- End add default stop token ---

        ignored_options = {k: v for k, v in raw_options.items() if k not in KNOWN_OLLAMA_OPTIONS and k != "stop"} # Adjust ignored check
        if ignored_options: logger.warning(f"OllamaProvider ignoring unknown options: {ignored_options}")

        payload = { "model": model, "messages": messages, "stream": self.streaming_mode, "options": valid_options } # Pass options under 'options' key

        mode_log = "Streaming" if self.streaming_mode else "Non-Streaming"
        options_log = valid_options # Log only valid options sent
        logger.info(f"OllamaProvider preparing request ({mode_log}). Model: {model}, Endpoint: {chat_endpoint}, Options: {options_log}.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' ({mode_log})..."}

        last_exception = None
        response: Optional[aiohttp.ClientResponse] = None

        # --- Retry Loop for Initial API Request ---
        try: # Wrap the entire request/retry/stream logic to ensure session closure
            for attempt in range(MAX_RETRIES + 1):
                last_exception = None
                response = None
                try:
                    logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                    response = await session.post(chat_endpoint, json=payload)

                    req_info = response.request_info
                    logger.info(f"Request Headers Sent: {dict(req_info.headers)}")
                    logger.info(f"Response Status: {response.status}, Reason: {response.reason}")
                    logger.info(f"Response Headers Received: {dict(response.headers)}")
                    response_status = response.status

                    if response_status >= 400:
                        response_text = await self._read_response_safe(response)
                        logger.debug(f"Ollama API error status {response_status}. Body: {response_text[:500]}...")
                        if response_status in RETRYABLE_STATUS_CODES or response_status >= 500:
                            last_exception = aiohttp.ClientResponseError(req_info, response.history, status=response_status, message=f"Status {response_status}", headers=response.headers)
                            logger.warning(f"Ollama API Error attempt {attempt + 1}: Status {response_status}, Resp: {response_text[:200]}...")
                            if attempt < MAX_RETRIES:
                                logger.info(f"Status {response_status} retryable. Wait {RETRY_DELAY_SECONDS}s...")
                                await asyncio.sleep(RETRY_DELAY_SECONDS)
                                continue
                            else:
                                logger.error(f"Max retries ({MAX_RETRIES}) after status {response_status}.")
                                yield {"type": "error", "content": f"[Ollama Error]: Max retries. Last: Status {response_status} - {response_text[:100]}", "_exception_obj": last_exception}
                                return
                        else: # Non-retryable 4xx
                            client_err = aiohttp.ClientResponseError(req_info, response.history, status=response_status, message=f"Status {response_status}", headers=response.headers)
                            logger.error(f"Ollama API Client Error: Status {response_status}, Resp: {response_text[:200]}")
                            yield {"type": "error", "content": f"[Ollama Error]: Client Error {response_status} - {response_text[:100]}", "_exception_obj": client_err}
                            return
                    else: # Status 2xx
                        logger.info(f"API call headers OK (Status {response_status}) attempt {attempt + 1}. Start stream.")
                        break

                except RETRYABLE_AIOHTTP_EXCEPTIONS as e:
                    last_exception = e
                    logger.warning(f"Retryable aiohttp error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                        logger.info(f"Waiting {RETRY_DELAY_SECONDS}s...")
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        # Need to recreate session on connection errors when using per-request sessions
                        if session and not session.closed: await session.close()
                        session = await self._create_request_session()
                        continue
                    else:
                        logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}.")
                        yield {"type": "error", "content": f"[Ollama Error]: Max retries after connection/timeout. Last: {e}", "_exception_obj": e}
                        return
                except Exception as e:
                    last_exception = e
                    logger.exception(f"Unexpected Error during API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                         logger.info(f"Waiting {RETRY_DELAY_SECONDS}s...")
                         await asyncio.sleep(RETRY_DELAY_SECONDS)
                         # Need to recreate session on errors when using per-request sessions
                         if session and not session.closed: await session.close()
                         session = await self._create_request_session()
                         continue
                    else:
                         logger.error(f"Max retries ({MAX_RETRIES}) after unexpected error.")
                         yield {"type": "error", "content": f"[Ollama Error]: Unexpected Error after retries - {type(e).__name__}", "_exception_obj": e}
                         return

            if response is None or response.status >= 400: # Should not happen if logic above is correct, but check
                logger.error(f"Ollama API request failed. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
                err_content = f"[Ollama Error]: API request failed after {MAX_RETRIES} retries. Error: {type(last_exception).__name__ if last_exception else 'Request Failed'}"
                yield {"type": "error", "content": err_content, "_exception_obj": last_exception}
                return

            # --- Process the Successful Response Stream ---
            byte_buffer = b""
            processed_lines = 0
            stream_error_occurred = False
            stream_error_obj = None
            try:
                if self.streaming_mode:
                    logger.debug("Starting streaming using response.content.iter_any()")
                    async for chunk in response.content.iter_any():
                        if not chunk: continue
                        byte_buffer += chunk
                        while b'\n' in byte_buffer:
                            line_bytes, byte_buffer = byte_buffer.split(b'\n', 1)
                            line = line_bytes.decode('utf-8').strip()
                            if not line:
                                logger.warning("OllamaProvider: Received empty line from stream.")
                                continue
                            processed_lines += 1
                            try:
                                chunk_data = json.loads(line)
                                logger.debug(f"OllamaProvider: Processing chunk_data: {chunk_data}")
                                if chunk_data.get("error"):
                                    error_msg = chunk_data["error"]
                                    logger.error(f"Ollama stream error: {error_msg}")
                                    stream_error_obj = ValueError(f"[Ollama Error]: {error_msg}") # Treat as ValueError
                                    yield {"type": "error", "content": f"[Ollama Error]: {error_msg}", "_exception_obj": stream_error_obj}
                                    stream_error_occurred = True
                                    # Don't return immediately, maybe more data follows? Or maybe we should? Let's try continuing for now.
                                    # return # Exit on error

                                # Check for content even if error occurred? Maybe not.
                                if not stream_error_occurred:
                                     if content_chunk := chunk_data.get("message", {}).get("content"):
                                         logger.debug(f"OllamaProvider: Yielding response_chunk: {content_chunk[:100]}...")
                                         yield {"type": "response_chunk", "content": content_chunk}
                                     else:
                                         logger.warning("OllamaProvider: No content found in chunk_data.")

                                if chunk_data.get("done", False):
                                    total_duration = chunk_data.get("total_duration")
                                    logger.debug(f"Received done=true. Total duration: {total_duration}ns")
                                    if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)"}
                                    # Check for done *after* processing potential content in the same chunk
                                    if chunk_data.get("done", False):
                                        total_duration = chunk_data.get("total_duration")
                                        logger.debug(f"Received done=true. Total duration: {total_duration}ns")
                                        if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)"}
                                        stream_error_occurred = False # Reset error if we successfully reach done
                                        return # Exit cleanly on done

                            except json.JSONDecodeError as e:
                                logger.error(f"JSONDecodeError: {e} - Line: {line[:200]}")
                                stream_error_obj = ValueError(f"Invalid JSON response: {line[:100]}")
                                yield {"type": "error", "content": f"Invalid JSON response: {line[:100]}", "_exception_obj": stream_error_obj}
                                stream_error_occurred = True
                                # Continue processing stream? Or break? Let's continue for now.
                                # break # Exit on error
                            except Exception as e:
                                logger.error(f"Stream processing error: {str(e)}")
                                stream_error_obj = e
                                yield {"type": "error", "content": f"Stream error: {str(e)}", "_exception_obj": e}
                                stream_error_occurred = True
                                # Continue processing stream? Or break? Let's continue for now.
                                # break # Exit on error

                    logger.debug(f"Finished streaming loop (iter_any). Processed lines: {processed_lines}. Error occurred: {stream_error_occurred}")
                    # Process final buffer only if no stream error occurred previously
                    if byte_buffer.strip() and not stream_error_occurred:
                        logger.warning(f"Processing remaining buffer after loop: {byte_buffer.decode('utf-8', errors='ignore')[:200]}...")
                        line = byte_buffer.decode('utf-8', errors='ignore').strip()
                        if line: # Ensure line is not empty after decode/strip
                            try:
                                chunk_data = json.loads(line)
                                # Check for content first
                                if content_chunk := chunk_data.get("message", {}).get("content"):
                                    logger.debug("Yielding final content chunk from buffer.")
                                    yield {"type": "response_chunk", "content": content_chunk}
                                # Then check for done
                                if chunk_data.get("done", False):
                                    logger.debug("Processed final 'done' from remaining buffer.")
                                    total_duration = chunk_data.get("total_duration")
                                    if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)"}
                                else:
                                    logger.warning("Final buffer chunk did not contain 'done': True.")
                            except json.JSONDecodeError as final_e:
                                logger.error(f"Could not parse final buffer as JSON: {final_e}")
                                # Don't yield error here, just log, as we might have already yielded content
                            except Exception as final_e:
                                logger.error(f"Unexpected error processing final buffer: {final_e}")
                                # Don't yield error here

                else: # Non-Streaming (logic same as before)
                    logger.debug("Processing non-streaming response...")
                    response_data_text = ""
                    try:
                         response_data_text = await response.text()
                         response_data = json.loads(response_data_text)
                         if response_data.get("error"):
                             error_msg = response_data["error"]
                             logger.error(f"Ollama non-streaming error: {error_msg}")
                             stream_error_obj = ValueError(f"[Ollama Error]: {error_msg}")
                             yield {"type": "error", "content": f"[Ollama Error]: {error_msg}", "_exception_obj": stream_error_obj}
                         elif response_data.get("message") and isinstance(response_data["message"], dict):
                             full_content = response_data["message"].get("content");
                             if full_content: logger.info(f"Non-streaming len: {len(full_content)}"); yield {"type": "response_chunk", "content": full_content}
                             else: logger.warning("Non-streaming message content empty.")
                             if response_data.get("done", False): logger.debug("Non-streaming done=true.")
                             else: logger.warning("Non-streaming missing done=true.")
                         else:
                             logger.error(f"Unexpected non-streaming structure: {response_data}"); yield {"type": "error", "content": "[Ollama Error]: Unexpected non-streaming structure."}
                    except json.JSONDecodeError:
                         logger.error(f"Failed non-streaming JSON decode. Raw: {response_data_text[:500]}...")
                         stream_error_obj = ValueError("Failed non-streaming decode.")
                         yield {"type": "error", "content": "[Ollama Error]: Failed non-streaming decode.", "_exception_obj": stream_error_obj}
                    except Exception as e:
                         logger.error(f"Error processing non-streaming: {e}", exc_info=True)
                         stream_error_obj = e
                         yield {"type": "error", "content": f"[Ollama Error]: Non-streaming processing error - {type(e).__name__}", "_exception_obj": e}


            except aiohttp.ClientPayloadError as payload_err: # Specific error for payload issues during streaming
                logger.error(f"Ollama processing failed with ClientPayloadError: {payload_err}", exc_info=True)
                stream_error_obj = payload_err # This seems to be the error logged
                yield {"type": "error", "content": f"[Ollama Error]: Connection closed unexpectedly during stream - {payload_err}", "_exception_obj": payload_err}
                stream_error_occurred = True
            except aiohttp.ClientConnectionError as conn_err: # Catch connection errors during streaming
                 logger.error(f"Ollama processing failed with ClientConnectionError: {conn_err}", exc_info=True)
                 stream_error_obj = conn_err
                 yield {"type": "error", "content": f"[Ollama Error]: Network error during stream - {conn_err}", "_exception_obj": conn_err}
                 stream_error_occurred = True
            except asyncio.TimeoutError as timeout_err: # Catch timeouts during streaming
                read_timeout = getattr(session.timeout, 'sock_read', 'N/A')
                logger.error(f"Ollama timeout during stream read (read={read_timeout}s): {timeout_err}", exc_info=False)
                stream_error_obj = timeout_err
                yield {"type": "error", "content": f"[Ollama Error]: Timeout waiting for stream data (read={read_timeout}s)", "_exception_obj": timeout_err}
                stream_error_occurred = True
            except Exception as e:
                logger.exception(f"Unexpected Error processing Ollama response stream: {type(e).__name__} - {e}")
                stream_error_obj = e
                yield {"type": "error", "content": f"[Ollama Error]: Unexpected stream processing error - {type(e).__name__}", "_exception_obj": e}
                stream_error_occurred = True
            finally:
                # Ensure the response connection is released even if iteration failed
                if response and not response.closed:
                    response.release()

            if not stream_error_occurred:
                logger.info(f"OllamaProvider: stream_completion finished cleanly for model {model}.")
            else:
                logger.warning(f"OllamaProvider: stream_completion finished for model {model}, but error encountered.")

        finally:
            # Ensure the session is always closed after the request attempt/stream processing is done
            if session and not session.closed:
                await session.close()
                logger.debug("OllamaProvider: Closed per-request aiohttp ClientSession.")

    async def _read_response_safe(self, response: aiohttp.ClientResponse) -> str:
        """Safely reads response text, handling potential errors."""
        try:
            return await response.text()
        except Exception as read_err:
            logger.warning(f"Could not read response body for status {response.status}: {read_err}")
            return f"(Read err: {read_err})"


    def __repr__(self) -> str:
        mode = "streaming" # Always streaming now
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', mode='{mode}')>"

    async def __aenter__(self):
        # Nothing to do here as session is per-request
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Nothing to do here as session is per-request
        pass
