with open("src/agents/cycle_handler.py", "r") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if line.startswith("                elif event_type in [\"response_chunk\""):
        start_idx = i
        break

for i in range(start_idx, len(lines)):
    if "else: logger.warning(f\"CycleHandler: Unknown event type" in lines[i]:
        end_idx = i + 1
        break

for i in range(start_idx, end_idx):
    lines[i] = "    " + lines[i]

with open("src/agents/cycle_handler.py", "w") as f:
    f.writelines(lines)

print("Fixed indentation.")
