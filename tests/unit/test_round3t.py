# START OF FILE tests/unit/test_round3t.py
"""
Round 3t: the run of 2026-10-26 12:18-15:47 (WiFi_Snake_Game, Python, qwen3.5:4b-q4_K_M on a remote Ollama).

* 13:16-14:51 PM1 rewrote "Kick-off Task 7: Write pytest tests for game logic, player management, and leaderboard in tests/"
  four times, last into "Examine src/game_logic.py ... No test runs required." - which W2 marked finished.
* 13:30-14:12 PM1 tried to fix src/leaderboard.py and src/game_logic.py itself 9 times (refused), then gave W2 - whose
  task did not name those files - the fix of W1's src/game_logic.py; W2 was refused and bounced between worker_work,
  worker_report and worker_wait for 90 minutes.
* src/leaderboard.py stored its entries in src/leaderboard.json (beside the module) while reset_leaderboard() deleted
  'leaderboard.json' in the folder the tests ran in: all 19 full test runs failed, the tests saw each other's entries.
* 17 context summaries stopped at the 1000-token limit in the middle of a sentence; the prompt asked for "Current progress
  and next steps" last.
"""
import asyncio
import logging
import os
import shutil
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1792988304"

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids = [], {}
        self.agent_config = {"config": {"project_name_context": PROJECT}}


def _manager(*agents):
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        state_manager=types.SimpleNamespace(get_agent_team=lambda agent_id: f"team_{PROJECT}",
                                            get_agents_in_team=lambda team_id: list(agents)))


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    from src.config.settings import settings
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    call.workspace = workspace
    return call


def _add(call, manager, text, assignee=None, tags=None, caller="framework"):
    args = {"assignee_agent_id": assignee} if assignee else {}
    if tags:
        args["tags"] = tags
    added = call(caller, manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_7 = "Kick-off Task 7: Write pytest tests for game logic, player management, and leaderboard in tests/"


# ======================================================================================== 1. the plan's task text stays
@needs_tw
def test_a_planned_task_keeps_its_text(tasks):
    pm1, w2 = Agent("PM1", "pm", "pm_report_check"), Agent("W2")
    manager = _manager(pm1, w2)
    t7 = _add(tasks, manager, TASK_7, "W2", tags=["kickoff", "pm_decomposed", "task_order_7"], caller="PM1")
    # 14:51:50: replaced by other work - refused, the text stays
    answer = tasks("PM1", manager, action="modify_task", task_id=t7, description=(
        "Examine src/game_logic.py to verify GRID_WIDTH and GRID_HEIGHT are in __all__. If not, add them. Task is complete "
        "when the file is reviewed and no export changes are needed. No test runs required."))
    assert answer["status"] == "error" and answer["error_type"] == "planned_task"
    assert answer["message"].startswith(
        f"Task '{t7}' is a planned task of the approved plan ('Kick-off Task 7: Write pytest tests for game logic, player "
        "management, and leaderboard in tests/') and its text was NOT replaced: a new description would erase this "
        "planned work (and the checks that belong to it, such as the passing test run a test task needs). For other work - "
        "for example a fix in a file of another worker - create a NEW task with action 'add_task'")
    listed = tasks("PM1", manager, action="list_tasks", project_filter=PROJECT)["tasks"]
    assert next(t for t in listed if t["uuid"] == t7)["description"] == TASK_7
    # details added after the planned text are fine
    longer = TASK_7 + " - Note: src/leaderboard.py stores its data in src/leaderboard.json; give the tests a tmp file."
    assert tasks("PM1", manager, action="modify_task", task_id=t7, description=longer)["status"] == "success"
    # other tasks can be reworded as before; the framework may change planned tasks
    other = _add(tasks, manager, "Fix the leaderboard reset in src/leaderboard.py", caller="PM1")
    assert tasks("PM1", manager, action="modify_task", task_id=other,
                 description="Fix reset_leaderboard() in src/leaderboard.py")["status"] == "success"
    assert tasks("framework", manager, action="modify_task", task_id=t7,
                 description="Kick-off Task 7: Write the tests")["status"] == "success"


def test_what_counts_as_a_planned_task():
    from src.tools.project_management import planned_task_rewrite_error, _is_planned_task
    task = {"description": TASK_7, "tags": ["kickoff"], "uuid": "u7"}
    assert _is_planned_task(task)
    assert _is_planned_task({"description": "Update 2 Task 1: Add sound effects to src/game.js", "tags": []})
    assert not _is_planned_task({"description": "Fix the score display in src/ui.js", "tags": ["assigned"]})
    assert planned_task_rewrite_error(task, "u7", {"description": "Something else"}, "PM1")["error_type"] == "planned_task"
    assert planned_task_rewrite_error(task, "u7", {"task_progress": "finished"}, "PM1") is None   # no new text
    assert planned_task_rewrite_error(task, "u7", {"description": ""}, "PM1") is None
    # the text without its "Kick-off Task 7:" label counts as kept
    assert planned_task_rewrite_error(task, "u7", {"description": "Write pytest tests for game logic, player management, "
                                                                 "and leaderboard in tests/ (use tmp_path)"}, "PM1") is None


# ======================================================================================== 2. who owns the file a PM wanted to fix
@needs_tw
def test_a_pms_refused_fix_names_the_owner_of_the_file(tasks):
    from src.tools.project_management import file_owner_note
    from src.tools.executor import ToolExecutor
    pm1, w1, w2, w4 = Agent("PM1", "pm", "pm_report_check"), Agent("W1"), Agent("W2"), Agent("W4")
    manager = _manager(pm1, w1, w2, w4)
    t3 = _add(tasks, manager, "Kick-off Task 3: Implement persistent leaderboard functionality in src/leaderboard.py",
              "W4", caller="PM1")
    _add(tasks, manager, "Kick-off Task 1: Implement core game logic in src/game_logic.py", "W1", caller="PM1")
    (tasks.workspace / "src").mkdir()  # Round 4j: the files exist before the task that makes them is finished
    (tasks.workspace / "src" / "leaderboard.py").write_text("class Leaderboard:\n    pass\n", encoding="utf-8")
    (tasks.workspace / "src" / "game_logic.py").write_text("GRID_WIDTH = 20\n", encoding="utf-8")
    tasks("W4", manager, action="modify_task", task_id=t3, task_progress="finished")
    note = file_owner_note(manager, PROJECT, SESSION, "src/leaderboard.py")
    assert note == (" 'src/leaderboard.py' belongs to W4 (its FINISHED task 'Kick-off Task 3: Implement persistent "
                    "leaderboard functionality in ...'): create a NEW task for the fix - action 'add_task' with a "
                    "description that names 'src/leaderboard.py' and the change, and assignee_agent_id='W4'. Do not give the "
                    "fix to a worker whose task does not name this file: it may not change it.")
    assert file_owner_note(manager, PROJECT, SESSION, "src/game_logic.py").startswith(
        " 'src/game_logic.py' belongs to W1, whose open task 'Kick-off Task 1: Implement core game logic in "
        "src/game_logic.py' (")
    assert file_owner_note(manager, PROJECT, SESSION, "src/unknown.py") == ""
    # 13:30:51 through the tool executor: the PM's refusal carries the note, the file is unchanged
    edit = {"action": "replace_chunks", "filename": "src/leaderboard.py",
            "chunks": [{"search": "    pass", "replace": "    entries = []"}]}
    with quiet():
        answer = run(ToolExecutor().execute_tool("PM1", tasks.workspace.parent, "code_editor", edit, project_name=PROJECT,
                                                 session_name=SESSION, manager=manager))
    assert "A Project Manager does not change project files" in str(answer)
    assert str(answer).endswith(note), answer
    assert "pass" in (tasks.workspace / "src" / "leaderboard.py").read_text(encoding="utf-8")


# ======================================================================================== 3. data files the run wrote
LEADERBOARD_PY = '''import json
import os


class Leaderboard:
    STORAGE_FILE = 'leaderboard.json'

    def __init__(self):
        self.storage_path = os.path.join(os.path.dirname(__file__), self.STORAGE_FILE)
        self.entries = []
        if os.path.exists(self.storage_path):
            with open(self.storage_path) as f:
                self.entries = json.load(f)

    def add(self, name):
        self.entries.append(name)
        with open(self.storage_path, 'w') as f:
            json.dump(self.entries, f)


def reset_leaderboard():
    if os.path.exists(Leaderboard.STORAGE_FILE):
        os.remove(Leaderboard.STORAGE_FILE)
'''
TEST_PY = '''import pytest
from src.leaderboard import Leaderboard, reset_leaderboard


@pytest.fixture(autouse=True)
def clean():
    reset_leaderboard()


def test_add_one():
    board = Leaderboard()
    board.add("Alice")
    assert len(board.entries) == 1


def test_add_two():
    board = Leaderboard()
    board.add("Bob")
    board.add("Carol")
    assert len(board.entries) == 2
'''


def _project(root: Path) -> Path:
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "conftest.py").write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
    (root / "src" / "leaderboard.py").write_text(LEADERBOARD_PY, encoding="utf-8")
    (root / "tests" / "test_leaderboard.py").write_text(TEST_PY, encoding="utf-8")
    (root / "src" / "config.json").write_text("{}", encoding="utf-8")
    old = time.time() - 3600
    os.utime(root / "src" / "config.json", (old, old))
    return root


def test_a_failed_run_that_wrote_data_files_says_so(tmp_path):
    from src.utils.test_diagnosis import written_data_note, data_files_written_since
    root = _project(tmp_path / "game")
    since = time.time()
    result = __import__("subprocess").run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p",
                                           "no:randomly"], cwd=root, capture_output=True, text=True, timeout=120)
    assert "1 failed, 1 passed" in result.stdout, result.stdout
    assert data_files_written_since(root, since) == ["src/leaderboard.json"]        # config.json was not touched
    note = written_data_note(root, result.stdout, since)
    assert note == ("[Framework check - this test run WROTE the project's data file(s) src/leaderboard.json:]\n"
                    "Tests that use a real data file share what is in it: each test (and each later run) starts with the "
                    "data that earlier tests left there, so counts and contents depend on the order of the tests. Give the "
                    "tests their own temporary file instead - e.g. pass `tmp_path / '<file name>'` as the storage path, or "
                    "`monkeypatch` the path the code uses - and delete nothing in the project from a test.\n"
                    "- 'leaderboard.json' (src/leaderboard.py line 6) is a path relative to the folder the tests run in "
                    "(the workspace root), where no such file is - but the file the code reads and writes is "
                    "src/leaderboard.json. Deleting or opening 'leaderboard.json' there does not touch src/leaderboard.json.")
    # no failure, no start time, or nothing written since: no note
    assert written_data_note(root, "2 passed in 0.01s", since) is None
    assert written_data_note(root, result.stdout, None) is None
    assert written_data_note(root, result.stdout, time.time() + 5) is None
    # a file of that name at the root: the bare name is not pointing elsewhere
    (root / "leaderboard.json").write_text("[]", encoding="utf-8")
    assert "- 'leaderboard.json'" not in written_data_note(root, result.stdout, since)


def test_the_test_runner_reports_the_written_data_file(tmp_path):
    from src.tools.test_runner import TestRunnerTool
    root = _project(tmp_path / "sandbox")
    with quiet():
        result = run(TestRunnerTool().execute("W4", root, command=f"{sys.executable} -m pytest -q -p no:cacheprovider",
                                              scope="private"))
    assert result["status"] == "error"
    assert "[Framework check - this test run WROTE the project's data file(s) src/leaderboard.json:]" in \
        result["framework_check"]


def test_the_command_executor_reports_the_written_data_file(tmp_path):
    from src.tools.command_executor import CommandExecutionTool
    root = _project(tmp_path / "sandbox")
    with quiet():
        result = run(CommandExecutionTool().execute(agent_id="W4", agent_sandbox_path=root, action="run_command",
                                                    scope="private", command="python -m pytest -q -p no:cacheprovider",
                                                    timeout=60))
    assert "1 failed, 1 passed" in result["content"], result
    assert "[Framework check - this test run WROTE the project's data file(s) src/leaderboard.json:]" in result["content"]


# ======================================================================================== 4. summaries
def _summarizer(provider):
    from src.agents.cycle_components.context_summarizer import ContextSummarizer
    from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID
    cg = types.SimpleNamespace(llm_provider=provider, model="m", message_history=["the CG's own history"])
    return ContextSummarizer(types.SimpleNamespace(agents={CONSTITUTIONAL_GUARDIAN_AGENT_ID: cg})), cg


def test_a_summary_that_reached_its_length_limit_is_marked():
    from src.agents.cycle_components.context_summarizer import SUMMARY_LENGTH_NOTE, SUMMARY_WORD_LIMIT
    prompts = []

    class Limited:
        async def stream_completion(self, model, messages, temperature, max_tokens=None, **kwargs):
            prompts.append(messages[-1]["content"])
            yield {"type": "response_chunk", "content": "W4 fixed reset_leaderboard() and the tests still fail in"}
            yield {"type": "finish_info", "done_reason": "length", "output_tokens": 1000}
    summarizer, cg = _summarizer(Limited())
    with quiet():
        summary = run(summarizer._summarize_chunk([{"role": "user", "content": "x"}], 1, "W4"))
    assert summary == "W4 fixed reset_leaderboard() and the tests still fail in" + SUMMARY_LENGTH_NOTE
    assert cg.message_history == ["the CG's own history"]
    assert f"at most {SUMMARY_WORD_LIMIT} words" in prompts[0]
    assert prompts[0].index("1. Current progress and next steps") < prompts[0].index("2. Key decisions")

    class Finished:
        async def stream_completion(self, model, messages, temperature, max_tokens=None, **kwargs):
            yield {"type": "response_chunk", "content": "Done."}
            yield {"type": "finish_info", "done_reason": "stop", "output_tokens": 2}
    summarizer, _cg = _summarizer(Finished())
    with quiet():
        assert run(summarizer._summarize_chunk([{"role": "user", "content": "x"}], 1, "W4")) == "Done."

# END OF FILE tests/unit/test_round3t.py
