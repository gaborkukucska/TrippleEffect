# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp
import json
import asyncio
import logging # Import logging
import time # Import time
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)
# Basic config (ideally configure centrally)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


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
# Safety limit for tool call loops
MAX_TOOL_CALLS_PER_TURN_OLLAMA = 5 # No longer used directly here


class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models with retry mechanism.
    Uses aiohttp to stream raw text completions from the Ollama API.
    Tool handling is done by the Agent Core via XML parsing.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """ Initializes the Ollama provider. """
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        if api_key:
            logger.warning("OllamaProvider Warning: API key provided but not used by standard Ollama.")

        self._session_kwargs = kwargs
        self._session: Optional[aiohttp.ClientSession] = None
        logger.info(f"OllamaProvider initialized. Base URL: {self.base_url}. Tool support via XML parsing by Agent.") # Updated log

    async def _get_session(self) -> aiohttp.ClientSession:
        """Creates or returns an existing aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            # Set a default timeout if not provided in kwargs
            timeout_seconds = self._session_kwargs.pop('timeout', 300) # Remove timeout if present, handle manually
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            # Store remaining kwargs for session creation
            remaining_kwargs = self._session_kwargs
            self._session = aiohttp.ClientSession(timeout=timeout, **remaining_kwargs)
            logger.info(f"OllamaProvider: Created new aiohttp session with timeout {timeout_seconds}s.")
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
        """ Streams completion from Ollama. Ignores tools/tool_choice parameters. """
        session = await self._get_session()
        chat_endpoint = f"{self.base_url}/api/chat"

        # Log that tools/tool_choice are ignored if provided
        if tools or tool_choice:
            logger.warning(f"OllamaProvider received tools/tool_choice arguments, but they will be ignored as tool handling is done via XML parsing by the Agent.")


        # --- Prepare Ollama payload (NO tools) ---
        payload = {
            "model": model, "messages": messages, "stream": True,
            "options": {"temperature": temperature, **kwargs}
        }
        if max_tokens is not None: payload["options"]["num_predict"] = max_tokens
        # Ensure no None values are sent in options
        payload["options"] = {k: v for k, v in payload["options"].items() if v is not None}

        logger.info(f"OllamaProvider preparing request. Model: {model}. Options: {payload.get('options')}. Tools ignored.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}'..."}

        response = None
        last_exception = None
        response_status = 0
        response_text = ""

        # --- Retry Loop for API call ---
        for attempt in range(MAX_RETRIES + 1):
            response = None # Reset response for each attempt
            try:
                 logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                 async with session.post(chat_endpoint, json=payload) as resp:
                    response_status = resp.status
                    if response_status >= 400:
                         response_text = await resp.text()

                    if response_status == 200:
                         logger.info(f"API call successful (Status {response_status}) on attempt {attempt + 1}.")
                         last_exception = None
                         response = resp # Keep the response object for streaming
                         break # Exit retry loop

                    elif response_status >= 500:
                         last_exception = ValueError(f"Ollama API Error {response_status}")
                         logger.warning(f"Ollama API Error on attempt {attempt + 1}: Status {response_status}, Response: {response_text[:200]}...")
                         if attempt < MAX_RETRIES:
                             logger.info(f"Status {response_status} >= 500. Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                             await asyncio.sleep(RETRY_DELAY_SECONDS)
                             continue
                         else:
                             logger.error(f"Max retries ({MAX_RETRIES}) reached after status {response_status}.")
                             yield {"type": "error", "content": f"[OllamaProvider Error: Max retries. Last error: Status {response_status} - {response_text[:100]}]"}
                             return

                    else: # 4xx errors
                        logger.error(f"Ollama API Client Error: Status {response_status}, Response: {response_text[:200]}")
                        yield {"type": "error", "content": f"[OllamaProvider Error: Client Error {response_status} - {response_text[:100]}]"}
                        return

            except RETRYABLE_OLLAMA_EXCEPTIONS as e:
                last_exception = e
                logger.warning(f"Retryable connection/timeout error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                     logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                     await asyncio.sleep(RETRY_DELAY_SECONDS)
                     continue
                else:
                     logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}.")
                     yield {"type": "error", "content": f"[OllamaProvider Error: Max retries reached after connection/timeout error. Last: {type(e).__name__}]"}
                     return

            except Exception as e:
                last_exception = e
                logger.exception(f"Unexpected Error during Ollama API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                     logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...")
                     await asyncio.sleep(RETRY_DELAY_SECONDS)
                     continue
                else:
                     logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error.")
                     yield {"type": "error", "content": f"[OllamaProvider Error: Unexpected Error after retries - {type(e).__name__}]"}
                     return

        # --- Check if API call failed after all retries ---
        if response is None or response.status != 200:
            logger.error(f"Ollama API call failed after all retries. Last status: {response_status}. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
            err_content = f"[OllamaProvider Error: API call failed after {MAX_RETRIES} retries. Status: {response_status}. Error: {type(last_exception).__name__ if last_exception else 'Failed Request'}]"
            yield {"type": "error", "content": err_content}
            return # Exit generator

        # --- Process the Successful Stream ---
        stream_error = False
        is_done = False

        try:
            async for line in response.content:
                if line:
                    try:
                        chunk_data = json.loads(line.decode('utf-8'))

                        if chunk_data.get("error"):
                            error_msg = chunk_data["error"]
                            logger.error(f"Received error in Ollama stream: {error_msg}")
                            yield {"type": "error", "content": f"[OllamaProvider Error: {error_msg}]"}
                            stream_error = True
                            break

                        message_chunk = chunk_data.get("message")
                        if message_chunk and isinstance(message_chunk, dict):
                            content_chunk = message_chunk.get("content")
                            if content_chunk:
                                # Yield raw text chunk for agent to parse
                                yield {"type": "response_chunk", "content": content_chunk}

                            # REMOVED: Tool call detection logic
                            # if "tool_calls" in message_chunk and message_chunk["tool_calls"]:
                            #     logger.warning(f"Ollama model returned 'tool_calls' unexpectedly, but provider ignores them.")

                        is_done = chunk_data.get("done", False)
                        if is_done:
                            if not stream_error:
                                logger.debug(f"Received done=true from stream for model {model}.")
                                total_duration = chunk_data.get("total_duration")
                                if total_duration:
                                     yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)."}
                            break

                    except json.JSONDecodeError:
                        logger.warning(f"Failed to decode JSON line from Ollama stream: {line}")
                    except Exception as e:
                        logger.error(f"Error processing Ollama stream line: {e}", exc_info=True)
                        yield {"type": "error", "content": f"[OllamaProvider Error processing stream: {e}]"}
                        stream_error = True
                        break

            # --- After finishing reading the stream ---
            if stream_error:
                logger.error("Exiting stream processing due to error encountered in stream.")

            # REMOVED: Assistant message appending (handled by manager/agent)
            # REMOVED: Tool call processing logic
            # REMOVED: Outer tool call attempt loop

        except Exception as e:
             logger.exception(f"Unexpected Error processing Ollama stream: {type(e).__name__} - {e}")
             yield {"type": "error", "content": f"[OllamaProvider Error: Stream processing error - {type(e).__name__}]"}

        logger.info(f"OllamaProvider: stream_completion finished for model {model}.")

    def __repr__(self) -> str:
        session_status = "closed" if self._session is None or self._session.closed else "open"
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', session='{session_status}')>"

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
