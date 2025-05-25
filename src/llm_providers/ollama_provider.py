# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict
from src.agents.constants import (
    MAX_RETRIES, RETRY_DELAY_SECONDS, RETRYABLE_STATUS_CODES, KNOWN_OLLAMA_OPTIONS
)

logger = logging.getLogger(__name__)

RETRYABLE_AIOHTTP_EXCEPTIONS = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError, 
)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 1200.0 
DEFAULT_TOTAL_TIMEOUT = 1800.0 

from src.config.settings import settings 
import copy

class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models using aiohttp.
    Handles streaming by reading raw bytes and splitting by newline.
    Creates a new ClientSession for each request to ensure clean state.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        logger.info(f"OllamaProvider initialized. Using Base URL: {self.base_url}")

        if api_key: logger.warning("OllamaProvider Warning: API key provided but not used.")
        self._session_timeout_config = kwargs.pop('timeout', None) # Pop before logging ignored, as it's handled
        if kwargs: # Log any other unexpected kwargs passed to constructor
            logger.warning(f"OllamaProvider __init__: Ignoring unexpected kwargs: {kwargs}")
            
        self.streaming_mode = True 
        mode_str = "Streaming"
        logger.info(f"OllamaProvider initialized with aiohttp. Effective Base URL: {self.base_url}. Mode: {mode_str}. Sessions created per-request.")

    async def _create_request_session(self) -> aiohttp.ClientSession:
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

        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        headers = {
            'Accept': 'application/json, text/event-stream',
        }
        session = aiohttp.ClientSession(
            base_url=self.base_url,
            timeout=timeout,
            connector=connector,
            headers=headers 
        )
        logger.debug(f"OllamaProvider: Created new aiohttp ClientSession for request. Timeout: {timeout}")
        return session

    async def close_session(self):
        logger.debug("OllamaProvider: close_session called (no-op - sessions are per-request).")
        pass

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

        logger.debug(f"OllamaProvider.stream_completion received messages (length {len(messages)}):")
        for i, msg_to_log in enumerate(messages):
            content_preview = str(msg_to_log.get('content'))[:200] 
            tool_calls_preview = msg_to_log.get('tool_calls')
            log_line = f"  [{i}] Role: {msg_to_log.get('role')}, Content: {content_preview}{'...' if len(str(msg_to_log.get('content'))) > 200 else ''}"
            if tool_calls_preview:
                try:
                    tool_calls_str = json.dumps(tool_calls_preview)
                    log_line += f", ToolCalls: {tool_calls_str}"
                except TypeError:
                    log_line += f", ToolCalls: [UnserializableData:{type(tool_calls_preview).__name__}]"
                except Exception as e_json: 
                    log_line += f", ToolCalls: [SerializationError:{str(e_json)}]"
            if msg_to_log.get('role') == 'tool':
                if 'name' in msg_to_log: 
                    log_line += f", ToolName: {msg_to_log['name']}"
                if 'tool_call_id' in msg_to_log:
                    log_line += f", ToolCallID: {msg_to_log['tool_call_id']}"
            tool_results_db_preview = msg_to_log.get('tool_results_json') 
            if tool_results_db_preview: 
                try:
                    tool_results_db_str = json.dumps(tool_results_db_preview)
                    log_line += f", ToolResults (from DB?): {tool_results_db_str}"
                except TypeError:
                    log_line += f", ToolResults (from DB?): [UnserializableData:{type(tool_results_db_preview).__name__}]"
                except Exception as e_json:
                    log_line += f", ToolResults (from DB?): [SerializationError:{str(e_json)}]"
            logger.debug(log_line)

        session = await self._create_request_session()
        chat_endpoint = "/api/chat"

        if tools or tool_choice: logger.warning(f"OllamaProvider ignoring tools/tool_choice arguments as XML parsing is primary.")

        # --- Explicitly remove 'project_name' if present in kwargs, as it's not an Ollama option ---
        if 'project_name' in kwargs:
            logger.debug(f"OllamaProvider: Removing 'project_name' from kwargs before processing options. Value: {kwargs['project_name']}")
            kwargs.pop('project_name')
        # --- End explicit removal ---

        raw_options = {"temperature": temperature, **kwargs}
        valid_options = {k: v for k, v in raw_options.items() if k in KNOWN_OLLAMA_OPTIONS and v is not None}
        if max_tokens is not None:
            valid_options["num_predict"] = max_tokens 
            logger.debug(f"Setting Ollama num_predict (max_tokens) to: {max_tokens}")
        if "num_ctx" not in valid_options and "num_ctx" not in kwargs: # Check original kwargs too
            valid_options["num_ctx"] = 8192 # Default context size
            logger.debug("Added default context size 'num_ctx: 8192' to Ollama options.")
        if "stop" not in valid_options and "stop" not in kwargs: # Check original kwargs too
            valid_options["stop"] = ["<|eot_id|>"] # Common stop token for newer models
            logger.debug("Added default stop token '<|eot_id|>' to Ollama options.")

        ignored_options = {k: v for k, v in raw_options.items() if k not in KNOWN_OLLAMA_OPTIONS and k != "stop"} # 'stop' handled above
        if ignored_options: logger.warning(f"OllamaProvider ignoring unknown options: {ignored_options}")

        messages_for_ollama_payload = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            processed_content = "" 
            if content is not None:
                if not isinstance(content, str):
                    try:
                        processed_content = json.dumps(content)
                        logger.warning(f"Message content for role '{role}' was not a string ({type(content)}), serialized to JSON.")
                    except (TypeError, ValueError):
                        processed_content = str(content)
                        logger.warning(f"Message content for role '{role}' was not a string ({type(content)}) and could not be JSON serialized. Converted to string.")
                else:
                    processed_content = content
            msg_to_send: Dict[str, Any] = {"role": role, "content": processed_content}
            if role == "assistant" and msg.get("tool_calls"):
                tc = msg.get("tool_calls")
                if isinstance(tc, list) and all(isinstance(t, dict) for t in tc):
                    msg_to_send["tool_calls"] = tc
                else:
                    logger.warning(f"tool_calls for assistant message is not in the expected format (list of dicts): {tc}")
            if role == "tool":
                if msg.get("tool_call_id"):
                    msg_to_send["tool_call_id"] = msg.get("tool_call_id")
            messages_for_ollama_payload.append(msg_to_send)

        payload = { "model": model, "messages": messages_for_ollama_payload, "stream": self.streaming_mode, "options": valid_options }
        
        try:
            full_payload_json_str_for_debug = json.dumps(payload, indent=2)
            logger.debug(f"OllamaProvider '{model}': EXACT JSON payload being sent to {chat_endpoint} (before request):\n{full_payload_json_str_for_debug}")
        except Exception as e_debug_log:
            logger.error(f"OllamaProvider '{model}': Could not serialize payload for detailed pre-request logging: {e_debug_log}")

        try:
            payload_for_log_str = json.dumps(payload, indent=2)
            if len(payload_for_log_str) > 2000: 
                 payload_copy_for_log = copy.deepcopy(payload) 
                 if "messages" in payload_copy_for_log and isinstance(payload_copy_for_log["messages"], list):
                      messages_summary_str = f"[Summarized: {len(payload_copy_for_log['messages'])} messages. First role: {payload_copy_for_log['messages'][0].get('role') if payload_copy_for_log['messages'] else 'N/A'}, Last role: {payload_copy_for_log['messages'][-1].get('role') if payload_copy_for_log['messages'] else 'N/A'}]"
                      payload_copy_for_log["messages"] = messages_summary_str
                      payload_for_log_str_summary = json.dumps(payload_copy_for_log, indent=2)
                      logger.debug(f"OllamaProvider: Payload being sent to {chat_endpoint} (Messages Summarized for Log):\n{payload_for_log_str_summary}")
                 else:
                      logger.debug(f"OllamaProvider: Payload being sent to {chat_endpoint}:\n{payload_for_log_str}")
            else:
                 logger.debug(f"OllamaProvider: Payload being sent to {chat_endpoint}:\n{payload_for_log_str}")
        except Exception as e_payload_log:
            logger.error(f"OllamaProvider: Could not serialize/log payload: {e_payload_log}")
            logger.debug(f"OllamaProvider: Basic payload info - model={payload.get('model')}, num_messages={len(payload.get('messages', []))}, options={payload.get('options')}")

        mode_log = "Streaming" if self.streaming_mode else "Non-Streaming"
        options_log = valid_options 
        logger.info(f"OllamaProvider preparing request ({mode_log}). Model: {model}, Endpoint: {chat_endpoint}, Options: {options_log}.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' ({mode_log})..."}

        last_exception = None
        response: Optional[aiohttp.ClientResponse] = None

        try: 
            for attempt in range(MAX_RETRIES + 1):
                last_exception = None
                response = None
                try:
                    logger.info(f"OllamaProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}).")
                    try:
                        json_payload_str = json.dumps(payload)
                        custom_headers = session.headers.copy() 
                        custom_headers['Content-Type'] = 'application/json' 
                        
                        response = await session.post(
                            chat_endpoint,
                            data=json_payload_str.encode('utf-8'), 
                            headers=custom_headers 
                        )
                    except TypeError as ser_err:
                        logger.error(f"OllamaProvider: TypeError during manual JSON serialization of payload: {ser_err}", exc_info=True)
                        logger.error(f"Problematic payload structure (details may be limited by error): {payload}")
                        last_exception = ser_err
                        if attempt < MAX_RETRIES:
                            logger.info(f"Serialization error, but retrying attempt {attempt + 2} if applicable...")
                            await asyncio.sleep(RETRY_DELAY_SECONDS)
                            continue 
                        else:
                            logger.error(f"Max retries reached after serialization error.")
                            yield {"type": "error", "content": f"[OllamaProvider Error]: Failed to serialize payload after retries - {ser_err}", "_exception_obj": ser_err}
                            if session and not session.closed: await session.close() 
                            return 
                        
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
                                if response and not response.closed: response.release() 
                                await asyncio.sleep(RETRY_DELAY_SECONDS)
                                continue
                            else:
                                logger.error(f"Max retries ({MAX_RETRIES}) after status {response_status}.")
                                yield {"type": "error", "content": f"[Ollama Error]: Max retries. Last: Status {response_status} - {response_text[:100]}", "_exception_obj": last_exception}
                                if session and not session.closed: await session.close()
                                return
                        else: 
                            client_err = aiohttp.ClientResponseError(req_info, response.history, status=response_status, message=f"Status {response_status}", headers=response.headers)
                            logger.error(f"Ollama API Client Error: Status {response_status}, Resp: {response_text[:200]}")
                            yield {"type": "error", "content": f"[Ollama Error]: Client Error {response_status} - {response_text[:100]}", "_exception_obj": client_err}
                            if session and not session.closed: await session.close()
                            return
                    else: 
                        logger.info(f"API call headers OK (Status {response_status}) attempt {attempt + 1}. Start stream.")
                        break

                except RETRYABLE_AIOHTTP_EXCEPTIONS as e:
                    last_exception = e
                    logger.warning(f"Retryable aiohttp error attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                        logger.info(f"Waiting {RETRY_DELAY_SECONDS}s...")
                        if response and not response.closed: response.release()
                        if session and not session.closed: await session.close() 
                        session = await self._create_request_session() 
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue
                    else:
                        logger.error(f"Max retries ({MAX_RETRIES}) reached after {type(e).__name__}.")
                        yield {"type": "error", "content": f"[Ollama Error]: Max retries after connection/timeout. Last: {e}", "_exception_obj": e}
                        if session and not session.closed: await session.close()
                        return
                except Exception as e:
                    last_exception = e
                    logger.exception(f"Unexpected Error during API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                    if attempt < MAX_RETRIES:
                         logger.info(f"Waiting {RETRY_DELAY_SECONDS}s...")
                         if response and not response.closed: response.release()
                         if session and not session.closed: await session.close()
                         session = await self._create_request_session()
                         await asyncio.sleep(RETRY_DELAY_SECONDS)
                         continue
                    else:
                         logger.error(f"Max retries ({MAX_RETRIES}) after unexpected error.")
                         yield {"type": "error", "content": f"[Ollama Error]: Unexpected Error after retries - {type(e).__name__}", "_exception_obj": e}
                         if session and not session.closed: await session.close()
                         return

            if response is None or response.status >= 400: 
                logger.error(f"Ollama API request failed. Last exception: {type(last_exception).__name__ if last_exception else 'N/A'}")
                err_content = f"[Ollama Error]: API request failed after {MAX_RETRIES} retries. Error: {type(last_exception).__name__ if last_exception else 'Request Failed'}"
                yield {"type": "error", "content": err_content, "_exception_obj": last_exception}
                if session and not session.closed: await session.close()
                return

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
                                continue
                            processed_lines += 1
                            try:
                                chunk_data = json.loads(line)
                                if chunk_data.get("error"):
                                    error_msg = chunk_data["error"]
                                    logger.error(f"Ollama stream error: {error_msg}")
                                    stream_error_obj = ValueError(f"[Ollama Error]: {error_msg}") 
                                    yield {"type": "error", "content": f"[Ollama Error]: {error_msg}", "_exception_obj": stream_error_obj}
                                    stream_error_occurred = True
                                if not stream_error_occurred: 
                                     if "message" in chunk_data and isinstance(chunk_data["message"], dict) and \
                                        "content" in chunk_data["message"]:
                                         content_chunk = chunk_data["message"]["content"]
                                         if content_chunk: 
                                             logger.debug(f"OllamaProvider: Yielding response_chunk: {content_chunk[:100]}...")
                                             yield {"type": "response_chunk", "content": content_chunk}
                                if chunk_data.get("done", False):
                                    total_duration = chunk_data.get("total_duration")
                                    logger.debug(f"Received done=true. Total duration: {total_duration}ns")
                                    if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)"}
                                    stream_error_occurred = False 
                                    if session and not session.closed: await session.close()
                                    return 
                            except json.JSONDecodeError as e:
                                logger.error(f"JSONDecodeError: {e} - Line: {line[:200]}")
                                stream_error_obj = ValueError(f"Invalid JSON response: {line[:100]}")
                                yield {"type": "error", "content": f"Invalid JSON response: {line[:100]}", "_exception_obj": stream_error_obj}
                                stream_error_occurred = True
                            except Exception as e:
                                logger.error(f"Stream processing error: {str(e)}")
                                stream_error_obj = e
                                yield {"type": "error", "content": f"Stream error: {str(e)}", "_exception_obj": e}
                                stream_error_occurred = True
                        if stream_error_occurred: break 
                    if stream_error_occurred: 
                        if session and not session.closed: await session.close()
                        return

                    logger.debug(f"Finished streaming loop (iter_any). Processed lines: {processed_lines}. Error occurred: {stream_error_occurred}")
                    if byte_buffer.strip() and not stream_error_occurred:
                        logger.warning(f"Processing remaining buffer after loop: {byte_buffer.decode('utf-8', errors='ignore')[:200]}...")
                        line = byte_buffer.decode('utf-8', errors='ignore').strip()
                        if line: 
                            try:
                                chunk_data = json.loads(line)
                                if "message" in chunk_data and isinstance(chunk_data["message"], dict) and \
                                   "content" in chunk_data["message"]:
                                    content_chunk = chunk_data["message"]["content"]
                                    if content_chunk:
                                        logger.debug("Yielding final content chunk from buffer.")
                                        yield {"type": "response_chunk", "content": content_chunk}
                                if chunk_data.get("done", False):
                                    logger.debug("Processed final 'done' from remaining buffer.")
                                    total_duration = chunk_data.get("total_duration")
                                    if total_duration: yield {"type": "status", "content": f"Ollama turn finished ({total_duration / 1e9:.2f}s)"}
                                else:
                                    logger.warning("Final buffer chunk did not contain 'done': True.")
                            except json.JSONDecodeError as final_e:
                                logger.error(f"Could not parse final buffer as JSON: {final_e}")
                            except Exception as final_e:
                                logger.error(f"Unexpected error processing final buffer: {final_e}")
                else: 
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
            except aiohttp.ClientPayloadError as payload_err: 
                logger.error(f"Ollama processing failed with ClientPayloadError: {payload_err}", exc_info=True)
                stream_error_obj = payload_err 
                yield {"type": "error", "content": f"[Ollama Error]: Connection closed unexpectedly during stream - {payload_err}", "_exception_obj": payload_err}
                stream_error_occurred = True
            except aiohttp.ClientConnectionError as conn_err: 
                 logger.error(f"Ollama processing failed with ClientConnectionError: {conn_err}", exc_info=True)
                 stream_error_obj = conn_err
                 yield {"type": "error", "content": f"[Ollama Error]: Network error during stream - {conn_err}", "_exception_obj": conn_err}
                 stream_error_occurred = True
            except asyncio.TimeoutError as timeout_err: 
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
                if response and not response.closed:
                    response.release()
            if not stream_error_occurred:
                logger.info(f"OllamaProvider: stream_completion finished cleanly for model {model}.")
            else:
                logger.warning(f"OllamaProvider: stream_completion finished for model {model}, but error encountered during stream.")
        finally:
            if session and not session.closed:
                await session.close()
                logger.debug("OllamaProvider: Closed per-request aiohttp ClientSession.")

    async def _read_response_safe(self, response: aiohttp.ClientResponse) -> str:
        try:
            return await response.text()
        except Exception as read_err:
            logger.warning(f"Could not read response body for status {response.status}: {read_err}")
            return f"(Read err: {read_err})"

    def __repr__(self) -> str:
        mode = "streaming" 
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', mode='{mode}')>"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass