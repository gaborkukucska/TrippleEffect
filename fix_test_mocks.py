import re

with open('tests/unit/test_model_registry.py', 'r') as f:
    content = f.read()

# Fix await _apply_filters()
content = content.replace('await self.model_registry._apply_filters()', 'self.model_registry._apply_filters()')

# Fix OPENAI_BASE_URL missing
content = content.replace('self.mock_settings.OLLAMA_PROXY_SERVER = None', 'self.mock_settings.OLLAMA_PROXY_SERVER = None\n        self.mock_settings.OPENAI_BASE_URL = None')

with open('tests/unit/test_model_registry.py', 'w') as f:
    f.write(content)

with open('tests/unit/test_failover_handler.py', 'r') as f:
    failover = f.read()

# Fix MAX_FAILOVER_ATTEMPTS missing
failover = failover.replace('mock_settings.MODEL_TIER =', 'mock_settings.MAX_FAILOVER_ATTEMPTS = 5\n        mock_settings.MODEL_TIER =')
failover = failover.replace('self.mock_settings.MODEL_TIER =', 'self.mock_settings.MAX_FAILOVER_ATTEMPTS = 5\n        self.mock_settings.MODEL_TIER =')

with open('tests/unit/test_failover_handler.py', 'w') as f:
    f.write(failover)

