import re
import sys
from collections import Counter, defaultdict

LOG = "/home/tom/TrippleEffect/logs/app_20260415_051124_1745709.log"

# High-level scan
errors = []
warnings_critical = []
semaphore_issues = []
state_changes = []
duplicate_blocked = []
agent_activity = Counter()
tool_errors = []
task_completions = []
agent_states = defaultdict(list)  # agent -> [(timestamp, state)]

line_count = 0
with open(LOG, 'r', errors='replace') as f:
    for line in f:
        line_count += 1
        
        # Count ERROR level logs (but skip huge ones)
        if ' - ERROR - ' in line and 'DEBUG' not in line:
            errors.append((line_count, line.strip()[:200]))
        
        # Semaphore issues
        if 'semaphore' in line.lower() and ('limit 0' in line or 'limit 1' in line or 'starvation' in line.lower()):
            semaphore_issues.append((line_count, line.strip()[:200]))
        
        # State changes
        m = re.search(r"Agent '(\w+)' state changed.*?-> (\w+)", line)
        if m:
            agent_id, new_state = m.group(1), m.group(2)
            ts_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            ts = ts_match.group(1) if ts_match else "?"
            state_changes.append((line_count, agent_id, new_state, ts))
            agent_states[agent_id].append((ts, new_state))
        
        # Duplicate blocked
        if 'DUPLICATE BLOCKED' in line and 'If you receive' not in line and 'prompt_assembler' not in line and 'ollama_provider' not in line:
            ts_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            ts = ts_match.group(1) if ts_match else "?"
            duplicate_blocked.append((line_count, ts, line.strip()[:150]))
        
        # Tool errors
        if 'Tool Error' in line or "'status': 'error'" in line or '"status": "error"' in line:
            if 'CycleHandler' in line or 'InteractionHandler' in line:
                tool_errors.append((line_count, line.strip()[:200]))
        
        # Task completions
        if 'marked as finished' in line.lower() or "task_progress': 'finished'" in line:
            task_completions.append((line_count, line.strip()[:200]))
        
        # Agent cycle tracking
        m2 = re.search(r"Agent '(\w+)'.*cycle", line, re.IGNORECASE)
        if m2:
            agent_activity[m2.group(1)] += 1

print(f"=== LOG OVERVIEW ===")
print(f"Total lines: {line_count}")
print(f"ERROR-level entries: {len(errors)}")
print(f"Semaphore issues: {len(semaphore_issues)}")
print(f"DUPLICATE BLOCKED events: {len(duplicate_blocked)}")
print(f"Tool errors: {len(tool_errors)}")
print(f"Task completions: {len(task_completions)}")
print(f"State transitions: {len(state_changes)}")

print(f"\n=== ERRORS (last 15) ===")
for ln, msg in errors[-15:]:
    print(f"  L{ln}: {msg}")

print(f"\n=== SEMAPHORE ISSUES (last 10) ===")
for ln, msg in semaphore_issues[-10:]:
    print(f"  L{ln}: {msg}")

print(f"\n=== DUPLICATE BLOCKED (last 10) ===")
for ln, ts, msg in duplicate_blocked[-10:]:
    print(f"  L{ln} [{ts}]: {msg}")

print(f"\n=== AGENT STATE SUMMARY ===")
for agent_id in sorted(agent_states.keys()):
    transitions = agent_states[agent_id]
    last_state = transitions[-1] if transitions else ("?", "?")
    print(f"  {agent_id}: {len(transitions)} transitions, last={last_state[1]} @ {last_state[0]}")

print(f"\n=== TASK COMPLETIONS (last 10) ===")
for ln, msg in task_completions[-10:]:
    print(f"  L{ln}: {msg}")

print(f"\n=== AGENT CYCLE ACTIVITY (top 10) ===")
for agent, count in agent_activity.most_common(10):
    print(f"  {agent}: {count} cycle-related log lines")
