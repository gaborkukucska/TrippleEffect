# START OF FILE src/llm_providers/vllm_provider.py
import logging
import json
from typing import Optional, List, Dict, Any, AsyncGenerator

from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.base import MessageDict, ToolDict, ToolResultDict

logger = logging.getLogger(__name__)


class VllmProvider(OpenAIProvider):
    """
    LLM Provider implementation for vLLM local servers.
    Inherits streaming, retry logic from OpenAIProvider.

    Key differences from vanilla OpenAI:
    - Native tool calling is DISABLED by default because vLLM requires
      --enable-auto-tool-choice and --tool-call-parser flags at server startup.
      Without those flags, sending tools/tool_choice causes a 400 error.
      The framework falls back to XML-based tool calling automatically.
    - Message history is sanitized to remove internal framework tool_calls/tool
      role messages that don't conform to the strict OpenAI message schema
      that vLLM enforces (causing pydantic validation errors).
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        # Defaults for vLLM if not provided
        if not base_url:
            base_url = "http://localhost:8000/v1"
        if not api_key:
            api_key = "EMPTY"  # Standard placeholder for vLLM when auth is disabled

        logger.info(f"Initializing VllmProvider pointing to {base_url}")

        super().__init__(api_key=api_key, base_url=base_url, **kwargs)

    def _sanitize_messages_for_vllm(self, messages: List[MessageDict]) -> List[MessageDict]:
        """
        Sanitizes the message history to be compatible with vLLM's strict
        OpenAI API validation AND strict chat templates (e.g., Qwen3.5).

        Strict chat templates like Qwen3.5 enforce that system messages may
        ONLY appear as a single contiguous block at the very beginning of the
        conversation. Any system message after a user/assistant message causes
        a 400 BadRequestError.

        This method performs three passes:
        1. Clean: Strip tool_calls from assistant msgs, convert tool role → user
        2. Consolidate: Merge all leading system messages into a single one
        3. Convert: Any remaining mid-conversation system messages → user role
           with a [Framework Directive] prefix to avoid confusion with real
           user messages.
        """
        # --- Pass 1: Clean tool_calls and convert tool role messages ---
        cleaned = []
        for msg in messages:
            role = msg.get("role")

            # Convert 'tool' role to 'user' role - vLLM rejects tool messages
            # that reference tool_call_ids which don't exist (since we strip
            # tool_calls from assistant msgs). Use 'user' not 'system' to
            # avoid creating mid-conversation system messages.
            if role == "tool":
                tool_name = msg.get("name", "unknown_tool")
                tool_content = msg.get("content", "")
                cleaned.append({
                    "role": "user",
                    "content": f"[Tool Result: {tool_name}]\n{tool_content}"
                })
                continue

            # For assistant messages, strip the tool_calls field
            if role == "assistant" and "tool_calls" in msg:
                cleaned_msg = {k: v for k, v in msg.items() if k != "tool_calls"}
                # Ensure content is not empty/None (vLLM rejects null content)
                if not cleaned_msg.get("content"):
                    cleaned_msg["content"] = ""
                cleaned.append(cleaned_msg)
                continue

            # All other messages pass through unchanged
            cleaned.append(msg)

        # --- Pass 2: Consolidate leading system messages into one ---
        # Qwen3.5 (and similar strict templates) only allow system messages
        # at the very beginning. The framework injects multiple system messages
        # (main prompt, health report, workspace tree, etc.) - merge them.
        leading_system_parts = []
        first_non_system_idx = 0
        for i, msg in enumerate(cleaned):
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if content:  # Skip empty system messages
                    leading_system_parts.append(content)
                first_non_system_idx = i + 1
            else:
                break

        remaining = cleaned[first_non_system_idx:]

        # --- Pass 3: Convert any mid-conversation system messages to user ---
        # Framework directives (e.g., "[Framework System Message]", gate blocks,
        # error feedback) are injected as system messages throughout history.
        # Convert them to user role with a clear prefix.
        for msg in remaining:
            if msg.get("role") == "system":
                msg["role"] = "user"
                msg["content"] = f"[Framework Directive]\n{msg.get('content', '')}"

        # --- Rebuild final message list ---
        result = []
        if leading_system_parts:
            result.append({
                "role": "system",
                "content": "\n\n---\n\n".join(leading_system_parts)
            })
        result.extend(remaining)

        # --- Pass 4: Ensure at least one user message exists ---
        # Strict chat templates like Qwen3.5 require at least one 'user'
        # message. Some framework states (e.g., pm_startup, CG review)
        # produce only system messages with no user message at all.
        # Inject a minimal user message to satisfy the template.
        has_user_msg = any(m.get("role") == "user" for m in result)
        if not has_user_msg:
            result.append({
                "role": "user",
                "content": "Begin."
            })
            logger.debug(
                "VllmProvider: Injected synthetic 'Begin.' user message "
                "(no user message found in conversation)"
            )

        # --- Pass 5: Merge consecutive messages of the same role ---
        # Qwen3.5 chat template strictly requires alternating roles (e.g., user -> assistant -> user).
        # We can easily end up with consecutive 'user' messages because of synthetic
        # injections or converted mid-conversation system messages.
        final_result = []
        for msg in result:
            if not final_result:
                final_result.append(msg)
            elif final_result[-1].get("role") == msg.get("role"):
                # Merge with previous message of the same role
                final_result[-1]["content"] = f"{final_result[-1].get('content', '')}\n\n---\n\n{msg.get('content', '')}"
            else:
                final_result.append(msg)

        logger.debug(
            f"VllmProvider: Sanitized messages: {len(messages)} → {len(final_result)} "
            f"(merged {len(leading_system_parts)} leading system msgs into 1, "
            f"converted mid-conversation system msgs to user role, "
            f"merged consecutive same-role msgs)"
        )

        return final_result

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
        Override to:
        1. Strip native tools/tool_choice (vLLM needs --enable-auto-tool-choice)
        2. Sanitize message history (remove internal tool_calls/tool role msgs)

        The framework automatically falls back to XML-based tool calling when
        no native tool calls are returned by the provider.
        """
        if tools:
            logger.info(
                f"VllmProvider: Stripping {len(tools)} native tool schemas from API call. "
                f"vLLM uses XML-based tool calling unless started with --enable-auto-tool-choice."
            )

        # Sanitize messages to remove framework-internal tool tracking
        sanitized_messages = self._sanitize_messages_for_vllm(messages)

        # Call parent with tools=None and tool_choice=None
        async for event in super().stream_completion(
            messages=sanitized_messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=None,         # Force XML tool calling
            tool_choice=None,   # No tool_choice without tools
            **kwargs
        ):
            yield event

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(client_initialized={bool(self._openai_client)})>"

# END OF FILE src/llm_providers/vllm_provider.py
