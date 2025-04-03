import json
import threading
import logging
from queue import PriorityQueue
from typing import Dict, List, Optional
from sqlalchemy.orm import scoped_session
from .base_agent import BaseAgent, AgentConfig, AgentMessage
from ..api_clients import APIClientFactory
from ..sandbox.security_layer import SecurityPolicy

class AgentManager:
    """Orchestration system for managing agent collaboration"""
    
    def __init__(self, db_session: scoped_session):
        self.db = db_session
        self.lock = threading.RLock()
        self.agents: Dict[str, BaseAgent] = {}
        self.message_queue = PriorityQueue()
        self.security = SecurityPolicy()
        self.active_requests = {}

    def load_agents(self):
        """Initialize agents from database configuration"""
        with self.lock:
            agent_configs = self.db.execute(
                "SELECT config_json FROM agents WHERE active = 1"
            ).fetchall()
            
            for config in agent_configs:
                try:
                    agent_config = AgentConfig(**json.loads(config[0]))
                    self._instantiate_agent(agent_config)
                except ValidationError as e:
                    logging.error(f"Invalid agent config: {str(e)}")
                    
            logging.info(f"Loaded {len(self.agents)} active agents")

    def _instantiate_agent(self, config: AgentConfig):
        """Create agent instance with security checks"""
        if len(self.agents) >= 3:
            raise RuntimeError("Maximum 3 agents allowed")
            
        self.security.validate_config(config)
        api_client = APIClientFactory.create_client(config.api_config)
        agent = BaseAgent(config, self.db, api_client)
        
        # Initialize sandbox
        agent.venv.create_venv(config.name)
        agent.venv.apply_security_policy(self.security.default_policy)
        
        self.agents[config.name] = agent
        logging.info(f"Agent {config.name} initialized")

    def dispatch_request(self, prompt: str, files: List[str] = None) -> Dict:
        """Coordinate multi-agent processing of user request"""
        request_id = self._generate_request_id()
        self.active_requests[request_id] = {
            'status': 'processing',
            'agents': list(self.agents.keys()),
            'results': []
        }
        
        # Distribute request using round-robin strategy
        for agent in self.agents.values():
            agent_thread = threading.Thread(
                target=self._process_agent_task,
                args=(agent, prompt, files, request_id)
            )
            agent_thread.start()
            
        return request_id

    def _process_agent_task(self, agent: BaseAgent, prompt: str, files: List[str], request_id: str):
        """Thread-safe task processing with resource limits"""
        try:
            with agent:
                # 1. Process main prompt
                response = agent.generate_response(prompt)
                
                # 2. Handle file attachments
                if files:
                    file_analysis = agent.process_files(files)
                    response['files'] = file_analysis
                
                # 3. Handle inter-agent communication
                if agent.config.communication_priority != 'isolated':
                    self._handle_agent_comms(agent, response)
                
                # Update request tracking
                self.active_requests[request_id]['results'].append({
                    'agent': agent.config.name,
                    'response': response
                })
                
        except Exception as e:
            logging.error(f"Agent {agent.config.name} failed: {str(e)}")
            self.active_requests[request_id]['results'].append({
                'agent': agent.config.name,
                'error': str(e)
            })

    def _handle_agent_comms(self, sender: BaseAgent, message: Dict):
        """Route messages between agents"""
        with self.lock:
            for recipient in self.agents.values():
                if recipient.config.name != sender.config.name:
                    priority = self._calculate_message_priority(
                        sender.config.communication_priority,
                        recipient.config.communication_priority
                    )
                    msg_obj = AgentMessage(
                        sender=sender.config.name,
                        content=message,
                        recipients=[recipient.config.name],
                        priority=priority
                    )
                    self.message_queue.put(msg_obj)
                    recipient.receive_message(msg_obj)

    def get_agent_status(self) -> List[Dict]:
        """Return current status of all agents"""
        return [{
            'name': agent.config.name,
            'state': agent.state,
            'models': agent.api_client.list_models(),
            'tools': len(agent.config.tools)
        } for agent in self.agents.values()]

    def _generate_request_id(self) -> str:
        """Create unique request identifier"""
        return f"REQ_{int(time.time() * 1000)}"

    def _calculate_message_priority(self, sender_priority: str, receiver_priority: str) -> int:
        """Determine message priority using matrix"""
        priority_matrix = {
            'high': {'high': 1, 'balanced': 2, 'low': 3},
            'balanced': {'high': 2, 'balanced': 3, 'low': 4},
            'low': {'high': 3, 'balanced': 4, 'low': 5}
        }
        return priority_matrix[sender_priority][receiver_priority]

    def shutdown(self):
        """Gracefully terminate all agents"""
        with self.lock:
            for agent in self.agents.values():
                agent.__exit__(None, None, None)
            self.db.remove()
