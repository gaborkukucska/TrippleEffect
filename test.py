# test_tasklib.py
from pathlib import Path
try:
    from tasklib import TaskWarrior
    TASKLIB_AVAILABLE = True
except ImportError:
    TaskWarrior = None
    TASKLIB_AVAILABLE = False
    print("Tasklib library is NOT installed.")
    exit()

if not TASKLIB_AVAILABLE:
    print("Tasklib not available (already checked, but for completeness).")
    exit()

print(f"Tasklib is available: {TASKLIB_AVAILABLE}")

# Mimic your path construction
BASE_DIR_TEST = Path(__file__).resolve().parent # Assuming test_tasklib.py is in project root
project_name = "TestProjectIsolated"
session_name = "TestSessionIsolated"
data_path = BASE_DIR_TEST / "projects" / project_name / session_name / "task_data"

print(f"Attempting to use data_path: {data_path}")
data_path.mkdir(parents=True, exist_ok=True)
print(f"Ensured directory exists: {data_path}")

taskrc_path = data_path / '.taskrc'
if not taskrc_path.exists():
    try:
        uda_config = "uda.assignee.type=string\nuda.assignee.label=Assignee\n"
        with open(taskrc_path, 'w') as f:
            f.write(uda_config)
        print(f"Created minimal .taskrc at {taskrc_path}")
    except Exception as rc_err:
        print(f"Failed to create .taskrc: {rc_err}")
else:
    print(f".taskrc already exists at {taskrc_path}")

try:
    print(f"Attempting to initialize TaskWarrior with data_location='{str(data_path)}'...")
    tw = TaskWarrior(data_location=str(data_path), taskrc_location=str(taskrc_path)) # Specify taskrc_location
    print(f"TaskWarrior initialized successfully: {tw}")
    print(f"Taskwarrior version detected by tasklib: {tw.version}")
    # Try a simple command
    tasks = tw.tasks.all()
    print(f"Found {len(tasks)} tasks (should be 0 for new setup).")
    print("Test successful!")
except Exception as e:
    print(f"ERROR initializing TaskWarrior or running command: {e}")
    import traceback
    traceback.print_exc()

# Cleanup (optional)
# import shutil
# if data_path.exists():
#     shutil.rmtree(data_path.parent.parent) # Remove TestProjectIsolated
#     print(f"Cleaned up test directory: {data_path.parent.parent}")
