import requests
import subprocess
from typing import List, Dict
from ..config.global_settings import Settings

class OllamaClient:
    """Client for local Ollama inference"""
    
    def __init__(self, config: dict):
        self.base_url = "http://localhost:11434/api"
        self.api_key = config.get('api_key', '')
        self.available_models = []
        self.settings = Settings()
        self._verify_installation()

    def _verify_installation(self):
        """Check if Ollama is installed and running"""
        try:
            subprocess.run(["ollama", "--version"], check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise EnvironmentError("Ollama not installed or not in PATH")

    def list_models(self) -> List[str]:
        """List available local models"""
        try:
            response = requests.get(f"{self.base_url}/tags")
            response.raise_for_status()
            self.available_models = [m['name'] for m in response.json()['models']]
            return self.available_models
        except Exception as e:
            raise ConnectionError(f"Ollama model list failed: {str(e)}")

    def generate(self, prompt: str, params: dict) -> Dict:
        """Generate response using local Ollama model"""
        if params['model'] not in self.available_models:
            self._pull_model(params['model'])

        payload = {
            "model": params['model'],
            "prompt": prompt,
            "options": {
                "temperature": params.get('temperature', self.settings.DEFAULT_TEMP),
                "top_p": params.get('top_p', self.settings.DEFAULT_TOP_P),
                "num_predict": params.get('max_tokens', 2000)
            }
        }

        try:
            response = requests.post(
                f"{self.base_url}/generate",
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            
            full_response = ""
            for line in response.iter_lines():
                if line:
                    chunk = json.loads(line)
                    full_response += chunk.get('response', '')
                    
            return {
                "content": full_response,
                "model": params['model'],
                "usage": {"completion_tokens": len(full_response.split())}
            }
        except Exception as e:
            raise ConnectionError(f"Ollama generation failed: {str(e)}")

    def _pull_model(self, model_name: str):
        """Pull model from Ollama library"""
        try:
            subprocess.run(["ollama", "pull", model_name], check=True)
            self.available_models.append(model_name)
        except subprocess.CalledProcessError as e:
            raise ValueError(f"Failed to pull model {model_name}: {str(e)}")
