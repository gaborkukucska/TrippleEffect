# START OF FILE tests/unit/test_round4o.py
"""
Round 4o: a test run that does not END because a CLASS starts a timer in its constructor is named - and, when the class is
another worker's, routed to that worker.

The run of 2026-10-31 (WiFi_Multiplayer_Snake_Game, startup_1793408847): from 09:53 to 10:48 "Kick-off Task 7: Write
comprehensive automated tests ... in tests/" (W3) was refused 14 times with "its tests do not END". The constructors of
ScoreManager (src/score.js, W1's Task 4) and Leaderboard (src/leaderboard.js, W2's Task 5) call setupAutoSave() /
setupAutoRefresh(), which start a setInterval and keep no way to clear it; every test makes one in beforeEach(). The
framework only gave its general hint (Round 4a looks at top-level functions, not class methods), and W3 spent the hour
rewriting its own test files - no test can stop that timer, and W3 may not change the classes.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Multiplayer_Snake_Game", "startup_1793408847"
HAVE_NODE = shutil.which("node") is not None
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_node = pytest.mark.skipif(not HAVE_NODE, reason="node is not installed")
needs_tw_and_node = pytest.mark.skipif(not (HAVE_TW and HAVE_NODE), reason="Taskwarrior (task + tasklib) or node missing")

# src/score.js of the run (shortened; the timer is at line 20 as at line 77 there)
SCORE_JS = """/**
 * Score tracking
 */
const SCORE_CONFIG = { PERSISTENCE_INTERVAL: 5000 };

class ScoreManager {
  constructor() {
    this.scores = new Map();
    this.lastSaveTime = Date.now();
    this.setupAutoSave();
  }

  addScore(playerId, points) {
    this.scores.set(playerId, (this.scores.get(playerId) || 0) + points);
    return this.scores.get(playerId);
  }

  setupAutoSave() {
    // save now and then
    setInterval(() => {
      this.lastSaveTime = Date.now();
    }, SCORE_CONFIG.PERSISTENCE_INTERVAL);
  }
}

module.exports = { ScoreManager, SCORE_CONFIG };
"""

SCORE_TEST_JS = """const assert = require('node:assert');
const { describe, test, beforeEach, afterEach } = require('node:test');

const { ScoreManager } = require('../../src/score');

describe('ScoreManager', () => {
  let manager;

  beforeEach(() => {
    manager = new ScoreManager();
  });

  afterEach(() => {
    manager = null;
  });

  test('adds points', () => {
    assert.strictEqual(manager.addScore('p1', 3), 3);
  });
});
"""

TASK_4 = "Kick-off Task 4: Implement score calculation and real-time persistence in src/score.js"
TASK_7 = ("Kick-off Task 7: Write comprehensive automated tests for game logic, networking, player management, scoring, "
          "and leaderboard in tests/")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _workspace(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


# ======================================================================================== 1. the class timer is found
def test_the_timer_of_the_run_is_named_with_its_class_file_and_line(tmp_path):
    from src.utils.code_map import class_timer_findings, open_timer_problems
    root = _workspace(tmp_path, {"src/score.js": SCORE_JS, "tests/score/test.js": SCORE_TEST_JS})
    assert class_timer_findings(root, "tests/score/test.js") == [
        {"module": "src/score.js", "cls": "ScoreManager", "method": "setupAutoSave", "stoppers": [], "line": 20,
         "test_line": 10}]
    problems = open_timer_problems(root, "tests/score/test.js")
    assert problems == [
        "tests/score/test.js makes a `ScoreManager` (line 10), and its constructor (through `setupAutoSave()`) starts a "
        "setInterval/setTimeout in src/score.js (line 20) that is never cleared: an open timer keeps Node running, so the "
        "test run NEVER ends. To fix: change src/score.js: keep the timer's id (`this.timer = setInterval(...)`) and give "
        "`ScoreManager` a method that clears it (clearInterval) for the tests to call in afterEach() - or call `.unref()` "
        "on the timer so it does not keep Node running. A test alone cannot stop this timer, and the same command will "
        "time out again."]


def test_the_hint_of_a_test_command_that_timed_out_names_it(tmp_path):
    from src.tools.project_links_check import test_hang_hint
    root = _workspace(tmp_path, {"src/score.js": SCORE_JS, "tests/score/test.js": SCORE_TEST_JS})
    hint = test_hang_hint(root, "node --test tests/score/test.js", 0.0, "TAP version 13\n")
    assert "makes a `ScoreManager` (line 10)" in hint and "src/score.js (line 20)" in hint


def test_a_timer_in_the_constructor_itself_and_one_reached_through_two_methods(tmp_path):
    from src.utils.code_map import class_timer_findings
    direct = SCORE_JS.replace("    this.setupAutoSave();\n", "    setTimeout(() => {}, 60000);\n")
    root = _workspace(tmp_path, {"src/score.js": direct, "tests/score/test.js": SCORE_TEST_JS})
    found = class_timer_findings(root, "tests/score/test.js")
    assert [(f["method"], f["line"]) for f in found] == [("constructor", 10)]
    chained = SCORE_JS.replace("    this.setupAutoSave();\n", "    this.init();\n").replace(
        "  addScore(", "  init() {\n    this.setupAutoSave();\n  }\n\n  addScore(")
    _workspace(tmp_path, {"src/score.js": chained})
    assert [f["method"] for f in class_timer_findings(root, "tests/score/test.js")] == ["setupAutoSave"]


def test_what_is_not_a_finding(tmp_path):
    from src.utils.code_map import class_timer_findings
    root = _workspace(tmp_path, {"tests/score/test.js": SCORE_TEST_JS})
    cases = {
        "an unref'd timer": SCORE_JS.replace("}, SCORE_CONFIG.PERSISTENCE_INTERVAL);",
                                             "}, SCORE_CONFIG.PERSISTENCE_INTERVAL).unref();"),
        "a timer only a method the constructor does not call starts": SCORE_JS.replace("    this.setupAutoSave();\n", ""),
        "a class the module does not export": SCORE_JS.replace("module.exports = { ScoreManager, SCORE_CONFIG };",
                                                                "module.exports = { SCORE_CONFIG };"),
        "a timer the same method clears": SCORE_JS.replace("      this.lastSaveTime = Date.now();\n",
                                                           "      this.lastSaveTime = Date.now();\n      clearInterval(0);\n"),
    }
    for case, source in cases.items():
        _workspace(tmp_path, {"src/score.js": source})
        assert class_timer_findings(root, "tests/score/test.js") == [], case
    # the test never makes a ScoreManager
    _workspace(tmp_path, {"src/score.js": SCORE_JS,
                          "tests/score/test.js": SCORE_TEST_JS.replace("new ScoreManager()", "{ addScore: () => 3 }")})
    assert class_timer_findings(root, "tests/score/test.js") == []


def test_a_stopper_the_tests_call_in_afterEach_is_enough(tmp_path):
    from src.utils.code_map import class_timer_findings, open_timer_problems
    with_stop = SCORE_JS.replace("    setInterval(() => {", "    this.timer = setInterval(() => {").replace(
        "  addScore(", "  stop() {\n    clearInterval(this.timer);\n  }\n\n  addScore(")
    root = _workspace(tmp_path, {"src/score.js": with_stop, "tests/score/test.js": SCORE_TEST_JS})
    found = class_timer_findings(root, "tests/score/test.js")
    assert [f["stoppers"] for f in found] == [["stop"]]
    assert "call `.stop()` on every `ScoreManager` the tests make, in an afterEach() hook" in open_timer_problems(
        root, "tests/score/test.js")[0]
    _workspace(tmp_path, {"tests/score/test.js": SCORE_TEST_JS.replace("    manager = null;", "    manager.stop();")})
    assert class_timer_findings(root, "tests/score/test.js") == []
    # an afterEach called INSIDE a test never runs
    inside = SCORE_TEST_JS.replace("    assert.strictEqual(", "    afterEach(() => manager.stop());\n    assert.strictEqual(")
    _workspace(tmp_path, {"tests/score/test.js": inside})
    assert len(class_timer_findings(root, "tests/score/test.js")) == 1


# ======================================================================================== 2. the run, on a real task store
class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history = []


def _manager(*agents):
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        state_manager=types.SimpleNamespace(
            get_agent_team=lambda agent_id: f"team_{PROJECT}",
            get_agents_in_team=lambda team_id: [a for a in agents]))


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    from src.config.settings import settings
    from src.utils import file_authorship, test_task_check
    from src.utils.test_runs import clear_test_runs
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    monkeypatch.setattr(test_task_check, "COMPLETION_TEST_TIMEOUT", 6.0)
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)
    file_authorship.reset()
    clear_test_runs()

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    call.workspace = workspace
    yield call
    file_authorship.reset()
    clear_test_runs()


def _add(call, manager, text, assignee=None, **extra):
    args = {"assignee_agent_id": assignee} if assignee else {}
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args, **extra)
    assert result["status"] == "success", result
    return result["task_uuid"]


def _the_run(call, score_author="W1"):
    from src.utils.file_authorship import record_file_change
    pm1, w1, w3 = Agent("PM1", "pm", "pm_manage"), Agent("W1"), Agent("W3", state="worker_work")
    manager = _manager(pm1, w1, w3)
    ws = _workspace(call.workspace, {"src/score.js": SCORE_JS, "tests/score/test.js": SCORE_TEST_JS})
    t4 = _add(call, manager, TASK_4, "W1")
    assert call("framework", manager, action="modify_task", task_id=t4, task_progress="finished")["status"] == "success"
    t7 = _add(call, manager, TASK_7, "W3")
    record_file_change(ws / "src/score.js", score_author, t4 if score_author == "W1" else t7)
    record_file_change(ws / "tests/score/test.js", "W3", t7)
    w3.current_task_id = w3.active_task_id = t7
    return manager, t4, t7


@needs_tw_and_node
def test_the_class_of_another_worker_gets_a_fix_task_and_the_test_task_waits(tasks):
    manager, _t4, t7 = _the_run(tasks)
    refused = tasks("W3", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "tests_routed", refused
    assert refused["message"].startswith(f"Task '{t7}' was NOT marked finished: its tests do not END, because classes in "
                                         "files OTHER workers made start a setInterval/setTimeout")
    [routed] = refused["routed"]
    assert routed["file"] == "src/score.js" and routed["assignee"] == "W1"
    listed = {t["uuid"]: t for t in tasks("framework", manager, action="list_tasks", project_filter=PROJECT)["tasks"]}
    fix = listed[routed["task_uuid"]]
    assert fix["description"].startswith("Fix: `ScoreManager` in src/score.js starts a setInterval/setTimeout in its "
                                         "constructor (line 20, through `setupAutoSave()`) and never clears it")
    assert routed["task_uuid"] in listed[t7]["depends"]
    # asked again while the fix is open: the same fix task, no second one
    again = tasks("W3", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert again["status"] == "error", again
    fixes = [t for t in tasks("framework", manager, action="list_tasks", project_filter=PROJECT)["tasks"]
             if t["description"].startswith("Fix: `ScoreManager`")]
    assert len(fixes) == 1


@needs_tw_and_node
def test_the_workers_own_class_is_not_routed(tasks):
    manager, _t4, t7 = _the_run(tasks, score_author="W3")
    from src.utils.file_authorship import reset, record_file_change
    reset()
    record_file_change(tasks.workspace / "src/score.js", "W3", t7)
    refused = tasks("W3", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert refused["error_type"] == "tests_do_not_end", refused
    assert "makes a `ScoreManager` (line 10)" in refused["message"] and "change src/score.js" in refused["message"]

# END OF FILE tests/unit/test_round4o.py
