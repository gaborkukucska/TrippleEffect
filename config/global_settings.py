import os
from pathlib import Path
from pydantic import BaseSettings, Field

class Settings(BaseSettings):
    BASE_DIR: Path = Path(__file__).parent.parent
    MAX_FILE_SIZE: int = 10_000_000  # 10MB
    ALLOWED_FILE_TYPES: list = ['text/plain', 'application/json']
    AGENT_LIMIT: int = 3
    DEFAULT_TEMP: float = 0.7
    DEFAULT_TOP_P: float = 0.9
    SANDBOX_BASE: Path = BASE_DIR / 'sandbox'
    REQUEST_TIMEOUT: int = 300  # seconds
    
    class Config:
        env_file = BASE_DIR / '.env'

class AgentConfigTemplate(BaseSettings):
    name: str = Field(..., min_length=3, max_length=20)
    description: str = Field(..., max_length=100)
    system_messages: list = Field(
        default=["You are an intelligent assistant"],
        max_items=5
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    max_tokens: int = Field(default=2000, ge=100, le=4000)
    sandbox_size: str = '500MB'
    allow_network: bool = False
    max_tools: int = 10
    
    class Config:
        schema_extra = {
            "example": {
                "name": "CodeExpert",
                "description": "Specializes in Python development",
                "system_messages": [
                    "You are a senior Python developer",
                    "Always write PEP8 compliant code"
                ],
                "temperature": 0.2,
                "top_p": 0.95,
                "max_tokens": 3000
            }
        }
