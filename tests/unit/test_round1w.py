"""Round 1w tests (run 2026-10-17 00:48-02:40): no project was ever created.

In 'planning' the Admin AI wrote a complete numbered Markdown plan three times but never the ```json {"title": ...}``` block.
Only that block (or the legacy <plan> XML) triggered ProjectCreationWorkflow, so all three plans were discarded and the run
ended with "Admin AI could not produce a project plan". A real plan without the block now creates the project (the user
still approves it); the title comes from the user's request ('called "..."') or the plan's heading.
"""
import asyncio
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.constants import ADMIN_STATE_PLANNING, AGENT_TYPE_ADMIN, AGENT_TYPE_PM
from src.agents.tool_activity import looks_like_markdown_plan, plan_title_from_text

USER_REQUEST = (
    'Hello :) Please start a small project called "Temp Converter CLI".\n\nBuild a Python command-line temperature '
    "converter using only the standard library (argparse) and pytest: ..."
)

# the three replies from the log (00:58:08, 01:07:11, 01:23:45), unchanged
PLAN_1 = """# Temp Converter CLI Project Plan

## Tech stack: Python 3 standard library only + pytest for tests, argparse from stdlib for CLI parsing

1. **Core conversion module**: Implement `celsius_to_fahrenheit(c)` and `fahrenheit_to_celsius(f)` functions in `src/temp_convert.py` using the formula F = C × (9/5) + 32 and C = (F - 32) × (5/9), plus a main() function that uses argparse to parse command-line arguments like "python temp_convert.py 100 C" or similar variations.

2. **CLI interface**: Add proper argument parsing in `src/temp_convert.py` with --celsius and --fahrenheit options, default value of None for either option (so user specifies one unit type), convert the appropriate numeric input to a float if provided as string from argparse.

3. **Tests implementation**: Write pytest tests in `tests/test_temp_convert.py`:
   - Test celsius_to_fahrenheit: 0 C → 32 F, and 100 C → 212 F
   - Test fahrenheit_to_celsius: should return the same value when converting back (e.g., convert 40.67°C to Fahrenheit then back) or specifically test that -40°F equals -40°C in both directions

4. **Documentation**: Create `README.md` with examples of CLI usage like "python temp_convert.py --celsius=100" and instructions for running tests using the exact command: python -m pytest -q (or similar)

5. **Verification step**: Execute a final run that includes testing both conversion functions, verifying they produce correct output on sample values including edge case of -40°, then confirm all 8 test cases pass before marking project complete"""

PLAN_2 = """**Objective:** Build a Python command-line temperature converter CLI using argparse and standard library functions for Celsius/Fahrenheit conversion plus pytest tests.

1. **Core module**: Implement `celsius_to_fahrenheit(c)` returning F = C × (9/5) + 32, implement `fahrenheit_to_celsius(f)` returning C = (F - 32) × (5/9), and add a main() function that uses argparse to parse command-line arguments like "python temp_convert.py --celsius=100" or similar variations.

2. **Tests**: Write pytest tests in `tests/test_temp_convert.py` for celsius_to_fahrenheit(0)=32, celsius_to_fahrenheit(100)=212, fahrenheit_to_celsius(-40)=-40, and verify that converting -40°C to Fahrenheit then back yields the original value.

3. **Documentation**: Create `README.md` with usage examples showing CLI command syntax like "python temp_convert.py --celsius=100" plus instructions for running tests using python -m pytest -q (or equivalent).

4. **Verification**: Run all test cases to confirm correct conversions, execute the main() function on sample inputs including edge case of -40°, verify both conversion functions produce expected outputs before marking project complete."""

PLAN_3 = """**Objective:** Build a Python command-line temperature converter using argparse for CLI parsing, standard library conversion functions, pytest tests verifying correct conversions including edge case of -40°, and documentation with usage examples.

1. **Core module implementation**: Implement `celsius_to_fahrenheit(c)` in src/temp_convert.py returning F = C × (9/5) + 32, implement `fahrenheit_to_celsius(f)` returning C = (F − 32) × (5/9), and add main() function using argparse to parse command-line input like "python temp_convert.py --celsius=100".

2. **Pytest tests**: Write test_temp_convert.py in tests directory with assertions: celsius_to_fahrenheit(0)=32, celsius_to_fahrenheit(100)=212, fahrenheit_to_celsius(-40)=-40, and verify round-trip conversion of -40°C to Fahrenheit back yields original value.

3. **README documentation**: Create README.md with CLI usage examples showing syntax for both --celsius= and --fahrenheit= options plus command "python -m pytest -q" for running tests.

4. **Final verification execution**: Run complete test suite in step 2, execute main() function on sample inputs including edge case of −40°, confirm all assertions pass before project completion."""

GREETING = ("Greetings! Welcome to TrippleEffect where I'm able to orchestrate groups of AI agents following your command! "
            "Would you like to get a new project started, receive updates, adjust my settings or just have a casual chat?")


def history(*user_texts):
    out = [{"role": "system", "content": "sys"}, {"role": "user", "content": "[System: Backend initialized, begin proactive startup sequence.]"}]
    out += [{"role": "user", "content": text} for text in user_texts]
    return out


# --------------------------------------------------------------------------------------------------------- recognition
@pytest.mark.parametrize("plan", [PLAN_1, PLAN_2, PLAN_3])
def test_the_observed_replies_are_plans(plan):
    assert looks_like_markdown_plan(plan)


@pytest.mark.parametrize("text", [
    "",
    None,
    GREETING,
    "I'll help you get started! Here's the complete workflow we're going to follow, followed by a JSON block.",
    "Plan:\n1. a\n2. b\n3. c",                                              # numbered but far too short
    # a clarifying question with options is NOT a plan
    "Before I write the plan I need to know which interface you want. The options are:\n1. A command-line tool using argparse"
    " only.\n2. A small Tkinter GUI window with two input fields.\n3. A Flask web page with a form and a result area.\n"
    "Which one would you like?",
    "This text mentions version 3.14 and 2.0 and 1.5 in prose, but it has no numbered list at all, so it is no plan even "
    "though it is long enough to pass the length check; it just keeps talking about things for a while longer here.",
])
def test_other_replies_are_not_plans(text):
    assert not looks_like_markdown_plan(text)


# --------------------------------------------------------------------------------------------------------------- titles
def test_the_name_the_user_gave_wins():
    for plan in (PLAN_1, PLAN_2, PLAN_3):
        assert plan_title_from_text(plan, history(USER_REQUEST)) == "Temp Converter CLI"


def test_the_heading_is_used_when_the_user_gave_no_name():
    assert plan_title_from_text(PLAN_1, history("Please build me a temperature converter project with tests.")) == "Temp Converter CLI"


def test_no_name_and_no_heading_gives_no_title():
    assert plan_title_from_text(PLAN_2, history("Please build me a temperature converter project with tests.")) is None


@pytest.mark.parametrize("request_text,expected", [
    ('start a project called "Word Stats"', "Word Stats"),
    ("start a project named 'Snake Game'", "Snake Game"),
    ("a project titled “Todo Manager” please", "Todo Manager"),
    ('a project called "Budget Tracker Project"', "Budget Tracker"),
    ('a project called "x"', None),                                       # too short to be a name
])
def test_named_project_patterns(request_text, expected):
    assert plan_title_from_text("no heading here", history(request_text)) == expected


@pytest.mark.parametrize("heading,expected", [
    ("# Weather CLI Project Plan", "Weather CLI"),
    ("## Snake Game - Implementation Plan", "Snake Game"),
    ("# **Todo App** Plan", "Todo App"),
    ("# Overview\n## Tasks\n# Pixel Editor", "Pixel Editor"),             # generic headings are skipped
    ("# Plan", None),
    ("# Airplane Tracker", "Airplane Tracker"),                           # 'plan' inside a word is kept
    ("# " + " ".join(["word"] * 12), None),                                # a sentence, not a title
])
def test_heading_titles(heading, expected):
    assert plan_title_from_text(heading, []) == expected


def test_framework_messages_and_older_requests():
    hist = history('start a project called "Old One"', 'now start a project called "New One"')
    hist.append({"role": "user", "content": '[Framework System Message]: project called "Not This"'})
    assert plan_title_from_text("", hist) == "New One"


# ------------------------------------------------------------------------------------------------ workflow integration
def make_admin(state=ADMIN_STATE_PLANNING, agent_type=AGENT_TYPE_ADMIN, user_text=USER_REQUEST):
    return types.SimpleNamespace(agent_id="admin_ai", agent_type=agent_type, state=state,
                                 message_history=history(user_text))


@pytest.fixture
def workflow_manager(monkeypatch):
    from src.agents.workflow_manager import AgentWorkflowManager
    from src.workflows.base import WorkflowResult
    from src.workflows.project_creation_workflow import ProjectCreationWorkflow

    calls = []

    async def fake_execute(self, manager, agent, data_input):
        calls.append(data_input)
        return WorkflowResult(success=True, message="created", workflow_name=self.name,
                              next_agent_state="admin_conversation", next_agent_status="idle")

    monkeypatch.setattr(ProjectCreationWorkflow, "execute", fake_execute)
    wm = AgentWorkflowManager()
    wm.calls = calls
    return wm


def process(wm, agent, text):
    return asyncio.run(wm.process_agent_output_for_workflow(types.SimpleNamespace(), agent, text))


@pytest.mark.parametrize("plan", [PLAN_1, PLAN_2, PLAN_3])
def test_observed_plans_now_create_the_project(workflow_manager, plan):
    result = process(workflow_manager, make_admin(), plan)
    assert result is not None and result.success and result.workflow_name == "project_creation"
    assert workflow_manager.calls == [{"title": "Temp Converter CLI", "_raw_plan_body_": plan}]


def test_the_json_title_block_still_takes_priority(workflow_manager):
    text = PLAN_2 + '\n\n```json\n{"title": "From JSON"}\n```'
    result = process(workflow_manager, make_admin(), text)
    assert result.success and workflow_manager.calls[0]["title"] == "From JSON"
    assert workflow_manager.calls[0]["_raw_plan_body_"] == PLAN_2


@pytest.mark.parametrize("agent_kwargs,text", [
    (dict(), GREETING),                                                            # not a plan
    (dict(user_text="please build a temperature converter project"), PLAN_2),      # plan, but no title anywhere
    (dict(state="admin_conversation"), PLAN_1),                                    # only in 'planning'
    (dict(agent_type=AGENT_TYPE_PM, state="pm_startup"), PLAN_1),                  # only for the Admin AI
])
def test_nothing_happens_otherwise(workflow_manager, agent_kwargs, text):
    assert process(workflow_manager, make_admin(**agent_kwargs), text) is None
    assert workflow_manager.calls == []


def test_think_blocks_are_ignored(workflow_manager):
    import re
    agent = make_admin()
    agent.think_pattern = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)
    result = process(workflow_manager, agent, "<think>Let me plan. 1. x\n2. y\n3. z</think>\n" + PLAN_3)
    assert result.success and workflow_manager.calls[0]["_raw_plan_body_"] == PLAN_3


def test_a_failing_workflow_is_reported_not_raised(workflow_manager, monkeypatch):
    from src.workflows.project_creation_workflow import ProjectCreationWorkflow

    async def boom(self, manager, agent, data_input):
        raise RuntimeError("db down")
    monkeypatch.setattr(ProjectCreationWorkflow, "execute", boom)
    result = process(workflow_manager, make_admin(), PLAN_1)
    assert result is not None and not result.success and "db down" in result.message
