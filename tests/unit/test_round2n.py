# START OF FILE tests/unit/test_round2n.py
"""
Round 2n: fixtures from the 2026-10-20 23:11 - 2026-10-21 00:09 run with qwen3.5:9b on one remote Ollama server
(project WiFi_Snake_Game, session startup_1792509061; num_ctx capped at 32768).

* 23:21-23:24 - W1's complete src/app.py was cut off at 4096 output tokens three times (about 85 s each); after every
  cut-off a small call (list a directory, pip install) reset the budget to 4096 and the next complete file was cut off again.
* 23:25-23:55 - W1's corrected versions of its OWN src/app.py and src/game_logic.py were refused "already exists" 20+
  times: they renamed a class (GameStateManager -> GameState) or dropped imports (Session, sessionmaker). W1 then sent the
  action 'force_overwrite_file_write' 5 times ("Invalid action") and wrote the same broken text 4 times ("success").
* 23:26:25 - PM1 marked Kick-off Task 1 ("Setup Flask backend, SQLite schema ... in data/scores.db") finished although
  src/app.py, written by W1 for that task, used the undefined name 'current_direction'.
* 23:16:39 - PM1 called the tool 'manage_tool'; the call was dropped and PM1 was told its reply was EMPTY.
* 23:32-23:37 - W1 marked the already-read message 'msg_1792509985381_9d8ee4' read 6 times while 2 others stayed unread.
* 23:50-23:55 - W1 sent file_system 'read' with start_line/end_line and no file name 9 times.
* 23:59:03 - W1 (147 messages) filled the context window: 32,675 prompt + 93 output tokens = num_ctx 32,768. It was
  taken for an output cut-off (budget doubled to 8192), and the Guardian blocked W1 for two "empty" replies. The
  summarizer never ran: it used the model's native num_ctx 262,144 (threshold 131,072 tokens).
* The ChatML template that replaces qwen3.5's RAW '{{ .Prompt }}' template renders only message content, so the model
  never saw which tool calls it had made.
* 00:02:41 - failover gave W1 codellama:34b (no tool support) and skipped gemma4:12b (tools) for its RAW template.
* collect.py exported the Temp_Converter_CLI session instead of the WiFi_Snake_Game run of the log.
"""
import asyncio
import importlib.util
import json
import logging
import os
import time
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.utils import file_authorship
from src.utils.file_authorship import (
    files_changed_for_task, last_change, last_writer, record_file_change, record_tool_call_changes,
)

GAME_LOGIC_23_42 = (
    '"""Game logic module for WiFi Snake Game."""\n'
    '\n'
    '\n'
    'class GameStateManager:\n'
    '    """Manages game state including snake position, direction, and collisions."""\n'
    '\n'
    '    def __init__(self):\n'
    '        self.snake = []  # List of (x, y) tuples representing body segments\n'
    "        self.direction = 'right'  # Current movement direction  \n"
    '        self.grid_size = 20  # Default grid size\n'
    '        self.food_position = None\n'
    '        self.score = 0\n'
    '\n'
    '    def initialize_game(self):\n'
    '        """Initialize the game state with a fresh snake and spawn food."""\n'
    '\n'
    '        # Initialize snake body starting from center-right area going left\n'
    '        start_y = self.grid_size // 2 - (self.grid_size % 4) // 2 + ((i if i else 0))\n'
    '        \n'
    '        for segment_idx in range(min(5, max(self.grid_size - 6, 1))):\n'
    '            x_pos = half_grid\n'
    '            y_offset = start_y + (segment_idx * self.grid_size % 4)\n'
    '            snake_segment = (x_pos, y_offset)\n'
    '            \n'
    '\n'
    '\n'
    '    def move_snake(self):\n'
    '        """Move the snake one step and handle growth on food collision."""\n'
    '        \n'
    '        # Calculate new head position based on current direction  \n'
    "        if self.direction == 'right':\n"
    '            new_head = (self.snake[0][0] + 1, self.snake[0][1])\n'
    "        elif self.direction == 'left':\n"
    '            new_head = (self.snake[0][0] - 1, self.snake[0][1])\n'
    "        elif self.direction == 'up':\n"
    '            new_head = (self.snake[0][0], self.snake[0][1] + 1)\n'
    '        else: # down or default to right\n'
    '            new_head = (self.snake[0][0], self.snake[0][1] - 1)\n'
    '\n'
    '    def check_wall_collision(self, head_x, head_y):\n'
    '        """Check if snake hits the wall boundaries."""\n'
    '        \n'
    '        return not (0 <= head_x < self.grid_size and \n'
    '                    0 <= head_y < self.grid_size)\n'
    '\n'
    '    def _get_head_position(self):\n'
    '        """Get current snake head position."""\n'
    '        return self.snake[0] if len(self.snake) > 0 else None\n'
)   # src/game_logic.py as W1 left it (undefined 'i' and 'half_grid')
APP_23_24 = (
    'from flask import Flask, request, jsonify\n'
    'from sqlalchemy.ext.ormapi import declarative_base, Session, sessionmaker\n'
    'from sqlalchemy import Column, Integer, String, create_engine\n'
    '\n'
    '\n'
    'app = Flask(__name__)\n'
    '\n'
    '# Database configuration\n'
    'DATABASE_URL = "sqlite:///data/scores.db"\n'
    'engine = create_engine(DATABASE_URL)\n'
    'Base = declarative_base()\n'
    '\n'
    '\n'
    'class Player(Base):\n'
    "    __tablename__ = 'players'\n"
    '    \n'
    '    id = Column(Integer, primary_key=True)\n'
    '    name = Column(String(50), unique=True, nullable=False)\n'
    '\n'
    '\n'
    'class ScoreRecord(Base):\n'
    "    __tablename__ = 'score_records'\n"
    '    \n'
    '    id = Column(Integer, primary_key=True)\n'
    '    game_id = Column(Integer, nullable=False)\n'
    '    player_name = Column(String(50))  # Foreign key to players table (simplified for now)\n'
    '    score = Column(Integer, nullable=False)\n'
    '\n'
    '\n'
    "# Create tables if they don't exist\n"
    'Base.metadata.create_all(engine)\n'
    '\n'
    'SessionLocal = sessionmaker(bind=engine)\n'
    '\n'
    '\n'
    "@app.route('/init_game', methods=['GET'])\n"
    'def init_game():\n'
    '    """Initialize a new game and return the initial state."""\n'
    '    \n'
    '    # Get grid size from query parameter (default 20x20)\n'
    '    try:\n'
    "        grid_size = int(request.args.get('size', '20'))\n"
    '    except ValueError:\n'
    '        grid_size = 20\n'
    '    \n'
    '    if not (1 <= grid_size <= 50):\n'
    "        return jsonify({'error': 'Grid size must be between 1 and 50'}), 400\n"
    '    \n'
    '    # Generate initial snake position (center of the board)\n'
    '    half_grid = grid_size // 2\n'
    '    snake_head_x = half_grid - 3\n'
    '    snake_head_y = half_grid + 1\n'
    '    \n'
    '    # Initial direction: moving right\n'
    "    initial_direction = 'right'\n"
    '    \n'
    '    return jsonify({\n'
    "        'grid': {\n"
    "            'size': grid_size,\n"
    "            'snake': [\n"
    "                {'x': snake_head_x, 'y': snake_head_y},\n"
    "                {'x': snake_head_x - 1, 'y': snake_head_y}\n"
    '            ],\n'
    "            'food': None  # Will be set when first move is made or after spawning logic runs\n"
    '        },\n'
    "        'direction': initial_direction,\n"
    "        'game_over': False,\n"
    "        'score': 0,\n"
    "        'players_registered': []\n"
    '    })\n'
    '\n'
    '\n'
    "@app.route('/register_player', methods=['POST'])\n"
    'def register_player():\n'
    '    """Register a player\'s first move and name."""\n'
    '    \n'
    '    data = request.get_json() or {}\n'
    "    direction = data.get('direction')\n"
    "    username = data.get('username')\n"
    '    \n'
    '    if not direction:\n'
    "        return jsonify({'error': 'Direction is required'}), 400\n"
    '    \n'
    "    valid_directions = ['up', 'down', 'left', 'right']\n"
    '    current_head_x, current_head_y = None, None\n'
    '    \n'
    '    # Get snake from session or request context (for now using a simple approach)\n'
    '    try:\n'
    "        game_state_data = data.get('game')  # This would come from the previous /init_game response stored in client memory\n"
    '        \n'
    '        if not isinstance(game_state_data, dict):\n'
    "            return jsonify({'error': 'Invalid game state'}), 400\n"
    '            \n'
    "        current_head_x = game_state_data['grid']['snake'][0]['x']\n"
    "        current_head_y = game_state_data['grid']['snake'][0]['y']\n"
    '        \n'
    '    except (KeyError, TypeError) as e:\n'
    "        return jsonify({'error': 'Missing required fields'}), 422\n"
    '    \n'
    '    # Calculate new head position based on direction and previous move\n'
    "    if direction == 'up' or current_head_y is None:\n"
    "        delta_x = -1 * int(current_direction[0] in ['left', 'right']) + \\\n"
    '                   (current_head_x % grid_size)  # Simplified logic for demo\n'
    '    \n'
    '    new_snake_position = {\n'
    "        'x': snake_head_x,\n"
    "        'y': snake_head_y\n"
    '    }\n'
)                 # src/app.py as W1 wrote it at 23:24:34 for Kick-off Task 1
GAME_STATE_REWRITE = (               # W1's complete new version: renames the class and drops methods
    '"""Game logic module for WiFi Snake Game."""\n\n\nclass GameState:\n    """Manages game state."""\n\n'
    "    def __init__(self, grid_size=20):\n        self.grid_size = grid_size\n        self.snake = [(10, 10)]\n"
    "        self.direction = 'right'\n\n    def move(self):\n        x, y = self.snake[0]\n"
    "        self.snake.insert(0, (x + 1, y))\n        self.snake.pop()\n")
TASK_1 = "90109794-fdce-4025-832e-78f865f8b41c"
TASK_2 = "c359c2a5-76ab-44ae-a5ea-57ca48897d87"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def quiet_and_clean():
    logging.disable(logging.CRITICAL)
    file_authorship.reset()
    yield
    file_authorship.reset()
    logging.disable(logging.NOTSET)


def _agent(agent_id="W1", agent_type="worker", state="worker_work", task=None, history=None):
    return types.SimpleNamespace(agent_id=agent_id, agent_type=agent_type, state=state, active_task_id=task,
                                 current_task_id=None, message_history=list(history or []), read_message_ids={})


def _manager(*agents):
    return types.SimpleNamespace(agents={a.agent_id: a for a in agents})


def _execute(tmp_path, agent, tool, args, manager=None):
    from src.tools.executor import ToolExecutor
    return run(ToolExecutor().execute_tool(agent.agent_id, tmp_path, tool, dict(args, scope="private"),
                                           manager=manager or _manager(agent)))


def _fs(tmp_path, agent_id="W1", **args):
    from src.tools.file_system import FileSystemTool
    return run(FileSystemTool().execute(agent_id, tmp_path, scope="private", **args))


def _write_game_logic(tmp_path, text=GAME_LOGIC_23_42):
    (tmp_path / "src").mkdir(exist_ok=True)
    path = tmp_path / "src" / "game_logic.py"
    path.write_text(text)
    return path


# ======================================================================================== 1. who changed which file
def test_the_registry_remembers_the_last_writer_and_task(tmp_path):
    path = _write_game_logic(tmp_path)
    assert last_writer(path) is None
    record_file_change(path, "W1", TASK_2, "write")
    record_file_change(tmp_path / "src" / ".." / "src" / "game_logic.py", "W1", TASK_2, "replace_chunks")
    assert last_writer(path) == "W1" and last_change(path)["task_id"] == TASK_2
    assert files_changed_for_task(TASK_2) == [path.resolve()]
    assert files_changed_for_task(TASK_2[:8]) == [path.resolve()]          # a copied short id
    assert files_changed_for_task(TASK_1) == [] and files_changed_for_task(None) == []
    assert files_changed_for_task(TASK_2, workspace=tmp_path / "other") == []
    record_file_change(path, "W2", None)
    assert last_writer(path) == "W2" and files_changed_for_task(TASK_2) == []


def test_tool_calls_record_moves_deletes_and_copies(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert record_tool_call_changes("file_system", {"action": "write", "filename": "a.py", "scope": "private"},
                                    "W1", TASK_1, tmp_path, None, None) == [tmp_path / "a.py"]
    assert last_writer(tmp_path / "a.py") == "W1"
    (tmp_path / "a.py").rename(tmp_path / "b.py")
    record_tool_call_changes("file_system", {"action": "move", "path": "a.py", "destination_path": "b.py"},
                             "W1", TASK_1, tmp_path, None, None)
    assert last_writer(tmp_path / "a.py") is None and last_writer(tmp_path / "b.py") == "W1"
    record_tool_call_changes("file_system", {"action": "copy", "path": "b.py", "destination_path": "c.py"},
                             "W2", None, tmp_path, None, None)
    assert last_writer(tmp_path / "c.py") == "W2" and last_writer(tmp_path / "b.py") == "W1"
    record_tool_call_changes("file_system", {"action": "delete", "path": "b.py"}, "W1", None, tmp_path, None, None)
    assert last_writer(tmp_path / "b.py") is None
    # no workspace for a shared call without project: nothing recorded, nothing raised
    assert record_tool_call_changes("code_editor", {"action": "replace_chunks", "filename": "x.py", "scope": "shared"},
                                    "W1", None, tmp_path, None, None) == []


def test_shared_scope_uses_the_session_workspace(tmp_path, monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / "WiFi_Snake_Game" / "startup_1792509061" / "shared_workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text(APP_23_24)
    recorded = record_tool_call_changes("code_editor", {"action": "replace_chunks", "filename": "src/app.py"}, "W1",
                                        TASK_1, tmp_path / "sandbox", "WiFi_Snake_Game", "startup_1792509061")
    assert recorded == [workspace / "src" / "app.py"]
    assert files_changed_for_task(TASK_1, workspace) == [(workspace / "src" / "app.py").resolve()]


def test_the_executor_records_successful_changes_with_the_active_task(tmp_path):
    agent = _agent(task=TASK_2)
    result = _execute(tmp_path, agent, "file_system", {"action": "write", "filename": "src/game_logic.py",
                                                       "content": GAME_LOGIC_23_42})
    assert "Successfully wrote" in str(result)
    assert last_change(tmp_path / "src" / "game_logic.py")["task_id"] == TASK_2
    # a refused or failed call records nothing
    other = _agent("W2", task=TASK_1)
    _execute(tmp_path, other, "file_system", {"action": "write", "filename": "src/game_logic.py", "content": "x = 2\n"},
             manager=_manager(agent, other))
    assert last_writer(tmp_path / "src" / "game_logic.py") == "W1"
    # reads are not changes
    _execute(tmp_path, other, "file_system", {"action": "read", "filename": "src/game_logic.py"}, manager=_manager(agent, other))
    assert last_writer(tmp_path / "src" / "game_logic.py") == "W1"


# ======================================================================================== 2. rewriting one's own file
def test_an_agent_replaces_its_own_file_and_hears_what_is_gone(tmp_path):
    path = _write_game_logic(tmp_path)
    record_file_change(path, "W1", TASK_2, "write")
    result = _fs(tmp_path, action="write", filename="src/game_logic.py", content=GAME_STATE_REWRITE)
    assert result["status"] == "success", result
    assert path.read_text() == GAME_STATE_REWRITE
    assert "you wrote this file yourself earlier" in result["message"]
    assert "class 'GameStateManager' (line 4) would be removed" in result["message"]
    assert "If that was not intended, add them back" in result["message"]


def test_a_file_another_agent_wrote_stays_protected(tmp_path):
    path = _write_game_logic(tmp_path)
    record_file_change(path, "W1", TASK_2, "write")
    result = _fs(tmp_path, agent_id="W2", action="write", filename="src/game_logic.py", content=GAME_STATE_REWRITE)
    assert result["status"] == "error" and "already exists" in result["message"]
    assert "last changed by agent 'W1', not by you" in result["message"]
    assert path.read_text() == GAME_LOGIC_23_42


def test_a_file_nobody_wrote_in_this_run_stays_protected(tmp_path):
    path = _write_game_logic(tmp_path)
    result = _fs(tmp_path, action="write", filename="src/game_logic.py", content=GAME_STATE_REWRITE)
    assert result["status"] == "error" and "class 'GameStateManager' (line 4) would be removed" in result["message"]
    assert "last changed by agent" not in result["message"]
    assert path.read_text() == GAME_LOGIC_23_42


def test_an_own_rewrite_with_a_syntax_error_is_still_rejected(tmp_path):
    path = _write_game_logic(tmp_path)
    record_file_change(path, "W1", TASK_2, "write")
    broken = GAME_STATE_REWRITE + "    def bad(self:\n        pass\n"
    result = _fs(tmp_path, action="write", filename="src/game_logic.py", content=broken)
    assert result["status"] == "error" and "SyntaxError" in result["message"]
    assert path.read_text() == GAME_LOGIC_23_42


def test_own_non_python_files_are_replaced_too(tmp_path):
    (tmp_path / "templates").mkdir()
    page = tmp_path / "templates" / "index.html"
    page.write_text("<html><body>old</body></html>\n")
    record_file_change(page, "W1", None, "write")
    result = _fs(tmp_path, action="write", filename="templates/index.html", content="<html><body>new</body></html>\n")
    assert result["status"] == "success" and page.read_text().endswith("new</body></html>\n")
    result = _fs(tmp_path, agent_id="W2", action="write", filename="templates/index.html", content="<p>x</p>\n")
    assert result["status"] == "error"


def test_the_whole_story_through_the_executor(tmp_path):
    """W1 writes its file, then sends a complete corrected version: it is saved (23:28:23 was refused)."""
    agent = _agent(task=TASK_2)
    _execute(tmp_path, agent, "file_system", {"action": "write", "filename": "src/game_logic.py", "content": GAME_LOGIC_23_42})
    result = _execute(tmp_path, agent, "file_system", {"action": "write", "filename": "src/game_logic.py",
                                                       "content": GAME_STATE_REWRITE})
    assert "Successfully wrote" in str(result) and (tmp_path / "src" / "game_logic.py").read_text() == GAME_STATE_REWRITE


# ======================================================================================== 3. force_overwrite as an action
@pytest.mark.parametrize("action", ["force_overwrite_file_write", "overwrite", "rewrite_file", "force_write>"])
def test_force_write_actions_are_writes_with_force_overwrite(tmp_path, action):
    path = _write_game_logic(tmp_path)
    result = _fs(tmp_path, agent_id="W2", action=action, filename="src/game_logic.py", content=GAME_STATE_REWRITE)
    assert result["status"] == "success", result
    assert path.read_text() == GAME_STATE_REWRITE
    assert "was run as 'write' with force_overwrite=true" in result["message"]


def test_force_overwrite_given_as_text(tmp_path):
    path = _write_game_logic(tmp_path)
    result = _fs(tmp_path, action="write", filename="src/game_logic.py", content=GAME_STATE_REWRITE, force_overwrite="false")
    assert result["status"] == "error" and path.read_text() == GAME_LOGIC_23_42
    result = _fs(tmp_path, action="write", filename="src/game_logic.py", content=GAME_STATE_REWRITE, force_overwrite="true")
    assert result["status"] == "success" and path.read_text() == GAME_STATE_REWRITE


def test_force_write_actions_count_as_file_changes():
    from src.agents.tool_activity import duplicate_file_change_verdict, is_file_change_call, state_tool_block_message
    args = {"action": "force_overwrite_file_write", "filename": "src/game_logic.py", "content": "x"}
    assert is_file_change_call("file_system", args)
    assert duplicate_file_change_verdict("file_system", args) == ("ALLOW", "")
    from src.agents.tool_activity import written_code_files
    history = [{"role": "assistant", "content": "", "tool_calls": [{"id": "f1", "name": "file_system", "arguments": args}]},
               {"role": "tool", "tool_call_id": "f1", "name": "file_system",
                "content": "Successfully wrote 1 characters to 'src/game_logic.py'"}]
    assert written_code_files(history) == ["src/game_logic.py"]
    assert state_tool_block_message("pm", "pm_report_check", "file_system", args).startswith("A Project Manager does not")


def test_rewriting_the_same_broken_text_names_its_defects(tmp_path):
    _write_game_logic(tmp_path)
    result = _fs(tmp_path, agent_id="W2", action="write", filename="src/game_logic.py", content=GAME_LOGIC_23_42)
    assert result["status"] == "success" and "exact same content" in result["message"]
    assert "STILL has these defects" in result["message"]
    assert "'i' is used on line 18" in result["message"] and "'half_grid' is used on line 21" in result["message"]


def test_dropped_imports_are_not_a_loss():
    from src.tools.python_static_check import python_rewrite_problems
    fixed = APP_23_24.replace("from sqlalchemy.ext.ormapi import declarative_base, Session, sessionmaker\n",
                              "from sqlalchemy.orm import declarative_base\n")
    fixed = fixed.replace("SessionLocal = sessionmaker(bind=engine)\n", "")
    # 23:25:24 said Session, sessionmaker AND SessionLocal would be removed; only the assignment is content
    assert python_rewrite_problems(APP_23_24, fixed) == ["top-level name 'SessionLocal' (line 33) would be removed"]


# ======================================================================================== 4. the completion check
def test_a_task_cannot_finish_with_broken_files_its_worker_wrote(tmp_path):
    from src.utils.task_files import task_completion_problems
    (tmp_path / "src").mkdir()
    app = tmp_path / "src" / "app.py"
    app.write_text(APP_23_24)
    description = "Kick-off Task 1: Setup Flask backend, SQLite schema for players and scores in data/scores.db"
    assert task_completion_problems(description, tmp_path) == []     # what happened at 23:26:25
    problems = task_completion_problems(description, tmp_path, [app])
    assert problems == ["src/app.py: 'current_direction' is used on line 102 but is never imported or defined in this file, "
                        "so it raises NameError (missing import, or a typo in the name?)."]
    # files outside the workspace, non-Python files and the framework conftest.py are not checked
    (tmp_path / "conftest.py").write_text("import nothing_here_x\nx = undefined_name\n")
    (tmp_path / "notes.md").write_text("x")
    assert task_completion_problems(description, tmp_path, [tmp_path / "conftest.py", tmp_path / "notes.md",
                                                            tmp_path.parent / "elsewhere.py"]) == []


def test_project_management_refuses_finished_for_the_workers_file(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.project_management import ProjectManagementTool
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / "WiFi_Snake_Game" / "startup_1792509061" / "shared_workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text(APP_23_24)
    record_file_change(workspace / "src" / "app.py", "W1", TASK_1, "write")
    task = {"uuid": TASK_1, "description": "Kick-off Task 1: Setup Flask backend, SQLite schema for players and scores in data/scores.db"}
    refusal = ProjectManagementTool()._completion_refusal(task, "WiFi_Snake_Game", "startup_1792509061", TASK_1)
    assert refusal and refusal["status"] == "error"
    assert "was NOT marked finished" in refusal["message"] and "'current_direction'" in refusal["message"]
    # a task without a uuid (older callers) is checked as before
    assert ProjectManagementTool()._completion_refusal({"description": task["description"]}, "WiFi_Snake_Game",
                                                       "startup_1792509061", TASK_1) is None


# ======================================================================================== 5. tool names and missing file names
def test_resolve_tool_name():
    from src.tools.executor import resolve_tool_name
    tools = ["manage_team", "code_editor", "tool_information", "command_executor", "file_system", "send_message",
             "asset_search", "project_management", "test_runner", "github_tool", "codebase_search", "mark_message_read",
             "zim_search", "web_search", "knowledge_base", "system_help"]
    assert resolve_tool_name("manage_tool", tools) == "manage_team"          # 23:16:39
    assert resolve_tool_name("file_sytem", tools) == "file_system"
    assert resolve_tool_name("File-System", tools) == "file_system"
    assert resolve_tool_name("code_search", tools) == "codebase_search"
    assert resolve_tool_name("read_file", tools) == "file_system"
    assert resolve_tool_name("tool_info", tools) == "tool_information"
    assert resolve_tool_name("project_management", tools) == "project_management"
    assert resolve_tool_name("no_such_tool", tools) is None
    assert resolve_tool_name("", tools) is None and resolve_tool_name(None, tools) is None


def test_the_executor_runs_a_misnamed_tool_as_the_tool_it_means(tmp_path):
    agent = _agent()
    result = _execute(tmp_path, agent, "file_sytem", {"action": "write", "filename": "a.txt", "content": "x"})
    assert "there is no tool named 'file_sytem'" in str(result) and "Successfully wrote" in str(result)
    assert (tmp_path / "a.txt").read_text() == "x"


def test_last_file_target():
    from src.agents.tool_activity import last_file_target
    history = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "name": "file_system", "arguments": {"action": "read", "filename": "src/game_logic.py"}}]},
        {"role": "tool", "tool_call_id": "1", "name": "file_system", "content": "1: ..."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "2", "name": "file_system", "arguments": {"action": "list", "path": "src/"}}]},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "3", "name": "send_message", "arguments": {}}]},
    ]
    assert last_file_target(history) == "src/game_logic.py"
    assert last_file_target([]) is None
    assert last_file_target(history[2:]) is None


def test_a_read_without_a_file_name_reads_the_last_file(tmp_path):
    """23:50:43-23:55:43: {'action': 'read', 'end_line': 47, 'start_line': 15} nine times."""
    _write_game_logic(tmp_path)
    history = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "name": "code_editor", "arguments": {"action": "replace_chunks", "filename": "src/game_logic.py",
                                                          "chunks": "[]"}}]}]
    agent = _agent(history=history)
    result = _execute(tmp_path, agent, "file_system", {"action": "read", "start_line": 15, "end_line": 23})
    assert "had no 'filename', so the file of your last file call, 'src/game_logic.py', was read" in str(result)
    assert "18:         start_y" in str(result)
    # no earlier file call: the old error
    result = _execute(tmp_path, _agent(), "file_system", {"action": "read", "start_line": 15, "end_line": 23})
    assert "'filename' parameter is required for 'read'" in str(result)


# ======================================================================================== 6. already-read messages
def test_marking_an_already_read_message_again():
    from src.agents.tool_activity import mark_read_repeat_notice, unread_message_ids
    agent = _agent(state="worker_report", history=[
        {"role": "user", "content": "[From @PM1]: fix it", "message_id": "msg_1792510803377_PM1"},
        {"role": "user", "content": "[From @PM1]: and test", "message_id": "msg_1792511322799_PM1"},
    ])
    agent.read_message_ids = {"msg_1792509985381_9d8ee4": time.time()}
    assert unread_message_ids(agent) == ["msg_1792510803377_PM1", "msg_1792511322799_PM1"]
    text = mark_read_repeat_notice(agent, {"message_id": "msg_1792509985381_9d8ee4"})
    assert text.startswith("Message 'msg_1792509985381_9d8ee4' was ALREADY marked as read earlier")
    assert "Messages still unread: msg_1792510803377_PM1, msg_1792511322799_PM1" in text
    assert mark_read_repeat_notice(agent, {"message_id": "msg_1792510803377_PM1"}) is None   # a new id: normal path
    agent.message_history = []
    assert "no unread messages" in mark_read_repeat_notice(agent, '{"message_id": "msg_1792509985381_9d8ee4"}')
    assert mark_read_repeat_notice(agent, {}) is None


# ======================================================================================== 7. the context window
def test_context_window_full_versus_output_cut_off():
    from src.llm_providers.ollama_provider import is_context_window_full
    assert is_context_window_full("length", 32675, 93, 32768, 4096)       # 23:59:03
    assert is_context_window_full("length", 32742, 26, 32768, 8192)       # 23:59:30
    assert not is_context_window_full("length", 11095, 4096, 32768, 4096)  # 23:21:12: an ordinary cut-off
    assert not is_context_window_full("stop", 32675, 93, 32768, 4096)
    assert not is_context_window_full("length", 12000, 93, 32768, 4096)
    assert not is_context_window_full("length", None, 93, 32768, 4096)
    assert not is_context_window_full("length", 32675, 93, None, 4096)


def test_effective_ollama_num_ctx(monkeypatch):
    from src.config.settings import settings
    from src.llm_providers.ollama_provider import effective_ollama_num_ctx
    monkeypatch.setattr(settings, "OLLAMA_MAX_CTX_CAP", 32768)
    assert effective_ollama_num_ctx(262144) == 32768
    assert effective_ollama_num_ctx(8192) == 8192
    assert effective_ollama_num_ctx(None) == 32768


def test_the_summarizer_uses_the_window_the_provider_sends():
    from src.agents.cycle_handler import effective_context_window
    ollama_agent = types.SimpleNamespace(provider_name="ollama-local-oli-gaborkukucska-com", _last_num_ctx=None)
    assert effective_context_window(ollama_agent, 262144) == 32768
    ollama_agent._last_num_ctx = 16384
    assert effective_context_window(ollama_agent, 262144) == 16384
    other = types.SimpleNamespace(provider_name="openrouter", _last_num_ctx=None)
    assert effective_context_window(other, 128000) == 128000


def test_should_summarize_with_real_prompt_tokens_and_force():
    from src.agents.cycle_components.context_summarizer import ContextSummarizer
    summarizer = ContextSummarizer(types.SimpleNamespace(agents={}))
    check = summarizer.should_summarize_context
    # the estimate (chars / 4, no tool schemas) is below 50% but the real prompt is above 75% of the window
    assert run(check("W1", 12000, 4096, [], model_num_ctx=32768, real_prompt_tokens=26000))
    assert not run(check("W1", 12000, 4096, [], model_num_ctx=32768, real_prompt_tokens=20000))
    assert run(check("W1", 100, 4096, [], model_num_ctx=32768, force=True))
    # two summaries already: only near the hard limit (90%)
    summaries = [{"role": "system", "content": "[CONTEXT SUMMARY 1/2 - x]"}, {"role": "system", "content": "[CONTEXT SUMMARY 2/2 - x]"}]
    assert not run(check("W1", 12000, 4096, summaries, model_num_ctx=32768, real_prompt_tokens=26000))
    assert run(check("W1", 12000, 4096, summaries, model_num_ctx=32768, real_prompt_tokens=30000))


class _FakeProvider:
    def __init__(self, events):
        self.events, self.max_tokens_seen = events, []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.max_tokens_seen.append(max_tokens)
        for event in self.events:
            yield event


def _core_worker(provider, monkeypatch, state="worker_work", tools=None):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", tools is not None)
    monkeypatch.setattr(core_module.settings, "WORKER_WORK_STATE_MAX_TOKENS", 4096)

    async def no_workflow(*_args, **_kwargs):
        return None
    tool_objects = {name: types.SimpleNamespace(auth_level="worker", get_json_schema=lambda n=name: {"name": n})
                    for name in (tools or [])}
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools=tool_objects),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s, is_valid_state=lambda t, s: True,
                                               _valid_states={}),
        cycle_handler=None)
    agent = Agent({"agent_id": "W1", "config": {"agent_type": "worker", "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = state
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return run(go())


def test_a_full_context_window_is_not_an_output_cut_off(monkeypatch):
    provider = _FakeProvider([{"type": "finish_info", "done_reason": "length", "output_tokens": 93,
                               "thinking_chars": 193, "thinking_tail": "I'll use code_editor", "prompt_tokens": 32675,
                               "num_ctx": 32768, "num_predict": 4096}])
    agent = _core_worker(provider, monkeypatch)
    _consume(agent)
    assert agent._context_window_full is True and agent._last_reply_cut_off is False
    assert agent._consecutive_cut_off_replies == 0 and agent._last_prompt_tokens == 32675 and agent._last_num_ctx == 32768
    _consume(agent)
    assert provider.max_tokens_seen == [4096, 4096]       # 23:59:30 asked for 8192


def test_a_cut_off_keeps_the_larger_budget_in_that_state(monkeypatch):
    """23:21-23:24: cut off at 4096 - small call - cut off at 4096 again."""
    cut = [{"type": "finish_info", "done_reason": "length", "output_tokens": 4096, "thinking_chars": 293,
            "prompt_tokens": 11095, "num_ctx": 32768, "num_predict": 4096}]
    small = [{"type": "response_chunk", "content": "listing data/"}, {"type": "finish_info", "done_reason": "stop",
                                                                       "output_tokens": 37}]
    provider = _FakeProvider(cut)
    agent = _core_worker(provider, monkeypatch)
    _consume(agent)
    provider.events = small
    _consume(agent)
    _consume(agent)
    assert provider.max_tokens_seen == [4096, 8192, 8192]
    from src.agents.cycle_handler import cut_off_reply_notice
    agent._consecutive_cut_off_replies = 1
    notice = cut_off_reply_notice(agent)
    assert "write its first part with file_system 'write', then add the rest with 'append'" in notice


def test_an_unknown_native_tool_name_reaches_the_executor(monkeypatch):
    from src.agents.core import Agent  # noqa: F401  (import check)
    tools = ["manage_team", "file_system", "project_management"]
    call = {"id": "call_6jjzyc7a", "function": {"name": "manage_tool", "arguments": {
        "action": "create_team", "target_agent_id": "N/A", "team_id": "team_WiFi_Snake_Game"}}}
    agent = _core_worker(_FakeProvider([{"type": "native_tool_calls", "tool_calls": [call]}]), monkeypatch, tools=tools)
    events = _consume(agent)
    requests = [e for e in events if e.get("type") == "tool_requests"]
    assert requests and requests[0]["calls"][0]["name"] == "manage_tool"   # the executor maps it to manage_team
    # a name nobody can resolve still reaches the executor when it is the only call (it answers "not found")
    call["function"]["name"] = "xyz_unknown"
    agent = _core_worker(_FakeProvider([{"type": "native_tool_calls", "tool_calls": [call]}]), monkeypatch, tools=tools)
    requests = [e for e in _consume(agent) if e.get("type") == "tool_requests"]
    assert requests and requests[0]["calls"][0]["name"] == "xyz_unknown"


def test_the_context_full_notice():
    from src.agents.cycle_handler import context_full_notice
    agent = types.SimpleNamespace(_last_num_ctx=32768, _last_prompt_tokens=32675)
    text = context_full_notice(agent)
    assert "context window was FULL (32675 of 32768 tokens)" in text and "NOTHING was executed or saved" in text


# ======================================================================================== 8. what the model sees of its calls
# Round 2o: the "[Action Taken: called ...]" text of Round 2n was removed - Ollama's /api/chat ignores a 'template' field and
# its built-in qwen3.5 renderer already shows earlier calls as <tool_call> blocks; the model copied the text instead of calling.


def _serve_ollama_chat(payloads, reply):
    from aiohttp import web

    async def chat(request):
        payloads.append(await request.json())
        return web.json_response(reply)

    async def start():
        app = web.Application()
        app.router.add_post("/api/chat", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    return start


def test_earlier_tool_calls_are_sent_as_tool_calls_and_finish_info_has_the_window(monkeypatch):
    from src.config.settings import settings
    from src.llm_providers.ollama_provider import OllamaProvider
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    monkeypatch.setattr(settings, "OLLAMA_MAX_CTX_CAP", 32768)
    payloads = []
    reply = {"model": "qwen3.5:9b-q4_K_M", "done": True, "done_reason": "length", "prompt_eval_count": 32675,
             "eval_count": 93, "message": {"role": "assistant", "content": "", "thinking": "I need to fix lines 15-23"}}
    registry = types.SimpleNamespace(get_model_info=lambda model: {
        "id": model, "model_num_ctx": 262144, "model_template": "{{ .Prompt }}", "family": "qwen35"})
    history = [
        {"role": "system", "content": "You are W1."},
        {"role": "user", "content": "Fix src/game_logic.py"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "name": "file_system", "arguments": {"action": "read", "filename": "src/game_logic.py"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "file_system", "content": "1: \"\"\"Game logic\"\"\""},
    ]

    async def go():
        runner, url = await _serve_ollama_chat(payloads, reply)()
        try:
            provider = OllamaProvider(base_url=url, model_registry=registry)
            return [event async for event in provider.stream_completion(
                messages=history, model="qwen3.5:9b-q4_K_M", temperature=0.7, max_tokens=4096,
                tools=[{"type": "function", "function": {"name": "file_system"}}])]
        finally:
            await runner.cleanup()
    events = run(go())
    sent = payloads[0]
    assert sent["options"]["num_ctx"] == 32768 and "template" not in sent       # Round 2o: Ollama ignores it
    assistant = [m for m in sent["messages"] if m["role"] == "assistant"][0]
    assert assistant["content"] == ""                                              # Round 2o: no imitable call text
    assert assistant["tool_calls"][0]["function"]["name"] == "file_system"
    finish = [e for e in events if e.get("type") == "finish_info"][0]
    assert (finish["prompt_tokens"], finish["output_tokens"], finish["num_ctx"], finish["num_predict"]) == (32675, 93, 32768, 4096)


# ======================================================================================== 9. failover and tool support
def test_capabilities_decide_tool_support(monkeypatch):
    import src.agents.failover_handler as failover
    infos = {
        "qwen3.5:9b-q4_K_M": {"model_template": "{{ .Prompt }}", "model_capabilities": ["completion", "tools", "thinking"]},
        "gemma4:12b": {"model_template": "{{ .Prompt }}", "model_capabilities": ["completion", "vision", "tools"]},
        "codellama:34b-instruct-q5_1": {"model_template": "[INST] {{ .Prompt }} [/INST]", "model_capabilities": ["completion", "insert"]},
        "old-raw:latest": {"model_template": "{{ .Prompt }}"},
        "old-chat:latest": {"model_template": "{{ .System }} {{ .Prompt }}"},
    }
    monkeypatch.setattr(failover.model_registry, "get_model_info", lambda model_id: infos.get(model_id))
    assert failover.model_supports_tools("gemma4:12b") is True
    assert failover.model_supports_tools("codellama:34b-instruct-q5_1") is False
    assert failover.model_supports_tools("old-raw:latest") is None
    assert not failover._lacks_tool_support("gemma4:12b") and not failover._lacks_tool_support("qwen3.5:9b-q4_K_M")
    assert failover._lacks_tool_support("codellama:34b-instruct-q5_1")
    assert failover._lacks_tool_support("old-raw:latest") and not failover._lacks_tool_support("old-chat:latest")


def test_the_model_sorter_keeps_raw_template_models_with_capabilities():
    from src.agents.agent_utils import sort_models_by_size_performance_id
    models = [
        {"id": "gemma4:12b", "provider": "p", "num_parameters": 12_000_000_000, "model_template": "{{ .Prompt }}",
         "model_capabilities": ["completion", "tools"]},
        {"id": "old-raw:latest", "provider": "p", "num_parameters": 7_000_000_000, "model_template": "{{ .Prompt }}"},
        {"id": "nomic-embed:latest", "provider": "p", "model_capabilities": ["embedding"]},
        {"id": "embedder:latest", "provider": "p", "model_capabilities": ["embedding"]},
    ]
    assert [m["id"] for m in sort_models_by_size_performance_id(models)] == ["gemma4:12b"]


def test_discovery_keeps_ollama_capabilities():
    from aiohttp import web
    from unittest.mock import MagicMock
    from src.config.model_registry import ModelRegistry
    from src.config.settings import Settings

    async def tags(request):
        return web.json_response({"models": [{"name": "gemma4:12b"}]})

    async def show(request):
        return web.json_response({"details": {"parameter_size": "12B"}, "template": "{{ .Prompt }}",
                                  "capabilities": ["completion", "vision", "tools"]})

    async def go():
        app = web.Application()
        app.router.add_get("/api/tags", tags)
        app.router.add_post("/api/show", show)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            settings_obj = MagicMock(spec=Settings)
            settings_obj.MODEL_TIER = "LOCAL"
            registry = ModelRegistry(settings_obj=settings_obj)
            return await registry._verify_and_fetch_models(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
        finally:
            await runner.cleanup()
    _provider, _url, models = run(go())
    assert models[0]["model_capabilities"] == ["completion", "vision", "tools"]


# ======================================================================================== 10. collect.py
def _collect_module():
    path = Path(__file__).resolve().parents[2] / "collect.py"
    spec = importlib.util.spec_from_file_location("collect_round2n", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_takes_the_session_of_the_log(tmp_path):
    collect = _collect_module()
    snake = tmp_path / "projects" / "WiFi_Snake_Game" / "startup_1792509061" / "shared_workspace" / "src"
    older = tmp_path / "projects" / "Temp_Converter_CLI" / "startup_1792451114" / "shared_workspace"
    snake.mkdir(parents=True)
    older.mkdir(parents=True)
    (snake / "game_logic.py").write_text(GAME_LOGIC_23_42)
    (older / "PROJECT_STRUCTURE.md").write_text("x")
    log = ("2026-10-20 23:11:01,086 - src.agents.manager - INFO - Setting context. Project: DefaultProject, Session: startup_1792509061, Loading: False\n"
           "2026-10-20 23:14:55,581 - src.agents.manager - INFO - Setting context. Project: WiFi_Snake_Game, Session: startup_1792509061, Loading: False\n")
    assert collect.session_dir_from_log(tmp_path, log) == tmp_path / "projects" / "WiFi_Snake_Game" / "startup_1792509061"
    assert collect.session_dir_from_log(tmp_path, log.splitlines()[0]) is None
    # without the log: the session with the newest FILE (the folder date of shared_workspace did not change)
    old_time = time.time() - 3600
    os.utime(snake / "game_logic.py", (old_time, old_time))
    os.utime(older / "PROJECT_STRUCTURE.md", (old_time - 10, old_time - 10))
    os.utime(older, (time.time(), time.time()))
    newest_file = time.time()
    os.utime(snake / "game_logic.py", (newest_file, newest_file))
    assert collect.get_latest_session_dir(tmp_path) == tmp_path / "projects" / "WiFi_Snake_Game" / "startup_1792509061"

# END OF FILE tests/unit/test_round2n.py
