# START OF FILE tests/unit/test_round3n.py
"""
Round 3n: the run of 2026-10-24 23:35 - 2026-10-25 03:49 (WiFi_Multiplayer_Snake_Game, Python, 32k-token window).

* 167 context summaries in 4 hours (W3: 75, median every 3 cycles). Right after a summary the prompt was still ~15,000 of
  the ~22,500 tokens that trigger the next one: the kept messages held whole-file rewrites (code_editor chunks of up to
  27,085 characters) and whole-file reads (src/server.py, 32,657 characters). W3 read tests/test_player_manager.py 55 times.
* 03:47 W2 started `python src/server.py &` twice: "No module named 'src'" (the file imports `from src.snake_game`).
* 03:48:49 `pkill -f "python src/server.py"` killed its own shell (exit code -15).
"""
import asyncio
import json
import logging
import types
from contextlib import contextmanager

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


SERVER = "\n".join(f"{n}: line {n} of src/server.py " + "x" * 60 for n in range(1, 401))     # ~33,000 characters
CHUNK = "def authenticate_player(self, name, password):\n" + "    pass  # long body\n" * 900       # ~20,000 characters
PYTEST_OLD = "============ test session starts ============\n" + "tests/test_player_manager.py::test_x FAILED\n" * 60 + \
    "FAILED tests/test_player_manager.py::test_auth - assert None\n========= 3 failed, 21 passed in 0.31s =========\n"
PYTEST_NEW = "============ test session starts ============\n" + "tests/test_player_manager.py::test_x PASSED\n" * 40 + \
    "========= 24 passed in 0.30s =========\n"


def _call(call_id, name, **arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}]}


def _result(call_id, name, content):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def _history():
    return [
        {"role": "system", "content": "--- Current State: WORK --- (system prompt)"},
        {"role": "user", "content": "[Framework Directive]: work on task 3"},
        _call("r1", "file_system", action="read", filename="src/server.py"),
        _result("r1", "file_system", SERVER),
        _call("e1", "code_editor", action="replace_chunks", filename="src/server.py",
              chunks=[{"search": "def authenticate_player", "replace": CHUNK}]),
        _result("e1", "code_editor", "Successfully applied 1 edits to 'src/server.py'."),
        _call("r2", "file_system", action="read", filename="src/server.py"),
        _result("r2", "file_system", SERVER),
        _call("c1", "command_executor", action="run_command", command="cd /workspace && python -m pytest -q"),
        _result("c1", "command_executor", PYTEST_OLD),
        _call("c2", "command_executor", action="run_command", command="python -m pytest -q"),
        _result("c2", "command_executor", PYTEST_NEW),
        _call("s1", "send_message", target_agent_id="PM1", message_content="all tests pass"),
        _result("s1", "send_message", "Message delivered to 'PM1'."),
        {"role": "system", "content": "[Framework Watchdog Intervention]: 15 cycles in worker_work"},
        {"role": "user", "content": "[System State Change]: State changed to worker_work."},
    ]


# ======================================================================================== 1. compaction
def test_compaction_removes_what_the_agent_no_longer_needs():
    from src.agents.context_compaction import compact_history, COMPACTION_MARKER
    history = _history()
    original = json.dumps(history)
    compacted, saved = compact_history(history, keep_recent=4)
    assert json.dumps(history) == original                                   # the input is not changed
    assert len(compacted) == len(history)
    assert [m.get("role") for m in compacted] == [m.get("role") for m in history]
    assert [m.get("tool_call_id") for m in compacted] == [m.get("tool_call_id") for m in history]
    # the first read of src/server.py: read again later
    assert compacted[3]["content"] == (
        f"[Earlier read of 'src/server.py' ({len(SERVER):,} characters) removed - {COMPACTION_MARKER}: you read it again "
        "later. Read the file again if you need its current text.]")
    # the agent's own 20,000-character chunk: the file holds it
    chunks = compacted[4]["tool_calls"][0]["arguments"]["chunks"]
    assert chunks.startswith("<<") and COMPACTION_MARKER in chunks and "characters of new text" in chunks
    assert compacted[4]["tool_calls"][0]["arguments"]["filename"] == "src/server.py"
    assert compacted[4]["tool_calls"][0]["id"] == "e1"
    # the newest read stays (it is newer than the edit), the older pytest output keeps its first and last lines
    assert compacted[7]["content"] == SERVER
    old_run = compacted[9]["content"]
    assert old_run.startswith(f"[Older output of `python -m pytest -q` shortened - {COMPACTION_MARKER}: you ran it again "
                              "later.]\n============ test session starts ============\n...")
    assert old_run.endswith("========= 3 failed, 21 passed in 0.31s =========")
    assert compacted[11]["content"] == PYTEST_NEW
    assert compacted[12:] == history[12:]                                    # the newest 4 messages are kept as they are
    assert saved > len(SERVER) + len(CHUNK)
    assert compact_history(compacted, keep_recent=4)[1] == 0                 # nothing more to do the second time


def test_a_read_is_out_of_date_only_after_a_successful_change():
    from src.agents.context_compaction import compact_history
    history = [
        {"role": "system", "content": "prompt"},
        _call("r1", "file_system", action="read", filename="src/player_manager.py"),
        _result("r1", "file_system", SERVER),
        _call("e1", "file_system", action="write", filename="src/player_manager.py", content=CHUNK),
        _result("e1", "file_system", "[Tool Execution Failed] Write rejected: SyntaxError at line 3"),
        _call("x1", "file_system", action="read", filename="tests/test_a.py", start_line=1, end_line=50),
        _result("x1", "file_system", SERVER),
        _call("x2", "file_system", action="read", filename="tests/test_a.py", start_line=50, end_line=99),
        _result("x2", "file_system", SERVER),
        {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
    ]
    compacted, _ = compact_history(history, keep_recent=2)
    assert compacted[2]["content"] == SERVER          # the write failed: the read is still the file's text
    assert compacted[6]["content"] == SERVER          # another range of the same file does not replace it
    assert "applied or refused" in compacted[3]["tool_calls"][0]["arguments"]["content"]
    history[4] = _result("e1", "file_system", "File 'src/player_manager.py' written successfully.")
    compacted, _ = compact_history(history, keep_recent=2)
    assert "the file was changed after this read, so this text is out of date" in compacted[2]["content"]
    # arguments given as a JSON string stay a JSON string
    as_string = [{"role": "assistant", "tool_calls": [{"id": "w", "name": "code_editor", "arguments": json.dumps(
        {"action": "replace_chunks", "filename": "a.py", "chunks": [{"search": "a", "replace": CHUNK}]})}]},
        {"role": "tool", "tool_call_id": "w", "name": "code_editor", "content": "ok"}, {"role": "user", "content": "x"}]
    compacted, _ = compact_history(as_string, keep_recent=1)
    arguments = compacted[0]["tool_calls"][0]["arguments"]
    assert isinstance(arguments, str) and json.loads(arguments)["filename"] == "a.py"
    assert compact_history(None) == ([], 0)


def test_the_placeholder_is_never_written():
    from src.agents.context_compaction import compaction_placeholder_refusal, compact_history
    compacted, _ = compact_history(_history(), keep_recent=4)
    copied = compacted[4]["tool_calls"][0]["arguments"]
    refusal = compaction_placeholder_refusal("code_editor", copied)
    assert refusal.startswith("NOTHING was written: the 'chunks' you sent contains the framework's placeholder")
    assert compaction_placeholder_refusal("file_system", {"action": "write", "filename": "a.py",
                                                          "content": copied["chunks"]}) is not None
    assert compaction_placeholder_refusal("file_system", {"action": "write", "filename": "a.py", "content": "x = 1"}) is None
    assert compaction_placeholder_refusal("file_system", {"action": "read", "filename": "a.py"}) is None
    import inspect
    from src.tools import executor
    assert "compaction_placeholder_refusal(tool_name, kwargs_for_tool)" in inspect.getsource(executor)


# ======================================================================================== 2. compact before summarizing
def _handler():
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.cycle_components.context_summarizer import ContextSummarizer
    handler = object.__new__(AgentCycleHandler)
    handler._context_summarizer = ContextSummarizer(types.SimpleNamespace(agents={}))
    return handler


def test_compaction_can_make_the_summary_unnecessary():
    handler = _handler()
    stored = _history()
    agent = types.SimpleNamespace(agent_id="W3", message_history=list(stored), _last_prompt_tokens=25000)
    reports = [{"role": "system", "content": "[SHARED WORKSPACE TREE (Current State)] ..."}]
    context = types.SimpleNamespace(history_for_call=[dict(stored[0])] + stored[1:] + reports)
    before = handler._context_summarizer.estimate_token_count(context.history_for_call)
    with quiet():
        assert handler._compacted_instead_of_summary(agent, context, before, 25000, 32768) is True
    after = handler._context_summarizer.estimate_token_count(context.history_for_call)
    assert after < before // 2
    assert context.history_for_call[-1] == reports[0]                      # the appended reports stay last
    assert context.history_for_call[1:-1] == agent.message_history[1:]
    assert agent._last_prompt_tokens == 25000 - (before - after)
    # still too big after compaction: the summary runs (with the compacted history)
    big = _history() + [_call("r9", "file_system", action="read", filename="docs/all.md"),
                        _result("r9", "file_system", "y" * 80000)]
    agent = types.SimpleNamespace(agent_id="W3", message_history=list(big), _last_prompt_tokens=0)
    context = types.SimpleNamespace(history_for_call=list(big))
    with quiet():
        assert handler._compacted_instead_of_summary(
            agent, context, handler._context_summarizer.estimate_token_count(big), 0, 32768) is False
    assert "compacted by the framework" in agent.message_history[3]["content"]


def test_the_cycle_compacts_before_it_summarizes():
    import inspect
    from src.agents import cycle_handler
    source = inspect.getsource(cycle_handler)
    assert "self._compacted_instead_of_summary(agent, context, estimated_tokens, real_prompt_tokens," in source
    assert source.index("_compacted_instead_of_summary(agent, context, estimated_tokens") < \
        source.index("success, summarized_context = await self._context_summarizer.summarize_agent_context(")


def test_the_messages_kept_after_a_summary_are_compacted(monkeypatch):
    from src.agents.cycle_components.context_summarizer import ContextSummarizer
    from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID
    summarizer = ContextSummarizer(types.SimpleNamespace(agents={CONSTITUTIONAL_GUARDIAN_AGENT_ID: object()}))

    async def summary(self, chunk, number, agent_id):
        return f"summary {number}"
    monkeypatch.setattr(ContextSummarizer, "_summarize_chunk", summary)
    history = _history()[:2] + [{"role": "user", "content": f"step {n}"} for n in range(12)] + [
        _call("e1", "code_editor", action="replace_chunks", filename="src/server.py",
              chunks=[{"search": "def authenticate_player", "replace": CHUNK}]),
        _result("e1", "code_editor", "Successfully applied 1 edits to 'src/server.py'."),
        {"role": "user", "content": "x"}, {"role": "user", "content": "y"}]
    with quiet():
        ok, condensed = run(summarizer.summarize_agent_context("W3", history))
    assert ok
    kept_call = next(m for m in condensed if m.get("tool_calls"))
    assert "compacted by the framework" in kept_call["tool_calls"][0]["arguments"]["chunks"]
    assert condensed[-2:] == history[-2:]


# ======================================================================================== 3. command notes
def test_a_script_of_a_package_folder_is_run_as_a_module(tmp_path):
    from src.tools.command_hints import command_notes
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.py").write_text("import os\nfrom src.snake_game import SnakeGame\n")
    note = ("`python src/server.py` puts src/ itself on the import path, so `from src.<module> import ...` inside it fails "
            "with \"No module named 'src'\". Run it from the project folder as a module instead: `python -m src.server` "
            "(add ' &' to keep a server running). Do not change the imports for this.")
    assert command_notes(tmp_path, "cd /workspace && python src/server.py &", 0, "") == [note]
    assert command_notes(tmp_path, "python3 -u src/server.py", 1, "") == [note]
    (tmp_path / "src" / "server.py").write_text("from snake_game import SnakeGame\n")
    assert command_notes(tmp_path, "python src/server.py", 0, "") == []
    assert command_notes(tmp_path, "python src/other.py", 1,
                         "ModuleNotFoundError: No module named 'src'") == [
        note.replace("src/server.py", "src/other.py").replace("src.server", "src.other")]
    assert command_notes(tmp_path, "python -m src.server", 1, "") == []


def test_a_pkill_that_killed_itself():
    from src.tools.command_hints import command_notes
    notes = command_notes(None, 'pkill -f "python src/server.py"', -15, "")
    assert notes == ["`pkill -f \"python src/server.py\"` killed ITSELF (exit code -15): the shell that ran it has 'python "
                     "src/server.py' in its own command line, so the -f pattern matched it too. The process you meant may "
                     "still be running. Use a pattern that cannot match itself: `pkill -f '[p]ython src/server.py'`."]
    assert command_notes(None, 'pkill -f "python src/server.py"', 0, "") == []
    assert command_notes(None, "kill 1234", -15, "") == []


def test_the_command_result_carries_the_notes(tmp_path, monkeypatch):
    import src.tools.command_executor as ce

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None, **kwargs):
        return types.SimpleNamespace(status="completed", returncode=-15, stdout="", stderr="", message="",
                                     background_log=None, stdout_truncated=False, stderr_truncated=False, cpu_busy=None)
    monkeypatch.setattr(ce, "run_sandboxed", fake_run)
    with quiet():
        answer = run(ce.CommandExecutionTool().execute(agent_id="W2", agent_sandbox_path=tmp_path, scope="private",
                                                       action="run_command", command='pkill -f "python src/server.py"'))
    assert "[Framework notes on this command:]\n- `pkill -f \"python src/server.py\"` killed ITSELF" in answer["content"]

# END OF FILE tests/unit/test_round3n.py
