import requests
from typing import List, Dict
from ..config.global_settings import Settings

class OpenRouterClient:
    """Client for OpenRouter.ai's unified API gateway"""
    
    def __init__(self, config: dict):
        self.api_key = config['api_key']
        self.base_url = "https://openrouter.ai/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/yourusername/TrippleEffect",
            "X-Title": "TrippleEffect Agent"
        }
        self.available_models = []
        self.settings = Settings()

    def list_models(self) -> List[str]:
        """Get all available models through OpenRouter"""
        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers=self.headers
            )
            response.raise_for_status()
            self.available_models = [m['id'] for m in response.json()['data']]
            return self.available_models
        except Exception as e:
            raise ConnectionError(f"OpenRouter model list failed: {str(e)}")

    def generate(self, prompt: str, params: dict) -> Dict:
        """Generate response through OpenRouter's unified API"""
        if params['model'] not in self.available_models:
            raise ValueError(f"Model {params['model']} not available")

        payload = {
            "model": params['model'],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": params.get('temperature', self.settings.DEFAULT_TEMP),
            "top_p": params.get('top_p', self.settings.DEFAULT_TOP_P),
            "max_tokens": params.get('max_tokens', 2000)
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            return {
                "content": data['choices'][0]['message']['content'],
                "model": data['model'],
                "usage": data['usage']
            }
        except Exception as e:
            raise ConnectionError(f"OpenRouter request failed: {str(e)}")
