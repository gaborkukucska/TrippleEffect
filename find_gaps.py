import re
from datetime import datetime
import sys

log_file = sys.argv[1]
pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})')
last_time = None
last_line = None

gaps = []

with open(log_file, 'r', errors='ignore') as f:
    for line in f:
        match = pattern.match(line)
        if match:
            time_str = match.group(1)
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S,%f")
                if last_time:
                    delta = (dt - last_time).total_seconds()
                    if delta > 10:  # Gaps larger than 10 seconds
                        gaps.append((delta, time_str, last_line.strip(), line.strip()))
                last_time = dt
                last_line = line
            except ValueError:
                pass

gaps.sort(key=lambda x: x[0], reverse=True)
for gap in gaps[:20]:
    print(f"Gap: {gap[0]} seconds at {gap[1]}")
    print(f"  Before: {gap[2]}")
    print(f"  After:  {gap[3]}")
    print("-" * 40)
