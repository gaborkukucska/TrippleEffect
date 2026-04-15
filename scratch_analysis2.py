import re
from collections import Counter, defaultdict

LOG = "/home/tom/TrippleEffect/logs/app_20260415_051124_1745709.log"

# 1. State tracking per agent via set_state calls
state_transitions = defaultdict(list)
# 2. Semaphore tracking
semaphore_events = []
# 3. PM activity - what is PM1 doing
pm_actions = []
# 4. Worker progress tracking
worker_tasks_finished = Counter()
# 5. Last 50 lines of log for current state
last_lines = []

with open(LOG, 'r', errors='replace') as f:
    for i, line in enumerate(f, 1):
        # State changes via set_state or State change
        if 'set_state' in line or 'state changed' in line.lower():
            m = re.search(r"[Aa]gent['\s]+(\w+)['\s].*?(?:state changed|set_state).*?(\w+)", line)
            if m:
                ts_m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                ts = ts_m.group(1) if ts_m else "?"
                state_transitions[m.group(1)].append((i, ts, m.group(2)))
        
        # PM1 tool calls
        if 'PM1' in line and ('XML tool call' in line or 'TOOL_EXEC_START' in line or 'native tool_calls' in line):
            ts_m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            ts = ts_m.group(1) if ts_m else "?"
            pm_actions.append((i, ts, line.strip()[:200]))
        
        # Worker task completions
        if 'modify_task' in line and "'task_progress': 'finished'" in line:
            m = re.search(r"agent['\s]+(\w+)", line, re.IGNORECASE)
            if m:
                worker_tasks_finished[m.group(1)] += 1
        
        # Semaphore at limit 0
        if 'Waiting for semaphore (limit 0)' in line:
            ts_m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            ts = ts_m.group(1) if ts_m else "?"
            semaphore_events.append((i, ts))
        
        # Keep last 5 lines for tail
        if i > 862500:
            last_lines.append((i, line.strip()[:200]))

print("=== AGENT STATE TRANSITIONS ===")
for agent in sorted(state_transitions.keys()):
    transitions = state_transitions[agent]
    print(f"\n  {agent}: {len(transitions)} transitions")
    # Show last 5 state changes
    for ln, ts, state in transitions[-5:]:
        print(f"    L{ln} [{ts}]: -> {state}")

print(f"\n=== PM1 ACTIONS (last 15) ===")
for ln, ts, msg in pm_actions[-15:]:
    print(f"  L{ln} [{ts}]: {msg}")

print(f"\n=== WORKER TASKS FINISHED ===")
for worker, count in worker_tasks_finished.most_common():
    print(f"  {worker}: {count} tasks marked finished")

print(f"\n=== SEMAPHORE STARVATION TIMELINE ===")
if semaphore_events:
    print(f"  Total 'limit 0' events: {len(semaphore_events)}")
    print(f"  First: L{semaphore_events[0][0]} [{semaphore_events[0][1]}]")
    print(f"  Last: L{semaphore_events[-1][0]} [{semaphore_events[-1][1]}]")
    # Show clusters
    timestamps = [e[1] for e in semaphore_events]
    print(f"  Last 5 events:")
    for ln, ts in semaphore_events[-5:]:
        print(f"    L{ln} [{ts}]")
else:
    print("  No semaphore starvation events found!")

print(f"\n=== LOG TAIL (very end) ===")
for ln, msg in last_lines[-10:]:
    print(f"  L{ln}: {msg}")
