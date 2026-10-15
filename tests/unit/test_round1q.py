"""Round 1q: prompts.yaml guidance for Admin planning, PM kickoff/manage and worker writes."""
import string
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
REPO_ROOT = Path(__file__).resolve().parents[2]


class _Blank(dict):
    def __missing__(self, key):
        return ""


@pytest.fixture(scope="module")
def prompts():
    return yaml.safe_load((REPO_ROOT / "prompts.yaml").read_text(encoding="utf-8"))


def _fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_every_string_prompt_still_formats(prompts):
    for key, text in prompts.items():
        if isinstance(text, str):
            text.format_map(_Blank())  # raises on stray or unbalanced braces


def test_planning_prompt_keeps_placeholders_and_json_example(prompts):
    text = prompts["admin_ai_planning_prompt"]
    assert _fields(text) == {"admin_standard_framework_instructions"}
    rendered = text.format_map(_Blank())
    assert '"title": "The Project Title"' in rendered
    assert "```json" in rendered


def test_planning_prompt_demands_the_plan_itself(prompts):
    text = prompts["admin_ai_planning_prompt"]
    assert "YOUR REPLY *IS* THE PLAN" in text
    assert "MUST start directly with the plan" in text
    assert "NO busywork" in text
    for needed in ("automated tests", "README", "Do NOT add other frameworks"):
        assert needed in text


def test_pm_startup_prompt_requires_full_delivery_chain(prompts):
    text = prompts["pm_startup_prompt"]
    assert _fields(text) == {"pm_standard_framework_instructions", "task_description"}
    assert "4 to 8" in text
    for needed in ("writes automated tests", "writes the README", "runs the tests and the program"):
        assert needed in text
    assert "NO busywork" in text
    assert "Do NOT introduce additional frameworks" in text
    # the old rule that kept tests/docs out of the kickoff plan is gone
    assert "DO NOT include testing or documentation tasks" not in text
    # round 1h guarantees are preserved
    assert "NEVER list 'Project_Manager'" in text
    assert "MUST include a developer role" in text


def test_pm_manage_does_not_duplicate_planned_tasks(prompts):
    text = prompts["pm_manage_prompt"]
    assert _fields(text) == {"pm_standard_framework_instructions", "team_wip_updates", "project_name"}
    assert "do NOT create duplicates" in text
    assert "never introduce another framework" in text
    assert "create it only if the task list has no verification task yet" in text


def test_worker_prompt_says_saved_means_saved(prompts):
    text = prompts["worker_work_prompt"]
    assert "SAVED MEANS SAVED" in text
    assert "NEVER send the same write again" in text
    assert "find_replace" in text


def test_untouched_prompts_are_unchanged_in_structure(prompts):
    for key in ("admin_ai_startup_prompt", "pm_build_team_tasks_prompt", "pm_audit_prompt",
                "worker_report_prompt", "cg_system_prompt"):
        assert key in prompts and isinstance(prompts[key], str)
