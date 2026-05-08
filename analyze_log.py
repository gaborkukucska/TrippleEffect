import re
import collections

log_file = '/home/tom/TrippleEffect/logs/app_20260507_224325_9676.log'

errors = []
warnings = collections.Counter()
tool_calls = collections.Counter()
tool_errors = collections.Counter()
agent_actions = collections.defaultdict(list)
framework_interventions = collections.Counter()

error_pattern = re.compile(r'ERROR - (.*)')
warning_pattern = re.compile(r'WARNING - (.*)')
tool_call_pattern = re.compile(r'ToolExecutor.*Executing tool: (\w+)')
tool_error_pattern = re.compile(r'Tool execution failed: (\w+)')
duplicate_pattern = re.compile(r'DUPLICATE BLOCKED')
auto_advance_pattern = re.compile(r'AUTO-ADVANCE')
cg_pattern = re.compile(r'ConstitutionalGuardian')

try:
    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
        for i, line in enumerate(lines):
            if 'ERROR - ' in line:
                errors.append(line.strip())
                # Grab a few lines of context
                for j in range(1, 5):
                    if i+j < len(lines):
                        errors.append(lines[i+j].strip())
            
            w_match = warning_pattern.search(line)
            if w_match:
                warnings[w_match.group(1)[:100]] += 1
                
            tc_match = tool_call_pattern.search(line)
            if tc_match:
                tool_calls[tc_match.group(1)] += 1
                
            te_match = tool_error_pattern.search(line)
            if te_match:
                tool_errors[te_match.group(1)] += 1
                
            if duplicate_pattern.search(line):
                framework_interventions['DUPLICATE BLOCKED'] += 1
                
            if auto_advance_pattern.search(line):
                framework_interventions['AUTO-ADVANCE'] += 1
                
            if cg_pattern.search(line):
                framework_interventions['ConstitutionalGuardian'] += 1

    print("--- Log Analysis ---")
    print(f"Total Errors found: {len(errors)}")
    print(f"Top Warnings: {warnings.most_common(5)}")
    print(f"Tool Calls: {tool_calls.most_common()}")
    print(f"Tool Errors: {tool_errors.most_common()}")
    print(f"Framework Interventions: {framework_interventions.most_common()}")
    print("\n--- First 20 Error lines ---")
    for e in errors[:20]:
        print(e)
except Exception as e:
    print(f"Failed to process: {e}")

