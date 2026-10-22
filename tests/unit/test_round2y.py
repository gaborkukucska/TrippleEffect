# START OF FILE tests/unit/test_round2y.py
"""
Round 2y: the 2026-10-22 14:43-14:53 run (WiFi_Snake_Game_Multiplayer, qwen3.5:9b-q4_K_M).

1. Small tool calls were dropped too: 14:48:08-14:48:31 PM1 tried five times to assign Kick-off Task 1 to W1. Every reply
   finished normally with 85-192 output tokens, no text and no tool call (two of them with no thinking at all). The
   Round 2x lost-output check needed 200 tokens beyond the thinking, so they were called "empty" / "only thinking" and
   the Constitutional Guardian blocked PM1 (5 empty replies). The same call arrived at 14:48:59.
2. 14:51-14:53 W1 sent src/game.js three times as a complete new file. Each was rejected (Round 2x), but the error moved
   from line 102 to 117 to 134 - the repair was progressing - and after three failed tool calls in a row the scheduler
   forced W1 to report. A cut-off or lost reply also counted as a failed cycle.
3. PROJECT_STRUCTURE.md listed src/game/game.js AND src/game.js, src/storage/player_manager.js AND
   storage/player_manager.js: the structure and Kick-off Task 1 named the same files in different directories.
4. The Ollama server's version is now logged when the endpoint is discovered.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

# src/game.js as W1 sent it three times (14:51:28, 14:52:27, 14:53:33), shortened: each version fixes the previous
# error, and the next one is further down - the three messages of the run
GAME_HEAD = "class SnakeGame {\n  constructor(canvas) {\n    this.canvas = canvas;\n    this.snake = [];\n  }\n\n"
KEYS_BAD = "  onKey(e) {\n    switch (e.key) {\n      case 'w': break;\n      default: continue;\n    }\n  }\n\n"
KEYS_OK = KEYS_BAD.replace("default: continue;", "default: return;")
INPUT_BAD = ("  setupInput() {\n    if (this.canvas) {\n      this.canvas.addEventListener('touchstart', () => {});\n\n"
             "} catch(err){console.error('Error setting up input handlers:', err);}\n  }\n\n")
INPUT_OK = ("  setupInput() {\n    try {\n      this.canvas.addEventListener('touchstart', () => {});\n"
            "    } catch(err){console.error('Error setting up input handlers:', err);}\n  }\n\n")
CLEAN_BAD = "  cleanup() {\n    try { this.canvas = null; } catch(err){console.error('Error cleaning up:', err);}\n  }\n  }\n\n"
CLEAN_OK = "  cleanup() {\n    try { this.canvas = null; } catch(err){console.error('Error cleaning up:', err);}\n  }\n\n"
FOOD = ("  /** Spawn food at a random position not occupied by the snake */\n  spawnFood() {\n    return { x: 1, y: 2 };\n"
        "  }\n}\n\nexport { SnakeGame };\n")
GAME_14_51_28 = GAME_HEAD + KEYS_BAD + INPUT_BAD + CLEAN_BAD + FOOD
GAME_14_52_27 = GAME_HEAD + KEYS_OK + INPUT_BAD + CLEAN_BAD + FOOD
GAME_14_53_33 = GAME_HEAD + KEYS_OK + INPUT_OK + CLEAN_BAD + FOOD
GAME_FIXED = GAME_HEAD + KEYS_OK + INPUT_OK + CLEAN_OK + FOOD


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(autouse=True)
def _no_kept_drafts():
    from src.tools import rejected_drafts
    rejected_drafts.clear()
    yield
    rejected_drafts.clear()


# ======================================================================================== 1. small lost tool calls
# (output_tokens, thinking_chars) of replies with no text and no tool call, from the two runs of 2026-10-22
LOST_REPLIES = [(157, 178), (85, 0), (192, 397), (85, 0), (187, 347),   # PM1 14:48:08-14:48:31 (modify_task)
                (1392, 410), (981, 0), (535, 355), (2049, 367)]           # W2 11:44:07, 11:44:30, 11:47:42, 11:49:03
THINKING_ONLY_REPLIES = [(947, 4836), (304, 1592), (82, 263), (112, 340), (157, 544), (60, 328), (116, 364), (468, 1573),
                         (570, 1871), (163, 490), (228, 967), (179, 738)]


@pytest.mark.parametrize("output_tokens, thinking_chars", LOST_REPLIES)
def test_dropped_tool_calls_are_recognised(output_tokens, thinking_chars):
    from src.llm_providers.ollama_provider import is_output_lost, lost_output_tokens
    assert is_output_lost("stop", output_tokens, thinking_chars, 0, 0)
    assert lost_output_tokens("stop", output_tokens, thinking_chars, 0, 0) >= 50


@pytest.mark.parametrize("output_tokens, thinking_chars", THINKING_ONLY_REPLIES)
def test_replies_that_only_thought_are_not_lost(output_tokens, thinking_chars):
    from src.llm_providers.ollama_provider import is_output_lost, lost_output_tokens
    assert not is_output_lost("stop", output_tokens, thinking_chars, 0, 0)
    assert lost_output_tokens("stop", output_tokens, thinking_chars, 0, 0) == 0


def test_the_lost_token_count():
    from src.llm_providers.ollama_provider import lost_output_tokens
    assert lost_output_tokens("stop", 157, 178, 0, 0) == 157 - 51          # 178 / 3.5 = 50.9 -> 51 thinking tokens
    assert lost_output_tokens("stop", 85, 0, 0, 0) == 85
    assert lost_output_tokens("stop", 87, 0, 0, 1) == 0                     # 14:48:59: the call arrived
    assert lost_output_tokens("stop", 95, 0, 469, 0) == 0                   # 14:48:40: text arrived
    assert lost_output_tokens("length", 4096, 19998, 0, 0) == 0             # 14:46:25: a cut-off
    assert lost_output_tokens("stop", 49, 0, 0, 0) == 0 and lost_output_tokens("stop", 50, 0, 0, 0) == 50


class _FakeProvider:
    def __init__(self, events):
        self.events, self.kwargs_seen = events, []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.kwargs_seen.append(kwargs)
        for event in self.events:
            yield event


def _core_pm(provider, monkeypatch):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools={}),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s, is_valid_state=lambda t, s: True,
                                               _valid_states={}),
        cycle_handler=None)
    agent = Agent({"agent_id": "PM1", "config": {"agent_type": "pm", "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = "pm_activate_workers"
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    with quiet():
        return run(go())


def test_pm1s_dropped_assignments_are_lost_output(monkeypatch):
    from src.agents.cycle_handler import lost_output_notice
    first = [{"type": "finish_info", "done_reason": "stop", "output_tokens": 157, "thinking_chars": 178,
              "thinking_tail": "I need to assign Task 1 (UUID: e0750524-...) to worker W1", "prompt_tokens": 10863}]
    provider = _FakeProvider(first)
    agent = _core_pm(provider, monkeypatch)
    _consume(agent)
    assert agent._last_reply_output_lost is True and agent._last_reply_lost_tokens == 106
    assert agent._think_off_next_call is False                  # 14:48:08 switched thinking off ("only thought")
    provider.events = [{"type": "finish_info", "done_reason": "stop", "output_tokens": 85, "thinking_chars": 0,
                        "prompt_tokens": 10950}]
    _consume(agent)
    assert agent._consecutive_output_lost_replies == 2 and agent._last_reply_lost_tokens == 85
    text = lost_output_notice(agent)
    assert text.startswith("[Framework Notice]: Your last reply produced 85 output tokens but NOTHING arrived - no text "
                           "and no complete tool call (2 time(s) in a row)")
    assert ("Make the same call again as ONE plain native tool call with ONLY the parameters it needs (e.g. "
            "project_management with just action, task_id and assignee_agent_id)") in text
    assert "about 80 lines" not in text


def test_a_large_lost_call_still_gets_the_file_advice():
    from src.agents.cycle_handler import lost_output_notice
    agent = types.SimpleNamespace(_last_output_tokens=1392, _consecutive_output_lost_replies=1, _last_reply_lost_tokens=1275)
    text = lost_output_notice(agent)
    assert "write its first part (about 80 lines) with file_system 'write'" in text and "ONLY the parameters" not in text


# ======================================================================================== 2. a repair that progresses
def _fs(tmp_path, agent="W1", **kwargs):
    from src.tools.file_system import FileSystemTool
    with quiet():
        return run(FileSystemTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, scope="private", **kwargs))


def test_the_rejection_streak_and_progress():
    from pathlib import Path
    from src.tools import rejected_drafts
    path = Path("/tmp/x/src/game.js")
    assert rejected_drafts.note_rejection(path, "W1", 102) == [102] and not rejected_drafts.take_progress("W1")
    assert rejected_drafts.note_rejection(path, "W1", 117) == [102, 117]
    assert rejected_drafts.take_progress("W1") and not rejected_drafts.take_progress("W1")      # taken once
    rejected_drafts.note_rejection(path, "W1", 117)                                            # same line: no progress
    assert not rejected_drafts.take_progress("W1")
    rejected_drafts.note_rejection(path, "W1", 90)                                             # earlier: no progress
    assert not rejected_drafts.take_progress("W1")
    assert rejected_drafts.note_rejection(path, "W2", 130) == [130]                            # another agent: new streak
    progress = []
    for line in range(200, 200 + rejected_drafts.MAX_PROGRESS_STEPS + 3):
        rejected_drafts.note_rejection(path, "W3", line)
        progress.append(rejected_drafts.take_progress("W3"))
    # the first rejection starts the streak; the next MAX_PROGRESS_STEPS are progress; beyond the cap they are not
    assert progress == [False] + [True] * rejected_drafts.MAX_PROGRESS_STEPS + [False, False]
    rejected_drafts.forget(path)
    assert rejected_drafts.note_rejection(path, "W3", 5) == [5]


@needs_node
def test_three_rewrites_whose_error_moves_down(tmp_path):
    from src.tools import rejected_drafts
    first = _fs(tmp_path, action="write", filename="src/game.js", content=GAME_14_51_28)
    assert first["status"] == "error" and "Illegal continue statement" in first["message"]
    assert "This is your rejection number" not in first["message"] and not rejected_drafts.take_progress("W1")
    second = _fs(tmp_path, action="write", filename="src/game.js", content=GAME_14_52_27)
    assert second["status"] == "error" and "Unexpected token 'catch'" in second["message"]
    assert ("This is your rejection number 2 of 'src/game.js' in a row (errors at lines 10, 18). Do NOT send the whole "
            "file again") in second["message"]
    assert rejected_drafts.take_progress("W1")
    third = _fs(tmp_path, action="write", filename="src/game.js", content=GAME_14_53_33)
    assert "(errors at lines 10, 18, 26)" in third["message"] and rejected_drafts.take_progress("W1")
    fixed = _fs(tmp_path, action="replace_lines", filename="src/game.js", start_line=22, end_line=23,
                content="  }\n")                                       # line 23: the one '}' too many
    assert fixed["status"] == "success" and (tmp_path / "src/game.js").read_text(encoding="utf-8") == GAME_FIXED
    assert rejected_drafts.note_rejection(tmp_path / "src/game.js", "W1", 3) == [3]          # the streak ended


@needs_node
def test_a_step_by_step_repair_of_the_kept_text_is_progress(tmp_path):
    from src.tools import rejected_drafts
    _fs(tmp_path, action="write", filename="src/game.js", content=GAME_14_51_28)
    step = _fs(tmp_path, action="find_replace", filename="src/game.js", find_text="default: continue;",
               replace_text="default: return;")
    assert step["status"] == "error" and "still not valid JavaScript (SyntaxError: Unexpected token 'catch' at line 18)" \
        in step["message"]
    assert rejected_drafts.take_progress("W1")


def _worker(state="worker_work"):
    agent = types.SimpleNamespace(agent_id="W1", agent_type="worker", state=state, status="processing",
                                  message_history=[{"role": "system", "content": "worker prompt"}],
                                  _consecutive_failed_cycles=0, _duplicate_tool_call_count=0)
    agent.set_status = lambda status, **kw: setattr(agent, "status", status)
    return agent


def _ctx(agent):
    return types.SimpleNamespace(
        agent=agent, all_tool_results=[], trigger_failover=False, needs_reactivation_after_cycle=False,
        is_retryable_error_type=False, retry_count=0, max_retries_for_cycle=3, cycle_completed_successfully=True,
        executed_tool_successfully_this_cycle=False, action_taken_this_cycle=True,
        state_change_requested_this_cycle=False, current_model_key_for_tracking="m",
        last_error_content="Write rejected", last_error_obj=None, plan_submitted_this_cycle=False,
        thought_produced_this_cycle=False)


def _scheduler():
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    manager = MagicMock()
    manager.schedule_cycle = AsyncMock(return_value="task")
    manager.send_to_ui = AsyncMock()
    manager.current_session_db_id = None
    manager.loop_coordinator.should_intervene.return_value = True
    manager.agents = {}
    return NextStepScheduler(manager), manager


def _forced_reports(manager):
    return [c for c in manager.workflow_manager.change_state.call_args_list if "worker_report" in [str(a) for a in c.args]]


@needs_node
def test_the_scheduler_does_not_force_a_report_while_the_repair_progresses(tmp_path):
    sched, manager = _scheduler()
    agent = _worker()
    for version in (GAME_14_51_28, GAME_14_52_27, GAME_14_53_33):
        _fs(tmp_path, action="write", filename="src/game.js", content=version)
        with quiet():
            run(sched.schedule_next_step(_ctx(agent)))
    assert agent._consecutive_failed_cycles == 1 and not _forced_reports(manager)


def test_three_real_failures_still_force_a_report():
    sched, manager = _scheduler()
    agent = _worker()
    for _ in range(3):
        with quiet():
            run(sched.schedule_next_step(_ctx(agent)))
    assert _forced_reports(manager)


def test_cut_off_and_lost_replies_are_not_failed_cycles():
    from src.agents.cycle_components.next_step_scheduler import failed_cycle_excuse
    sched, manager = _scheduler()
    agent = _worker()
    agent._last_reply_output_lost, agent._last_reply_cut_off, agent._context_window_full = True, False, False
    for streak in (1, 2, 3):
        agent._consecutive_unusable_replies = streak
        with quiet():
            run(sched.schedule_next_step(_ctx(agent)))
    assert agent._consecutive_failed_cycles == 0 and not _forced_reports(manager)
    assert failed_cycle_excuse(agent) == "its reply was cut off, lost or hit the full context window (it is retried)"
    agent._consecutive_unusable_replies = 99
    assert failed_cycle_excuse(agent) is None


# ======================================================================================== 3. one file, two paths
STRUCTURE_14_46_39 = [
    {"dir": "src", "children": [{"dir": "game", "files": ["game.js"]}, {"dir": "storage", "files": ["player_manager.js"]},
                                {"dir": "network", "files": ["multiplayer.js"]}]},
    {"dir": "public/styles"}, {"dir": "tests"}, {"dir": "docs"}]
TASK_FILES_14_46_39 = ["src/game.js", "storage/player_manager.js", "src/network/multiplayer.js", "tests/game.test.js",
                       "public/styles/app.css"]


def test_a_file_named_twice_is_planned_once():
    from src.workflows.pm_kickoff_workflow import duplicate_structure_files, normalize_planned_layout, plan_project_structure
    structure_files = normalize_planned_layout(*plan_project_structure(STRUCTURE_14_46_39)[:2])[1]
    dropped, notes = duplicate_structure_files(structure_files, TASK_FILES_14_46_39)
    assert dropped == ["src/game/game.js", "src/storage/player_manager.js"]
    assert notes[0] == ("Planned file 'src/game/game.js' dropped: the kick-off task names this file 'src/game.js', so "
                        "that is the one path to create.")


def test_shared_names_and_named_twice_files_are_kept():
    from src.workflows.pm_kickoff_workflow import duplicate_structure_files
    assert duplicate_structure_files(["src/app/__init__.py", "src/index.js"], ["src/__init__.py", "web/index.js"]) == ([], [])
    # the task names both paths: two different files
    assert duplicate_structure_files(["src/game/game.js"], ["src/game.js", "src/game/game.js"]) == ([], [])
    # two structure files of that name: unclear which one is meant - nothing is dropped
    assert duplicate_structure_files(["a/game.js", "b/game.js"], ["src/game.js"]) == ([], [])


HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False


class _Executor:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path

    async def execute_tool(self, agent_id, agent_sandbox_path, tool_name, tool_args, project_name, session_name, manager):
        import src.tools.project_management as pm
        return await pm.ProjectManagementTool().execute(agent_id, self.tmp_path, project_name=project_name,
                                                        session_name=session_name, **tool_args)


class _FakePM:
    def __init__(self, tmp_path):
        self.agent_id, self.sandbox_path = "PM1", tmp_path
        self.agent_config = {"config": {"project_name_context": "P"}}
        self.message_history = []

    def clear_history(self):
        self.message_history = []


@pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")
def test_the_kickoff_plans_one_path_per_file(tmp_path, monkeypatch):
    import src.tools.project_management as pm
    import src.workflows.pm_kickoff_workflow as pkw
    from src.config.settings import settings
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)
    ws = tmp_path / "projects" / "P" / "S" / "shared_workspace"
    ws.mkdir(parents=True)
    plan = {"roles": ["Coder", "Technical_Writer"], "project_structure": STRUCTURE_14_46_39, "tasks": [
        {"id": "task_1", "description": "Implement the core game engine, player management system, and network "
                                       "multiplayer mode in src/game.js, storage/player_manager.js, and "
                                       "src/network/multiplayer.js respectively."},
        {"id": "task_2", "depends_on": ["task_1"], "description": "Write automated unit tests for the game engine "
                                                                  "collision detection in tests/game.test.js."},
        {"id": "task_3", "depends_on": ["task_1"], "description": "Create responsive UI design files including "
                                                                  "public/styles/app.css for touch controls overlay."}]}
    manager = types.SimpleNamespace(current_project="P", current_session="S", tool_executor=_Executor(tmp_path))
    with quiet():
        result = run(pkw.PMKickoffWorkflow().execute(manager, _FakePM(tmp_path), plan))
    assert result.success, result.message
    md = (ws / "PROJECT_STRUCTURE.md").read_text(encoding="utf-8")
    to_create = md.split("## Files To Create", 1)[1]
    for planned in ("src/game.js", "storage/player_manager.js", "src/network/multiplayer.js", "tests/game.test.js",
                    "public/styles/app.css"):
        assert f"- {planned}\n" in to_create
    assert "src/game/game.js" not in to_create and "src/storage/player_manager.js" not in to_create


# ======================================================================================== 4. the Ollama server version
class _Response:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._data


class _Session:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.urls = response, error, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


def test_the_server_version_is_logged(caplog):
    from src.config.model_registry import log_ollama_server_version
    session = _Session(_Response(200, {"version": "0.12.3"}))
    with caplog.at_level(logging.INFO, logger="src.config.model_registry"):
        assert run(log_ollama_server_version(session, "https://oli.gaborkukucska.com")) == "0.12.3"
    assert session.urls == ["https://oli.gaborkukucska.com/api/version"]
    assert "Ollama endpoint https://oli.gaborkukucska.com: server version 0.12.3." in caplog.text
    assert run(log_ollama_server_version(_Session(_Response(404, {})), "http://h")) is None
    assert run(log_ollama_server_version(_Session(error=OSError("refused")), "http://h")) is None
    assert run(log_ollama_server_version(_Session(_Response(200, ["x"])), "http://h")) is None


def test_discovery_asks_for_the_version():
    import inspect
    from src.config import model_registry
    assert "await log_ollama_server_version(session, base_url)" in inspect.getsource(model_registry)

# END OF FILE tests/unit/test_round2y.py
