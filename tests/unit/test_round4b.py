# START OF FILE tests/unit/test_round4b.py
"""
Round 4b: the run of 2026-10-28 17:34-18:46 (granite4.2:8b-q4_K_M on one Ollama server for every agent,
Local_Snake_Multiplayer). The model thinks at length (9-14 tokens/s), the run stopped after 72 minutes with no project file.

* 17:55:46 PM1's second kick-off reply held a complete plan in its LAST JSON block, followed by its own check ("Check: -
  roles: ... Good."). The plan was refused for the text after it; the third reply took another 11 minutes.
* 18:08:42 in pm_activate_workers PM1 listed tasks with tags_filter='assigned' but without tags_filter_mode 'exclude': 0
  tasks, and the framework said "No unassigned tasks found" with 5 kick-off tasks unassigned. PM1 created "Create src/server.js
  with Express server and /join endpoint" (half of Kick-off Task 1), assigned it, and was told "All kick-off tasks have now
  been assigned". Kick-off Task 1, which Tasks 2-5 wait for, was never assigned.
* 18:24:44 admin_ai answered PM1's "kick-off phase complete" report with "Project ... new request: ... (WebSocket server +
  canvas client). Phase 1: ...", then read the plan files for 14 minutes and tried to write src/client/style.css.
* 18:30:07 after the worker_decompose -> worker_work clear W1 read PROJECT_STRUCTURE.md and PROJECT_PLAN.md again (it had read
  the plan in worker_decompose; the note only named it).
* PM1's kick-off replies thought 14,422 and 27,644 characters: OLLAMA_THINK=off switches thinking off for every request.
"""
import asyncio
import json
import logging
import shutil
import types
from contextlib import contextmanager

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "Local_Snake_Multiplayer", "startup_1793180087"


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


# ======================================================================================== 1. the kick-off plan
PLAN = {
    "roles": ["Coder", "Technical_Writer"],
    "tasks": [
        {"id": "task_1", "description": "Setup & Basic Server Structure: Create package.json, src/server.js with GET /join"},
        {"id": "task_2", "description": "Implement Game Logic (Client): Create src/game.js with pure functions"},
        {"id": "task_3", "depends_on": ["task_1", "task_2"], "description": "Implement POST /move and GET /leaderboard"},
        {"id": "task_4", "depends_on": "task_3", "description": "Write Automated Tests in tests/test_server.js, tests/test_game.js"},
    ],
    "code_base_definitions": "Node.js with Express; tests use Node's built-in assert.",
    "project_structure": [{"dir": "src", "files": ["server.js", "game.js"]}, {"dir": "tests"}],
}

# the shape of PM1's reply of 2026-10-28 17:55:46: thinking written as text, drafts, the plan, then a check of the plan
REPLY_1755 = (
    "I need to produce the required JSON kickoff plan for the project. Let me analyze the project overview.\n\n"
    "The example shows:\n```json\n\"project_structure\": [\n  {\"dir\": \"src\", \"children\": [{\"dir\": \"core\"}]},\n]\n```\n\n"
    "Thus for us: `{\"dir\": \"src\", \"files\": [\"server.js\", \"game.js\"]}` or `{\"dir\": \"src\", \"children\": []}`.\n"
    "Let's do:\n```json\n\"project_structure\": [\n  {\"dir\": \"root\", \"files\": [\"package.json\"]}\n]\n```\n\n"
    "That's fine.\n" + json.dumps(PLAN, indent=2) + "\n```\n\n"
    "Check:\n- roles: \"Coder\", \"Technical_Writer\" - unique, not PM. Good.\n- tasks: 4 tasks, depends_on specified correctly."
)


class FakeWorkflow:
    name = "pm_project_kickoff"

    def __init__(self):
        self.data = None

    async def execute(self, manager, agent, data):
        self.data = data
        return "executed"


def _pm():
    from src.agents import constants as C
    return types.SimpleNamespace(agent_id="PM1", agent_type=C.AGENT_TYPE_PM, state=C.PM_STATE_STARTUP)


def test_the_last_complete_plan_is_found_with_text_after_it():
    from src.agents.workflow_manager import last_kickoff_plan
    plan, start, end = last_kickoff_plan(REPLY_1755)
    assert plan == PLAN
    assert REPLY_1755[end:].strip().startswith("```\n\nCheck:")
    assert REPLY_1755[start:].startswith('{\n  "roles"')
    # without roles AND tasks there is no plan; broken JSON is left to the old repairs
    assert last_kickoff_plan('{"title": "Local Snake Multiplayer"}') is None
    assert last_kickoff_plan('{"roles": ["Coder"], "tasks": []}') is None
    assert last_kickoff_plan('{"roles": ["Coder"], "tasks": [ {oops ]}') is None
    assert last_kickoff_plan(None) is None


def test_of_two_plans_the_last_one_counts():
    from src.agents.workflow_manager import last_kickoff_plan
    draft = dict(PLAN, roles=["Coder"])
    text = "Draft:\n" + json.dumps(draft) + "\nWait, a writer is needed too. Final:\n" + json.dumps(PLAN) + "\nDone."
    assert last_kickoff_plan(text)[0] == PLAN


def test_the_kickoff_reply_of_17_55_46_starts_the_kickoff():
    from src.agents.workflow_manager import AgentWorkflowManager
    workflow = FakeWorkflow()
    with quiet():
        result = run(AgentWorkflowManager()._try_json_workflow_trigger(None, _pm(), REPLY_1755, [("kickoff_plan", workflow)]))
    assert result == "executed" and workflow.data == PLAN


def test_other_triggers_still_refuse_text_after_the_json():
    from src.agents.workflow_manager import AgentWorkflowManager
    from src.agents import constants as C
    workflow = FakeWorkflow()
    # the same reply for a worker's task_list trigger: the trailing text rule is unchanged there
    worker = types.SimpleNamespace(agent_id="W1", agent_type=C.AGENT_TYPE_WORKER, state="worker_decompose")
    with quiet():
        result = run(AgentWorkflowManager()._try_json_workflow_trigger(None, worker, REPLY_1755, [("task_list", workflow)]))
    assert result is None and workflow.data is None
    # and a PM reply whose JSON is no complete plan is judged as before
    with quiet():
        result = run(AgentWorkflowManager()._try_json_workflow_trigger(
            None, _pm(), '```json\n{"roles": ["Coder"]}\n```\nCheck: no tasks yet.', [("kickoff_plan", workflow)]))
    assert result is None and workflow.data is None


# ======================================================================================== 2. the activation's task list
class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_startup"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id


def _manager(*agents):
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


KICKOFF = [
    "Kick-off Task 1: Implement server logic in src/server.js and game functions in src/game.js (pure functions: "
    "createSnake, moveSnake, checkCollision, spawnFood, updateScore). Server includes GET /join, GET /state, POST /move",
    "Kick-off Task 2: Create UI (index.html with canvas, name input, score display, leaderboard container) and style.css",
    "Kick-off Task 3: Write automated tests in tests/ (test_server.js and test_game.js using Node's assert)",
    "Kick-off Task 4: Write README.md with usage instructions: run command (npm install && node src/server.js)",
    "Kick-off Task 5: Run tests and verify functionality: start server (node src/server.js), open http://localhost:3000",
]


def _kickoff(call, manager):
    uuids = []
    for number, text in enumerate(KICKOFF):
        extra = {"depends": uuids[0]} if number else {}
        result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT,
                      tags="kickoff", **extra)
        assert result["status"] == "success", result
        uuids.append(result["task_uuid"])
    return uuids


@needs_tw
def test_the_activation_lists_the_task_store_not_the_pm_filter(tasks):
    from src.agents.assignable_work import activation_task_list
    pm1, w1, w2 = Agent("PM1", "pm", "pm_activate_workers"), Agent("W1"), Agent("W2")
    manager = _manager(pm1, w1, w2)
    uuids = _kickoff(tasks, manager)
    # 18:08:42 the PM's call: tags_filter='assigned' without tags_filter_mode 'exclude' lists nothing
    listed = tasks("PM1", manager, action="list_tasks", project_filter=PROJECT, task_progress_filter="todo",
                   tags_filter="assigned")
    assert listed["status"] == "success" and listed["tasks"] == []
    pm1.unassigned_tasks_summary, pm1._all_kickoff_task_uuids = [], set()       # what the directive read from that result
    with quiet():
        note = activation_task_list(manager, pm1)
    assert [t["uuid"] for t in pm1.unassigned_tasks_summary] == uuids
    assert pm1.unassigned_tasks_summary[1]["depends"] == [uuids[0]]
    assert note == "\n(Your list_tasks call showed 0 of them - its filters hid 5. This list is read from the task store.)"
    assert pm1._all_kickoff_task_uuids == set(uuids)
    # a call that showed every unassigned task gets no note
    with quiet():
        assert activation_task_list(manager, pm1) == ""


@needs_tw
def test_all_assigned_is_said_only_when_the_store_has_nothing_unassigned(tasks):
    from src.agents.assignable_work import activation_remaining_tasks
    pm1, w1, w2 = Agent("PM1", "pm", "pm_activate_workers"), Agent("W1"), Agent("W2")
    manager = _manager(pm1, w1, w2)
    uuids = _kickoff(tasks, manager)
    pm1.unassigned_tasks_summary = []                                           # "No unassigned tasks found"
    # 18:24:15 the PM assigned a task of its own: the 5 kick-off tasks are still unassigned
    pm1.state = "pm_manage"
    own = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                description="Create src/server.js with Express server and /join endpoint")
    assert own["status"] == "success", own
    pm1.state = "pm_activate_workers"
    assert tasks("PM1", manager, action="modify_task", task_id=own["task_uuid"], assignee_agent_id="W1")["status"] == "success"
    with quiet():
        activation_remaining_tasks(manager, pm1, own["task_uuid"])
    assert [t["uuid"] for t in pm1.unassigned_tasks_summary] == uuids          # not "All kick-off tasks have been assigned"
    assert own["task_uuid"] in pm1._all_kickoff_task_uuids
    # once every task has a worker the list is empty
    for uuid in uuids:
        task = __import__("src.tools.project_management", fromlist=["x"]).ProjectManagementTool()._get_taskwarrior_instance(
            PROJECT, SESSION).tasks.get(uuid=uuid)
        task["assignee"] = "W2"
        task.save()
    with quiet():
        activation_remaining_tasks(manager, pm1)
    assert pm1.unassigned_tasks_summary == []


def test_without_a_task_store_the_listed_tasks_are_kept():
    from src.agents.assignable_work import activation_remaining_tasks, activation_task_list
    pm1 = Agent("PM1", "pm", "pm_activate_workers")
    pm1.unassigned_tasks_summary = [{"uuid": "u1", "description": "x", "depends": []}]
    no_store = types.SimpleNamespace(agents={}, current_project=None, current_session=None)
    assert activation_task_list(no_store, pm1) == ""
    activation_remaining_tasks(no_store, pm1, "u2")
    assert pm1.unassigned_tasks_summary == [{"uuid": "u1", "description": "x", "depends": []}]


def test_the_activation_directives_use_the_task_store():
    import inspect
    from src.agents import cycle_handler
    source = inspect.getsource(cycle_handler)
    assert "hidden_note = activation_task_list(self._manager, agent)" in source
    assert 'f"{summary_str}{hidden_note}\\n\\n"' in source
    assert "activation_remaining_tasks(self._manager, agent, assigned_task_uuid)" in source
    assert (source.index("activation_remaining_tasks(self._manager, agent, assigned_task_uuid)")
            < source.index("remaining_tasks = getattr(agent, 'unassigned_tasks_summary', [])"))


@needs_tw
def test_no_new_task_in_the_activation_while_planned_tasks_wait(tasks):
    pm1, w1, w2 = Agent("PM1", "pm", "pm_activate_workers"), Agent("W1"), Agent("W2")
    manager = _manager(pm1, w1, w2)
    uuids = _kickoff(tasks, manager)
    refused = tasks("PM1", manager, action="add_task", project_filter=PROJECT,          # 18:21:22
                    title="Create src/server.js with Express server and /join endpoint",
                    description="Create src/server.js that serves files from src/client and implements GET /join")
    assert refused["status"] == "error" and refused["error_type"] == "activation_add_task", refused
    assert refused["message"].startswith("The task was NOT created: in this state you ASSIGN the planned tasks, and these "
                                         "are not assigned yet:\n- 'Kick-off Task 1: Implement server logic in src/server.js")
    assert all(uuid in refused["message"] for uuid in uuids)
    assert "Work the plan misses can be added later, in pm_manage." in refused["message"]
    # in pm_manage a PM plans as before
    pm1.state = "pm_manage"
    assert tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                 description="Add wall wrapping to src/game.js as an option")["status"] == "success"
    # in the activation with every planned task assigned, a new task is allowed
    pm1.state = "pm_activate_workers"
    from src.tools.project_management import ProjectManagementTool
    tw = ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
    for task in tw.tasks.pending():
        task["assignee"] = "W2"
        task.save()
    assert tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                 description="Create package.json with the express dependency")["status"] == "success"
    # framework calls are never refused
    for task in tw.tasks.pending():
        task["assignee"] = ""
        task.save()
    assert tasks("framework", manager, action="add_task", project_filter=PROJECT,
                 description="Update 1 Task 1: Add a pause key to src/game.js")["status"] == "success"


# ======================================================================================== 3. the Admin AI and a PM report
REPORT = ("[MESSAGE_ID: msg_1793183084029_PM1] [From @PM1 (Project Manager (@pm_agent) (Local_Snake_Multiplayer))]: Project "
          "`Local_Snake_Multiplayer` kick-off phase complete. All initial tasks have been assigned to workers.")
NEW_REQUEST = ("Project 'Local_Snake_Multiplayer' new request: browser-based snake game, hostable on laptop, mobile access via "
               "same WiFi, with player naming, score system, leaderboard. Start with minimal viable multiplayer (WebSocket "
               "server + canvas client). Phase 1: implement single-player logic + basic server; Phase 2: add player names, "
               "scores, sync; Phase 3: leaderboard + host instructions. Proceed autonomously with PROJECT_PLAN.md decomposition.")


def _admin(history, state="admin_conversation"):
    return types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", state=state, message_history=history,
                                 read_message_ids=set())


def test_a_kickoff_report_tells_the_admin_to_inform_the_user_and_stand_by():
    from src.agents.message_delivery import new_messages_block
    admin = _admin([{"role": "user", "content": REPORT, "message_id": "msg_1793183084029_PM1"}])
    block = new_messages_block(admin, admin.message_history)
    assert block.startswith("[NEW MESSAGE(S) FOR YOU - read this before your next step]\n" + REPORT + "\n")
    assert block.endswith("These are STATUS REPORTS of a Project Manager: they ask you nothing. Tell the user in one or two "
                          "sentences that the project's work has started, acknowledge them with mark_message_read (message_id "
                          "'msg_1793183084029_PM1'), then request 'admin_standby'. Do NOT message the PM about them, and do "
                          "not plan, read or build anything for them: the PM and its workers do the project.")
    assert "answer with send_message" not in block
    for text in ("Project `X` initial kick-off tasks assigned: every worker has a task.",
                 "Initial actionable kick-off tasks for project 'X' have been assigned to the relevant workers."):
        admin = _admin([{"role": "user", "content": f"[MESSAGE_ID: m2] [From @PM2 (PM)]: {text}", "message_id": "m2"}])
        assert "STATUS REPORTS" in new_messages_block(admin, admin.message_history)


def test_other_messages_keep_the_act_on_it_note():
    from src.agents.message_delivery import new_messages_block
    question = {"role": "user", "content": "[MESSAGE_ID: m3] [From @PM1 (PM)]: Need guidance on Phase 1", "message_id": "m3"}
    report = {"role": "user", "content": REPORT, "message_id": "msg_1793183084029_PM1"}
    for history in ([question], [report, question]):
        block = new_messages_block(_admin(history), history)
        assert "act on it first (answer with send_message if it asks you something)" in block and "STATUS" not in block
    # a worker's message with the same words, and the same report in a PM's history, are not status reports for the admin
    from_worker = {"role": "user", "content": REPORT.replace("[From @PM1", "[From @W1"), "message_id": "m4"}
    assert "STATUS" not in new_messages_block(_admin([from_worker]), [from_worker])
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm", read_message_ids=set())
    assert "STATUS" not in new_messages_block(pm, [report])


def test_the_admin_cannot_give_a_pm_a_new_request_by_message():
    from src.agents.interaction_handler import admin_new_request_refusal
    admin, pm1 = _admin([]), types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage")
    refusal = admin_new_request_refusal(admin, pm1, NEW_REQUEST)
    assert refusal.startswith("[Manager Feedback for SendMessage]: NOTHING was delivered: a new or changed request does not "
                              "reach a PM by message")
    assert "request state 'planning'" in refusal and "request 'admin_standby'" in refusal
    # answers, other states and other senders are not refused
    assert admin_new_request_refusal(admin, pm1, "Proceed autonomously with Phase 1 from PROJECT_PLAN.md.") is None
    assert admin_new_request_refusal(_admin([], state="admin_work"), pm1, NEW_REQUEST) is None
    worker = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_work")
    assert admin_new_request_refusal(worker, pm1, NEW_REQUEST) is None
    assert admin_new_request_refusal(admin, worker, NEW_REQUEST) is None


def test_the_refused_new_request_is_not_delivered():
    from src.agents.interaction_handler import AgentInteractionHandler
    admin = types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", state="admin_conversation",
                                  persona="Admin AI", message_history=[])
    pm1 = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage", persona="PM1", message_history=[],
                                _awaiting_project_approval=False)
    manager = types.SimpleNamespace(
        agents={"admin_ai": admin, "PM1": pm1},
        state_manager=types.SimpleNamespace(get_agent_team=lambda agent_id: None),
        workflow_manager=types.SimpleNamespace(_get_agent_project_name=lambda agent, manager: "N/A"))
    handler = AgentInteractionHandler.__new__(AgentInteractionHandler)
    handler._manager = manager
    with quiet():
        result, task = run(handler.route_and_activate_agent_message("admin_ai", "PM1", NEW_REQUEST))
    assert result["status"] == "error" and "NOTHING was delivered" in result["message"] and task is None
    assert pm1.message_history == []


# ======================================================================================== 4. the decompose -> work clear
def _history(plan_text):
    return [
        {"role": "system", "content": "prompt"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "file_system", "arguments": {"action": "read", "path": "PROJECT_PLAN.md"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "file_system", "content": plan_text},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "name": "file_system", "arguments": {"action": "list", "path": "src"}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "file_system", "content": "Contents of 'src': client/"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c3", "name": "file_system", "arguments": {"action": "read", "filename": "src/old.js"}}]},
        {"role": "tool", "tool_call_id": "c3", "name": "file_system", "content": "1: module.exports = {};"},
    ]


def test_the_plan_text_read_before_the_clear_comes_along():
    from src.agents.workflow_manager import AgentWorkflowManager
    plan = "1: # Project Plan: Local_Snake_Multiplayer\n2: **Objective:** A browser-based snake game"
    agent = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_decompose",
                                  message_history=_history(plan), current_task_id=None)
    with quiet():
        assert AgentWorkflowManager().change_state(agent, "worker_work") is True
    note = agent.message_history[-1]["content"]
    assert note.startswith("[Framework Note] Your history before this step was cleared, but nothing you did is lost: "
                           "you read PROJECT_PLAN.md, src/old.js. The CODE MAP shows the interface of your task's files; "
                           "read a file again before you change it. The text of the document(s) you read is below - you do "
                           "not need to read them again:\n--- PROJECT_PLAN.md (as you read it) ---\n" + plan)
    assert "module.exports" not in note                                       # code: the CODE MAP shows it
    assert len(agent.message_history) == 2


def test_long_documents_are_only_named():
    from src.utils.code_map import CARRIED_DOC_MAX_CHARS, carried_files_note
    note = carried_files_note(_history("x" * (CARRIED_DOC_MAX_CHARS + 1)))
    assert "you read PROJECT_PLAN.md, src/old.js." in note and "as you read it" not in note
    failed = _history("[Tool Execution Failed] File not found: PROJECT_PLAN.md")
    assert "PROJECT_PLAN.md" not in carried_files_note(failed)


def test_the_work_prompt_does_not_send_the_worker_back_to_files_it_has():
    from src.config.settings import settings
    prompt = settings.PROMPTS["worker_work_prompt"]
    assert "(read it, unless a framework note in your history already shows its text)" in prompt
    assert "(not again if your history or a framework note already shows them)" in prompt


# ======================================================================================== 5. OLLAMA_THINK
def test_ollama_think_sets_the_think_field(monkeypatch):
    from src.config.settings import settings
    from src.llm_providers.ollama_provider import think_field_value
    monkeypatch.setattr(settings, "OLLAMA_THINK", "auto", raising=False)
    assert think_field_value(None) is None and think_field_value(False) is False and think_field_value(True) is True
    monkeypatch.setattr(settings, "OLLAMA_THINK", "off")
    assert think_field_value(None) is False and think_field_value(True) is True    # a call's own value counts
    monkeypatch.setattr(settings, "OLLAMA_THINK", "on")
    assert think_field_value(None) is True and think_field_value(False) is False
    assert settings.OLLAMA_THINK in ("auto", "on", "off")


async def _serve(handler):
    from aiohttp import web
    app = web.Application()
    app.router.add_route("POST", "/api/chat", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def _payloads(monkeypatch, think_mode, **call_kwargs):
    from aiohttp import web
    from src.config.settings import settings
    from src.llm_providers import ollama_provider as op
    monkeypatch.setattr(op, "MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "OLLAMA_THINK", think_mode, raising=False)
    seen = []

    async def chat(request):
        seen.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        await response.write((json.dumps({"message": {"role": "assistant", "content": "ok"}, "done": True,
                                          "done_reason": "stop", "eval_count": 1}) + "\n").encode())
        await response.write_eof()
        return response

    async def go():
        runner, url = await _serve(chat)
        try:
            provider = op.OllamaProvider(base_url=url, model_registry=types.SimpleNamespace(
                get_model_info=lambda model: {"model_capabilities": ["completion"]}))
            return [e async for e in provider.stream_completion(
                messages=[{"role": "user", "content": "hi"}], model="granite4.2:8b-q4_K_M", temperature=0.1,
                max_tokens=16, **call_kwargs)]
        finally:
            await runner.cleanup()
    with quiet():
        run(go())
    return seen


def test_the_request_carries_think_false_with_ollama_think_off(monkeypatch):
    assert _payloads(monkeypatch, "off")[0]["think"] is False
    assert "think" not in _payloads(monkeypatch, "auto")[0]                     # the model's own default, as before
    assert _payloads(monkeypatch, "off", think=True)[0]["think"] is True


def test_the_env_example_documents_ollama_think():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "\nOLLAMA_THINK=auto\n" in text

# END OF FILE tests/unit/test_round4b.py
