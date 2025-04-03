from .openai_client import OpenAIClient
from .anthropic_client import AnthropicClient
from .openrouter_client import OpenRouterClient
from .ollama_client import OllamaClient

class APIClientFactory:
    """Factory class for creating API clients"""
    
    @staticmethod
    def create_client(config: dict):
        provider = config.get('provider', '').lower()
        
        if provider == 'openai':
            return OpenAIClient(config)
        elif provider == 'anthropic':
            return AnthropicClient(config)
        elif provider == 'openrouter':
            return OpenRouterClient(config)
        elif provider == 'ollama':
            return OllamaClient(config)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
