# START OF FILE src/agents/cycle_components/context_summarizer.py
"""
Constitutional Guardian Context Summarization System for TrippleEffect Framework

This module provides context summarization functionality when max_tokens limits are hit,
specifically designed to work with small locally hosted LLMs by splitting context into
manageable chunks and using the Constitutional Guardian for summarization.
"""

import logging
import json
from typing import TYPE_CHECKING, List, Dict, Any, Tuple, Optional
import asyncio
from datetime import datetime

from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID

if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

SUMMARY_MAX_TOKENS = 1000
# Round 3f: 2026-10-23 20:53:51 the Ollama stream of W2's 2nd summary stopped after 2,608 characters and the call waited 30
# minutes (the provider's per-chunk timeout) while the whole team stood still. A summary call longer than this is given up
# and the fallback summary is used.
SUMMARY_TIMEOUT_SECONDS = 600.0
SUMMARY_CUT_OFF_NOTE = "\n(This summary was cut off: the model stopped answering.)"
FALLBACK_SUMMARY_MAX_LINES = 30
FALLBACK_TEXT_CHARS = 160

# --- Round 3d: summaries roll on instead of one summary followed by hard truncations ------------------------------------------
# 2026-10-23 (Multiplayer_Snake_Game): every summary adds TWO messages ('[CONTEXT SUMMARY 1/2' and '2/2'), and "2 summaries
# present" stopped all further summaries. So after the FIRST summary of a worker, every later condensation was a hard
# truncation that kept only the last 2 messages and that first, ever older summary (139 "summarization SKIPPED", 32 hard
# truncations); W1 forgot what it had just read and read tests/test_gameLogic.js 50+ times. Now a new summary is made
# whenever enough NEW messages have come in since the last one (the older summary is summarized with the rest); only a
# history that is still too big right after a summary is hard-truncated near the window's end.
SUMMARY_MARKER = "[CONTEXT SUMMARY"
CONDENSED_KEEP_MESSAGES = 6        # messages _create_condensed_history keeps after the summaries
MIN_NEW_MESSAGES_FOR_NEW_SUMMARY = 6


def is_summary_message(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("role") == "system" and SUMMARY_MARKER in str(msg.get("content") or "")


def messages_since_last_summary(history: Optional[List[Dict[str, Any]]]) -> Optional[int]:
    """How many messages follow the newest summary message, or None if the history holds no summary."""
    messages = list(history or [])
    for index in range(len(messages) - 1, -1, -1):
        if is_summary_message(messages[index]):
            return len(messages) - index - 1
    return None


def new_summary_would_help(history: Optional[List[Dict[str, Any]]]) -> bool:
    """
    True if a new LLM summary can shrink the history: there is no summary yet, or enough messages came in after the newest one
    (more than the condensation keeps plus MIN_NEW_MESSAGES_FOR_NEW_SUMMARY). False right after a summary: summarizing again
    would only repeat it.
    """
    since = messages_since_last_summary(history)
    return since is None or since >= CONDENSED_KEEP_MESSAGES + MIN_NEW_MESSAGES_FOR_NEW_SUMMARY


def _short(text: Any, limit: int = FALLBACK_TEXT_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 3] + "..."


def fallback_chunk_summary(chunk: List[Dict[str, Any]]) -> str:
    """
    Round 2p: a summary of a history chunk without an LLM - what the agent called (tool, action, file) and what came back
    (first line of each result), plus the last instruction it was given. Never empty.
    """
    lines: List[str] = []
    for msg in chunk or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                func = call.get("function") if isinstance(call.get("function"), dict) else call
                args = func.get("arguments", call.get("arguments", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (ValueError, TypeError):
                        args = {}
                details = []
                if isinstance(args, dict):
                    for key in ("action", "filename", "path", "task_id", "target_agent_id", "state", "command"):
                        if args.get(key):
                            details.append(f"{key}={_short(args[key], 60)}")
                lines.append(f"- called {func.get('name') or call.get('name') or 'a tool'}({', '.join(details)})")
            if not msg.get("tool_calls") and msg.get("content"):
                lines.append(f"- said: {_short(msg.get('content'))}")
        elif role == "tool":
            first = next((line for line in str(msg.get("content") or "").splitlines() if line.strip()), "")
            lines.append(f"  result: {_short(first)}")
    instructions = [m for m in chunk or [] if isinstance(m, dict) and m.get("role") in ("user", "system")
                    and isinstance(m.get("content"), str) and m["content"].strip()][1:]
    if instructions:
        lines.append(f"- last instruction: {_short(instructions[-1]['content'], 240)}")
    if len(lines) > FALLBACK_SUMMARY_MAX_LINES:
        tail = FALLBACK_SUMMARY_MAX_LINES - 6
        lines = lines[:5] + [f"- ... ({len(lines) - 5 - tail} steps left out) ..."] + lines[-tail:]
    return "Summary of earlier steps (made by the framework):\n" + ("\n".join(lines) if lines else "- no tool calls in this part")


class ContextSummarizer:
    """Handles context summarization when token limits are exceeded."""
    
    def __init__(self, manager: 'AgentManager'):
        """Initialize the context summarizer with agent manager reference."""
        self._manager = manager
        self.max_chunk_size = 8000  # Conservative chunk size for small local LLMs
        self.overlap_size = 200     # Small overlap between chunks
        
    REAL_PROMPT_TRIGGER_RATIO = 0.75  # Round 2n: share of the window the last REAL prompt may fill

    async def should_summarize_context(self, agent_id: str, context_length: int, max_tokens: int,
                                        message_history: Optional[List[Dict[str, Any]]] = None,
                                        model_num_ctx: Optional[int] = None,
                                        real_prompt_tokens: Optional[int] = None,
                                        force: bool = False) -> bool:
        """
        Determine if context should be summarized based on token limits.
        
        Args:
            agent_id: The agent whose context is being checked
            context_length: Current estimated context length in tokens
            max_tokens: Maximum tokens allowed for the model
            message_history: Optional message history to check for existing summaries
            model_num_ctx: Optional model-specific context window size for dynamic threshold (the window the provider
                really uses, e.g. the capped Ollama num_ctx)
            real_prompt_tokens: Round 2n - the prompt size the provider reported for the agent's last call. It includes the
                system prompt and the tool schemas, which the estimate does not count (about 5k tokens for a worker).
            force: Round 2n - the last reply stopped because the context window was FULL.

        Returns:
            True if summarization should be triggered
        """
        from src.config.settings import settings
        if force:
            logger.warning(f"Context summarization FORCED for agent {agent_id}: its last reply found the context window full.")
            return True
        if real_prompt_tokens and model_num_ctx and model_num_ctx > 0 and \
                real_prompt_tokens > int(model_num_ctx * self.REAL_PROMPT_TRIGGER_RATIO):
            # the real prompt is near the window: compare the real size with the limits below
            context_length = max(int(context_length or 0), int(real_prompt_tokens))
            logger.info(f"Context summarizer: last real prompt of agent {agent_id} had {real_prompt_tokens} tokens "
                        f"(> {int(self.REAL_PROMPT_TRIGGER_RATIO * 100)}% of num_ctx={model_num_ctx}).")
        # Dynamic threshold: use 50% of model's context window if available
        if model_num_ctx and model_num_ctx > 0:
            threshold = int(model_num_ctx * 0.5)
            logger.debug(f"Context summarizer: Using dynamic threshold {threshold} "
                        f"(50% of model num_ctx={model_num_ctx}) for agent {agent_id}")
        else:
            threshold = settings.SUMMARIZER_TRIGGER_THRESHOLD
        
        if context_length > threshold:
            # CRITICAL FIX: Prevent infinite summarization loops, but allow EMERGENCY truncation.
            # Round 3d: "loop" = summarizing again right after a summary; once enough new messages came in, summarize again.
            if message_history and not new_summary_would_help(message_history):
                since = messages_since_last_summary(message_history)
                hard_limit = int(model_num_ctx * 0.9) if (model_num_ctx and model_num_ctx > 0) else 29000
                if context_length >= hard_limit:
                    logger.warning(f"CRITICAL: Context {context_length} tokens approaching hard limit {hard_limit} for agent {agent_id}! Forcing emergency hard truncation.")
                    return True
                logger.info(f"Context summarization SKIPPED for agent {agent_id}. "
                            f"Context: {context_length} tokens > Threshold: {threshold}, "
                            f"but only {since} message(s) came after the last summary.")
                return False
            
            logger.info(f"Context summarization triggered for agent {agent_id}. "
                       f"Context: {context_length} tokens, Threshold: {threshold}")
            return True
        
        return False
    
    async def summarize_agent_context(self, agent_id: str, message_history: List[Dict[str, Any]]) -> Tuple[bool, Optional[List[Dict[str, Any]]]]:
        """
        Summarize an agent's context using the Constitutional Guardian.
        
        Args:
            agent_id: The agent whose context needs summarization
            message_history: The agent's message history to summarize
            
        Returns:
            Tuple of (success, summarized_history)
        """
        if not message_history or len(message_history) <= 3:
            logger.debug(f"Agent {agent_id} has minimal history, no summarization needed")
            return False, None
            
        # Don't summarize if Constitutional Guardian doesn't exist
        if CONSTITUTIONAL_GUARDIAN_AGENT_ID not in self._manager.agents:
            logger.warning("Constitutional Guardian not available for context summarization")
            return False, None
            
        # Check if we need to do an emergency hard truncate instead of LLM summarization (Round 3d: only right after a
        # summary - otherwise the history is summarized again, the older summary with it)
        if not new_summary_would_help(message_history):
            logger.warning(f"Performing emergency HARD TRUNCATION for agent {agent_id} to avoid context limit loops.")
            condensed = []
            if message_history and message_history[0].get('role') == 'system' and not is_summary_message(message_history[0]):
                condensed.append(message_history[0])
            
            # Round 3d: only the NEWEST summary (one '1/2' + '2/2' pair), not every older one as well
            summaries = [msg for msg in message_history if is_summary_message(msg)]
            for msg in summaries[-2:]:
                condensed.append(msg)
                    
            if len(message_history) > 2:
                for msg in message_history[-2:]:
                    # Truncate content of preserved messages to prevent single huge messages from blowing up the context
                    msg_copy = dict(msg)
                    content = msg_copy.get('content', '')
                    if isinstance(content, str) and len(content) > 8000:
                        msg_copy['content'] = content[:7900] + "\n...[System: content truncated due to context limit]...\n" + content[-100:]
                    if msg_copy not in condensed:
                        condensed.append(msg_copy)
                        
            return True, condensed
            
        try:
            # Split context into two manageable chunks
            chunk1, chunk2 = self._split_context_into_chunks(message_history)
            
            # Summarize each chunk separately using Constitutional Guardian. Round 2p: a chunk the model returns no summary
            # for gets a summary built from its tool calls and results, so the history is still condensed (an empty
            # summary used to abort the whole summarization, which was then retried - and failed again - every cycle).
            summary1 = await self._summarize_chunk(chunk1, 1, agent_id)
            if not summary1:
                logger.warning("Failed to summarize first context chunk with the LLM; using the framework's summary of it.")
                summary1 = fallback_chunk_summary(chunk1)

            summary2 = await self._summarize_chunk(chunk2, 2, agent_id)
            if not summary2:
                logger.warning("Failed to summarize second context chunk with the LLM; using the framework's summary of it.")
                summary2 = fallback_chunk_summary(chunk2)
            
            # Create new condensed history
            condensed_history = self._create_condensed_history(
                message_history, summary1, summary2
            )
            
            logger.info(f"Successfully summarized context for agent {agent_id}. "
                       f"Original: {len(message_history)} messages, "
                       f"Condensed: {len(condensed_history)} messages")
            
            return True, condensed_history
            
        except Exception as e:
            logger.error(f"Error during context summarization for agent {agent_id}: {e}", exc_info=True)
            return False, None
    
    def _split_context_into_chunks(self, message_history: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split message history into two roughly equal chunks with overlap."""
        if len(message_history) <= 4:
            # Very short history, split in half
            mid_point = len(message_history) // 2
            return message_history[:mid_point + 1], message_history[mid_point:]
        
        # Keep first message (usually system prompt) in both chunks
        system_msg = message_history[0] if message_history[0].get('role') == 'system' else None
        working_history = message_history[1:] if system_msg else message_history
        
        # Split the working history
        mid_point = len(working_history) // 2
        overlap_start = max(0, mid_point - 2)  # Small overlap
        
        chunk1 = [system_msg] if system_msg else []
        chunk1.extend(working_history[:mid_point])
        
        chunk2 = [system_msg] if system_msg else []
        chunk2.extend(working_history[overlap_start:])
        
        return chunk1, chunk2
    
    async def _summarize_chunk(self, chunk: List[Dict[str, Any]], chunk_num: int, original_agent_id: str) -> Optional[str]:
        """
        Summarize a context chunk using the Constitutional Guardian.
        
        Args:
            chunk: The message history chunk to summarize
            chunk_num: Which chunk this is (1 or 2)
            original_agent_id: ID of the original agent whose context is being summarized
            
        Returns:
            Summary text or None if failed
        """
        try:
            cg_agent = self._manager.agents[CONSTITUTIONAL_GUARDIAN_AGENT_ID]
            
            # Create summarization prompt for Constitutional Guardian
            chunk_text = self._format_chunk_for_summarization(chunk)
            
            summarization_prompt = f"""You are being asked to summarize conversation history for agent '{original_agent_id}' to help manage context length for small local LLMs.

Please provide a concise but comprehensive summary of this conversation chunk ({chunk_num}/2) that preserves:
1. Key decisions and actions taken
2. Important context and state changes
3. Tool usage and results
4. Any errors or issues encountered
5. Current progress and next steps

CONVERSATION CHUNK TO SUMMARIZE:
{chunk_text}

Please provide your summary as plain text (no XML tags needed for this summarization task):"""

            # Prepare temporary history for CG
            temp_history = [
                {"role": "system", "content": "You are the Constitutional Guardian. You are being asked to help with context summarization."},
                {"role": "user", "content": summarization_prompt}
            ]
            
            # Temporarily set CG history and get summary
            original_history = cg_agent.message_history
            cg_agent.message_history = temp_history
            from contextlib import aclosing
            
            # Use the CG's LLM provider directly for summarization
            summary_chunks = []
            stream_errors = []
            # Round 2p: thinking off - with thinking on, qwen3.5 spent all 800 tokens thinking and returned no summary 14 times
            # on 2026-10-21 (18:13-18:33), so W1 dragged a 20-26k token history through every cycle.
            from src.llm_providers.ollama_provider import thinking_off_kwargs

            async def read_summary() -> None:
                async with aclosing(cg_agent.llm_provider.stream_completion(
                    model=cg_agent.model,
                    messages=temp_history,
                    temperature=0.3,  # Lower temperature for more focused summaries
                    max_tokens=SUMMARY_MAX_TOKENS,
                    **thinking_off_kwargs(cg_agent.llm_provider)
                )) as stream:
                    async for response_chunk in stream:
                        if isinstance(response_chunk, dict) and response_chunk.get("type") == "response_chunk" and response_chunk.get("content"):
                            summary_chunks.append(response_chunk["content"])
                        elif isinstance(response_chunk, dict) and response_chunk.get("type") == "error":
                            stream_errors.append(str(response_chunk.get("content") or "error"))

            try:
                await asyncio.wait_for(read_summary(), timeout=SUMMARY_TIMEOUT_SECONDS)  # Round 3f
            except asyncio.TimeoutError:
                logger.warning(f"ContextSummarizer: summary {chunk_num}/2 for '{original_agent_id}' took longer than "
                               f"{SUMMARY_TIMEOUT_SECONDS:.0f}s; using the fallback summary.")
                return None
            finally:
                # Restore original CG history (Round 3f: also after an error or a timeout)
                cg_agent.message_history = original_history
            
            summary = ''.join(summary_chunks).strip()
            if summary and stream_errors:
                # Round 3f: a reply that stopped mid-stream is kept, but marked as incomplete
                logger.warning(f"ContextSummarizer: summary {chunk_num}/2 for '{original_agent_id}' was cut off "
                               f"({stream_errors[-1][:120]}).")
                summary += SUMMARY_CUT_OFF_NOTE
            
            if summary:
                logger.debug(f"Generated summary for chunk {chunk_num}: {len(summary)} characters")
                return summary
            else:
                logger.warning(f"Empty summary generated for chunk {chunk_num}")
                return None
                
        except Exception as e:
            logger.error(f"Error summarizing chunk {chunk_num}: {e}", exc_info=True)
            return None
    
    def _format_chunk_for_summarization(self, chunk: List[Dict[str, Any]]) -> str:
        """Format a message chunk for summarization."""
        formatted_messages = []
        
        for msg in chunk:
            role = msg.get('role', 'unknown')
            content = str(msg.get('content') or '')  # Round 3d: None for tool-call messages
            if role == "assistant" and msg.get("tool_calls"):
                # Round 3d: the calls themselves (a tool-call message has no text, so the summary did not know what was done)
                called = [line[len("- called "):] for line in fallback_chunk_summary([msg]).splitlines()
                          if line.startswith("- called ")]
                if called:
                    content = (content + " " if content else "") + "[called " + "; ".join(called) + "]"

            # Truncate very long messages (Round 3d: an earlier summary is kept up to its full size, ~1000 tokens, so a new
            # summary carries it on)
            limit = 4400 if is_summary_message(msg) else 2000
            if len(content) > limit:
                content = content[:limit - 100] + "...[truncated]"
            
            formatted_messages.append(f"[{role.upper()}]: {content}")
        
        return '\n\n'.join(formatted_messages)
    
    def _create_condensed_history(self, original_history: List[Dict[str, Any]], 
                                summary1: str, summary2: str) -> List[Dict[str, Any]]:
        """
        Create a condensed message history that is more robust and less prone to context corruption.
        This simplified logic preserves the system prompt, adds summaries, and keeps the last
        10 messages to ensure immediate context is always maintained.
        """
        condensed = []
        
        # 1. Keep the original system message if it exists
        if original_history and original_history[0].get('role') == 'system':
            condensed.append(original_history[0])
        
        # 2. Add the generated summary messages
        timestamp = datetime.now().isoformat()
        condensed.append({
            "role": "system",
            "content": f"[CONTEXT SUMMARY 1/2 - {timestamp}]\n\n{summary1}"
        })
        condensed.append({
            "role": "system", 
            "content": f"[CONTEXT SUMMARY 2/2 - {timestamp}]\n\n{summary2}"
        })
        
        # 3. Keep the last 6 messages from the original history to preserve immediate context
        # Reduced from 10 to help keep total token count below the summarization threshold
        # and prevent infinite summarize→LLM→summarize loops.
        num_messages_to_keep = 6
        if len(original_history) > num_messages_to_keep:
            recent_history = original_history[-num_messages_to_keep:]
            # Avoid duplicating the system message if it's already in the recent history
            for msg in recent_history:
                if msg not in condensed:
                    condensed.append(msg)
        else:
            # If the history is short, just use the original history (minus system prompt if already added)
            start_index = 1 if (condensed and condensed[0] == original_history[0]) else 0
            for i in range(start_index, len(original_history)):
                if original_history[i] not in condensed:
                    condensed.append(original_history[i])

        logger.info(f"ContextSummarizer: Simplified condensed history created. "
                   f"Original: {len(original_history)} messages, Condensed: {len(condensed)} messages.")
        
        return condensed
    
    def estimate_token_count(self, messages: List[Dict[str, Any]]) -> int:
        """
        Rough estimation of token count for message history.
        Uses a simple heuristic: ~4 characters per token.
        """
        total_chars = 0
        for msg in messages:
            content = msg.get('content') # Get content, which can be None
            if content:
                total_chars += len(content)

            # Also account for tool calls, which add to the context size
            if msg.get("tool_calls"):
                try:
                    total_chars += len(json.dumps(msg["tool_calls"]))
                except (TypeError, ValueError):
                    pass # Ignore if not serializable
        
        # Rough conversion: 4 characters per token
        estimated_tokens = total_chars // 4
        
        # Add overhead for message structure
        overhead = len(messages) * 5  # ~5 tokens overhead per message
        
        return estimated_tokens + overhead

# Global instance for framework use
context_summarizer = None

def get_context_summarizer(manager: 'AgentManager') -> ContextSummarizer:
    """Get or create the global context summarizer instance."""
    global context_summarizer
    if context_summarizer is None:
        context_summarizer = ContextSummarizer(manager)
    return context_summarizer
