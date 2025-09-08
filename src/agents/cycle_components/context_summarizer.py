# START OF FILE src/agents/cycle_components/context_summarizer.py
"""
Constitutional Guardian Context Summarization System for TrippleEffect Framework

This module provides context summarization functionality when max_tokens limits are hit,
specifically designed to work with small locally hosted LLMs by splitting context into
manageable chunks and using the Constitutional Guardian for summarization.
"""

import logging
from typing import TYPE_CHECKING, List, Dict, Any, Tuple, Optional
import asyncio
from datetime import datetime

from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID

if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

class ContextSummarizer:
    """Handles context summarization when token limits are exceeded."""
    
    def __init__(self, manager: 'AgentManager'):
        """Initialize the context summarizer with agent manager reference."""
        self._manager = manager
        self.max_chunk_size = 8000  # Conservative chunk size for small local LLMs
        self.overlap_size = 200     # Small overlap between chunks
        
    async def should_summarize_context(self, agent_id: str, context_length: int, max_tokens: int) -> bool:
        """
        Determine if context should be summarized based on token limits.
        
        Args:
            agent_id: The agent whose context is being checked
            context_length: Current estimated context length in tokens
            max_tokens: Maximum tokens allowed for the model
            
        Returns:
            True if summarization should be triggered
        """
        # Trigger summarization at 80% of max tokens to leave room for response
        threshold = max_tokens * 0.8
        
        if context_length > threshold:
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
            
        try:
            # Split context into two manageable chunks
            chunk1, chunk2 = self._split_context_into_chunks(message_history)
            
            # Summarize each chunk separately using Constitutional Guardian
            summary1 = await self._summarize_chunk(chunk1, 1, agent_id)
            if not summary1:
                logger.error("Failed to summarize first context chunk")
                return False, None
                
            summary2 = await self._summarize_chunk(chunk2, 2, agent_id) 
            if not summary2:
                logger.error("Failed to summarize second context chunk")
                return False, None
            
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
            
            # Use the CG's LLM provider directly for summarization
            summary_chunks = []
            async for chunk in cg_agent.llm_provider.stream_completion(
                model=cg_agent.model,
                messages=temp_history,
                temperature=0.3,  # Lower temperature for more focused summaries
                max_tokens=800    # Limit summary length
            ):
                if chunk and hasattr(chunk, 'content') and chunk.content:
                    summary_chunks.append(chunk.content)
            
            # Restore original CG history
            cg_agent.message_history = original_history
            
            summary = ''.join(summary_chunks).strip()
            
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
            content = msg.get('content', '')
            
            # Truncate very long messages
            if len(content) > 2000:
                content = content[:1900] + "...[truncated]"
            
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
        
        # 3. Keep the last 10 messages from the original history to preserve immediate context
        # This is a much safer approach than trying to selectively filter messages.
        num_messages_to_keep = 10
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
            content = msg.get('content', '')
            total_chars += len(content)
        
        # Rough conversion: 4 characters per token
        estimated_tokens = total_chars // 4
        
        # Add overhead for message structure
        overhead = len(messages) * 50  # ~50 tokens overhead per message
        
        return estimated_tokens + overhead

# Global instance for framework use
context_summarizer = None

def get_context_summarizer(manager: 'AgentManager') -> ContextSummarizer:
    """Get or create the global context summarizer instance."""
    global context_summarizer
    if context_summarizer is None:
        context_summarizer = ContextSummarizer(manager)
    return context_summarizer
