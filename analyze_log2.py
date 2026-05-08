import json

log_file = '/home/tom/TrippleEffect/logs/app_20260507_224325_9676.log'

tool_executions = {}
agent_states = {}

try:
    with open(log_file, 'r', encoding='utf-8') as f:
        for _ in range(500000): # Limit to avoid taking forever if needed, but we can process whole file
            line = f.readline()
            if not line:
                break
            
            if 'Executing tool' in line or 'tool' in line.lower() and 'execut' in line.lower():
                if 'DEBUG' in line or 'INFO' in line:
                    pass
            
            if 'InteractionHandler: Executing tool' in line:
                print("Found tool exec line:", line.strip())
                break
            elif 'InteractionHandler' in line and 'tool' in line.lower():
                print("Found interaction handler tool line:", line.strip())
                break
            elif 'ToolExecutor' in line and 'tool' in line.lower():
                print("Found ToolExecutor line:", line.strip())
                break
                
except Exception as e:
    pass

