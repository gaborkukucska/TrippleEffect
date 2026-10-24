# START OF FILE tests/unit/test_round3i.py
"""
Round 3i: messages between agents reach a busy agent at its next step, and are shown LAST in its prompt.

2026-10-24 10:00-11:43 (WiFi_Snake_Multiplayer_Game): PM1 sent 8 messages to W1 and W2; 6 never reached them. A message to a
worker in worker_work / worker_report waited in its inbox until the worker went to worker_wait or sent a message itself:
PM1's answers to W2's report (11:08:32, 11:10:42), its answer to W1's report (11:16:53), 11:28:44, and its answer to the
Guardian's escalation (11:38:37, 35 seconds after W1 had gone back to worker_work). A message that did reach the history was
followed by the workspace tree, the task list and "[TASK FOCUS REMINDER] ... take the next concrete step".
"""
import asyncio
import logging
import types
from contextlib import contextmanager

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer_Game", "startup_1792807214"


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


PM_ANSWER = ("I see the issue - the task description is too broad and the tests in tests/test_game/test_game.py expect "
             "food at a fixed place. Fix the tests first: set game.food before calling update().")


def _message(message_id, text, sender="PM1"):
    return {"role": "user", "content": f"[MESSAGE_ID: {message_id}] [From @{sender} (Project Manager)]: {text}",
            "message_id": message_id}


def _worker(state="worker_work"):
    return types.SimpleNamespace(
        agent_id="W1", agent_type="worker", state=state, status="processing", persona="Game Developer",
        message_history=[{"role": "system", "content": "SYSTEM PROMPT"},
                         {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "code_editor",
                                                                                "arguments": {}}]},
                         {"role": "tool", "tool_call_id": "c1", "content": "Code edit applied to src/game.py"}],
        message_inbox=[], read_message_ids={}, needs_priority_recheck=False, active_task_id=None,
        _last_system_prompt_state=("worker", state))


# ======================================================================================== 1. delivery
def test_queued_messages_reach_a_busy_agent_but_task_activations_wait():
    from src.agents.message_delivery import deliver_pending_messages
    worker = _worker()
    activation = {"role": "user", "content": "[Framework Directive]: You have been assigned a new task: Task 3",
                  "message_id": "msg_act", "_deferred_task_description": "Task 3", "_deferred_task_id": "t3"}
    worker.message_inbox = [_message("msg_1792813117669_PM1", PM_ANSWER), activation]
    assert deliver_pending_messages(worker) == 1
    assert worker.message_history[-1]["message_id"] == "msg_1792813117669_PM1"
    assert worker.message_inbox == [activation]                       # the new task still waits for worker_wait
    assert deliver_pending_messages(worker) == 0
    assert deliver_pending_messages(types.SimpleNamespace(agent_id="x")) == 0     # no inbox: nothing, no error


def _handler(target):
    from src.agents.interaction_handler import AgentInteractionHandler
    pm1 = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage", status="processing",
                                persona="Project Manager", message_history=[])
    scheduled = []

    async def schedule_cycle(agent, delay):
        scheduled.append(agent.agent_id)

    async def send_to_ui(_event):
        return None
    manager = types.SimpleNamespace(
        agents={"PM1": pm1, target.agent_id: target},
        state_manager=types.SimpleNamespace(get_agent_team=lambda agent_id: f"team_{PROJECT}"),
        workflow_manager=types.SimpleNamespace(_get_agent_project_name=lambda agent, manager: PROJECT,
                                               change_state=lambda agent, state: None),
        schedule_cycle=schedule_cycle, send_to_ui=send_to_ui)
    return AgentInteractionHandler(manager), scheduled


def test_the_escalation_answer_of_11_38_reaches_the_worker_at_its_next_step():
    from src.agents.message_delivery import deliver_pending_messages
    from src.agents.cycle_components.prompt_assembler import PromptAssembler
    worker = _worker()
    handler, _scheduled = _handler(worker)
    with quiet():
        status, _task = run(handler.route_and_activate_agent_message("PM1", "W1", PM_ANSWER))
    assert status["status"] == "queued" and "It is shown to them at their next step" in status["message"]
    assert worker.needs_priority_recheck and len(worker.message_inbox) == 1
    # the worker's next step (cycle_handler calls this before assembling the prompt)
    deliver_pending_messages(worker)
    assembler = PromptAssembler(types.SimpleNamespace(current_project=PROJECT, current_session=SESSION))

    async def tree():
        return "[SHARED WORKSPACE TREE (Current State)]\n  |-- src/game.py"
    assembler._generate_workspace_tree_report = tree
    assembler._generate_worker_tasks_report = lambda agent: "[YOUR ASSIGNED TASKS - 3 task(s)]"
    context = types.SimpleNamespace(agent=worker)
    with quiet():
        run(assembler.prepare_llm_call_data(context))
    last = context.history_for_call[-1]["content"]
    assert last.startswith("[NEW MESSAGE(S) FOR YOU - read this before your next step]\n[MESSAGE_ID: msg_")
    assert "[From @PM1 (Project Manager)]: I see the issue - the task description is too broad" in last
    assert "act on it first (answer with send_message if it asks you something)" in last
    assert "mark_message_read (message_id 'msg_" in last
    # the message is also in the stored history, before the injected reports
    assert any(PM_ANSWER in str(m.get("content")) for m in worker.message_history)


# ======================================================================================== 2. the block at the end
def test_the_block_shows_a_message_in_full_three_times_then_names_it():
    from src.agents.message_delivery import new_messages_block, FULL_SHOWS
    worker = _worker()
    history = worker.message_history + [_message("msg_1", "Fix the tests first."), _message("msg_2", "Then run pytest.")]
    for _ in range(FULL_SHOWS):
        block = new_messages_block(worker, history)
        assert "Fix the tests first." in block and "Then run pytest." in block
    block = new_messages_block(worker, history)
    assert "Fix the tests first." not in block and "Still unread (shown above earlier): msg_1, msg_2" in block
    # a new message is shown in full; a message marked read is gone
    history.append(_message("msg_3", "Also update the README."))
    worker.read_message_ids = {"msg_1": 1.0}
    block = new_messages_block(worker, history)
    assert "Also update the README." in block and "msg_1" not in block and "Still unread (shown above earlier): msg_2" in block
    worker.read_message_ids = {"msg_1": 1.0, "msg_2": 1.0, "msg_3": 1.0}
    assert new_messages_block(worker, history) is None
    long_text = "x" * 5000
    block = new_messages_block(_worker(), [_message("msg_9", long_text)])
    assert len(block) < 2200


def test_unread_messages_survive_a_context_summary():
    from src.agents.message_delivery import unread_messages_missing_from
    worker = _worker()
    worker.read_message_ids = {"msg_old": 1.0}
    before = worker.message_history + [_message("msg_old", "already read"), _message("msg_new", PM_ANSWER),
                                       {"role": "tool", "tool_call_id": "c9", "content": "x"}]
    after = [{"role": "system", "content": "[CONTEXT SUMMARY 1/2 - t]"}, {"role": "tool", "tool_call_id": "c9",
                                                                          "content": "x"}]
    assert [m["message_id"] for m in unread_messages_missing_from(worker, before, after)] == ["msg_new"]
    assert unread_messages_missing_from(worker, before, after + [before[-2]]) == []


def test_the_cycle_handler_delivers_and_keeps_unread_messages():
    import inspect
    from src.agents import cycle_handler
    source = inspect.getsource(cycle_handler)
    delivery = source.index("deliver_pending_messages(agent)")
    assembly = source.index("await self._prompt_assembler.prepare_llm_call_data(context) # Ensures history_for_call is fresh")
    assert delivery < assembly and assembly - delivery < 300                     # right before the prompt is assembled
    assert "unread_messages_missing_from(agent, context.history_for_call" in source


def test_a_sender_s_own_inbox_keeps_its_deferred_activation():
    from src.agents.message_delivery import deliver_pending_messages
    import inspect
    from src.agents import interaction_handler
    source = inspect.getsource(interaction_handler)
    assert "delivered_count = deliver_pending_messages(agent)" in source
    worker = _worker()
    worker.message_inbox = [{"role": "user", "content": "assigned", "message_id": "a", "_deferred_task_id": "t"}]
    assert deliver_pending_messages(worker) == 0 and len(worker.message_inbox) == 1

# END OF FILE tests/unit/test_round3i.py
