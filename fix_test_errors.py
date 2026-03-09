import re

with open('tests/unit/test_failover_handler.py', 'r') as f:
    failover_content = f.read()

# Fix MagicMock for async get_metrics and get_ranked_models
failover_content = failover_content.replace('get_metrics = MagicMock(', 'get_metrics = AsyncMock(')
# There are multiple places
with open('tests/unit/test_failover_handler.py', 'w') as f:
    f.write(failover_content)

with open('tests/unit/test_agent_workflow_manager.py', 'r') as f:
    awm_content = f.read()

# Fix bootstrap_agents attribute missing
awm_content = awm_content.replace('self.mock_manager.agents = {"test_agent_gp": self.mock_agent}', 'self.mock_manager.agents = {"test_agent_gp": self.mock_agent}\n        self.mock_manager.bootstrap_agents = []')

# Remove local imports
awm_content = awm_content.replace('import src.agents.workflow_manager as wm\n', '')

# Ensure we have ONE import at the top
if 'import src.agents.workflow_manager as wm' not in awm_content:
    awm_content = awm_content.replace('import unittest\n', 'import unittest\nimport src.agents.workflow_manager as wm\n')

with open('tests/unit/test_agent_workflow_manager.py', 'w') as f:
    f.write(awm_content)
