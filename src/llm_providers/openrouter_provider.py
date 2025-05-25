# START OF FILE src/llm_providers/openrouter_provider.py
import openai
import json
import asyncio
import logging
import time 
from typing import List, Dict, Any, Optional, AsyncGenerator

from .base import BaseLLMProvider, MessageDict, ToolDict, ToolResultDict
from src.config.settings import settings 
from src.agents.constants import (
    MAX_RETRIES, RETRY_DELAY_SECONDS, RETRYABLE_STATUS_CODES, RETRYABLE_EXCEPTIONS
)

logger = logging.getLogger(__name__)

LOCAL_RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError
)

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_REFERER = settings.DEFAULT_PERSONA
OPENROUTER_REFERER = settings.OPENROUTER_REFERER if hasattr(settings, 'OPENROUTER_REFERER') and settings.OPENROUTER_REFERER else DEFAULT_REFERER

# Standard OpenAI client constructor arguments
OPENAI_CLIENT_VALID_INIT_KWARGS = {
    "api_key", "organization", "project", "base_url", "timeout", "max_retries",
    "default_headers", "default_query", "http_client", "api_type", "api_version",
    "azure_endpoint", "azure_deployment", "azure_ad_token", "azure_ad_token_provider",
    " 스트리밍_usage" # Example of an odd one, good to list knowns
}

# Standard OpenAI chat completions create arguments (excluding messages, model, stream, temperature, max_tokens)
OPENAI_COMPLETIONS_VALID_KWARGS = {
    "frequency_penalty", "logit_bias", "logprobs", "top_logprobs", "max_tokens", 
    "n", "presence_penalty", "response_format", "seed", "stop", 
    "stream_options", "temperature", "tool_choice", "tools", "top_p", "user",
    # "extra_headers", "extra_query", "extra_body", # These are for passing extra HTTP details
    # "timeout" # Can also be specified per-request
}


class OpenRouterProvider(BaseLLMProvider):
    """
    LLM Provider implementation for OpenRouter API with retry mechanism.
    Uses the openai library configured for OpenRouter.
    Streams raw text completions. Tool handling is done by the Agent Core via XML parsing.
    Includes enhanced handling for errors occurring during stream processing.
    Filters kwargs to prevent passing unsupported arguments to the OpenAI client.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        if not api_key:
            raise ValueError("OpenRouter API key is required for OpenRouterProvider.")

        self.base_url = (base_url or DEFAULT_OPENROUTER_BASE_URL).rstrip('/')
        self.api_key = api_key
        referer = kwargs.pop('referer', OPENROUTER_REFERER)

        default_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": referer,
            "X-Title": "TrippleEffect", 
        }
        logger.info(f"OpenRouterProvider: Using Referer: {referer}")

        # Filter kwargs for AsyncOpenAI constructor
        valid_client_kwargs = {k: v for k, v in kwargs.items() if k in OPENAI_CLIENT_VALID_INIT_KWARGS}
        ignored_client_kwargs = {k: v for k, v in kwargs.items() if k not in OPENAI_CLIENT_VALID_INIT_KWARGS}
        if ignored_client_kwargs:
            logger.warning(f"OpenRouterProvider __init__: Ignoring unsupported kwargs for OpenAI client: {ignored_client_kwargs}")

        try:
            self._openai_client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=self.base_url,
                default_headers=default_headers,
                max_retries=0, 
                **valid_client_kwargs # Pass only valid kwargs
            )
            logger.info(f"OpenRouterProvider initialized. Base URL: {self.base_url}. Client: {self._openai_client}. Tool support via XML parsing by Agent.")
        except Exception as e:
            logger.error(f"Error initializing OpenAI client for OpenRouter: {e}", exc_info=True)
            raise ValueError(f"Failed to initialize OpenAI client for OpenRouter: {e}") from e

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
        logger.info(f"Starting stream_completion with OpenRouter model {model}. History length: {len(messages)}.")
        if tools or tool_choice:
            logger.warning(f"OpenRouterProvider received tools/tool_choice arguments, but they will be ignored.")

        response_stream = None
        last_exception = None
        
        # Filter kwargs for chat.completions.create
        # Start with core params always needed
        api_params: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None: # Only add if not None
            api_params["max_tokens"] = max_tokens

        # Add other valid OpenAI completion kwargs from the provided **kwargs
        for k, v in kwargs.items():
            if k in OPENAI_COMPLETIONS_VALID_KWARGS:
                api_params[k] = v
            else:
                logger.warning(f"OpenRouterProvider stream_completion: Ignoring unsupported kwarg '{k}' for OpenAI chat completions.")

        for attempt in range(MAX_RETRIES + 1):
            try:
                log_params = {k: v for k, v in api_params.items() if k != 'messages'} # For concise logging
                logger.info(f"OpenRouterProvider making API call (Attempt {attempt + 1}/{MAX_RETRIES + 1}). Params: {log_params}")
                
                try:
                    full_api_params_json_str = json.dumps(api_params, indent=2, default=str) 
                    logger.debug(f"OpenRouterProvider '{model}': FULL JSON equivalent of api_params being sent:\n{full_api_params_json_str}")
                except Exception as e_full_params_log:
                    logger.error(f"OpenRouterProvider '{model}': CRITICAL - Could not serialize FULL api_params for logging: {e_full_params_log}")
                    logger.debug(f"OpenRouterProvider '{model}': Fallback api_params parts: model={api_params.get('model')}, options_subset={ {k:v for k,v in api_params.items() if k not in ['messages']} }")
                    if "messages" in api_params:
                        logger.debug(f"OpenRouterProvider '{model}': Fallback - Number of messages in api_params: {len(api_params['messages'])}")
                        for i_msg, msg_item in enumerate(api_params['messages']):
                            try: logger.debug(f"OpenRouterProvider '{model}': Fallback - Msg [{i_msg}]: {json.dumps(msg_item, default=str)}")
                            except: logger.debug(f"OpenRouterProvider '{model}': Fallback - Msg [{i_msg}] (not fully serializable): role={msg_item.get('role')}, content_type={type(msg_item.get('content'))}, tool_calls_type={type(msg_item.get('tool_calls'))}")
                
                response_stream = await self._openai_client.chat.completions.create(**api_params)
                logger.info(f"API call successful on attempt {attempt + 1}.")
                last_exception = None 
                break 

            except LOCAL_RETRYABLE_EXCEPTIONS as e: 
                last_exception = e; logger.warning(f"Retryable OpenRouter error on attempt {attempt + 1}/{MAX_RETRIES + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...") 
                    await asyncio.sleep(RETRY_DELAY_SECONDS); continue 
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after retryable error.") 
                    yield {"type": "error", "content": f"[OpenRouterProvider Error]: Max retries reached. Last error: {type(e).__name__}", "_exception_obj": e}
                    return
            except openai.APIStatusError as e:
                last_exception = e
                logger.warning(f"OpenRouter API Status Error on attempt {attempt + 1}/{MAX_RETRIES + 1}: Status={e.status_code}, Body={e.body}")
                if (e.status_code >= 500 or e.status_code in RETRYABLE_STATUS_CODES) and attempt < MAX_RETRIES: 
                    logger.info(f"Status {e.status_code} is retryable. Waiting {RETRY_DELAY_SECONDS}s before retrying...") 
                    await asyncio.sleep(RETRY_DELAY_SECONDS) 
                    continue
                else:
                    logger.error(f"Non-retryable API Status Error ({e.status_code}) or max retries reached.") 
                    user_message = f"[OpenRouterProvider Error]: API Status {e.status_code}"
                    try:
                        body_dict = json.loads(e.body) if isinstance(e.body, str) else (e.body if isinstance(e.body, dict) else {}) 
                        error_detail = body_dict.get('error', {}).get('message') or body_dict.get('message') 
                        if error_detail: user_message += f" - {str(error_detail)[:100]}"
                    except Exception: pass 
                    yield {"type": "error", "content": user_message, "_exception_obj": e}
                    return
            except (openai.AuthenticationError, openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e: 
                 error_type_name = type(e).__name__
                 status_code = getattr(e, 'status_code', 'N/A')
                 error_body = getattr(e, 'body', 'N/A')
                 logger.error(f"Non-retryable OpenAI API error (via OpenRouter): {error_type_name} (Status: {status_code}), Body: {error_body}")
                 user_message = f"[OpenRouterProvider Error]: {error_type_name}"
                 try:
                     body_dict = json.loads(error_body) if isinstance(error_body, str) else (error_body if isinstance(error_body, dict) else {}) 
                     error_detail = body_dict.get('error', {}).get('message') or body_dict.get('message') 
                     if error_detail:
                         user_message += f" - {str(error_detail)[:100]}"
                 except Exception: 
                     pass
                 yield {"type": "error", "content": user_message, "_exception_obj": e}
                 return
            except Exception as e: 
                last_exception = e; logger.exception(f"Unexpected Error during OpenRouter API call attempt {attempt + 1}: {type(e).__name__} - {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"Waiting {RETRY_DELAY_SECONDS}s before retrying...") 
                    await asyncio.sleep(RETRY_DELAY_SECONDS) 
                    continue
                else:
                    logger.error(f"Max retries ({MAX_RETRIES}) reached after unexpected error.") 
                    yield {"type": "error", "content": f"[OpenRouterProvider Error]: Unexpected Error after retries - {type(e).__name__}", "_exception_obj": e}
                    return

        if response_stream is None:
             logger.error("OpenRouter API call failed after all retries, response_stream is None.")
             err_msg = f"[OpenRouterProvider Error]: API call failed after {MAX_RETRIES} retries." 
             if last_exception: err_msg += f" Last error: {type(last_exception).__name__}"
             yield {"type": "error", "content": err_msg, "_exception_obj": last_exception}; return

        try:
            finish_reason = None
            try:
                async for chunk in response_stream:
                    raw_chunk_data_for_log = None
                    try:
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if chunk.choices and chunk.choices[0].finish_reason:
                            finish_reason = chunk.choices[0].finish_reason
                        if not delta: continue
                        if delta.content:
                            yield {"type": "response_chunk", "content": delta.content}
                    except Exception as chunk_proc_err:
                        logger.error(f"Error processing OpenRouter chunk content: {chunk_proc_err}", exc_info=True)
                        try: raw_chunk_data_for_log = chunk.model_dump_json(); logger.error(f"Raw OpenRouter chunk causing processing error: {raw_chunk_data_for_log}")
                        except Exception: pass
                        yield {"type": "error", "content": f"[OpenRouterProvider Error]: Error processing stream chunk - {type(chunk_proc_err).__name__}", "_exception_obj": chunk_proc_err}
                        return 
            except openai.APIError as stream_api_err:
                logger.error(f"OpenRouter APIError occurred during stream processing: {stream_api_err}", exc_info=True)
                try: logger.error(f"APIError details: Status={stream_api_err.status_code}, Body={stream_api_err.body}")
                except Exception: pass
                yield {"type": "error", "content": f"[OpenRouterProvider Error]: APIError during stream - {stream_api_err}", "_exception_obj": stream_api_err} 
                return 
            logger.debug(f"OpenRouter stream finished processing loop. Finish reason captured: {finish_reason}")
        except Exception as stream_err:
             logger.exception(f"Unexpected Error processing OpenRouter response stream: {stream_err}")
             yield {"type": "error", "content": f"[OpenRouterProvider Error]: Unexpected Error processing stream - {type(stream_err).__name__}", "_exception_obj": stream_err}
        logger.info(f"OpenRouterProvider stream_completion finished for model {model}.")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(base_url='{self.base_url}', client_initialized={bool(self._openai_client)})>"