import sys
P = "tests/unit/test_round1h.py"
OLD = '''    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
    agent = SimpleNamespace(agent_id="PM1")
    plan = {"roles": ["PM1_Project_Manager", "Project_Manager"]'''
NEW = '''    from unittest.mock import MagicMock
    from src.agents.core import Agent
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
    agent = MagicMock(spec=Agent)
    agent.agent_id = "PM1"
    plan = {"roles": ["PM1_Project_Manager", "Project_Manager"]'''
s = open(P).read()
if s.count(OLD) != 1:
    sys.exit(f"ERROR: expected exactly 1 match, found {s.count(OLD)}")
if "--dry-run" in sys.argv:
    print("[dry-run] would patch", P)
else:
    open(P, "w").write(s.replace(OLD, NEW))
    print("PATCHED ", P)
