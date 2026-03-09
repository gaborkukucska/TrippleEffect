import re

with open('tests/unit/test_failover_handler.py', 'r') as f:
    failover_content = f.read()

# Fix performance_tracker mock
failover_content = failover_content.replace('self.manager_mock.performance_tracker = AsyncMock()', 'self.manager_mock.performance_tracker = MagicMock()\n        self.manager_mock.performance_tracker.get_all_metrics = MagicMock(return_value={})\n        self.manager_mock.performance_tracker.get_metrics = MagicMock(return_value={})')

# Fix the manual AsyncMock(side_effect=...) back to MagicMock(side_effect=...)
failover_content = failover_content.replace('get_metrics = AsyncMock(', 'get_metrics = MagicMock(')

with open('tests/unit/test_failover_handler.py', 'w') as f:
    f.write(failover_content)
