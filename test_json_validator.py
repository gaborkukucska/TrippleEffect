import asyncio
import json
from src.agents.cycle_components.json_validator import JSONValidator
from src.agents.agent_tool_parser import _parse_tool_call_json_blocks

val = JSONValidator()

def test_validator():
    malformed_json = '{"action": "list_tools", "tool_name": "test"}'
    
    # Test valid
    res = val.validate_json(malformed_json)
    print("Valid JSON Test:", res['is_valid'])
    
    # Test missing quote recovery
    bad_json = "{'action': 'list_tools'}"
    res2 = val.recover_json(bad_json)
    print("Recover JSON Test:", res2['success'])
    
    print("Done")

if __name__ == "__main__":
    test_validator()
