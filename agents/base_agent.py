import logging
import json
from abc import ABC, abstractmethod
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, ValidationError, Field, validator
from queue import PriorityQueue
from ..sandbox.venv_manager import VenvManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AgentConfig(BaseModel):
    """Pydantic model for agent configuration validation"""
    name: str = Field(..., min_length=3, max_length=20)
    api_config: Dict[str, Any]
    model_params: Dict[str, float]
    system_messages: List[str]
    sandbox: Dict[str, str]
    communication_priority: str = 'balanced'
    
    @validator('model_params')
    def validate_model_params(cls, v):
        required = {'temperature', 'top_p', 'max_tokens'}
        if not required.issubset(v.keys()):
            raise ValueError(f"Model params must contain {required}")
        return v

class BaseAgent(ABC):
    """Abstract base class for all LLM agents"""
    
    def __init__(self, config: AgentConfig, db_session):
        self.config = config
        self.db_session = db_session
        self.message_queue = PriorityQueue()
        self.api_client = self._setup_api_client()
        self.system_message_chain = self._build_system_messages()
        self.venv = VenvManager(config.sandbox['venv_path'])
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.state = {
            'active': False,
            'last_used': datetime.now(),
            'resource_usage': {'memory': 0, 'cpu': 0}
        }

    def _setup_api_client(self):
        """Factory method for API clients"""
        provider = self.config.api_config.get('provider')
        
        if provider == 'openai':
            from ..api_clients.openai_client import OpenAIClient
            return OpenAIClient(self.config.api_config)
            
        elif provider == 'anthropic':
            from ..api_clients.anthropic_client import AnthropicClient
            return AnthropicClient(self.config.api_config)
            
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _build_system_messages(self):
        """Compile system messages with fallback structure"""
        return [
            {"role": "system", "content": msg}
            for msg in self.config.system_messages
        ]

    @abstractmethod
    def process_message(self, message: Dict) -> Dict:
        """Main message processing pipeline (to be implemented)"""
        pass

    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Generate response from LLM (to be implemented)"""
        pass

    def create_message(self, content: str, msg_type: str = 'text') -> Dict:
        """Structure messages with metadata"""
        return {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'content': content,
            'type': msg_type,
            'sender': self.config.name,
            'priority': self._calculate_priority(msg_type)
        }

    def send_message(self, message: Dict, require_ack: bool = False):
        """Asynchronous message dispatch with validation"""
        try:
            validated = AgentMessage(**message)
            self.message_queue.put((validated['priority'], validated))
            self.executor.submit(self._process_message_async, validated)
            
            if require_ack:
                return {'status': 'queued', 'message_id': validated['id']}
                
        except ValidationError as e:
            logger.error(f"Invalid message format: {str(e)}")
            return {'error': str(e)}

    def register_tool(self, tool_code: str):
        """Register new tool in sandbox environment"""
        try:
            tool_id = self.venv.create_tool(tool_code)
            self.config.tools.append(tool_id)
            self._update_agent_state()
            return {'tool_id': tool_id}
        except ToolCreationError as e:
            logger.error(f"Tool registration failed: {str(e)}")
            return {'error': str(e)}

    def execute_tool(self, tool_id: str, args: Dict):
        """Execute registered tool in sandbox"""
        if tool_id not in self.config.tools:
            return {'error': 'Tool not registered'}
            
        result = self.venv.execute_tool(tool_id, args)
        self._log_activity('tool_execution', result)
        return result

    def _update_agent_state(self):
        """Update resource monitoring metrics"""
        self.state['resource_usage'] = {
            'memory': self.venv.get_memory_usage(),
            'cpu': self.venv.get_cpu_usage()
        }
        self.state['last_used'] = datetime.now()

    def _log_activity(self, event_type: str, data: Dict):
        """Persistent activity logging"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'agent': self.config.name,
            'event_type': event_type,
            'data': data
        }
        self.db_session.add(ActivityLog(**log_entry))
        self.db_session.commit()

    def __enter__(self):
        self.state['active'] = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.state['active'] = False
        self.executor.shutdown(wait=False)
        self.db_session.close()

class AgentMessage(BaseModel):
    """Standardized message format for inter-agent communication"""
    sender: str
    content: str
    recipients: List[str] = ['all']
    priority: int = Field(ge=1, le=5)
    context: Dict[str, Any] = {}
    require_response: bool = False
