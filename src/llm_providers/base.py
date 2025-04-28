# START OF FILE src/llm_providers/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncGenerator

# Type definitions matching OpenAI's structure for clarity, can be adjusted
MessageDict = Dict[str, Any]  # e.g., {"role": "user", "content": "..."}
ToolDict = Dict[str, Any]     # Schema for a tool (still useful for describing tools, even if not passed directly)
ToolResultDict = Dict[str, Any] # e.g., {"call_id": "...", "content": "..."} # Used for sending results back TO the generator

class BaseLLMProvider(ABC):
    """
    Abstract Base Class for LLM provider implementations.
    Defines the interface for interacting with different LLM APIs.
    """

    @abstractmethod
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """
        Initializes the provider. Specific implementations will handle
        necessary credentials and endpoint configurations.

        Args:
            api_key (Optional[str]): The API key for the provider, if required.
            base_url (Optional[str]): The base URL for the provider's API endpoint, if required.
            **kwargs: Additional provider-specific arguments.
        """
        pass

    @abstractmethod
    async def stream_completion(
        self,
        messages: List[MessageDict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None, # Added max_tokens parameter
        # Tools/tool_choice are now optional at the base level,
        # as the primary mechanism might be XML parsing by the caller.
        # Implementations can still accept them if they support native tool calls.
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = None,
        # Add other common parameters as needed (e.g., top_p, stop sequences)
        **kwargs # Allow provider-specific parameters
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Streams the LLM completion for the given messages and configuration.

        This is an async generator that yields dictionaries representing events:
        - {'type': 'response_chunk', 'content': '...'} : Regular text streamed from LLM.
              The calling layer (Agent/AgentManager) might need to parse this content
              for XML-formatted tool calls.
        - {'type': 'error', 'content': '...'} : If an error occurs during generation.
        - {'type': 'status', 'content': '...'} : Optional status updates from the provider.
        - {'type': 'tool_requests', ...} : (Potentially yielded by providers that DO support native tool calling,
                                             but the primary method is now XML parsing by the caller).

        It can receive tool results via `generator.asend(list_of_tool_results)`.
        This is used after the *caller* has parsed an XML tool call from the 'response_chunk's,
        executed the tool, and needs to send the result back into the loop if the provider's
        API supports multi-turn tool interactions (like OpenAI's native tool calling).
        For providers where the LLM expects the tool result directly in the next message,
        the caller (AgentManager) will handle appending the result to the message history
        before the *next* call to stream_completion, and this generator might simply finish
        after yielding the response containing the XML tool request.

        Args:
            messages (List[MessageDict]): The conversation history.
            model (str): The specific model to use.
            temperature (float): The sampling temperature.
            max_tokens (Optional[int]): Maximum number of tokens to generate.
            tools (Optional[List[ToolDict]]): Optional: List of tool schemas available (might be used by some providers).
            tool_choice (Optional[str]): Optional: How the model should use tools (might be used by some providers).
            **kwargs: Additional provider-specific parameters for the API call.

        Yields:
            Dict[str, Any]: Events describing the generation process (chunks, errors, status).

        Receives:
            Optional[List[ToolResultDict]]: Optional: Results from executed tool calls sent back via `asend()`
                                            (relevant mainly for providers supporting multi-turn native tool calls).
        """
        # This is an abstract method, implementations will vary.
        # The `yield` below is just to make Python recognize this as an async generator definition.
        # It should never actually be executed in the base class.
        if False: # pragma: no cover
            yield {}
            tool_results = yield {} # Example of receiving send() value
            print(tool_results) # Keep linters happy

    def __repr__(self) -> str:
        """Provides a basic representation of the provider instance."""
        return f"<{self.__class__.__name__}()>"

    # Optional: Add cleanup method if providers need it (e.g., closing sessions)
    async def close_session(self):
        """Optional method to clean up resources like network sessions."""
        pass

# Example Usage (for illustration, not part of the base file):
#
# class MyProvider(BaseLLMProvider):
#     def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
#         # Initialize client, etc.
#         pass
#
#     async def stream_completion(
#         self, messages: List[MessageDict], model: str, temperature: float,
#         max_tokens: Optional[int] = None, tools: Optional[List[ToolDict]] = None, # Keep tools/tool_choice optional
#         tool_choice: Optional[str] = None, **kwargs
#     ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
#         # Make API call WITHOUT necessarily passing tools/tool_choice
#         # ...
#         async for chunk in api_response_stream:
#             # Process chunk (e.g., extract text)
#             yield {"type": "response_chunk", "content": text_chunk}
#         # ... handle completion ...
#
#     async def close_session(self):
#         # Cleanup if needed
#         pass
