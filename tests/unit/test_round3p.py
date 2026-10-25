# START OF FILE tests/unit/test_round3p.py
"""
Round 3p: the run of 2026-10-25 20:00-20:56 (WiFi_Snake_Game_With_Leaderboard, qwen3.5:4b-q4_K_M on a laptop's Ollama).

* The .env set OLLAMA_MAX_CTX_CAP=24576 and OLLAMA_CONCURRENCY_LIMIT=1 in its "laptop" block and 32768 / 3 further down (the
  "Ollama Specific Overrides" block of .env.example): the LAST values applied, and nothing said so.
* config.yaml gave admin_ai provider 'vllm' (no vLLM server) with model qwen3.5:4b-q4_K_M, which Ollama had: auto-selection
  picked qwen3.5:9b-q4_K_M, so the laptop's Ollama held a second, larger model.
* admin_ai on 'ollama-local' got num_predict 8192 instead of ADMIN_AI_LOCAL_MAX_TOKENS (4096): the test for local providers
  checked the prefix 'ollama-local-'.
* 20:15-20:56 W1 kept adding a leaderboard and multiplayer state (the work of Kick-off Tasks 3 and 5) to its finished
  src/game.js; the watchdog at cycle 10 did not say that the task's file was complete.
* 20:43:03 W1 re-sent lines 151-165 of src/game.js unchanged as 'search' and 'replace'.
* 20:56:32 a reply cut off while writing a tool call (1,536 output tokens, 385 thinking characters) switched thinking off
  "because the last reply only thought".
"""
import asyncio
import importlib.util
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT, SESSION = "WiFi_Snake_Game_With_Leaderboard", "startup_1792929654"

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


# The run's .env (shortened: the laptop block, then the overrides block of .env.example)
ENV_TEXT = """### --- When using Ollama on BigBOY.
# OLLAMA_MAX_CTX_CAP=49152
# OLLAMA_CONCURRENCY_LIMIT=3

### --- When using Ollama on laptop.
SUMMARIZER_TRIGGER_THRESHOLD=12288
WORKER_WORK_STATE_MAX_TOKENS=3072
OLLAMA_MAX_CTX_CAP=24576
OLLAMA_CONCURRENCY_LIMIT=1
OPENROUTER_API_KEY_1=sk-or-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

###
# --- Ollama Specific Overrides ---
###
# Hard cap for the context window sent to Ollama (num_ctx configuration)
OLLAMA_MAX_CTX_CAP=32768
OLLAMA_CONCURRENCY_LIMIT="3"  # quoted, with a comment
WORKER_WORK_STATE_MAX_TOKENS=3072
OPENROUTER_API_KEY_1=sk-or-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""


# ======================================================================================== 1. a key set twice in .env
def test_keys_set_twice_with_different_values_are_reported(tmp_path):
    from src.config.settings import env_file_duplicates, env_duplicate_warnings
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    duplicates = env_file_duplicates(env)
    assert duplicates == {
        "OLLAMA_MAX_CTX_CAP": [(8, "24576"), (16, "32768")],
        "OLLAMA_CONCURRENCY_LIMIT": [(9, "1"), (17, "3")],
        "OPENROUTER_API_KEY_1": [(10, "sk-or-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), (19, "sk-or-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")],
    }  # the same value twice (WORKER_WORK_STATE_MAX_TOKENS) and commented lines are not reported
    warnings = env_duplicate_warnings(env)
    assert warnings[0] == (".env sets OLLAMA_MAX_CTX_CAP 2 times with different values (line 8: '24576', line 16: "
                           "'32768'). The LAST one (line 16) applies; remove or comment out the others.")
    assert warnings[1].startswith(".env sets OLLAMA_CONCURRENCY_LIMIT 2 times with different values (line 9: '1', "
                                  "line 17: '3').")
    assert warnings[2] == (".env sets OPENROUTER_API_KEY_1 2 times with different values (line 10: <hidden>, line 19: "
                           "<hidden>). The LAST one (line 19) applies; remove or comment out the others.")
    assert "sk-or" not in " ".join(warnings)
    assert env_duplicate_warnings(tmp_path / "missing.env") == []


def test_the_value_python_dotenv_really_uses_is_the_last_one(tmp_path, monkeypatch):
    # the warning describes python-dotenv's behaviour correctly
    from dotenv import dotenv_values
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    values = dotenv_values(env)
    assert values["OLLAMA_MAX_CTX_CAP"] == "32768" and values["OLLAMA_CONCURRENCY_LIMIT"] == "3"


def test_the_startup_logs_the_warning():
    source = (REPO / "src" / "main.py").read_text(encoding="utf-8")
    start = source.index('logger.info("Application startup sequence initiated...")')
    assert "env_duplicate_warnings(BASE_DIR / '.env')" in source[start:start + 600]
    assert 'logger.warning(f"Configuration: {env_warning}")' in source[start:start + 600]


def test_collect_marks_keys_set_twice():
    spec = importlib.util.spec_from_file_location("te_collect", REPO / "collect.py")
    collect = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collect)
    text, values, hidden = collect.redact_env_file(ENV_TEXT)
    assert values["OLLAMA_MAX_CTX_CAP"] == "32768"
    tail = text.split("KEYS SET MORE THAN ONCE", 1)[1]
    assert "#   OLLAMA_MAX_CTX_CAP (line 8: 24576, line 16: 32768) -> line 16 applies" in tail
    assert "#   OLLAMA_CONCURRENCY_LIMIT (line 9: 1, line 17: \"3\") -> line 17 applies" in tail
    assert "WORKER_WORK_STATE_MAX_TOKENS" not in tail and "sk-or" not in text
    assert "KEYS SET MORE THAN ONCE" not in collect.redact_env_file("A=1\nB=2\n")[0]


def test_the_env_example_explains_the_threshold_and_single_keys():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "Used only for models whose context window is not known" in text
    assert "Set each key ONCE in .env: when a key appears" in text


# ======================================================================================== 2. the configured model is kept
class _Registry:
    def __init__(self, models, reachable=("ollama-local",)):
        self.models, self._reachable_providers = models, {name: "http://localhost:11434" for name in reachable}

    def is_model_available(self, provider, model):
        return model in self.models.get(provider, ())

    def get_formatted_available_models(self):
        return "models"


def test_the_configured_model_on_another_local_provider():
    from src.agents.agent_lifecycle import same_model_on_other_local_provider
    registry = _Registry({"ollama-local": ["qwen3.5:4b-q4_K_M", "qwen3.5:9b-q4_K_M"]})
    manager = types.SimpleNamespace(available_local_providers_list={"ollama": ["ollama-local"]})
    assert same_model_on_other_local_provider(manager, registry, "vllm", "qwen3.5:4b-q4_K_M") == \
        ("ollama-local", "ollama/qwen3.5:4b-q4_K_M")
    assert same_model_on_other_local_provider(manager, registry, "vllm", "vllm/qwen3.5:4b-q4_K_M") == \
        ("ollama-local", "ollama/qwen3.5:4b-q4_K_M")
    assert same_model_on_other_local_provider(manager, registry, "vllm", "llama3:70b") is None
    assert same_model_on_other_local_provider(manager, registry, "ollama", "qwen3.5:4b-q4_K_M") is None  # same type
    assert same_model_on_other_local_provider(manager, registry, "openrouter", "qwen3.5:4b-q4_K_M") is None


def test_bootstrap_keeps_the_configured_model_and_the_local_output_budget(tmp_path, monkeypatch):
    import src.agents.agent_lifecycle as lifecycle
    from src.config.settings import settings
    registry = _Registry({"ollama-local": ["qwen3.5:4b-q4_K_M", "qwen3.5:9b-q4_K_M"]})
    monkeypatch.setattr(lifecycle, "model_registry", registry)
    monkeypatch.setattr(lifecycle, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "AGENT_CONFIGURATIONS", [
        {"agent_id": "admin_ai", "config": {"provider": "vllm", "model": "qwen3.5:4b-q4_K_M", "agent_type": "admin"}}])
    created = []

    async def fake_create(manager, agent_id_requested=None, agent_config_data=None, is_bootstrap=False):
        created.append((agent_id_requested, dict(agent_config_data)))
        return False, "not created in this test", None
    monkeypatch.setattr(lifecycle, "_create_agent_internal", fake_create)

    async def no_auto_selection(*_args, **_kwargs):
        raise AssertionError("auto-selection must not run: the configured model is available on Ollama")
    monkeypatch.setattr(lifecycle, "_select_best_available_model", no_auto_selection)
    manager = types.SimpleNamespace(available_local_providers_list={"ollama": ["ollama-local"]},
                                    local_api_usage_round_robin_index={}, model_registry=registry, bootstrap_agents=[],
                                    agents={})
    with quiet():
        run(lifecycle.initialize_bootstrap_agents(manager))
    assert len(created) == 1
    agent_id, config = created[0]
    assert agent_id == "admin_ai"
    assert config["provider"] == "ollama-local" and config["model"] == "ollama/qwen3.5:4b-q4_K_M"
    # 'ollama-local' is a local provider: the Admin AI gets ADMIN_AI_LOCAL_MAX_TOKENS
    assert config["max_tokens"] == settings.ADMIN_AI_LOCAL_MAX_TOKENS


# ======================================================================================== 3. a finished task is named
class Worker:
    def __init__(self, agent_id, agent_type="worker", state="worker_work"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids = [], {}


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


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_1 = ("Kick-off Task 1: Implement the core game engine in src/game.js including snake movement, food spawning, collision "
          "detection, and game loop with debug logging")
TASK_3 = ("Kick-off Task 3: Build player registration and naming system in src/player_manager.js with localStorage-based "
          "persistence for high scores and leaderboards")
TASK_5 = ("Kick-off Task 5: Create state synchronization logic in src/state_sync.js to ensure all connected clients see the "
          "same game state in real-time")
GAME_JS = ("const GRID_SIZE = 20;\nlet snake = [];\n\nfunction update() {\n    snake.pop();\n}\n\n"
           "module.exports = { update, GRID_SIZE };\n")


@needs_tw
def test_a_worker_whose_task_files_are_complete_is_told_it_is_done(tasks):
    from src.agents.task_focus import task_done_hint
    w1, w2 = Worker("W1"), Worker("W2", state="worker_wait")
    manager = _manager(w1, w2)
    t1 = _add(tasks, manager, TASK_1, "W1")
    _add(tasks, manager, TASK_3)
    _add(tasks, manager, TASK_5, "W2")
    w1.active_task_id = t1
    # the file is still missing: no hint
    assert task_done_hint(manager, w1) == ""
    (tasks.workspace / "src").mkdir()
    (tasks.workspace / "src" / "game.js").write_text(GAME_JS, encoding="utf-8")
    hint = task_done_hint(manager, w1)
    assert hint.startswith("[Framework check]: The file(s) your task names (src/game.js) exist and pass the framework's "
                           "checks. If they do what your task asks ('Kick-off Task 1: Implement the core game engine")
    assert f"task_id='{t1}', task_progress='finished')" in hint
    assert ("These belong to OTHER tasks - do not build them in your files: src/player_manager.js ('Kick-off Task 3: Build "
            "player registration and naming system in src...'); src/state_sync.js ('Kick-off Task 5: Create state "
            "synchronization logic in src/state_sy...').") in hint
    # a file with a syntax error is not complete
    (tasks.workspace / "src" / "game.js").write_text("function update() {\n    snake.pop();\n", encoding="utf-8")
    if shutil.which("node"):
        assert task_done_hint(manager, w1) == ""
    # other states, agents and unknown tasks get nothing
    (tasks.workspace / "src" / "game.js").write_text(GAME_JS, encoding="utf-8")
    w1.state = "worker_report"
    assert task_done_hint(manager, w1) == ""
    w1.state, w1.active_task_id = "worker_work", None
    assert task_done_hint(manager, w1) == ""
    assert task_done_hint(manager, Worker("PM1", "pm", "pm_manage")) == ""


@needs_tw
def test_the_watchdog_message_carries_the_hint(tasks, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    w1 = Worker("W1")
    manager_ns = _manager(w1)
    t1 = _add(tasks, manager_ns, TASK_1, "W1")
    (tasks.workspace / "src").mkdir()
    (tasks.workspace / "src" / "game.js").write_text(GAME_JS, encoding="utf-8")
    manager = MagicMock(spec=AgentManager)
    manager.current_project, manager.current_session, manager.current_session_db_id = PROJECT, SESSION, None
    manager.send_to_ui = AsyncMock()
    manager.agents = {"W1": w1}
    handler = AgentCycleHandler(manager, MagicMock())
    agent = MagicMock()
    agent.agent_id, agent.agent_type, agent.state, agent.status = "W1", "worker", "worker_work", "awaiting_user_review_cg"
    agent.active_task_id, agent.current_task_id = t1, None
    agent._cycles_without_transition, agent._last_state_for_watchdog = 9, "worker_work"
    agent.message_history = []
    with quiet():
        run(handler._run_cycle_internal(agent, 0))   # the blocked status stops the cycle right after the watchdog
    messages = [m["content"] for m in agent.message_history if "[Framework Watchdog Intervention]" in m.get("content", "")]
    assert len(messages) == 1
    assert "\n[Framework check]: The file(s) your task names (src/game.js) exist and pass the framework's checks." in messages[0]


# ======================================================================================== 4. an edit that changes nothing
def test_a_no_op_edit_says_that_the_text_is_already_in_the_file(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    block = "    // Check food collision\n    if (head.x === food.x) {\n        snake.unshift(head);\n    }\n"
    (tmp_path / "game.js").write_text("function update() {\n" + block + "}\n", encoding="utf-8")
    with quiet():
        result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename="game.js",
                                              chunks=[{"start_line": 2, "end_line": 5, "search": block, "replace": block}]))
    assert result["status"] == "error"
    assert ("Chunk 0 changes NOTHING: its 'replace' text is exactly its 'search' text. Put the NEW text in 'replace'. That "
            "text is ALREADY in the file (lines 2-5) exactly as you sent it: there is nothing to change there.") in \
        result["message"]
    with quiet():
        result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename="game.js",
                                              chunks=[{"search": "let x = 1;", "replace": "let x = 1;"}]))
    assert "Put the NEW text in 'replace'." in result["message"] and "ALREADY in the file" not in result["message"]


# ======================================================================================== 5. a write cut off by the limit
def test_a_reply_cut_off_while_writing_keeps_thinking_on():
    from src.agents.core import cut_off_while_writing
    assert cut_off_while_writing(1536, 385)          # 20:56:32 W2: ~110 of 1,536 tokens were thinking
    assert cut_off_while_writing(3072, 227)          # 20:30:29 W2
    assert not cut_off_while_writing(1536, 5000)     # a reply that spent its budget thinking
    assert not cut_off_while_writing(0, 100) and not cut_off_while_writing(None, None)


def test_the_core_switches_thinking_off_only_for_a_reply_that_thought(monkeypatch):
    from src.agents.core import Agent
    import src.agents.core as core_module

    class Provider:
        def __init__(self, events):
            self.events = events

        async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
            for event in self.events:
                yield event

    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)
    monkeypatch.setattr(core_module.settings, "WORKER_DECOMPOSE_STATE_MAX_TOKENS", 1536)

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(tool_executor=types.SimpleNamespace(tools={}), cycle_handler=None,
                                    workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow))

    def agent_with(events):
        agent = Agent({"agent_id": "W2", "config": {"agent_type": "worker", "provider": "ollama", "model": "m"}},
                      Provider(events), manager)
        agent.state = "worker_decompose"
        agent.ensure_sandbox_exists = lambda: True
        return agent

    async def consume(agent):
        return [event async for event in agent.process_message()]

    # 20:56:32: cut off while writing - thinking stays on
    writing = agent_with([{"type": "finish_info", "done_reason": "length", "output_tokens": 1536, "thinking_chars": 385,
                           "thinking_tail": "Now I write src/server.js"}])
    with quiet():
        run(consume(writing))
    assert writing._last_reply_cut_off is True and not getattr(writing, "_think_off_next_call", False)
    # a reply that only thought until the limit: thinking off for the retry (unchanged)
    thinking = agent_with([{"type": "finish_info", "done_reason": "length", "output_tokens": 1536, "thinking_chars": 5200,
                            "thinking_tail": "Let me think again"}])
    with quiet():
        run(consume(thinking))
    assert thinking._think_off_next_call is True

# END OF FILE tests/unit/test_round3p.py
