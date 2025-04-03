import anthropic
from typing import List, Dict
from ..config.global_settings import Settings

class AnthropicClient:
    """Managed Anthropic API client with model validation"""
    
    def __init__(self, config: dict):
        self.client = anthropic.Anthropic(api_key=config['api_key'])
        self.available_models = ["claude-3-opus-20240229", "claude-3-sonnet-20240229"]
        self.settings = Settings()

    def list_models(self) -> List[str]:
        """Return supported Anthropic models"""
        return self.available_models

    def generate(self, prompt: str, params: dict) -> Dict:
        """Generate response with Claude model"""
        if params['model'] not in self.available_models:
            raise ValueError(f"Model {params['model']} not supported")

        try:
            response = self.client.messages.create(
                model=params['model'],
                max_tokens=params.get('max_tokens', 2000),
                temperature=params.get('temperature', self.settings.DEFAULT_TEMP),
                top_p=params.get('top_p', self.settings.DEFAULT_TOP_P),
                system=params.get('system_message', "You are a helpful assistant."),
                messages=[{"role": "user", "content": prompt}]
            )
            
            return {
                "content": response.content[0].text,
                "model": response.model,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens
                }
            }
        except Exception as e:
            raise ConnectionError(f"Anthropic API error: {str(e)}")
