# START OF FILE src/llm_providers/ollama_provider.py
import openai # Use the official openai library
import json
import asyncio
import logging
import time
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
RETRYABLE_STATUS_CODES = [429, 500, 502, 503, 504]
RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError
)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

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
    LLM Provider implementation for local Ollama models using the openai library.
    Assumes Ollama API endpoint compatibility. Corrected syntax error in error handling.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """
        Initializes the client using the openai library, targeting the Ollama endpoint.
        """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        self.api_key = api_key if api_key is not None else "ollama"
        valid_client_kwargs = {k: v for k, v in kwargs.items() if k not in KNOWN_OLLAMA_OPTIONS}

        try:
            self._openai_client = openai.AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url,
                max_retries=0, **valid_client_kwargs )
            logger.info(f"OllamaProvider initialized using 'openai' library. Target Base URL: {self.base_url}")
        except Exception as e:
            logger.error(f"Error initializing OpenAI client for Ollama endpoint: {e}", exc_info=True)
            raise ValueError(f"Failed to initialize OpenAI client for Ollama endpoint: {e}") from e

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
        """ Streams text completion using the openai library against the Ollama endpoint. """

        logger.info(f"Starting stream_completion with Ollama model {model}. History length: {len(messages)}.")
        if tools or tool_choice: logger.warning(f"OllamaProvider ignoring tools/tool_choice.")

        # Filter kwargs for Ollama 'options'
        ollama_options = {k: v for k, v in kwargs.items() if k in KNOWN_OLLAMA_OPTIONS and v is not None}
        ollama_options["temperature"] = temperature
        if max_tokens is not None: ollama_options["num_predict"] = max_tokens
        ignored_options = {k: v for k, v in kwargs.items() if k not in KNOWN_OLLAMA_OPTIONS}
        if ignored_options: logger.warning(f"OllamaProvider ignoring unknown options: {ignored_options}")

        # Prepare main payload
        api_params = { "model": model, "messages": messages, "temperature": temperature, "stream": True, **({"options": ollama_options} if ollama_options else {}) }
        log_params = {k: v for k, v in api_params.items() if k != 'messages'}
        logger.debug(f"OllamaProvider API call params (using openai lib): {log_params}")

        response_stream = None; last_exception = None

        # --- Retry Loop for Initial API Call ---
        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                response_stream = await self._openai_client.chat.completions.create(**api_params)
                logger.info(f"API call successful on attempt {attempt + 1}.")
                last_exception = None; break # Exit retry loop
            except RETRYABLE_EXCEPTIONS as e:
                last_exception = e; logger.warning(f"Retryable error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) after retryable error."); yield {"type": "error", "content": f"[Ollama Error]: Max retries. Last: {type(e).__name__}"}; return
            except openai.APIStatusError as e:
                last_exception = e; logger.warning(f"API Status Error attempt {attempt + 1}/{MAX_RETRIES + 1}: Status={e.status_code}, Body={e.body}")
                if (e.status_code in RETRYABLE_STATUS_CODES or e.status_code >= 500) and attempt < MAX_RETRIES: logger.info(f"Status {e.status_code} retryable. Wait {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else:
                    logger.error(f"Non-retryable API Status Error ({e.status_code}) or max retries reached.")
                    user_message = f"[Ollama Error]: API Status {e.status_code}";
                    # *** Corrected Inner Try/Except Block Indentation ***
                    try:
                        body_dict = json.loads(e.body) if isinstance(e.body, str) else (e.body if isinstance(e.body, dict) else {})
                        error_detail = body_dict.get('error')
                        # Check if error_detail itself is a dict with a message
                        if isinstance(error_detail, dict):
                             error_detail = error_detail.get('message')
                        # Now check if we have a string to append
                        if error_detail and isinstance(error_detail, str): # Ensure it's a string
                            user_message += f" - {error_detail[:100]}"
                    except Exception:
                        pass # Ignore parsing errors silently
                    # *** End Correction ***
                    yield {"type": "error", "content": user_message}
                    return
            except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e:
                 error_type_name = type(e).__name__; status_code = getattr(e, 'status_code', 'N/A'); error_body = getattr(e, 'body', 'N/A')
                 logger.error(f"Non-retryable Ollama API client error: {error_type_name} (Status: {status_code}), Body: {error_body}")
                 user_message = f"[Ollama Error]: Client Error ({error_type_name})"
                 # *** Corrected Inner Try/Except Block Indentation ***
                 try:
                     body_dict = json.loads(error_body) if isinstance(error_body, str) else (error_body if isinstance(error_body, dict) else {})
                     error_detail = body_dict.get('error')
                     if isinstance(error_detail, dict):
                          error_detail = error_detail.get('message')
                     if error_detail and isinstance(error_detail, str):
                          user_message += f" - {error_detail[:100]}"
                 except Exception:
                     pass # Ignore parsing errors silently
                 # *** End Correction ***
                 yield {"type": "error", "content": user_message}; return
            except Exception as e: # General catch-all
                last_exception = e; logger.exception(f"Unexpected Error API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES: logger.info(f"Waiting {RETRY_DELAY_SECONDS}s..."); await asyncio.sleep(RETRY_DELAY_SECONDS); continue
                else: logger.error(f"Max retries ({MAX_RETRIES}) after unexpected error."); yield {"type": "error", "content": f"[Ollama Error]: Unexpected Error after retries - {type(e).__name__}"}; return

        if response_stream is None:
             logger.error("API call failed after all retries, response_stream is None.")
             err_msg = f"[Ollama Error]: API call failed after {MAX_RETRIES} retries."
             if last_exception: err_msg += f" Last error: {type(last_exception).__name__}"
             yield {"type": "error", "content": err_msg}; return

        # --- Process the Stream ---
        try:
            finish_reason = None
            async for chunk in response_stream:
                raw_chunk_data_for_log = None
                try:
                     delta = chunk.choices[0].delta if chunk.choices else None
                     if chunk.choices and chunk.choices[0].finish_reason: finish_reason = chunk.choices[0].finish_reason
                     if not delta: continue
                     if delta.content: yield {"type": "response_chunk", "content": delta.content}
                     # Ignore delta.tool_calls for Ollama
                except Exception as chunk_proc_err:
                     logger.error(f"Error processing Ollama chunk: {chunk_proc_err}", exc_info=True)
                     try: raw_chunk_data_for_log = chunk.model_dump_json(); logger.error(f"Raw chunk error: {raw_chunk_data_for_log}")
                     except Exception: pass
                     yield {"type": "error", "content": f"[Ollama Error]: Chunk processing error - {type(chunk_proc_err).__name__}"}; return

            logger.debug(f"Ollama stream finished. Finish reason: {finish_reason}")
            if finish_reason == 'length': yield {"type": "status", "content": "Ollama turn finished (max_tokens limit reached)."}
            elif finish_reason == 'stop': yield {"type": "status", "content": "Ollama turn finished (stop sequence)."}

        except openai.APIError as stream_api_err:
            logger.error(f"Ollama APIError during stream: {stream_api_err}", exc_info=True)
            try: logger.error(f"APIError details: Status={stream_api_err.status_code}, Body={stream_api_err.body}")
            except Exception: pass
            yield {"type": "error", "content": f"[Ollama Error]: APIError during stream - {stream_api_err}"}
        except Exception as stream_err:
             logger.exception(f"Unexpected Error processing Ollama stream: {stream_err}")
             yield {"type": "error", "content": f"[Ollama Error]: Unexpected stream error - {type(stream_err).__name__}"}

        logger.info(f"OllamaProvider: stream_completion finished for model {model}.")


    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client_initialized={bool(self._openai_client)})>"

    async def close_session(self):
        logger.debug("OpenAI AsyncClient does not require explicit session closing.")
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
