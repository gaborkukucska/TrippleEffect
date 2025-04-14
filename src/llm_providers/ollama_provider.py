# START OF FILE src/llm_providers/ollama_provider.py
import httpx
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
RETRYABLE_HTTPX_EXCEPTIONS = (
    httpx.NetworkError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)
RETRYABLE_STATUS_CODES = [429, 500, 502, 503, 504]

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 300.0
DEFAULT_TOTAL_TIMEOUT = 600.0

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
    LLM Provider implementation for local Ollama models using httpx.
    Handles streaming by reading raw bytes and splitting by newline.
    Improved handling of stream end.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        # (init remains the same)
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key: logger.warning("OllamaProvider Warning: API key provided but not used.")
        self.streaming_mode = kwargs.pop('stream', True)
        self._session_timeout_config = kwargs.pop('timeout', None)
        self._client_kwargs = kwargs
        self._client: Optional[httpx.AsyncClient] = None
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider initialized with httpx. Base URL: {self.base_url}. Mode: {mode_str}.")

    async def _get_client(self) -> httpx.AsyncClient:
        # (remains the same - using HTTP/1.1 transport)
        if self._client is None or self._client.is_closed:
            if isinstance(self._session_timeout_config, httpx.Timeout):
                timeout = self._session_timeout_config
            elif isinstance(self._session_timeout_config, (int, float)):
                timeout_total = float(self._session_timeout_config)
                timeout = httpx.Timeout(timeout_total, connect=DEFAULT_CONNECT_TIMEOUT, read=DEFAULT_READ_TIMEOUT)
                logger.warning(f"Using provided single timeout value ({timeout.total}s) for total, explicit defaults for connect/read.")
            else:
                timeout = httpx.Timeout(DEFAULT_TOTAL_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT, read=DEFAULT_READ_TIMEOUT)
                if self._session_timeout_config is not None: logger.warning(f"Invalid timeout config '{self._session_timeout_config}', using defaults.")

            transport = httpx.AsyncHTTPTransport(http1=True, retries=0)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout,
                transport=transport,
                http1=True, http2=False,
                headers={ 'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json' },
                **self._client_kwargs
            )
            logger.info(f"OllamaProvider: Created new httpx client (Forced HTTP/1.1 via Transport, Minimal Headers). Timeout: {timeout}")
        return self._client

    async def close_session(self):
         # (remains the same)
        if self._client and not self._client.is_closed:
            await self._client.aclose(); self._client = None; logger.info("OllamaProvider: Closed httpx client.")

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

        client = await self._get_client()
        chat_endpoint = "/api/chat"

        if tools or tool_choice: logger.warning(f"OllamaProvider ignoring tools/tool_choice.")

        # Filter options (Unchanged)
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

        last_exception = None
        response: Optional[httpx.Response] = None
        stream_context = None

        # --- Retry Loop for Initial API Request (Unchanged) ---
        for attempt in range(MAX_RETRIES + 1):
            # (Retry logic remains the same...)
            last_exception = None; response = None; stream_context = None
            try:
                 logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                 stream_context = client.stream("POST", chat_endpoint, json=payload)
                 async with stream_context as resp:
                    response_status = resp.status_code
                    if response_status >= 400:
                         # ... (Error handling for non-200 status remains the same) ...
                         response_text = "";
                         try: error_bytes = await resp.aread(); response_text = error_bytes.decode('utf-8', errors='ignore')
                         except Exception as read_err: logger.warning(f"Could not read error body status {response_status}: {read_err}"); response_text = f"(Read err: {read_err})"
                         logger.debug(f"Ollama API error status {response_status}. Body: {response_text[:500]}...")
                         if response_status in RETRYABLE_STATUS_CODES or response_status >= 500:
                             last_exception = httpx.HTTPStatusError(f"Status {response_status}", request=resp.request, response=resp)
                             logger.warning(f"Ollama API Error attempt {attempt + 1}: Status {response_status}, Resp: {response_text[:200]}...")
                             if attempt < MAX_RETRIES: logger.info(f"Status {response_status} retryable. Wait {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                             else: logger.error(f"Max retries ({MAX_RETRIES}) after status {response_status}."); yield {"type": "error", "content": f"[Ollama Error]: Max retries. Last: Status {response_status} - {response_text[:100]}"}; return
                         else: # Non-retryable 4xx
                             logger.error(f"Ollama API Client Error: Status {response_status}, Resp: {response_text[:200]}"); yield {"type": "error", "content": f"[Ollama Error]: Client Error {response_status} - {response_text[:100]}"}; return
                    else: # Status 200
                        logger.info(f"API call headers OK (Status 200) attempt {attempt + 1}. Start stream.")
                        response = resp; break # Exit retry loop successfully
            except RETRYABLE_HTTPX_EXCEPTIONS as e:
                # ... (Retryable exception handling remains the same) ...
                last_exception = e; logger.warning(f"Retryable httpx error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) after {type(e).__name__}."); yield {"type": "error", "content": f"[Ollama Error]: Max retries after connection/timeout. Last: {e}"}; return
            except Exception as e:
                # ... (Unexpected exception handling remains the same) ...
                last_exception = e; logger.exception(f"Unexpected Error API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) after unexpected error."); yield {"type": "error", "content": f"[Ollama Error]: Unexpected Error after retries - {type(e).__name__}"}; return
            finally:
                 if response is None and stream_context is not None and hasattr(stream_context, 'aclose'): await stream_context.aclose()


        if response is None: # Check if request failed after all retries
             logger.error(f"Ollama API request failed. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
             err_content = f"[Ollama Error]: API request failed after {MAX_RETRIES} retries. Error: {type(last_exception).__name__ if last_exception else 'Request Failed'}"
             yield {"type": "error", "content": err_content}; return

        # --- Process the Successful Response Stream ---
        byte_buffer = b""
        processed_lines = 0
        stream_error_occurred = False
        # *** Outer try block to catch errors during the entire stream iteration ***
        try:
            if self.streaming_mode:
                logger.debug("Starting streaming loop using response.aiter_raw()...")
                async for chunk in response.aiter_raw():
                    if not chunk: continue
                    byte_buffer += chunk
                    while True: # Process complete lines
                        newline_pos = byte_buffer.find(b'\n')
                        if newline_pos == -1: break
                        json_line = byte_buffer[:newline_pos]; byte_buffer = byte_buffer[newline_pos + 1:]
                        if not json_line.strip(): continue
                        processed_lines += 1; decoded_line = ""
                        # *** Inner try specifically for JSON parsing of a line ***
                        try:
                            decoded_line = json_line.decode('utf-8'); chunk_data = json.loads(decoded_line)
                            if chunk_data.get("error"): error_msg = chunk_data["error"]; logger.error(f"Ollama stream error: {error_msg}"); yield {"type": "error", "content": f"[Ollama Error]: {error_msg}"}; stream_error_occurred = True; break
                            message_chunk = chunk_data.get("message");
                            if message_chunk and isinstance(message_chunk, dict): content_chunk = message_chunk.get("content");
                            if content_chunk: yield {"type": "response_chunk", "content": content_chunk}
                            if chunk_data.get("done", False): logger.debug(f"Received done=true. Line: {processed_lines}"); total_duration = chunk_data.get("total_duration");
                            if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                        except json.JSONDecodeError: logger.error(f"JSONDecodeError line {processed_lines}: {decoded_line[:500]}..."); yield {"type": "error", "content": "[Ollama Error]: Failed stream JSON decode."}; stream_error_occurred = True; break
                        # *** Removed general Exception catch here to let outer catch handle stream errors ***
                    if stream_error_occurred: break # Break outer async for

                logger.debug(f"Finished streaming loop (aiter_raw). Lines: {processed_lines}. Error: {stream_error_occurred}")
                if stream_error_occurred: return # Exit if error during line processing

                # Process final buffer part (Unchanged)
                if byte_buffer.strip():
                     logger.warning(f"Processing remaining buffer: {byte_buffer[:200]}...")
                     try: chunk_data = json.loads(byte_buffer.decode('utf-8'));
                     if chunk_data.get("done", False): logger.debug("Processed final 'done'.")
                     elif chunk_data.get("message", {}).get("content"): logger.warning("Final buffer has msg content."); yield {"type": "response_chunk", "content": chunk_data["message"]["content"]}
                     else: logger.warning("Final buffer not 'done' or message.")
                     except Exception as final_e: logger.error(f"Could not parse final buffer: {final_e}")

            else: # Non-Streaming (Unchanged)
                 logger.debug("Processing non-streaming response...")
                 try:
                     # Ensure response is fully read before context manager exits
                     await response.aread() # Read the full response body
                     response_data = json.loads(response.text) # Parse from response.text

                     if response_data.get("error"): error_msg = response_data["error"]; logger.error(f"Ollama non-streaming error: {error_msg}"); yield {"type": "error", "content": f"[Ollama Error]: {error_msg}"}
                     elif response_data.get("message") and isinstance(response_data["message"], dict):
                         full_content = response_data["message"].get("content");
                         if full_content: logger.info(f"Non-streaming len: {len(full_content)}"); yield {"type": "response_chunk", "content": full_content}
                         else: logger.warning("Non-streaming message content empty.")
                         if response_data.get("done", False): total_duration = response_data.get("total_duration");
                         if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                         else: logger.warning("Non-streaming missing done=true.")
                     else: logger.error(f"Unexpected non-streaming structure: {response_data}"); yield {"type": "error", "content": "[Ollama Error]: Unexpected non-streaming structure."}
                 except json.JSONDecodeError: logger.error(f"Failed non-streaming JSON decode. Raw: {response.text[:500]}..."); yield {"type": "error", "content": "[Ollama Error]: Failed non-streaming decode."}
                 except Exception as e: logger.error(f"Error processing non-streaming: {e}", exc_info=True); yield {"type": "error", "content": f"[Ollama Error]: Non-streaming processing error - {type(e).__name__}"}

        # --- Catch exceptions DURING the entire stream processing ---
        # Treat StreamClosed as potentially recoverable by retry/failover mechanism outside
        except httpx.StreamClosed as stream_closed_err:
             logger.error(f"Ollama httpx stream closed unexpectedly: {stream_closed_err}", exc_info=True)
             yield {"type": "error", "content": f"[Ollama Error]: Stream closed unexpectedly - {stream_closed_err}"}
             stream_error_occurred = True # Mark error occurred
        except httpx.ReadTimeout as timeout_err:
             logger.error(f"Ollama httpx timeout during stream read (read={client.timeout.read}s): {timeout_err}", exc_info=False)
             yield {"type": "error", "content": f"[Ollama Error]: Timeout waiting for stream data (read={client.timeout.read}s)"}
             stream_error_occurred = True
        except httpx.RemoteProtocolError as proto_err:
             logger.error(f"Ollama processing failed with RemoteProtocolError: {proto_err}", exc_info=True)
             yield {"type": "error", "content": f"[Ollama Error]: Connection closed unexpectedly - {proto_err}"}
             stream_error_occurred = True
        except httpx.NetworkError as net_err:
             logger.error(f"Ollama processing failed with NetworkError: {net_err}", exc_info=True)
             yield {"type": "error", "content": f"[Ollama Error]: Network error during stream - {net_err}"}
             stream_error_occurred = True
        except Exception as e:
             logger.exception(f"Unexpected Error processing Ollama response stream: {type(e).__name__} - {e}")
             yield {"type": "error", "content": f"[Ollama Error]: Unexpected stream processing error - {type(e).__name__}"}
             stream_error_occurred = True
        # --- End stream processing error handling ---

        # Log final completion status
        if not stream_error_occurred: logger.info(f"OllamaProvider: stream_completion finished cleanly for model {model}.")
        else: logger.warning(f"OllamaProvider: stream_completion finished for model {model}, but error encountered.")


    def __repr__(self) -> str:
        # (Unchanged)
        client_status = "closed" if self._client is None or self._client.is_closed else "open"
        mode = "streaming" if self.streaming_mode else "non-streaming"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client='{client_status}', mode='{mode}')>"

    async def __aenter__(self):
        # (Unchanged)
        await self._get_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # (Unchanged)
        await self.close_session()
