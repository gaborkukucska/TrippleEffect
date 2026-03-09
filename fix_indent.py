import re

with open("src/agents/cycle_handler.py", "r") as f:
    lines = f.readlines()

# Goal:
# 1. Find the block from `                    # NEW: Enhanced intervention logic for PM agent stuck in MANAGE state producing only <think>`
#    down to the end of that if block.
# 2. Extract it.
# 3. Un-indent the `elif event_type in ["response_chunk", "status", "final_response", "invalid_state_request_output"]:` 
#    and subsequent `elif` blocks up to the next `if llm_stream_ended_cleanly` block.
# 4. Insert the extracted block into the `if llm_stream_ended_cleanly` block at the end.

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if "This block handles cases where the LLM stream" in line and start_idx == -1:
        start_idx = i
        break

for i in range(start_idx + 1, len(lines)):
    if "elif event_type in [\"response_chunk\"" in lines[i]:
        end_idx = i
        break

extracted = lines[start_idx:end_idx]
del lines[start_idx:end_idx]

# Now the un-indent phase.
# Find the end of the `event_type` handlers.
end_elif_idx = -1
for i in range(start_idx, len(lines)):
    if "This block handles cases where the LLM stream" in lines[i]:
        end_elif_idx = i
        break

for i in range(start_idx, end_elif_idx):
    if lines[i].startswith("    "):
        lines[i] = lines[i][4:]

# Now insert `extracted` after the `if llm_stream_ended_cleanly` check
insert_target_idx = -1
for i in range(end_elif_idx, len(lines)):
    if "Intervention logic for PM agent stuck after team creation" in lines[i]:
        insert_target_idx = i
        break

# We need to strip the wrapping `if llm_stream_ended_cleanly` from `extracted` if we insert it inside an existing one.
# Wait, `extracted` contains:
#                 # This block handles cases...
#                 if llm_stream_ended_cleanly and not context.last_error_obj and not context.action_taken_this_cycle:
#                     # Reset empty response...
#                     ...
# But the target block also has `if llm_stream_ended_cleanly and ...:`
# Let's just insert the body of the exacted block into the target block.

body_to_insert = extracted[2:] # skip comment and `if` statement

lines = lines[:insert_target_idx] + body_to_insert + lines[insert_target_idx:]

with open("src/agents/cycle_handler.py", "w") as f:
    f.writelines(lines)

print("Fix applied.")
