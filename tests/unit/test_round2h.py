# START OF FILE tests/unit/test_round2h.py
"""
Round 2h: fixtures from the 2026-10-19 18:50-19:46 run with qwen3.5:9b (project Temp_Converter_CLI, session
startup_1792407051).

* 19:17-19:39 - PM1 sent 7 replies that only thought (no text, no tool call). Their thinking ended with the next call
  ("Next action: modify_task for ba793058-0f64... with assignee_agent_id='W1'"), the framework answered "Your last response
  was empty" / "All workers appear to be busy. You should transition to standby".
* 19:36:12 - after a context reset PM1 called modify_task with the invented id
  'temp_conversion_logic_cli_tool_implementation_and_unit_tests' and was only told to list the tasks.
* 19:36:35 - PM1 was forced into pm_standby while 5 'todo' tasks had no assignee and W1 was idle in worker_startup; the
  standby watchdog only wakes a PM for inbox messages or workers in worker_wait, so the project stopped.
"""
import asyncio
import logging
import shutil
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.assignable_work import (
    ASSIGNMENT_CHECK_TAG, assignable_tasks, assignment_hint, assignment_hint_for, find_assignable_work, idle_worker_ids,
)
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_TYPE_PM, AGENT_TYPE_WORKER, PM_STATE_ACTIVATE_WORKERS,
    PM_STATE_MANAGE, PM_STATE_STANDBY, WORKER_STATE_STARTUP, WORKER_STATE_WAIT, WORKER_STATE_WORK,
)

needs_taskwarrior = pytest.mark.skipif(shutil.which("task") is None, reason="Taskwarrior binary not installed")

PROJECT, SESSION = "Temp_Converter_CLI", "startup_1792407051"
KICKOFF = "PROJECT KICK-OFF: Temp_Converter_CLI  Full Plan Overview: **Objective:** Build a Python command-line temperature converter"
# pending.data of the run (uuid, description, tags)
TASKS = [
    ("ba793058-0f64-4463-bfd7-6eca068937e4", "Kick-off Task 1: Create the project directory structure with src/, tests/ directories and initialize temp_convert.py skeleton file", 1),
    ("143b6f6b-4a1c-4700-8ad1-54b3924a6000", "Kick-off Task 2: Implement temperature conversion functions (celsius_to_fahrenheit, fahrenheit_to_celsius) in src/temp_convert.py with proper argparse CLI handling and main() entry point", 2),
    ("1133d677-06cf-4088-8560-2fac18d625ed", "Kick-off Task 3: Write pytest test suite covering 0°C=32°F, 100°C=212°F, -40 conversion equivalence across units, and negative temperature edge cases", 3),
    ("c81df2c8-ed43-4422-8502-5ac14b38be46", "Kick-off Task 4: Create README.md with usage examples for both Celsius-to-Fahrenheit conversions via CLI arguments (e.g., python temp_convert.py 37 C) and pytest test execution instructions", 4),
    ("de341565-13fa-4230-9f28-1fc46ec45f6d", "Kick-off Task 5: Execute tests with `python -m pytest` and run the CLI tool to verify all conversion functions work correctly end-to-end", 5),
]
# PM1's thinking at 19:28:27 (content_chars=253 there; 19:21:49 had the same plan without text)
THINKING_19_21 = ("I have both the task list and agent list now. I need to assign Kick-off Task 1 (creating directory structure) to "
                  "W1 first since it's independent of other tasks - this is an actionable kickoff with no dependencies. | Task UUID: "
                  "ba793058-0f64-4463-bfd7-6eca068937e4 | Worker Agent ID for task assignment will be W1 (the available worker)")


def _task_dicts():
    tasks = [{"uuid": "e8ea92df-37bb-4e64-923c-248f6dcb0d49", "description": KICKOFF, "status": "pending",
              "task_progress": "decomposed", "assignee": "PM1",
              "tags": ["PM1", "assigned", "auto_created_by_framework", "project_kickoff"], "entry": "20261019T110100Z"}]
    for uuid, description, order in reversed(TASKS):  # stored order must not matter
        tasks.append({"uuid": uuid, "description": description, "status": "pending", "task_progress": "todo",
                      "tags": ["kickoff", "pm_decomposed", f"task_order_{order}"], "entry": "20261019T110755Z"})
    return tasks


def _worker(agent_id="W1", state=WORKER_STATE_STARTUP, status=AGENT_STATUS_IDLE):
    return types.SimpleNamespace(agent_id=agent_id, agent_type=AGENT_TYPE_WORKER, state=state, status=status)


# --- assignable work ------------------------------------------------------------------------------------------------------
def test_the_observed_tasks_are_assignable_in_task_order():
    tasks = assignable_tasks(_task_dicts())
    assert [t["uuid"] for t in tasks] == [uuid for uuid, _, _ in TASKS]


def test_assigned_finished_decomposed_and_blocked_tasks_are_not_assignable():
    tasks = _task_dicts()
    tasks[1]["assignee"] = "W1"                      # task 5
    tasks[2]["task_progress"] = "finished"           # task 4
    tasks[3]["status"] = "completed"                 # task 3
    # task 2 depends on a task that is not finished
    tasks[4]["depends"] = [{"uuid": "x", "status": "pending", "task_progress": "todo", "description": "x"}]
    left = [t["uuid"] for t in assignable_tasks(tasks)]
    assert "de341565-13fa-4230-9f28-1fc46ec45f6d" not in left and "c81df2c8-ed43-4422-8502-5ac14b38be46" not in left
    assert "1133d677-06cf-4088-8560-2fac18d625ed" not in left and "143b6f6b-4a1c-4700-8ad1-54b3924a6000" not in left
    assert left == ["ba793058-0f64-4463-bfd7-6eca068937e4"]
    assert "e8ea92df-37bb-4e64-923c-248f6dcb0d49" not in left


def test_idle_workers():
    tasks = _task_dicts()
    assert idle_worker_ids([_worker()], tasks) == ["W1"]
    assert idle_worker_ids([_worker(state=WORKER_STATE_WAIT)], tasks) == ["W1"]
    assert idle_worker_ids([_worker(state=WORKER_STATE_WORK)], tasks) == []
    assert idle_worker_ids([_worker(status=AGENT_STATUS_PROCESSING)], tasks) == []
    tasks[1]["assignee"] = "W1"  # W1 already has an open task: the watchdog starts it, the PM is not needed
    assert idle_worker_ids([_worker()], tasks) == []
    pm = types.SimpleNamespace(agent_id="PM1", agent_type=AGENT_TYPE_PM, state=PM_STATE_MANAGE, status=AGENT_STATUS_IDLE)
    assert idle_worker_ids([pm], _task_dicts()) == []


def test_the_hint_names_the_tasks_the_worker_and_the_exact_call():
    hint = assignment_hint(assignable_tasks(_task_dicts()), ["W1"])
    assert hint.startswith(ASSIGNMENT_CHECK_TAG)
    assert "5 task(s) are not assigned to anyone and worker(s) W1 is idle" in hint
    assert "Workers are NOT busy" in hint
    assert '{"action": "modify_task", "task_id": "ba793058-0f64-4463-bfd7-6eca068937e4", "assignee_agent_id": "W1"}' in hint
    for uuid, _, _ in TASKS:
        assert f"- {uuid}: Kick-off Task" in hint
    assert assignment_hint([], ["W1"]) == "" and assignment_hint(assignable_tasks(_task_dicts()), []) == ""


@pytest.fixture
def task_store(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    if shutil.which("task") is None:
        pytest.skip("Taskwarrior binary not installed")
    import src.tools.project_management as pm
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    tool = pm.ProjectManagementTool()
    asyncio.run(tool.execute("framework", tmp_path, project_name=PROJECT, session_name=SESSION, action="add_task",
                             description=KICKOFF, tags=["project_kickoff", "auto_created_by_framework"], assignee_agent_id="PM1"))
    tw = tool._get_taskwarrior_instance(PROJECT, SESSION)
    kickoff = tw.tasks.pending()[0]
    kickoff["task_progress"] = "decomposed"
    kickoff.save()
    for _, description, order in TASKS:
        asyncio.run(tool.execute("PM1", tmp_path, project_name=PROJECT, session_name=SESSION, action="add_task",
                                 description=description, tags=["kickoff", "pm_decomposed", f"task_order_{order}"]))
    return tool


def _manager(workers):
    team = types.SimpleNamespace(get_agent_team=lambda agent_id: "team_Temp_Converter_CLI",
                                 get_agents_in_team=lambda team_id: list(workers))
    return types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, state_manager=team)


@needs_taskwarrior
def test_find_assignable_work_in_the_observed_store(task_store):
    pm = types.SimpleNamespace(agent_id="PM1")
    tasks, workers = find_assignable_work(_manager([_worker()]), pm)
    assert [str(t["description"])[:16] for t in tasks] == ["Kick-off Task 1:", "Kick-off Task 2:", "Kick-off Task 3:",
                                                           "Kick-off Task 4:", "Kick-off Task 5:"]
    assert workers == ["W1"]
    assert ASSIGNMENT_CHECK_TAG in assignment_hint_for(_manager([_worker()]), pm)
    assert assignment_hint_for(_manager([_worker(state=WORKER_STATE_WORK)]), pm) is None
    assert assignment_hint_for(_manager([]), pm) is None
    assert find_assignable_work(types.SimpleNamespace(), pm) == ([], [])


# --- task not found -------------------------------------------------------------------------------------------------------
@needs_taskwarrior
def test_an_unknown_task_id_gets_the_open_tasks(task_store, tmp_path):
    result = asyncio.run(task_store.execute(
        "PM1", tmp_path, project_name=PROJECT, session_name=SESSION, action="modify_task",
        task_id="temp_conversion_logic_cli_tool_implementation_and_unit_tests", assignee_agent_id="W1", priority="high"))
    assert result["status"] == "error"
    message = result["message"]
    assert message.startswith("Task 'temp_conversion_logic_cli_tool_implementation_and_unit_tests' not found.")
    assert "Open tasks (use one of these exact task_id values):" in message
    tw = task_store._get_taskwarrior_instance(PROJECT, SESSION)
    for task in tw.tasks.pending():
        if task["task_progress"] == "decomposed":
            assert task["uuid"] not in message
        else:
            assert f"- {task['uuid']} [todo, unassigned]: Kick-off Task" in message


def test_open_tasks_listing_handles_a_broken_store():
    from src.tools.project_management import open_tasks_listing
    assert open_tasks_listing(types.SimpleNamespace()) == ""


# --- thinking of a reply --------------------------------------------------------------------------------------------------
def test_finish_info_carries_the_thinking():
    from src.llm_providers.ollama_provider import finish_info_event, thinking_tail
    event = finish_info_event({"eval_count": 99, "message": {"content": "", "thinking": THINKING_19_21}}, "stop")
    assert event["thinking_chars"] == len(THINKING_19_21)
    assert event["thinking_tail"] == " ".join(THINKING_19_21.split())
    assert finish_info_event({"eval_count": 1}, "stop", "a\n  b")["thinking_tail"] == "a b"
    long = "word " * 400
    tail = thinking_tail(long)
    assert len(tail) <= 600 and tail.startswith("word") and long.strip().endswith(tail)


def test_thinking_off_only_for_ollama():
    from src.llm_providers.ollama_provider import OllamaProvider, thinking_off_kwargs
    assert thinking_off_kwargs(OllamaProvider.__new__(OllamaProvider)) == {"think": False}
    assert thinking_off_kwargs(object()) == {}


class _FakeProvider:
    def __init__(self, events):
        self.events, self.kwargs_seen = events, []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.kwargs_seen.append(dict(kwargs))
        for event in self.events:
            yield event


def _pm_agent(provider, monkeypatch):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools={}),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow),
        cycle_handler=None)
    agent = Agent({"agent_id": "PM1", "config": {"agent_type": "pm", "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = PM_STATE_ACTIVATE_WORKERS
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return asyncio.run(go())


def test_the_agent_records_the_thinking_and_switches_it_off_once(monkeypatch):
    import src.llm_providers.ollama_provider as ollama_module
    provider = _FakeProvider([{"type": "finish_info", "done_reason": "stop", "output_tokens": 99,
                               "thinking_chars": len(THINKING_19_21), "thinking_tail": THINKING_19_21}])
    monkeypatch.setattr(ollama_module, "OllamaProvider", _FakeProvider)  # the fake counts as an Ollama provider
    agent = _pm_agent(provider, monkeypatch)
    _consume(agent)
    assert agent._last_reply_thinking_chars == len(THINKING_19_21) and agent._last_reply_thinking_tail == THINKING_19_21
    assert "think" not in provider.kwargs_seen[-1]
    agent._think_off_next_call = True
    provider.events = [{"type": "response_chunk", "content": "ok"}, {"type": "finish_info", "done_reason": "stop"}]
    _consume(agent)
    assert provider.kwargs_seen[-1].get("think") is False and agent._think_off_next_call is False
    assert agent._last_reply_thinking_chars == 0 and agent._last_reply_thinking_tail == ""
    _consume(agent)
    assert "think" not in provider.kwargs_seen[-1]


def test_thinking_only_notice():
    from src.agents.cycle_handler import thinking_only_notice, thinking_only_tail
    agent = types.SimpleNamespace(_last_reply_thinking_chars=len(THINKING_19_21), _last_reply_thinking_tail=THINKING_19_21)
    tail = thinking_only_tail(agent)
    text = thinking_only_notice(tail, 1, 2)
    assert "contained ONLY thinking (attempt 1/2)" in text and "NOTHING was done" in text
    assert "Task UUID: ba793058-0f64-4463-bfd7-6eca068937e4" in text and "make that tool call" in text
    assert thinking_only_tail(types.SimpleNamespace()) == ""
    assert thinking_only_tail(types.SimpleNamespace(_last_reply_thinking_chars=0, _last_reply_thinking_tail="x")) == ""
    assert thinking_only_tail(MagicMock()) == ""


# --- the cycle handler: a reply that only thought -------------------------------------------------------------------------
def _run_empty_reply(state, workers, thinking=THINKING_19_21, store=True):
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager

    logging.disable(logging.CRITICAL)
    try:
        manager = MagicMock(spec=AgentManager)
        manager.db_manager = AsyncMock()
        manager.performance_tracker = AsyncMock()
        manager.send_to_ui = AsyncMock()
        manager.schedule_cycle = AsyncMock(return_value=None)
        manager.current_project = PROJECT if store else None
        manager.current_session = SESSION
        manager.current_session_db_id = None
        manager._agent_cycle_locks = {}
        manager.state_manager = _manager(workers).state_manager
        manager.workflow_manager = MagicMock()
        manager.agents = {}

        agent = MagicMock(spec=Agent)
        agent.agent_id = "PM1"
        agent.agent_type = AGENT_TYPE_PM
        agent.state = state
        agent.status = AGENT_STATUS_IDLE
        agent.provider_name = "ollama-local"
        agent.model = "qwen3.5:9b-q4_K_M"
        agent.persona = "Project Manager"
        agent.message_history = [{"role": "system", "content": "pm prompt"}]
        agent._failover_state = {}
        agent._failed_models_this_cycle = set()
        agent._last_reply_cut_off = False
        agent._think_off_next_call = False
        agent._empty_response_retry_count = 0
        agent._manage_unproductive_cycles = 0
        agent._manage_cycle_cooldown_until = 0
        agent.needs_priority_recheck = False

        def set_state(new_state):
            agent.state = new_state
        agent.set_state = MagicMock(side_effect=set_state)

        async def events(*_args, **_kwargs):
            agent._last_reply_thinking_chars = len(thinking)
            agent._last_reply_thinking_tail = thinking
            if False:
                yield {}

        agent.process_message = MagicMock(side_effect=events)
        handler = AgentCycleHandler(manager, MagicMock())
        asyncio.run(handler.run_cycle(agent, 0))
        return agent
    finally:
        logging.disable(logging.NOTSET)


def _system_texts(agent):
    return [m["content"] for m in agent.message_history if m.get("role") == "system"]


def test_a_reply_that_only_thought_is_named_quoted_and_retried_without_thinking():
    agent = _run_empty_reply(PM_STATE_ACTIVATE_WORKERS, [_worker()], store=False)
    notices = [t for t in _system_texts(agent) if "contained ONLY thinking" in t]
    assert len(notices) == 1 and "(attempt 1/2)" in notices[0]
    assert "Task UUID: ba793058-0f64-4463-bfd7-6eca068937e4" in notices[0]
    assert agent._think_off_next_call is True


def test_a_reply_without_any_thinking_keeps_the_empty_nudge():
    agent = _run_empty_reply(PM_STATE_ACTIVATE_WORKERS, [_worker()], thinking="", store=False)
    texts = _system_texts(agent)
    assert any("Your last response was empty (attempt 1/2)" in t for t in texts)
    assert not any("contained ONLY thinking" in t for t in texts)
    assert agent._think_off_next_call is False


@needs_taskwarrior
def test_pm_manage_gets_the_open_work_instead_of_all_workers_busy(task_store):
    agent = _run_empty_reply(PM_STATE_MANAGE, [_worker()])
    texts = _system_texts(agent)
    directive = [t for t in texts if ASSIGNMENT_CHECK_TAG in t]
    assert len(directive) == 1
    assert "contained ONLY thinking" in directive[0]
    assert '"task_id": "' in directive[0] and '"assignee_agent_id": "W1"' in directive[0]
    assert not any("All workers appear to be busy" in t for t in texts)
    assert agent._think_off_next_call is True
    assert agent._manage_unproductive_cycles == 1


@needs_taskwarrior
def test_pm_manage_with_busy_workers_keeps_the_standby_directive(task_store):
    agent = _run_empty_reply(PM_STATE_MANAGE, [_worker(state=WORKER_STATE_WORK)])
    texts = _system_texts(agent)
    assert any("All workers appear to be busy" in t for t in texts)
    assert not any(ASSIGNMENT_CHECK_TAG in t for t in texts)


@needs_taskwarrior
def test_the_forced_standby_says_the_project_is_not_finished(task_store):
    agent = _run_empty_reply_with_count(2)  # the third unproductive cycle in a row forces standby
    texts = _system_texts(agent)
    forced = [t for t in texts if "so you were put in standby" in t]
    assert len(forced) == 1 and "The project is NOT finished" in forced[0] and ASSIGNMENT_CHECK_TAG in forced[0]
    assert not any("Your project appears to be in a stable state" in t for t in texts)
    assert agent.state == PM_STATE_STANDBY


def _run_empty_reply_with_count(previous_unproductive):
    import src.agents.cycle_handler as ch
    original = ch.AgentCycleHandler.run_cycle

    async def run_cycle(self, agent, retry_count=0):
        agent._manage_unproductive_cycles = previous_unproductive
        return await original(self, agent, retry_count)
    ch.AgentCycleHandler.run_cycle = run_cycle
    try:
        return _run_empty_reply(PM_STATE_MANAGE, [_worker()])
    finally:
        ch.AgentCycleHandler.run_cycle = original


# --- the watchdog wakes a PM in standby for unassigned work ---------------------------------------------------------------
def _run_watchdog_once(pm, workers, monkeypatch):
    import src.agents.manager as manager_module
    from src.agents.manager import AgentManager

    calls = {"sleep": 0}

    async def one_sleep(_seconds):
        calls["sleep"] += 1
        if calls["sleep"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(manager_module.asyncio, "sleep", one_sleep)
    changes = []
    fake = types.SimpleNamespace(
        agents={"PM1": pm, **{w.agent_id: w for w in workers}},
        state_manager=_manager(workers).state_manager,
        current_project=PROJECT, current_session=SESSION,
        loop_coordinator=types.SimpleNamespace(should_intervene=lambda *a: True, record_intervention=lambda *a: None),
        workflow_manager=types.SimpleNamespace(change_state=lambda agent, state: changes.append(state) or True),
        schedule_cycle=AsyncMock(return_value=None),
        current_session_db_id=None,
    )

    async def not_complete(_agent):
        return False
    fake._check_pm_completion_status = not_complete
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(AgentManager._periodic_pm_manage_check(fake))
    return changes, fake


def _pm(state):
    return types.SimpleNamespace(agent_id="PM1", agent_type=AGENT_TYPE_PM, status=AGENT_STATUS_IDLE, state=state,
                                 message_history=[], message_inbox=[], _last_standby_wake_time=0)


@needs_taskwarrior
def test_the_watchdog_wakes_a_pm_in_standby_for_unassigned_work(task_store, monkeypatch):
    pm = _pm(PM_STATE_STANDBY)
    changes, fake = _run_watchdog_once(pm, [_worker()], monkeypatch)
    assert changes == [PM_STATE_MANAGE]
    fake.schedule_cycle.assert_awaited_once()
    assert "unassigned tasks and idle worker(s)" in pm.message_history[-1]["content"]
    assert ASSIGNMENT_CHECK_TAG in pm.message_history[-1]["content"]


@needs_taskwarrior
def test_the_watchdog_keeps_the_standby_backoff_and_busy_workers(task_store, monkeypatch):
    import time
    pm = _pm(PM_STATE_STANDBY)
    pm._last_standby_wake_time = time.time() + 240  # forced-standby cooldown still running
    changes, fake = _run_watchdog_once(pm, [_worker()], monkeypatch)
    assert changes == [] and pm.message_history == []
    pm = _pm(PM_STATE_STANDBY)
    changes, fake = _run_watchdog_once(pm, [_worker(state=WORKER_STATE_WORK)], monkeypatch)
    assert changes == [] and pm.message_history == []


@needs_taskwarrior
def test_the_watchdog_wakes_a_pm_idle_in_manage_for_unassigned_work(task_store, monkeypatch):
    pm = _pm(PM_STATE_MANAGE)
    changes, fake = _run_watchdog_once(pm, [_worker()], monkeypatch)
    fake.schedule_cycle.assert_awaited_once()
    assert ASSIGNMENT_CHECK_TAG in pm.message_history[-1]["content"]
    pm = _pm(PM_STATE_MANAGE)
    changes, fake = _run_watchdog_once(pm, [_worker(state=WORKER_STATE_WORK)], monkeypatch)
    fake.schedule_cycle.assert_not_awaited()
