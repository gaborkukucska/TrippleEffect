# START OF FILE src/llm_providers/ollama_provider.py
import httpx # Import httpx
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
# Define retryable exceptions for httpx (adjust as needed)
RETRYABLE_HTTPX_EXCEPTIONS = (
    httpx.NetworkError, # Covers connection errors, DNS errors etc.
    httpx.TimeoutException, # Covers connect, read, write, pool timeouts
    httpx.ReadError, # Specific read errors
)
# Retry specifically on 5xx status codes from httpx response
RETRYABLE_STATUS_CODES = [500, 502, 503, 504]


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
# HTTPX uses a Timeout object or float for total timeout
DEFAULT_TOTAL_TIMEOUT_HTTPX = 600.0 # seconds for the entire request/response cycle (10 mins)
# httpx also supports connect, read, write, pool timeouts within the Timeout object
DEFAULT_CONNECT_TIMEOUT_HTTPX = 15.0
DEFAULT_READ_TIMEOUT_HTTPX = 300.0 # Timeout waiting for data chunk (5 mins)

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
    LLM Provider implementation for local Ollama models.
    Uses httpx for async HTTP requests and streaming.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the Ollama provider with httpx client. """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key:
            logger.warning("OllamaProvider Warning: API key provided but not used.")

        self.streaming_mode = kwargs.pop('stream', True)

        # Store timeout config if provided via kwargs
        self._timeout_config = kwargs.pop('timeout', None)

        # Store remaining kwargs for potential httpx client config (less common than aiohttp)
        self._client_kwargs = kwargs
        self._client: Optional[httpx.AsyncClient] = None # httpx client instance
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider initialized. Base URL: {self.base_url}. Mode: {mode_str}. Using httpx client.")

    def _get_timeout_config(self) -> httpx.Timeout:
        """ Creates httpx Timeout object based on configuration. """
        if isinstance(self._timeout_config, httpx.Timeout):
            return self._timeout_config
        elif isinstance(self._timeout_config, (int, float)):
            timeout_total = float(self._timeout_config)
            logger.warning(f"Using provided single timeout value ({timeout_total}s) for total, applying defaults for others.")
            return httpx.Timeout(
                timeout_total, # Default for all if only one value given
                connect=DEFAULT_CONNECT_TIMEOUT_HTTPX,
                read=DEFAULT_READ_TIMEOUT_HTTPX
            )
        else:
            # Default timeout config
            timeout = httpx.Timeout(
                DEFAULT_TOTAL_TIMEOUT_HTTPX,
                connect=DEFAULT_CONNECT_TIMEOUT_HTTPX,
                read=DEFAULT_READ_TIMEOUT_HTTPX
            )
            if self._timeout_config is not None:
                logger.warning(f"Invalid httpx timeout config '{self._timeout_config}', using defaults.")
            return timeout

    async def _get_client(self) -> httpx.AsyncClient:
        """ Creates or returns an existing httpx AsyncClient. """
        if self._client is None:
            timeout = self._get_timeout_config()
            # Configure httpx client (can add headers, http versions etc. here if needed)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout,
                http1=True, # Explicitly use HTTP/1.1 (default usually fine)
                http2=False, # Disable HTTP/2 for simplicity with local server
                # Can add default headers via self._client_kwargs if necessary
                **self._client_kwargs
            )
            logger.info(f"OllamaProvider: Created new httpx AsyncClient with timeout: {timeout}")
        return self._client

    async def close_session(self):
        """ Closes the httpx client if it exists. """
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("OllamaProvider: Closed httpx client.")

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
        """ Gets completion from Ollama using httpx. """
        client = await self._get_client()
        chat_endpoint = "/api/chat" # Relative to base_url

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

        last_exception = None
        request = client.build_request("POST", chat_endpoint, json=payload)

        # Retry Loop for API Call
        for attempt in range(MAX_RETRIES + 1):
            response: Optional[httpx.Response] = None
            try:
                 logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                 # Stream the request using httpx
                 async with client.stream("POST", chat_endpoint, json=payload) as resp:
                     response = resp # Store response metadata
                     # Check status code immediately after headers are received
                     response.raise_for_status() # Raises HTTPStatusError for 4xx/5xx
                     logger.info(f"API call successful (Status {response.status_code}) on attempt {attempt + 1}.")
                     last_exception = None

                     # --- Process Stream/Response ---
                     byte_buffer = b""
                     processed_lines = 0
                     try:
                         if self.streaming_mode:
                             logger.debug("Starting httpx streaming response processing...")
                             async for chunk in response.aiter_bytes(): # Read bytes
                                 if not chunk: continue
                                 # logger.log(logging.DEBUG - 1, f"Httpx Received chunk (bytes): {chunk[:100]}...")
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
                                         # logger.log(logging.DEBUG - 1, f"Httpx Parsing line {processed_lines}: {decoded_line[:200]}...")
                                         chunk_data = json.loads(decoded_line)

                                         # Process parsed object
                                         if chunk_data.get("error"):
                                             error_msg = chunk_data["error"]; logger.error(f"Ollama stream error: {error_msg}"); yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}; return
                                         message_chunk = chunk_data.get("message");
                                         if message_chunk and isinstance(message_chunk, dict):
                                             content_chunk = message_chunk.get("content");
                                             if content_chunk: yield {"type": "response_chunk", "content": content_chunk}
                                         if chunk_data.get("done", False):
                                             logger.debug(f"Httpx Received done=true. Line: {processed_lines}")
                                             total_duration = chunk_data.get("total_duration");
                                             if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}

                                     except json.JSONDecodeError:
                                         logger.error(f"Httpx JSONDecodeError line {processed_lines}: {decoded_line[:500]}..."); yield {"type": "error", "content": "[OllamaProvider Error]: Failed stream JSON decode."}; return
                                     except Exception as e:
                                         logger.error(f"Httpx Error processing stream line {processed_lines}: {e}", exc_info=True); logger.error(f"Problem line: {decoded_line[:500]}"); yield {"type": "error", "content": f"[OllamaProvider Error]: Stream processing error - {type(e).__name__}"}; return

                             logger.debug(f"Httpx Finished streaming loop. Processed {processed_lines} lines.")
                             if byte_buffer.strip(): # Process final part
                                 logger.warning(f"Httpx Processing remaining buffer: {byte_buffer[:200]}...")
                                 try:
                                     chunk_data = json.loads(byte_buffer.decode('utf-8'))
                                     # Process final object...
                                     if chunk_data.get("done", False): logger.debug("Processed final 'done' from buffer.")
                                 except Exception as final_e: logger.error(f"Could not parse final buffer: {final_e}")

                         else: # Non-Streaming with httpx
                             logger.debug("Processing non-streaming httpx response...")
                             response_data = await response.aread() # Read entire body
                             try:
                                 json_data = json.loads(response_data.decode('utf-8'))
                                 if json_data.get("error"): error_msg = json_data["error"]; logger.error(f"Ollama non-streaming error: {error_msg}"); yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}
                                 elif json_data.get("message") and isinstance(json_data["message"], dict):
                                     full_content = json_data["message"].get("content");
                                     if full_content: logger.info(f"Non-streaming response len: {len(full_content)}"); yield {"type": "response_chunk", "content": full_content}
                                     else: logger.warning("Non-streaming message content empty.")
                                     if json_data.get("done", False): total_duration = json_data.get("total_duration");
                                     if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                                     else: logger.warning("Non-streaming response missing done=true.")
                                 else: logger.error(f"Unexpected non-streaming structure: {json_data}"); yield {"type": "error", "content": "[OllamaProvider Error]: Unexpected non-streaming structure."}
                             except json.JSONDecodeError:
                                  logger.error(f"Failed non-streaming JSON decode. Raw: {response_data[:500]}..."); yield {"type": "error", "content": "[OllamaProvider Error]: Failed non-streaming decode."}
                             except Exception as e:
                                  logger.error(f"Error processing non-streaming: {e}", exc_info=True); yield {"type": "error", "content": f"[OllamaProvider Error]: Non-streaming processing error - {type(e).__name__}"}

                     # If we successfully processed the stream/response, exit the retry loop
                     break

            # Handle httpx specific errors for retry logic
            except httpx.HTTPStatusError as e: # Handles 4xx/5xx
                 last_exception = e
                 logger.warning(f"HTTP Status Error attempt {attempt + 1}/{MAX_RETRIES + 1}: Status {e.response.status_code}")
                 if e.response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                     logger.info(f"Status {e.response.status_code} is retryable. Waiting {RETRY_DELAY_SECONDS}s...")
                     await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                 else:
                     response_text = e.response.text[:200] if hasattr(e.response, 'text') else "N/A"
                     logger.error(f"Non-retryable status {e.response.status_code} or max retries reached. Body: {response_text}")
                     yield {"type": "error", "content": f"[OllamaProvider Error]: HTTP Error {e.response.status_code} - {response_text}"}; return
            except RETRYABLE_HTTPX_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable httpx error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}."); yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries after connection/timeout. Last: {type(e).__name__}"}; return
            except Exception as e: # Catch other errors during request/stream init
                last_exception = e; logger.exception(f"Unexpected Error during Ollama API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error."); yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected Error after retries - {type(e).__name__}"}; return

        # If loop finishes without success (shouldn't happen if errors are handled correctly)
        if last_exception:
            logger.error(f"Ollama API call failed definitively after retries. Last exception: {type(last_exception).__name__}")
            yield {"type": "error", "content": f"[OllamaProvider Error]: API call failed after {MAX_RETRIES} retries. Last error: {type(last_exception).__name__}"}

        logger.info(f"OllamaProvider: stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        client_status = "initialized" if self._client else "uninitialized"
        mode = "streaming" if self.streaming_mode else "non-streaming"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client='{client_status}', mode='{mode}')>"

    async def __aenter__(self):
        await self._get_client() # Ensure client is created
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
