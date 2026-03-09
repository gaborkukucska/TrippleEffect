import re

files_to_fix = [
    'tests/unit/test_agent_lifecycle.py',
    'tests/unit/test_failover_handler.py',
    'tests/unit/test_model_registry.py'
]

targets = [
    'TestSelectBestAvailableModel',
    'TestHandleAgentModelFailoverInitialModelSelection',
    'TestModelRegistryParameterDiscovery'
]

for filepath in files_to_fix:
    with open(filepath, 'r') as f:
        content = f.read()
    
    for target in targets:
        content = content.replace(f'class {target}(unittest.TestCase):', f'class {target}(unittest.IsolatedAsyncioTestCase):')
        
    with open(filepath, 'w') as f:
        f.write(content)
