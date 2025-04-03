import pytest
from agents.base_agent import BaseAgent, AgentConfig

@pytest.fixture
def sample_config():
    return AgentConfig(
        name="TestAgent",
        api_config={"provider": "openai"},
        model_params={"temperature": 0.5},
        system_messages=["Test"],
        sandbox={"venv_path": "/test"}
    )

def test_agent_initialization(sample_config):
    agent = BaseAgent(sample_config)
    assert agent.config.name == "TestAgent"
    
def test_message_validation(sample_config):
    agent = BaseAgent(sample_config)
    valid_msg = agent.create_message("Valid")
    assert valid_msg['type'] == 'text'
