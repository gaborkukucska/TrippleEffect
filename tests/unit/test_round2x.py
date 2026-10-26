# START OF FILE tests/unit/test_round2x.py
"""
Round 2x: the 2026-10-22 11:24-12:29 run (WiFi_Snake_Game_2, qwen3.5:9b-q4_K_M).

1. Output that never arrived: 11:44:07 W2's reply finished normally (done_reason 'stop') after 1,392 output tokens with 410
   characters of thinking, no text and no tool call; 11:44:30 the retry: 981 output tokens, nothing at all. Both were
   dropped file writes. The framework called them "empty" (the first even "only thinking" and switched thinking off), the
   Constitutional Guardian blocked W2 for "2 consecutive empty responses" and moved it to worker_wait (512 output tokens),
   where its next file writes were cut off at 512 / 1024 / 2048 tokens and it was blocked again - six times until 12:19.
2. src/js/main_game_controller.js was saved six times with "SyntaxError: Unexpected token '{'" at line 52 (only a note),
   and at 12:25:48 the completion check refused the task for it.
3. The kick-off plan chained every task to the one before it, so W1's Kick-off Task 2 waited for W2's Task 1 all run;
   PM1 assigned it with task_progress 'doing' and the list showed it 'doing' although it never started.
4. W2 and PM1 replied with text such as "[Action Taken: Transitioned to state 'worker_report', ...]" - the framework's own
   history note for a state change, which it wrote as the agent's reply.
5. The stall watchdog told PM1 (in pm_report_check) to request 'worker_wait' and counted separate visits as one stay.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.constants import AGENT_STATUS_IDLE

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")

# src/js/main_game_controller.js lines 30-52 as W2 left them: the keydown listener and init() are never closed
CONTROLLER_HEAD = (
    "class GameController {\n"
    "    constructor(canvas) {\n"
    "        this.canvas = canvas;\n"
    "        this.isRunning = false;\n"
    "    }\n"
    "\n"
    "    async init(startScreen, gameOverScreen) {\n"
    "        window.addEventListener('keydown', async (e) => {\n"
    "            if (!this.isRunning) return;\n"
    "            const keyInfo = this.getKeyDirectionFromKey(e.key);\n"
    "            e.preventDefault();\n"
    "                await this.handleInput(keyInfo, 'keyboard');\n")
CONTROLLER_TAIL = (
    "\n"
    "    /**\n"
    "     * Handle keyboard input events\n"
    "     */\n"
    "    handleKeyDown(e) {\n"
    "        return this.getKeyDirectionFromKey(e.key);\n"
    "    }\n"
    "}\n"
    "export { GameController };\n")
CONTROLLER_BROKEN = CONTROLLER_HEAD + CONTROLLER_TAIL
CONTROLLER_FIXED = CONTROLLER_HEAD + "        });\n    }\n" + CONTROLLER_TAIL
BROKEN_LINE = CONTROLLER_BROKEN.splitlines().index("    handleKeyDown(e) {") + 1


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


# ======================================================================================== 1. output that never arrived
@pytest.mark.parametrize("finish, lost", [
    (("stop", 1392, 410, 0, 0), True),     # 11:44:07 W2
    (("stop", 981, 0, 0, 0), True),        # 11:44:30 W2
    (("stop", 947, 4836, 0, 0), False),    # 11:29:00 PM1 only thought
    (("stop", 68, 304, 0, 0), False),      # 11:29:41 a short thinking-only reply
    (("stop", 1192, 2795, 3703, 0), False),  # 11:25:31 a text reply
    (("stop", 400, 804, 0, 1), False),     # 11:29:28 a tool call
    (("length", 4096, 0, 0, 0), False),    # a cut-off is reported as such
    (("stop", None, None, None, None), False),
    (("stop", "x", 0, 0, 0), False),
])
def test_lost_output_is_recognised(finish, lost):
    from src.llm_providers.ollama_provider import is_output_lost
    assert is_output_lost(*finish) is lost


class _FakeProvider:
    def __init__(self, events):
        self.events, self.max_tokens_seen, self.kwargs_seen = events, [], []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.max_tokens_seen.append(max_tokens)
        self.kwargs_seen.append(kwargs)
        for event in self.events:
            yield event


def _core_worker(provider, monkeypatch, state="worker_work"):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)
    monkeypatch.setattr(core_module.settings, "WORKER_WORK_STATE_MAX_TOKENS", 8192)

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools={}),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s, is_valid_state=lambda t, s: True,
                                               _valid_states={}),
        cycle_handler=None)
    agent = Agent({"agent_id": "W2", "config": {"agent_type": "worker", "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = state
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return run(go())


LOST_11_44_07 = [{"type": "finish_info", "done_reason": "stop", "output_tokens": 1392, "thinking_chars": 410,
                  "thinking_tail": "Now I need to create the JavaScript modules", "prompt_tokens": 12596,
                  "num_ctx": 32768, "num_predict": 8192}]
LOST_11_44_30 = [{"type": "finish_info", "done_reason": "stop", "output_tokens": 981, "thinking_chars": 0,
                  "prompt_tokens": 12736, "num_ctx": 32768, "num_predict": 8192}]
TEXT_REPLY = [{"type": "response_chunk", "content": "Reading the file first."},
              {"type": "finish_info", "done_reason": "stop", "output_tokens": 12}]


def test_core_marks_lost_output_and_keeps_thinking_on(monkeypatch):
    provider = _FakeProvider(LOST_11_44_07)
    agent = _core_worker(provider, monkeypatch)
    with quiet():
        _consume(agent)
    assert agent._last_reply_output_lost is True and agent._consecutive_output_lost_replies == 1
    assert agent._consecutive_unusable_replies == 1 and agent._last_reply_cut_off is False
    assert agent._think_off_next_call is False       # 11:44:07 switched thinking off: "the last reply only thought"
    provider.events = LOST_11_44_30
    with quiet():
        _consume(agent)
    assert agent._consecutive_output_lost_replies == 2 and agent._consecutive_unusable_replies == 2
    assert provider.max_tokens_seen == [8192, 8192]   # a lost reply is not a budget problem
    provider.events = TEXT_REPLY
    with quiet():
        _consume(agent)
    assert agent._last_reply_output_lost is False and agent._consecutive_output_lost_replies == 0
    assert agent._consecutive_unusable_replies == 0


def test_cut_off_and_lost_replies_count_together(monkeypatch):
    cut = [{"type": "finish_info", "done_reason": "length", "output_tokens": 8192, "thinking_chars": 100,
            "prompt_tokens": 9000, "num_ctx": 32768, "num_predict": 8192}]
    provider = _FakeProvider(cut)
    agent = _core_worker(provider, monkeypatch)
    with quiet():
        _consume(agent)
    assert agent._last_reply_cut_off is True and agent._last_reply_output_lost is False
    provider.events = LOST_11_44_30
    with quiet():
        _consume(agent)
    assert agent._last_reply_cut_off is False and agent._last_reply_output_lost is True
    assert agent._consecutive_unusable_replies == 2


def test_excused_replies_are_capped():
    from src.agents.core import UNUSABLE_REPLY_RETRY_CAP, excused_unusable_reply, unusable_reply_kind
    agent = types.SimpleNamespace(_context_window_full=False, _last_reply_cut_off=False, _last_reply_output_lost=True,
                                  _consecutive_unusable_replies=1)
    assert unusable_reply_kind(agent) == "output_lost" and excused_unusable_reply(agent)
    agent._consecutive_unusable_replies = UNUSABLE_REPLY_RETRY_CAP
    assert excused_unusable_reply(agent)
    agent._consecutive_unusable_replies = UNUSABLE_REPLY_RETRY_CAP + 1
    assert not excused_unusable_reply(agent)
    agent._consecutive_unusable_replies, agent._last_reply_output_lost = 1, False
    assert unusable_reply_kind(agent) is None and not excused_unusable_reply(agent)   # a really empty reply
    agent._last_reply_cut_off = True
    assert unusable_reply_kind(agent) == "cut_off" and excused_unusable_reply(agent)
    agent._context_window_full = True
    assert unusable_reply_kind(agent) == "context_full"
    assert not excused_unusable_reply(types.SimpleNamespace())


def test_the_lost_output_notice():
    from src.agents.cycle_handler import lost_output_notice
    agent = types.SimpleNamespace(_last_output_tokens=981, _consecutive_output_lost_replies=2)
    text = lost_output_notice(agent)
    assert text.startswith("[Framework Notice]: Your last reply produced 981 output tokens but NOTHING arrived - no text "
                           "and no complete tool call (2 time(s) in a row) - so NOTHING was executed or saved.")
    assert "write its first part (about 80 lines) with file_system 'write', then add the rest with 'append'" in text
    assert "never 'src/app.js.part1'" in text and "empty" not in text.lower()


def test_the_guardian_does_not_count_an_attempted_action():
    from src.agents.cycle_components.agent_health_monitor import AgentHealthRecord
    record = AgentHealthRecord("W2")
    record.record_response("", False, False, "worker_work", attempted_action=True)
    record.record_response("", False, False, "worker_work", attempted_action=True)
    assert record.consecutive_empty_responses == 0 and record.cycle_count_in_current_state == 1
    record.record_response("", False, False, "worker_work")
    assert record.consecutive_empty_responses == 1
    record.record_response("", False, False, "worker_work", attempted_action=True)   # keeps the count, does not reset it
    assert record.consecutive_empty_responses == 1
    record.record_response("", False, False, "worker_work")
    assert record.consecutive_empty_responses == 2


def _cycle_setup(state="worker_work"):
    from unittest.mock import AsyncMock, MagicMock
    from src.agents.constants import AGENT_TYPE_WORKER
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    manager = MagicMock(spec=AgentManager)
    manager.db_manager = AsyncMock()
    manager.performance_tracker = AsyncMock()
    manager.send_to_ui = AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value="task")
    manager.current_project, manager.current_session, manager.current_session_db_id = None, "s", None
    manager._agent_cycle_locks = {}
    manager.state_manager = MagicMock()
    manager.state_manager.get_agent_team.return_value = None
    manager.workflow_manager = MagicMock()
    manager.workflow_manager.get_system_prompt.return_value = "worker prompt"
    manager.agents = {}
    agent = MagicMock(spec=Agent)
    agent.agent_id, agent.agent_type, agent.state, agent.status = "W2", AGENT_TYPE_WORKER, state, AGENT_STATUS_IDLE
    agent.llm_provider, agent.provider_kwargs, agent.max_tokens = None, {}, None
    agent.provider_name, agent.model, agent.persona = "ollama-local", "qwen3.5:9b-q4_K_M", "Snake Game Engineer"
    agent.message_history = [{"role": "system", "content": "worker prompt"}]
    agent._failover_state, agent._failed_models_this_cycle = {}, set()
    agent._last_reply_cut_off = agent._think_off_next_call = agent._context_window_full = False
    agent._empty_response_retry_count = 0
    agent._last_output_tokens, agent._consecutive_output_lost_replies = 981, 1
    agent.needs_priority_recheck = False
    agent.active_task_id = None
    agent.set_state = MagicMock(side_effect=lambda new_state: setattr(agent, "state", new_state))
    handler = AgentCycleHandler(manager, MagicMock())
    return handler, agent


def _lost_reply_cycle(handler, agent, streak):
    record = handler._health_monitor._health_records.get(agent.agent_id)
    if record is not None:
        record.last_meaningful_action_time -= 120   # the Guardian skips agents that acted in the last 30 s
    async def events(*_args, **_kwargs):
        agent._last_reply_output_lost, agent._consecutive_unusable_replies = True, streak
        agent._consecutive_output_lost_replies = streak
        agent._last_reply_thinking_chars, agent._last_reply_thinking_tail = 0, ""
        if False:
            yield {}
    from unittest.mock import MagicMock
    agent.process_message = MagicMock(side_effect=events)
    with quiet():
        asyncio.run(handler.run_cycle(agent, 0))


def test_lost_replies_are_retried_with_their_notice_and_never_blocked():
    from src.agents.core import UNUSABLE_REPLY_RETRY_CAP
    from src.agents.cycle_handler import lost_output_notice
    handler, agent = _cycle_setup()
    for streak in range(1, UNUSABLE_REPLY_RETRY_CAP + 1):
        _lost_reply_cycle(handler, agent, streak)
        notices = [m["content"] for m in agent.message_history if "Framework Notice" in str(m.get("content"))]
        assert notices[-1] == lost_output_notice(agent)
        assert agent._empty_response_retry_count == 0 and agent.state == "worker_work"
    record = handler._health_monitor._health_records["W2"]
    assert record.consecutive_empty_responses == 0
    assert not any("Your last response was empty" in str(m.get("content")) for m in agent.message_history)
    # beyond the cap a lost reply is handled like an empty one again (nudge 1/2, counted by the Guardian) ...
    _lost_reply_cycle(handler, agent, UNUSABLE_REPLY_RETRY_CAP + 1)
    assert agent._empty_response_retry_count == 1 and record.consecutive_empty_responses == 1
    assert agent.state == "worker_work"
    # ... and two of them make the Guardian intervene, as for any two empty replies
    _lost_reply_cycle(handler, agent, UNUSABLE_REPLY_RETRY_CAP + 2)
    assert agent.state == "worker_wait"


# ======================================================================================== 2. syntax errors block
def _fs(tmp_path, agent="W2", **kwargs):
    from src.tools.file_system import FileSystemTool
    return run(FileSystemTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, scope="private", **kwargs))


@pytest.fixture(autouse=True)
def _no_kept_drafts():
    from src.tools import rejected_drafts
    rejected_drafts.clear()
    yield
    rejected_drafts.clear()


@needs_node
def test_a_broken_new_javascript_file_is_rejected_kept_and_fixed_in_place(tmp_path):
    path = "src/js/main_game_controller.js"
    rejected = _fs(tmp_path, action="write", filename=path, content=CONTROLLER_BROKEN)
    assert rejected["status"] == "error" and not (tmp_path / path).exists()
    message = rejected["message"]
    assert message.startswith(f"Write rejected: '{path}' is not valid JavaScript (SyntaxError: Unexpected token '{{' at "
                              f"line {BROKEN_LINE}). No changes were saved. '{path}' was NOT created and still does not "
                              "exist. You do NOT have to send the whole file again")
    assert f"'{path}' is created as soon as the text is valid JavaScript." in message
    assert f"{BROKEN_LINE} |     handleKeyDown(e) {{" in message
    assert "a function, method or block that is not closed (a missing '}' or ')')" in message
    assert message.endswith("Fix the faulty line(s) with find_replace or replace_lines (the rejected text is kept), or send "
                            "the COMPLETE corrected file with 'write'. Do not resend the same text.")
    fixed = _fs(tmp_path, action="find_replace", filename=path,
                find_text="                await this.handleInput(keyInfo, 'keyboard');\n",
                replace_text="                await this.handleInput(keyInfo, 'keyboard');\n        });\n    }\n")
    assert fixed["status"] == "success" and fixed["message"].startswith(
        f"[Framework: '{path}' did not exist; your change was applied to your rejected text of it, which is now valid "
        f"JavaScript, so '{path}' was created.]")
    assert (tmp_path / path).read_text(encoding="utf-8") == CONTROLLER_FIXED


@needs_node
def test_a_still_broken_draft_stays_kept(tmp_path):
    path = "src/js/main_game_controller.js"
    _fs(tmp_path, action="write", filename=path, content=CONTROLLER_BROKEN)
    still = _fs(tmp_path, action="replace_lines", filename=path, start_line=1, end_line=1,
                content="class GameController {  // the controller\n")
    assert still["status"] == "error" and not (tmp_path / path).exists()
    assert still["message"].startswith(f"Your change was applied to your kept (rejected) text of '{path}', but it is still "
                                       f"not valid JavaScript (SyntaxError: Unexpected token '{{' at line {BROKEN_LINE}), so "
                                       f"'{path}' still does not exist.")


def test_a_json_file_written_in_two_parts(tmp_path):
    first = _fs(tmp_path, action="write", filename="package.json", content='{\n  "name": "wifi-snake",\n')
    assert first["status"] == "error" and not (tmp_path / "package.json").exists()
    assert "or add a missing end with 'append'" in first["message"]
    done = _fs(tmp_path, action="append", filename="package.json", content='  "version": "1.0.0"\n}\n')
    assert done["status"] == "success" and done["message"].startswith(
        "[Framework: 'package.json' did not exist; your append was applied to your rejected text of it, which is now valid "
        "JSON, so 'package.json' was created.]")
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == '{\n  "name": "wifi-snake",\n  "version": "1.0.0"\n}\n'


def test_an_append_that_starts_a_new_file_is_kept_too(tmp_path):
    first = _fs(tmp_path, action="append", filename="data/levels.json", content='[\n  {"level": 1},\n')
    assert first["status"] == "error" and not (tmp_path / "data/levels.json").exists()
    assert first["message"].startswith("Append rejected: 'data/levels.json' would not be valid JSON after it")
    assert "your rejected text of 'data/levels.json' is kept" in first["message"]
    done = _fs(tmp_path, action="append", filename="data/levels.json", content='  {"level": 2}\n]\n')
    assert done["status"] == "success"
    assert (tmp_path / "data/levels.json").read_text(encoding="utf-8") == '[\n  {"level": 1},\n  {"level": 2}\n]\n'


def test_a_python_file_written_in_parts_uses_the_kept_text(tmp_path):
    part_one = "class Board:\n    def __init__(self):\n"
    first = _fs(tmp_path, action="write", filename="src/board.py", content=part_one)
    assert first["status"] == "error" and not (tmp_path / "src/board.py").exists()
    done = _fs(tmp_path, action="append", filename="src/board.py", content="        self.cells = []\n")
    assert done["status"] == "success" and "now valid Python" in done["message"]
    assert (tmp_path / "src/board.py").read_text(encoding="utf-8") == part_one + "        self.cells = []\n"


@needs_node
def test_every_edit_that_breaks_valid_javascript_is_rejected(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    path = "src/js/main_game_controller.js"
    assert _fs(tmp_path, action="write", filename=path, content=CONTROLLER_FIXED)["status"] == "success"
    closing = "        });\n    }\n"
    attempts = {
        "find_replace": dict(action="find_replace", filename=path, find_text=closing, replace_text=""),
        "regex_replace": dict(action="regex_replace", filename=path, regex_pattern=r"^        \}\);\n", replace_text=""),
        "replace_lines": dict(action="replace_lines", filename=path, start_line=13, end_line=13, content="        )\n"),
        "insert_lines": dict(action="insert_lines", filename=path, insert_line=2, content="    {\n"),
        "search_replace_block": dict(action="search_replace_block", filename=path, search_block=closing, replace_block="\n"),
        "append": dict(action="append", filename=path, content="class {\n"),
    }
    for name, kwargs in attempts.items():
        result = _fs(tmp_path, **kwargs)
        assert result["status"] == "error", (name, result)
        assert "would not be valid JavaScript" in result["message"], (name, result["message"])
        assert (tmp_path / path).read_text(encoding="utf-8") == CONTROLLER_FIXED, name
    edited = run(CodeEditorTool().execute("W2", tmp_path, scope="private", action="replace_chunks", filename=path,
                                          chunks=[{"search": closing, "replace": ""}]))
    assert edited["status"] == "error" and edited["message"].startswith(
        "Code edit rejected: the result would not be valid JavaScript (SyntaxError:")
    assert (tmp_path / path).read_text(encoding="utf-8") == CONTROLLER_FIXED
    forced = _fs(tmp_path, action="write", filename=path, content=CONTROLLER_BROKEN, force_overwrite=True)
    assert forced["status"] == "error" and f"The existing '{path}' is unchanged." in forced["message"]
    assert (tmp_path / path).read_text(encoding="utf-8") == CONTROLLER_FIXED


@needs_node
def test_a_file_that_is_already_broken_can_be_repaired_step_by_step(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    path = tmp_path / "src/js/main_game_controller.js"
    path.parent.mkdir(parents=True)
    path.write_text(CONTROLLER_BROKEN, encoding="utf-8")               # saved broken before Round 2x
    step = _fs(tmp_path, action="find_replace", filename="src/js/main_game_controller.js",
               find_text="this.isRunning = false;", replace_text="this.isRunning = false;  // not started")
    assert step["status"] == "success"
    assert ("[Framework check - the edit WAS saved (the file was already invalid before it), but the file is STILL NOT "
            "valid JavaScript:]") in step["message"]
    repaired = run(CodeEditorTool().execute("W2", tmp_path, scope="private", action="replace_chunks",
                                            filename="src/js/main_game_controller.js", chunks=[{
                                                "search": "                await this.handleInput(keyInfo, 'keyboard');\n",
                                                "replace": "                await this.handleInput(keyInfo, 'keyboard');\n"
                                                           "        });\n    }\n"}]))
    assert repaired["status"] == "success" and "Framework check" not in repaired["message"]


@needs_bash
def test_shell_scripts_are_checked_the_same_way(tmp_path):
    rejected = _fs(tmp_path, action="write", filename="scripts/start.sh", content="#!/bin/bash\nif [ -f x ]; then\n echo hi\n")
    assert rejected["status"] == "error" and rejected["message"].startswith(
        "Write rejected: 'scripts/start.sh' is not valid shell script (")
    assert "'fi', 'done', 'esac', '}'" in rejected["message"]
    done = _fs(tmp_path, action="append", filename="scripts/start.sh", content="fi\n")
    assert done["status"] == "success" and (tmp_path / "scripts/start.sh").exists()


def test_what_is_not_blocked(tmp_path):
    from src.tools.source_check import blocking_issue, check_kind, edit_rejection
    empty = _fs(tmp_path, action="write", filename="data/scores.json", content="")
    assert empty["status"] == "success" and "the file is empty" in empty["message"]    # reported, not refused
    eslint = '{\n  // our rules\n  "rules": {}\n}\n'
    assert _fs(tmp_path, action="write", filename=".eslintrc.json", content=eslint)["status"] == "success"
    assert check_kind(".devcontainer/devcontainer.json") is None and check_kind("tslint.json") is None
    assert check_kind("package.json") == "JSON"
    assert _fs(tmp_path, action="write", filename="notes.txt", content="default: {")["status"] == "success"
    assert blocking_issue("  \n", "a.json") is None and blocking_issue('{"a": 1}', "a.json") is None
    assert edit_rejection("a.json", '{"a": 1,}', '{"a": 2,}') is None        # was already broken: never blocked
    assert edit_rejection("a.json", '{"a": 1}', '{"a": 2,}').startswith(
        "Edit rejected: the result would not be valid JSON (Illegal trailing comma before end of object at line 1).")


def test_the_tool_help_describes_the_new_checks():
    from src.tools.code_editor import CodeEditorTool
    from src.tools.file_system import FileSystemTool
    fs_tool, editor = FileSystemTool(), CodeEditorTool()
    assert "Python, JavaScript (.js/.mjs/.cjs), JSON and shell (.sh/.bash) files must stay valid" in fs_tool.description
    assert "A file that is already invalid can still be edited to repair it." in fs_tool.get_detailed_usage()
    assert "Python, JavaScript, JSON and shell content must be valid." in fs_tool.get_detailed_usage(sub_action="write")
    append_help = fs_tool.get_detailed_usage(sub_action="append")
    assert "the appended text is added to your kept (rejected) text instead" in append_help
    for sub_action in ("replace_lines", "find_replace"):
        assert "NEW Python, JavaScript, JSON or shell file" in fs_tool.get_detailed_usage(sub_action=sub_action)
    assert "valid Python, JavaScript, JSON or shell file is rejected" in editor.description
    assert "valid JavaScript (.js/.mjs/.cjs), JSON and shell (.sh/.bash) files must" in editor.get_detailed_usage()
    # Round 3v: the rule moved from the worker_work prompt into the one [TOOL USE] file rule (src/agents/tool_guidance.py)
    from src.agents.tool_guidance import FILE_CHANGE_RULE
    assert "If the 'write' of a NEW file is rejected for a syntax error, the file does NOT exist but your text is kept" in FILE_CHANGE_RULE


# ======================================================================================== 3. tasks that wait
@pytest.fixture()
def tw_env(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    import src.tools.project_management as pm
    from src.config.settings import settings
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm, "broadcast", no_broadcast)
    (tmp_path / "projects" / "P" / "S" / "shared_workspace").mkdir(parents=True)

    def call(agent, **kw):
        with quiet():
            return run(pm.ProjectManagementTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P",
                                                          session_name="S", **kw))

    def get(uuid):
        tw = pm.ProjectManagementTool()._get_taskwarrior_instance("P", "S")
        return tw.tasks.get(uuid=uuid)
    return call, get


@needs_tw
def test_a_waiting_task_is_assigned_but_not_set_doing(tw_env):
    call, get = tw_env
    task_1 = call("PM1", action="add_task", description="Kick-off Task 1: Implement the core game engine",
                  assignee_agent_id="W2")["task_uuid"]
    task_2 = call("PM1", action="add_task", description="Kick-off Task 2: Create responsive frontend design (CSS)",
                  depends=task_1)["task_uuid"]
    # 11:34:52 PM1: assign Kick-off Task 2 to W1 and set it 'doing'
    result = call("PM1", action="modify_task", task_id=task_2, assignee_agent_id="W1", task_progress="doing")
    assert result["status"] == "success" and result["task_progress"] == "todo" and result["assignee"] == "W1"
    assert result["modified_fields"] == ["assignee", "tags"]
    assert result["message"] == (
        f"Task '{task_2}' modified successfully. Note: task_progress was NOT set to started: Task '{task_2}' depends on "
        f"unfinished task(s) 'Kick-off Task 1: Implement the core game engine' ({task_1[:8]}), so it cannot start yet. It "
        "stays waiting; its assignee is started automatically as soon as they are finished.")
    assert get(task_2)["task_progress"] == "todo" and get(task_2)["assignee"] == "W1"
    only_progress = call("PM1", action="modify_task", task_id=task_2, task_progress="in_progress")
    assert only_progress["status"] == "error" and only_progress["message"].startswith(f"Task '{task_2}' was NOT changed.")
    assert get(task_2)["task_progress"] == "todo"
    # once Task 1 is finished, Task 2 may start
    assert call("W2", action="modify_task", task_id=task_1, task_progress="finished")["status"] == "success"
    started = call("W1", action="modify_task", task_id=task_2, task_progress="doing")
    assert started["status"] == "success" and started["task_progress"] == "doing" and "Note:" not in started["message"]


@needs_tw
def test_a_new_waiting_task_starts_as_todo(tw_env):
    call, get = tw_env
    task_1 = call("PM1", action="add_task", description="Kick-off Task 1: Implement the core game engine")["task_uuid"]
    added = call("PM1", action="add_task", description="Kick-off Task 3: Write the tests", depends=task_1,
                 task_progress="doing", assignee_agent_id="W1")
    assert added["status"] == "success" and "Note: task_progress was NOT set to started: The new task depends on" in added["message"]
    assert get(added["task_uuid"])["task_progress"] == "todo"
    free = call("PM1", action="add_task", description="Kick-off Task 4: Write the README", task_progress="doing")
    assert get(free["task_uuid"])["task_progress"] == "doing" and "Note:" not in free["message"]


def test_the_project_management_help_says_waiting_tasks_do_not_start():
    from src.tools.project_management import ProjectManagementTool
    tool = ProjectManagementTool()
    progress = next(p for p in tool.parameters if p.name == "task_progress")
    assert "A task that depends on unfinished tasks is never set to 'doing'/'in_progress'" in progress.description
    assert "'doing'/'in_progress' is not set while the task depends on unfinished tasks" in tool.get_detailed_usage(
        sub_action="modify_task")
    assert "A task with unfinished dependencies starts as 'todo'." in tool.get_detailed_usage(sub_action="add_task")


def test_the_kickoff_prompt_asks_for_parallel_tasks():
    import yaml
    prompts = yaml.safe_load(Path("prompts.yaml").read_text(encoding="utf-8"))
    text = "\n".join(str(v) for v in prompts.values() if isinstance(v, str))
    assert "as a dependency chain" not in text
    assert "Do NOT simply chain every task to the one before it." in text
    assert ('{{"id": "task_3", "depends_on": "task_1", "description": "Write docs/README.md with usage instructions"}}'
            in text)
    assert '{{"id": "task_4", "depends_on": ["task_2", "task_3"]' in text


# ======================================================================================== 4. state change records
W2_12_19_32 = ("[Action Taken: Transitioned to state 'worker_report', indicating core game engine modules are tested and "
               "ready for user-facing README.md creation in docs/ directory.]")


def test_a_state_change_is_recorded_as_a_framework_note():
    from src.agents.tool_activity import call_imitation_notice, is_call_imitation_text, state_change_record
    record = state_change_record("worker_report", "f8f08bb0-8f1a-45a8-ab19-fc886b8f5db6")
    assert record == ("[Framework Record]: request_state received - transitioning to state 'worker_report' for task "
                      "f8f08bb0-8f1a-45a8-ab19-fc886b8f5db6.")
    assert is_call_imitation_text(record) and is_call_imitation_text(W2_12_19_32)
    notice = call_imitation_notice(record)
    assert "call the 'request_state' tool" in notice and "'[Framework Record]:'" in notice
    source = Path("src/agents/cycle_handler.py").read_text(encoding="utf-8")
    assert "[Action Taken: Transitioned to state '{" not in source     # the f-strings that wrote the agent's "reply"


def test_planning_is_still_recognised_after_the_new_record():
    from src.agents.tool_activity import project_request_needs_planning, state_change_record
    request = ("Please start a small project called \"Temp Converter CLI\".\n\nBuild a Python command-line temperature "
               "converter using only the standard library (argparse) and pytest:\n1. A module with two functions, "
               "celsius_to_fahrenheit(c) and fahrenheit_to_celsius(f).\n2. Pytest tests in tests/test_temp_convert.py.\n"
               "3. A README.md with usage examples.")                                  # the Round 2g request
    history = [{"role": "system", "content": "admin prompt"}, {"role": "user", "content": request}]
    assert project_request_needs_planning(history, None)
    assert not project_request_needs_planning(history + [{"role": "system", "content": state_change_record("planning")}],
                                              None)


# ======================================================================================== 5. the stall watchdog
def test_the_watchdog_names_states_of_the_agents_own_role():
    from src.agents.cycle_handler import watchdog_intervention_text
    pm = types.SimpleNamespace(agent_type="pm", state="pm_report_check", _cycles_without_transition=10)
    text = watchdog_intervention_text(pm)
    assert "'pm_manage' (to assign or review tasks) or 'pm_standby' (to wait for your workers)" in text
    assert "worker_wait" not in text and "for 10 cycles" in text
    worker = types.SimpleNamespace(agent_type="worker", state="worker_work", _cycles_without_transition=15)
    # Round 3v: a blocked worker reports - worker_wait is refused while its own task is open
    assert "'worker_report' (to tell your PM that your task is done, or what blocks you)" in watchdog_intervention_text(worker)
    assert "worker_wait" not in watchdog_intervention_text(worker)
    admin = types.SimpleNamespace(agent_type="admin", state="admin_work", _cycles_without_transition=10)
    assert "'admin_standby'" in watchdog_intervention_text(admin)


def test_every_state_change_restarts_the_watchdog_count(monkeypatch):
    agent = _core_worker(_FakeProvider([]), monkeypatch, state="pm_report_check")
    agent._cycles_without_transition, agent._last_state_for_watchdog = 9, "pm_report_check"
    with quiet():
        agent.set_state("pm_standby")
    assert agent._cycles_without_transition == 0 and agent._last_state_for_watchdog is None
    agent._cycles_without_transition = 4
    with quiet():
        agent.set_state("pm_standby")                     # no change: nothing restarts
    assert agent._cycles_without_transition == 4

# END OF FILE tests/unit/test_round2x.py
