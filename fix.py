#!/usr/bin/env python3
"""
TrippleEffect: Round 1k Surgical Fix  (static/js/websocket.js, static/js/handlers.js,
                                       src/agents/cycle_components/next_step_scheduler.py)

Rounds 1g-1j are assumed applied (this patch only needs the original structure of the three files above).

 1. RECONNECT NOTICE (UI only, no LLM call).  The Admin AI greeting is generated ONCE, at backend start. When the
    browser reconnects (laptop unlock, sleep, network blip) the page asks the backend for the chat history and
    REPLAYS it (chat_history_start clears the chat, then every stored message is re-sent), so the old greeting
    appears to arrive again. The replay is kept (it restores anything that happened while disconnected), but a
    reconnect now ends with one small system line in the chat: "Reconnected - chat history restored. Welcome back!".
    It is shown only for a RECONNECT of an already loaded page, never on first load, and costs no model time.
 2. worker_decompose NUDGE.  A worker that answers in prose only (no tool call, no state change) while in
    worker_decompose used to be re-activated with an unchanged history, wasting a full LLM cycle (11:03 run).
    Now the framework appends a short notice telling it to call project_management add_task or request_state
    'worker_work'. At most 2 nudges in a row (reset by any tool call), then behaviour is exactly as before.
 3. tests/unit/test_round1k.py (new).

Usage:
    python3 fix.py --dry-run
    python3 fix.py

All-or-nothing: if ANY find-string is missing (or matches the wrong number of times) nothing is written.
"""
import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path.cwd()
WS = "static/js/websocket.js"
HD = "static/js/handlers.js"
NS = "src/agents/cycle_components/next_step_scheduler.py"

PATCHES = [
    # ---- 1a. websocket.js: reconnect tracking ------------------------------------------------
    {
        "file": WS,
        "find": r"""/**
 * Establishes and manages the WebSocket connection.
 */
export const connectWebSocket = () => {""",
        "replace": r"""// Round 1k: remember whether this page has connected before, so a later onopen is known to be a RECONNECT.
let hasConnectedBefore = false;
let welcomeBackPending = false;

/**
 * Returns true once after a reconnect (used by the chat-history handler to show a one-line notice).
 */
export const consumeWelcomeBack = () => {
    const pending = welcomeBackPending;
    welcomeBackPending = false;
    return pending;
};

/**
 * Establishes and manages the WebSocket connection.
 */
export const connectWebSocket = () => {""",
    },
    {
        "file": WS,
        "find": r"""            state.setIsConnected(true);
            state.setReconnectDelay(config.INITIAL_RECONNECT_DELAY); // Reset delay""",
        "replace": r"""            state.setIsConnected(true);
            if (hasConnectedBefore) welcomeBackPending = true; // Round 1k: this is a reconnect
            hasConnectedBefore = true;
            state.setReconnectDelay(config.INITIAL_RECONNECT_DELAY); // Reset delay""",
    },
    # ---- 1b. handlers.js: show the notice after the history replay ----------------------------
    {
        "file": HD,
        "find": r"""                console.log("Handler: chat_history_end — scrolling to bottom.");
                if (DOM.conversationArea) {""",
        "replace": r"""                console.log("Handler: chat_history_end — scrolling to bottom.");
                // Round 1k: after a RECONNECT (not first load) end the replay with one small UI-only notice.
                if (typeof ws.consumeWelcomeBack === 'function' && ws.consumeWelcomeBack()) {
                    ui.displayMessage('Reconnected - chat history restored. Welcome back!', 'system_event', 'conversation-area', 'system');
                }
                if (DOM.conversationArea) {""",
    },
    # ---- 2. scheduler: nudge a prose-only worker_decompose cycle -------------------------------
    {
        "file": NS,
        "find": r"""                logger.info(f"NextStepScheduler: Worker '{agent_id}' in worker_decompose state completed cycle. Reactivating for continued decomposition.")
                if agent.status != AGENT_STATUS_ERROR:""",
        "replace": r"""                # Round 1k: a prose-only reply (no tool call at all, no state change) cannot make progress in this
                # state. Tell the worker what to do instead of re-activating it with an unchanged history.
                # Capped at 2 nudges in a row; any tool call resets the counter.
                if context.executed_tool_successfully_this_cycle or context.action_taken_this_cycle:
                    setattr(agent, '_decompose_prose_nudges', 0)
                else:
                    prose_nudges = getattr(agent, '_decompose_prose_nudges', 0)
                    if prose_nudges < 2:
                        setattr(agent, '_decompose_prose_nudges', prose_nudges + 1)
                        agent.message_history.append({
                            "role": "system",
                            "content": (
                                "[Framework Notice]: You replied with text only, but in worker_decompose you must act with a tool call. "
                                "Either call project_management with action 'add_task' to create each sub-task, or, if the task needs "
                                f"no decomposition, call request_state with state='{WORKER_STATE_WORK}'. Do not reply with prose only."
                            ),
                        })
                        logger.warning(f"NextStepScheduler: Worker '{agent_id}' replied with prose only in worker_decompose. Added nudge {prose_nudges + 1}/2.")
                logger.info(f"NextStepScheduler: Worker '{agent_id}' in worker_decompose state completed cycle. Reactivating for continued decomposition.")
                if agent.status != AGENT_STATUS_ERROR:""",
    },
]

NEW_FILES = {
    "tests/unit/test_round1k.py": r'''"""Round 1k tests: a worker in worker_decompose that replies with prose only gets a framework nudge (max 2 in a row).

Observed 2026-10-15 11:03: W1 answered in prose instead of calling request_state in worker_decompose; the scheduler
only re-activated it with an unchanged history, costing a full extra LLM cycle.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK


def _make(history=None):
    manager = MagicMock()
    manager.schedule_cycle = AsyncMock(return_value=MagicMock())
    manager.send_to_ui = AsyncMock()
    manager.current_session_db_id = None
    agent = SimpleNamespace(
        agent_id="W1", agent_type=AGENT_TYPE_WORKER, state=WORKER_STATE_DECOMPOSE, status="processing",
        message_history=list(history or []), set_status=lambda s, **k: None,
    )
    return NextStepScheduler(manager), manager, agent


def _ctx(agent, tool_ok=False, action=False, state_change=False):
    return SimpleNamespace(
        agent=agent, all_tool_results=[], trigger_failover=False, needs_reactivation_after_cycle=False,
        is_retryable_error_type=False, retry_count=0, max_retries_for_cycle=3, cycle_completed_successfully=True,
        executed_tool_successfully_this_cycle=tool_ok, action_taken_this_cycle=action,
        state_change_requested_this_cycle=state_change, current_model_key_for_tracking="m",
        last_error_content="", last_error_obj=None, plan_submitted_this_cycle=False, thought_produced_this_cycle=False,
    )


def _nudges(agent):
    return [m for m in agent.message_history if m.get("role") == "system" and "worker_decompose" in m.get("content", "")]


def test_prose_only_cycle_adds_nudge_and_reactivates():
    sched, manager, agent = _make([{"role": "assistant", "content": "I will plan the tasks."}])
    asyncio.run(sched.schedule_next_step(_ctx(agent)))
    n = _nudges(agent)
    assert len(n) == 1
    assert "request_state" in n[0]["content"] and WORKER_STATE_WORK in n[0]["content"] and "add_task" in n[0]["content"]
    manager.schedule_cycle.assert_awaited_once()


def test_nudges_are_capped_at_two_in_a_row_but_still_reactivates():
    sched, manager, agent = _make()
    for _ in range(5):
        asyncio.run(sched.schedule_next_step(_ctx(agent)))
    assert len(_nudges(agent)) == 2
    assert manager.schedule_cycle.await_count == 5


def test_tool_call_resets_counter_and_adds_no_nudge():
    sched, manager, agent = _make()
    asyncio.run(sched.schedule_next_step(_ctx(agent)))
    asyncio.run(sched.schedule_next_step(_ctx(agent, tool_ok=True, action=True)))
    assert len(_nudges(agent)) == 1
    assert agent._decompose_prose_nudges == 0
    asyncio.run(sched.schedule_next_step(_ctx(agent)))
    assert len(_nudges(agent)) == 2


def test_failed_tool_call_is_not_treated_as_prose():
    sched, manager, agent = _make()
    asyncio.run(sched.schedule_next_step(_ctx(agent, tool_ok=False, action=True)))
    assert _nudges(agent) == []
    manager.schedule_cycle.assert_awaited_once()


def test_state_change_cycle_untouched():
    sched, manager, agent = _make()
    asyncio.run(sched.schedule_next_step(_ctx(agent, state_change=True)))
    assert _nudges(agent) == []
''',
}

def apply_patches(dry_run: bool) -> int:
    originals = {}
    updated = {}
    errors = []

    for idx, p in enumerate(PATCHES, 1):
        rel = p["file"]
        path = ROOT / rel
        expected = p.get("count", 1)
        if rel not in updated:
            if not path.is_file():
                errors.append(f"[patch {idx}] File not found: {rel}")
                continue
            originals[rel] = path.read_text(encoding="utf-8")
            updated[rel] = originals[rel]
        count = updated[rel].count(p["find"])
        if count == 0:
            errors.append(f"[patch {idx}] NO MATCH in {rel} for find-string starting: {p['find'][:80]!r}")
        elif count != expected:
            errors.append(f"[patch {idx}] {count} MATCHES (expected exactly {expected}) in {rel}: {p['find'][:80]!r}")
        else:
            updated[rel] = updated[rel].replace(p["find"], p["replace"])

    for rel in NEW_FILES:
        if (ROOT / rel).exists():
            errors.append(f"New file already exists, refusing to overwrite: {rel}")

    if errors:
        print("ERROR: patch aborted, NOTHING was written:\n", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    for rel, new_text in updated.items():
        if new_text == originals[rel]:
            continue
        if dry_run:
            diff = difflib.unified_diff(
                originals[rel].splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2,
            )
            sys.stdout.writelines(diff)
            print()
        else:
            (ROOT / rel).write_text(new_text, encoding="utf-8")
            print(f"PATCHED  {rel}")

    for rel, content in NEW_FILES.items():
        if dry_run:
            print(f"[dry-run] would create {rel} ({len(content.splitlines())} lines)")
        else:
            target = ROOT / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"CREATED  {rel}")

    print("\nDry run complete: no files changed." if dry_run else "\nAll patches applied successfully.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply TrippleEffect Round 1k fixes.")
    ap.add_argument("--dry-run", action="store_true", help="Show diffs only; do not write any files.")
    args = ap.parse_args()
    if not (ROOT / "src").is_dir():
        print("ERROR: run this from the TrippleEffect repository root.", file=sys.stderr)
        return 1
    return apply_patches(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
