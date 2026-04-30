with open('/home/tom/TrippleEffect/src/agents/cycle_handler.py', 'r') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "requested_state_match = re.search" in line and "invalid_content" in line:
        lines[i] = '                            requested_state_match = re.search(r"state=[\\\'\\\"]([\w_]+)[\\\'\\\"]", invalid_content)\n'

with open('/home/tom/TrippleEffect/src/agents/cycle_handler.py', 'w') as f:
    f.writelines(lines)
