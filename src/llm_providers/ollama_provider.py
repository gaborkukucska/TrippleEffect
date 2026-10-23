# START OF FILE src/llm_providers/ollama_provider.py
import aiohttp
import json
import math
import asyncio
import logging
import random
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict
from src.agents.constants import (
    MAX_RETRIES, RETRY_DELAY_SECONDS, RETRYABLE_STATUS_CODES, KNOWN_OLLAMA_OPTIONS
)

logger = logging.getLogger(__name__)


THINKING_LOG_HEAD = 300
THINKING_LOG_TAIL = 700


def thinking_excerpt(thinking: Any) -> str:
    """Round 1z: the start and the end of a thinking text on one line ('' if there is none)."""
    text = " | ".join(line.strip() for line in str(thinking or "").splitlines() if line.strip())
    if len(text) <= THINKING_LOG_HEAD + THINKING_LOG_TAIL + 20:
        return text
    return f"{text[:THINKING_LOG_HEAD]} [... {len(text) - THINKING_LOG_HEAD - THINKING_LOG_TAIL} chars ...] {text[-THINKING_LOG_TAIL:]}"


def no_thinking_kwargs(provider: Any) -> Dict[str, Any]:
    """
    Round 1z: extra stream_completion() arguments for short framework verdicts (Constitutional Guardian). For Ollama they
    ask the model not to think: a Guardian review spent 4 min 48 s and 1337 tokens thinking before answering '<OK/>'.
    Set CG_THINKING=true in .env to let the Guardian think again. Other providers get nothing.
    """
    import os
    if os.getenv("CG_THINKING", "false").strip().lower() in ("1", "true", "yes", "on"):
        return {}
    return thinking_off_kwargs(provider)


def thinking_off_kwargs(provider: Any) -> Dict[str, Any]:
    """
    Round 2h: stream_completion() arguments that switch thinking off for ONE agent reply (Ollama only, {} otherwise).
    Used to retry after a reply that only thought (2026-10-19 19:17-19:39, qwen3.5:9b: PM1 sent 7 replies whose thinking
    ended with the next tool call, e.g. "Next action: modify_task for ba793058... with assignee_agent_id='W1'", but that
    contained no tool call and no text).
    """
    return {"think": False} if isinstance(provider, OllamaProvider) else {}


THINKING_TAIL_CHARS = 600


def thinking_tail(thinking: Any, limit: int = THINKING_TAIL_CHARS) -> str:
    """Round 2h: the end of a thinking text on one line ('' if there is none), cut at a word boundary."""
    text = " ".join(str(thinking or "").split())
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    space = tail.find(" ")
    return tail[space + 1:] if 0 <= space < 40 else tail


# Round 3c: the server's own timings (nanoseconds) of a finished reply
OLLAMA_TIMING_KEYS = ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")


def ollama_timing_text(data: Any) -> str:
    """
    Round 3c: ', timing: load 0.1s, prompt 3,097 tok in 28.1s (110 tok/s), output 94 tok in 23.0s (4.1 tok/s), total 51.5s'
    from a finished Ollama reply, or '' when it has no timings. 2026-10-22/23 (qwen3.5:4b) calls took about 2 minutes and
    the log could not tell reading the prompt from writing the reply, nor a model reload.
    """
    if not isinstance(data, dict) or not any(key in data for key in OLLAMA_TIMING_KEYS):
        return ""
    try:
        def seconds(key: str) -> Optional[float]:
            value = data.get(key)
            return float(value) / 1e9 if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        def part(label: str, count_key: str, duration_key: str) -> Optional[str]:
            duration, count = seconds(duration_key), data.get(count_key)
            if duration is None:
                return None
            if isinstance(count, int) and not isinstance(count, bool):
                rate = f" ({count / duration:.1f} tok/s)" if duration > 0 else ""
                return f"{label} {count:,} tok in {duration:.1f}s{rate}"
            return f"{label} {duration:.1f}s"

        parts = []
        load = seconds("load_duration")
        if load is not None:
            parts.append(f"load {load:.1f}s")
        for label, count_key, duration_key in (("prompt", "prompt_eval_count", "prompt_eval_duration"),
                                               ("output", "eval_count", "eval_duration")):
            text = part(label, count_key, duration_key)
            if text:
                parts.append(text)
        total = seconds("total_duration")
        if total is not None:
            parts.append(f"total {total:.1f}s")
        return (", timing: " + ", ".join(parts)) if parts else ""
    except Exception:
        return ""


def log_ollama_finish(data: Any, thinking_chars: Optional[int] = None, content_chars: Optional[int] = None,
                      thinking_text: Optional[str] = None) -> Optional[str]:
    """
    Round 1x: one log line per finished Ollama reply (done_reason, token counts, thinking size). A reply cut off at the
    output limit (done_reason 'length') is logged as a WARNING. Returns the done_reason, or None. Never raises.
    Round 1z: streamed replies pass the counts of ALL chunks (the 'done' chunk alone has no content), and a line with the
    start and end of the thinking text is logged first.
    """
    try:
        if not isinstance(data, dict) or not data.get("done"):
            return None
        reason = data.get("done_reason") or "?"
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        if thinking_text is None:
            thinking_text = msg.get("thinking") or ""
        if thinking_chars is None:
            thinking_chars = len(thinking_text)
        if content_chars is None:
            content_chars = len(msg.get("content") or "")
        excerpt = thinking_excerpt(thinking_text)
        if excerpt:
            logger.info(f"Ollama thinking ({len(thinking_text)} chars): {excerpt}")
        text = (f"Ollama finished: done_reason={reason}, prompt_tokens={data.get('prompt_eval_count', '?')}, "
                f"output_tokens={data.get('eval_count', '?')}, content_chars={content_chars}, "
                f"thinking_chars={thinking_chars}, tool_calls={len(msg.get('tool_calls') or [])}"
                + ollama_timing_text(data))  # Round 3c
        if reason == "length":
            logger.warning(text + " -> the reply was CUT OFF at the output token limit (num_predict / per-state max_tokens).")
        else:
            logger.info(text)
        return reason
    except Exception:
        return None

def finish_info_event(data: Any, reason: Optional[str], thinking_text: Optional[str] = None,
                      num_ctx: Optional[int] = None, num_predict: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Round 2d: a 'finish_info' event for the agent (done_reason and output token count), or None. The agent uses it to tell
    the model that a reply was CUT OFF at the output limit instead of calling it "empty" (2026-10-18: 4 complete-file
    replies of W1 were cut off at 1024 tokens and each time the framework said "Your last response was empty").
    Round 2h: it also carries the size and the end of the reply's thinking, so a reply that only thought can be told so.
    Round 2n: and the prompt size with the num_ctx / num_predict the request used, so the agent can tell a FULL CONTEXT
    WINDOW from an output cut-off (2026-10-20 23:59: W1 had 32,675 prompt tokens + 93 output tokens = num_ctx 32,768).
    """
    if not reason or not isinstance(data, dict):
        return None
    if thinking_text is None:
        msg = data.get("message")
        thinking_text = msg.get("thinking") if isinstance(msg, dict) else None
    thinking_text = str(thinking_text or "")
    return {"type": "finish_info", "done_reason": reason, "output_tokens": data.get("eval_count"),
            "thinking_chars": len(thinking_text), "thinking_tail": thinking_tail(thinking_text),
            "prompt_tokens": data.get("prompt_eval_count"), "num_ctx": num_ctx, "num_predict": num_predict}


CONTEXT_FULL_MARGIN = 16  # tokens


def is_context_window_full(done_reason: Any, prompt_tokens: Any, output_tokens: Any, num_ctx: Any,
                           num_predict: Any = None) -> bool:
    """
    Round 2n: True if a reply stopped because the CONTEXT WINDOW was full (prompt + output reached num_ctx) rather than
    because it reached its output limit (num_predict). A full window is not helped by a larger output budget: the history
    has to shrink.
    """
    try:
        if done_reason != "length" or not num_ctx or prompt_tokens is None:
            return False
        prompt, output, window = int(prompt_tokens), int(output_tokens or 0), int(num_ctx)
        if num_predict and int(num_predict) > 0:
            # Round 3c: a reply stops at 'length' for one of two limits. Below its output budget it was the window - also
            # when the server counted only the prompt tokens it did not have cached (a reused prompt reports fewer).
            return output < int(num_predict)
        return prompt + output >= window - CONTEXT_FULL_MARGIN
    except (TypeError, ValueError):
        return False


# Round 2y: calibrated on every reply of the 2026-10-22 runs (11:24-12:29 and 14:43-14:53): with the thinking counted as
# 3.5 characters per token, replies that only thought had at most 35 tokens left over; replies whose tool call was
# dropped had 78 or more (PM1's modify_task calls 14:48:08-14:48:31: 85-106; W2's file writes: 433-1,944).
LOST_OUTPUT_MIN_TOKENS = 50   # output tokens beyond the thinking that must have gone somewhere
THINKING_CHARS_PER_TOKEN = 3.5


def lost_output_tokens(done_reason: Any, output_tokens: Any, thinking_chars: Any, content_chars: Any,
                       tool_call_count: Any) -> int:
    """
    Round 2y: how many output tokens of a reply that finished NORMALLY (done_reason 'stop') delivered nothing - no text,
    no tool call and more tokens than its thinking explains - or 0. The server dropped that output: in practice a tool
    call it could not parse.
    """
    try:
        if done_reason != "stop" or int(content_chars or 0) > 0 or int(tool_call_count or 0) > 0:
            return 0
        thinking_tokens = math.ceil(int(thinking_chars or 0) / THINKING_CHARS_PER_TOKEN)
        unexplained = int(output_tokens or 0) - thinking_tokens
        return unexplained if unexplained >= LOST_OUTPUT_MIN_TOKENS else 0
    except (TypeError, ValueError):
        return 0


def is_output_lost(done_reason: Any, output_tokens: Any, thinking_chars: Any, content_chars: Any,
                   tool_call_count: Any) -> bool:
    """
    Round 2x: True if a reply that finished NORMALLY (done_reason 'stop') produced output tokens but delivered nothing: no
    text, no tool call, and far more tokens than its thinking explains (see lost_output_tokens). The server dropped that
    output - in practice a tool call it could not parse (2026-10-22 11:44:07 W2: 1,392 output tokens, 410 thinking chars,
    no text, no tool call; 11:44:30: 981 output tokens, nothing at all). Such a reply is not "empty": the model tried to act.
    """
    return lost_output_tokens(done_reason, output_tokens, thinking_chars, content_chars, tool_call_count) > 0


def sampling_penalty_options(options: Dict[str, Any], kwargs: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """
    Round 2y: the penalty options to add to a request: repeat_penalty = OLLAMA_REPEAT_PENALTY (default 1.0, i.e. off) and
    presence_penalty = OLLAMA_PRESENCE_PENALTY (default 0.0) - unless the call or the agent config sets them, or the
    setting is empty (then the model's own default applies).
    The provider used to add repeat_penalty 1.15 to every request, and qwen3.5's own default adds presence_penalty 1.5
    (Ollama server log, 2026-10-22 15:29: "repeat_penalty = 1.150 ... presence_penalty = 1.500"). Both penalise tokens
    the model has just written, and a tool call repeats '<parameter=...>' and '</parameter>' for every argument: PM1's
    three-argument modify_task was dropped five times in a row while one-argument request_state calls always arrived.
    """
    given = dict(kwargs or {})
    added: Dict[str, float] = {}
    for option, setting in (("repeat_penalty", "OLLAMA_REPEAT_PENALTY"), ("presence_penalty", "OLLAMA_PRESENCE_PENALTY")):
        if option in options or option in given:
            continue
        value = getattr(settings, setting, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            added[option] = float(value)
    return added


_sampling_logged: set = set()


def log_sampling_once(model: Any, options: Dict[str, Any]) -> None:
    """Round 2y: one INFO line per model with the sampling options the framework sends (the rest are the model's)."""
    key = str(model)
    if key in _sampling_logged:
        return
    _sampling_logged.add(key)
    shown = {k: options[k] for k in ("temperature", "repeat_penalty", "presence_penalty", "frequency_penalty", "top_k",
                                     "top_p", "min_p") if k in options}
    logger.info(f"OllamaProvider: sampling options sent for '{model}': {shown} (options not listed use the model's own "
                "defaults).")


def effective_ollama_num_ctx(native_num_ctx: Any) -> int:
    """
    Round 2n: the num_ctx the provider really sends for a model: its native context length capped at OLLAMA_MAX_CTX_CAP
    (default 32768), or the cap when the native value is unknown. The context summarizer must use the same number: it used
    the native 262,144 of qwen3.5:9b (threshold 131,072 tokens), so it never ran before the 32,768-token window was full.
    """
    cap = int(getattr(settings, 'OLLAMA_MAX_CTX_CAP', 32768) or 32768)
    try:
        native = int(native_num_ctx) if native_num_ctx else 0
    except (TypeError, ValueError):
        native = 0
    return min(native, cap) if native > 0 else cap


CONTINUE_AFTER_OWN_REPLY_NOTE = (
    "[Framework Note]: It is your turn again. Continue with your next step now. If your last reply above described an "
    "action, do it with a real tool call - text alone does not run anything."
)


def ends_with_assistant_message(messages: Any) -> bool:
    """
    Round 2p: True if a chat payload ends with an assistant message. Ollama then renders it as a prefill (qwen3.5 renderer:
    prefill := lastMessage && role == 'assistant') and the model only continues it - usually with a single end token.
    """
    return bool(messages) and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant"


RAW_TEMPLATES = ('{{ .Prompt }}', '{{ .Response }}', '{{ .System }}{{ .Prompt }}')
_RAW_TEMPLATE_WARNED: set = set()


def uses_builtin_renderer(model_info: Any) -> bool:
    """
    Round 2o: True if Ollama formats this model's chat itself (a built-in Go renderer, e.g. 'qwen3.5'), so a template of
    '{{ .Prompt }}' is NOT a missing chat template. /api/show reports 'renderer' (newer Ollama) and 'capabilities'.
    Ollama's /api/chat has no 'template' field: the ChatML override the provider used to send was ignored, and the renderer
    already shows the model its earlier calls as <tool_call> blocks.
    """
    if not isinstance(model_info, dict):
        return False
    if model_info.get("model_renderer"):
        return True
    capabilities = model_info.get("model_capabilities")
    return isinstance(capabilities, list) and "completion" in capabilities

# --- Concurrency Limiter ---
_ollama_semaphores = {}

def get_ollama_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _ollama_semaphores:
        limit = getattr(settings, 'OLLAMA_CONCURRENCY_LIMIT', 2)
        _ollama_semaphores[loop] = asyncio.Semaphore(limit)
    return _ollama_semaphores[loop]
# ---------------------------

RETRYABLE_AIOHTTP_EXCEPTIONS = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError, 
)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 1800.0 
DEFAULT_TOTAL_TIMEOUT = 3600.0 

from src.config.settings import settings 
import copy

class OllamaProvider(BaseLLMProvider):
    """
    LLM Provider implementation for local Ollama models using aiohttp.
    Handles streaming by reading raw bytes and splitting by newline.
    Creates a new ClientSession for each request to ensure clean state.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model_registry=None, **kwargs):
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip('/')
        logger.info(f"OllamaProvider initialized. Using Base URL: {self.base_url}")

        if api_key and api_key != 'ollama': logger.warning("OllamaProvider Warning: API key provided but not used.")
        self._model_registry = model_registry  # Reference to ModelRegistry for per-model metadata lookup
        self._session_timeout_config = kwargs.pop('timeout', None) # Pop before logging ignored, as it's handled
        if kwargs: # Log any other unexpected kwargs passed to constructor
            logger.warning(f"OllamaProvider __init__: Ignoring unexpected kwargs: {kwargs}")
            
        self.streaming_mode = True 
        mode_str = "Streaming"
        logger.info(f"OllamaProvider initialized with aiohttp. Effective Base URL: {self.base_url}. Mode: {mode_str}. Sessions created per-request. ModelRegistry: {'available' if model_registry else 'not provided'}.")

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

        if tools or tool_choice: logger.debug(f"OllamaProvider ignoring tools/tool_choice arguments as XML parsing is primary.")

        # --- Explicitly remove 'project_name' if present in kwargs, as it's not an Ollama option ---
        if 'project_name' in kwargs:
            logger.debug(f"OllamaProvider: Removing 'project_name' from kwargs before processing options. Value: {kwargs['project_name']}")
            kwargs.pop('project_name')
        # --- End explicit removal ---
        # Round 1z: 'think' is a top-level /api/chat field, not a model option (see no_thinking_kwargs()).
        think = kwargs.pop('think', None)

        raw_options = {"temperature": temperature, **kwargs}
        valid_options = {k: v for k, v in raw_options.items() if k in KNOWN_OLLAMA_OPTIONS and v is not None}
        
        # --- Sampling penalties (Round 2y: OLLAMA_REPEAT_PENALTY / OLLAMA_PRESENCE_PENALTY, see sampling_penalty_options) ---
        for option, value in sampling_penalty_options(valid_options, kwargs).items():
            valid_options[option] = value
        log_sampling_once(model, valid_options)
            
        if max_tokens is not None:
            valid_options["num_predict"] = max_tokens 
            logger.debug(f"Setting Ollama num_predict (max_tokens) to: {max_tokens}")
        else:
            valid_options["num_predict"] = 8192
            logger.debug(f"Setting Ollama default num_predict to: 8192 to prevent infinite hallucination loops")
        # --- Per-model metadata lookup from ModelRegistry ---
        model_info = None
        if self._model_registry and hasattr(self._model_registry, 'get_model_info'):
            model_info = self._model_registry.get_model_info(model)
            if model_info:
                logger.debug(f"OllamaProvider: Found model metadata for '{model}': family={model_info.get('family')}, "
                             f"num_ctx={model_info.get('model_num_ctx')}, stop_tokens={model_info.get('model_stop_tokens')}, "
                             f"template_preview={repr(str(model_info.get('model_template', ''))[:60])}")
            else:
                logger.debug(f"OllamaProvider: No model metadata found in registry for '{model}'.")
        
        # --- Set num_ctx: use model's native value, then fallback to 8192 ---
        if "num_ctx" not in valid_options and "num_ctx" not in kwargs:
            if model_info and model_info.get('model_num_ctx'):
                native_num_ctx = model_info['model_num_ctx']
                capped_num_ctx = effective_ollama_num_ctx(native_num_ctx)  # Round 2n: shared with the context summarizer
                valid_options["num_ctx"] = capped_num_ctx
                logger.info(f"OllamaProvider: Using model-native num_ctx={native_num_ctx} (capped to {capped_num_ctx}) for '{model}'.")
            else:
                valid_options["num_ctx"] = getattr(settings, 'OLLAMA_MAX_CTX_CAP', 8192)  # Conservative fallback when no metadata available
                logger.debug(f"OllamaProvider: No model-specific num_ctx found for '{model}', using default {valid_options['num_ctx']}.")
        
        # --- Set stop tokens: use model's native tokens, do NOT inject wrong defaults ---
        if "stop" not in valid_options and "stop" not in kwargs:
            if model_info and model_info.get('model_stop_tokens'):
                native_stops = model_info['model_stop_tokens']
                valid_options["stop"] = native_stops
                logger.info(f"OllamaProvider: Using model-native stop tokens {native_stops} for '{model}'.")
            else:
                # Do NOT inject any stop token — let Ollama use the model's built-in template stop handling
                logger.debug(f"OllamaProvider: No model-specific stop tokens found for '{model}'. "
                             f"Relying on Ollama's built-in template stop handling (no stop token injected).")
        
        # --- RAW templates (Round 2o) ---
        # Ollama's /api/chat takes no 'template' field (api.ChatRequest), so the ChatML override sent here until Round 2n was
        # silently ignored. A '{{ .Prompt }}' template usually means a built-in renderer formats the chat (qwen3.5, gemma4).
        if (model_info and model not in _RAW_TEMPLATE_WARNED and not uses_builtin_renderer(model_info)
                and str(model_info.get('model_template') or '').strip() in RAW_TEMPLATES):
            _RAW_TEMPLATE_WARNED.add(model)
            logger.warning(f"OllamaProvider: Model '{model}' has a RAW template and Ollama reports no renderer or capabilities "
                           f"for it: multi-turn /api/chat conversations may not be formatted correctly.")

        ignored_options = {k: v for k, v in raw_options.items() if k not in KNOWN_OLLAMA_OPTIONS and k != "stop"}
        if ignored_options: logger.warning(f"OllamaProvider ignoring unknown options: {ignored_options}")

        messages_for_ollama_payload = []
        first_system_seen = False
        
        # Models that need tool/system messages mapped to 'user' role due to their chat 
        # templates or Ollama's stripping behavior when native tools are disabled.
        models_needing_user_workaround = ["llama", "qwen", "mistral", "gemma", "phi", "deepseek"]
        needs_user_workaround = any(m in model.lower() for m in models_needing_user_workaround)
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "system":
                if first_system_seen:
                    if needs_user_workaround:
                        role = "user"
                        logger.debug(
                            f"OllamaProvider: Converting mid-conversation system message to 'user' role "
                            f"for model '{model}'. Content preview: {str(msg.get('content', ''))[:80]}..."
                        )
                    else:
                        logger.debug(f"OllamaProvider: Keeping mid-conversation system role for model '{model}'.")
                else:
                    first_system_seen = True
            elif role == "tool":
                if needs_user_workaround:
                    role = "user"
                    logger.debug(f"OllamaProvider: Converting 'tool' role message to 'user' role for model '{model}'.")
                else:
                    logger.debug(f"OllamaProvider: Keeping 'tool' role for model '{model}'.")

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
            
            # If this is a converted tool message, explicitly format it so the LLM knows what it is.
            if msg.get("role") == "tool" and role == "user":
                tool_name = msg.get("name", "unknown")
                processed_content = f"--- Tool Response ({tool_name}) ---\n{processed_content}\n-----------------------"

            # If this is a converted system message, explicitly format it so the LLM knows it's the system speaking.
            if msg.get("role") == "system" and role == "user":
                if not processed_content.lstrip().startswith("["):
                    processed_content = f"[System Notification]:\n{processed_content}"

            msg_to_send: Dict[str, Any] = {"role": role, "content": processed_content}
            # We explicitly DO NOT send `tool_calls` because TrippleEffect relies on XML-in-content 
            # and sending native tool_calls without the tools parameter confuses Ollama.
            # However, if native tool calling is enabled and tools are passed, we MUST include them.
            if settings.NATIVE_TOOL_CALLING_ENABLED and tools and msg.get("tool_calls"):
                ollama_tool_calls = []
                for tc in msg.get("tool_calls", []):
                    ollama_tc = {
                        "function": {
                            "name": tc.get("name"),
                            "arguments": tc.get("arguments", {})
                        }
                    }
                    ollama_tool_calls.append(ollama_tc)
                msg_to_send["tool_calls"] = ollama_tool_calls
                
            messages_for_ollama_payload.append(msg_to_send)

        # Round 2p: a history that ENDS with an assistant message is a "prefill" for Ollama - the model continues that
        # finished message instead of starting a new reply and stops at once (2026-10-21 18:24:29: PM1 got 1 output token
        # after its own text "...I should assign them immediately", counted as an empty reply 8 times in that run).
        if ends_with_assistant_message(messages_for_ollama_payload):
            messages_for_ollama_payload.append({"role": "user", "content": CONTINUE_AFTER_OWN_REPLY_NOTE})
            logger.info(f"OllamaProvider: the history for '{model}' ended with an assistant message; added a continue note "
                        "so the model starts a new reply instead of continuing its last one.")

        payload = { "model": model, "messages": messages_for_ollama_payload, "stream": self.streaming_mode, "options": valid_options }
        if think is not None:
            payload["think"] = bool(think)

        use_streaming_mode = self.streaming_mode
        if settings.NATIVE_TOOL_CALLING_ENABLED and tools:
            payload["tools"] = tools
            payload["stream"] = False  # Disable streaming for tool calls to ensure cleaner parsing
            use_streaming_mode = False
            logger.info("OllamaProvider: Native tool calling enabled. Attaching tools and forcing stream=False.")
        
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
                      payload_copy_for_log["messages"] = messages_summary_str  # type: ignore[assignment]
                      payload_for_log_str_summary = json.dumps(payload_copy_for_log, indent=2)
                      logger.debug(f"OllamaProvider: Payload being sent to {chat_endpoint} (Messages Summarized for Log):\n{payload_for_log_str_summary}")
                 else:
                      logger.debug(f"OllamaProvider: Payload being sent to {chat_endpoint}:\n{payload_for_log_str}")
            else:
                 logger.debug(f"OllamaProvider: Payload being sent to {chat_endpoint}:\n{payload_for_log_str}")
        except Exception as e_payload_log:
            logger.error(f"OllamaProvider: Could not serialize/log payload: {e_payload_log}")
            logger.debug(f"OllamaProvider: Basic payload info - model={payload.get('model')}, num_messages={len(payload.get('messages', []))}, options={payload.get('options')}")  # type: ignore[arg-type]

        mode_log = "Streaming" if self.streaming_mode else "Non-Streaming"
        options_log = valid_options 
        logger.info(f"OllamaProvider preparing request ({mode_log}). Model: {model}, Endpoint: {chat_endpoint}, Options: {options_log}.")
        yield {"type": "status", "content": f"Contacting Ollama model '{model}' ({mode_log})..."}

        last_exception = None
        response: Optional[aiohttp.ClientResponse] = None

        semaphore = get_ollama_semaphore()
        logger.debug(f"OllamaProvider '{model}': Waiting for semaphore (limit {semaphore._value})...")
        async with semaphore:
            logger.debug(f"OllamaProvider '{model}': Semaphore acquired!")
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
                                backoff_delay = RETRY_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1)
                                logger.info(f"Serialization error, but retrying attempt {attempt + 2} in {backoff_delay:.2f}s...")
                                await asyncio.sleep(backoff_delay)
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

                            # Round 1z: an Ollama version or model that does not accept "think" gets the request without it.
                            if response_status == 400 and "think" in payload and "think" in response_text.lower():
                                logger.warning(f"Ollama rejected the 'think' field ({response_text[:100]!r}). Sending the request without it.")
                                del payload["think"]
                                if response and not response.closed: response.release()
                                continue

                            # --- NATIVE TOOL FALLBACK START ---
                            is_xml_crash = response_status == 500 and "XML syntax error" in response_text
                            is_unsupported_tools = response_status == 400 and "does not support tools" in response_text
                            
                            if (is_xml_crash or is_unsupported_tools) and "tools" in payload:
                                logger.warning(f"Ollama native tool execution failed ({response_status}): '{response_text[:100]}'. Stripping tools from payload and falling back to RAW mode!")
                                del payload["tools"]
                                payload["stream"] = self.streaming_mode
                                use_streaming_mode = self.streaming_mode
                                
                                for m in payload.get("messages", []):  # type: ignore[union-attr]
                                    if isinstance(m, dict) and "tool_calls" in m:
                                        del m["tool_calls"]
                                        
                                if response and not response.closed: response.release()
                                continue
                            # --- NATIVE TOOL FALLBACK END ---

                            if response_status in RETRYABLE_STATUS_CODES or response_status >= 500:
                                last_exception = aiohttp.ClientResponseError(req_info, response.history, status=response_status, message=f"Status {response_status}", headers=response.headers)
                                logger.warning(f"Ollama API Error attempt {attempt + 1}: Status {response_status}, Resp: {response_text[:200]}...")
                                if attempt < MAX_RETRIES:
                                    backoff_delay = RETRY_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1)
                                    logger.info(f"Status {response_status} retryable. Wait {backoff_delay:.2f}s...")
                                    if response and not response.closed: response.release() 
                                    await asyncio.sleep(backoff_delay)
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
                            backoff_delay = RETRY_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1)
                            logger.info(f"Waiting {backoff_delay:.2f}s...")
                            if response and not response.closed: response.release()
                            if session and not session.closed: await session.close() 
                            session = await self._create_request_session() 
                            await asyncio.sleep(backoff_delay)
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
                             backoff_delay = RETRY_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1)
                             logger.info(f"Waiting {backoff_delay:.2f}s...")
                             if response and not response.closed: response.release()
                             if session and not session.closed: await session.close()
                             session = await self._create_request_session()
                             await asyncio.sleep(backoff_delay)
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
                streamed_content_chars = 0      # Round 1z: finish statistics over all streamed chunks
                streamed_thinking: List[str] = []
                try:
                    if use_streaming_mode:
                        logger.debug("Starting streaming using response.content.iter_any()")
                        # Use manual async iteration with per-chunk timeout to prevent
                        # indefinite hangs when Ollama unloads/stops model mid-stream.
                        # aiohttp's sock_read timeout does not always fire in chunked
                        # transfer-encoding edge cases, so this is a hard safety net.
                        chunk_read_timeout = DEFAULT_READ_TIMEOUT  # 240s per chunk
                        stream_iter = response.content.iter_any().__aiter__()
                        keep_alive_count = 0
                        while True:
                            try:
                                chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=chunk_read_timeout)
                            except StopAsyncIteration:
                                break  # Stream ended normally
                            except asyncio.TimeoutError:
                                logger.error(f"OllamaProvider: Hard per-chunk read timeout ({chunk_read_timeout}s) fired. Ollama likely stopped mid-stream.")
                                stream_error_obj = asyncio.TimeoutError(f"No data received from Ollama for {chunk_read_timeout}s during streaming")
                                yield {"type": "error", "content": f"[Ollama Error]: Stream stalled - no data for {chunk_read_timeout}s. Model may have been unloaded.", "_exception_obj": stream_error_obj}
                                stream_error_occurred = True
                                break
                            if not chunk: continue
                            byte_buffer += chunk
                            while b'\n' in byte_buffer:
                                line_bytes, byte_buffer = byte_buffer.split(b'\n', 1)
                                line = line_bytes.decode('utf-8').strip()
                                if not line:
                                    keep_alive_count += 1
                                    if keep_alive_count % 5 == 0:  # Yield status every 5 keep-alives
                                        yield {"type": "status", "content": f"Ollama is actively processing context (keep-alive {keep_alive_count})..."}
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
                                         if isinstance(chunk_data.get("message"), dict) and chunk_data["message"].get("thinking"):
                                             streamed_thinking.append(str(chunk_data["message"]["thinking"]))
                                         if "message" in chunk_data and isinstance(chunk_data["message"], dict) and \
                                            "content" in chunk_data["message"]:
                                             content_chunk = chunk_data["message"]["content"]
                                             if content_chunk:
                                                 streamed_content_chars += len(content_chunk)
                                                 # logger.debug(f"OllamaProvider: Yielding response_chunk: {content_chunk[:100]}...") # Disabled to prevent log spam
                                                 yield {"type": "response_chunk", "content": content_chunk}
                                    if chunk_data.get("done", False):
                                        all_thinking = "".join(streamed_thinking)
                                        finish_reason = log_ollama_finish(chunk_data, thinking_chars=len(all_thinking),
                                                                          content_chars=streamed_content_chars, thinking_text=all_thinking)
                                        finish_event = finish_info_event(chunk_data, finish_reason, all_thinking,
                                                                         num_ctx=valid_options.get("num_ctx"),
                                                                         num_predict=valid_options.get("num_predict"))
                                        if finish_event:
                                            yield finish_event
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
                             try:
                                 response_data = json.loads(response_data_text)
                             except json.JSONDecodeError:
                                 logger.warning(f"Failed strict non-streaming JSON decode. Attempting NDJSON fallback. Raw preview: {response_data_text[:200]}")
                                 lines = response_data_text.strip().split("\n")
                                 accumulated_tool_calls = []
                                 accumulated_content = ""
                                 accumulated_thinking_chars = 0  # Round 1x: finish statistics
                                 accumulated_thinking_parts: List[str] = []  # Round 1z: thinking excerpt in the log
                                 done_stats: Dict[str, Any] = {}
                                 is_done = False
                                 has_error = None
                                 for line in lines:
                                     if not line.strip(): continue
                                     try:
                                         chunk_data = json.loads(line)
                                         if chunk_data.get("error"):
                                             has_error = chunk_data["error"]
                                         msg = chunk_data.get("message", {})
                                         if msg.get("tool_calls"):
                                             accumulated_tool_calls.extend(msg["tool_calls"])
                                         if msg.get("content"):
                                             accumulated_content += msg["content"]
                                         if msg.get("thinking"):
                                             accumulated_thinking_chars += len(msg["thinking"])
                                             accumulated_thinking_parts.append(str(msg["thinking"]))
                                         if chunk_data.get("done"):
                                             is_done = True
                                             done_stats = {k: chunk_data[k] for k in ("done_reason", "eval_count", "prompt_eval_count") + OLLAMA_TIMING_KEYS
                                                           if k in chunk_data}  # Round 3c: + the timings
                                     except json.JSONDecodeError:
                                         logger.error(f"Failed to decode NDJSON fallback line: {line[:100]}...")
                                 
                                 response_data = {
                                     "message": {
                                         "role": "assistant",
                                         "content": accumulated_content,
                                         "thinking": "".join(accumulated_thinking_parts)  # Round 2h: for finish_info
                                     },
                                     "done": is_done
                                 }
                                 if accumulated_tool_calls:
                                     response_data["message"]["tool_calls"] = accumulated_tool_calls
                                 if has_error:
                                     response_data["error"] = has_error
                                 response_data.update(done_stats)
                                 finish_reason = log_ollama_finish(response_data, thinking_chars=accumulated_thinking_chars,
                                                                   thinking_text="".join(accumulated_thinking_parts))
                             else:
                                 finish_reason = log_ollama_finish(response_data)
                             finish_event = finish_info_event(response_data, finish_reason,
                                                              num_ctx=valid_options.get("num_ctx"),
                                                              num_predict=valid_options.get("num_predict"))
                             if finish_event:
                                 yield finish_event
                             if response_data.get("error"):
                                 error_msg = response_data["error"]

                                 logger.error(f"Ollama non-streaming error: {error_msg}")
                                 stream_error_obj = ValueError(f"[Ollama Error]: {error_msg}")
                                 yield {"type": "error", "content": f"[Ollama Error]: {error_msg}", "_exception_obj": stream_error_obj}
                             elif response_data.get("message") and isinstance(response_data["message"], dict):
                                 msg = response_data["message"]
                                 tool_calls = msg.get("tool_calls")
                                 full_content = msg.get("content", "")
                                 
                                 if tool_calls:
                                     logger.info(f"Ollama returned native tool_calls: {tool_calls}")
                                     yield {"type": "native_tool_calls", "tool_calls": tool_calls}
                                 elif full_content:
                                     # Fallback: check if content is a JSON string representing a tool call (Qwen2.5 behavior)
                                     try:
                                         parsed_content = json.loads(full_content)
                                         if isinstance(parsed_content, dict) and "name" in parsed_content and "arguments" in parsed_content:
                                             logger.info(f"Ollama (Qwen workaround) parsed raw JSON content as tool_call: {parsed_content}")
                                             yield {"type": "native_tool_calls", "tool_calls": [{"function": parsed_content}]}
                                         else:
                                             logger.info(f"Non-streaming len: {len(full_content)}")
                                             yield {"type": "response_chunk", "content": full_content}
                                     except json.JSONDecodeError:
                                         logger.info(f"Non-streaming len: {len(full_content)}")
                                         yield {"type": "response_chunk", "content": full_content}
                                 else:
                                     logger.warning("Non-streaming message content empty.")

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
