# START OF FILE src/llm_providers/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncGenerator

# Type definitions matching OpenAI's structure for clarity, can be adjusted
MessageDict = Dict[str, Any]  # e.g., {"role": "user", "content": "..."}
ToolDict = Dict[str, Any]     # Schema for a tool
ToolResultDict = Dict[str, Any] # e.g., {"call_id": "...", "content": "..."}

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
        max_tokens: Optional[int] = None, # Added max_tokens
        tools: Optional[List[ToolDict]] = None,
        tool_choice: Optional[str] = "auto", # Typically "auto", "none", or specific tool like {"type": "function", "function": {"name": "my_function"}}
        # Add other common parameters as needed (e.g., top_p, stop sequences)
        **kwargs # Allow provider-specific parameters
    ) -> AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]:
        """
        Streams the LLM completion for the given messages and configuration,
        handling potential tool calls.

        This is an async generator that yields dictionaries representing events:
        - {'type': 'response_chunk', 'content': '...'} : Regular text streamed from LLM.
        - {'type': 'tool_requests', 'calls': [{'id': '...', 'name': '...', 'arguments': {...}}, ...]} :
              When LLM requests one or more tool calls. `arguments` should be a parsed dictionary.
              The provider implementation is responsible for parsing the arguments string from the API if needed.
        - {'type': 'error', 'content': '...'} : If an error occurs during generation.
        - {'type': 'status', 'content': '...'} : Optional status updates from the provider.

        It can receive tool results via `generator.asend(list_of_tool_results)`.
        The `list_of_tool_results` should be: [{'call_id': '...', 'content': '...'}, ...]

        The generator should handle the full interaction loop for tool calls internally
        if the underlying API requires multiple calls (e.g., OpenAI).

        Args:
            messages (List[MessageDict]): The conversation history.
            model (str): The specific model to use.
            temperature (float): The sampling temperature.
            max_tokens (Optional[int]): Maximum number of tokens to generate.
            tools (Optional[List[ToolDict]]): List of tool schemas available.
            tool_choice (Optional[str]): How the model should use tools ("auto", "none", etc.).
            **kwargs: Additional provider-specific parameters for the API call.

        Yields:
            Dict[str, Any]: Events describing the generation process (chunks, tool requests, errors).

        Receives:
            Optional[List[ToolResultDict]]: Results from executed tool calls sent back via `asend()`.
                                            The provider needs to handle sending these back to the LLM API
                                            if required for the next generation step.
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

    # Optional: Add methods for other functionalities like embeddings, classifications, etc.
    # async def get_embeddings(self, text: str, model: str) -> List[float]:
    #     raise NotImplementedError
