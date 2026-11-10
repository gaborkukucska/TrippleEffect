# START OF FILE tests/unit/test_round5a.py
"""
Round 5a: the model-facing surface of the tools (no run log this time - measured on the code of Round 4o).

* Every call of a worker in 'worker_work' carried 14 tool schemas of 22,800 characters, a PM's 15 schemas of 24,700 and the
  Admin AI's 17 schemas of 26,500: `file_system` alone 5,700 (26 actions, 28 parameters, ten of them git actions), and
  `asset_search`, `github_tool` and `zim_search` in every call of a worker that writes a JavaScript module.
  src/agents/tool_surface.py shows each tool as the caller's type and state uses it and offers topic tools on request.
* Existing code could only be changed by position (line numbers or an exact search text, inside a JSON array of chunk
  objects whose schema said its items are STRINGS). `code_editor` action 'replace_function' replaces or adds one function,
  class or method by its name (src/utils/symbols.py), and `file_system` 'read' with 'function' reads only that one.
* collect.py v2.9 writes zz_context_budget.txt: what each call spent on system prompt, tool schemas and conversation.
"""
import asyncio
import importlib.util
import json
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(scope="module")
def executor():
    with quiet():
        from src.tools.executor import ToolExecutor
        return ToolExecutor()


@pytest.fixture(autouse=True)
def _focused_surface(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "TOOL_SURFACE", "focused", raising=False)
    monkeypatch.delenv("ZIM_FILES_PATH", raising=False)
    monkeypatch.delenv("GITHUB_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(settings, "GITHUB_ACCESS_TOKEN", None, raising=False)


GAME_PY = '''import os


class Game:
    size = 3

    def __init__(self):
        self.x = 1

    @property
    def score(self):
        return 1

    def update(self, dt):
        return dt


@cache
def helper(a):
    return a
'''

GAME_JS = '''// the game
const WIDTH = 20;
let state = { score: 0, snake: [] };

/**
 * Starts it.
 */
function start(opts = {}) {
  state.score = 0;
  if (opts.x) { run(); }
}

const tick = (dt) => {
  state.score += dt;
};
const double = x => x * 2;

class Game extends Base {
  constructor(w, h) {
    super();
    this.w = w;
  }
  async update(dt) {
    for (const s of this.snakes) {
      if (s) { s.move(); }
    }
  }
}

const api = {
  name: "x{",
  start() {
    return `a ${1} {`;
  },
  stop: function () {
    return 1;
  },
};

exports.helper = (a) => {
  return a;
};
module.exports = { start, tick, Game, api };
'''


# ============================================================================================ 1. definitions by name
def test_python_definitions_with_their_lines():
    from src.utils.symbols import find_symbols
    found = {s.name: (s.kind, s.head_start, s.start, s.end, s.indent) for s in find_symbols(GAME_PY, "src/game.py")}
    assert found["Game"] == ("class", 4, 4, 15, "")
    assert found["Game.__init__"] == ("method", 7, 7, 8, "    ")
    assert found["Game.score"] == ("method", 10, 11, 12, "    ")  # the decorator is the head
    assert found["Game.update"] == ("method", 14, 14, 15, "    ")
    assert found["helper"] == ("function", 18, 19, 20, "")
    assert find_symbols("def broken(:\n", "a.py") is None
    assert find_symbols("anything", "README.md") is None


def test_javascript_definitions_with_their_lines():
    from src.utils.symbols import find_symbols
    found = {s.name: (s.kind, s.head_start, s.start, s.end) for s in find_symbols(GAME_JS, "src/game.js")}
    assert found["start"] == ("function", 5, 8, 11)  # `opts = {}` is not the body; the JSDoc block is the head
    assert found["tick"] == ("function", 13, 13, 15)
    assert found["double"] == ("function", 16, 16, 16)
    assert found["Game"] == ("class", 18, 18, 28)
    assert found["Game.constructor"] == ("method", 19, 19, 22)
    assert found["Game.update"] == ("method", 23, 23, 27)
    assert found["api"] == ("object", 30, 30, 38)
    assert found["api.start"] == ("method", 32, 32, 34)  # braces inside strings and templates do not count
    assert found["api.stop"] == ("method", 35, 35, 37)
    assert found["helper"] == ("function", 40, 40, 42)
    assert "state.score" not in found and "WIDTH" not in found


def test_names_as_models_send_them():
    from src.utils.symbols import clean_name, close_names, find_symbols, lookup
    symbols = find_symbols(GAME_PY, "g.py")
    assert clean_name("def update(self, dt):") == "update"
    assert clean_name("`Game.update()`") == "Game.update"
    assert clean_name("Game::update") == "Game.update"
    assert clean_name("function tick") == "tick"
    assert lookup(symbols, "update")[0].name == "Game.update"  # the only method of that name
    assert lookup(symbols, "self.update")[0].name == "Game.update"
    assert lookup(symbols, "Game.missing") == (None, [])
    assert close_names(symbols, "Helper") == ["helper"]
    two = find_symbols("class A:\n    def run(self):\n        pass\n\nclass B:\n    def run(self):\n        pass\n", "x.py")
    found, candidates = lookup(two, "run")
    assert found is None and [c.name for c in candidates] == ["A.run", "B.run"]


def test_reindent_moves_every_line_by_the_same_amount():
    from src.utils.symbols import reindent, strip_code_fence
    assert reindent("def f(self):\n    if x:\n        return 1\n\n    return 2", "    ") == \
        "    def f(self):\n        if x:\n            return 1\n\n        return 2"
    assert reindent("        def f():\n            pass", "") == "def f():\n    pass"
    assert strip_code_fence("```python\ndef f():\n    pass\n```") == "def f():\n    pass"


# ============================================================================================ 2. replace_function
@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "game.py").write_text(GAME_PY, encoding="utf-8")
    (tmp_path / "game.js").write_text(GAME_JS.replace(" extends Base", "").replace("    super();\n", ""), encoding="utf-8")
    return tmp_path


def _edit(sandbox, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute("W1", sandbox, scope="private", **kwargs))


def test_a_method_is_replaced_by_name_at_its_indentation(sandbox):
    result = _edit(sandbox, action="replace_function", filename="game.py", function="Game.update",
                   code="def update(self, dt):\n    self.x += dt\n    return self.x")
    assert result["status"] == "success", result
    assert result["message"].startswith("Successfully applied 1 edits to 'game.py'.")  # counted as a saved edit
    assert "'Game.update' (was lines 14-15) now holds your new code (lines 14-16)" in result["message"]
    text = (sandbox / "game.py").read_text()
    assert "    def update(self, dt):\n        self.x += dt\n        return self.x\n\n\n@cache\ndef helper(a):" in text
    assert "WARNING" not in result["message"]  # no "these lines are NOT in your new text" for a replaced definition


def test_decorators_stay_unless_the_code_brings_its_own(sandbox):
    _edit(sandbox, action="replace_function", filename="game.py", function="score", code="def score(self):\n    return self.x")
    assert "    @property\n    def score(self):\n        return self.x\n" in (sandbox / "game.py").read_text()
    _edit(sandbox, action="replace_function", filename="game.py", function="helper",
          code="@lru_cache\ndef helper(a):\n    return a + 1")
    text = (sandbox / "game.py").read_text()
    assert "@lru_cache\ndef helper(a):\n    return a + 1\n" in text and "@cache" not in text


def test_a_name_the_file_does_not_have_is_added(sandbox):
    result = _edit(sandbox, action="replace_function", filename="game.py", function="Game.reset",
                   code="def reset(self):\n    self.x = 0")
    assert result["status"] == "success" and "ADDED after 'Game.update'" in result["message"]
    result = _edit(sandbox, action="replace_function", filename="game.py", function="main",
                   code="def main():\n    print(helper(1))")
    assert result["status"] == "success" and "ADDED after 'helper'" in result["message"]
    text = (sandbox / "game.py").read_text()
    assert "        return dt\n\n    def reset(self):\n        self.x = 0\n" in text
    assert text.endswith("    return a\n\n\ndef main():\n    print(helper(1))\n")
    compile(text.replace("@cache\n", ""), "game.py", "exec")


def test_calls_that_would_change_the_wrong_thing_are_refused(sandbox):
    before = (sandbox / "game.py").read_text()
    typo = _edit(sandbox, action="replace_function", filename="game.py", function="Helper", code="def Helper(a):\n    return a")
    assert typo["status"] == "error" and "has no 'Helper', but it has 'helper'" in typo["message"]
    other = _edit(sandbox, action="replace_function", filename="game.py", function="helper", code="def other(a):\n    return a")
    assert other["status"] == "error" and "your 'code' defines 'other'" in other["message"]
    part = _edit(sandbox, action="replace_function", filename="game.py", function="helper", code="    return a +")
    assert part["status"] == "error" and "not valid Python by itself" in part["message"] and "COMPLETE" in part["message"]
    no_class = _edit(sandbox, action="replace_function", filename="game.py", function="Board.draw",
                     code="def draw(self):\n    pass")
    assert no_class["status"] == "error" and "no class or object 'Board'" in no_class["message"]
    missing = _edit(sandbox, action="replace_function", filename="game.py")
    assert missing["status"] == "error" and "missing function, code" in missing["message"]
    assert "action='replace_function'" in missing["message"]
    text_file = sandbox / "notes.md"
    text_file.write_text("# notes\n")
    refused = _edit(sandbox, action="replace_function", filename="notes.md", function="x", code="def x():\n    pass")
    assert refused["status"] == "error" and "replace_lines" in refused["message"]
    assert (sandbox / "game.py").read_text() == before


def test_the_action_is_understood_without_its_name(sandbox):
    result = _edit(sandbox, filename="game.py", function="helper", code="def helper(a, b=2):\n    return a + b")
    assert result["status"] == "success" and "def helper(a, b=2):" in (sandbox / "game.py").read_text()
    result = _edit(sandbox, action="edit_function", filename="game.py", function_name="helper",
                   new_code="```python\ndef helper(a):\n    return -a\n```")
    assert result["status"] == "success" and "    return -a\n" in (sandbox / "game.py").read_text()


def test_a_chunk_with_a_position_stays_a_chunk_and_file_system_passes_the_action_on(sandbox):
    from src.tools.code_editor import function_edit_requested
    assert not function_edit_requested(None, {"filename": "a.py", "function": "f", "search": "a", "replace": "b"})
    assert not function_edit_requested("replace_chunks", {"filename": "a.py", "function": "f", "start_line": 1, "content": "b"})
    from src.tools.file_system import FileSystemTool
    with quiet():
        result = run(FileSystemTool().execute("W1", sandbox, scope="private", action="replace_function",
                                              filename="game.py", function="helper", code="def helper(a):\n    return 7"))
    assert result["status"] == "success" and "belongs to the 'code_editor' tool" in result["message"]
    assert result["message"].startswith("Successfully applied 1 edits to 'game.py'.")
    assert "    return 7\n" in (sandbox / "game.py").read_text()


def test_a_missing_file_is_created_from_the_code(sandbox):
    result = _edit(sandbox, action="replace_function", filename="util.py", function="clamp",
                   code="def clamp(v, lo, hi):\n    return max(lo, min(hi, v))")
    assert result["status"] == "success" and "did not exist" in result["message"]
    assert (sandbox / "util.py").read_text().startswith("def clamp(v, lo, hi):")


@needs_node
def test_javascript_functions_methods_and_object_members(sandbox):
    result = _edit(sandbox, action="replace_function", filename="game.js", function="api.start",
                   code="start() {\n  return 10;\n}")
    assert result["status"] == "success", result
    result = _edit(sandbox, action="replace_function", filename="game.js", function="Game.reset",
                   code="reset() {\n  this.w = 0;\n}")
    assert result["status"] == "success" and "ADDED after 'Game.update'" in result["message"]
    result = _edit(sandbox, action="replace_function", filename="game.js", function="tick",
                   code="const tick = (dt) => {\n  state.score += 2 * dt;\n};")
    assert result["status"] == "success", result
    added = _edit(sandbox, action="replace_function", filename="game.js", function="pause",
                  code="function pause() {\n  return true;\n}")
    assert added["status"] == "success" and "'pause' is NOT exported yet" in added["message"]
    text = (sandbox / "game.js").read_text()
    assert "  start() {\n    return 10;\n  },\n  stop: function () {" in text  # the member keeps its comma
    assert "  }\n\n  reset() {\n    this.w = 0;\n  }\n}" in text
    assert "state.score += 2 * dt;" in text and "/**\n * Starts it.\n */\nfunction start(opts = {}) {" in text
    before = text
    broken = _edit(sandbox, action="replace_function", filename="game.js", function="tick",
                   code="const tick = (dt) => {\n  state.score += dt;\n")
    assert broken["status"] == "error" and "'tick' in 'game.js' was NOT changed" in broken["message"]
    assert "OUT OF DATE" not in broken["message"]  # the agent sent no line numbers
    assert (sandbox / "game.js").read_text() == before


def test_a_failed_chunk_edit_names_the_way_without_positions(sandbox):
    result = _edit(sandbox, action="replace_chunks", filename="game.py",
                   chunks=[{"search": "def update(self):\n    return 0", "replace": "def update(self):\n    return 1"}])
    assert result["status"] == "error"
    assert "action 'replace_function'" in result["message"] and "Game.update (lines 14-15)" in result["message"]


def test_the_executor_does_not_ask_a_function_edit_for_chunks():
    from src.tools.code_editor import CodeEditorTool, function_edit_requested
    tool = CodeEditorTool()
    assert tool.params_not_required_for({"action": "replace_function", "filename": "a.py", "function": "f", "code": "x"}) == {"chunks"}
    assert tool.params_not_required_for({"filename": "a.py", "function": "f", "code": "x"}) == {"chunks", "action"}
    assert not function_edit_requested("replace_chunks", {"chunks": [{"search": "a", "replace": "b"}], "function": "f", "code": "x"})
    assert not function_edit_requested(None, {"name": "a.py", "code": "x"})  # 'name' alone is no function name
    from src.agents.tool_activity import is_file_change_call
    assert is_file_change_call("code_editor", {"action": "replace_function", "filename": "a.py"})


def test_the_chunks_schema_says_objects():
    from src.tools.code_editor import CodeEditorTool
    properties = CodeEditorTool().get_json_schema()["function"]["parameters"]["properties"]
    assert properties["chunks"]["items"]["type"] == "object"
    assert set(properties["chunks"]["items"]["properties"]) == {"start_line", "end_line", "search", "replace"}
    assert {"function", "code"} <= set(properties)


# ============================================================================================ 3. read one function
def _read(sandbox, **kwargs):
    from src.tools.file_system import FileSystemTool
    with quiet():
        return run(FileSystemTool().execute("W1", sandbox, scope="private", action="read", **kwargs))


def test_read_shows_only_the_named_function(sandbox):
    result = _read(sandbox, filename="game.py", function="Game.score")
    assert result["status"] == "success"
    content = result["content"]
    assert content.startswith("['Game.score' is lines 10-12 of 'game.py' (20 lines).")
    assert "action 'replace_function' with function='Game.score'" in content
    assert "10:     @property\n11:     def score(self):\n12:         return 1\n" in content
    assert "def update" not in content and "import os" not in content


def test_read_of_a_name_the_file_does_not_have_lists_its_definitions(sandbox):
    result = _read(sandbox, filename="game.py", function="render")
    assert result["status"] == "error"
    assert "has no function, class or method named 'render'" in result["message"]
    assert "Game.update (lines 14-15)" in result["message"] and "helper (lines 19-20)" in result["message"]
    whole = _read(sandbox, filename="notes.txt", function="x") if (sandbox / "notes.txt").write_text("a\n") else None
    assert whole["status"] == "success" and "1: a" in whole["content"]  # no code file: the whole file


def test_reads_of_two_functions_are_two_reads():
    from src.agents.context_compaction import _read_key
    one = _read_key("file_system", {"action": "read", "filename": "src/game.py", "function": "update"})
    two = _read_key("file_system", {"action": "read", "filename": "src/game.py", "function": "reset"})
    whole = _read_key("file_system", {"action": "read", "filename": "src/game.py"})
    assert one != two and one != whole and one[0] == whole[0] == "src/game.py"


# ============================================================================================ 4. the tool surface
def _agent(agent_type, state, role="Coder", task="Kick-off Task 2: Create src/game.js with the snake logic", **extra):
    return types.SimpleNamespace(agent_id="X1", agent_type=agent_type, state=state, role=role, persona=role,
                                 _config_system_prompt="", current_task_description=task, **extra)


def _offered(executor, agent):
    from src.agents.tool_guidance import offered_tool_names
    from src.agents.tool_surface import hidden_tools
    return offered_tool_names(agent.agent_type, agent.state, executor.tools,
                              skip=["resolve_concern"] + hidden_tools(agent, executor.tools.keys()))


def _size(executor, agent):
    from src.agents.tool_surface import schema_for
    return sum(len(json.dumps(schema_for(executor.tools[name], agent.agent_type, agent.state)))
               for name in _offered(executor, agent))


STATES = [("worker", "worker_work"), ("worker", "worker_test"), ("worker", "worker_decompose"), ("worker", "worker_report"),
          ("pm", "pm_manage"), ("pm", "pm_audit"), ("pm", "pm_standby"), ("admin", "conversation"), ("admin", "planning"),
          ("admin", "work")]


def test_a_view_only_leaves_out(executor):
    """Every parameter a view shows is a real parameter of the tool, with the tool's type; required ones are shown."""
    from src.agents.tool_surface import schema_for
    for name, tool in executor.tools.items():
        full = tool.get_json_schema()["function"]["parameters"]
        for agent_type, state in STATES:
            shown = schema_for(tool, agent_type, state)
            assert shown["function"]["name"] == name
            parameters = shown["function"]["parameters"]
            for param, entry in parameters["properties"].items():
                assert param in full["properties"], (name, agent_type, state, param)
                assert entry["type"] == full["properties"][param]["type"], (name, param)
                assert entry.get("description"), (name, param)
            assert set(parameters["required"]) <= set(parameters["properties"]), (name, agent_type, state)
            assert set(parameters["required"]) <= set(full["required"]), (name, agent_type, state)
            assert shown["function"]["description"]


def test_every_action_a_view_names_is_an_action_of_the_tool(executor):
    import re
    from src.agents import tool_surface as ts
    real = {"file_system": executor.tools["file_system"].get_json_schema()["function"]["parameters"]["properties"]["action"]["description"],
            "project_management": executor.tools["project_management"].description,
            "knowledge_base": executor.tools["knowledge_base"].description}
    for tool, view in (("file_system", ts.FILE_EDIT_VIEW), ("file_system", ts.FILE_READ_VIEW),
                       ("project_management", ts.TASKS_WORKER_VIEW), ("knowledge_base", ts.KNOWLEDGE_VIEW)):
        for action in re.findall(r"'(\w+)'", view.params["action"]):
            assert f"'{action}'" in real[tool], (tool, action)


def test_the_surface_of_a_call_is_about_half(executor):
    from src.config.settings import settings
    sizes = {}
    for agent_type, state in (("worker", "worker_work"), ("pm", "pm_manage"), ("worker", "worker_decompose")):
        agent = _agent(agent_type, state)
        settings.TOOL_SURFACE = "full"
        full = _size(executor, agent)
        settings.TOOL_SURFACE = "focused"
        sizes[(agent_type, state)] = (full, _size(executor, agent))
    for key, (full, focused) in sizes.items():
        assert focused < 0.6 * full, (key, full, focused)
    assert sizes[("worker", "worker_work")][1] < 13_500 and sizes[("pm", "pm_manage")][1] < 12_000


def test_full_surface_is_the_tools_as_they_are(executor, monkeypatch):
    from src.agents.tool_surface import hidden_tools, schema_for
    from src.config.settings import settings
    monkeypatch.setattr(settings, "TOOL_SURFACE", "full")
    for name, tool in executor.tools.items():
        assert schema_for(tool, "worker", "worker_work") == tool.get_json_schema()
    assert hidden_tools(_agent("worker", "worker_work"), executor.tools.keys()) == []


def test_what_a_worker_and_a_pm_are_shown(executor):
    from src.agents.tool_surface import schema_for
    files = schema_for(executor.tools["file_system"], "worker", "worker_work")["function"]
    assert {"action", "filename", "content", "function", "start_line", "end_line", "force_overwrite"} <= set(files["parameters"]["properties"])
    assert not {"scope", "commit_message", "branch", "regex_pattern", "search_block"} & set(files["parameters"]["properties"])
    assert "git_" not in files["parameters"]["properties"]["action"]["description"]
    pm_files = schema_for(executor.tools["file_system"], "pm", "pm_manage")["function"]
    assert set(pm_files["parameters"]["properties"]) == {"action", "filename", "path", "function", "start_line", "end_line"}
    assert "'write'" not in json.dumps(pm_files)  # a PM never changes project files
    decompose = schema_for(executor.tools["file_system"], "worker", "worker_decompose")["function"]
    assert "content" not in decompose["parameters"]["properties"]
    tasks = schema_for(executor.tools["project_management"], "worker", "worker_work")["function"]["parameters"]["properties"]
    assert set(tasks) == {"action", "task_id", "task_progress", "description", "depends"}
    pm_tasks = schema_for(executor.tools["project_management"], "pm", "pm_manage")["function"]["parameters"]["properties"]
    assert {"assignee_agent_id", "project_filter", "assignee_filter"} <= set(pm_tasks) and "dry_run" not in pm_tasks
    editor = schema_for(executor.tools["code_editor"], "worker", "worker_work")["function"]["parameters"]
    assert editor["required"] == ["action", "filename"] and "scope" not in editor["properties"]
    admin_files = schema_for(executor.tools["file_system"], "admin", "work")["function"]["parameters"]["properties"]
    assert "scope" in admin_files  # the Admin AI also works outside a project


def test_topic_tools_are_offered_when_the_work_names_their_subject(executor, monkeypatch):
    from src.agents.tool_surface import hidden_tools
    names = executor.tools.keys()
    coder = _agent("worker", "worker_work")
    assert set(hidden_tools(coder, names)) == {"asset_search", "github_tool", "zim_search"}
    assert "web_search" in _offered(executor, coder) and "code_editor" in _offered(executor, coder)
    artist = _agent("worker", "worker_work", role="Game Artist", task="Find sprite images and a sound for the snake")
    assert "asset_search" not in hidden_tools(artist, names)
    monkeypatch.setenv("ZIM_FILES_PATH", "/tmp/zim")
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "x")
    assert "zim_search" in hidden_tools(coder, names)  # configured, but a coder's task names no research
    researcher = _agent("worker", "worker_work", role="Researcher", task="Research the history of the game Snake")
    assert "zim_search" not in hidden_tools(researcher, names)
    cloner = _agent("worker", "worker_work", task="Clone the GitHub repository of the engine and read its README")
    assert "github_tool" not in hidden_tools(cloner, names)
    pm = _agent("pm", "pm_manage", role="Project Manager", task="Find images")
    assert set(hidden_tools(pm, names)) == {"asset_search", "github_tool", "zim_search", "web_search"}
    admin = _agent("admin", "conversation")
    assert hidden_tools(admin, names) == []  # everything is configured: the Admin AI keeps every tool
    monkeypatch.delenv("ZIM_FILES_PATH")
    assert hidden_tools(admin, names) == ["zim_search"]


def test_the_offered_tools_do_not_change_within_a_task(executor):
    agent = _agent("worker", "worker_work")
    first = _offered(executor, agent)
    agent.message_history = [{"role": "user", "content": "please find some images"}]
    assert _offered(executor, agent) == first  # only role and task decide, not the conversation


def test_asking_for_a_hidden_tool_offers_it(executor, monkeypatch):
    from src.agents.tool_surface import hidden_tools
    from src.tools.tool_information import ToolInformationTool
    agent = _agent("worker", "worker_work")
    state_manager = types.SimpleNamespace(get_agent_team=lambda _id: "team_P")
    manager = types.SimpleNamespace(agents={"X1": agent}, tool_executor=executor, current_project="P",
                                    state_manager=state_manager, cg_escalation=None)
    with quiet():
        listed = run(ToolInformationTool().execute("X1", Path("."), manager=manager, action="list_tools"))
    assert "Offered only when you need them: asset_search." in listed["message"]
    assert "github_tool" not in listed["message"] and "zim_search" not in listed["message"]  # not configured here
    assert "**asset_search**" not in listed["message"]
    with quiet():
        info = run(ToolInformationTool().execute("X1", Path("."), manager=manager, action="get_info", tool_name="asset_search"))
    assert info["status"] == "success" and "offered to you from your NEXT step on" in info["message"]
    assert "asset_search" not in hidden_tools(agent, executor.tools.keys()) and "asset_search" in _offered(executor, agent)
    with quiet():
        refused = run(ToolInformationTool().execute("X1", Path("."), manager=manager, action="get_info", tool_name="github_tool"))
    assert refused["status"] == "error" and "GITHUB_ACCESS_TOKEN" in refused["message"]


def test_the_prompt_names_the_tools_on_request():
    from src.agents.tool_guidance import FILE_CHANGE_RULE, tool_use_instructions
    text = tool_use_instructions("worker", "worker_work", ["file_system", "code_editor", "tool_information"],
                                 on_request=["asset_search"])
    assert "More tools only when you need them (asset_search)" in text
    assert "asset_search" not in text.split("Other tools are not available here")[0]
    assert "More tools" not in tool_use_instructions("worker", "worker_work", ["file_system", "tool_information"])
    assert "'replace_function'" in FILE_CHANGE_RULE and "no line numbers needed" in FILE_CHANGE_RULE
    assert FILE_CHANGE_RULE.index("'replace_function'") < FILE_CHANGE_RULE.index("'replace_chunks'")


def test_the_tool_surface_setting(monkeypatch):
    from src.config.settings import Settings
    with quiet():
        monkeypatch.setenv("TOOL_SURFACE", "FULL")
        assert Settings().TOOL_SURFACE == "full"
        monkeypatch.setenv("TOOL_SURFACE", "tiny")
        assert Settings().TOOL_SURFACE == "focused"
        monkeypatch.delenv("TOOL_SURFACE")
        assert Settings().TOOL_SURFACE == "focused"


# ============================================================================================ 5. collect.py v2.9
def _collect():
    spec = importlib.util.spec_from_file_location("collect_for_test_5a", Path(__file__).resolve().parents[2] / "collect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(calls):
    lines, second = [], 0
    for agent, state, history, prompt_tokens in calls:
        system = (f"[SYSTEM CONTEXT]\n- Your Agent ID: `{agent}`\n- Your Agent Type: `worker`\n"
                  f"- **Tools you can call in this state ('{state}'):** file_system." + "x" * 3000)
        payload = {"model": "q", "options": {"num_ctx": 8192},
                   "tools": [{"type": "function", "function": {"name": "file_system", "description": "d" * 1000}}],
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": "u" * history}]}
        second += 1
        lines.append(f"2026-11-10 10:00:{second:02d},000 - src.llm_providers.ollama_provider - DEBUG - OllamaProvider 'q': "
                     f"EXACT JSON payload being sent to http://x/api/chat (before request):\n{json.dumps(payload, indent=2)}")
        if prompt_tokens:
            second += 1
            lines.append(f"2026-11-10 10:00:{second:02d},000 - src.llm_providers.ollama_provider - INFO - Ollama finished: "
                         f"done_reason=stop, prompt_tokens={prompt_tokens}, output_tokens=100, content_chars=0, "
                         "thinking_chars=0, tool_calls=1, timing: load 0.1s, prompt 1,000 tok in 10.0s (100 tok/s), "
                         "output 100 tok in 20.0s (5.0 tok/s), total 30.0s")
    lines.append("2026-11-10 10:01:00,000 - src.agents.cycle_components.context_summarizer - INFO - Context summarization "
                 "triggered for agent W1. Context: 5000 tokens, Threshold: 4096")
    return "\n".join(lines) + "\n"


def test_collect_measures_the_context_budget():
    collect = _collect()
    entries = collect.parse_entries(_log([("W1", "worker_work", 1000, 1500), ("W1", "worker_work", 3000, 2100),
                                          ("W2", "worker_test", 500, None), ("W2", "worker_test", 500, 1300)]))
    budget = collect.collect_budget(entries)
    calls = budget["calls"]
    assert [c["agent"] for c in calls] == ["W1", "W1", "W2", "W2"]
    assert [c["prompt_tokens"] for c in calls] == [1500, 2100, None, 1300]  # the retried request has no reply
    assert calls[0]["history"] == 1000 and calls[0]["tool_count"] == 1 and calls[0]["num_ctx"] == 8192
    assert calls[0]["system"] > 3000 and 1000 < calls[0]["tools"] < 1200 and calls[0]["total_s"] == 30.0
    assert budget["summaries"] == {"W1": 1}
    report = collect.build_budget_report(budget, {"counts": {("code_editor", "replace_function"): [4, 1],
                                                             ("file_system", "write"): [3, 0],
                                                             ("file_system", "replace_lines"): [2, 2]}})
    assert "requests 4 (1 without a reply line" in report
    assert "worker   worker_work" in report and "worker   worker_test" in report
    assert "context windows sent (num_ctx): 8,192" in report
    assert "W1: 1" in report and "replace_function         4 calls      1 failed   25% failed" in report
    assert "line / text edits        2 calls      2 failed   100% failed" in report
    assert "characters per prompt token (measured)" in report and "of the 8,192-token window" in report
    empty = collect.build_budget_report({"calls": [], "summaries": {}})
    assert "No request payloads" in empty

# END OF FILE tests/unit/test_round5a.py
