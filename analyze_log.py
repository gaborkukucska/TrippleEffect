import re
from collections import Counter
import sys

def analyze(log_file):
    print("Analyzing log:", log_file)
    errors = Counter()
    warnings = Counter()
    cg_interventions = Counter()
    cycles = Counter()
    blockages = Counter()
    
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line_str = line.strip()
            
            # Count log levels
            if " - ERROR - " in line_str:
                match = re.search(r' - ERROR - (.*)', line_str)
                if match:
                    # Simplify error signatures for aggregation
                    err = match.group(1)[:100]  # Just take first 100 chars
                    c_err = re.sub(r"'[^']*'", "'*'", err) # erase single quotes strings
                    c_err = re.sub(r"\"[^\"]*\"", "\"*\"", c_err) # erase double quotes strings
                    c_err = re.sub(r"\d+", "N", c_err) # erase numbers
                    errors[c_err] += 1
            if " - WARNING - " in line_str:
                match = re.search(r' - WARNING - (.*)', line_str)
                if match:
                    warn = match.group(1)[:100]
                    c_warn = re.sub(r"'[^']*'", "'*'", warn)
                    c_warn = re.sub(r"\"[^\"]*\"", "\"*\"", c_warn)
                    c_warn = re.sub(r"\d+", "N", c_warn)
                    warnings[c_warn] += 1
                    
            if "ConstitutionalGuardian" in line_str:
                if "BLOCKED agent" in line_str or "Forcing worker_wait" in line_str or "Executing CRITICAL intervention" in line_str:
                    cg_interventions[line_str] += 1
                    
            if "CycleHandler: Finished cycle logic for Agent" in line_str:
                match = re.search(r"Agent '([^']+)'", line_str)
                if match:
                    cycles[match.group(1)] += 1
                    
            if "DUPLICATE BLOCKED" in line_str:
                blockages["DUPLICATE BLOCKED"] += 1
            if "raise ValueError" in line_str or "Exception" in line_str:
                if "ValueError" in line_str:
                    blockages["ValueError Exception"] += 1
                    
    print("\n--- Agent Cycle Counts ---")
    for agent, count in cycles.most_common():
        print(f"  {agent}: {count}")

    print("\n--- Common Errors ---")
    for err, count in errors.most_common(10):
        print(f"  {count}: {err}")
        
    print("\n--- Common Warnings ---")
    for warn, count in warnings.most_common(10):
        print(f"  {count}: {warn}")

    print("\n--- Blockages (Duplicate loops, exceptions) ---")
    for b, count in blockages.most_common():
        print(f"  {count}: {b}")
        
    print("\n--- Constitutional Guardian Signals ---")
    for cg, count in list(cg_interventions.items())[-20:]:  # Print last 20 for context
        print(f"  {count}: {cg}")

if __name__ == "__main__":
    analyze("/home/tom/TrippleEffect/logs/app_20260408_233414_1395575.log")

