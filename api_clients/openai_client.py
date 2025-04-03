import openai
from typing import List, Dict
from ..config.global_settings import Settings

class OpenAIClient:
    """Managed OpenAI API client with automatic model discovery"""
    
    def __init__(self, config: dict):
        self.client = openai.OpenAI(api_key=config['api_key'])
        self.available_models = []
        self.settings = Settings()
        
    def list_models(self) -> List[str]:
        """Discover available models from API"""
        if not self.available_models:
            try:
                models = self.client.models.list()
                self.available_models = [m.id for m in models.data]
            except Exception as e:
                raise ConnectionError(f"Model discovery failed: {str(e)}")
        return self.available_models
    
    def generate(self, prompt: str, params: dict) -> Dict:
        """Execute generation with safety checks"""
        if params['model'] not in self.available_models:
            raise ValueError(f"Model {params['model']} not available")
            
        response = self.client.chat.completions.create(
            model=params['model'],
            messages=[{"role": "user", "content": prompt}],
            temperature=params.get('temperature', self.settings.DEFAULT_TEMP),
            top_p=params.get('top_p', self.settings.DEFAULT_TOP_P),
            max_tokens=params.get('max_tokens', 2000)
        )
        
        return {
            "content": response.choices[0].message.content,
            "model": response.model,
            "usage": dict(response.usage)
        }
