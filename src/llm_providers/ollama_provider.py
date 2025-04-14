# START OF FILE src/llm_providers/ollama_provider.py
# *** WARNING: SYNCHRONOUS DEBUGGING VERSION - DO NOT USE IN PRODUCTION ***
import requests # Use requests for sync test
import json
# import asyncio # Comment out asyncio if not needed elsewhere
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator # Keep generator type hint

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration (requests handles retries differently, simplified here)
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_TOTAL_TIMEOUT = 600.0 # requests uses a single timeout value

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
    LLM Provider implementation for Ollama using SYNC requests for debugging.
    *** THIS IS FOR DIAGNOSTICS ONLY - NOT SUITABLE FOR ASYNC FRAMEWORK ***
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key: logger.warning("OllamaProvider Warning: API key provided but not used.")
        self.streaming_mode = kwargs.pop('stream', True) # Keep track of mode
        self._client_kwargs = kwargs # Store remaining kwargs (might not be used by requests)
        self._timeout = float(kwargs.pop('timeout', DEFAULT_TOTAL_TIMEOUT))
        mode_str = "Streaming" if self.streaming_mode else "Non-Streaming"
        logger.warning(f"--- USING SYNCHRONOUS REQUESTS FOR DEBUGGING ---")
        logger.info(f"OllamaProvider initialized with requests. Base URL: {self.base_url}. Mode: {mode_str}.")

    # Remove async client management
    async def _get_client(self): pass
    async def close_session(self): pass

    # *** MAKE stream_completion SYNCHRONOUS FOR TEST ***
    # Note: The 'async' and 'yield' make it technically an async generator still,
    # but the core HTTP call is synchronous. This will likely block the event loop.
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

        last_exception = None
        response = None

        # Simple retry loop for synchronous requests
        for attempt in range(MAX_RETRIES + 1):
            last_exception = None
            try:
                logger.info(f"OllamaProvider making SYNC API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                # Use requests.post with stream=True
                response = requests.post(
                    chat_endpoint,
                    json=payload,
                    stream=self.streaming_mode, # Pass streaming mode to requests
                    timeout=self._timeout,
                     headers={'Accept': 'application/json, text/event-stream'} # Mimic header
                )
                response_status = response.status_code

                if response_status == 200:
                    logger.info(f"SYNC API call successful (Status 200) on attempt {attempt + 1}.")
                    break # Success

                # Handle errors (simplified retry logic for sync)
                response_text = response.text # Read full error body
                logger.debug(f"Ollama API error status {response_status}. Body: {response_text[:500]}...")
                if response_status in RETRYABLE_STATUS_CODES or response_status >= 500:
                    last_exception = requests.exceptions.HTTPError(f"Status {response_status}", response=response)
                    logger.warning(f"Ollama API Error attempt {attempt + 1}: Status {response_status}...")
                    if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue # Still use asyncio.sleep for delay
                    else: logger.error(f"Max retries ({MAX_RETRIES}) after status {response_status}."); yield {"type": "error", "content": f"[Ollama Error]: Max retries. Last: Status {response_status} - {response_text[:100]}"}; return
                else: # Non-retryable 4xx
                    logger.error(f"Ollama API Client Error: Status {response_status}"); yield {"type": "error", "content": f"[Ollama Error]: Client Error {response_status} - {response_text[:100]}"}; return

            except requests.exceptions.Timeout as e:
                 last_exception = e; logger.warning(f"Retryable requests Timeout error attempt {attempt + 1}: {e}")
                 if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                 else: logger.error(f"Max retries ({MAX_RETRIES}) after Timeout."); yield {"type": "error", "content": f"[Ollama Error]: Max retries after Timeout. Last: {e}"}; return
            except requests.exceptions.RequestException as e: # Catch other requests errors
                last_exception = e; logger.warning(f"Retryable requests error attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) after {type(e).__name__}."); yield {"type": "error", "content": f"[Ollama Error]: Max retries after connection error. Last: {e}"}; return
            except Exception as e: # Catch unexpected errors
                last_exception = e; logger.exception(f"Unexpected Error SYNC API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) after unexpected error."); yield {"type": "error", "content": f"[Ollama Error]: Unexpected Error after retries - {type(e).__name__}"}; return


        if response is None or response.status_code != 200:
            logger.error(f"Ollama SYNC API request failed. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[Ollama Error]: SYNC API request failed after {MAX_RETRIES} retries. Error: {type(last_exception).__name__ if last_exception else 'Request Failed'}"
            yield {"type": "error", "content": err_content}; return

        # --- Process Response (Sync Version) ---
        stream_error_occurred = False
        try:
            if self.streaming_mode:
                logger.debug("Processing streaming response using requests iter_lines...")
                processed_lines = 0
                # Use response.iter_lines() for sync streaming
                for line in response.iter_lines():
                    if not line: continue # Skip keep-alive newlines

                    processed_lines += 1
                    decoded_line = line.decode('utf-8')
                    # logger.log(logging.DEBUG - 1, f"Parsing line {processed_lines}: {decoded_line[:200]}...")
                    try:
                        chunk_data = json.loads(decoded_line)
                        if chunk_data.get("error"): error_msg = chunk_data["error"]; logger.error(f"Ollama stream error: {error_msg}"); yield {"type": "error", "content": f"[Ollama Error]: {error_msg}"}; stream_error_occurred = True; break
                        message_chunk = chunk_data.get("message");
                        if message_chunk and isinstance(message_chunk, dict): content_chunk = message_chunk.get("content");
                        if content_chunk: yield {"type": "response_chunk", "content": content_chunk}
                        if chunk_data.get("done", False): logger.debug(f"Received done=true. Line: {processed_lines}"); total_duration = chunk_data.get("total_duration");
                        if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                    except json.JSONDecodeError: logger.error(f"JSONDecodeError line {processed_lines}: {decoded_line[:500]}..."); yield {"type": "error", "content": "[Ollama Error]: Failed stream JSON decode."}; stream_error_occurred = True; break
                    except Exception as e: logger.error(f"Error processing line {processed_lines}: {e}", exc_info=True); logger.error(f"Problem line: {decoded_line[:500]}"); yield {"type": "error", "content": f"[Ollama Error]: Stream error - {type(e).__name__}"}; stream_error_occurred = True; break
                logger.debug(f"Finished SYNC streaming loop. Lines: {processed_lines}. Error: {stream_error_occurred}")
                if stream_error_occurred: return

            else: # Non-Streaming (Sync)
                logger.debug("Processing non-streaming response (sync)...")
                try:
                    response_data = response.json() # Parse directly
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

        except requests.exceptions.ChunkedEncodingError as chunk_err:
            logger.error(f"Ollama requests ChunkedEncodingError during stream: {chunk_err}", exc_info=True)
            yield {"type": "error", "content": f"[Ollama Error]: Connection error during stream - {chunk_err}"}
        except requests.exceptions.RequestException as req_err:
            logger.error(f"Ollama requests error during stream: {req_err}", exc_info=True)
            yield {"type": "error", "content": f"[Ollama Error]: Network error during stream - {req_err}"}
        except Exception as e:
             logger.exception(f"Unexpected Error processing Ollama response (sync): {type(e).__name__} - {e}")
             yield {"type": "error", "content": f"[Ollama Error]: Unexpected processing error - {type(e).__name__}"}
        finally:
             if response is not None:
                 response.close() # Close sync response
                 logger.debug("Closed sync response.")

        if not stream_error_occurred: logger.info(f"OllamaProvider: stream_completion finished cleanly for model {model}.")
        else: logger.warning(f"OllamaProvider: stream_completion finished for model {model}, but error encountered.")


    def __repr__(self) -> str:
        mode = "streaming" if self.streaming_mode else "non-streaming"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client='requests (sync)', mode='{mode}')>"

    # Remove async context managers
    # async def __aenter__(self): return self
    # async def __aexit__(self, exc_type, exc_val, exc_tb): pass
