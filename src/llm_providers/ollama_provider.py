# START OF FILE src/llm_providers/ollama_provider.py
import httpx # Use httpx
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
# Define retryable exceptions for httpx
RETRYABLE_HTTPX_EXCEPTIONS = (
    httpx.NetworkError, # Includes connection errors, DNS errors etc.
    httpx.TimeoutException,
    httpx.RemoteProtocolError, # Can sometimes indicate temporary server issues
)
# Specific status codes to retry (usually server-side transient issues)
RETRYABLE_STATUS_CODES = [
    429, # Rate Limit
    500, # Internal Server Error (Maybe temporary?)
    502, # Bad Gateway
    503, # Service Unavailable
    504, # Gateway Timeout
]


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
# httpx uses a Timeout object or float for total timeout
DEFAULT_CONNECT_TIMEOUT = 15.0 # seconds
DEFAULT_READ_TIMEOUT = 300.0 # seconds (for reading response data) - Long timeout for stream
DEFAULT_TOTAL_TIMEOUT = 600.0 # seconds (overall)

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
    LLM Provider implementation for local Ollama models using httpx with retries.
    Handles streaming by iterating over lines.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the Ollama provider using httpx. """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key: logger.warning("OllamaProvider Warning: API key provided but not used.")

        self.streaming_mode = kwargs.pop('stream', True)
        self._client_kwargs = kwargs # Store remaining kwargs for client init

        # Configure httpx timeouts
        connect_timeout = kwargs.pop('connect_timeout', DEFAULT_CONNECT_TIMEOUT)
        read_timeout = kwargs.pop('read_timeout', DEFAULT_READ_TIMEOUT)
        total_timeout = kwargs.pop('timeout', DEFAULT_TOTAL_TIMEOUT) # Allow overriding total
        self._timeout = httpx.Timeout(total_timeout, connect=connect_timeout, read=read_timeout)

        self._client: Optional[httpx.AsyncClient] = None
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.info(f"OllamaProvider initialized with httpx. Base URL: {self.base_url}. Mode: {mode_str}.")

    async def _get_client(self) -> httpx.AsyncClient:
        """Creates or returns an existing httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            # Enable HTTP/2 if available and potentially beneficial (optional)
            # http2_support = self._client_kwargs.pop('http2', True)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                # http2=http2_support, # Enable HTTP/2?
                **self._client_kwargs
                )
            logger.info(f"OllamaProvider: Created new httpx client. Timeout: {self._timeout}")
        return self._client

    async def close_session(self):
        """Closes the httpx client if it exists."""
        if self._client and not self._client.is_closed:
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
        """
        Gets completion from Ollama using httpx. Handles streaming line by line.
        """
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
        response: Optional[httpx.Response] = None

        # --- Retry Loop for Initial API Request ---
        for attempt in range(MAX_RETRIES + 1):
            last_exception = None # Reset exception for this attempt
            try:
                logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                # Use context manager for streaming request
                async with client.stream("POST", chat_endpoint, json=payload) as resp:
                    # Check status code immediately after headers are received
                    response_status = resp.status_code
                    response_text = ""
                    if response_status >= 400:
                        # Read error body (this might consume the stream)
                        try:
                            response_text = await resp.aread() # Read the full body for error
                            response_text = response_text.decode('utf-8', errors='ignore')
                        except Exception as read_err:
                            logger.warning(f"Could not read error body for status {response_status}: {read_err}")
                            response_text = f"(Could not read error body: {read_err})"
                        logger.debug(f"Ollama API returned error status {response_status}. Body: {response_text[:500]}...")

                        if response_status in RETRYABLE_STATUS_CODES or response_status >= 500:
                            last_exception = httpx.HTTPStatusError(f"Status {response_status}", request=resp.request, response=resp) # Create an exception
                            logger.warning(f"Ollama API Error attempt {attempt + 1}: Status {response_status}, Resp: {response_text[:200]}...")
                            if attempt < MAX_RETRIES: logger.info(f"Status {response_status} retryable. Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                            else: logger.error(f"Max retries ({MAX_RETRIES}) after status {response_status}."); yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries. Last: Status {response_status} - {response_text[:100]}"}; return
                        else: # Non-retryable 4xx
                            logger.error(f"Ollama API Client Error: Status {response_status}, Resp: {response_text[:200]}"); yield {"type": "error", "content": f"[OllamaProvider Error]: Client Error {response_status} - {response_text[:100]}"}; return

                    # --- SUCCESS (Status 200) ---
                    logger.info(f"API call successful (Status 200) on attempt {attempt + 1}. Starting stream processing...")
                    response = resp # Store the response object
                    break # Exit retry loop

            except RETRYABLE_HTTPX_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable httpx error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}."); yield {"type": "error", "content": f"[OllamaProvider Error]: Max retries after {type(e).__name__}. Last: {e}"}; return
            except Exception as e: # Catch other unexpected errors during request phase
                last_exception = e; logger.exception(f"Unexpected Error API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) after unexpected error."); yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected Error after retries - {type(e).__name__}"}; return

        # --- Check if request failed after all retries ---
        if response is None:
            logger.error(f"Ollama API request failed after all retries. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[OllamaProvider Error]: API request failed after {MAX_RETRIES} retries. Error: {type(last_exception).__name__ if last_exception else 'Request Failed'}"
            yield {"type": "error", "content": err_content}; return

        # --- Process the Successful Response Stream ---
        processed_lines = 0
        stream_error_occurred = False
        try:
            if self.streaming_mode:
                logger.debug("Starting streaming response processing loop using response.aiter_lines()...")
                async for line in response.aiter_lines():
                    if not line.strip(): continue # Skip empty lines

                    processed_lines += 1
                    # logger.log(logging.DEBUG - 1, f"Parsing line {processed_lines}: {line[:200]}...")
                    try:
                        chunk_data = json.loads(line) # Parse line directly

                        if chunk_data.get("error"):
                            error_msg = chunk_data["error"]; logger.error(f"Ollama stream error object: {error_msg}")
                            yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}; stream_error_occurred = True; break

                        message_chunk = chunk_data.get("message")
                        if message_chunk and isinstance(message_chunk, dict):
                            content_chunk = message_chunk.get("content");
                            if content_chunk: yield {"type": "response_chunk", "content": content_chunk}

                        if chunk_data.get("done", False):
                            logger.debug(f"Received done=true object. Line: {processed_lines}")
                            total_duration = chunk_data.get("total_duration");
                            if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                            # Let loop finish naturally

                    except json.JSONDecodeError:
                        logger.error(f"JSONDecodeError line {processed_lines}: {line[:500]}..."); yield {"type": "error", "content": "[OllamaProvider Error]: Failed stream JSON decode."}; stream_error_occurred = True; break
                    except Exception as e:
                        logger.error(f"Error processing stream line {processed_lines}: {e}", exc_info=True); logger.error(f"Problem line: {line[:500]}"); yield {"type": "error", "content": f"[OllamaProvider Error]: Stream processing error - {type(e).__name__}"}; stream_error_occurred = True; break

                logger.debug(f"Finished streaming loop (aiter_lines). Processed {processed_lines} lines. Error: {stream_error_occurred}")
                if stream_error_occurred: return # Exit if stream parsing error occurred

            else: # Non-Streaming (Using httpx)
                logger.debug("Processing non-streaming response...")
                try:
                    # Read the full response content before closing the context manager
                    full_response_text = await response.aread()
                    response_data = json.loads(full_response_text.decode('utf-8'))

                    if response_data.get("error"): error_msg = response_data["error"]; logger.error(f"Ollama non-streaming error: {error_msg}"); yield {"type": "error", "content": f"[OllamaProvider Error]: {error_msg}"}
                    elif response_data.get("message") and isinstance(response_data["message"], dict):
                        full_content = response_data["message"].get("content");
                        if full_content: logger.info(f"Non-streaming response len: {len(full_content)}"); yield {"type": "response_chunk", "content": full_content}
                        else: logger.warning("Non-streaming message content empty.")
                        if response_data.get("done", False): total_duration = response_data.get("total_duration");
                        if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                        else: logger.warning("Non-streaming response missing done=true.")
                    else: logger.error(f"Unexpected non-streaming structure: {response_data}"); yield {"type": "error", "content": "[OllamaProvider Error]: Unexpected non-streaming structure."}
                except json.JSONDecodeError:
                     logger.error(f"Failed non-streaming JSON decode. Raw: {full_response_text[:500]}..."); yield {"type": "error", "content": "[OllamaProvider Error]: Failed non-streaming decode."}
                except Exception as e:
                     logger.error(f"Error processing non-streaming: {e}", exc_info=True); yield {"type": "error", "content": f"[OllamaProvider Error]: Non-streaming processing error - {type(e).__name__}"}

        # --- Catch exceptions DURING stream processing ---
        except httpx.ReadTimeout as timeout_err:
             # This catches timeouts specifically during the reading phase (sock_read)
             logger.error(f"Ollama httpx timeout during stream read (read={client.timeout.read}s): {timeout_err}", exc_info=False)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Timeout waiting for stream data (read={client.timeout.read}s)"}
        except httpx.RemoteProtocolError as proto_err:
            # This can sometimes indicate the server closed the connection unexpectedly
            logger.error(f"Ollama processing failed with RemoteProtocolError: {proto_err}", exc_info=True)
            yield {"type": "error", "content": f"[OllamaProvider Error]: Connection closed unexpectedly during stream processing - {proto_err}"}
        except httpx.NetworkError as net_err:
             # General network errors during stream read
             logger.error(f"Ollama processing failed with NetworkError: {net_err}", exc_info=True)
             yield {"type": "error", "content": f"[OllamaProvider Error]: Network error during stream processing - {net_err}"}
        except Exception as e:
             logger.exception(f"Unexpected Error processing Ollama response stream: {type(e).__name__} - {e}")
             yield {"type": "error", "content": f"[OllamaProvider Error]: Unexpected stream processing error - {type(e).__name__}"}

        logger.info(f"OllamaProvider: stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        client_status = "closed" if self._client is None or self._client.is_closed else "open"
        mode = "streaming" if self.streaming_mode else "non-streaming"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client='{client_status}', mode='{mode}')>"

    async def __aenter__(self):
        await self._get_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
