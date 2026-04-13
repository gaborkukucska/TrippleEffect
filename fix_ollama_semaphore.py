import sys

with open("src/llm_providers/ollama_provider.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_main_block = False
try_depth = 0

start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if "await semaphore.acquire()" in line:
        start_idx = i - 1  # Before the logger.debug statement that prints "Waiting..."
        break

start_try_idx = -1
for i in range(start_idx, len(lines)):
    if lines[i].strip() == "try:":
        start_try_idx = i
        break

end_finally_idx = -1
for i in range(len(lines)-1, -1, -1):
    if "semaphore.release()" in lines[i]:
        end_finally_idx = i - 1 # The finally: line
        break

if start_idx == -1 or start_try_idx == -1 or end_finally_idx == -1:
    print("Could not find markers")
    sys.exit(1)

# we want to modify lines[start_idx:end_finally_idx]

# Remove the 'await semaphore.acquire()'
# Change 'logger.debug(f"OllamaProvider '{model}': Waiting for semaphore (limit {semaphore._value})...")'
# to:
# logger.debug(...)
# async with semaphore:
#     logger.debug(... acquired...)
#     try:

new_lines = lines[:start_idx]

# Find the waiting log line:
wait_line_idx = -1
for i in range(start_idx, start_try_idx):
    if "Waiting for semaphore" in lines[i]:
        wait_line_idx = i
        break

new_lines.extend(lines[start_idx:wait_line_idx+1])
indent = lines[wait_line_idx][:len(lines[wait_line_idx]) - len(lines[wait_line_idx].lstrip())]

new_lines.append(indent + "async with semaphore:\n")
new_lines.append(indent + "    logger.debug(f\"OllamaProvider '{model}': Semaphore acquired!\")\n")
new_lines.append(indent + "    try: \n")

for i in range(start_try_idx+1, end_finally_idx):
    line = lines[i]
    if line == "\n":
        new_lines.append(line)
    else:
        new_lines.append("    " + line)

# After the try block, the outer finally needs to be modified:
# The original outer finally:
#         finally:
#             semaphore.release()
#             logger.debug(f"OllamaProvider '{model}': Semaphore released!")
#             if session and not session.closed:
#                 await session.close()
#                 logger.debug("OllamaProvider: Closed per-request aiohttp ClientSession.")

# We change it to:
#         finally:
#             if session and not session.closed:
#                 await session.close()
#                 logger.debug("OllamaProvider: Closed per-request aiohttp ClientSession.")

new_lines.append(indent + "finally:\n")
# skip semaphore.release() and logger.debug(...)
# only add session close
session_close_lines = lines[end_finally_idx+3 : end_finally_idx+6]
new_lines.extend(session_close_lines)

new_lines.extend(lines[end_finally_idx+6:])

with open("src/llm_providers/ollama_provider.py", "w") as f:
    f.writelines(new_lines)

print("Replaced successfully")
