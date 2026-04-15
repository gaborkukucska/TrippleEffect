import re

LOG = "/home/tom/TrippleEffect/logs/app_20260415_051124_1745709.log"

acquired = []
released = []
limit_events = []

with open(LOG, 'r', errors='replace') as f:
    for i, line in enumerate(f, 1):
        if 'Semaphore acquired' in line:
            ts_m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)', line)
            ts = ts_m.group(1) if ts_m else "?"
            acquired.append((i, ts, line.strip()[:200]))
        elif 'Semaphore released' in line:
            ts_m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)', line)
            ts = ts_m.group(1) if ts_m else "?"
            released.append((i, ts, line.strip()[:200]))
        elif 'limit 0' in line and 'semaphore' in line.lower():
            ts_m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)', line)
            ts = ts_m.group(1) if ts_m else "?"
            limit_events.append((i, ts))

print(f"Total semaphore acquired: {len(acquired)}")
print(f"Total semaphore released: {len(released)}")
print(f"Delta (leak): {len(acquired) - len(released)}")
print(f"Total 'limit 0' events: {len(limit_events)}")

# Find when the first limit-0 event happened
if limit_events:
    first_limit0 = limit_events[0]
    print(f"\nFirst 'limit 0': L{first_limit0[0]} [{first_limit0[1]}]")
    
    # Count acquires and releases before that point
    pre_acq = len([a for a in acquired if a[0] < first_limit0[0]])
    pre_rel = len([r for r in released if r[0] < first_limit0[0]])
    print(f"Acquired before first 'limit 0': {pre_acq}")
    print(f"Released before first 'limit 0': {pre_rel}")
    print(f"Leaked before first 'limit 0': {pre_acq - pre_rel}")

# Show last 10 acquired and released
print(f"\nLast 10 acquired:")
for ln, ts, msg in acquired[-10:]:
    print(f"  L{ln} [{ts}]: {msg}")

print(f"\nLast 10 released:")
for ln, ts, msg in released[-10:]:
    print(f"  L{ln} [{ts}]: {msg}")
