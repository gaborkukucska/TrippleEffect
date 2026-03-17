import json

with open("prompts.json", "r") as f:
    prompts = json.load(f)

# Update pm_manage_prompt
old_text = "4.  **Assign New Work:** Are there any 'pending' tasks that have not been assigned? If yes, and you have available workers, assign one task using `<project_management><action>modify_task</action>...</project_management>`."

new_text = "4.  **Assign New Work:** Are there any 'pending' tasks that have not been assigned? If yes, and you have available workers, assign one task using `<project_management><action>modify_task</action>...</project_management>`. DO NOT repeatedly assign the same task to the same agent. If a task is already assigned and pending or in progress, you MUST WAIT for the agent to complete it."

if old_text in prompts["pm_manage_prompt"]:
    prompts["pm_manage_prompt"] = prompts["pm_manage_prompt"].replace(old_text, new_text)
    
    with open("prompts.json", "w") as f:
        json.dump(prompts, f, indent=2)
    print("Success: Updated pm_manage_prompt.")
else:
    print("Error: Could not find target text in pm_manage_prompt.")
