# START OF FILE tests/unit/test_round3c.py
"""
Round 3c: the long local run of 2026-10-22 23:12 - 2026-10-23 09:52 (qwen3.5:4b-q4_K_M on Ollama for every agent,
Multiplayer_Snake_Game). 10.5 hours, 224 model calls (about 2 minutes each), and 4 of 7 kick-off tasks finished.

* 23:24:14 the kick-off plan gave dependencies only to the last task: the tests and the README depended on nothing.
* 23:33-00:13 PM1 gave Kick-off Tasks 1, 2, 3, 4 and 5 to W1 and only Task 6 (README) to W2: the activation counted an
  ASSIGNED dependency as met and nothing stopped a second task for a busy worker. W2 finished the README at 00:42 and then
  waited; Task 2 moved to W2 only at 06:25.
* 01:59 PM1 created "Kick-off Task 8: Write README.md ..." although Task 6 had finished the README at 00:42.
* The code was JavaScript, the plan said "pytest": tests/test_*.py were planned, the framework wrote conftest.py, and W1
  spent the night on a Python copy of the game (src/game.py) for pytest to import.
* Reports injected for a call were inserted after the system prompt / before the last messages, so the start of every
  request changed (a local server must then read the whole prompt again); the log had no server timings.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT, SESSION = "Multiplayer_Snake_Game", "startup_1792681922"


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


class Worker:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id


def _manager(*agents):
    team = [a for a in agents if a.agent_type == "worker"]
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        state_manager=types.SimpleNamespace(
            get_agent_team=lambda agent_id: f"team_{PROJECT}",
            get_agents_in_team=lambda team_id: [a for a in agents]))


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    """The project's task store (real Taskwarrior) and a call helper: call(agent_id, manager, **tool_args)."""
    import src.tools.project_management as pmt
    from src.config.settings import settings
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    (tmp_path / "projects" / PROJECT / SESSION / "shared_workspace").mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    return call


def _add(call, manager, text, **extra):
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **extra)
    assert result["status"] == "success", result
    return result["task_uuid"]


# ======================================================================================== 1. one ready task per worker
@needs_tw
def test_a_busy_worker_gets_no_second_ready_task(tasks):
    pm1, w1, w2 = Worker("PM1", "pm", "pm_activate_workers"), Worker("W1"), Worker("W2")
    manager = _manager(pm1, w1, w2)
    t1 = _add(tasks, manager, "Kick-off Task 1: Implement core game logic in src/game.js")
    t2 = _add(tasks, manager, "Kick-off Task 2: Implement player registration in src/player.js")
    t3 = _add(tasks, manager, "Kick-off Task 3: Build the WebSocket sync in src/server.js")
    assert tasks("PM1", manager, action="modify_task", task_id=t1, assignee_agent_id="W1")["status"] == "success"  # 23:33
    refused = tasks("PM1", manager, action="modify_task", task_id=t2, assignee_agent_id="W1")                      # 23:45
    assert refused["status"] == "error" and refused["error_type"] == "busy_worker"
    assert refused["message"].startswith(f"Task '{t2}' was NOT changed: 'W1' already has an open task ('Kick-off Task 1: "
                                         "Implement core game logic in src/game.js', todo, ")
    assert refused["message"].endswith("Give this task to a worker without a task: W2.")
    assert tasks("PM1", manager, action="modify_task", task_id=t2, assignee_agent_id="W2")["status"] == "success"
    full = tasks("PM1", manager, action="modify_task", task_id=t3, assignee_agent_id="W1")
    assert full["status"] == "error" and "All your workers have a task now: leave this task unassigned" in full["message"]
    # a new task for a busy worker is refused the same way
    added = tasks("PM1", manager, action="add_task", description="Write the leaderboard in src/leaderboard.js",
                  project_filter=PROJECT, assignee_agent_id="W2")
    assert added["status"] == "error" and added["message"].startswith("The task was NOT created: 'W2' already has an open task")


@needs_tw
def test_a_waiting_task_may_be_queued_and_others_are_not_checked(tasks):
    pm1, w1, w2 = Worker("PM1", "pm", "pm_manage"), Worker("W1"), Worker("W2")
    manager = _manager(pm1, w1, w2)
    t1 = _add(tasks, manager, "Kick-off Task 1: Implement core game logic in src/game.js")
    t5 = _add(tasks, manager, "Kick-off Task 5: Write tests for the game logic", depends=t1)
    assert tasks("PM1", manager, action="modify_task", task_id=t1, assignee_agent_id="W1")["status"] == "success"
    # Task 5 waits for Task 1: queued for W1, it starts W1 when Task 1 is finished
    assert tasks("PM1", manager, action="modify_task", task_id=t5, assignee_agent_id="W1")["status"] == "success"
    # the framework and workers (sub-tasks of a decomposition) are not checked
    assert tasks("framework", manager, action="add_task", description="Framework task for W1", project_filter=PROJECT,
                 assignee_agent_id="W1")["status"] == "success"
    assert tasks("W1", manager, action="add_task", description="Sub-task: snake movement", project_filter=PROJECT,
                 assignee_agent_id="W1")["status"] == "success"
    # re-assigning the worker's own task is not a second task
    assert tasks("PM1", manager, action="modify_task", task_id=t1, assignee_agent_id="W1")["status"] == "success"


@needs_tw
def test_a_reassigned_task_loses_the_old_assignee_tag(tasks):
    from src.tools.project_management import ProjectManagementTool
    from src.utils.task_utils import task_is_assigned_to
    pm1, w1, w2 = Worker("PM1", "pm", "pm_manage"), Worker("W1"), Worker("W2")
    manager = _manager(pm1, w1, w2)
    t3 = _add(tasks, manager, "Kick-off Task 3: Build the WebSocket sync in src/server.js")
    assert tasks("PM1", manager, action="modify_task", task_id=t3, assignee_agent_id="W2")["status"] == "success"  # 04:02
    assert tasks("PM1", manager, action="modify_task", task_id=t3, assignee_agent_id="W1")["status"] == "success"  # 04:09
    with quiet():
        task = ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION).tasks.get(uuid=t3)
    assert "W2" not in set(task["tags"]) and {"W1", "assigned"} <= set(task["tags"])
    assert task_is_assigned_to(task, "W1") and not task_is_assigned_to(task, "W2")


def test_the_assignee_field_decides_over_old_tags():
    from src.utils.task_utils import task_is_assigned_to
    reassigned = {"assignee": "W1", "tags": {"assigned", "W1", "W2"}}
    assert task_is_assigned_to(reassigned, "W1") and not task_is_assigned_to(reassigned, "W2")
    legacy = {"tags": {"assigned", "W2"}}                                          # no assignee field: the tags count
    assert task_is_assigned_to(legacy, "W2") and not task_is_assigned_to(legacy, "W1")


# ======================================================================================== 2. finished work is not planned again
@needs_tw
def test_the_readme_of_01_59_is_not_planned_again(tasks):
    from src.tools.project_management import ProjectManagementTool
    pm1, w2 = Worker("PM1", "pm", "pm_manage"), Worker("W2")
    manager = _manager(pm1, w2)
    t6 = _add(tasks, manager, "Kick-off Task 6: Write README.md with installation instructions and usage examples",
              tags=["kickoff"])
    with quiet():
        tw = ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
        task = tw.tasks.get(uuid=t6)
        task["task_progress"], task["status"] = "finished", "completed"                     # 00:42
        task.save()
    again = tasks("PM1", manager, action="add_task", title="Kick-off Task 8: Write README.md with installation "
                  "instructions and usage examples", description="Write README.md with installation instructions and "
                  "usage examples", project_filter=PROJECT, assignee_agent_id="W2")          # 01:59
    assert again["status"] == "error" and again["error_type"] == "finished_duplicate"
    assert again["message"].startswith(f"The task was NOT created: the same work is already FINISHED in task {t6} "
                                       "('Kick-off Task 6: Write README.md with installation instructions and usage examples').")
    fix = tasks("PM1", manager, action="add_task", title="Fix README start command",
                description="README.md says 'npm run dev' but package.json has only 'npm start': correct it",
                project_filter=PROJECT)
    assert fix["status"] == "success"
    update = tasks("PM1", manager, action="add_task", description="Update 1 Task 2: Write README.md with installation "
                   "instructions and usage examples", project_filter=PROJECT, tags=["kickoff", "update_1"])
    assert update["status"] == "success"                                          # a new plan's kick-off task


def test_the_task_text_without_labels_and_repeats():
    from src.tools.project_management import task_core_text
    assert task_core_text(None, "Kick-off Task 8: Write README.md: Write README.md") == "Write README.md"
    assert task_core_text("Kick-off Task 8: Write README.md", "Write README.md") == "Write README.md"
    assert task_core_text(None, "Update 2 Task 1: Fix the touch controls") == "Fix the touch controls"
    assert task_core_text("Fix README", "The start command is wrong") == "Fix README: The start command is wrong"


# ======================================================================================== 3. the kick-off plan
KICKOFF_23_24_14 = [
    {"id": "task_1", "description": "Implement core game logic including snake movement, food spawning, collision detection, and game loop in src/game.js"},
    {"id": "task_2", "description": "Implement player registration, naming, and connection handling in src/player.js"},
    {"id": "task_3", "description": "Build WebSocket-based real-time synchronization for multiplayer movement and game state in src/server.js"},
    {"id": "task_4", "description": "Create HTML/CSS/JavaScript interface with player selection, game controls, and leaderboard view in public/index.html"},
    {"id": "task_5", "description": "Write pytest tests for game logic, player management, and leaderboard in tests/"},
    {"id": "task_6", "description": "Write README.md with installation instructions and usage examples"},
    {"id": "task_7", "depends_on": ["task_5", "task_6"], "description": "Run tests, start server, and verify game works with multiple connected players"},
]


def test_tests_and_readme_of_23_24_14_wait_for_the_code():
    import copy
    from src.workflows.pm_kickoff_workflow import infer_missing_dependencies, kickoff_task_kind
    assert [kickoff_task_kind(t["description"]) for t in KICKOFF_23_24_14] == [
        "code", "code", "code", "code", "tests", "docs", "verify"]
    plan = copy.deepcopy(KICKOFF_23_24_14)
    notes = infer_missing_dependencies(plan)
    assert plan[4]["depends_on"] == ["task_1", "task_2", "task_3", "task_4"]
    assert plan[5]["depends_on"] == ["task_1", "task_2", "task_3", "task_4"]
    assert plan[6]["depends_on"] == ["task_5", "task_6"]                                  # the PM's own choice stays
    assert all("depends_on" not in t for t in plan[:4])                                   # the code still runs in parallel
    assert notes == ["'task_5' (tests) had no dependency: it now waits for task_1, task_2, task_3, task_4.",
                     "'task_6' (docs) had no dependency: it now waits for task_1, task_2, task_3, task_4."]


def test_a_verification_task_waits_for_everything_before_it():
    from src.workflows.pm_kickoff_workflow import infer_missing_dependencies, kickoff_task_kind
    plan = [{"id": "a", "description": "Implement src/app.py"}, {"id": "b", "description": "Write tests in tests/test_app.py"},
            {"id": "c", "description": "Verify that the program and the tests work"}]
    infer_missing_dependencies(plan)
    assert plan[1]["depends_on"] == ["a"] and plan[2]["depends_on"] == ["a", "b"]
    # words inside a code task do not make it a test or verification task
    assert kickoff_task_kind("Implement input validation to verify player names in src/player.js") == "code"
    assert kickoff_task_kind("Create the HTML document in public/index.html") == "code"
    first = [{"id": "t", "description": "Write tests for the parser in tests/test_parser.py"}]
    assert infer_missing_dependencies(first) == [] and "depends_on" not in first[0]         # nothing earlier to wait for


def test_python_tests_are_not_planned_for_javascript_code():
    from src.workflows.pm_kickoff_workflow import JS_TEST_LANGUAGE_NOTE, align_test_language
    planned = ["src/game.js", "src/player.js", "src/server.js", "public/index.html", "public/app.js",
               "tests/test_game.py", "tests/test_player.py", "tests/test_leaderboard.py", "docs/README.md"]
    files, note = align_test_language(planned)
    assert files == ["src/game.js", "src/player.js", "src/server.js", "public/index.html", "public/app.js", "docs/README.md"]
    assert note == JS_TEST_LANGUAGE_NOTE and "node --test" in note
    assert align_test_language(["src/app.py", "tests/test_app.py"]) == (["src/app.py", "tests/test_app.py"], None)
    assert align_test_language(["server.py", "static/app.js", "tests/test_server.py"])[1] is None  # Python code too
    assert align_test_language(["src/game.js", "tests/game.test.js"])[1] is None


def test_the_kickoff_of_23_24_14_writes_no_conftest(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    import src.workflows.pm_kickoff_workflow as pkw
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    monkeypatch.setattr(pmt, "TASKLIB_AVAILABLE", False)
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    ws.mkdir(parents=True)
    calls = []

    class Executor:
        async def execute_tool(self, **kwargs):
            calls.append(kwargs["tool_args"])
            return {"status": "success", "task_id": len(calls), "task_uuid": f"00000000-0000-0000-0000-{len(calls):012d}"}

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class PM:
        agent_id, sandbox_path, message_history = "PM1", tmp_path, []
        agent_config = {"config": {"project_name_context": PROJECT}}

        def clear_history(self):
            self.message_history = []
    plan = {"roles": ["Coder", "Technical_Writer"], "tasks": KICKOFF_23_24_14,
            "code_base_definitions": "Tech Stack: HTML/CSS/JavaScript, Node.js/Express/Socket.io. Testing: pytest.",
            "project_structure": [{"dir": "src", "files": ["game.js", "player.js", "server.js"]},
                                  {"dir": "public", "files": ["index.html"]},
                                  {"dir": "tests", "files": ["test_game.py", "test_player.py"]}]}
    manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, tool_executor=Executor())
    with quiet():
        result = run(pkw.PMKickoffWorkflow().execute(manager, PM(), plan))
    assert result.success
    assert not (ws / "conftest.py").exists()
    md = (ws / "PROJECT_STRUCTURE.md").read_text()
    assert "test_game.py" not in md and pkw.JS_TEST_LANGUAGE_NOTE in md
    tests_task = next(c for c in calls if c.get("description", "").startswith("Kick-off Task 5:"))
    assert tests_task["depends"] == "task_1,task_2,task_3,task_4"


# ======================================================================================== 4. the activation
@needs_tw
def test_the_activation_counts_finished_dependencies_and_free_workers(tasks):
    from src.agents.assignable_work import activation_view
    from src.tools.project_management import ProjectManagementTool
    pm1, w1, w2 = Worker("PM1", "pm", "pm_activate_workers"), Worker("W1"), Worker("W2")
    manager = _manager(pm1, w1, w2)
    t1 = _add(tasks, manager, "Kick-off Task 1: Implement core game logic in src/game.js")
    t2 = _add(tasks, manager, "Kick-off Task 2: Implement player registration in src/player.js")
    t5 = _add(tasks, manager, "Kick-off Task 5: Write tests for the game logic", depends=t1)
    assert tasks("PM1", manager, action="modify_task", task_id=t1, assignee_agent_id="W1")["status"] == "success"
    with quiet():
        view = activation_view(manager, pm1, [{"uuid": t2}, {"uuid": t5}])
    assert view == {"blocked": {t5}, "free_workers": ["W2"]}                            # Task 1 is assigned, not finished
    assert tasks("PM1", manager, action="modify_task", task_id=t2, assignee_agent_id="W2")["status"] == "success"
    with quiet():
        assert activation_view(manager, pm1, [{"uuid": t5}])["free_workers"] == []
        task = ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION).tasks.get(uuid=t1)
        task["task_progress"], task["status"] = "finished", "completed"
        task.save()
        assert activation_view(manager, pm1, [{"uuid": t5}]) == {"blocked": set(), "free_workers": ["W1"]}


def test_the_activation_directive_uses_the_view():
    import inspect
    from src.agents import cycle_handler
    source = inspect.getsource(cycle_handler)
    assert "view = activation_view(self._manager, agent, remaining_tasks)" in source
    assert "has_unmet_deps = uuid in view[\"blocked\"]" in source
    assert "Every worker of your team now has a task" in source


# ======================================================================================== 5. a stable start for every request
class StubAssembler:
    @staticmethod
    def make(agent_type="worker", state="worker_work"):
        from src.agents.cycle_components.prompt_assembler import PromptAssembler
        manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION)
        assembler = PromptAssembler(manager)

        async def tree():
            return "[SHARED WORKSPACE TREE (Current State)]\n  |-- game.js"

        async def health(agent):
            return "[Framework Internal Status: System operational.]"
        assembler._generate_workspace_tree_report = tree
        assembler._generate_worker_tasks_report = lambda agent: "[YOUR ASSIGNED TASKS - 1 task(s)]"
        assembler._generate_system_health_report = health
        agent = types.SimpleNamespace(
            agent_id="W1" if agent_type == "worker" else "admin_ai", agent_type=agent_type, state=state,
            message_history=[{"role": "system", "content": "SYSTEM PROMPT"},
                             {"role": "user", "content": "[MESSAGE_ID: m1] [From @PM1]: do Task 1", "message_id": "m1"},
                             {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "file_system", "arguments": {}}]},
                             {"role": "tool", "tool_call_id": "c1", "content": "file listed"}],
            _last_system_prompt_state=(agent_type, state), read_message_ids=set(), active_task_id=None)
        return assembler, agent


def test_injected_reports_follow_the_stored_history():
    assembler, agent = StubAssembler.make()
    context = types.SimpleNamespace(agent=agent)
    with quiet():
        run(assembler.prepare_llm_call_data(context))
    stored = list(agent.message_history)
    sent = context.history_for_call
    assert sent[:len(stored)] == stored                                            # the request starts with the history
    extra = [m["content"].split("\n")[0] for m in sent[len(stored):]]
    assert extra == ["[SHARED WORKSPACE TREE (Current State)]", "[YOUR ASSIGNED TASKS - 1 task(s)]",
                     "[MESSAGE ACKNOWLEDGEMENT SYSTEM]",
                     "[NEW MESSAGE(S) FOR YOU - read this before your next step]"]   # Round 3i: the unread message last
    # the next request (two more messages) starts with everything the previous one stored
    agent.message_history += [{"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "name": "file_system", "arguments": {}}]},
                              {"role": "tool", "tool_call_id": "c2", "content": "file read"}]
    second = types.SimpleNamespace(agent=agent)
    with quiet():
        run(assembler.prepare_llm_call_data(second))
    assert second.history_for_call[:len(stored)] == stored
    assert second.history_for_call[len(stored):len(stored) + 2] == agent.message_history[len(stored):]


def test_admin_health_report_follows_the_history():
    assembler, agent = StubAssembler.make("admin", "admin_conversation")
    context = types.SimpleNamespace(agent=agent)
    with quiet():
        run(assembler.prepare_llm_call_data(context))
    assert context.history_for_call[:4] == agent.message_history
    assert context.history_for_call[4]["content"] == "[Framework Internal Status: System operational.]"


# ======================================================================================== 6. Ollama: timings and the context window
def test_the_server_timings_are_logged(caplog):
    from src.llm_providers.ollama_provider import log_ollama_finish, ollama_timing_text
    data = {"done": True, "done_reason": "stop", "prompt_eval_count": 3097, "eval_count": 94,
            "load_duration": 120_000_000, "prompt_eval_duration": 28_150_000_000, "eval_duration": 23_000_000_000,
            "total_duration": 51_500_000_000, "message": {"content": "", "tool_calls": [{"function": {"name": "x"}}]}}
    assert ollama_timing_text(data) == (", timing: load 0.1s, prompt 3,097 tok in 28.1s (110.0 tok/s), output 94 tok in "
                                        "23.0s (4.1 tok/s), total 51.5s")
    assert ollama_timing_text({"done": True, "eval_count": 5}) == ""
    with caplog.at_level(logging.INFO, logger="src.llm_providers.ollama_provider"):
        log_ollama_finish(data)
    assert any("timing: load 0.1s" in record.getMessage() for record in caplog.records)


def test_a_full_window_is_found_with_a_cached_prompt():
    from src.llm_providers.ollama_provider import is_context_window_full
    assert is_context_window_full("length", 1200, 93, 32768, 4096)                    # most of the prompt was cached
    assert not is_context_window_full("length", 1200, 4096, 32768, 4096)              # its whole output budget
    assert is_context_window_full("length", 32675, 93, 32768, None)                   # no budget known: the sum decides
    assert not is_context_window_full("length", 1200, 93, 32768, None)


# ======================================================================================== 7. the prompts
def test_tests_use_the_language_of_the_code():
    import yaml
    prompts = yaml.safe_load((REPO / "prompts.yaml").read_text())
    planning = prompts["admin_ai_planning_prompt"]
    assert ("Tests are written in the SAME language as the code they test, with that language's standard test tool: pytest "
            "for Python code, Node's built-in test runner (`node --test`) for JavaScript code. Never plan Python tests for "
            "JavaScript code.") in planning
    assert "The tests use the language of the code they test (pytest for Python, `node --test` for JavaScript)" in prompts["pm_startup_prompt"]

# END OF FILE tests/unit/test_round3c.py
