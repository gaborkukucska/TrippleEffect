import re

with open('tests/unit/test_failover_handler.py', 'r') as f:
    content = f.read()

# Fix model1_free, etc. to model1_free_large
content = content.replace('self.model1_free[', 'self.model1_free_large[')
content = content.replace('self.model2_paid[', 'self.model2_paid_medium[')
content = content.replace('self.model3_free[', 'self.model3_free_small[')
content = content.replace('self.model4_free[', 'self.model4_free_medium[')

content = content.replace('self.model1_free,', 'self.model1_free_large,')
content = content.replace('self.model2_paid,', 'self.model2_paid_medium,')
content = content.replace('self.model3_free,', 'self.model3_free_small,')
content = content.replace('self.model4_free,', 'self.model4_free_medium,')

content = content.replace('self.local_model_r2', 'self.local_model_m_medium_perf')
content = content.replace('self.local_model_alpha_reg1', 'self.local_model_s_small')
content = content.replace('self.local_model_zeta_r1', 'something else.. wait')

with open('tests/unit/test_failover_handler.py', 'w') as f:
    f.write(content)

with open('tests/unit/test_agent_workflow_manager.py', 'r') as f:
    content2 = f.read()
    
content2 = content2.replace('self.mock_manager.settings.GOVERNANCE_PRINCIPLES', 'wm.settings.GOVERNANCE_PRINCIPLES')
# Make sure wm is imported at the top level
if 'import src.agents.workflow_manager as wm' not in content2:
    content2 = content2.replace('import unittest\n', 'import unittest\nimport src.agents.workflow_manager as wm\n')

with open('tests/unit/test_agent_workflow_manager.py', 'w') as f:
    f.write(content2)
